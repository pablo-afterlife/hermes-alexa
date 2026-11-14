"""The bridge leans on a few internals of the pinned aioamazondevices version.

If an upgrade changes them, these fail loudly instead of the Echo going silent.
"""

import inspect

import aioamazondevices
from aioamazondevices.api import AmazonEchoApi
from aioamazondevices.const.metadata import SEQUENCE_BATCH_DELAY
from aioamazondevices.implementation.sequence import AmazonSequenceHandler


def test_pinned_version():
    assert aioamazondevices.__version__ == "17.2.11"


def test_commands_are_batched_in_background_tasks_we_can_await():
    assert 0 < SEQUENCE_BATCH_DELAY < 2
    init = inspect.getsource(AmazonSequenceHandler.__init__)
    assert "self._tasks" in init
    assert "_sequence_handler" in inspect.getsource(AmazonEchoApi.__init__)


def test_action_signatures_we_call():
    params = lambda f: list(inspect.signature(f).parameters)[1:]  # noqa: E731
    assert params(AmazonEchoApi.call_alexa_speak) == ["device", "text_to_speak"]
    assert params(AmazonEchoApi.call_alexa_announcement)[:2] == ["device", "text_to_announce"]
    assert params(AmazonEchoApi.call_alexa_text_command) == ["device", "text_command"]
    assert params(AmazonEchoApi.set_device_volume) == ["device", "volume"]
    assert params(AmazonEchoApi.call_routine) == ["routine_name"]
