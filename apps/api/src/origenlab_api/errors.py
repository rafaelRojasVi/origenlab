"""Centralized safe JSON error responses for apps/api."""

from __future__ import annotations

import re
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from origenlab_api.repositories.postgres.common import PostgresBackendUnavailableError
from origenlab_api.schemas.errors import ApiErrorBody, ApiErrorResponse
from origenlab_api.request_id import attach_request_id_header, get_request_id

_POSTGRES_URL_RE = re.compile(r"postgres(?:ql)?://[^\s\"']+", re.IGNORECASE)
_ENV_SECRET_RE = re.compile(
    r"(ORIGENLAB_POSTGRES_URL|ALEMBIC_DATABASE_URL|CHILECOMPRA_API_TICKET)=\S+",
    re.IGNORECASE,
)
_PASSWORD_KV_RE = re.compile(r"(password|passwd|pwd)\s*[:=]\s*\S+", re.IGNORECASE)
_SENSITIVE_DETAIL_KEY_RE = re.compile(
    r"(password|passwd|pwd|token|secret|api[_-]?key|postgres_url|database_url)",
    re.IGNORECASE,
)

_FORBIDDEN_SUBSTRINGS = (
    "traceback",
    "Traceback",
)


def sanitize_text(text: str) -> str:
    """Redact secrets and connection strings from operator-facing error text."""
    if not text:
        return ""
    cleaned = text
    cleaned = _POSTGRES_URL_RE.sub("<redacted-database-url>", cleaned)
    cleaned = _ENV_SECRET_RE.sub(r"\1=<redacted>", cleaned)
    cleaned = _PASSWORD_KV_RE.sub(r"\1=<redacted>", cleaned)
    for needle in _FORBIDDEN_SUBSTRINGS:
        if needle in cleaned:
            return "An error occurred"
    return cleaned



def _safe_detail_key(key: Any) -> str:
    """Return a safe JSON object key for error details."""
    raw = sanitize_text(str(key))
    if _SENSITIVE_DETAIL_KEY_RE.search(raw):
        return "redacted"
    return raw


def sanitize_value(value: Any) -> Any:
    """Recursively redact unsafe strings in JSON-compatible error details."""
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            safe_key = _safe_detail_key(key)
            if safe_key == "redacted":
                sanitized[safe_key] = "<redacted>"
            else:
                sanitized[safe_key] = sanitize_value(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_value(v) for v in value]
    if isinstance(value, tuple):
        return [sanitize_value(v) for v in value]
    return value


def _error_payload(
    *,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    body = ApiErrorResponse(
        error=ApiErrorBody(
            code=code,
            message=sanitize_text(message),
            details=details or {},
            request_id=request_id,
        )
    )
    return body.model_dump()


def json_error_response(
    status_code: int,
    *,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> JSONResponse:
    response = JSONResponse(
        status_code=status_code,
        content=_error_payload(
            code=code,
            message=message,
            details=details,
            request_id=request_id,
        ),
    )
    attach_request_id_header(response, request_id)
    return response


def _string_detail(exc: StarletteHTTPException) -> str:
    detail = exc.detail
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict):
        message = detail.get("message") or detail.get("msg")
        if isinstance(message, str):
            return message
        return sanitize_text(str(detail))
    if isinstance(detail, list):
        parts = [str(item) for item in detail[:3]]
        return "; ".join(parts) if parts else "Request failed"
    return "Request failed"


def _code_for_http_exception(exc: StarletteHTTPException, message: str) -> str:
    status = exc.status_code
    lowered = message.lower()
    if status == 403:
        return "forbidden"
    if status == 404:
        return "not_found"
    if status == 503:
        if "mirror" in lowered or "postgres url required" in lowered or "origenlab_postgres_url" in lowered:
            return "mirror_not_configured"
        return "backend_unavailable"
    if status == 422 and "invalid category" in lowered:
        return "invalid_query_param"
    if status in {400, 422}:
        return "validation_error"
    if status >= 500:
        return "internal_error"
    return "validation_error"


def _details_for_http_exception(exc: StarletteHTTPException, message: str) -> dict[str, Any]:
    details: dict[str, Any] = {}
    if exc.status_code == 422 and "invalid category" in message.lower():
        details["param"] = "category"
        if "allowed:" in message:
            details["hint"] = sanitize_text(message.split("allowed:", 1)[1].strip())
    detail = exc.detail
    if isinstance(detail, dict):
        details.update(sanitize_value(detail))
    return details


def _validation_details(exc: RequestValidationError) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    for item in exc.errors():
        errors.append(
            {
                "loc": [str(part) for part in item.get("loc", ())],
                "msg": sanitize_text(str(item.get("msg", ""))),
                "type": str(item.get("type", "")),
            }
        )
    return {"validation_errors": errors}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(PostgresBackendUnavailableError)
    async def postgres_backend_unavailable_handler(
        request: Request,
        exc: PostgresBackendUnavailableError,
    ) -> JSONResponse:
        return json_error_response(
            503,
            code="backend_unavailable",
            message=str(exc) or "Postgres read model unavailable.",
            details={"backend": "postgres"},
            request_id=get_request_id(request),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        message = _string_detail(exc)
        code = _code_for_http_exception(exc, message)
        return json_error_response(
            exc.status_code,
            code=code,
            message=message,
            details=_details_for_http_exception(exc, message),
            request_id=get_request_id(request),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return json_error_response(
            422,
            code="validation_error",
            message="Request validation failed",
            details=_validation_details(exc),
            request_id=get_request_id(request),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        return json_error_response(
            500,
            code="internal_error",
            message="An unexpected error occurred",
            details={},
            request_id=get_request_id(request),
        )
