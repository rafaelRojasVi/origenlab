"""Shared Postgres read-only connection helpers."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from origenlab_api.settings import Settings

try:
    import psycopg
except ImportError as exc:  # pragma: no cover
    psycopg = None  # type: ignore[misc, assignment]
    _PSYCOPG_IMPORT_ERROR = exc
else:
    _PSYCOPG_IMPORT_ERROR = None


class PostgresBackendUnavailableError(RuntimeError):
    """Postgres read-model dependency is unavailable or failed safely."""



def normalize_postgres_url(url: str) -> str:
    u = url.strip()
    for prefix in ("postgresql+psycopg://", "postgresql+psycopg2://"):
        if u.startswith(prefix):
            return "postgresql://" + u[len(prefix) :]
    return u


def require_psycopg() -> Any:
    if psycopg is None:
        raise PostgresBackendUnavailableError(
            "Postgres driver unavailable; install the postgres dependency group."
        ) from _PSYCOPG_IMPORT_ERROR
    return psycopg


def _is_psycopg_error(pg: Any, exc: BaseException) -> bool:
    error_cls = getattr(pg, "Error", None)
    return isinstance(error_cls, type) and isinstance(exc, error_cls)


@contextmanager
def postgres_connection(settings: Settings) -> Iterator[Any]:
    pg = require_psycopg()
    url = settings.require_postgres_url()
    timeout_ms = settings.postgres_statement_timeout_ms
    options = f"-c statement_timeout={timeout_ms}"
    try:
        with pg.connect(
            normalize_postgres_url(url),
            connect_timeout=10,
            options=options,
        ) as conn:
            yield conn
    except PostgresBackendUnavailableError:
        raise
    except Exception as exc:
        if _is_psycopg_error(pg, exc):
            raise PostgresBackendUnavailableError(
                "Postgres read model unavailable."
            ) from exc
        raise
