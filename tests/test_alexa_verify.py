"""Only signed, fresh requests for this skill get through (Amazon's endpoint rules)."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from alexa_fakes import CERT_URL, NOW, SKILL_ID, FakeAmazon, alexa_request, body_of

from alexa_plugin.alexa_verify import AlexaRequestVerifier, RequestRejected, check_cert_url


@pytest.fixture(scope="module")
def amazon():
    return FakeAmazon()


def verifier(amazon, **kwargs):
    return AlexaRequestVerifier([SKILL_ID], fetch=amazon.fetch, roots=amazon.store,
                                clock=lambda: NOW.timestamp(), **kwargs)


def verify(v, headers, body):
    return asyncio.run(v.verify(headers, body))


def test_accepts_a_signed_request_and_caches_the_chain(amazon):
    v = verifier(amazon)
    amazon.fetched.clear()
    for _ in range(2):
        body = body_of(alexa_request(text="que horas são"))
        request = verify(v, amazon.headers(body), body)
        assert request["request"]["intent"]["slots"]["pedido"]["value"] == "que horas são"
    assert amazon.fetched == [CERT_URL]


@pytest.mark.parametrize("url", [
    "https://s3.amazonaws.com/echo.api/echo-api-cert.pem",
    "https://s3.amazonaws.com:443/echo.api/echo-api-cert.pem",
    "https://S3.AMAZONAWS.COM/echo.api/echo-api-cert.pem",
    "https://s3.amazonaws.com/echo.api/../echo.api/echo-api-cert.pem",
])
def test_cert_urls_amazon_uses(url):
    assert check_cert_url(url).startswith("https://s3.amazonaws.com/echo.api/")


@pytest.mark.parametrize("url", [
    "http://s3.amazonaws.com/echo.api/echo-api-cert.pem",
    "https://notamazon.com/echo.api/echo-api-cert.pem",
    "https://s3.amazonaws.com/EcHo.aPi/echo-api-cert.pem",
    "https://s3.amazonaws.com/invalid.path/echo-api-cert.pem",
    "https://s3.amazonaws.com:563/echo.api/echo-api-cert.pem",
    "https://s3.amazonaws.com/echo.api/../evil/cert.pem",
    "https://user@s3.amazonaws.com/echo.api/echo-api-cert.pem",
    "https://s3.amazonaws.com/echo.api/%2e%2e/evil/cert.pem",
    "https://s3.amazonaws.com/echo.api/..%2Fevil/cert.pem",
    "https://s3.amazonaws.com/echo.api/..\\evil/cert.pem",
    "",
])
def test_cert_urls_that_are_not_amazons(url):
    with pytest.raises(RequestRejected):
        check_cert_url(url)


def test_rejects_a_tampered_body(amazon):
    body = body_of(alexa_request(text="liga a luz"))
    headers = amazon.headers(body)
    with pytest.raises(RequestRejected, match="signature"):
        verify(verifier(amazon), headers, body.replace(b"liga", b"apaga"))


def test_rejects_missing_or_malformed_signature(amazon):
    body = body_of(alexa_request(text="oi"))
    for sig in ("", "not base64!!"):
        with pytest.raises(RequestRejected):
            verify(verifier(amazon), {"SignatureCertChainUrl": CERT_URL, "Signature-256": sig}, body)


def test_rejects_a_chain_from_an_untrusted_ca(amazon):
    impostor = FakeAmazon()
    body = body_of(alexa_request(text="oi"))
    v = AlexaRequestVerifier([SKILL_ID], fetch=impostor.fetch, roots=amazon.store,
                             clock=lambda: NOW.timestamp())
    with pytest.raises(RequestRejected, match="chain rejected"):
        verify(v, impostor.headers(body), body)


@pytest.mark.parametrize("fake", [FakeAmazon(dns_name="evil.example.com"), FakeAmazon(expired=True)])
def test_rejects_wrong_name_or_expired_certificate(fake):
    body = body_of(alexa_request(text="oi"))
    with pytest.raises(RequestRejected, match="chain rejected"):
        verify(verifier(fake), fake.headers(body), body)


@pytest.mark.parametrize("skew", [timedelta(seconds=151), timedelta(seconds=-151)])
def test_rejects_stale_or_future_requests(amazon, skew):
    body = body_of(alexa_request(text="oi", at=NOW - skew))
    with pytest.raises(RequestRejected, match="timestamp"):
        verify(verifier(amazon), amazon.headers(body), body)


def test_rejects_requests_for_another_skill(amazon):
    body = body_of(alexa_request(text="oi", skill_id="amzn1.ask.skill.someone-else"))
    with pytest.raises(RequestRejected, match="another skill"):
        verify(verifier(amazon), amazon.headers(body), body)


def test_refuses_everything_without_a_configured_skill_id(amazon):
    body = body_of(alexa_request(text="oi"))
    v = AlexaRequestVerifier([], fetch=amazon.fetch, roots=amazon.store, clock=lambda: NOW.timestamp())
    with pytest.raises(RequestRejected, match="no skill id"):
        verify(v, amazon.headers(body), body)


def test_the_same_request_is_accepted_only_once(amazon):
    v = verifier(amazon)
    body = body_of(alexa_request(text="desliga tudo"))
    verify(v, amazon.headers(body), body)
    with pytest.raises(RequestRejected, match="repeated"):
        verify(v, amazon.headers(body), body)


def test_a_failing_certificate_url_is_not_fetched_again_right_away(amazon):
    calls = []

    async def missing(url):
        calls.append(url)
        raise RequestRejected("certificate download failed (HTTP 403)")

    v = AlexaRequestVerifier([SKILL_ID], fetch=missing, roots=amazon.store,
                             clock=lambda: NOW.timestamp())
    body = body_of(alexa_request(text="oi"))
    for _ in range(3):
        with pytest.raises(RequestRejected):
            verify(v, amazon.headers(body), body)
    assert calls == [CERT_URL]
