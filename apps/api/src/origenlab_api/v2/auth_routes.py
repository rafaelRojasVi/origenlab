"""Dashboard sign-in routes — Google Workspace login, the current session, logout.

| Route | Mounted when | Does |
|---|---|---|
| `GET /auth/google/login` | Google sign-in is on | sets the sign-in cookie (state, nonce, PKCE verifier) and redirects to Google |
| `GET /auth/google/callback` | Google sign-in is on | checks state, exchanges the code, verifies the ID token's RS256 signature against Google's JWKS, checks the claims, maps the address to `platform.operator`, sets the session cookie |
| `GET /auth/session` | the V2 boundary is mounted | who the current request resolves to, through the same identity port every `/v2` read uses |
| `POST /auth/logout` | the V2 boundary is mounted | revokes the session's row, then clears the session cookie |

**For an individual operator the callback's only write is the session row.** It reads
`platform.operator` through the read-only repository and records one `platform.auth_session` row
(`sign_in_kind = 'google_account'`, `auth_session_store.py`); it never creates an operator,
because operators are created by an admin command (`docs/ARCHITECTURE.md` §5), not by signing
in. An address with no row is refused, and so is a row that is not `active`. No row, no cookie:
a session that logout could not revoke is never issued.

**A shared Workspace account** (`docs/ARCHITECTURE.md` §5.1). When profile sign-in is on and
the verified address is a `platform.auth_principal`, the callback does not look for an operator
at all: it records a `platform.auth_session` row (the only write it makes) and issues a
*principal session* with no operator in it, and the dashboard shows the
profile screen (`profile_routes.py`). Every other route refuses that session with
`profile_required` until a profile is selected with its PIN. Logout revokes the session's own
row — either kind — and writes one `platform.auth_event`, before the cookie is cleared
(`logout`).

The callback always answers with a redirect to the dashboard — to its root on success, and to
`?login_error=<code>` otherwise. The code is one of a fixed set chosen here; nothing from the
query string is ever reflected into the redirect.
"""

from __future__ import annotations

import logging
import secrets
import time
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from origenlab_api.v2.auth_session import (
    TRANSACTION_TTL_SECONDS,
    CookieRefused,
    PrincipalSession,
    new_session_id,
    read_cookie,
)
from origenlab_api.v2.google_jwks import (
    GoogleJwks,
    JwksUnavailable,
    SignatureRefused,
    verify_signature,
)
from origenlab_api.v2.google_oidc import (
    DEFAULT_TOKEN_EXCHANGER,
    ClaimsRefused,
    GoogleAuthConfig,
    TokenExchangeFailed,
    decode_id_token,
    start_sign_in,
    validate_claims,
)
from origenlab_api.v2.identity import (
    PROFILE_REQUIRED,
    IdentityAbsent,
    IdentityPort,
    IdentityRefused,
    OperatorLookup,
    ProfileRequired,
)
from origenlab_api.v2.profile_auth import ROLE_LABELS

logger = logging.getLogger(__name__)

auth_router = APIRouter(prefix="/auth", tags=["auth"])
google_auth_router = APIRouter(prefix="/auth/google", tags=["auth"])

#: Roles that may use the dashboard at all (`docs/OPERATIONS.md` §2).
_DASHBOARD_ROLES = ("viewer", "sales", "admin")
#: Longest `code` or `state` accepted. Google's codes are a few hundred characters.
_MAX_PARAM_LENGTH = 2048

#: Every `login_error` the callback may put in the dashboard URL. The client sees one of these
#: codes and nothing else; why the sign-in failed — exception text, Google's answer, the
#: address — goes to the server log only. A code outside this set is sent as `sign_in_failed`.
_PUBLIC_LOGIN_ERRORS = frozenset({
    "invalid_request",
    "access_denied",
    "google_error",
    "invalid_state",
    "token_exchange_failed",
    "signature_unverified",
    "invalid_token",
    "email_unverified",
    "wrong_domain",
    "unknown_operator",
    "operator_disabled",
    "operator_not_permitted",
    "sign_in_failed",
})

#: The only `detail` texts `/auth/session` returns on 401. `IdentityRefused` messages can name
#: the operator's role or an internal table; they are logged, never returned.
SESSION_ABSENT_DETAIL = "not signed in: sign in with Google Workspace"
SESSION_REFUSED_DETAIL = "the session was refused: sign in again"
#: The `detail` of a logout whose revocation could not be written (503); the session stays live.
LOGOUT_NOT_RECORDED = "logout_not_recorded"


def _google(request: Request) -> GoogleAuthConfig | None:
    return getattr(request.app.state, "v2_google_auth", None)


