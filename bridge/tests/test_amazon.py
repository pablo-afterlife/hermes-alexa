import asyncio
import json
import logging
from types import SimpleNamespace

import pytest

from hermes_alexa_bridge.amazon import AlexaAccount, AlexaError, NotLoggedIn, pick_device


def dev(name, serial=None, online=True):
    return SimpleNamespace(account_name=name, serial_number=serial or name.upper(),
                           model="Echo Dot", device_family="ECHO", online=online,
                           voice_control_supported=True)


SALA, QUARTO = dev("Echo Dot de Pablo", "G0"), dev("Echo do Quarto", "G1", online=False)


class TestPickDevice:
    def test_single_device_needs_no_name(self):
        assert pick_device([SALA], None) is SALA

    def test_only_online_device_is_the_default(self):
        assert pick_device([SALA, QUARTO], None) is SALA

    def test_several_online_devices_require_a_name(self):
        with pytest.raises(AlexaError, match="say which one"):
            pick_device([SALA, dev("Echo Show")], None)

    @pytest.mark.parametrize("wanted", ["G1", "echo do quarto", "ÉCHO  DO QUARTO", "quarto"])
    def test_matches_serial_name_accents_and_partial(self, wanted):
        assert pick_device([SALA, QUARTO], wanted) is QUARTO

    def test_ambiguous_partial_lists_candidates(self):
        with pytest.raises(AlexaError, match="several devices"):
            pick_device([SALA, QUARTO], "echo")

    def test_unknown_name_lists_devices(self):
        with pytest.raises(AlexaError, match="Echo Dot de Pablo"):
            pick_device([SALA], "cozinha")


class FakeApi:
    """Mimics the library: commands are POSTed later by a background task that only logs errors."""

    def __init__(self, fail=False):
        self.fail, self.sent = fail, []
        self._sequence_handler = SimpleNamespace(_tasks=set())

    def _enqueue(self, what):
        async def post():
            await asyncio.sleep(0.05)
            if self.fail:
                logging.getLogger("aioamazondevices.sequence").error("Failed to send batch")
                return
            self.sent.append(what)

        task = asyncio.ensure_future(post())
        self._sequence_handler._tasks.add(task)
        task.add_done_callback(self._sequence_handler._tasks.discard)

    async def call_alexa_speak(self, device, text):
        self._enqueue(("speak", device.account_name, text))

    async def call_routine(self, name):
        self._enqueue(("routine", name))

    async def update_routines(self):
        self.routines = ["Bom dia", "Boa noite"]


def account_with(api, tmp_path, devices=(SALA,)):
    acc = AlexaAccount(tmp_path)
    acc._api, acc._devices, acc._login_data = api, list(devices), {"site": "x"}
    acc._saved_snapshot = json.dumps(acc._login_data, sort_keys=True)
    return acc


async def test_speak_waits_until_the_command_really_left(tmp_path):
    api = FakeApi()
    target = await account_with(api, tmp_path).speak("Olá")
    assert api.sent == [("speak", "Echo Dot de Pablo", "Olá")]
    assert target.name == "Echo Dot de Pablo"


async def test_background_failure_becomes_an_error_instead_of_silence(tmp_path):
    with pytest.raises(AlexaError, match="Failed to send batch"):
        await account_with(FakeApi(fail=True), tmp_path).speak("Olá")


async def test_actions_need_a_session(tmp_path):
    with pytest.raises(NotLoggedIn, match="hermes alexa login"):
        await AlexaAccount(tmp_path).speak("Olá")


async def test_start_without_saved_session_says_how_to_log_in(tmp_path):
    with pytest.raises(NotLoggedIn, match="hermes alexa login"):
        await AlexaAccount(tmp_path).start(session=None)


async def test_routine_matches_name_loosely(tmp_path):
    api = FakeApi()
    assert await account_with(api, tmp_path).run_routine("boa NOITE") == "Boa noite"
    assert api.sent == [("routine", "Boa noite")]


async def test_refreshed_tokens_are_saved_back(tmp_path):
    acc = account_with(FakeApi(), tmp_path)
    acc._email = "me@example.com"
    acc._login_data["access_token"] = "new"  # the library mutates the dict in place
    await acc.speak("Olá")
    saved = json.loads((tmp_path / "login-data.json").read_text(encoding="utf-8"))
    assert saved == {"email": "me@example.com", "login_data": {"site": "x", "access_token": "new"}}


class HistoryApi(FakeApi):
    """Alexa's voice history: latest record per serial, timestamps in epoch ms."""

    def __init__(self, heard):
        super().__init__()
        self.heard, self.history_calls = heard, 0

    async def sync_history_state(self):
        self.history_calls += 1
        return {serial: SimpleNamespace(timestamp=ts) for serial, ts in self.heard.items()}


async def test_single_echo_is_the_skill_device_without_asking_amazon(tmp_path):
    api = HistoryApi({})
    target = await account_with(api, tmp_path).resolve_skill_device("echo-abc")
    assert (target.name, api.history_calls) == ("Echo Dot de Pablo", 0)


async def test_skill_device_is_the_echo_heard_last_and_is_remembered(tmp_path):
    api = HistoryApi({"G0": 1_000, "G1": 9_000})
    acc = account_with(api, tmp_path, devices=(SALA, QUARTO))
    assert (await acc.resolve_skill_device("echo-abc", since_ms=8_000)).serial == "G1"
    api.heard = {"G0": 20_000}  # someone else talks to the living room Echo later
    assert (await acc.resolve_skill_device("echo-abc")).serial == "G1"
    assert api.history_calls == 1
    assert json.loads((tmp_path / "skill-devices.json").read_text(encoding="utf-8")) == {"echo-abc": "G1"}


async def test_skill_device_unknown_when_history_has_nothing_recent(tmp_path):
    acc = account_with(HistoryApi({"G0": 1_000}), tmp_path, devices=(SALA, QUARTO))
    with pytest.raises(AlexaError, match="which Echo"):
        await acc.resolve_skill_device("echo-abc", since_ms=500_000)
    assert not (tmp_path / "skill-devices.json").exists()
