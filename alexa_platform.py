"""Alexa as a Hermes gateway platform: settings and registration.

Kept light on purpose: ``register(ctx)`` runs in every ``hermes`` command, so the adapter
(aiohttp, cryptography, gateway internals) is imported only when the gateway builds it.
"""

from __future__ import annotations

import asyncio
import dataclasses
import importlib.util
import os
from dataclasses import dataclass
from typing import Any

from . import alexa_bridge as bridge
from .alexa_skill import MAX_SPEECH_CHARS, speakable

PLATFORM = "alexa"
DEFAULT_HOST = "127.0.0.1"  # the tunnel (e.g. Tailscale Funnel) forwards to loopback
DEFAULT_PORT = 8796
DEFAULT_PATH = "/alexa"
DEFAULT_REPLY_SECONDS = 5.5  # Alexa gives a skill 8 s; leave room for the tunnel
HOME_DEFAULT = "default"  # cron deliver=alexa without a home channel: the only Echo

PLATFORM_HINT = (
    "You are talking through an Amazon Echo: Alexa reads your reply out loud. Answer in the "
    "user's language in one to three short spoken sentences. No markdown, lists, tables, code, "
    "emoji or URLs. If you need more than a few seconds the user hears 'I'll let you know' and "
    "your final answer is announced on the same Echo when ready, so just give the result, "
    "briefly. Do not call alexa_speak to answer: your reply already goes to the Echo.")

# What Hermes may use when a request comes by voice. Anyone near the Echo can speak (a guest,
# the TV), so no terminal, code, files, browser, computer control or memory writes. The owner
# can choose otherwise with ``platform_toolsets.alexa`` in config.yaml.
VOICE_TOOLSETS = ("web", "session_search", "todo", "alexa")


def voice_toolsets(user_config: dict | None) -> list[str] | None:
    """The toolsets for a voice request: None when the owner set ``platform_toolsets.alexa``
    (the gateway then uses that list), else the safe voice set."""
    chosen = ((user_config or {}).get("platform_toolsets") or {}).get(PLATFORM)
    return None if isinstance(chosen, list) else list(VOICE_TOOLSETS)


def _env(name: str) -> str:
    try:
        from gateway.platforms._shared import get_scoped_secret
    except ImportError:  # outside the gateway (tests, plain CLI)
        return os.environ.get(name, "").strip()
    return str(get_scoped_secret(name, "") or "").strip()


def _number(raw: Any, kind: type, default: Any) -> Any:
    try:
        return kind(raw)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    skill_ids: tuple[str, ...]
    host: str
    port: int
    path: str
    reply_seconds: float

    @classmethod
    def load(cls, config: Any = None) -> Settings:
        extra = getattr(config, "extra", None) or {}

        def pick(env: str, key: str, default: Any) -> Any:
            return _env(env) or extra.get(key) or default

        skills = str(pick("ALEXA_SKILL_ID", "skill_id", ""))
        path = "/" + str(pick("ALEXA_PATH", "path", DEFAULT_PATH)).strip("/")
        return cls(skill_ids=tuple(s.strip() for s in skills.split(",") if s.strip()),
                   host=str(pick("ALEXA_HOST", "host", DEFAULT_HOST)),
                   port=_number(pick("ALEXA_PORT", "port", DEFAULT_PORT), int, DEFAULT_PORT),
                   path=path,
                   reply_seconds=_number(pick("ALEXA_REPLY_SECONDS", "reply_seconds",
                                              DEFAULT_REPLY_SECONDS), float, DEFAULT_REPLY_SECONDS))


def check_requirements() -> bool:
    """Passive: a skill id is configured and the adapter's libraries import."""
    if not Settings.load().skill_ids:
        return False
    return all(importlib.util.find_spec(m) for m in ("aiohttp", "cryptography", "certifi"))


def validate_config(config: Any) -> bool:
    return bool(Settings.load(config).skill_ids)


def env_enablement() -> dict[str, Any] | None:
    """Enable the platform from ``.env`` alone (``ALEXA_SKILL_ID``), like the bundled ones."""
    if not _env("ALEXA_SKILL_ID"):
        return None
    from gateway.platforms._shared import seed_extra_from_env

    return seed_extra_from_env(
        [("ALEXA_SKILL_ID", "skill_id", None), ("ALEXA_HOST", "host", None),
         ("ALEXA_PORT", "port", int), ("ALEXA_PATH", "path", None),
         ("ALEXA_REPLY_SECONDS", "reply_seconds", float)],
        home_env="ALEXA_HOME_CHANNEL", home_default=HOME_DEFAULT)


async def standalone_send(pconfig: Any, chat_id: str, message: str, *, thread_id: str | None = None,
                          media_files: list[str] | None = None,
                          force_document: bool = False) -> dict[str, Any]:
    """Cron delivery when the gateway is not running: announce through the bridge."""
    text = speakable(message)
    if not text:
        return {"success": True, "message_id": None}
    try:
        await asyncio.to_thread(bridge.announce, chat_id, text)
    except bridge.BridgeError as exc:
        return {"success": False, "error": str(exc)}
    return {"success": True, "message_id": None}


def _make_adapter(config: Any) -> Any:
    from .alexa_adapter import AlexaAdapter

    return AlexaAdapter(config)


def _supported(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Drop PlatformEntry fields this Hermes version does not have (newer ones are optional)."""
    try:
        from gateway.platform_registry import PlatformEntry
    except ImportError:
        return kwargs
    known = {f.name for f in dataclasses.fields(PlatformEntry)}
    return {k: v for k, v in kwargs.items() if k in known}


def register_platform(ctx: Any) -> None:
    ctx.register_platform(
        name=PLATFORM, label="Alexa", adapter_factory=_make_adapter, check_fn=check_requirements,
        validate_config=validate_config, required_env=["ALEXA_SKILL_ID"],
        install_hint="hermes alexa setup", **_supported(dict(
            dashboard_description="Talk to Hermes from your Amazon Echo through your own Alexa skill.",
            is_connected=validate_config, env_enablement_fn=env_enablement,
            cron_deliver_env_var="ALEXA_HOME_CHANNEL", standalone_sender_fn=standalone_send,
            allowed_users_env="ALEXA_ALLOWED_USERS", allow_all_env="ALEXA_ALLOW_ALL_USERS",
            max_message_length=MAX_SPEECH_CHARS, emoji="🔵", display_tier="minimal",
            platform_hint=PLATFORM_HINT, allow_update_command=False)))
