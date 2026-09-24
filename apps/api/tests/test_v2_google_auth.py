"""Google Workspace dashboard sign-in: the protocol checks, the cookies, and the boundary.

Nothing here talks to Google or to a database. The token endpoint is replaced through the
`app.state.v2_token_exchanger` seam and `platform.operator` through a stub lookup, so what is
under test is exactly the part this repository owns: which callbacks are refused, what the
cookies carry, and which identities the V2 boundary will and will not accept.
"""

from __future__ import annotations

import base64
import json
import secrets
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
from origenlab_api.main import create_app
from origenlab_api.v2 import google_oidc
from origenlab_api.v2.auth_session import CookieRefused, CookieSigner
from origenlab_api.v2.google_oidc import (
    ClaimsRefused,
    GoogleAuthMisconfigured,
    build_google_auth_config,
    decode_id_token,
    pkce_challenge,
    validate_claims,
)
from origenlab_api.v2.identity import (
    ChainedIdentity,
    GoogleSessionIdentity,
    IdentityMisconfigured,
    LocalDevIdentity,
    OperatorIdentity,
    build_identity_port,
)
from origenlab_api.v2.repository import Page

LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"
CLIENT_ID = "1234567890-test.apps.googleusercontent.com"
LOCAL_BASE = "http://localhost:5173"
PROD_BASE = "https://dashboard.origenlab.cl/api"
OPERATOR_ID = "00000000-0000-4000-8000-0000000000aa"
EMAIL = "contacto@origenlab.cl"


def _operator(status: str = "active", role: str = "admin", operator_id: str = OPERATOR_ID,
              email: str = EMAIL) -> OperatorIdentity:
    return OperatorIdentity(operator_id=operator_id, email_norm=email,
                            display_name="Contacto", role=role, status=status)


class _Repo:
    """Stands in for V2Repository: an operator lookup plus one read, and no write at all."""

    def __init__(self, operators: dict[str, OperatorIdentity] | None = None) -> None:
        self.operators = dict(operators if operators is not None else {EMAIL: _operator()})
        self.calls: list[str] = []

    def by_email(self, email_norm: str) -> OperatorIdentity | None:
        self.calls.append(f"by_email:{email_norm}")
        return self.operators.get(email_norm)

    def contacts(self, **kwargs: Any) -> Page:
        self.calls.append("contacts")
        return Page(items=[], total=0, limit=kwargs["limit"], offset=kwargs["offset"])


