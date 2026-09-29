"""Signed cookies for dashboard sign-in — the sign-in transaction and the session.

Both are a JSON payload, base64url-encoded, followed by an HMAC-SHA256 tag over it.

**Every session is revocable** (`20260928195000`). Both shapes below name a
`platform.auth_session` row by a random identifier (`sid`), and the API serves a session only
while that row is live — not revoked by logout, not rotated, not expired by the database clock.
The database holds :meth:`CookieSigner.session_token_hash` of the identifier, never the
identifier, so a database reader cannot build a cookie from it. A cookie without a `sid` is
refused, so no session can exist that logout could not revoke.

Each cookie kind signs under its own purpose label. A transaction cookie can therefore never
be replayed as a session cookie, even though both are signed with the same key.

Only the standard library is used. The payload is not encrypted: it carries the operator's
address and ids, which the operator already knows, and nothing that grants anything without
the tag.

**Two session shapes share the one session cookie.** An *operator session* (no `k` field — the
original shape, still what an operator with their own Google account gets) names an operator
by address and id, with the operator's `version` at sign-in (`ov`): a role, status or address
change bumps it by trigger, so the session ends on the next request. A *principal session* (`k = "principal"`) names a shared Google sign-in
(`platform.auth_principal`) and, once a profile is chosen, the operator selected through it,
together with the operator's, the profile's and the principal's `version` at selection time,
the Google authentication time, and the account it was signed in as (issuer and subject). Choosing or clearing a profile re-issues the cookie with the
same authentication time and the same expiry, so switching profile never extends a sign-in.
The shape is decided by a signed field, so neither can be passed off as the other. Every
re-issue also carries a **new** `sid`: the previous row is revoked as its successor is inserted.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlsplit

from starlette.requests import cookie_parser

#: Minimum length of `ORIGENLAB_AUTH_SESSION_SECRET`. 32 characters of `secrets.token_urlsafe`
#: output is ~190 bits; anything shorter is refused at startup rather than weakly accepted.
MIN_SESSION_SECRET_LENGTH = 32

#: The sign-in transaction (state, nonce, PKCE verifier) lives this long. Long enough to pick
#: an account and pass a second factor, short enough that a stale tab cannot finish.
TRANSACTION_TTL_SECONDS = 10 * 60

_SESSION_PURPOSE = b"origenlab.dashboard.session.v1"
_TRANSACTION_PURPOSE = b"origenlab.dashboard.signin.v1"
_SESSION_ID_PURPOSE = b"origenlab.dashboard.session-id.v1"


def new_session_id() -> str:
    """A fresh random session identifier (256 bits) for one `platform.auth_session` row."""
    return secrets.token_urlsafe(32)


class CookieRefused(Exception):
    """The cookie is absent, malformed, forged or expired."""


PRINCIPAL_SESSION_KIND = "principal"
#: How a principal session was established: Google sign-in, or the local development shortcut
#: (`profile_routes.py`), which production refuses to honour whatever the signature says.
PRINCIPAL_METHODS = ("google", "dev")


@dataclass(frozen=True)
class ProfileSelection:
    """The operator chosen through a principal, and the versions it was chosen at."""

    operator_id: str
    operator_version: int
    profile_version: int
    selected_at: int


@dataclass(frozen=True)
class PrincipalSession:
    """A shared Google sign-in, with or without a selected profile."""

    principal_id: str
    email: str
    subject: str
    principal_version: int
    auth_time: int
    exp: int
    profile: ProfileSelection | None = None
    method: str = "google"
    #: The verified ID token's issuer (canonical form); with `subject`, the Google account.
    issuer: str = "https://accounts.google.com"
    #: The random identifier of this session's `platform.auth_session` row. Required: a cookie
    #: without one is refused, so a session can never exist that logout could not revoke.
    sid: str = ""

    def rotated(self, profile: ProfileSelection | None, sid: str) -> "PrincipalSession":
        """The successor session: another profile (or none) and a new identifier, same expiry."""
        return replace(self, profile=profile, sid=sid)

    def with_profile(self, profile: ProfileSelection | None) -> "PrincipalSession":
        return replace(self, profile=profile)


@dataclass(frozen=True)
class OperatorSession:
    """An operator's own Google sign-in: who, at which operator version, and its session row."""

    email: str
    operator_id: str
    operator_version: int
    #: The random identifier of this session's `platform.auth_session` row. Required.
    sid: str


