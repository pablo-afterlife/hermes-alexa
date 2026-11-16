"""The gateway adapter end to end over HTTP: Alexa request in, speech out (now or announced)."""

from __future__ import annotations

import asyncio
import dataclasses

import pytest
from aiohttp.test_utils import TestClient, TestServer
from alexa_fakes import NOW, SKILL_ID, FakeAmazon, alexa_request, body_of

from alexa_plugin import alexa_adapter, alexa_bridge
from alexa_plugin.alexa_skill import short_id
from alexa_plugin.alexa_verify import AlexaRequestVerifier

ECHO = short_id("echo", "amzn1.ask.device.TESTDEVICE")
USER = short_id("amzn", "amzn1.ask.account.TESTUSER")
AMAZON = FakeAmazon()


@pytest.fixture(autouse=True)
def alexa_platform_name():
    from gateway.config import Platform

    if "alexa" not in Platform._value2member_map_:
        Platform._add_pseudo_member("alexa")  # the gateway does this when the plugin registers


@pytest.fixture
def announced(monkeypatch):
    calls = {"announce": [], "learn": []}

    def announce(chat_id, text):
        calls["announce"].append((chat_id, text))
        return "Echo Dot de Pablo"

    def learn(chat_id, since_ms):
        calls["learn"].append((chat_id, since_ms))
        return "Echo Dot de Pablo"

    monkeypatch.setattr(alexa_bridge, "announce", announce)
    monkeypatch.setattr(alexa_bridge, "learn_skill_device", learn)
    monkeypatch.setattr(alexa_adapter, "LEARN_DEVICE_AFTER_S", 0)
    return calls


class Hermes:
    """Stands in for the gateway: records events and answers through ``adapter.send``."""

    def __init__(self, adapter, replies=(), delay=0.0):
        self.adapter, self.replies, self.delay, self.events = adapter, list(replies), delay, []
        adapter.handle_message = self.handle_message

    async def handle_message(self, event):  # returns at once, like the real one
        self.events.append(event)

        async def answer():
            await asyncio.sleep(self.delay)
            for content, metadata in self.replies:
                await self.adapter.send(event.source.chat_id, content, reply_to=event.message_id,
                                        metadata=metadata)

        asyncio.get_running_loop().create_task(answer())


def make_adapter(reply_seconds=0.3):
    from gateway.config import PlatformConfig

    adapter = alexa_adapter.AlexaAdapter(PlatformConfig(enabled=True, extra={
        "skill_id": SKILL_ID, "reply_seconds": reply_seconds}))
    adapter.verifier = AlexaRequestVerifier([SKILL_ID], fetch=AMAZON.fetch, roots=AMAZON.store,
                                            clock=lambda: NOW.timestamp())
    return adapter


def run(adapter, *requests, signed=True, after=0.0, headers=None):
    """POST each request to the skill endpoint; returns (status, json) per request."""

    async def go():
        out = []
        async with TestClient(TestServer(adapter.build_app())) as client:
            for request in requests:
                body = body_of(request)
                sent = {**(AMAZON.headers(body) if signed else {}), **(headers or {})}
                resp = await client.post("/alexa", data=body, headers=sent)
                out.append((resp.status, await resp.json()))
            await asyncio.sleep(after)  # let late replies and background tasks finish
        return out

    return asyncio.run(go())


def speech(answer):
    return answer["response"]["outputSpeech"]["text"]


def test_quick_reply_is_spoken_in_the_same_exchange(announced):
    adapter = make_adapter()
    hermes = Hermes(adapter, [("São **três** horas. 🕒", None)])
    [(status, answer)] = run(adapter, alexa_request(text="que horas são"), after=0.05)
    assert status == 200
    assert speech(answer) == "São três horas."
    assert answer["response"]["shouldEndSession"] is True
    (event,) = hermes.events
    assert (event.text, event.source.chat_id, event.source.user_id) == ("que horas são", ECHO, USER)
    assert announced["announce"] == []


def test_slow_reply_is_announced_on_the_same_echo(announced):
    adapter = make_adapter(reply_seconds=0.1)
    Hermes(adapter, [("Resumo pronto: 3 e-mails importantes.", None)], delay=0.3)
    [(_, answer)] = run(adapter, alexa_request(text="resume meus e-mails"), after=0.5)
    assert speech(answer) == "Tô vendo. Já te aviso por aqui."
    assert announced["announce"] == [(ECHO, "Resumo pronto: 3 e-mails importantes.")]


