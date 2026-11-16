"""Plugin side of the bridge: where it lives, how to install/start it, and how to call it.

Runs inside Hermes (Python >= 3.11) with the standard library only. The bridge itself is a
separate Python >= 3.13 process (see ``bridge/`` and ``docs/architecture.md``).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PLUGIN_DIR = Path(__file__).resolve().parent
BRIDGE_SOURCE = PLUGIN_DIR / "bridge"
DEFAULT_PORT = 8795
START_TIMEOUT_S = 25.0


class BridgeError(Exception):
    """Message is meant for the user/agent as-is."""


def data_dir() -> Path:
    if override := os.environ.get("HERMES_ALEXA_DATA_DIR"):
        return Path(override)
    from hermes_constants import get_hermes_home  # Hermes core; imported lazily for tests

    return get_hermes_home() / "plugin-data" / "hermes-alexa"


def venv_python(base: Path | None = None) -> Path:
    venv = (base or data_dir()) / "venv"
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def is_installed() -> bool:
    return venv_python().exists()


def has_session() -> bool:
    return (data_dir() / "login-data.json").exists()


def port() -> int:
    return int(os.environ.get("HERMES_ALEXA_BRIDGE_PORT", DEFAULT_PORT))


def _clean_env() -> dict[str, str]:
    """The bridge has its own venv: never let Hermes's Python paths leak into it."""
    return {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}


def find_uv() -> str | None:
    if found := shutil.which("uv"):
        return found
    try:
        from hermes_constants import get_hermes_home

        bundled = get_hermes_home() / "bin" / ("uv.exe" if os.name == "nt" else "uv")
        return str(bundled) if bundled.exists() else None
    except ImportError:
        return None


def install(run=subprocess.run) -> Path:
    """Create the bridge venv (Python 3.13, via uv) and install the bridge into it."""
    uv = find_uv()
    if not uv:
        raise BridgeError("uv is required to install the bridge: https://docs.astral.sh/uv/")
    base = data_dir()
    base.mkdir(parents=True, exist_ok=True)
    py = venv_python(base)
    env = _clean_env()
    if not py.exists():
        run([uv, "venv", "--python", "3.13", str(base / "venv")], check=True, env=env)
    run([uv, "pip", "install", "--python", str(py), "--upgrade", str(BRIDGE_SOURCE)],
        check=True, env=env)
    return py


class BridgeClient:
    def __init__(self, base: Path | None = None, port_: int | None = None) -> None:
        self.base = base or data_dir()
        self.port = port_ or port()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _token(self) -> str:
        path = self.base / "bridge-token"
        return path.read_text(encoding="utf-8").strip() if path.exists() else ""

    def request(self, method: str, path: str, body: dict[str, Any] | None = None,
                timeout: float = 30.0) -> dict[str, Any]:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self._token()}",
            "Content-Type": "application/json; charset=utf-8"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                message = json.loads(exc.read().decode("utf-8")).get("error") or str(exc)
            except (ValueError, UnicodeDecodeError):
                message = str(exc)
            raise BridgeError(message) from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise BridgeError(f"Bridge not reachable at {self.url}: {exc}") from exc

    def health(self) -> dict[str, Any] | None:
        try:
            return self.request("GET", "/health", timeout=3)
        except BridgeError:
            return None

    def start(self, popen=subprocess.Popen) -> None:
        if not venv_python(self.base).exists():
            raise BridgeError("The Alexa bridge is not installed. Run: hermes alexa setup")
        log = open(self.base / "bridge.log", "ab")  # noqa: SIM115 - handed to the child
        kwargs: dict[str, Any] = {"stdout": log, "stderr": subprocess.STDOUT,
                                  "stdin": subprocess.DEVNULL, "cwd": str(self.base),
                                  "env": _clean_env()}
        if os.name == "nt":
            kwargs["creationflags"] = (subprocess.DETACHED_PROCESS
                                       | subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            kwargs["start_new_session"] = True
        popen([str(venv_python(self.base)), "-I", "-m", "hermes_alexa_bridge", "serve",
               "--data-dir", str(self.base), "--port", str(self.port)], **kwargs)
        log.close()

    def ensure_running(self, popen=subprocess.Popen, sleep=time.sleep,
                       clock=time.monotonic) -> dict[str, Any]:
        if status := self.health():
            return status
        self.start(popen)
        deadline = clock() + START_TIMEOUT_S
        while clock() < deadline:
            sleep(0.5)
            if status := self.health():
                return status
        raise BridgeError(f"The Alexa bridge did not start; see {self.base / 'bridge.log'}")

    def stop(self) -> bool:
        if not self.health():
            return False
        self.request("POST", "/shutdown", {})
        return True


def run_login(run=subprocess.run) -> int:
    """Interactive Amazon login in the foreground (asks for password and 2FA code)."""
    py = venv_python()
    if not py.exists():
        raise BridgeError("Install the bridge first: hermes alexa setup")
    result = run([str(py), "-I", "-m", "hermes_alexa_bridge", "login",
                  "--data-dir", str(data_dir())], env=_clean_env())
    return result.returncode


# -- used by the Alexa platform (gateway) ---------------------------------------------------

SKILL_DEVICE_PREFIX = "echo-"
DEFAULT_TARGETS = {"", "default", "home"}


def echo_target(chat_id: str) -> dict[str, str]:
    """Bridge fields naming the Echo behind a Hermes chat id.

    ``echo-…`` is an Echo seen by the skill; ``default``/empty is the only (or only online)
    Echo; anything else is an Echo name or serial (e.g. a cron home channel).
    """
    chat_id = (chat_id or "").strip()
    if chat_id.startswith(SKILL_DEVICE_PREFIX):
        return {"alexa_device": chat_id}
    if chat_id.lower() in DEFAULT_TARGETS:
        return {}
    return {"device": chat_id}


def announce(chat_id: str, text: str) -> str:
    """Blocking: announce ``text`` on the Echo for ``chat_id``; returns the Echo's name."""
    client = BridgeClient()
    client.ensure_running()
    return str(client.request("POST", "/announce", {"text": text, **echo_target(chat_id)})
               .get("device") or "")


def check_session() -> int:
    """Blocking: prove the Amazon session still works by listing the Echos; their count."""
    client = BridgeClient()
    client.ensure_running()
    return len(client.request("GET", "/devices", timeout=20).get("devices") or [])


def learn_skill_device(chat_id: str, since_ms: int | None) -> str:
    """Blocking: have the bridge work out (and remember) which Echo ``chat_id`` is."""
    client = BridgeClient()
    client.ensure_running()
    body: dict[str, Any] = {"alexa_device": chat_id, "since_ms": since_ms}
    return str(client.request("POST", "/skill-device", body, timeout=20).get("device") or "")