def _jwt(claims: dict[str, Any]) -> str:
    def seg(obj: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    return f"{seg({'alg': 'RS256', 'typ': 'JWT'})}.{seg(claims)}.c2lnbmF0dXJl"


def _claims(nonce: str, /, **overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "azp": CLIENT_ID,
        "sub": "109876543210",
        "email": EMAIL,
        "email_verified": True,
        "hd": "origenlab.cl",
        "iat": now,
        "exp": now + 3600,
        "nonce": nonce,
    }
    for key, value in overrides.items():
        if value is _DROP:
            claims.pop(key, None)
        else:
            claims[key] = value
    return claims


_DROP = object()


def _env(monkeypatch: pytest.MonkeyPatch, *, base: str = LOCAL_BASE, google: bool = True,
         dev: bool = False, production: bool = False) -> None:
    monkeypatch.setenv("ORIGENLAB_DISABLE_DOTENV", "1")
    monkeypatch.setenv("ORIGENLAB_V2_DATABASE_URL", LOOPBACK)
    monkeypatch.delenv("ORIGENLAB_V2_JWKS_URL", raising=False)
    monkeypatch.setenv("ORIGENLAB_GOOGLE_AUTH_ENABLED", "true" if google else "false")
    monkeypatch.setenv("ORIGENLAB_GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("ORIGENLAB_GOOGLE_CLIENT_SECRET", secrets.token_urlsafe(24))
    monkeypatch.setenv("ORIGENLAB_AUTH_PUBLIC_BASE_URL", base)
    monkeypatch.setenv("ORIGENLAB_AUTH_SESSION_SECRET", secrets.token_urlsafe(48))
    monkeypatch.delenv("ORIGENLAB_GOOGLE_WORKSPACE_DOMAIN", raising=False)
    monkeypatch.setenv("ORIGENLAB_DEV_LOGIN_ENABLED", "true" if dev else "false")
    if production:
        monkeypatch.setenv("ORIGENLAB_ENV", "production")
        monkeypatch.setenv("ORIGENLAB_API_BACKEND", "postgres")
        monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@127.0.0.1:5432/unused")
        monkeypatch.setenv("ORIGENLAB_API_CORS_ORIGINS", "https://dashboard.origenlab.cl")
        monkeypatch.setenv("ORIGENLAB_API_AUTH_TOKEN", "proxy-token-for-tests")
    else:
        monkeypatch.delenv("ORIGENLAB_ENV", raising=False)


class _Harness:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, repo: _Repo | None = None,
                 **env: Any) -> None:
        _env(monkeypatch, **env)
        self.app = create_app()
        self.repo = repo or _Repo()
        self.app.state.v2_repository = self.repo
        # The identity port captured the real repository at startup; rebuild it on the stub.
        config = self.app.state.v2_google_auth
        self.app.state.v2_identity = build_identity_port(
            jwks_url=None, database_url=LOOPBACK, lookup=self.repo, google=config,
            dev_login_enabled=env.get("dev", False), production=env.get("production", False),
        )
        self.config = config
        self.exchanges: list[dict[str, Any]] = []
        self.claims_override: dict[str, Any] = {}
        self.app.state.v2_token_exchanger = self._exchange
        secure = urlsplit(env.get("base", LOCAL_BASE)).scheme == "https"
        self.client = TestClient(self.app, base_url="https://testserver" if secure else "http://testserver")
        self.headers = {"X-OriginLab-API-Key": "proxy-token-for-tests"} if env.get("production") else {}

    def _exchange(self, *, code: str, verifier: str, config: Any) -> dict[str, Any]:
        self.exchanges.append({"code": code, "verifier": verifier})
        return {"id_token": _jwt(_claims(self.nonce, **self.claims_override)),
                "access_token": "discarded"}

    def login(self) -> Any:
        response = self.client.get("/auth/google/login", headers=self.headers, follow_redirects=False)
        query = parse_qs(urlsplit(response.headers["location"]).query)
        self.state = query["state"][0]
        self.nonce = query["nonce"][0]
        self.challenge = query["code_challenge"][0]
        return response

    def callback(self, **params: str) -> Any:
        return self.client.get("/auth/google/callback", params=params, headers=self.headers,
                               follow_redirects=False)

    def sign_in(self, **claims_override: Any) -> Any:
        self.claims_override = claims_override
        self.login()
        return self.callback(code="authcode", state=self.state)


def _login_error(response: Any) -> str | None:
    assert response.status_code == 303
    return parse_qs(urlsplit(response.headers["location"]).query).get("login_error", [None])[0]


def _set_cookies(response: Any) -> list[str]:
    return response.headers.get_list("set-cookie")


def _session_cookie_set(response: Any, name: str = "origenlab_session") -> bool:
    return any(c.startswith(f"{name}=") and "Max-Age=0" not in c and 'expires=Thu, 01 Jan 1970' not in c
               for c in _set_cookies(response))


# ---------------------------------------------------------------------------- login


def test_login_redirects_to_google_with_only_identity_scopes_and_pkce(monkeypatch) -> None:
    h = _Harness(monkeypatch)
    response = h.login()
    assert response.status_code == 302
    parts = urlsplit(response.headers["location"])
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == google_oidc.GOOGLE_AUTHORIZATION_ENDPOINT
    query = parse_qs(parts.query)
    assert query["scope"] == ["openid email profile"]
    assert query["response_type"] == ["code"]
    assert query["client_id"] == [CLIENT_ID]
    assert query["redirect_uri"] == [f"{LOCAL_BASE}/auth/google/callback"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["hd"] == ["origenlab.cl"]
    # No offline access, so no refresh token; and nothing that reaches Gmail or Drive.
    assert "access_type" not in query
    location = response.headers["location"].lower()
    for forbidden in ("gmail", "drive", "calendar", "offline"):
        assert forbidden not in location
    cookie = next(c for c in _set_cookies(response) if c.startswith("origenlab_signin="))
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie


def test_every_login_gets_fresh_state_nonce_and_verifier(monkeypatch) -> None:
    h = _Harness(monkeypatch)
    h.login()
    first = (h.state, h.nonce, h.challenge)
    h.login()
    assert all(a != b for a, b in zip(first, (h.state, h.nonce, h.challenge)))


# ------------------------------------------------------------------------ valid path


def test_a_valid_origenlab_operator_gets_a_session(monkeypatch) -> None:
    h = _Harness(monkeypatch)
    response = h.sign_in()
    assert response.status_code == 303
    assert response.headers["location"] == "http://localhost:5173/"
    session = next(c for c in _set_cookies(response) if c.startswith("origenlab_session="))
    assert "HttpOnly" in session and "SameSite=lax" in session and "Path=/" in session
    assert f"Max-Age={8 * 3600}" in session
    # The PKCE verifier sent to the token endpoint is the one whose challenge went to Google.
    assert pkce_challenge(h.exchanges[0]["verifier"]) == h.challenge

    me = h.client.get("/auth/session")
    assert me.status_code == 200
    body = me.json()
    assert body["authenticated"] is True
    assert body["auth_method"] == "google_session"
    assert body["operator"] == {"operator_id": OPERATOR_ID, "email": EMAIL,
                                "display_name": "Contacto", "role": "admin"}
    assert h.client.get("/v2/contacts").status_code == 200


def test_signing_in_reads_the_operator_and_writes_nothing(monkeypatch) -> None:
    h = _Harness(monkeypatch)
    h.sign_in()
    # The stub has no write method at all; the only thing sign-in may do is look up.
    assert h.repo.calls == [f"by_email:{EMAIL}"]


def test_the_address_is_normalized_before_lookup(monkeypatch) -> None:
    h = _Harness(monkeypatch)
    response = h.sign_in(email="  Contacto@OrigenLab.CL ")
    assert _session_cookie_set(response)


def test_a_string_true_email_verified_is_accepted(monkeypatch) -> None:
    h = _Harness(monkeypatch)
    assert _session_cookie_set(h.sign_in(email_verified="true"))


# ------------------------------------------------------------------ malformed callback


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"code": "authcode"},
        {"state": "whatever"},
        {"code": "", "state": ""},
        {"code": "x" * 5000, "state": "s"},
    ],
)
def test_a_malformed_callback_is_refused(monkeypatch, params) -> None:
    h = _Harness(monkeypatch)
    h.login()
    response = h.callback(**params)
    assert _login_error(response) == "invalid_request"
    assert not _session_cookie_set(response)
    assert h.exchanges == []