def test_first_request_from_an_echo_teaches_the_bridge_which_one_it_is(announced):
    adapter = make_adapter()
    Hermes(adapter, [("Oi.", None)])
    run(adapter, alexa_request(text="oi"), alexa_request(text="tudo bem"), after=0.1)
    assert announced["learn"] == [(ECHO, int(NOW.timestamp() * 1000))]


def test_status_chatter_is_never_announced_and_only_spoken_as_a_last_resort(announced):
    adapter = make_adapter(reply_seconds=0.2)
    Hermes(adapter, [("⏳ Ainda estou no pedido anterior.", {"_interim_send": True})])
    [(_, answer)] = run(adapter, alexa_request(text="e agora"), after=0.05)
    assert speech(answer) == "Ainda estou no pedido anterior."
    assert announced["announce"] == []


def test_open_skill_starts_a_conversation_that_stays_open(announced):
    adapter = make_adapter()
    Hermes(adapter, [("Amanhã faz sol.", None)])
    (_, hello), (_, answer) = run(
        adapter, alexa_request("LaunchRequest"),
        alexa_request(text="vai chover amanhã", new=False, attributes={"conversation": True}),
        after=0.05)
    assert hello["response"]["shouldEndSession"] is False and hello["sessionAttributes"]["conversation"]
    assert speech(answer) == "Amanhã faz sol."
    assert answer["response"]["shouldEndSession"] is False
    assert answer["response"]["reprompt"]["outputSpeech"]["text"] == "Mais alguma coisa?"


@pytest.mark.parametrize("request_, expected, ends", [
    (alexa_request(intent="AMAZON.StopIntent"), "Até mais.", True),
    (alexa_request(intent="AMAZON.HelpIntent"), "É só falar", False),
    (alexa_request(text=""), "O que você quer pedir", False),
    (alexa_request(intent="AMAZON.FallbackIntent", locale="en-US"), "Sorry", False),
])
def test_built_in_intents_never_reach_hermes(announced, request_, expected, ends):
    adapter = make_adapter()
    hermes = Hermes(adapter)
    [(_, answer)] = run(adapter, request_)
    assert speech(answer).startswith(expected)
    assert answer["response"]["shouldEndSession"] is ends
    assert hermes.events == []


def test_session_ended_gets_an_empty_answer(announced):
    adapter = make_adapter()
    [(status, answer)] = run(adapter, alexa_request("SessionEndedRequest"))
    assert (status, answer["response"]) == (200, {})


def test_unsigned_requests_never_reach_hermes(announced):
    adapter = make_adapter()
    hermes = Hermes(adapter, [("segredo", None)])
    [(status, _)] = run(adapter, alexa_request(text="apaga tudo"), signed=False)
    assert status == 400 and hermes.events == []


def test_messages_outside_a_request_are_announced(announced):
    adapter = make_adapter()
    result = asyncio.run(adapter.send("default", "Lembrete: **reunião** às 15h https://x.y/z"))
    assert result.success
    assert announced["announce"] == [("default", "Lembrete: reunião às 15h.")]


def test_announce_failure_is_reported(monkeypatch):
    def broken(chat_id, text):
        raise alexa_bridge.BridgeError("The Alexa bridge is not installed. Run: hermes alexa setup")

    monkeypatch.setattr(alexa_bridge, "announce", broken)
    monkeypatch.setattr(alexa_bridge, "check_session", lambda: 1)
    result = asyncio.run(make_adapter().send(ECHO, "Pronto."))
    assert not result.success and "hermes alexa setup" in result.error


@pytest.fixture
def alerts(monkeypatch):
    sent = []
    monkeypatch.setattr(alexa_adapter, "alert_owner",
                        lambda platform, text: sent.append((platform, text)) or True)
    return sent


def make_watched_adapter(monkeypatch, session):
    """An adapter that alerts on Telegram; ``session`` says whether the Amazon session works."""
    def check():
        if not session["ok"]:
            raise alexa_bridge.BridgeError("Amazon refused the session (401)")
        return 1

    monkeypatch.setattr(alexa_bridge, "check_session", check)
    adapter = make_adapter(reply_seconds=0.1)
    adapter.settings = dataclasses.replace(adapter.settings, alert_to="telegram")
    return adapter


