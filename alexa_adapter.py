"""Gateway adapter: "Alexa, ask Hermes…" in, the answer out on the same Echo.

Alexa calls the skill endpoint and waits about 8 seconds for what to say. The adapter hands
the request to Hermes and waits a few seconds for the reply:

* reply in time -> Alexa says it, in the same exchange;
* no reply yet  -> Alexa says "I'll let you know", and when Hermes answers, ``send()``
  announces it on that Echo through the bridge (the Amazon session ``hermes alexa`` keeps).

Each Echo is its own conversation (``chat_id`` = hash of the skill's device id). Requests
only get in after ``alexa_verify`` proves they come from Alexa, for this skill.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from aiohttp import web

from gateway.config import Platform
from gateway.platforms.base import BasePlatformAdapter, SendResult
from gateway.platforms.event import MessageEvent, MessageType

from . import alexa_bridge as bridge
from . import alexa_skill as skill
from .alexa_platform import PLATFORM, Settings
from .alexa_verify import AlexaRequestVerifier, RequestRejected

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 64 * 1024
SETTLE_S = 0.4  # after the first chunk, catch one that follows right behind
LEARN_DEVICE_AFTER_S = 4.0  # Alexa's voice history needs a moment to show the request
# Mid-turn status sends start with one of these (busy acks); the words are localized.
_STATUS_PREFIXES = ("⚡", "⏳", "⏩", "💾")


def _is_status(content: str, metadata: dict[str, Any] | None, *, waiting: bool) -> bool:
    """Gateway status chatter, not an answer. The emoji prefix alone only counts while a
    request waits (busy acks answer a new request); later it could be a real answer."""
    if (metadata or {}).get("_interim_send"):
        return True
    return waiting and content.startswith(_STATUS_PREFIXES)


def _epoch_ms(timestamp: str) -> int | None:
    try:
        return int(datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp() * 1000)
    except (AttributeError, ValueError):
        return None


@dataclass
class _Waiting:
    """One Alexa request waiting for Hermes's reply."""

    request_id: str
    parts: list[str] = field(default_factory=list)
    status: str = ""  # a busy ack, said only if no real reply arrives
    arrived: asyncio.Event = field(default_factory=asyncio.Event)
    open: bool = True