def test_a_repeated_parameter_is_refused(monkeypatch) -> None:
    h = _Harness(monkeypatch)
    h.login()
    response = h.client.get(
        f"/auth/google/callback?code=a&state={h.state}&state={h.state}", follow_redirects=False
    )
    assert _login_error(response) == "invalid_request"
    assert h.exchanges == []


@pytest.mark.parametrize("error, code", [("access_denied", "access_denied"),
                                         ("server_error", "google_error")])
def test_a_google_error_is_refused_without_an_exchange(monkeypatch, error, code) -> None:
    h = _Harness(monkeypatch)
    h.login()
    response = h.callback(error=error, state=h.state)
    assert _login_error(response) == code
    assert h.exchanges == []


def test_nothing_from_the_query_is_reflected_into_the_redirect(monkeypatch) -> None:
    h = _Harness(monkeypatch)
    h.login()
    response = h.callback(error="<script>alert(1)</script>", state=h.state)
    assert response.headers["location"] == "http://localhost:5173/?login_error=google_error"


def test_a_failed_token_exchange_is_refused(monkeypatch) -> None:
    h = _Harness(monkeypatch)

    def fail(**_: Any) -> dict[str, Any]:
        raise google_oidc.TokenExchangeFailed("token endpoint answered 400")

    h.app.state.v2_token_exchanger = fail
    h.login()
    assert _login_error(h.callback(code="c", state=h.state)) == "token_exchange_failed"


def test_a_token_response_without_an_id_token_is_refused(monkeypatch) -> None:
    h = _Harness(monkeypatch)
    h.app.state.v2_token_exchanger = lambda **_: {"access_token": "x"}
    h.login()
    assert _login_error(h.callback(code="c", state=h.state)) == "invalid_token"


