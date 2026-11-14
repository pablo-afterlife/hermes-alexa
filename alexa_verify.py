"""Proof that a request really comes from Alexa, for this skill.

Amazon signs every request sent to a custom skill's HTTPS endpoint. The endpoint must check,
before trusting anything in the body:

1. ``SignatureCertChainUrl`` points at Amazon's bucket (https, ``s3.amazonaws.com``, path
   under ``/echo.api/``, port 443);
2. the certificate chain found there is valid now, issued for ``echo-api.amazon.com``, and
   chains to a trusted root;
3. ``Signature-256`` is that certificate's RSA/SHA-256 signature of the raw body;
4. the request timestamp is within 150 seconds of now;
5. the request is addressed to this skill (``applicationId``).

Reference: https://developer.amazon.com/docs/custom-skills/host-a-custom-skill-as-a-web-service.html
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import posixpath
import time
import warnings
from dataclasses import dataclass
from datetime import datetime, UTC
from typing import Any
from collections.abc import Awaitable, Callable, Iterable
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.utils import CryptographyDeprecationWarning
from cryptography.x509.verification import PolicyBuilder, Store, VerificationError

CERT_HOST = "s3.amazonaws.com"
CERT_PATH_PREFIX = "/echo.api/"
SIGNING_NAME = "echo-api.amazon.com"
MAX_SKEW_S = 150
MAX_CERT_BYTES = 64 * 1024
CERT_FETCH_TIMEOUT_S = 3.0
MAX_PARALLEL_FETCHES = 4
FAILED_FETCH_RETRY_S = 60.0  # a URL that failed is not fetched again for a minute
MAX_FAILED_URLS = 256

Fetch = Callable[[str], Awaitable[bytes]]


class RequestRejected(Exception):
    """The request is not a valid Alexa request for this skill; the message says why."""


def check_cert_url(url: str) -> str:
    """Return the normalized URL when it is one Amazon would use; raise otherwise."""
    try:
        parts = urlsplit(url or "")
        port = parts.port
    except ValueError as exc:
        raise RequestRejected("malformed certificate URL") from exc
    if "%" in parts.path or "\\" in parts.path:  # no encoded dots/slashes to fool the prefix check
        raise RequestRejected("certificate URL is not Amazon's")
    path = posixpath.normpath(parts.path) if parts.path else ""
    if parts.path.endswith("/") and not path.endswith("/"):
        path += "/"
    if (parts.scheme.lower() != "https" or (parts.hostname or "").lower() != CERT_HOST
            or port not in (None, 443) or not path.startswith(CERT_PATH_PREFIX)
            or parts.username or parts.password):
        raise RequestRejected("certificate URL is not Amazon's")
    return f"https://{CERT_HOST}{path}"


def load_roots(pem_paths: Iterable[str] | None = None) -> Store:
    """Trusted roots: certifi's bundle by default (it carries the CAs Amazon uses)."""
    if pem_paths is None:
        import certifi

        pem_paths = [certifi.where()]
    roots: list[x509.Certificate] = []
    for path in pem_paths:
        with open(path, "rb") as fh, warnings.catch_warnings():
            # a few old roots in the bundle have non-positive serials; harmless here
            warnings.simplefilter("ignore", CryptographyDeprecationWarning)
            roots.extend(x509.load_pem_x509_certificates(fh.read()))
    return Store(roots)


async def fetch_cert_chain(url: str) -> bytes:
    """Download the PEM chain (no redirects, bounded size and time)."""
    import aiohttp

    timeout = aiohttp.ClientTimeout(total=CERT_FETCH_TIMEOUT_S)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, allow_redirects=False) as resp:
            if resp.status != 200:
                raise RequestRejected(f"certificate download failed (HTTP {resp.status})")
            data = await resp.content.read(MAX_CERT_BYTES + 1)
    if len(data) > MAX_CERT_BYTES:
        raise RequestRejected("certificate chain too large")
    return data


@dataclass(frozen=True)
class _Chain:
    leaf: x509.Certificate

    def valid_at(self, now: datetime) -> bool:
        return self.leaf.not_valid_before_utc <= now <= self.leaf.not_valid_after_utc


def _parse_timestamp(value: Any) -> float:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError as exc:
        raise RequestRejected("bad request timestamp") from exc


def application_id(request: dict[str, Any]) -> str:
    system = ((request.get("context") or {}).get("System") or {})
    app = system.get("application") or ((request.get("session") or {}).get("application")) or {}
    return str(app.get("applicationId") or "")


