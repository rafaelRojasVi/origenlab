"""Centralized safe JSON error responses for apps/api."""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg_pool import PoolTimeout
from starlette.exceptions import HTTPException as StarletteHTTPException

from origenlab_api.repositories.postgres.common import PostgresBackendUnavailableError
from origenlab_api.schemas.errors import ApiErrorBody, ApiErrorResponse
from origenlab_api.request_id import attach_request_id_header, get_request_id

_log = logging.getLogger(__name__)

#: Seconds a client should wait before retrying a request the database was too busy to serve.
SERVICE_BUSY_RETRY_AFTER_SECONDS = 5

#: SQLSTATEs a V2 command answers with a refusal instead of a 500 (`database_refusal`).
LOCK_NOT_AVAILABLE = "55P03"
QUERY_CANCELED = "57014"
UNIQUE_VIOLATION = "23505"


class DatabaseRefusal(Exception):
    """A command the database stopped for a reason the operator can act on — not a bug.

    Raised by `v2.command_core.CommandTransaction` *after* its transaction rolled back, so
    nothing the command would have written exists, its idempotency receipt included: the same
    request may be sent again. `code` is what the dashboard reads:

    * `record_busy` (409) — another transaction held a row lock longer than `lock_timeout`;
    * `service_busy` (503, `Retry-After`) — the statement timed out, or no pooled connection
      was free in time;
    * `duplicate` (409) — a unique constraint the command did not check for itself refused
      the row; `details.constraint` names the constraint, never the values.
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        self.retry_after = retry_after


def _service_busy() -> DatabaseRefusal:
    return DatabaseRefusal(
        503,
        "service_busy",
        "the database is busy; nothing was written, retry in a few seconds",
        retry_after=SERVICE_BUSY_RETRY_AFTER_SECONDS,
    )


def database_refusal(exc: BaseException) -> DatabaseRefusal | None:
    """The refusal a database failure inside a command stands for, or None for a real fault.

    Reads `sqlstate` rather than matching driver classes, so a fake connection in a test and
    psycopg in production take the same path. Anything not listed stays an exception and
    answers 500 `internal_error`.
    """
    if isinstance(exc, PoolTimeout):
        return _service_busy()
    sqlstate = getattr(exc, "sqlstate", None)
    if sqlstate == LOCK_NOT_AVAILABLE:
        return DatabaseRefusal(
            409,
            "record_busy",
            "another operator is saving this record; nothing was written, retry in a few seconds",
        )
    if sqlstate == QUERY_CANCELED:
        return _service_busy()
    if sqlstate == UNIQUE_VIOLATION:
        constraint = getattr(getattr(exc, "diag", None), "constraint_name", None)
        return DatabaseRefusal(
            409,
            "duplicate",
            "a record with these values already exists; nothing was written",
            details={"constraint": str(constraint)} if constraint else {},
        )
    return None

_POSTGRES_URL_RE = re.compile(r"postgres(?:ql)?://[^\s\"']+", re.IGNORECASE)
_ENV_SECRET_RE = re.compile(
    r"(ORIGENLAB_POSTGRES_URL|ORIGENLAB_POSTGRES_WRITE_URL|ALEMBIC_DATABASE_URL|CHILECOMPRA_API_TICKET)=\S+",
    re.IGNORECASE,
)
_PASSWORD_KV_RE = re.compile(r"(password|passwd|pwd)\s*[:=]\s*\S+", re.IGNORECASE)
_SENSITIVE_DETAIL_KEY_RE = re.compile(
    r"(password|passwd|pwd|token|secret|api[_-]?key|postgres(?:_write)?_url|database_url)",
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
    if status == 401:
        return "unauthorized"
    if status == 403:
        return "forbidden"
    if status == 404:
        return "not_found"
    if status == 409:
        return "conflict"
    if status == 503:
        if (
            "mirror" in lowered
            or "postgres url required" in lowered
            or "origenlab_postgres_url" in lowered
        ):
            return "mirror_not_configured"
        return "backend_unavailable"
    if status == 422 and "invalid category" in lowered:
        return "invalid_query_param"
    if status in {400, 422}:
        return "validation_error"
    if status >= 500:
        return "internal_error"
    return "validation_error"


def _details_for_http_exception(
    exc: StarletteHTTPException, message: str
) -> dict[str, Any]:
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

    @app.exception_handler(DatabaseRefusal)
    async def database_refusal_handler(
        request: Request,
        exc: DatabaseRefusal,
    ) -> JSONResponse:
        # The code and the driver's class name only: a path can carry an identifier.
        cause = exc.__cause__
        _log.warning("command not run: %s (%s)", exc.code, type(cause).__name__ if cause else "-")
        response = json_error_response(
            exc.status_code,
            code=exc.code,
            message=exc.message,
            details=sanitize_value(exc.details),
            request_id=get_request_id(request),
        )
        if exc.retry_after is not None:
            response.headers["Retry-After"] = str(exc.retry_after)
        return response

    @app.exception_handler(PoolTimeout)
    async def pool_timeout_handler(
        request: Request,
        exc: PoolTimeout,
    ) -> JSONResponse:
        # Every pooled connection stayed busy for the whole wait — a read and a command alike.
        refusal = _service_busy()
        refusal.__cause__ = exc
        return await database_refusal_handler(request, refusal)

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