# ---------------------------------------------------------------------- invalid state


def test_a_state_that_does_not_match_the_cookie_is_refused(monkeypatch) -> None:
    h = _Harness(monkeypatch)
    h.login()
    response = h.callback(code="authcode", state=secrets.token_urlsafe(32))
    assert _login_error(response) == "invalid_state"
    assert h.exchanges == []


def test_a_callback_without_the_signin_cookie_is_refused(monkeypatch) -> None:
    # Login CSRF: an attacker's own valid code and state, replayed into a victim's browser
    # that never started a sign-in, has no transaction cookie to match.
    h = _Harness(monkeypatch)
    h.login()
    h.client.cookies.clear()
    assert _login_error(h.callback(code="authcode", state=h.state)) == "invalid_state"


def test_a_forged_signin_cookie_is_refused(monkeypatch) -> None:
    h = _Harness(monkeypatch)
    h.login()
    forged = CookieSigner(secrets.token_urlsafe(48)).dump_transaction(
        state=h.state, nonce=h.nonce, verifier="v" * 64, now=time.time()
    )
    h.client.cookies.set("origenlab_signin", forged)
    assert _login_error(h.callback(code="authcode", state=h.state)) == "invalid_state"


def test_an_expired_signin_cookie_is_refused(monkeypatch) -> None:
    h = _Harness(monkeypatch)
    h.login()
    stale = h.config.signer.dump_transaction(
        state=h.state, nonce=h.nonce, verifier="v" * 64, now=time.time() - 3600
    )
    h.client.cookies.set("origenlab_signin", stale)
    assert _login_error(h.callback(code="authcode", state=h.state)) == "invalid_state"


def test_the_signin_cookie_is_cleared_after_any_callback(monkeypatch) -> None:
    h = _Harness(monkeypatch)
    h.login()
    response = h.callback(code="authcode", state="wrong")
    assert any(c.startswith("origenlab_signin=") and 'Max-Age=0' in c for c in _set_cookies(response))


def test_a_session_cookie_cannot_stand_in_for_a_signin_cookie(monkeypatch) -> None:
    h = _Harness(monkeypatch)
    h.login()
    session = h.config.signer.dump_session(email_norm=EMAIL, operator_id=OPERATOR_ID,
                                           google_sub="s", ttl=600, now=time.time())
    h.client.cookies.set("origenlab_signin", session)
    assert _login_error(h.callback(code="authcode", state=h.state)) == "invalid_state"


# --------------------------------------------------------------------- claim checks


@pytest.mark.parametrize(
    "override, reason",
    [
        ({"iss": "https://evil.example"}, "issuer"),
        ({"iss": _DROP}, "issuer"),
        ({"aud": "someone-else.apps.googleusercontent.com"}, "audience"),
        ({"aud": ["someone-else", CLIENT_ID], "azp": "someone-else"}, "authorized party"),
        ({"azp": "someone-else"}, "authorized party"),
        ({"exp": int(time.time()) - 3600}, "expired"),
        ({"exp": _DROP}, "expired"),
        ({"iat": int(time.time()) + 3600}, "future"),
        ({"nonce": "not-the-nonce"}, "nonce"),
        ({"nonce": _DROP}, "nonce"),
        ({"sub": ""}, "subject"),
    ],
)
def test_claims_that_are_not_for_this_signin_are_refused(override, reason) -> None:
    with pytest.raises(ClaimsRefused) as excinfo:
        validate_claims(_claims("n", **override), client_id=CLIENT_ID, nonce="n",
                        workspace_domain="origenlab.cl", now=time.time())
    assert excinfo.value.code == "invalid_token"
    assert reason in excinfo.value.reason


@pytest.mark.parametrize("override", [{"iss": "https://evil.example"},
                                      {"aud": "someone-else.apps.googleusercontent.com"}])
def test_invalid_issuer_or_audience_is_refused_at_the_callback(monkeypatch, override) -> None:
    h = _Harness(monkeypatch)
    response = h.sign_in(**override)
    assert _login_error(response) == "invalid_token"
    assert not _session_cookie_set(response)
    assert h.repo.calls == []