def _require_google(request: Request) -> GoogleAuthConfig:
    config = _google(request)
    if config is None:  # pragma: no cover - the router is not mounted without it
        raise HTTPException(status_code=404, detail="Google sign-in is not configured")
    return config


def _set_cookie(response: Response, config: GoogleAuthConfig, name: str, value: str,
                max_age: int) -> None:
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        path="/",
        secure=config.secure_cookies,
        httponly=True,
        samesite="lax",
    )


def _clear_cookie(response: Response, config: GoogleAuthConfig, name: str) -> None:
    response.delete_cookie(
        name, path="/", secure=config.secure_cookies, httponly=True, samesite="lax"
    )


# --------------------------------------------------------------------------- Google


@google_auth_router.get("/login")
def google_login(request: Request) -> Response:
    config = _require_google(request)
    start = start_sign_in(config)
    response = RedirectResponse(start.authorization_url, status_code=302)
    _set_cookie(
        response,
        config,
        config.cookie_names.transaction,
        config.signer.dump_transaction(
            state=start.state, nonce=start.nonce, verifier=start.verifier, now=time.time()
        ),
        TRANSACTION_TTL_SECONDS,
    )
    return response


def _refuse(config: GoogleAuthConfig, code: str, reason: str) -> Response:
    """Redirect to the dashboard with a public error code. `reason` is for the log only."""
    logger.warning("dashboard sign-in refused: %s (%s)", code, reason)
    public_code = code if code in _PUBLIC_LOGIN_ERRORS else "sign_in_failed"
    response = RedirectResponse(
        f"{config.dashboard_url}?login_error={public_code}", status_code=303
    )
    _clear_cookie(response, config, config.cookie_names.transaction)
    return response


def _single(request: Request, name: str) -> str | None:
    """The one value of a query parameter; a repeated parameter is malformed."""
    values = request.query_params.getlist(name)
    if len(values) > 1:
        raise ValueError(name)
    return values[0] if values else None


@google_auth_router.get("/callback")
def google_callback(request: Request) -> Response:
    config = _require_google(request)
    now = time.time()

    try:
        error = _single(request, "error")
        code = _single(request, "code")
        state = _single(request, "state")
    except ValueError as exc:
        return _refuse(config, "invalid_request", f"repeated query parameter {exc}")
    if error is not None:
        # The person declined, or Google refused the request. Either way, no session.
        return _refuse(
            config,
            "access_denied" if error == "access_denied" else "google_error",
            "Google returned an error to the callback",
        )
    if not code or not state or len(code) > _MAX_PARAM_LENGTH or len(state) > _MAX_PARAM_LENGTH:
        return _refuse(config, "invalid_request", "callback is missing code or state")

    try:
        transaction = config.signer.load_transaction(
            read_cookie(dict(request.headers), config.cookie_names.transaction), now=now
        )
    except CookieRefused as exc:
        return _refuse(config, "invalid_state", f"sign-in cookie {exc}")
    if not secrets.compare_digest(state, transaction["state"]):
        return _refuse(config, "invalid_state", "state does not match the sign-in cookie")

    exchanger: Callable[..., dict[str, Any]] = getattr(
        request.app.state, "v2_token_exchanger", DEFAULT_TOKEN_EXCHANGER
    )
    try:
        tokens = exchanger(code=code, verifier=transaction["verifier"], config=config)
    except TokenExchangeFailed as exc:
        return _refuse(config, "token_exchange_failed", str(exc))

    # The signature is checked before any claim is read: an ID token that Google's current
    # keys did not sign is refused whatever it says (`google_jwks.py`). There is no fallback
    # when the key set is unavailable — the sign-in fails closed.
    jwks: GoogleJwks | None = getattr(request.app.state, "v2_google_jwks", None)
    if jwks is None:
        return _refuse(config, "signature_unverified", "no Google JWKS is configured")
    try:
        verify_signature(tokens.get("id_token"), jwks)
    except SignatureRefused as exc:
        return _refuse(config, "invalid_token", str(exc))
    except JwksUnavailable as exc:
        return _refuse(config, "signature_unverified", f"Google JWKS unavailable: {exc}")

    try:
        account = validate_claims(
            decode_id_token(tokens.get("id_token")),
            client_id=config.client_id,
            nonce=transaction["nonce"],
            workspace_domain=config.workspace_domain,
            now=now,
        )
    except ClaimsRefused as exc:
        return _refuse(config, exc.code, exc.reason)

    profile_login = getattr(request.app.state, "v2_profile_login", None)
    if profile_login is not None:
        candidates = profile_login.profiles.principals_for_google(
            account.email_norm, account.issuer, account.subject)
        if candidates:
            return _principal_signed_in(config, profile_login, candidates, account, now)

    lookup: OperatorLookup = request.app.state.v2_repository
    operator = lookup.by_email(account.email_norm)
    if operator is None:
        return _refuse(config, "unknown_operator", f"no platform.operator row for {account.email_norm}")
    if not operator.is_active:
        return _refuse(config, "operator_disabled", f"operator {operator.operator_id} is {operator.status}")
    if operator.role not in _DASHBOARD_ROLES:
        return _refuse(config, "operator_not_permitted", f"operator role {operator.role!r}")

    sessions = getattr(request.app.state, "v2_auth_sessions", None)
    if sessions is None or operator.version is None:
        return _refuse(config, "sign_in_failed", "no session store, or no operator version read")
    sid = new_session_id()
    try:
        sessions.open_operator_session(operator_id=operator.operator_id,
                                       token_hash=config.signer.session_token_hash(sid),
                                       expires_at=int(now) + config.session_ttl_seconds)
    except Exception as exc:  # no row, no session: a cookie logout could not revoke is never issued
        return _refuse(config, "sign_in_failed", f"session not recorded: {exc.__class__.__name__}")
    response = RedirectResponse(config.dashboard_url, status_code=303)
    _clear_cookie(response, config, config.cookie_names.transaction)
    _set_cookie(
        response,
        config,
        config.cookie_names.session,
        config.signer.dump_session(
            email_norm=operator.email_norm,
            operator_id=operator.operator_id,
            google_sub=account.subject,
            operator_version=operator.version,
            sid=sid,
            ttl=config.session_ttl_seconds,
            now=now,
        ),
        config.session_ttl_seconds,
    )
    logger.info("dashboard sign-in: operator %s", operator.operator_id)
    return response


