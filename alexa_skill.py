"""The Alexa custom-skill protocol: what a request asks for, and the JSON we answer with.

Pure functions, no Hermes imports: the gateway adapter (``alexa_adapter``) does the wiring.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

ASK_INTENT = "PedidoIntent"
ASK_SLOT = "pedido"
MAX_SPEECH_CHARS = 6000  # Alexa accepts 8000 in outputSpeech; leave room
CONVERSATION = "conversation"  # session attribute: opened with "Alexa, open <invocation>"

STRINGS = {
    "pt": {
        "launch": "Oi! O que você quer pedir pro Hermes?",
        "reprompt": "Pode falar.",
        "more": "Mais alguma coisa?",
        "empty": "O que você quer pedir pro Hermes?",
        "help": ("É só falar o que você quer, por exemplo: pede pro Hermes Agent resumir meus e-mails. "
                 "Se demorar, eu aviso aqui quando ficar pronto. O que você quer pedir?"),
        "stop": "Até mais.",
        "fallback": "Não entendi. Pode repetir?",
        "deferred": "Tô vendo. Já te aviso por aqui.",
        "no_announce": ("Tô vendo, mas não vou conseguir te avisar por aqui: a conexão com a "
                        "Amazon caiu. Roda hermes alexa login no computador."),
        "unavailable": "O Hermes não está disponível agora.",
    },
    "en": {
        "launch": "Hi! What do you want to ask Hermes?",
        "reprompt": "Go ahead.",
        "more": "Anything else?",
        "empty": "What do you want to ask Hermes?",
        "help": ("Just say what you need, for example: ask Hermes Agent to summarize my email. "
                 "If it takes a while, I'll let you know here when it's done. What do you want to ask?"),
        "stop": "Goodbye.",
        "fallback": "Sorry, I didn't get that. Can you say it again?",
        "deferred": "On it. I'll let you know here.",
        "no_announce": ("On it, but I can't let you know here: the Amazon connection is down. "
                        "Run hermes alexa login on the computer."),
        "unavailable": "Hermes is not available right now.",
    },
}

_STOP = {"AMAZON.StopIntent", "AMAZON.CancelIntent", "AMAZON.NoIntent"}


def say(locale: str, key: str) -> str:
    lang = (locale or "").split("-")[0].lower()
    return STRINGS.get(lang, STRINGS["en"])[key]


def short_id(prefix: str, raw: str) -> str:
    """Alexa ids are ~200 opaque chars; Hermes gets a short, stable stand-in."""
    return f"{prefix}-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:12]}"


@dataclass(frozen=True)
class Turn:
    """One Alexa request, reduced to what the adapter needs."""

    kind: str  # launch | ask | empty | help | stop | fallback | ended | other
    text: str
    chat_id: str  # one conversation per Echo
    user_id: str
    locale: str
    request_id: str
    conversation: bool  # the user opened the skill and is talking back and forth


def parse(request: dict[str, Any]) -> Turn:
    system = (request.get("context") or {}).get("System") or {}
    session = request.get("session") or {}
    req = request.get("request") or {}
    device_id = str((system.get("device") or {}).get("deviceId") or "unknown-device")
    user_id = str((system.get("user") or session.get("user") or {}).get("userId") or "unknown-user")
    conversation = bool((session.get("attributes") or {}).get(CONVERSATION))

    kind, text = "other", ""
    rtype = req.get("type")
    if rtype == "LaunchRequest":
        kind = "launch"
    elif rtype == "SessionEndedRequest":
        kind = "ended"
    elif rtype == "IntentRequest":
        intent = req.get("intent") or {}
        name = intent.get("name")
        if name == ASK_INTENT:
            text = str(((intent.get("slots") or {}).get(ASK_SLOT) or {}).get("value") or "").strip()
            kind = "ask" if text else "empty"
        elif name in _STOP:
            kind = "stop"
        elif name == "AMAZON.HelpIntent":
            kind = "help"
        else:  # AMAZON.FallbackIntent and anything the model did not expect
            kind = "fallback"
    return Turn(kind=kind, text=text, chat_id=short_id("echo", device_id),
                user_id=short_id("amzn", user_id), locale=str(req.get("locale") or "en-US"),
                request_id=str(req.get("requestId") or ""), conversation=conversation)


def response(text: str, *, end: bool = True, reprompt: str | None = None,
             conversation: bool = False) -> dict[str, Any]:
    body: dict[str, Any] = {"outputSpeech": {"type": "PlainText", "text": text},
                            "shouldEndSession": end}
    if reprompt and not end:
        body["reprompt"] = {"outputSpeech": {"type": "PlainText", "text": reprompt}}
    return {"version": "1.0", "sessionAttributes": {CONVERSATION: True} if conversation else {},
            "response": body}


def empty_response() -> dict[str, Any]:
    """Answer to SessionEndedRequest: Alexa ignores the body, it only needs a 200."""
    return {"version": "1.0", "response": {}}


_SPEECH_RULES: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"```.*?(```|$)", re.DOTALL), " "),            # code blocks: unspeakable
    (re.compile(r"!?\[([^\]]*)\]\([^)]*\)"), r"\1"),          # [label](url) -> label
    (re.compile(r"<?https?://\S+>?"), ""),                    # bare URLs
    (re.compile(r"`([^`]*)`"), r"\1"),
    (re.compile(r"(\*\*|__)(.+?)\1"), r"\2"),
    (re.compile(r"(?<![\w*])[*_](?!\s)(.+?)(?<!\s)[*_](?![\w*])"), r"\1"),
    (re.compile(r"^\s{0,3}(#{1,6}|>|[-*+•]|\d+[.)])\s+", re.MULTILINE), ""),
    (re.compile(r"^[ 	]*\|?[-:| 	]{3,}\|?[ 	]*$", re.MULTILINE), ""),  # table rulers
    (re.compile(r"[ 	]*\|[ 	]*"), ", "),
)


_JOINERS = {chr(0x200D), chr(0xFE0F)}  # glue inside emoji


def _drop_symbols(text: str) -> str:
    """Emoji and pictographs: Alexa either skips them or reads their names out loud."""
    return "".join(c for c in text if unicodedata.category(c) not in ("So", "Cs", "Co")
                   and c not in _JOINERS)


def speakable(text: str, limit: int = MAX_SPEECH_CHARS) -> str:
    """Markdown answer -> text that sounds right when Alexa reads it."""
    for pattern, repl in _SPEECH_RULES:
        text = pattern.sub(repl, text or "")
    text = _drop_symbols(text)
    lines = [ln.strip(" ,") for ln in text.splitlines()]
    sentences = []
    for line in filter(None, lines):
        sentences.append(line if line[-1] in ".!?:;…" else line + ".")
    text = re.sub(r"\s+", " ", " ".join(sentences)).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return (cut[: end + 1] if end > limit // 2 else cut.rsplit(" ", 1)[0] + "…").strip()