def test_a_nonce_from_another_signin_is_refused_at_the_callback(monkeypatch) -> None:
    h = _Harness(monkeypatch)
    assert _login_error(h.sign_in(nonce="replayed-from-elsewhere")) == "invalid_token"


@pytest.mark.parametrize("value", [False, "false", None, _DROP, 1])
def test_an_unverified_email_is_refused(monkeypatch, value) -> None:
    h = _Harness(monkeypatch)
    response = h.sign_in(email_verified=value)
    assert _login_error(response) == "email_unverified"
    assert h.repo.calls == []


@pytest.mark.parametrize(
    "override",
    [
        {"email": "someone@gmail.com", "hd": _DROP},
        {"email": "contacto@origenlab.cl.evil.com", "hd": "origenlab.cl.evil.com"},
        {"email": "contacto@evilorigenlab.cl"},
        {"email": "contacto@sub.origenlab.cl", "hd": "sub.origenlab.cl"},
        {"email": "contacto@origenlab.cl@evil.com"},
        # A consumer Google account registered with an @origenlab.cl address: verified, the
        # right suffix, and no `hd` — because it is not a member of the Workspace.
        {"hd": _DROP},
        {"hd": "other-workspace.cl"},
        {"email": _DROP},
        {"email": 42},
    ],
)
def test_a_wrong_workspace_domain_is_refused(monkeypatch, override) -> None:
    h = _Harness(monkeypatch)
    response = h.sign_in(**override)
    assert _login_error(response) == "wrong_domain"
    assert h.repo.calls == []


def test_the_id_token_must_be_a_jwt() -> None:
    for token in (None, "", "a.b", "a.b.c.d", "a.!!!.c", f"a.{base64.urlsafe_b64encode(b'[1]').decode()}.c"):
        with pytest.raises(ClaimsRefused):
            decode_id_token(token)


# ------------------------------------------------------------------- operator mapping


def test_an_unknown_operator_is_refused_and_not_created(monkeypatch) -> None:
    h = _Harness(monkeypatch, repo=_Repo(operators={}))
    response = h.sign_in()
    assert _login_error(response) == "unknown_operator"
    assert not _session_cookie_set(response)
    assert h.repo.operators == {}


def test_a_disabled_operator_is_refused(monkeypatch) -> None:
    h = _Harness(monkeypatch, repo=_Repo({EMAIL: _operator(status="disabled")}))
    response = h.sign_in()
    assert _login_error(response) == "operator_disabled"
    assert not _session_cookie_set(response)


def test_an_operator_disabled_after_signin_loses_the_session_at_once(monkeypatch) -> None:
    h = _Harness(monkeypatch)
    h.sign_in()
    assert h.client.get("/auth/session").status_code == 200
    h.repo.operators[EMAIL] = _operator(status="disabled")
    assert h.client.get("/auth/session").status_code == 401
    assert h.client.get("/v2/contacts").status_code == 401


def test_a_session_for_a_reassigned_address_is_refused(monkeypatch) -> None:
    h = _Harness(monkeypatch)
    h.sign_in()
    h.repo.operators[EMAIL] = _operator(operator_id="00000000-0000-4000-8000-0000000000bb")
    assert h.client.get("/auth/session").status_code == 401


def test_an_unknown_role_is_refused(monkeypatch) -> None:
    h = _Harness(monkeypatch, repo=_Repo({EMAIL: _operator(role="auditor")}))
    assert _login_error(h.sign_in()) == "operator_not_permitted"


# --------------------------------------------------------------------------- session


def test_no_session_is_401_with_the_signin_options(monkeypatch) -> None:
    h = _Harness(monkeypatch)
    response = h.client.get("/auth/session")
    assert response.status_code == 401
    body = response.json()
    assert body["authenticated"] is False
    assert body["google_login_enabled"] is True
    assert body["workspace_domain"] == "origenlab.cl"


def test_a_tampered_session_cookie_is_refused(monkeypatch) -> None:
    h = _Harness(monkeypatch)
    h.sign_in()
    value = h.client.cookies.get("origenlab_session")
    body, _, tag = value.partition(".")
    claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    claims["email"] = "otra@origenlab.cl"
    forged = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    h.client.cookies.set("origenlab_session", f"{forged}.{tag}")
    assert h.client.get("/auth/session").status_code == 401


