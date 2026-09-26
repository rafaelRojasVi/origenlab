"""`google_jwks.py`: the key cache and the RS256 check, without the network or the app."""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from origenlab_api.v2.google_jwks import (
    DEFAULT_CACHE_SECONDS,
    MAX_CACHE_SECONDS,
    GoogleJwks,
    JwksUnavailable,
    SignatureRefused,
    _cache_seconds,
    parse_jwks,
    verify_signature,
)

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _jwk(key: rsa.RSAPrivateKey, kid: str = "k1", **extra: Any) -> dict[str, Any]:
    n = key.public_key().public_numbers().n
    return {"kty": "RSA", "kid": kid, "n": _b64(n.to_bytes((n.bit_length() + 7) // 8, "big")),
            "e": "AQAB", **extra}


def _token(key: rsa.RSAPrivateKey = KEY, kid: str = "k1") -> str:
    head = _b64(json.dumps({"alg": "RS256", "kid": kid}).encode())
    body = _b64(json.dumps({"sub": "1"}).encode())
    sig = key.sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{head}.{body}.{_b64(sig)}"


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_a_token_signed_by_a_published_key_verifies() -> None:
    verify_signature(_token(), GoogleJwks(fetcher=lambda: ({"keys": [_jwk(KEY)]}, 3600)))


def test_the_key_set_is_cached_until_it_expires() -> None:
    fetches: list[int] = []
    clock = _Clock()

    def fetch() -> Any:
        fetches.append(1)
        return {"keys": [_jwk(KEY)]}, 600

    jwks = GoogleJwks(fetcher=fetch, clock=clock)
    verify_signature(_token(), jwks)
    clock.now = 599
    verify_signature(_token(), jwks)
    assert len(fetches) == 1
    clock.now = 600
    verify_signature(_token(), jwks)
    assert len(fetches) == 2


def test_an_unknown_kid_refetches_at_most_once_per_interval() -> None:
    fetches: list[int] = []
    clock = _Clock()

    def fetch() -> Any:
        fetches.append(1)
        return {"keys": [_jwk(KEY)]}, 3600

    jwks = GoogleJwks(fetcher=fetch, clock=clock)
    verify_signature(_token(), jwks)
    for _ in range(5):
        with pytest.raises(SignatureRefused):
            verify_signature(_token(kid="unknown"), jwks)
    assert len(fetches) == 1  # the refetch waits for MIN_REFRESH_INTERVAL_SECONDS
    clock.now = 61
    with pytest.raises(SignatureRefused):
        verify_signature(_token(kid="unknown"), jwks)
    assert len(fetches) == 2


@pytest.mark.parametrize("jwk", [
    _jwk(KEY, use="enc"),
    _jwk(KEY, alg="RS512"),
    {**_jwk(KEY), "kty": "EC"},
    _jwk(rsa.generate_private_key(public_exponent=65537, key_size=1024)),
])
def test_keys_that_are_not_rs256_signing_keys_are_ignored(jwk) -> None:
    with pytest.raises(JwksUnavailable):
        parse_jwks({"keys": [jwk]})


@pytest.mark.parametrize("body", [{}, {"keys": "x"}, {"keys": []}, {"keys": [{"kid": "k"}]}])
def test_an_unusable_key_set_is_unavailable(body) -> None:
    with pytest.raises(JwksUnavailable):
        GoogleJwks(fetcher=lambda: (body, 3600)).key_for("k1")


@pytest.mark.parametrize("header,expected", [
    (None, DEFAULT_CACHE_SECONDS),
    ("public, max-age=19836, must-revalidate, no-transform", 19836),
    ("max-age=999999", MAX_CACHE_SECONDS),
    ("no-store", DEFAULT_CACHE_SECONDS),
])
def test_cache_lifetime_follows_cache_control_within_bounds(header, expected) -> None:
    assert _cache_seconds(header) == expected


@pytest.mark.parametrize("token", [None, 42, "a.b", "a.b.c.d", "!!.e30.AA", "e30.e30.@@"])
def test_malformed_tokens_are_refused(token) -> None:
    with pytest.raises(SignatureRefused):
        verify_signature(token, GoogleJwks(fetcher=lambda: ({"keys": [_jwk(KEY)]}, 3600)))
