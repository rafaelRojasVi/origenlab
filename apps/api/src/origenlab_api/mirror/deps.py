"""FastAPI dependencies for Postgres mirror routes (read-only)."""

from __future__ import annotations

from collections.abc import Generator
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request
from psycopg import Connection

from origenlab_api.repositories.postgres.common import normalize_postgres_url
from origenlab_api.settings import Settings, get_settings
from origenlab_email_pipeline.postgres_dashboard_api.db import (
    PostgresUnavailableError,
    postgres_connection,
)
from origenlab_email_pipeline.postgres_url import (
    PostgresUrlError,
    redact_postgres_url,
    resolve_postgres_url,
)


class MirrorConfigError(RuntimeError):
    """Mirror routes require a Postgres URL."""


def resolve_mirror_postgres_url(settings: Settings) -> str:
    direct = (settings.postgres_url or "").strip()
    if direct:
        return normalize_postgres_url(direct)
    try:
        url = resolve_postgres_url(None, required=True)
    except PostgresUrlError as exc:
        raise MirrorConfigError(str(exc)) from exc
    if not url:
        raise MirrorConfigError(
            "Postgres URL required for /mirror routes. "
            "Set ORIGENLAB_POSTGRES_URL or ALEMBIC_DATABASE_URL."
        )
    return normalize_postgres_url(url)


def mirror_postgres_url_redacted(settings: Settings) -> str:
    return redact_postgres_url(resolve_mirror_postgres_url(settings))


def get_mirror_db_conn(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> Generator[Connection, None, None]:
    """Yield a read-only Postgres connection (one per request)."""
    try:
        url = resolve_mirror_postgres_url(settings)
    except MirrorConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        with postgres_connection(url) as conn:
            request.state.mirror_db_conn = conn
            yield conn
    except HTTPException:
        raise
    except PostgresUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Postgres connection failed: {exc}",
        ) from exc


MirrorDbConn = Annotated[Connection, Depends(get_mirror_db_conn)]


def mirror_gmail_user() -> str:
    from origenlab_email_pipeline.config import load_settings

    return (load_settings().gmail_workspace_user or "contacto@origenlab.cl").strip()