def test_an_expired_session_is_refused(monkeypatch) -> None:
    h = _Harness(monkeypatch)
    stale = h.config.signer.dump_session(email_norm=EMAIL, operator_id=OPERATOR_ID,
                                         google_sub="s", ttl=600, now=time.time() - 3600)
    h.client.cookies.set("origenlab_session", stale)
    assert h.client.get("/auth/session").status_code == 401


def test_a_signin_cookie_cannot_stand_in_for_a_session(monkeypatch) -> None:
    h = _Harness(monkeypatch)
    tx = h.config.signer.dump_transaction(state="s", nonce="n", verifier="v", now=time.time())
    h.client.cookies.set("origenlab_session", tx)
    assert h.client.get("/auth/session").status_code == 401


# ---------------------------------------------------------------------------- logout


def test_logout_clears_the_session(monkeypatch) -> None:
    h = _Harness(monkeypatch)
    h.sign_in()
    response = h.client.post("/auth/logout")
    assert response.status_code == 200
    assert response.json() == {"authenticated": False}
    cleared = next(c for c in _set_cookies(response) if c.startswith("origenlab_session="))
    assert "Max-Age=0" in cleared
    assert h.client.get("/auth/session").status_code == 401
    assert h.client.get("/v2/contacts").status_code == 401


def test_logout_without_a_session_is_harmless(monkeypatch) -> None:
    h = _Harness(monkeypatch)
    assert h.client.post("/auth/logout").status_code == 200


def test_logout_is_post_only(monkeypatch) -> None:
    h = _Harness(monkeypatch)
    assert h.client.get("/auth/logout").status_code == 405


# ------------------------------------------------------------------------ production


def test_production_uses_host_prefixed_secure_cookies(monkeypatch) -> None:
    h = _Harness(monkeypatch, base=PROD_BASE, production=True)
    login = h.login()
    query = parse_qs(urlsplit(login.headers["location"]).query)
    assert query["redirect_uri"] == ["https://dashboard.origenlab.cl/api/auth/google/callback"]
    tx = next(c for c in _set_cookies(login) if c.startswith("__Host-origenlab_signin="))
    assert "Secure" in tx and "HttpOnly" in tx and "Domain" not in tx
    response = h.callback(code="authcode", state=h.state)
    assert response.headers["location"] == "https://dashboard.origenlab.cl/"
    session = next(c for c in _set_cookies(response) if c.startswith("__Host-origenlab_session="))
    assert "Secure" in session and "HttpOnly" in session and "SameSite=lax" in session
    assert "Domain" not in session


def test_production_never_trusts_the_operator_email_header(monkeypatch) -> None:
    h = _Harness(monkeypatch, base=PROD_BASE, production=True)
    spoofed = {**h.headers, OPERATOR_EMAIL_HEADER: EMAIL}
    assert h.client.get("/v2/contacts", headers=spoofed).status_code == 401
    response = h.client.get("/auth/session", headers=spoofed)
    assert response.status_code == 401
    assert "contacts" not in h.repo.calls
    assert h.repo.calls == []  # the header was not even looked up


def test_production_refuses_the_dev_login_at_startup(monkeypatch) -> None:
    _env(monkeypatch, base=PROD_BASE, production=True, dev=True)
    with pytest.raises(ValueError, match="ORIGENLAB_DEV_LOGIN_ENABLED"):
        create_app()


def test_production_refuses_the_dev_login_even_without_v2(monkeypatch) -> None:
    _env(monkeypatch, base=PROD_BASE, production=True, dev=True, google=False)
    monkeypatch.delenv("ORIGENLAB_V2_DATABASE_URL")
    with pytest.raises(ValueError, match="ORIGENLAB_DEV_LOGIN_ENABLED"):
        create_app()


def test_production_refuses_a_plain_http_public_url(monkeypatch) -> None:
    _env(monkeypatch, base=LOCAL_BASE, production=True)
    with pytest.raises(GoogleAuthMisconfigured, match="https"):
        create_app()


