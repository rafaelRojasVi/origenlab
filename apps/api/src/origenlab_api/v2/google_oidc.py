"""Google Workspace sign-in (OpenID Connect, authorization-code flow with PKCE).

What this module decides, and why each rule is here rather than trusted to Google's UI:

* **Scopes are `openid email profile` and nothing else.** The dashboard learns who the
  person is; it gets no access to Gmail, Drive, Calendar or any other Google API. No refresh
  token is requested (`access_type` is left at its `online` default) and the access token the
  token endpoint returns is discarded unread.
* **The `hd` authorization parameter is a hint, not a check.** It only shapes Google's account
  picker. The domain is enforced on the returned claims, twice: the verified address must end
  exactly in `@<domain>`, and the `hd` claim must equal `<domain>`. The second check is the one
  that matters: a consumer Google account can be created with an `@origenlab.cl` address, and
  only a Workspace account carries `hd`.
* **The ID token signature is verified against Google's JWKS** (`google_jwks.py`), before
  any claim is read, even though the token arrives over the back channel — OpenID Connect
  Core §3.1.3.7 (6) would allow TLS to stand in for it, and the owner asked for the check as
  production hardening. Every claim is then checked too: `iss`, `aud`, `azp`, `exp`, `iat`,
  `nonce`, `email_verified`, the address domain, and `hd`. An unreachable key set refuses
  the sign-in; nothing falls back to the unverified claims.
* **Google's endpoints are constants.** No discovery document is fetched at runtime, so
  nothing reachable over the network can redirect the token exchange or the key fetch.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Protocol
from urllib.parse import urlsplit

from origenlab_api.commercial_operator_identity import normalize_operator_email
from origenlab_api.v2.auth_session import CookieNames, CookieSigner, is_secure_base_url

GOOGLE_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
#: Google issues both forms (https://developers.google.com/identity/openid-connect/openid-connect#validatinganidtoken).
GOOGLE_ISSUERS = frozenset({"https://accounts.google.com", "accounts.google.com"})
SCOPES = "openid email profile"
CALLBACK_PATH = "/auth/google/callback"

#: Seconds of clock skew tolerated on `exp` and `iat`.
CLOCK_SKEW_SECONDS = 60
TOKEN_EXCHANGE_TIMEOUT_SECONDS = 10

#: Consumer domains. Their accounts never carry `hd`, so a configuration naming one could
#: never admit anybody — refusing it at startup says so instead of failing every sign-in.
_CONSUMER_DOMAINS = frozenset({"gmail.com", "googlemail.com"})
_DOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class GoogleAuthMisconfigured(ValueError):
    """The Google sign-in settings are incomplete or unsafe. Raised at startup."""


class TokenExchangeFailed(Exception):
    """Google's token endpoint did not return a usable response."""


class ClaimsRefused(Exception):
    """The ID token's claims do not admit this person.

    ``code`` is the stable, user-facing category — it goes into the redirect back to the
    dashboard. ``reason`` is the precise check that failed, for logs and tests only.
    """

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason


@dataclass(frozen=True)
class GoogleAuthConfig:
    client_id: str
    client_secret: str
    workspace_domain: str
    public_base_url: str
    redirect_uri: str
    dashboard_url: str
    secure_cookies: bool
    cookie_names: CookieNames
    session_ttl_seconds: int
    signer: CookieSigner


def _required(value: str | None, name: str) -> str:
    text = (value or "").strip()
    if not text:
        raise GoogleAuthMisconfigured(f"{name} is required when ORIGENLAB_GOOGLE_AUTH_ENABLED=true")
    return text


def validate_workspace_domain(raw: str | None) -> str:
    domain = (raw or "").strip().lower()
    if not _DOMAIN_RE.fullmatch(domain):
        raise GoogleAuthMisconfigured(
            f"ORIGENLAB_GOOGLE_WORKSPACE_DOMAIN {raw!r} is not a domain name"
        )
    if domain in _CONSUMER_DOMAINS:
        raise GoogleAuthMisconfigured(
            f"ORIGENLAB_GOOGLE_WORKSPACE_DOMAIN {domain!r} is a consumer domain; only a "
            "Google Workspace domain can be admitted"
        )
    return domain


