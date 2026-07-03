"""Production HTTP hardening (CORS, OpenAPI docs, response headers) for read-only operator API."""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from origenlab_api.errors import json_error_response
from origenlab_api.request_id import get_request_id, resolve_request_id
from origenlab_api.settings import Settings

_OPERATOR_CACHE_CONTROL = "no-store, private"
_HOST_REJECT_STATUS = 403
_UNAUTHORIZED_STATUS = 401
_PUBLIC_PATHS = frozenset({"/health"})
_API_KEY_HEADER = "X-OriginLab-API-Key"
_SECURITY_RESPONSE_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
    "Cache-Control": _OPERATOR_CACHE_CONTROL,
}


def normalize_host_header(host: str | None) -> str | None:
    """Strip port and lowercase for exact Host allowlist matching."""
    if host is None:
        return None
    value = host.strip()
    if not value:
        return None
    # Use first token if a proxy forwarded multiple values.
    value = value.split(",", 1)[0].strip()
    if ":" in value:
        value = value.rsplit(":", 1)[0].strip()
    return value.lower() if value else None


def host_allowlist_enabled(settings: Settings) -> bool:
    return settings.production_mode() and bool(settings.parsed_allowed_hosts())


def request_host_allowed(request: Request, settings: Settings) -> bool:
    if not host_allowlist_enabled(settings):
        return True
    allowed = frozenset(settings.parsed_allowed_hosts())
    host = normalize_host_header(request.headers.get("host"))
    if host is None:
        return False
    return host in allowed


def api_auth_enabled(settings: Settings) -> bool:
    return settings.production_mode() and bool((settings.api_auth_token or "").strip())


def is_public_route(request: Request) -> bool:
    if request.method == "OPTIONS":
        return True
    return request.url.path in _PUBLIC_PATHS


def extract_api_token(request: Request) -> str | None:
    auth = (request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        if token:
            return token
    api_key = (request.headers.get(_API_KEY_HEADER) or "").strip()
    return api_key or None


def api_token_is_valid(provided: str | None, expected: str) -> bool:
    if not provided:
        return False
    return secrets.compare_digest(provided, expected)


class ApiTokenAuthMiddleware(BaseHTTPMiddleware):
    """Require bearer/API-key auth for non-public routes in production."""

    def __init__(self, app, settings: Settings) -> None:
        super().__init__(app)
        self._expected_token = (settings.api_auth_token or "").strip()

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        resolve_request_id(request)
        if is_public_route(request):
            return await call_next(request)
        provided = extract_api_token(request)
        if api_token_is_valid(provided, self._expected_token):
            return await call_next(request)
        request_id = get_request_id(request)
        response = json_error_response(
            _UNAUTHORIZED_STATUS,
            code="unauthorized",
            message="Unauthorized",
            request_id=request_id,
        )
        response.headers["WWW-Authenticate"] = "Bearer"
        response.headers["Cache-Control"] = _OPERATOR_CACHE_CONTROL
        return response


class AllowedHostMiddleware(BaseHTTPMiddleware):
    """Reject requests whose Host is not in ORIGENLAB_API_ALLOWED_HOSTS (production only)."""

    def __init__(self, app, settings: Settings) -> None:
        super().__init__(app)
        self._settings = settings

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        resolve_request_id(request)
        if not request_host_allowed(request, self._settings):
            request_id = get_request_id(request)
            response = json_error_response(
                _HOST_REJECT_STATUS,
                code="forbidden",
                message="Forbidden",
                request_id=request_id,
            )
            response.headers["Cache-Control"] = _OPERATOR_CACHE_CONTROL
            return response
        return await call_next(request)


class OperatorSecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Baseline headers for JSON operator responses (no full CSP on API)."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        for key, value in _SECURITY_RESPONSE_HEADERS.items():
            if key not in response.headers:
                response.headers[key] = value
        return response


def openapi_docs_enabled(settings: Settings) -> bool:
    if settings.api_disable_docs:
        return False
    if settings.production_mode():
        return False
    return True


def validate_http_security_settings(settings: Settings) -> None:
    """Fail fast on unsafe production CORS / Host configuration."""
    for host in settings.parsed_allowed_hosts():
        if host in ("*", "localhost", "127.0.0.1"):
            raise ValueError(
                f"Invalid ORIGENLAB_API_ALLOWED_HOSTS entry {host!r}: "
                "use the public API hostname only (not * or loopback)"
            )
        if "/" in host or " " in host:
            raise ValueError(f"Invalid ORIGENLAB_API_ALLOWED_HOSTS entry {host!r}")
    for origin in settings.parsed_cors_origins():
        if origin == "*":
            raise ValueError(
                "ORIGENLAB_API_CORS_ORIGINS must not include '*' "
                "(credentialed CORS requires explicit dashboard origins)"
            )
        if not origin.startswith(("https://", "http://")):
            raise ValueError(
                f"Invalid CORS origin {origin!r}: must start with http:// or https://"
            )


def configure_http_security(app: FastAPI, settings: Settings) -> None:
    validate_http_security_settings(settings)
    if host_allowlist_enabled(settings):
        app.add_middleware(AllowedHostMiddleware, settings=settings)
    if api_auth_enabled(settings):
        app.add_middleware(ApiTokenAuthMiddleware, settings=settings)
    app.add_middleware(OperatorSecurityHeadersMiddleware)
    origins = settings.parsed_cors_origins()
    if not origins:
        return
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "HEAD", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