def test_the_dev_adapter_itself_refuses_production() -> None:
    with pytest.raises(IdentityMisconfigured):
        LocalDevIdentity(LOOPBACK, _Repo(), production=True)
    with pytest.raises(IdentityMisconfigured):
        build_identity_port(jwks_url=None, database_url=LOOPBACK, lookup=_Repo(),
                            dev_login_enabled=True, production=True)


# ------------------------------------------------------------------ development login


def test_v2_with_no_login_switched_on_refuses_to_start(monkeypatch) -> None:
    _env(monkeypatch, google=False, dev=False)
    with pytest.raises(IdentityMisconfigured, match="ORIGENLAB_DEV_LOGIN_ENABLED"):
        create_app()


def test_the_header_is_ignored_unless_dev_login_is_enabled(monkeypatch) -> None:
    h = _Harness(monkeypatch, google=True, dev=False)
    response = h.client.get("/auth/session", headers={OPERATOR_EMAIL_HEADER: EMAIL})
    assert response.status_code == 401
    assert h.repo.calls == []


def test_the_dev_login_works_only_when_explicitly_enabled(monkeypatch) -> None:
    h = _Harness(monkeypatch, google=False, dev=True)
    response = h.client.get("/auth/session", headers={OPERATOR_EMAIL_HEADER: EMAIL})
    assert response.status_code == 200
    assert response.json()["auth_method"] == "dev_header"
    assert response.json()["google_login_enabled"] is False
    # Google routes are absent when Google sign-in is off.
    assert h.client.get("/auth/google/login", follow_redirects=False).status_code == 404


def test_with_both_enabled_a_bad_session_is_not_rescued_by_the_header(monkeypatch) -> None:
    h = _Harness(monkeypatch, google=True, dev=True)
    assert isinstance(h.app.state.v2_identity, ChainedIdentity)
    h.client.cookies.set("origenlab_session", "garbage.value")
    response = h.client.get("/auth/session", headers={OPERATOR_EMAIL_HEADER: EMAIL})
    assert response.status_code == 401


def test_with_both_enabled_a_google_session_wins(monkeypatch) -> None:
    h = _Harness(monkeypatch, google=True, dev=True)
    h.sign_in()
    response = h.client.get("/auth/session", headers={OPERATOR_EMAIL_HEADER: "otro@origenlab.cl"})
    assert response.json()["auth_method"] == "google_session"


# --------------------------------------------------------------------- configuration


def _config(**overrides: Any):
    kwargs: dict[str, Any] = dict(
        enabled=True, client_id=CLIENT_ID, client_secret="s" * 24, workspace_domain="origenlab.cl",
        public_base_url=LOCAL_BASE, session_secret="k" * 48, session_ttl_seconds=3600,
        production=False,
    )
    kwargs.update(overrides)
    return build_google_auth_config(**kwargs)


def test_google_sign_in_off_builds_nothing() -> None:
    assert _config(enabled=False, client_id=None, client_secret=None) is None


@pytest.mark.parametrize(
    "override",
    [
        {"client_id": ""},
        {"client_secret": None},
        {"session_secret": "too-short"},
        {"session_secret": None},
        {"public_base_url": None},
        {"public_base_url": "ftp://dashboard.origenlab.cl"},
        {"public_base_url": "http://dashboard.origenlab.cl/api"},
        {"public_base_url": "https://dashboard.origenlab.cl/api?x=1"},
        {"public_base_url": "https://user:pw@dashboard.origenlab.cl/api"},
        {"workspace_domain": "gmail.com"},
        {"workspace_domain": "not a domain"},
        {"workspace_domain": ""},
        {"session_ttl_seconds": 10},
    ],
)
def test_incomplete_or_unsafe_configuration_refuses_to_start(override) -> None:
    with pytest.raises(GoogleAuthMisconfigured):
        _config(**override)


def test_google_sign_in_without_a_v2_database_refuses_to_start(monkeypatch) -> None:
    _env(monkeypatch)
    monkeypatch.delenv("ORIGENLAB_V2_DATABASE_URL")
    with pytest.raises(ValueError, match="ORIGENLAB_V2_DATABASE_URL"):
        create_app()


