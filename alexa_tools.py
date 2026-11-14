"""Alexa tools for Hermes (registered by this plugin's ``register(ctx)``).

Each handler starts the bridge if needed, calls it, and returns a JSON string.
"""

from __future__ import annotations

from typing import Any, Callable

from tools.registry import tool_error, tool_result

from . import alexa_bridge as bridge

TOOLSET = "alexa"

_DEVICE = {"type": "string", "description": (
    "Echo name (or part of it) as shown by alexa_devices. Omit when there is only one Echo.")}


def check_available() -> bool:
    """Cheap, offline: the bridge is installed and an Amazon session exists."""
    try:
        return bridge.is_installed() and bridge.has_session()
    except Exception:
        return False


def _schema(name: str, description: str, properties: dict, required: tuple = ()) -> dict:
    return {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": list(required)}}


ALEXA_SPEAK_SCHEMA = _schema(
    "alexa_speak",
    "Say something out loud on the user's Amazon Echo at home. Use announce=true to play the "
    "Alexa chime first (good for notifications the user should notice). Write the text in the "
    "language the Echo is set to, short and natural to hear: no markdown, lists or URLs.",
    {"text": {"type": "string", "description": "What the Echo should say"},
     "device": _DEVICE,
     "announce": {"type": "boolean", "description": "Chime first, like an Alexa announcement"}},
    ("text",))

ALEXA_COMMAND_SCHEMA = _schema(
    "alexa_command",
    "Give Alexa a command as if the user had spoken it to the Echo: smart-home devices "
    "(lights, plugs, TV), timers, alarms, music, weather, Alexa skills. Write it the way a "
    "person would say it, in the Echo's language (e.g. 'apaga a luz da sala'). Alexa answers "
    "out loud on the Echo; that spoken answer is NOT returned to you.",
    {"command": {"type": "string", "description": "The command, without the wake word"},
     "device": _DEVICE},
    ("command",))

ALEXA_DEVICES_SCHEMA = _schema(
    "alexa_devices", "List the Echo devices on the user's Amazon account and whether they are online.",
    {})

ALEXA_VOLUME_SCHEMA = _schema(
    "alexa_volume", "Set an Echo's volume (0-100).",
    {"volume": {"type": "integer", "minimum": 0, "maximum": 100}, "device": _DEVICE},
    ("volume",))

ALEXA_ROUTINES_SCHEMA = _schema(
    "alexa_routines", "List the user's Alexa routines, or run one by name.",
    {"action": {"type": "string", "enum": ["list", "run"]},
     "name": {"type": "string", "description": "Routine name, for action=run"}},
    ("action",))


def _call(fn: Callable[[bridge.BridgeClient], Any]) -> str:
    try:
        client = bridge.BridgeClient()
        client.ensure_running()
        return tool_result(fn(client))
    except bridge.BridgeError as exc:
        return tool_error(str(exc))


def _required_text(args: dict, key: str) -> str | None:
    value = str(args.get(key) or "").strip()
    return value or None


def handle_speak(args: dict, **_: Any) -> str:
    text = _required_text(args, "text")
    if not text:
        return tool_error("text is required")
    path = "/announce" if args.get("announce") else "/speak"
    return _call(lambda c: c.request("POST", path, {"text": text, "device": args.get("device")}))


def handle_command(args: dict, **_: Any) -> str:
    command = _required_text(args, "command")
    if not command:
        return tool_error("command is required")
    return _call(lambda c: c.request("POST", "/command",
                                     {"text": command, "device": args.get("device")}))


def handle_devices(args: dict, **_: Any) -> str:
    return _call(lambda c: c.request("GET", "/devices"))


def handle_volume(args: dict, **_: Any) -> str:
    if args.get("volume") is None:
        return tool_error("volume is required")
    return _call(lambda c: c.request("POST", "/volume",
                                     {"volume": args["volume"], "device": args.get("device")}))


def handle_routines(args: dict, **_: Any) -> str:
    action = args.get("action") or "list"
    if action == "list":
        return _call(lambda c: c.request("GET", "/routines"))
    if action == "run":
        name = _required_text(args, "name")
        if not name:
            return tool_error("name is required for action=run")
        return _call(lambda c: c.request("POST", "/routine", {"name": name}))
    return tool_error(f"Unknown alexa_routines action: {action}")


TOOLS = (
    ("alexa_speak", ALEXA_SPEAK_SCHEMA, handle_speak, "🔊"),
    ("alexa_command", ALEXA_COMMAND_SCHEMA, handle_command, "🗣️"),
    ("alexa_devices", ALEXA_DEVICES_SCHEMA, handle_devices, "📟"),
    ("alexa_volume", ALEXA_VOLUME_SCHEMA, handle_volume, "🔉"),
    ("alexa_routines", ALEXA_ROUTINES_SCHEMA, handle_routines, "🔁"),
)