def validate_public_base_url(raw: str | None, *, production: bool) -> str:
    """Return the base URL without a trailing slash, or refuse it.

    HTTPS is required, except plain HTTP on a loopback host for local development — and never
    in production mode.
    """
    url = _required(raw, "ORIGENLAB_AUTH_PUBLIC_BASE_URL").rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http"):
        raise GoogleAuthMisconfigured("ORIGENLAB_AUTH_PUBLIC_BASE_URL must be an http(s) URL")
    if parts.query or parts.fragment or parts.username or parts.password:
        raise GoogleAuthMisconfigured(
            "ORIGENLAB_AUTH_PUBLIC_BASE_URL must carry no query, fragment or credentials"
        )
    if not parts.hostname:
        raise GoogleAuthMisconfigured("ORIGENLAB_AUTH_PUBLIC_BASE_URL names no host")
    if parts.scheme == "http":
        if production:
            raise GoogleAuthMisconfigured(
                "ORIGENLAB_AUTH_PUBLIC_BASE_URL must be https:// when ORIGENLAB_ENV=production"
            )
        if parts.hostname not in _LOOPBACK_HOSTS:
            raise GoogleAuthMisconfigured(
                "ORIGENLAB_AUTH_PUBLIC_BASE_URL may use http:// only on a loopback host"
            )
    return url


def build_google_auth_config(
    *,
    enabled: bool,
    client_id: str | None,
    client_secret: str | None,
    workspace_domain: str | None,
    public_base_url: str | None,
    session_secret: str | None,
    session_ttl_seconds: int,
    production: bool,
) -> GoogleAuthConfig | None:
    """The validated configuration, or None when Google sign-in is switched off."""
    if not enabled:
        return None
    base = validate_public_base_url(public_base_url, production=production)
    if not (300 <= int(session_ttl_seconds) <= 7 * 24 * 60 * 60):
        raise GoogleAuthMisconfigured(
            "ORIGENLAB_AUTH_SESSION_TTL_SECONDS must be between 300 and 604800"
        )
    try:
        signer = CookieSigner(_required(session_secret, "ORIGENLAB_AUTH_SESSION_SECRET"))
    except ValueError as exc:
        raise GoogleAuthMisconfigured(str(exc)) from exc
    secure = is_secure_base_url(base)
    parts = urlsplit(base)
    return GoogleAuthConfig(
        client_id=_required(client_id, "ORIGENLAB_GOOGLE_CLIENT_ID"),
        client_secret=_required(client_secret, "ORIGENLAB_GOOGLE_CLIENT_SECRET"),
        workspace_domain=validate_workspace_domain(workspace_domain),
        public_base_url=base,
        redirect_uri=f"{base}{CALLBACK_PATH}",
        # The dashboard is served from the root of the same origin in every deployment this
        # repository knows (`render.yaml`, the Vite dev server), so the post-sign-in target
        # is derived from configuration, never from a request header or a query parameter.
        dashboard_url=f"{parts.scheme}://{parts.netloc}/",
        secure_cookies=secure,
        cookie_names=CookieNames.for_secure(secure),
        session_ttl_seconds=int(session_ttl_seconds),
        signer=signer,
    )


# ------------------------------------------------------------------------- the flow


@dataclass(frozen=True)
class SignInStart:
    state: str
    nonce: str
    verifier: str
    authorization_url: str


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def start_sign_in(config: GoogleAuthConfig) -> SignInStart:
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)  # 86 characters, inside RFC 7636's 43..128
    query = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "scope": SCOPES,
        "state": state,
        "nonce": nonce,
        "code_challenge": pkce_challenge(verifier),
        "code_challenge_method": "S256",
        "hd": config.workspace_domain,
        "prompt": "select_account",
    })
    return SignInStart(state, nonce, verifier, f"{GOOGLE_AUTHORIZATION_ENDPOINT}?{query}")


