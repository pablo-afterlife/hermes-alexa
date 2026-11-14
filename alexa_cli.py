"""``hermes alexa setup|login|status|start|stop|say``."""

from __future__ import annotations

import subprocess

from . import alexa_bridge as bridge


def setup_cli(parser) -> None:
    parser.add_argument("action", nargs="?", default="status",
                        choices=["setup", "login", "status", "start", "stop", "say"])
    parser.add_argument("text", nargs="?", help="For `say`: what the Echo should say")
    parser.add_argument("--device", help="Echo name (default: the only one)")
    parser.add_argument("--announce", action="store_true", help="For `say`: chime first")


def _setup(args) -> None:
    print("Installing the Alexa bridge (Python 3.13 venv, via uv)...")
    try:
        bridge.install()
    except subprocess.CalledProcessError as exc:
        raise bridge.BridgeError(f"Bridge install failed: {exc}") from exc
    client = bridge.BridgeClient()
    if client.health():  # an older bridge may be running: restart on the new code
        client.stop()
    if not bridge.has_session():
        _login(args)
    _start(args)


def _login(args) -> None:
    if bridge.run_login() != 0:
        raise bridge.BridgeError("Amazon login failed.")
    client = bridge.BridgeClient()
    if client.health():
        client.request("POST", "/reload", {})


def _start(args) -> None:
    status = bridge.BridgeClient().ensure_running()
    print(f"Bridge running: v{status['version']}, aioamazondevices {status['library']}, "
          f"{'logged in to ' + status['site'] if status['logged_in'] else 'NOT logged in'}")


def _status(args) -> None:
    print(f"data dir:  {bridge.data_dir()}")
    print(f"installed: {bridge.is_installed()}")
    print(f"session:   {bridge.has_session()}")
    client = bridge.BridgeClient()
    status = client.health()
    if not status:
        print("bridge:    not running (starts on first use, or: hermes alexa start)")
        return
    print(f"bridge:    running v{status['version']} on {client.url}, site {status['site']}")
    for d in client.request("GET", "/devices")["devices"]:
        print(f"  - {d['name']} ({d['model']}, {'online' if d['online'] else 'offline'})")


def _stop(args) -> None:
    print("Bridge stopped." if bridge.BridgeClient().stop() else "Bridge was not running.")


def _say(args) -> None:
    if not args.text:
        raise bridge.BridgeError('Usage: hermes alexa say "text" [--device NAME] [--announce]')
    client = bridge.BridgeClient()
    client.ensure_running()
    result = client.request("POST", "/announce" if args.announce else "/speak",
                            {"text": args.text, "device": args.device})
    print(f"Sent to {result['device']}.")


_ACTIONS = {"setup": _setup, "login": _login, "status": _status, "start": _start,
            "stop": _stop, "say": _say}


def handle_cli(args) -> None:
    try:
        _ACTIONS[getattr(args, "action", None) or "status"](args)
    except bridge.BridgeError as exc:
        print(f"hermes alexa: {exc}")
        raise SystemExit(1) from exc
