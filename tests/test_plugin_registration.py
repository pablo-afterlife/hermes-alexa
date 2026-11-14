"""register(ctx) wiring: five tools in the ``alexa`` toolset and the ``hermes alexa`` command,
all through the public plugin API."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

import alexa_plugin
from alexa_plugin import alexa_bridge, alexa_tools

TOOL_NAMES = ["alexa_speak", "alexa_command", "alexa_devices", "alexa_volume", "alexa_routines"]


class RecordingCtx:
    def __init__(self):
        self.tools, self.cli = [], []

    def register_tool(self, **kwargs):
        self.tools.append(kwargs)

    def register_cli_command(self, **kwargs):
        self.cli.append(kwargs)


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