def test_the_owner_hears_once_when_the_amazon_session_drops_and_when_it_is_back(monkeypatch,
                                                                                alerts):
    session = {"ok": True}
    adapter = make_watched_adapter(monkeypatch, session)

    async def checks():
        await adapter.check_session()  # fine from the start: nothing to say
        session["ok"] = False
        await adapter.check_session()
        await adapter.check_session()  # still down: no second alert
        session["ok"] = True
        await adapter.check_session()

    asyncio.run(checks())
    assert [platform for platform, _ in alerts] == ["telegram", "telegram"]
    assert "hermes alexa login" in alerts[0][1] and "401" in alerts[0][1]
    assert "works again" in alerts[1][1]


def test_with_the_session_down_alexa_does_not_promise_an_announcement(monkeypatch, alerts,
                                                                      announced):
    adapter = make_watched_adapter(monkeypatch, {"ok": False})
    asyncio.run(adapter.check_session())
    Hermes(adapter, [("Resumo pronto.", None)], delay=0.3)
    [(_, answer)] = run(adapter, alexa_request(text="resume meus e-mails"))
    assert "hermes alexa login" in speech(answer)


def test_a_failed_announcement_checks_the_session_and_alerts_the_owner(monkeypatch, alerts):
    adapter = make_watched_adapter(monkeypatch, {"ok": False})

    def broken(chat_id, text):
        raise alexa_bridge.BridgeError("Amazon refused the session (401)")

    monkeypatch.setattr(alexa_bridge, "announce", broken)

    async def go():
        await adapter.send(ECHO, "Pronto.")
        await asyncio.sleep(0.05)  # the check runs in the background

    asyncio.run(go())
    assert len(alerts) == 1


def test_a_late_reply_to_an_earlier_request_is_announced_not_given_as_this_answer(announced):
    adapter = make_adapter(reply_seconds=0.3)

    async def go():
        async with TestClient(TestServer(adapter.build_app())) as client:
            first = alexa_request(text="pergunta lenta")
            Hermes(adapter)  # first request: Hermes stays quiet within the window
            body = body_of(first)
            await client.post("/alexa", data=body, headers=AMAZON.headers(body))
            second = alexa_request(text="pergunta nova")
            body = body_of(second)
            post = asyncio.ensure_future(client.post("/alexa", data=body, headers=AMAZON.headers(body)))
            await asyncio.sleep(0.1)  # the first request's answer lands while the second waits
            await adapter.send(ECHO, "Resposta da lenta.", reply_to=first["request"]["requestId"])
            await adapter.send(ECHO, "Resposta da nova.", reply_to=second["request"]["requestId"])
            return await (await post).json()

    answer = asyncio.run(go())
    assert speech(answer) == "Resposta da nova."
    assert announced["announce"] == [(ECHO, "Resposta da lenta.")]


def test_an_answer_that_happens_to_start_with_a_status_emoji_is_still_announced(announced):
    asyncio.run(make_adapter().send(ECHO, "⚡ Energia: consumo caiu 10%."))
    assert announced["announce"] == [(ECHO, "Energia: consumo caiu 10%.")]


def test_compressed_bodies_are_refused(announced):
    adapter = make_adapter()
    hermes = Hermes(adapter, [("oi", None)])
    [(status, _)] = run(adapter, alexa_request(text="oi"), headers={"Content-Encoding": "gzip"})
    assert status == 400 and hermes.events == []


@pytest.mark.parametrize("config, expected", [
    ({}, list(alexa_adapter.voice_toolsets(None))),
    ({"platform_toolsets": {"alexa": ["web", "terminal"]}}, None),
    (RuntimeError("config unreadable"), list(alexa_adapter.voice_toolsets(None))),
])
def test_voice_requests_use_the_safe_toolsets_unless_the_owner_chose(monkeypatch, config,
                                                                     expected):
    import hermes_cli.config

    def load():
        if isinstance(config, Exception):
            raise config
        return config

    monkeypatch.setattr(hermes_cli.config, "load_config_readonly", load)
    assert make_adapter().toolsets_for_source(None) == expected


def test_the_echo_is_not_told_every_time_the_gateway_restarts():
    adapter = make_adapter()
    assert adapter.config.gateway_restart_notification is False
