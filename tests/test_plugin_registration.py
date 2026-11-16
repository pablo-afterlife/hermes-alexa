"""register(ctx) wiring: five tools in the ``alexa`` toolset, the ``hermes alexa`` command and
the ``alexa`` gateway platform, all through the public plugin API."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

import alexa_plugin
from alexa_plugin import alexa_bridge, alexa_platform, alexa_tools

TOOL_NAMES = ["alexa_speak", "alexa_command", "alexa_devices", "alexa_volume", "alexa_routines"]


class RecordingCtx:
    def __init__(self):
        self.tools, self.cli, self.platforms = [], [], []

    def register_tool(self, **kwargs):
        self.tools.append(kwargs)

    def register_cli_command(self, **kwargs):
        self.cli.append(kwargs)

    def register_platform(self, **kwargs):
        self.platforms.append(kwargs)


@pytest.fixture
def ctx():
    c = RecordingCtx()
    alexa_plugin.register(c)
    return c


def test_registers_the_five_tools_in_the_alexa_toolset(ctx):
    assert [t["name"] for t in ctx.tools] == TOOL_NAMES
    for tool in ctx.tools:
        assert tool["toolset"] == "alexa"
        assert tool["schema"]["name"] == tool["name"]
        assert tool["check_fn"] is alexa_tools.check_available


def test_manifest_declares_exactly_the_registered_tools(ctx):
    manifest = (Path(alexa_plugin.__file__).parent / "plugin.yaml").read_text(encoding="utf-8")
    declared = [line.strip()[2:] for line in manifest.splitlines() if line.startswith("  - alexa_")]
    assert declared == TOOL_NAMES


def test_registers_the_alexa_cli_command(ctx):
    (cmd,) = ctx.cli
    assert cmd["name"] == "alexa"
    parser = argparse.ArgumentParser()
    cmd["setup_fn"](parser)
    args = parser.parse_args(["say", "Olá", "--device", "sala", "--announce"])
    assert (args.action, args.text, args.device, args.announce) == ("say", "Olá", "sala", True)
    assert parser.parse_args([]).action == "status"


def test_tools_are_unavailable_until_installed_and_logged_in(data_dir):
    assert alexa_tools.check_available() is False
    vpy = alexa_bridge.venv_python(data_dir)
    vpy.parent.mkdir(parents=True)
    vpy.touch()
    assert alexa_tools.check_available() is False  # installed, but no Amazon session yet
    (data_dir / "login-data.json").write_text("{}", encoding="utf-8")
    assert alexa_tools.check_available() is True


def test_registers_alexa_as_a_voice_platform(ctx):
    (entry,) = ctx.platforms
    assert (entry["name"], entry["label"]) == ("alexa", "Alexa")
    assert entry["display_tier"] == "minimal"  # no tool progress read out loud
    assert entry["allowed_users_env"] == "ALEXA_ALLOWED_USERS"
    assert entry["cron_deliver_env_var"] == "ALEXA_HOME_CHANNEL"
    assert "alexa_speak" in entry["platform_hint"]  # the agent must not answer twice
    assert entry["allow_update_command"] is False  # no /update by voice


DANGEROUS = {"terminal", "execute_code", "computer_use", "write_file", "patch", "read_file",
             "browser_exec", "delegate_task", "memory", "skill_manage", "cronjob_manage",
             "manage_connections", "process_manage"}


def test_voice_requests_get_no_tool_that_acts_on_the_machine():
    import toolsets

    voice = alexa_platform.voice_toolsets({"platform_toolsets": {"cli": ["terminal"]}})
    tools = {tool for name in voice for tool in toolsets.resolve_toolset(name)}
    assert tools and not tools & DANGEROUS


def test_the_owner_can_choose_the_alexa_toolsets():
    chosen = {"platform_toolsets": {"alexa": ["web", "terminal"]}}
    assert alexa_platform.voice_toolsets(chosen) is None  # the gateway uses the owner's list


def test_platform_is_off_until_a_skill_id_is_set(monkeypatch):
    from gateway.config import PlatformConfig

    monkeypatch.delenv("ALEXA_SKILL_ID", raising=False)
    assert alexa_platform.check_requirements() is False
    assert alexa_platform.env_enablement() is None
    assert alexa_platform.validate_config(PlatformConfig(enabled=True, extra={})) is False
    monkeypatch.setenv("ALEXA_SKILL_ID", "amzn1.ask.skill.x")
    assert alexa_platform.check_requirements() is True
    seed = alexa_platform.env_enablement()
    assert seed["skill_id"] == "amzn1.ask.skill.x"
    assert seed["home_channel"]["chat_id"] == "default"  # cron deliver=alexa: the only Echo


def test_settings_come_from_env_over_config(monkeypatch):
    from gateway.config import PlatformConfig

    monkeypatch.setenv("ALEXA_PORT", "9000")
    s = alexa_platform.Settings.load(PlatformConfig(enabled=True, extra={
        "skill_id": "a, b", "port": 8000, "reply_seconds": "x", "path": "voz/"}))
    assert (s.skill_ids, s.port, s.reply_seconds, s.path) == (("a", "b"), 9000, 5.5, "/voz")


def test_cron_delivery_without_the_gateway_announces(monkeypatch):
    import asyncio

    sent = []
    monkeypatch.setattr(alexa_bridge, "announce", lambda chat_id, text: sent.append((chat_id, text)))
    result = asyncio.run(alexa_platform.standalone_send(None, "Sala", "# Bom dia\n- **sol**"))
    assert result["success"] and sent == [("Sala", "Bom dia. sol.")]
