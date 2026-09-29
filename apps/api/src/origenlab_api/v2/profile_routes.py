"""Operator profiles behind a shared Google sign-in — the profile screen's routes.

`docs/ARCHITECTURE.md` §5.1. After Google proves the shared Workspace account, the session is a
*principal session* with no operator in it, and every CRM, workspace and command route answers
401 `profile_required` (`identity.ProfileRequired`). These routes are the only way on:

| Route | Does |
|---|---|
| `GET /auth/profiles` | the usable profiles of the signed-in principal: id, display name, a role label. Nothing of any other principal, no status, no PIN metadata |
| `POST /auth/profile/select` | `{"profile_id", "pin"}`: the API checks the PIN (`profile_auth.py`) and, on success, revokes the current session row and re-issues the session — new identifier, same expiry — with that operator bound into it. Selecting while a profile is selected is a switch |
| `POST /auth/profile/clear` | back to the profile screen: the current session row is revoked and its successor keeps the Google sign-in, loses the operator, and keeps the same expiry. Google is never signed out |
| `POST /auth/dev/principal-session` | **local development only** — see :func:`dev_principal_session` |

**What the browser can and cannot influence.** It names a profile id and a PIN, nothing else.
Which principal it speaks for comes from the signed cookie, re-checked against the database;
the profile must be linked to that principal (the lookup joins on both ids); the operator and
its role come from the database. There is no header, body field or cookie value a caller can
change to become another operator or to raise a role — the only operator a session can carry is
one whose PIN was verified here, at the versions signed with it.

**One public refusal.** Unknown profile, another principal's profile, wrong PIN, malformed PIN,
disabled profile or operator, lockout: all answer 401 `profile_selection_failed` with the same
body and no cookie. The reason is recorded in `platform.auth_event` and nowhere a caller can see
it. The *time* is not guaranteed equal — one Argon2 derivation is spent on every path, but the
database work differs by outcome (`profile_auth.py`).

**No PIN leaves this module.** The body is parsed by hand — never by a Pydantic model, whose
422 would echo the input back — and the PIN is handed to the repository and dropped. It is
never logged, returned, or put into an exception or an audit row.

**Cross-site requests.** The dashboard proxy is the Origin/CSRF boundary (`apps/dashboard-proxy`,
`authCommandRefusal`). Independently, both POSTs here require `Content-Type: application/json`,
which no cross-site form can send and no cross-site script can send without a CORS preflight the
API never grants; a `Sec-Fetch-Site: cross-site` request is refused outright; and the session
cookie is `SameSite=Lax`, `HttpOnly`, and `Secure` + `__Host-` over HTTPS.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response

from origenlab_api.v2.auth_session import (
    CookieNames,
    CookieRefused,
    CookieSigner,
    PrincipalSession,
    ProfileSelection,
    new_session_id,
    read_cookie,
)
from origenlab_api.v2.identity import IdentityRefused, check_principal_session
from origenlab_api.v2.profile_auth import ROLE_LABELS, PrincipalRecord, ProfileAuthRepository
from origenlab_api.v2.profile_pin import PinHasher

logger = logging.getLogger(__name__)

profile_router = APIRouter(prefix="/auth", tags=["auth"])
dev_profile_router = APIRouter(prefix="/auth/dev", tags=["auth"])

#: The one answer to every failed selection.
SELECTION_FAILED = "profile_selection_failed"
#: No valid shared sign-in behind the request.
PRINCIPAL_REQUIRED = "not signed in: sign in with Google Workspace"
#: The body of a POST here is a profile id and a PIN; nothing near this size is legitimate.
MAX_BODY_BYTES = 1024
_RESERVED_DEV_SUFFIXES = (".invalid", ".test", ".example", ".localhost", "@example.com",
                          "@example.org", "@example.net")


@dataclass(frozen=True)
class ProfileLoginConfig:
    """Everything the profile routes need, fixed at startup."""

    profiles: ProfileAuthRepository
    hasher: PinHasher
    signer: CookieSigner
    cookie_names: CookieNames
    secure_cookies: bool
    session_ttl_seconds: int
    production: bool
    #: Local development only: the invented principal the dev shortcut signs in as.
    dev_principal_email: str | None = None


def is_reserved_dev_address(email: str) -> bool:
    """True for an address under a domain reserved for testing (RFC 2606 / RFC 6761)."""
    value = (email or "").strip().lower()
    return "@" in value and value.endswith(_RESERVED_DEV_SUFFIXES)


def _config(request: Request) -> ProfileLoginConfig:
    return request.app.state.v2_profile_login


def _json(status: int, content: dict[str, Any]) -> JSONResponse:
    return JSONResponse(status_code=status, content=content,
                        headers={"Cache-Control": "no-store"})


def set_session_cookie(response: Response, config: ProfileLoginConfig, session: PrincipalSession,
                       *, now: float) -> None:
    response.set_cookie(
        config.cookie_names.session,
        config.signer.dump_principal_session(session),
        max_age=max(1, int(session.exp - now)),
        path="/",
        secure=config.secure_cookies,
        httponly=True,
        samesite="lax",
    )


def _cross_site_refusal(request: Request, *, require_json: bool) -> JSONResponse | None:
    site = request.headers.get("sec-fetch-site")
    if site is not None and site not in ("same-origin", "same-site", "none"):
        return _json(403, {"detail": "cross_site_request"})
    if require_json:
        ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            return _json(415, {"detail": "unsupported_media_type"})
    return None


def principal_from_request(
    request: Request, config: ProfileLoginConfig
) -> tuple[PrincipalSession, PrincipalRecord] | None:
    """The valid principal session behind this request, or None. Never raises to the caller."""
    value = read_cookie(dict(request.headers), config.cookie_names.session)
    if value is None:
        return None
    try:
        session = config.signer.load_any_session(value)
    except CookieRefused:
        return None
    if not isinstance(session, PrincipalSession):
        return None
    try:
        principal, _ = check_principal_session(
            config.profiles, session, token_hash=config.signer.session_token_hash(session.sid),
            production=config.production)
    except IdentityRefused as exc:
        logger.info("principal session refused: %s", exc)
        return None
    return session, principal


async def _small_json_body(request: Request) -> Any:
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise ValueError("too large")
    if not raw:
        return {}
    return json.loads(raw)


# ---------------------------------------------------------------------------- routes


@profile_router.get("/profiles")
def list_profiles(request: Request) -> JSONResponse:
    config = _config(request)
    found = principal_from_request(request, config)
    if found is None:
        return _json(401, {"detail": PRINCIPAL_REQUIRED})
    _, principal = found
    cards = config.profiles.list_profiles(principal.principal_id)
    return _json(200, {
        "principal": {"email": principal.email_norm},
        "profiles": [card.public() for card in cards],
    })


@profile_router.post("/profile/select")
async def select_profile(request: Request) -> JSONResponse:
    config = _config(request)
    refusal = _cross_site_refusal(request, require_json=True)
    if refusal is not None:
        return refusal
    try:
        body = await _small_json_body(request)
    except (ValueError, UnicodeDecodeError):
        return _json(400, {"detail": "invalid_request"})
    if (not isinstance(body, dict) or set(body) != {"profile_id", "pin"}
            or not isinstance(body.get("profile_id"), str) or not isinstance(body.get("pin"), str)):
        return _json(400, {"detail": "invalid_request"})
    found = principal_from_request(request, config)
    if found is None:
        return _json(401, {"detail": PRINCIPAL_REQUIRED})
    session, principal = found

    now = time.time()
    new_sid = new_session_id()
    outcome = await run_in_threadpool(
        config.profiles.select_profile,
        principal_id=principal.principal_id,
        principal_email=principal.email_norm,
        principal_version=principal.version,
        requested_operator_id=body["profile_id"],
        pin=body["pin"],
        hasher=config.hasher,
        token_hash=config.signer.session_token_hash(session.sid),
        new_token_hash=config.signer.session_token_hash(new_sid),
        previous_operator_id=session.profile.operator_id if session.profile else None,
    )
    del body  # the PIN goes no further than the repository
    if not outcome.principal_valid:
        return _json(401, {"detail": PRINCIPAL_REQUIRED})
    if not outcome.ok or outcome.selected is None:
        logger.info("profile selection refused for principal %s", principal.principal_id)
        return _json(401, {"detail": SELECTION_FAILED})

    selected = outcome.selected
    new_session = session.rotated(ProfileSelection(
        operator_id=selected.operator_id,
        operator_version=selected.operator_version,
        profile_version=selected.profile_version,
        selected_at=int(now),
    ), new_sid)
    response = _json(200, {
        "authenticated": True,
        "state": "signed_in",
        "profile": {"id": selected.operator_id, "display_name": selected.display_name,
                    "role_label": ROLE_LABELS.get(selected.role, "Perfil")},
    })
    set_session_cookie(response, config, new_session, now=now)
    logger.info("profile selected: operator %s through principal %s",
                selected.operator_id, principal.principal_id)
    return response


@profile_router.post("/profile/clear")
async def clear_profile(request: Request) -> JSONResponse:
    config = _config(request)
    refusal = _cross_site_refusal(request, require_json=True)
    if refusal is not None:
        return refusal
    try:
        await _small_json_body(request)
    except (ValueError, UnicodeDecodeError):
        return _json(400, {"detail": "invalid_request"})
    found = principal_from_request(request, config)
    if found is None:
        return _json(401, {"detail": PRINCIPAL_REQUIRED})
    session, principal = found
    new_sid = new_session_id()
    cleared = await run_in_threadpool(
        config.profiles.clear_profile,
        principal_id=principal.principal_id, principal_email=principal.email_norm,
        token_hash=config.signer.session_token_hash(session.sid),
        new_token_hash=config.signer.session_token_hash(new_sid),
        previous_operator_id=session.profile.operator_id if session.profile else None,
    )
    if not cleared:
        return _json(401, {"detail": PRINCIPAL_REQUIRED})
    response = _json(200, {"authenticated": False, "state": "profile_required",
                           "detail": "profile_required"})
    set_session_cookie(response, config, session.rotated(None, new_sid), now=time.time())
    return response


# ------------------------------------------------------------------ local development


@dev_profile_router.post("/principal-session")
def dev_principal_session(request: Request) -> JSONResponse:
    """Sign in as the configured *invented* principal without Google. Local development only.

    Mounted only by `main._mount_profile_login`, which refuses production, a non-loopback
    database and any principal address outside a reserved test domain. It establishes the
    principal only: choosing a profile still takes its PIN, through the normal route, and the
    session is marked `dev`, which production refuses to honour whatever its signature says.
    """
    config = _config(request)
    refusal = _cross_site_refusal(request, require_json=False)
    if refusal is not None:
        return refusal
    email = config.dev_principal_email
    if config.production or not email:  # pragma: no cover - never mounted then
        return _json(404, {"detail": "not found"})
    principal = config.profiles.principal_by_email(email)
    if principal is None or not principal.is_active:
        return _json(401, {"detail": PRINCIPAL_REQUIRED})
    now = time.time()
    sid = new_session_id()
    session = PrincipalSession(
        principal_id=principal.principal_id, email=principal.email_norm, subject="dev-local",
        issuer="dev-local",
        principal_version=principal.version, auth_time=int(now),
        exp=int(now) + config.session_ttl_seconds, method="dev", sid=sid,
    )
    config.profiles.open_session(principal_id=principal.principal_id,
                                 token_hash=config.signer.session_token_hash(sid), expires_at=session.exp)
    response = _json(200, {"authenticated": False, "state": "profile_required",
                           "detail": "profile_required"})
    set_session_cookie(response, config, session, now=now)
    return response