class AlexaAdapter(BasePlatformAdapter):
    def __init__(self, config: Any, **kwargs: Any) -> None:
        super().__init__(config=config, platform=Platform(PLATFORM))
        self.settings = Settings.load(config)
        self.verifier = AlexaRequestVerifier(self.settings.skill_ids)
        self._runner: web.AppRunner | None = None
        self._waiting: dict[str, _Waiting] = {}
        self._known_devices: set[str] = set()
        self._known_users: set[str] = set()
        self._background: set[asyncio.Task] = set()

    # -- lifecycle ------------------------------------------------------------------------

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        if not self.settings.skill_ids:
            self._set_fatal_error("config_missing", "ALEXA_SKILL_ID is not set", retryable=False)
            return False
        identity = hashlib.sha256(",".join(self.settings.skill_ids).encode()).hexdigest()[:16]
        if not self._acquire_platform_lock(PLATFORM, identity, "Alexa skill"):
            return False
        await self.verifier.warm_up()
        app = self.build_app()
        self._wire_plugin_handlers(app)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        try:
            await web.TCPSite(runner, self.settings.host, self.settings.port).start()
        except OSError as exc:
            await runner.cleanup()
            self._release_platform_lock()
            self._set_fatal_error("bind_failed", f"Could not listen on {self.settings.host}:"
                                  f"{self.settings.port}: {exc}", retryable=True)
            return False
        self._runner = runner
        self._mark_connected()
        logger.info("Alexa: skill endpoint on %s:%s%s", self.settings.host, self.settings.port,
                    self.settings.path)
        return True

    def build_app(self) -> web.Application:
        app = web.Application(client_max_size=MAX_BODY_BYTES)
        app.router.add_post(self.settings.path, self._handle_request)
        app.router.add_get(f"{self.settings.path}/health", self._handle_health)
        return app

    async def disconnect(self) -> None:
        self._mark_disconnected()
        for task in list(self._background):
            task.cancel()
        if self._runner is not None:
            with contextlib.suppress(Exception):
                await self._runner.cleanup()
            self._runner = None
        with contextlib.suppress(Exception):
            self._release_platform_lock()

    async def get_chat_info(self, chat_id: str) -> dict[str, Any]:
        return {"name": "Echo", "type": "dm"}

    # -- outbound -------------------------------------------------------------------------

    async def send(self, chat_id: str, content: str, reply_to: str | None = None,
                   metadata: dict[str, Any] | None = None) -> SendResult:
        content = content or ""
        waiting = self._waiting.get(chat_id)
        if waiting and (not waiting.open or (reply_to and reply_to != waiting.request_id)):
            waiting = None  # a reply to an earlier request: announce it, don't answer this one
        if _is_status(content, metadata, waiting=waiting is not None):
            if waiting:
                waiting.status = content
            return SendResult(success=True)  # status chatter is not worth an announcement
        if waiting:
            waiting.parts.append(content)
            waiting.arrived.set()
            return SendResult(success=True, message_id=uuid.uuid4().hex)
        return await self._announce(chat_id, content)

    async def _announce(self, chat_id: str, content: str) -> SendResult:
        text = skill.speakable(content)
        if not text:
            return SendResult(success=True)
        try:
            await asyncio.to_thread(bridge.announce, chat_id, text)
        except bridge.BridgeError as exc:
            logger.warning("Alexa: could not announce on %s: %s", chat_id, exc)
            return SendResult(success=False, error=str(exc))
        return SendResult(success=True, message_id=uuid.uuid4().hex)

    # -- inbound --------------------------------------------------------------------------

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "platform": PLATFORM})

    async def _handle_request(self, request: web.Request) -> web.Response:
        started = asyncio.get_running_loop().time()  # Alexa's ~8 s count from here
        if request.headers.get("Content-Encoding", "identity").lower() != "identity":
            return web.json_response({"error": "bad request"}, status=400)  # Amazon never compresses
        body = await request.read()
        try:
            payload = await self.verifier.verify(request.headers, body)
        except RequestRejected as exc:
            logger.warning("Alexa: rejected request: %s", exc)
            return web.json_response({"error": "bad request"}, status=400)
        turn = skill.parse(payload)
        if turn.user_id not in self._known_users:
            self._known_users.add(turn.user_id)
            logger.info("Alexa: request from user %s on %s", turn.user_id, turn.chat_id)
        return web.json_response(await self._answer(turn, payload, started))

    async def _answer(self, turn: skill.Turn, payload: dict[str, Any],
                      started: float) -> dict[str, Any]:
        say = lambda key: skill.say(turn.locale, key)  # noqa: E731
        if turn.kind == "ended":
            return skill.empty_response()
        if turn.kind == "launch":
            return skill.response(say("launch"), end=False, reprompt=say("reprompt"),
                                  conversation=True)
        if turn.kind == "stop":
            return skill.response(say("stop"))
        if turn.kind in ("help", "empty", "fallback"):
            return skill.response(say(turn.kind), end=False, reprompt=say("reprompt"),
                                  conversation=turn.conversation)
        if turn.kind != "ask":
            return skill.response(say("fallback"), end=False, conversation=turn.conversation)
        reply = await self._ask_hermes(turn, started)
        self._learn_device(turn, payload)
        if reply is None:
            return skill.response(say("deferred"))
        if turn.conversation:
            return skill.response(reply, end=False, reprompt=say("more"), conversation=True)
        return skill.response(reply)

    async def _ask_hermes(self, turn: skill.Turn, started: float) -> str | None:
        """Give the request to Hermes; its reply as speech if it comes in time, else None."""
        waiting = _Waiting(request_id=turn.request_id or uuid.uuid4().hex)
        self._waiting[turn.chat_id] = waiting
        source = self.build_source(chat_id=turn.chat_id, chat_name="Echo", chat_type="dm",
                                   user_id=turn.user_id)
        loop = asyncio.get_running_loop()
        deadline = started + self.settings.reply_seconds
        try:
            await self.handle_message(MessageEvent(
                text=turn.text, message_type=MessageType.TEXT, source=source,
                user_id=turn.user_id, message_id=waiting.request_id))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(waiting.arrived.wait(), max(0.0, deadline - loop.time()))
                await asyncio.sleep(min(SETTLE_S, max(0.0, deadline - loop.time())))
        finally:
            waiting.open = False
            if self._waiting.get(turn.chat_id) is waiting:
                del self._waiting[turn.chat_id]
        text = skill.speakable(" ".join(waiting.parts) or waiting.status)
        return text or None

    def _learn_device(self, turn: skill.Turn, payload: dict[str, Any]) -> None:
        """First request from an Echo: let the bridge find out which one it is, for later
        announcements (Alexa's voice history shows it a few seconds after the request)."""
        if turn.chat_id in self._known_devices:
            return
        self._known_devices.add(turn.chat_id)
        since = _epoch_ms(str((payload.get("request") or {}).get("timestamp") or ""))

        async def learn() -> None:
            await asyncio.sleep(LEARN_DEVICE_AFTER_S)
            try:
                name = await asyncio.to_thread(bridge.learn_skill_device, turn.chat_id, since)
                logger.info("Alexa: %s is the Echo '%s'", turn.chat_id, name)
            except bridge.BridgeError as exc:
                self._known_devices.discard(turn.chat_id)  # try again on the next request
                logger.info("Alexa: could not tell which Echo %s is yet: %s", turn.chat_id, exc)

        task = asyncio.create_task(learn())
        self._background.add(task)
        task.add_done_callback(self._background.discard)