class AlexaRequestVerifier:
    """Checks signature, certificate, timestamp and skill id; returns the parsed request."""

    def __init__(self, skill_ids: Iterable[str], *, fetch: Fetch = fetch_cert_chain,
                 roots: Store | None = None, clock: Callable[[], float] = time.time) -> None:
        self.skill_ids = {s.strip() for s in skill_ids if s and s.strip()}
        self._fetch = fetch
        self._roots = roots
        self._clock = clock
        self._chains: dict[str, _Chain] = {}
        self._failed: dict[str, float] = {}  # url -> when it may be tried again
        self._fetching = asyncio.Semaphore(MAX_PARALLEL_FETCHES)
        self._seen: dict[str, float] = {}  # requestId -> when it was accepted

    async def warm_up(self) -> None:
        """Load the trusted roots off the event loop (parsing certifi takes a moment)."""
        if self._roots is None:
            self._roots = await asyncio.to_thread(load_roots)

    def _now(self) -> datetime:
        return datetime.fromtimestamp(self._clock(), tz=UTC)

    async def _chain(self, url: str) -> _Chain:
        now = self._now()
        cached = self._chains.get(url)
        if cached and cached.valid_at(now):
            return cached
        if self._failed.get(url, 0.0) > self._clock():
            raise RequestRejected("certificate URL failed recently")
        try:
            chain = await self._load_chain(url, now)
        except RequestRejected:
            self._remember_failure(url)
            raise
        self._chains[url] = chain
        return chain

    async def _load_chain(self, url: str, now: datetime) -> _Chain:
        async with self._fetching:
            pem = await self._fetch(url)
        try:
            certs = x509.load_pem_x509_certificates(pem)
        except ValueError as exc:
            raise RequestRejected("certificate chain is not PEM") from exc
        await self.warm_up()
        verifier = (PolicyBuilder().store(self._roots).time(now)
                    .build_server_verifier(x509.DNSName(SIGNING_NAME)))
        try:
            verifier.verify(certs[0], certs[1:])
        except VerificationError as exc:
            raise RequestRejected(f"certificate chain rejected: {exc}") from exc
        if not isinstance(certs[0].public_key(), rsa.RSAPublicKey):
            raise RequestRejected("certificate key is not RSA")
        return _Chain(certs[0])

    def _remember_failure(self, url: str) -> None:
        now = self._clock()
        if len(self._failed) >= MAX_FAILED_URLS:
            self._failed = {u: t for u, t in self._failed.items() if t > now}
            if len(self._failed) >= MAX_FAILED_URLS:
                self._failed.clear()
        self._failed[url] = now + FAILED_FETCH_RETRY_S

    def _first_time(self, request_id: str) -> bool:
        """Each signed request is accepted once (the timestamp window bounds the memory)."""
        now = self._clock()
        self._seen = {r: t for r, t in self._seen.items() if t > now - 2 * MAX_SKEW_S}
        if request_id in self._seen:
            return False
        self._seen[request_id] = now
        return True

    async def verify(self, headers: Any, body: bytes) -> dict[str, Any]:
        if not self.skill_ids:
            raise RequestRejected("no skill id configured")
        url = check_cert_url(headers.get("SignatureCertChainUrl", ""))
        try:
            signature = base64.b64decode(headers.get("Signature-256", ""), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise RequestRejected("malformed signature") from exc
        if not signature:
            raise RequestRejected("missing signature")
        chain = await self._chain(url)
        try:
            chain.leaf.public_key().verify(signature, body, padding.PKCS1v15(), hashes.SHA256())
        except InvalidSignature as exc:
            raise RequestRejected("signature does not match the body") from exc
        try:
            request = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RequestRejected("body is not JSON") from exc
        if not isinstance(request, dict):
            raise RequestRejected("body is not a JSON object")
        sent_at = _parse_timestamp((request.get("request") or {}).get("timestamp"))
        if abs(self._clock() - sent_at) > MAX_SKEW_S:
            raise RequestRejected("request timestamp too old or in the future")
        if application_id(request) not in self.skill_ids:
            raise RequestRejected("request is for another skill")
        request_id = str((request.get("request") or {}).get("requestId") or "")
        if not request_id or not self._first_time(request_id):
            raise RequestRejected("missing or repeated requestId")
        return request
