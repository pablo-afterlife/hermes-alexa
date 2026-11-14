"""Tool handlers against a fake bridge speaking real HTTP."""

from __future__ import annotations

import json

from alexa_plugin import alexa_tools as t

TOKEN = "test-token"  # written by the data_dir fixture in conftest.py


def result(raw: str) -> dict:
    return json.loads(raw)


def test_speak_posts_utf8_text_with_the_token(fake_bridge):
    out = result(t.handle_speak({"text": "Olá, está tudo pronto", "device": "sala"}))
    assert out == {"ok": True, "device": "Echo"}
    assert fake_bridge.calls() == [("POST", "/speak", {"text": "Olá, está tudo pronto", "device": "sala"})]
    assert all(auth == f"Bearer {TOKEN}" for *_, auth in fake_bridge.requests)


def test_announce_flag_uses_the_announcement_endpoint(fake_bridge):
    t.handle_speak({"text": "Deploy terminou", "announce": True})
    assert fake_bridge.calls() == [("POST", "/announce", {"text": "Deploy terminou", "device": None})]


def test_command_is_sent_as_text_command(fake_bridge):
    t.handle_command({"command": "apaga a luz da sala"})
    assert fake_bridge.calls() == [("POST", "/command", {"text": "apaga a luz da sala", "device": None})]


def test_devices_volume_and_routines(fake_bridge):
    t.handle_devices({})
    t.handle_volume({"volume": 30, "device": "sala"})
    t.handle_routines({"action": "list"})
    t.handle_routines({"action": "run", "name": "Boa noite"})
    assert fake_bridge.calls() == [
        ("GET", "/devices", None),
        ("POST", "/volume", {"volume": 30, "device": "sala"}),
        ("GET", "/routines", None),
        ("POST", "/routine", {"name": "Boa noite"}),
    ]


def test_bridge_errors_reach_the_agent_verbatim(fake_bridge):
    fake_bridge.responses["/speak"] = (400, {"error": "No Echo named 'cozinha'. Devices: Echo Dot"})
    assert result(t.handle_speak({"text": "Oi", "device": "cozinha"})) == {
        "error": "No Echo named 'cozinha'. Devices: Echo Dot"}


def test_missing_arguments_never_reach_the_bridge(fake_bridge):
    assert "text is required" in t.handle_speak({"text": "  "})
    assert "command is required" in t.handle_command({})
    assert "volume is required" in t.handle_volume({})
    assert "name is required" in t.handle_routines({"action": "run"})
    assert "Unknown alexa_routines action" in t.handle_routines({"action": "delete"})
    assert fake_bridge.calls() == []


def test_not_installed_bridge_tells_how_to_fix():
    out = result(t.handle_speak({"text": "Oi"}))
    assert "hermes alexa setup" in out["error"]