def _principal_signed_in(config: GoogleAuthConfig, profile_login: Any, candidates: list[Any],
                         account: Any, now: float) -> Response:
    """A shared Workspace account: a principal session, and the profile screen next.

    The token is admitted only as the one principal that is both its address and its Google
    account (issuer + subject). The address alone never suffices where an account is pinned —
    a deleted and recreated account keeps the address and gets a new subject — and in
    production an unpinned principal is refused: the address is all it could be matched on.
    """
    principal = candidates[0]
    if len(candidates) != 1 or principal.email_norm != account.email_norm:
        return _refuse(config, "operator_not_permitted",
                       "the token's address and Google account belong to different principals")
    if principal.has_pinned_account:
        if not principal.is_account(account.issuer, account.subject):
            return _refuse(config, "operator_not_permitted",
                           f"principal {principal.principal_id}: the Google account is not the pinned "
                           "one (same address, different subject: a recreated account?)")
    elif profile_login.production:
        # The observed subject is logged so the owner can pin it with profile_roster.py — only
        # after confirming this sign-in was their own (apps/api/docs/PRODUCTION_AUTH.md).
        return _refuse(config, "operator_not_permitted",
                       f"principal {principal.principal_id} has no pinned Google account; production "
                       f"refuses it until one is pinned (observed subject {account.subject})")
    if not principal.is_active:
        return _refuse(config, "operator_disabled", f"principal {principal.principal_id} is {principal.status}")
    from origenlab_api.v2.profile_routes import set_session_cookie

    sid = new_session_id()
    session = PrincipalSession(
        principal_id=principal.principal_id,
        email=principal.email_norm,
        subject=account.subject,
        issuer=account.issuer,
        principal_version=principal.version,
        auth_time=int(now),
        exp=int(now) + config.session_ttl_seconds,
        method="google",
        sid=sid,
    )
    try:
        profile_login.profiles.open_session(principal_id=principal.principal_id,
                                            token_hash=profile_login.signer.session_token_hash(sid),
                                            expires_at=session.exp)
    except Exception as exc:  # no row, no session: a cookie logout could not revoke is never issued
        return _refuse(config, "sign_in_failed", f"session not recorded: {exc.__class__.__name__}")
    response = RedirectResponse(config.dashboard_url, status_code=303)
    _clear_cookie(response, config, config.cookie_names.transaction)
    set_session_cookie(response, profile_login, session, now=now)
    logger.info("dashboard sign-in: shared principal %s (profile required)", principal.principal_id)
    return response


# -------------------------------------------------------------------------- session


def _login_options(config: GoogleAuthConfig | None, request: Request | None = None) -> dict[str, Any]:
    dev = request is not None and bool(getattr(request.app.state, "v2_dev_profile_login", False))
    return {
        "google_login_enabled": config is not None,
        "workspace_domain": config.workspace_domain if config is not None else None,
        "dev_profile_login_enabled": dev,
    }