def test_the_session_secret_never_appears_in_settings_repr(monkeypatch) -> None:
    _env(monkeypatch)
    from origenlab_api.settings import build_settings

    import os

    settings = build_settings(dotenv_disabled=True)
    assert os.environ["ORIGENLAB_AUTH_SESSION_SECRET"] not in repr(settings)
    assert os.environ["ORIGENLAB_GOOGLE_CLIENT_SECRET"] not in repr(settings)


def test_the_auth_routes_are_absent_without_v2(monkeypatch) -> None:
    monkeypatch.setenv("ORIGENLAB_DISABLE_DOTENV", "1")
    monkeypatch.delenv("ORIGENLAB_V2_DATABASE_URL", raising=False)
    monkeypatch.delenv("ORIGENLAB_GOOGLE_AUTH_ENABLED", raising=False)
    client = TestClient(create_app())
    assert client.get("/auth/session").status_code == 404
    assert client.get("/auth/google/login", follow_redirects=False).status_code == 404


def test_only_logout_is_a_post_route_under_auth(monkeypatch) -> None:
    h = _Harness(monkeypatch)
    paths = h.app.openapi()["paths"]
    auth = {path: set(ops) for path, ops in paths.items() if path.startswith("/auth")}
    assert auth == {
        "/auth/google/login": {"get"},
        "/auth/google/callback": {"get"},
        "/auth/session": {"get"},
        "/auth/logout": {"post"},
    }


# --------------------------------------------------------------------- token exchange


def test_the_token_exchange_sends_the_verifier_and_secret_to_google(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def read(self) -> bytes:
            return b'{"id_token": "a.b.c"}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = parse_qs(request.data.decode())
        captured["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr(google_oidc.urllib.request, "urlopen", fake_urlopen)
    config = _config()
    result = google_oidc.exchange_code(code="the-code", verifier="the-verifier", config=config)
    assert result == {"id_token": "a.b.c"}
    assert captured["url"] == "https://oauth2.googleapis.com/token"
    assert captured["body"] == {
        "grant_type": ["authorization_code"], "code": ["the-code"],
        "redirect_uri": [f"{LOCAL_BASE}/auth/google/callback"], "client_id": [CLIENT_ID],
        "client_secret": ["s" * 24], "code_verifier": ["the-verifier"],
    }
    assert captured["timeout"] == google_oidc.TOKEN_EXCHANGE_TIMEOUT_SECONDS


def test_a_token_endpoint_error_becomes_a_refusal(monkeypatch) -> None:
    import urllib.error

    def boom(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 400, "bad", {}, None)

    monkeypatch.setattr(google_oidc.urllib.request, "urlopen", boom)
    with pytest.raises(google_oidc.TokenExchangeFailed, match="400"):
        google_oidc.exchange_code(code="c", verifier="v", config=_config())


# ------------------------------------------------------------------------- the signer


def test_cookie_kinds_are_not_interchangeable() -> None:
    signer = CookieSigner("k" * 48)
    now = time.time()
    session = signer.dump_session(email_norm=EMAIL, operator_id=OPERATOR_ID, google_sub="s",
                                  ttl=600, now=now)
    tx = signer.dump_transaction(state="s", nonce="n", verifier="v", now=now)
    assert signer.load_session(session, now=now)["email"] == EMAIL
    with pytest.raises(CookieRefused):
        signer.load_transaction(session, now=now)
    with pytest.raises(CookieRefused):
        signer.load_session(tx, now=now)


def test_the_session_adapter_is_used_when_google_is_on() -> None:
    config = _config()
    port = build_identity_port(jwks_url=None, database_url=LOOPBACK, lookup=_Repo(), google=config)
    assert isinstance(port, GoogleSessionIdentity)


def test_a_malformed_neighbour_cookie_does_not_hide_the_session() -> None:
    # `http.cookies.SimpleCookie` abandons the whole header at the first cookie it cannot
    # parse; any other app on `localhost` could then sign the operator out.
    from origenlab_api.v2.auth_session import read_cookie

    header = 'bad"cookie=[x; origenlab_session=abc.def; other=1'
    assert read_cookie({"Cookie": header}, "origenlab_session") == "abc.def"
    assert read_cookie({"cookie": "a=1"}, "origenlab_session") is None
    assert read_cookie({}, "origenlab_session") is None
