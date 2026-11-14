"""What a skill request asks for, and turning a Markdown answer into something to hear."""

from __future__ import annotations

import pytest
from alexa_fakes import alexa_request

from alexa_plugin.alexa_skill import MAX_SPEECH_CHARS, parse, response, short_id, speakable


@pytest.mark.parametrize("request_, kind, text", [
    (alexa_request(text="  qual a previsão  "), "ask", "qual a previsão"),
    (alexa_request(text=None), "empty", ""),
    (alexa_request("LaunchRequest"), "launch", ""),
    (alexa_request("SessionEndedRequest"), "ended", ""),
    (alexa_request(intent="AMAZON.CancelIntent"), "stop", ""),
    (alexa_request(intent="AMAZON.HelpIntent"), "help", ""),
    (alexa_request(intent="AMAZON.NavigateHomeIntent"), "fallback", ""),
])
def test_parse_kinds(request_, kind, text):
    turn = parse(request_)
    assert (turn.kind, turn.text) == (kind, text)


def test_ids_are_short_and_stable():
    turn = parse(alexa_request(text="oi"))
    assert turn.chat_id == short_id("echo", "amzn1.ask.device.TESTDEVICE")
    assert turn.chat_id.startswith("echo-") and len(turn.chat_id) == 17
    assert turn.user_id.startswith("amzn-") and turn.locale == "pt-BR"
    assert not turn.conversation
    assert parse(alexa_request(text="oi", attributes={"conversation": True})).conversation


def test_reprompt_only_when_the_session_stays_open():
    assert "reprompt" not in response("Oi", end=True, reprompt="Pode falar")["response"]
    assert response("Oi", end=False, reprompt="Pode falar")["response"]["reprompt"]


@pytest.mark.parametrize("markdown, spoken", [
    ("**Pronto!** Veja [aqui](https://example.com).", "Pronto! Veja aqui."),
    ("## Agenda\n- 9h reunião\n- 14h dentista", "Agenda. 9h reunião. 14h dentista."),
    ("Rode isto:\n```bash\nrm -rf /\n```\nFeito", "Rode isto: Feito."),
    ("Abra https://example.com/a?b=1 agora", "Abra agora."),
    ("Tudo certo 👍✅", "Tudo certo."),
    ("| a | b |\n|---|---|\n| 1 | 2 |", "a, b. 1, 2."),
    ("snake_case e 2*3*4", "snake_case e 2*3*4."),
])
def test_speakable(markdown, spoken):
    assert speakable(markdown) == spoken


def test_long_answers_are_cut_at_a_sentence():
    text = "Frase curta aqui. " * 1000
    out = speakable(text)
    assert len(out) <= MAX_SPEECH_CHARS and out.endswith(".")