@auth_router.get("/session")
def current_session(request: Request) -> JSONResponse:
    """Who this request resolves to. 200 when signed in, 401 with sign-in options otherwise.

    Resolution goes through `app.state.v2_identity` — the same port, and so the same rules,
    as every `/v2` read. There is no second notion of "signed in" to drift from the first.
    """
    config = _google(request)
    port: IdentityPort = request.app.state.v2_identity
    options = _login_options(config, request)
    try:
        operator = port.resolve(dict(request.headers)).require_active().require_role(
            *_DASHBOARD_ROLES
        )
    except IdentityAbsent:
        return JSONResponse(
            status_code=401,
            content={"authenticated": False, "state": "signed_out",
                     "detail": SESSION_ABSENT_DETAIL, **options},
        )
    except ProfileRequired as exc:
        # A valid shared sign-in with no (still valid) profile: show the profile screen.
        return JSONResponse(
            status_code=401,
            content={"authenticated": False, "state": "profile_required",
                     "detail": PROFILE_REQUIRED,
                     "principal": {"email": exc.principal.email_norm},
                     "profile_expired": exc.stale, **options},
        )
    except IdentityRefused as exc:
        logger.info("dashboard session refused: %s", exc)
        return JSONResponse(
            status_code=401,
            content={"authenticated": False, "state": "signed_out",
                     "detail": SESSION_REFUSED_DETAIL, **options},
        )
    profile_session = operator.auth_method in ("google_profile", "dev_profile")
    return JSONResponse(content={
        "authenticated": True,
        "state": "signed_in",
        "auth_method": operator.auth_method,
        "operator": {
            "operator_id": operator.operator_id,
            "email": operator.email_norm,
            "display_name": operator.display_name,
            "role": operator.role,
        },
        "profile": ({"id": operator.operator_id, "display_name": operator.display_name,
                     "role_label": ROLE_LABELS.get(operator.role, "Perfil")}
                    if profile_session else None),
        "can_switch_profile": profile_session,
        **options,
    })


@auth_router.post("/logout")
def logout(request: Request) -> JSONResponse:
    """Revoke the session's row, then clear the cookie. Idempotent.

    Either kind of session — an operator's own Google account or a shared sign-in — is revoked
    in the database first: its own `platform.auth_session` row, found by the keyed hash of its
    identifier, together with one `session.logout` audit event, in one transaction. A copy of
    the cookie taken before this therefore fails on its next request, on any API instance.

    If that revocation cannot be written the answer is 503 `logout_not_recorded` **and the
    cookie is left as it is**: the session is still live in the database, so clearing it here
    would only hide a session that every copy can still use. The browser stays signed in and
    says so, and the person can simply try again — a retry that succeeds revokes the same row.

    No cookie, or one that is not a valid session (forged, expired, an old stateless shape),
    has nothing to revoke: the answer is 200 and the cookies are cleared.
    """
    config = _google(request)
    profile_login = getattr(request.app.state, "v2_profile_login", None)
    cookies = config if config is not None else profile_login
    revoked = _revoke_session(request, cookies, profile_login) if cookies is not None else True
    if not revoked:
        # Nothing is cleared: not the session cookie, not the sign-in cookie.
        return JSONResponse(status_code=503, content={"detail": LOGOUT_NOT_RECORDED},
                            headers={"Cache-Control": "no-store"})
    response = JSONResponse(content={"authenticated": False})
    if cookies is not None:
        _clear_cookie(response, cookies, cookies.cookie_names.session)
        _clear_cookie(response, cookies, cookies.cookie_names.transaction)
    return response


def _revoke_session(request: Request, cookies: Any, profile_login: Any) -> bool:
    """Revoke the signed session's row, if there is a session. False only when that failed."""
    value = read_cookie(dict(request.headers), cookies.cookie_names.session)
    if value is None:
        return True
    try:
        session = cookies.signer.load_any_session(value)
    except CookieRefused:
        return True
    token_hash = cookies.signer.session_token_hash(session.sid)
    try:
        if isinstance(session, PrincipalSession):
            if profile_login is None:  # profile sign-in is off: this cookie resolves nowhere
                return True
            profile_login.profiles.revoke_session(
                token_hash=token_hash, principal_id=session.principal_id,
                principal_email=session.email,
                operator_id=session.profile.operator_id if session.profile else None,
            )
        else:
            sessions = getattr(request.app.state, "v2_auth_sessions", None)
            if sessions is None:  # Google sign-in is off: this cookie resolves nowhere
                return True
            sessions.revoke_operator_session(token_hash=token_hash, operator_id=session.operator_id)
    except Exception as exc:
        logger.error("logout: session revocation not recorded: %s", exc.__class__.__name__)
        return False
    return True
