"""Dashboard sign-in routes — Google Workspace login, the current session, logout.

| Route | Mounted when | Does |
|---|---|---|
| `GET /auth/google/login` | Google sign-in is on | sets the sign-in cookie (state, nonce, PKCE verifier) and redirects to Google |
| `GET /auth/google/callback` | Google sign-in is on | checks state, exchanges the code, checks the claims, maps the address to `platform.operator`, sets the session cookie |
| `GET /auth/session` | the V2 boundary is mounted | who the current request resolves to, through the same identity port every `/v2` read uses |
| `POST /auth/logout` | the V2 boundary is mounted | clears the session cookie |

**None of this writes to the database.** The callback reads `platform.operator` through the
read-only repository and nothing else; it never creates an operator, because operators are
created by an admin command (`docs/ARCHITECTURE.md` §5), not by signing in. An address with no
row is refused, and so is a row that is not `active`.

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

from origenlab_api.v2.auth_session import TRANSACTION_TTL_SECONDS, CookieRefused, read_cookie
from origenlab_api.v2.google_oidc import (
    DEFAULT_TOKEN_EXCHANGER,
    ClaimsRefused,
    GoogleAuthConfig,
    TokenExchangeFailed,
    decode_id_token,
    start_sign_in,
    validate_claims,
)
from origenlab_api.v2.identity import IdentityPort, IdentityRefused, OperatorLookup

logger = logging.getLogger(__name__)

auth_router = APIRouter(prefix="/auth", tags=["auth"])
google_auth_router = APIRouter(prefix="/auth/google", tags=["auth"])

#: Roles that may use the dashboard at all (`docs/OPERATIONS.md` §2).
_DASHBOARD_ROLES = ("viewer", "sales", "admin")
#: Longest `code` or `state` accepted. Google's codes are a few hundred characters.
_MAX_PARAM_LENGTH = 2048


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
    logger.warning("dashboard sign-in refused: %s (%s)", code, reason)
    response = RedirectResponse(f"{config.dashboard_url}?login_error={code}", status_code=303)
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

    lookup: OperatorLookup = request.app.state.v2_repository
    operator = lookup.by_email(account.email_norm)
    if operator is None:
        return _refuse(config, "unknown_operator", f"no platform.operator row for {account.email_norm}")
    if not operator.is_active:
        return _refuse(config, "operator_disabled", f"operator {operator.operator_id} is {operator.status}")
    if operator.role not in _DASHBOARD_ROLES:
        return _refuse(config, "operator_not_permitted", f"operator role {operator.role!r}")

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
            ttl=config.session_ttl_seconds,
            now=now,
        ),
        config.session_ttl_seconds,
    )
    logger.info("dashboard sign-in: operator %s", operator.operator_id)
    return response


# -------------------------------------------------------------------------- session


def _login_options(config: GoogleAuthConfig | None) -> dict[str, Any]:
    return {
        "google_login_enabled": config is not None,
        "workspace_domain": config.workspace_domain if config is not None else None,
    }


@auth_router.get("/session")
def current_session(request: Request) -> JSONResponse:
    """Who this request resolves to. 200 when signed in, 401 with sign-in options otherwise.

    Resolution goes through `app.state.v2_identity` — the same port, and so the same rules,
    as every `/v2` read. There is no second notion of "signed in" to drift from the first.
    """
    config = _google(request)
    port: IdentityPort = request.app.state.v2_identity
    try:
        operator = port.resolve(dict(request.headers)).require_active().require_role(
            *_DASHBOARD_ROLES
        )
    except IdentityRefused as exc:
        return JSONResponse(
            status_code=401,
            content={"authenticated": False, "detail": str(exc), **_login_options(config)},
        )
    return JSONResponse(content={
        "authenticated": True,
        "auth_method": operator.auth_method,
        "operator": {
            "operator_id": operator.operator_id,
            "email": operator.email_norm,
            "display_name": operator.display_name,
            "role": operator.role,
        },
        **_login_options(config),
    })


@auth_router.post("/logout")
def logout(request: Request) -> JSONResponse:
    """Clear the session cookie. Idempotent; answers 200 whether or not one was set.

    Stateless sessions cannot be revoked server-side (`auth_session.py`); this ends the
    session in this browser. Disabling the operator ends it everywhere.
    """
    response = JSONResponse(content={"authenticated": False})
    config = _google(request)
    if config is not None:
        _clear_cookie(response, config, config.cookie_names.session)
        _clear_cookie(response, config, config.cookie_names.transaction)
    return response
