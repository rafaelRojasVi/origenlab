"""FastAPI application for the OrigenLab operator plane."""

from __future__ import annotations

from fastapi import FastAPI

from origenlab_api.backends.factory import validate_api_settings
from origenlab_api.errors import register_exception_handlers
from origenlab_api.http_security import configure_http_security, openapi_docs_enabled
from origenlab_api.request_id import RequestIdMiddleware
from origenlab_api.response_timing import ResponseTimingMiddleware
from origenlab_api.mirror import router as mirror_router
from origenlab_api.routes import (
    cases,
    contacts,
    emails,
    health,
    institutions,
    operations,
    operator,
    opportunities,
)
from origenlab_api.settings import Settings, get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    docs_on = openapi_docs_enabled(settings)
    app = FastAPI(
        title="OrigenLab API",
        description=(
            "Operator API (SQLite-first). "
            "Postgres mirror routes under /mirror/* remain read-only reporting. "
            "Does not send email or ingest Gmail. "
            "SQLite remains read-only. "
            "Durable commercial-operations writes are permitted only through "
            "the explicitly allowlisted /operations/* command routes when "
            "commercial writes are enabled and trusted operator identity is present. "
            "The procurement tender workflow permits one explicit file-backed "
            "operator document import; other contact/outreach mutations remain "
            "outside this API."
        ),
        version="0.1.0",
        docs_url="/docs" if docs_on else None,
        redoc_url="/redoc" if docs_on else None,
        openapi_url="/openapi.json" if docs_on else None,
    )
    configure_http_security(app, settings)
    # Starlette runs the last-added middleware outermost. Register request-id
    # first, then timing, so ResponseTimingMiddleware wraps RequestIdMiddleware
    # and both headers remain present on the response.
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(ResponseTimingMiddleware)
    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(operator.router)
    app.include_router(emails.router)
    app.include_router(cases.router)
    app.include_router(opportunities.router)
    app.include_router(operations.router)
    app.include_router(institutions.router)
    app.include_router(mirror_router)
    app.include_router(contacts.router)
    _mount_v2_read_boundary(app, settings)
    validate_api_settings(settings)
    return app


def _mount_v2_read_boundary(app: FastAPI, settings: Settings) -> None:
    """Mount `/v2/*` only when a V2 database is configured.

    An unconfigured deployment gets no V2 surface at all — not a surface that answers 503 on
    every request. That keeps the running V1 API byte-identical until someone deliberately
    points this app at the V2 durable core, which is the only safe default while the hosted
    phase is frozen (`docs/OPERATIONS.md` §1.1).
    """
    if not settings.v2_configured():
        return

    import psycopg

    from origenlab_api.v2.identity import build_identity_port
    from origenlab_api.v2.repository import V2Repository
    from origenlab_api.v2.routes import router as v2_router

    dsn = settings.require_v2_database_url()
    repository = V2Repository(
        psycopg.connect, dsn, statement_timeout_ms=settings.v2_statement_timeout_ms
    )
    app.state.v2_repository = repository
    # Constructing the port here, at startup, is deliberate: a misconfigured identity must
    # fail the process rather than surface as a per-request error that looks like a bad
    # credential.
    app.state.v2_identity = build_identity_port(
        jwks_url=settings.v2_jwks_url, database_url=dsn, lookup=repository
    )
    app.include_router(v2_router)


app = create_app()
