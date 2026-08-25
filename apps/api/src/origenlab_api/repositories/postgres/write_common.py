"""Restricted Postgres connection helper for durable operator writes.

This module deliberately uses ORIGENLAB_POSTGRES_WRITE_URL rather than the
read-model ORIGENLAB_POSTGRES_URL. Callers must never fall back from the write
credential to the read credential.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from origenlab_api.repositories.postgres.common import (
    PostgresBackendUnavailableError,
    normalize_postgres_url,
    require_psycopg,
)
from origenlab_api.settings import Settings


class PostgresWriteBackendUnavailableError(PostgresBackendUnavailableError):
    """Restricted operator-write Postgres dependency failed safely."""


def _is_psycopg_error(pg: Any, exc: BaseException) -> bool:
    error_cls = getattr(pg, "Error", None)
    return isinstance(error_cls, type) and isinstance(exc, error_cls)


@contextmanager
def postgres_write_connection(settings: Settings) -> Iterator[Any]:
    """Open the dedicated restricted commercial-operations write connection."""

    pg = require_psycopg()
    url = settings.require_postgres_write_url()
    timeout_ms = settings.postgres_statement_timeout_ms
    options = f"-c statement_timeout={timeout_ms}"

    try:
        with pg.connect(
            normalize_postgres_url(url),
            connect_timeout=10,
            options=options,
        ) as conn:
            yield conn
    except PostgresWriteBackendUnavailableError:
        raise
    except Exception as exc:
        if _is_psycopg_error(pg, exc):
            raise PostgresWriteBackendUnavailableError(
                "Commercial operations write database unavailable."
            ) from exc
        raise
