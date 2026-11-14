"""Bridge lifecycle from the plugin side: install, start on demand, clean environment."""

from __future__ import annotations

import subprocess

import pytest

from alexa_plugin import alexa_bridge as b


def fake_venv(base):
    py = b.venv_python(base)
    py.parent.mkdir(parents=True)
    py.touch()
    return py


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_start_without_install_points_to_setup(data_dir):
    with pytest.raises(b.BridgeError, match="hermes alexa setup"):
        b.BridgeClient().start(popen=lambda *a, **k: None)


def test_start_runs_isolated_python_without_hermes_paths(data_dir, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", r"C:\hermes-agent")
    py = fake_venv(data_dir)
    seen = {}
    b.BridgeClient().start(popen=lambda cmd, **kw: seen.update(cmd=cmd, **kw))
    assert seen["cmd"][:5] == [str(py), "-I", "-m", "hermes_alexa_bridge", "serve"]
    assert seen["cmd"][5:] == ["--data-dir", str(data_dir), "--port", "1"]
    assert "PYTHONPATH" not in seen["env"]
    assert seen["stdin"] is subprocess.DEVNULL


def test_ensure_running_starts_the_bridge_once_and_waits_for_health(data_dir, monkeypatch):
    fake_venv(data_dir)
    started, clock = [], Clock()
    healthy = iter([None, None, None, {"ok": True}])
    monkeypatch.setattr(b.BridgeClient, "health", lambda self: next(healthy))
    status = b.BridgeClient().ensure_running(
        popen=lambda *a, **k: started.append(1), sleep=clock.sleep, clock=clock)
    assert status == {"ok": True} and started == [1]


def test_ensure_running_gives_up_with_the_log_path(data_dir, monkeypatch):
    fake_venv(data_dir)
    clock = Clock()
    monkeypatch.setattr(b.BridgeClient, "health", lambda self: None)
    with pytest.raises(b.BridgeError, match="bridge.log"):
        b.BridgeClient().ensure_running(popen=lambda *a, **k: None, sleep=clock.sleep, clock=clock)


def test_already_running_bridge_is_reused(fake_bridge):
    assert b.BridgeClient().ensure_running(popen=pytest.fail)["logged_in"] is True


def test_install_creates_a_313_venv_then_installs_the_bridge(data_dir, monkeypatch):
    monkeypatch.setattr(b, "find_uv", lambda: "uv")
    commands = []
    b.install(run=lambda cmd, **kw: commands.append(cmd))
    assert commands[0] == ["uv", "venv", "--python", "3.13", str(data_dir / "venv")]
    assert commands[1][:4] == ["uv", "pip", "install", "--python"]
    assert commands[1][-1] == str(b.BRIDGE_SOURCE)


def test_install_without_uv_explains(data_dir, monkeypatch):
    monkeypatch.setattr(b, "find_uv", lambda: None)
    with pytest.raises(b.BridgeError, match="uv is required"):
        b.install(run=pytest.fail)


@pytest.mark.parametrize("chat_id, fields", [
    ("echo-0123456789ab", {"alexa_device": "echo-0123456789ab"}),
    ("default", {}),
    ("", {}),
    ("Echo do Quarto", {"device": "Echo do Quarto"}),
])
def test_echo_target_names_the_echo_behind_a_chat(chat_id, fields):
    assert b.echo_target(chat_id) == fields


def test_announce_and_learn_go_to_the_bridge(fake_bridge):
    fake_bridge.responses["/skill-device"] = (200, {"ok": True, "device": "Echo do Quarto"})
    assert b.announce("echo-0123456789ab", "Pronto.") == "Echo"
    assert b.learn_skill_device("echo-0123456789ab", 1700) == "Echo do Quarto"
    assert fake_bridge.calls() == [
        ("POST", "/announce", {"text": "Pronto.", "alexa_device": "echo-0123456789ab"}),
        ("POST", "/skill-device", {"alexa_device": "echo-0123456789ab", "since_ms": 1700})]
