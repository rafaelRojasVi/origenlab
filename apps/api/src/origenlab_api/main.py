"""FastAPI application (API-0: operator plane, read-only)."""

from __future__ import annotations

from fastapi import FastAPI

from origenlab_api.backends.factory import validate_api_settings
from origenlab_api.errors import register_exception_handlers
from origenlab_api.http_security import configure_http_security, openapi_docs_enabled
from origenlab_api.request_id import RequestIdMiddleware
from origenlab_api.response_timing import ResponseTimingMiddleware
from origenlab_api.mirror import router as mirror_router
from origenlab_api.routes import cases, contacts, emails, health, institutions, operator, opportunities
from origenlab_api.settings import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    docs_on = openapi_docs_enabled(settings)
    app = FastAPI(
        title="OrigenLab API",
        description=(
            "Read-only operator API (SQLite-first). "
            "Postgres mirror routes live under /mirror/* (API-3 Phase 1 complete; Phase 2 parity frozen). "
            "Does not send email, ingest Gmail, or write SQLite/Postgres. "
            "email-pipeline scripts remain the mutation path."
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
    app.include_router(institutions.router)
    app.include_router(mirror_router)
    app.include_router(contacts.router)
    validate_api_settings(settings)
    return app


app = create_app()
