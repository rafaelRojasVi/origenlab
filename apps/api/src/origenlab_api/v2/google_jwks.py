"""Verify the signature of a Google ID token against Google's published signing keys (JWKS).

The ID token arrives over the back channel, from Google's token endpoint, so OpenID Connect
Core §3.1.3.7 (6) would allow TLS to stand in for the signature. The owner asked for the
signature check anyway, as production hardening: with it, a token is accepted only if Google's
key signed it, whatever path it took to reach the API.

Rules:

* **RS256 only.** The header's `alg` must be exactly `RS256` — the one algorithm Google signs
  ID tokens with. `none`, `HS256` and every other value are refused before any key is looked
  up, so an attacker cannot pick the algorithm.
* **The key is chosen by `kid`, from Google's set only.** The JWKS URL is a constant; the key
  set is never taken from the token (`jku`, `x5u`, `jwk` headers are ignored). A key must be
  `kty=RSA`, and if it states `use` or `alg` they must be `sig` and `RS256`.
* **Unknown `kid` refreshes once.** Google rotates keys; a `kid` missing from the cached set
  triggers one refetch (at most once per :data:`MIN_REFRESH_INTERVAL_SECONDS`), then refuses.
* **Fail closed.** A JWKS that cannot be fetched or parsed refuses the sign-in; there is no
  fallback to the unverified claims.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
JWKS_FETCH_TIMEOUT_SECONDS = 10
#: Used when Google's response carries no usable `Cache-Control: max-age`.
DEFAULT_CACHE_SECONDS = 3600
#: Upper bound on how long a fetched key set is trusted, whatever Cache-Control says.
MAX_CACHE_SECONDS = 24 * 3600
#: Lower bound between two fetches, so a stream of unknown `kid`s cannot hammer Google.
MIN_REFRESH_INTERVAL_SECONDS = 60
#: Smallest RSA modulus accepted. Google's keys are 2048-bit.
MIN_RSA_BITS = 2048

_MAX_AGE_RE = re.compile(r"(?:^|,)\s*max-age\s*=\s*(\d+)", re.IGNORECASE)


class JwksUnavailable(Exception):
    """Google's key set could not be fetched or parsed."""


class SignatureRefused(Exception):
    """The token's signature does not verify against Google's keys."""


#: A fetcher returns (parsed JSON body, cache lifetime in seconds).
JwksFetcher = Callable[[], tuple[dict[str, Any], int]]


def _cache_seconds(cache_control: str | None) -> int:
    match = _MAX_AGE_RE.search(cache_control or "")
    if not match:
        return DEFAULT_CACHE_SECONDS
    return max(0, min(int(match.group(1)), MAX_CACHE_SECONDS))


def fetch_google_jwks() -> tuple[dict[str, Any], int]:
    """GET Google's JWKS. The URL is a constant; nothing from a token or request reaches it."""
    request = urllib.request.Request(GOOGLE_JWKS_URL, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=JWKS_FETCH_TIMEOUT_SECONDS) as resp:
            body = json.loads(resp.read())
            ttl = _cache_seconds(resp.headers.get("Cache-Control"))
    except urllib.error.HTTPError as exc:
        raise JwksUnavailable(f"JWKS endpoint answered {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise JwksUnavailable(type(exc).__name__) from None
    if not isinstance(body, dict):
        raise JwksUnavailable("JWKS is not a JSON object")
    return body, ttl


def _b64url(segment: str) -> bytes:
    if not isinstance(segment, str) or not re.fullmatch(r"[A-Za-z0-9_-]*", segment):
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _rsa_key(jwk: Any) -> rsa.RSAPublicKey | None:
    """The RSA verification key a JWK describes, or None if it is not one we accept."""
    if not isinstance(jwk, dict) or jwk.get("kty") != "RSA":
        return None
    if jwk.get("use", "sig") != "sig" or jwk.get("alg", "RS256") != "RS256":
        return None
    try:
        n = int.from_bytes(_b64url(jwk["n"]), "big")
        e = int.from_bytes(_b64url(jwk["e"]), "big")
        key = rsa.RSAPublicNumbers(e, n).public_key()
    except (KeyError, TypeError, ValueError, binascii.Error):
        return None
    return key if key.key_size >= MIN_RSA_BITS else None


def parse_jwks(body: dict[str, Any]) -> dict[str, rsa.RSAPublicKey]:
    keys = body.get("keys")
    if not isinstance(keys, list):
        raise JwksUnavailable("JWKS has no keys array")
    parsed: dict[str, rsa.RSAPublicKey] = {}
    for jwk in keys:
        kid = jwk.get("kid") if isinstance(jwk, dict) else None
        key = _rsa_key(jwk)
        if isinstance(kid, str) and kid and key is not None:
            parsed[kid] = key
    if not parsed:
        raise JwksUnavailable("JWKS holds no usable RS256 key")
    return parsed


class GoogleJwks:
    """A cached copy of Google's signing keys. Thread-safe; one per API process."""

    def __init__(self, fetcher: JwksFetcher = fetch_google_jwks,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._fetcher = fetcher
        self._clock = clock
        self._lock = threading.Lock()
        self._keys: dict[str, rsa.RSAPublicKey] = {}
        self._expires_at = 0.0
        self._fetched_at: float | None = None

    def _refresh(self) -> None:
        body, ttl = self._fetcher()
        keys = parse_jwks(body)
        now = self._clock()
        self._keys, self._expires_at, self._fetched_at = keys, now + ttl, now

    def key_for(self, kid: str) -> rsa.RSAPublicKey:
        with self._lock:
            now = self._clock()
            if now >= self._expires_at:
                self._refresh()
            key = self._keys.get(kid)
            if key is None and (
                self._fetched_at is None or now - self._fetched_at >= MIN_REFRESH_INTERVAL_SECONDS
            ):
                # Google may have rotated since the last fetch.
                self._refresh()
                key = self._keys.get(kid)
            if key is None:
                raise SignatureRefused("token is signed with a key Google does not publish")
            return key


def verify_signature(id_token: Any, jwks: GoogleJwks) -> None:
    """Refuse unless `id_token` is an RS256 JWS signed by one of Google's current keys.

    Raises :class:`SignatureRefused` for a bad token and :class:`JwksUnavailable` when the key
    set cannot be obtained. Returns nothing: the claims are decoded and checked separately.
    """
    if not isinstance(id_token, str) or id_token.count(".") != 2:
        raise SignatureRefused("id_token is not a compact JWS")
    header_b64, claims_b64, signature_b64 = id_token.split(".")
    try:
        header = json.loads(_b64url(header_b64))
        signature = _b64url(signature_b64)
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise SignatureRefused("id_token header or signature is malformed") from None
    if not isinstance(header, dict):
        raise SignatureRefused("id_token header is not an object")
    if header.get("alg") != "RS256":
        raise SignatureRefused(f"id_token algorithm {header.get('alg')!r} is not RS256")
    if "crit" in header:
        raise SignatureRefused("id_token carries critical header extensions")
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise SignatureRefused("id_token names no signing key")
    key = jwks.key_for(kid)
    try:
        key.verify(
            signature,
            f"{header_b64}.{claims_b64}".encode("ascii"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except InvalidSignature:
        raise SignatureRefused("id_token signature does not verify") from None
