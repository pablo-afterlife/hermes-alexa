from types import SimpleNamespace

import pytest

from hermes_alexa_bridge.amazon import AlexaError, NotLoggedIn
from hermes_alexa_bridge.server import build_app, load_or_create_token

TOKEN = "secret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class FakeAccount:
    logged_in, site = True, "https://www.amazon.com.br"

    def __init__(self):
        self.calls = []

    async def speak(self, text, device):
        if text == "boom":
            raise AlexaError("Amazon rejected the command")
        self.calls.append(("speak", text, device))
        return SimpleNamespace(name=device or "Echo Dot de Pablo")

    async def announce(self, text, device):
        raise NotLoggedIn("Not logged in to Amazon. Run: hermes alexa login")

    async def resolve_skill_device(self, key, since_ms=None):
        self.calls.append(("resolve", key, since_ms))
        return SimpleNamespace(name="Echo do Quarto", serial="G1")

    async def set_volume(self, level, device):
        self.calls.append(("volume", level, device))
        return SimpleNamespace(name="Echo Dot de Pablo")


@pytest.fixture
async def client(aiohttp_client):
    account = FakeAccount()
    c = await aiohttp_client(build_app(account, TOKEN))
    c.account = account
    return c


async def test_rejects_requests_without_the_token(client):
    assert (await client.get("/health")).status == 401
    assert (await client.get("/health", headers={"Authorization": "Bearer nope"})).status == 401


async def test_health(client):
    body = await (await client.get("/health", headers=AUTH)).json()
    assert body["ok"] and body["logged_in"] and body["site"].endswith(".com.br")


async def test_speak(client):
    resp = await client.post("/speak", json={"text": "Olá", "device": "sala"}, headers=AUTH)
    assert resp.status == 200
    assert await resp.json() == {"ok": True, "device": "sala"}
    assert client.account.calls == [("speak", "Olá", "sala")]


async def test_missing_text_is_a_400(client):
    resp = await client.post("/speak", json={"text": "  "}, headers=AUTH)
    assert resp.status == 400 and "'text' is required" in (await resp.json())["error"]


async def test_non_utf8_body_is_a_400_not_a_crash(client):
    body = '{"text": "está"}'.encode("cp1252")  # what a Windows console curl sends
    resp = await client.post("/speak", data=body, headers={**AUTH, "Content-Type": "application/json"})
    assert resp.status == 400 and "UTF-8" in (await resp.json())["error"]


async def test_amazon_errors_are_400_with_the_message(client):
    resp = await client.post("/speak", json={"text": "boom"}, headers=AUTH)
    assert resp.status == 400 and "rejected" in (await resp.json())["error"]


async def test_not_logged_in_is_a_409(client):
    resp = await client.post("/announce", json={"text": "Oi"}, headers=AUTH)
    assert resp.status == 409 and (await resp.json())["logged_in"] is False


async def test_volume_validates_integer(client):
    bad = await client.post("/volume", json={"volume": "alto"}, headers=AUTH)
    assert bad.status == 400
    ok = await client.post("/volume", json={"volume": 40}, headers=AUTH)
    assert (await ok.json())["volume"] == 40


def test_token_is_created_once_and_reused(tmp_path):
    first = load_or_create_token(tmp_path)
    assert len(first) >= 40 and load_or_create_token(tmp_path) == first


async def test_speak_on_the_echo_the_skill_saw(client):
    resp = await client.post("/speak", json={"text": "Pronto", "alexa_device": "echo-abc"}, headers=AUTH)
    assert (await resp.json())["device"] == "G1"
    assert client.account.calls == [("resolve", "echo-abc", None), ("speak", "Pronto", "G1")]


async def test_resolve_skill_device(client):
    resp = await client.post("/skill-device", json={"alexa_device": "echo-abc", "since_ms": 1700},
                             headers=AUTH)
    assert await resp.json() == {"ok": True, "device": "Echo do Quarto", "serial": "G1"}
    assert client.account.calls == [("resolve", "echo-abc", 1700)]
    bad = await client.post("/skill-device", json={"alexa_device": "echo-abc", "since_ms": "ontem"},
                            headers=AUTH)
    assert bad.status == 400
