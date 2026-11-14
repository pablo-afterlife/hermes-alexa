"""Test bootstrap: import the plugin directory as the package ``alexa_plugin``.

Mirrors how the Hermes loader imports a directory plugin as a package (relative imports
resolve). Hermes core (``tools.registry``) must be importable: run with a hermes-agent
checkout on PYTHONPATH (see README "Development").
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

PACKAGE = "alexa_plugin"
_ROOT = Path(__file__).resolve().parents[1]
sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != _ROOT]


def _load_plugin_package():
    if PACKAGE in sys.modules:
        return sys.modules[PACKAGE]
    spec = importlib.util.spec_from_file_location(
        PACKAGE, _ROOT / "__init__.py", submodule_search_locations=[str(_ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = module
    spec.loader.exec_module(module)
    return module


_load_plugin_package()

TOKEN = "test-token"


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    """Every test gets its own plugin data dir and bridge port; nothing touches the real ones."""
    base = tmp_path / "alexa"
    base.mkdir()
    (base / "bridge-token").write_text(TOKEN, encoding="utf-8")
    monkeypatch.setenv("HERMES_ALEXA_DATA_DIR", str(base))
    monkeypatch.setenv("HERMES_ALEXA_BRIDGE_PORT", "1")  # nothing listens there
    return base


class FakeBridge:
    """A real HTTP server speaking the bridge API, recording what the plugin sends."""

    def __init__(self):
        self.requests: list[tuple[str, str, dict | None, str]] = []
        self.responses: dict[str, tuple[int, dict]] = {}
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _reply(self, method):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                body = json.loads(raw.decode("utf-8")) if raw else None
                fake.requests.append((method, self.path, body, self.headers.get("Authorization")))
                status, payload = fake.responses.get(self.path, (200, {"ok": True, "device": "Echo"}))
                data = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._reply("GET")

            def do_POST(self):
                self._reply("POST")

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.responses["/health"] = (200, {"ok": True, "version": "0.1.0", "library": "17.2.11",
                                           "logged_in": True, "site": "https://www.amazon.com.br"})
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def calls(self):
        return [(m, p, b) for m, p, b, _ in self.requests if p != "/health"]


@pytest.fixture
def fake_bridge(monkeypatch):
    fake = FakeBridge()
    monkeypatch.setenv("HERMES_ALEXA_BRIDGE_PORT", str(fake.port))
    yield fake
    fake.server.shutdown()


class _RepoRootIsNotATestPackage:
    """The repo root is the plugin package (``__init__.py`` with relative imports): collect it
    as a plain directory so pytest never imports it standalone."""

    @pytest.hookimpl(tryfirst=True)
    def pytest_collect_directory(self, path, parent):
        if Path(path).resolve() == _ROOT:
            return pytest.Dir.from_parent(parent, path=path)
        return None


def pytest_configure(config):
    if not config.pluginmanager.has_plugin("alexa-repo-root"):
        config.pluginmanager.register(_RepoRootIsNotATestPackage(), "alexa-repo-root")