class TokenExchanger(Protocol):
    def __call__(self, *, code: str, verifier: str, config: GoogleAuthConfig) -> dict[str, Any]:
        ...


def exchange_code(*, code: str, verifier: str, config: GoogleAuthConfig) -> dict[str, Any]:
    """POST the authorization code to Google's token endpoint and return the JSON response."""
    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config.redirect_uri,
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "code_verifier": verifier,
    }).encode("ascii")
    request = urllib.request.Request(
        GOOGLE_TOKEN_ENDPOINT,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TOKEN_EXCHANGE_TIMEOUT_SECONDS) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise TokenExchangeFailed(f"token endpoint answered {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise TokenExchangeFailed(type(exc).__name__) from None
    if not isinstance(payload, dict):
        raise TokenExchangeFailed("token endpoint returned a non-object")
    return payload


def decode_id_token(id_token: Any) -> dict[str, Any]:
    """Decode the claims segment of a JWT whose signature `google_jwks.verify_signature` checked."""
    if not isinstance(id_token, str) or id_token.count(".") != 2:
        raise ClaimsRefused("invalid_token", "id_token is not a compact JWT")
    segment = id_token.split(".")[1]
    try:
        claims = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise ClaimsRefused("invalid_token", "id_token claims are not JSON") from None
    if not isinstance(claims, dict):
        raise ClaimsRefused("invalid_token", "id_token claims are not an object")
    return claims


@dataclass(frozen=True)
class VerifiedGoogleAccount:
    email_norm: str
    subject: str


def validate_claims(
    claims: dict[str, Any], *, client_id: str, nonce: str, workspace_domain: str, now: float
) -> VerifiedGoogleAccount:
    """Check every claim that decides admission. The first failure wins."""
    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise ClaimsRefused("invalid_token", "issuer is not Google")

    aud = claims.get("aud")
    audiences = aud if isinstance(aud, list) else [aud]
    if client_id not in audiences:
        raise ClaimsRefused("invalid_token", "audience is not this client")
    azp = claims.get("azp")
    if (len(audiences) > 1 or azp is not None) and azp != client_id:
        raise ClaimsRefused("invalid_token", "authorized party is not this client")

    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or isinstance(exp, bool) or exp + CLOCK_SKEW_SECONDS <= now:
        raise ClaimsRefused("invalid_token", "token is expired")
    iat = claims.get("iat")
    if not isinstance(iat, (int, float)) or isinstance(iat, bool) or iat - CLOCK_SKEW_SECONDS > now:
        raise ClaimsRefused("invalid_token", "token is issued in the future")

    token_nonce = claims.get("nonce")
    if not isinstance(token_nonce, str) or not secrets.compare_digest(token_nonce, nonce):
        raise ClaimsRefused("invalid_token", "nonce does not match this sign-in")

    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise ClaimsRefused("invalid_token", "token carries no subject")

    # Google has sent this as the string "true" in older tokens; nothing else counts. An
    # identity test, not `in (True, "true")`: `1 == True` in Python, and 1 is not a claim
    # Google makes.
    verified = claims.get("email_verified")
    if not (verified is True or verified == "true"):
        raise ClaimsRefused("email_unverified", "Google has not verified this address")

    raw_email = claims.get("email")
    email = normalize_operator_email(raw_email) if isinstance(raw_email, str) else None
    if email is None:
        raise ClaimsRefused("wrong_domain", "token carries no usable address")
    if email.rpartition("@")[2] != workspace_domain:
        raise ClaimsRefused("wrong_domain", "address is outside the Workspace domain")
    if claims.get("hd") != workspace_domain:
        raise ClaimsRefused("wrong_domain", "account is not a member of the Workspace domain")

    return VerifiedGoogleAccount(email_norm=email, subject=subject)


#: Test seam: the routes read the exchanger from app state, defaulting to the real one.
DEFAULT_TOKEN_EXCHANGER: Callable[..., dict[str, Any]] = exchange_code
