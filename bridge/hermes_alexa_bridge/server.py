"""Local HTTP API for the hermes-alexa plugin.

Listens on 127.0.0.1 only and requires a bearer token stored in the data dir, so other
users/processes on the machine cannot drive your Echo.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import secrets
from dataclasses import asdict
from pathlib import Path
from typing import Any, Awaitable, Callable

from aiohttp import ClientSession, web

from . import __version__
from .amazon import LIB_VERSION, AlexaAccount, AlexaError, NotLoggedIn

TOKEN_FILE = "bridge-token"
STATE_FILE = "bridge.json"
DEFAULT_PORT = 8795

log = logging.getLogger("hermes_alexa_bridge")
ACCOUNT = web.AppKey("account", AlexaAccount)
TOKEN = web.AppKey("token", str)
HTTP = web.AppKey("http", ClientSession)
Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


def load_or_create_token(data_dir: Path) -> str:
    path = Path(data_dir) / TOKEN_FILE
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    path.write_text(token, encoding="utf-8")
    return token


@web.middleware
async def _auth_and_errors(request: web.Request, handler: Handler) -> web.StreamResponse:
    sent = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(sent, request.app[TOKEN]):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        return await handler(request)
    except NotLoggedIn as exc:
        return web.json_response({"error": str(exc), "logged_in": False}, status=409)
    except AlexaError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except web.HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - surface anything unexpected to the caller
        log.exception("Unexpected error on %s", request.path)
        return web.json_response({"error": f"{type(exc).__name__}: {exc}"}, status=500)


async def _body(request: web.Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AlexaError("Body must be UTF-8 JSON.") from exc
    if not isinstance(data, dict):
        raise AlexaError("Body must be a JSON object.")
    return data


def _text(data: dict[str, Any], key: str = "text") -> str:
    value = str(data.get(key) or "").strip()
    if not value:
        raise AlexaError(f"'{key}' is required.")
    return value


async def health(request: web.Request) -> web.Response:
    account = request.app[ACCOUNT]
    return web.json_response({
        "ok": True, "version": __version__, "library": LIB_VERSION,
        "logged_in": account.logged_in, "site": account.site,
    })


async def devices(request: web.Request) -> web.Response:
    account = request.app[ACCOUNT]
    refreshed = await account.refresh_devices()
    return web.json_response({"devices": [asdict(d) for d in refreshed]})


def _action(method: str) -> Handler:
    async def handle(request: web.Request) -> web.Response:
        data = await _body(request)
        account = request.app[ACCOUNT]
        target = await getattr(account, method)(_text(data), data.get("device"))
        return web.json_response({"ok": True, "device": target.name})
    return handle


async def volume(request: web.Request) -> web.Response:
    data = await _body(request)
    try:
        level = int(data.get("volume"))
    except (TypeError, ValueError) as exc:
        raise AlexaError("'volume' must be an integer 0-100.") from exc
    target = await request.app[ACCOUNT].set_volume(level, data.get("device"))
    return web.json_response({"ok": True, "device": target.name, "volume": level})


async def routines(request: web.Request) -> web.Response:
    return web.json_response({"routines": await request.app[ACCOUNT].routines()})


async def run_routine(request: web.Request) -> web.Response:
    name = await request.app[ACCOUNT].run_routine(_text(await _body(request), "name"))
    return web.json_response({"ok": True, "routine": name})


async def reload_session(request: web.Request) -> web.Response:
    """Pick up a new session written by `hermes alexa login`."""
    await request.app[ACCOUNT].start(request.app[HTTP])
    return await health(request)


def _exit_gracefully() -> None:
    raise web.GracefulExit  # SystemExit subclass: escapes the loop, run_app cleans up


async def shutdown(request: web.Request) -> web.Response:
    """`hermes alexa stop`: answer first, then stop the server cleanly."""
    asyncio.get_running_loop().call_later(0.2, _exit_gracefully)
    return web.json_response({"ok": True, "stopping": True})


def build_app(account: AlexaAccount, token: str) -> web.Application:
    app = web.Application(middlewares=[_auth_and_errors])
    app[ACCOUNT], app[TOKEN] = account, token
    app.router.add_get("/health", health)
    app.router.add_get("/devices", devices)
    app.router.add_post("/speak", _action("speak"))
    app.router.add_post("/announce", _action("announce"))
    app.router.add_post("/command", _action("command"))
    app.router.add_post("/volume", volume)
    app.router.add_get("/routines", routines)
    app.router.add_post("/routine", run_routine)
    app.router.add_post("/reload", reload_session)
    app.router.add_post("/shutdown", shutdown)
    return app


def serve(data_dir: Path, port: int = DEFAULT_PORT) -> None:
    data_dir = Path(data_dir)
    account = AlexaAccount(data_dir)
    app = build_app(account, load_or_create_token(data_dir))

    async def on_startup(app: web.Application) -> None:
        app[HTTP] = ClientSession()
        try:
            await account.start(app[HTTP])
            log.info("Logged in (%s), %d Echo device(s)", account.site, len(account.devices()))
        except NotLoggedIn as exc:
            log.warning("%s", exc)
        (data_dir / STATE_FILE).write_text(
            json.dumps({"port": port, "pid": os.getpid(), "version": __version__}),
            encoding="utf-8")

    async def on_cleanup(app: web.Application) -> None:
        (data_dir / STATE_FILE).unlink(missing_ok=True)
        await app[HTTP].close()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    web.run_app(app, host="127.0.0.1", port=port, print=None)