def _positive_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CookieRefused("malformed")
    return value


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 320:
        raise CookieRefused("malformed")
    return value


@dataclass(frozen=True)
class CookieNames:
    """Cookie names for one deployment.

    Over HTTPS the `__Host-` prefix is used: the browser then refuses the cookie unless it is
    `Secure`, has `Path=/` and names no `Domain`, so no sibling subdomain can set or shadow
    it. Over plain-HTTP loopback (local development) the prefix cannot be honoured and the
    unprefixed names are used instead.
    """

    session: str
    transaction: str

    @classmethod
    def for_secure(cls, secure: bool) -> "CookieNames":
        prefix = "__Host-" if secure else ""
        return cls(session=f"{prefix}origenlab_session", transaction=f"{prefix}origenlab_signin")


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    padded = text + "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


class CookieSigner:
    """Sign and verify the two cookie kinds with one key."""

    def __init__(self, secret: str) -> None:
        if len(secret or "") < MIN_SESSION_SECRET_LENGTH:
            raise ValueError(
                f"ORIGENLAB_AUTH_SESSION_SECRET must be at least {MIN_SESSION_SECRET_LENGTH} "
                "characters (generate one with `python -c 'import secrets; "
                "print(secrets.token_urlsafe(48))'`)"
            )
        self._key = secret.encode("utf-8")

    def session_token_hash(self, sid: str) -> bytes:
        """What `platform.auth_session.token_hash` holds for `sid`: a keyed HMAC, never `sid`."""
        return hmac.new(self._key, _SESSION_ID_PURPOSE + b"." + sid.encode("utf-8"),
                        hashlib.sha256).digest()

    def _tag(self, purpose: bytes, body: str) -> str:
        return _b64encode(hmac.new(self._key, purpose + b"." + body.encode("ascii"),
                                   hashlib.sha256).digest())

    def _dump(self, purpose: bytes, payload: dict[str, Any]) -> str:
        body = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
        return f"{body}.{self._tag(purpose, body)}"

    def _load(self, purpose: bytes, value: str | None, *, now: float) -> dict[str, Any]:
        if not value:
            raise CookieRefused("absent")
        body, sep, tag = value.partition(".")
        if not sep or not body or not tag:
            raise CookieRefused("malformed")
        if not hmac.compare_digest(tag, self._tag(purpose, body)):
            raise CookieRefused("bad signature")
        try:
            payload = json.loads(_b64decode(body))
        except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
            raise CookieRefused("malformed") from exc
        if not isinstance(payload, dict):
            raise CookieRefused("malformed")
        exp = payload.get("exp")
        if not isinstance(exp, int) or exp <= now:
            raise CookieRefused("expired")
        return payload

    # ---------------------------------------------------------------- transaction

    def dump_transaction(self, *, state: str, nonce: str, verifier: str, now: float) -> str:
        return self._dump(_TRANSACTION_PURPOSE, {
            "state": state,
            "nonce": nonce,
            "verifier": verifier,
            "exp": int(now) + TRANSACTION_TTL_SECONDS,
        })

    def load_transaction(self, value: str | None, *, now: float) -> dict[str, str]:
        payload = self._load(_TRANSACTION_PURPOSE, value, now=now)
        out: dict[str, str] = {}
        for key in ("state", "nonce", "verifier"):
            item = payload.get(key)
            if not isinstance(item, str) or not item:
                raise CookieRefused("malformed")
            out[key] = item
        return out

    # -------------------------------------------------------------------- session

    def dump_session(
        self, *, email_norm: str, operator_id: str, google_sub: str, operator_version: int,
        sid: str, ttl: int, now: float
    ) -> str:
        return self._dump(_SESSION_PURPOSE, {
            "email": email_norm,
            "operator_id": operator_id,
            "sub": google_sub,
            "ov": int(operator_version),
            "sid": sid,
            "iat": int(now),
            "exp": int(now) + int(ttl),
        })

    def load_session(self, value: str | None, *, now: float | None = None) -> OperatorSession:
        """An operator session, or refuse. A principal session is not one."""
        payload = self._load(_SESSION_PURPOSE, value, now=time.time() if now is None else now)
        if "k" in payload:
            raise CookieRefused("not an operator session")
        email = payload.get("email")
        operator_id = payload.get("operator_id")
        if not isinstance(email, str) or not email or not isinstance(operator_id, str):
            raise CookieRefused("malformed")
        return OperatorSession(email=email, operator_id=operator_id,
                               operator_version=_positive_int(payload.get("ov")),
                               sid=_text(payload.get("sid")))

    def dump_principal_session(self, session: PrincipalSession) -> str:
        payload: dict[str, Any] = {
            "k": PRINCIPAL_SESSION_KIND,
            "pid": session.principal_id,
            "email": session.email,
            "sub": session.subject,
            "iss": session.issuer,
            "pv": session.principal_version,
            "at": session.auth_time,
            "exp": session.exp,
            "m": session.method,
            "sid": session.sid,
        }
        if session.profile is not None:
            payload.update({
                "op": session.profile.operator_id,
                "opv": session.profile.operator_version,
                "prv": session.profile.profile_version,
                "sel": session.profile.selected_at,
            })
        return self._dump(_SESSION_PURPOSE, payload)

    def load_any_session(
        self, value: str | None, *, now: float | None = None
    ) -> OperatorSession | PrincipalSession:
        """An operator session (as :meth:`load_session`) or a :class:`PrincipalSession`."""
        at = time.time() if now is None else now
        payload = self._load(_SESSION_PURPOSE, value, now=at)
        kind = payload.get("k")
        if kind is None:
            return self.load_session(value, now=at)
        if kind != PRINCIPAL_SESSION_KIND:
            raise CookieRefused("malformed")
        method = payload.get("m")
        if method not in PRINCIPAL_METHODS:
            raise CookieRefused("malformed")
        profile = None
        if any(key in payload for key in ("op", "opv", "prv", "sel")):
            profile = ProfileSelection(
                operator_id=_text(payload.get("op")),
                operator_version=_positive_int(payload.get("opv")),
                profile_version=_positive_int(payload.get("prv")),
                selected_at=_positive_int(payload.get("sel")),
            )
        return PrincipalSession(
            principal_id=_text(payload.get("pid")),
            email=_text(payload.get("email")),
            subject=_text(payload.get("sub")),
            issuer=_text(payload.get("iss")),
            principal_version=_positive_int(payload.get("pv")),
            auth_time=_positive_int(payload.get("at")),
            exp=_positive_int(payload.get("exp")),
            profile=profile,
            method=method,
            sid=_text(payload.get("sid")),
        )


def read_cookie(headers: dict[str, str], name: str) -> str | None:
    """Return one cookie's value from a header mapping, or None.

    Header names are matched case-insensitively. Parsing is Starlette's own lenient parser —
    the one behind `request.cookies` — so an unrelated malformed cookie on the same host (any
    other app on `localhost`, say) cannot hide the session the way `http.cookies` would.
    """
    raw = next((v for k, v in headers.items() if k.lower() == "cookie"), None)
    if not raw:
        return None
    return cookie_parser(raw).get(name) or None


def is_secure_base_url(url: str) -> bool:
    return urlsplit(url).scheme == "https"
