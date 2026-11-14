"""Test doubles for the Alexa side: a private CA that plays Amazon, and signed requests."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime, timedelta, UTC

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from cryptography.x509.verification import Store

SKILL_ID = "amzn1.ask.skill.test-skill"
CERT_URL = "https://s3.amazonaws.com/echo.api/echo-api-cert-99.pem"
DEVICE_ID = "amzn1.ask.device.TESTDEVICE"
USER_ID = "amzn1.ask.account.TESTUSER"
NOW = datetime(2026, 11, 14, 20, 0, 0, tzinfo=UTC)


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeAmazon:
    """A root CA and a leaf certificate for ``echo-api.amazon.com`` (or another name)."""

    def __init__(self, dns_name: str = "echo-api.amazon.com", *, expired: bool = False):
        self.root_key, self.leaf_key = _key(), _key()
        start = NOW - timedelta(days=30)
        self.root = (
            x509.CertificateBuilder().subject_name(_name("Fake Root")).issuer_name(_name("Fake Root"))
            .public_key(self.root_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(start).not_valid_after(NOW + timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False,
                                         key_encipherment=False, data_encipherment=False,
                                         key_agreement=False, key_cert_sign=True, crl_sign=True,
                                         encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(self.root_key.public_key()),
                           critical=False)
            .sign(self.root_key, hashes.SHA256()))
        end = NOW - timedelta(days=1) if expired else NOW + timedelta(days=90)
        self.leaf = (
            x509.CertificateBuilder().subject_name(_name(dns_name)).issuer_name(self.root.subject)
            .public_key(self.leaf_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(start).not_valid_after(end)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(dns_name)]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(
                self.root_key.public_key()), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(self.leaf_key.public_key()),
                           critical=False)
            .sign(self.root_key, hashes.SHA256()))
        self.fetched: list[str] = []

    @property
    def store(self) -> Store:
        return Store([self.root])

    def chain_pem(self) -> bytes:
        return self.leaf.public_bytes(serialization.Encoding.PEM)

    async def fetch(self, url: str) -> bytes:
        self.fetched.append(url)
        return self.chain_pem()

    def sign(self, body: bytes) -> str:
        return base64.b64encode(self.leaf_key.sign(body, padding.PKCS1v15(), hashes.SHA256())).decode()

    def headers(self, body: bytes, url: str = CERT_URL) -> dict[str, str]:
        return {"SignatureCertChainUrl": url, "Signature-256": self.sign(body)}


def alexa_request(kind: str = "IntentRequest", *, text: str | None = None, intent: str = "PedidoIntent",
                  skill_id: str = SKILL_ID, at: datetime = NOW, locale: str = "pt-BR",
                  new: bool = True, attributes: dict | None = None) -> dict:
    request: dict = {"type": kind, "requestId": f"amzn1.echo-api.request.{uuid.uuid4()}",
                     "timestamp": at.strftime("%Y-%m-%dT%H:%M:%SZ"), "locale": locale}
    if kind == "IntentRequest":
        slots = {"pedido": {"name": "pedido", "value": text}} if text is not None else {}
        request["intent"] = {"name": intent, "confirmationStatus": "NONE", "slots": slots}
    if kind == "SessionEndedRequest":
        request["reason"] = "USER_INITIATED"
    app = {"applicationId": skill_id}
    return {
        "version": "1.0",
        "session": {"new": new, "sessionId": "amzn1.echo-api.session.1", "application": app,
                    "user": {"userId": USER_ID}, "attributes": attributes or {}},
        "context": {"System": {"application": app, "user": {"userId": USER_ID},
                               "device": {"deviceId": DEVICE_ID, "supportedInterfaces": {}}}},
        "request": request,
    }


def body_of(request: dict) -> bytes:
    return json.dumps(request).encode("utf-8")
