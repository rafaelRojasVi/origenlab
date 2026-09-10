"""Neutral Postgres URL helpers: normalize, redact, resolve.

These three functions have nothing to do with outbound campaigns, but they lived in
``postgres_outbound_audit`` and were imported from there by ``apps/api``'s ``/mirror/*``
dependencies and by ``mart_core_postgres_migrate``. That coupling is what would have made
the outbound mirror apparatus undeletable, so the implementation lives here instead.

``postgres_outbound_audit`` keeps a thin wrapper around :func:`resolve_postgres_url` that
raises its own ``OutboundAuditError``, so the export lanes that catch it are unchanged.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit, urlunsplit

#: Environment variables consulted, in order, when no URL is passed explicitly.
POSTGRES_URL_ENV_KEYS: tuple[str, ...] = ("ORIGENLAB_POSTGRES_URL", "ALEMBIC_DATABASE_URL")


class PostgresUrlError(RuntimeError):
    """No Postgres URL could be resolved where one was required."""


def normalize_postgres_url(url: str) -> str:
    """Strip a SQLAlchemy driver suffix so the URL is usable by psycopg directly."""
    u = url.strip()
    for prefix in ("postgresql+psycopg://", "postgresql+psycopg2://"):
        if u.startswith(prefix):
            return "postgresql://" + u[len(prefix) :]
    return u


def redact_postgres_url(url: str | None) -> str:
    """Return ``url`` with the password replaced by ``***``. Never raises."""
    if not url:
        return "<empty>"
    try:
        p = urlsplit(url)
        if not p.netloc:
            return "<invalid-postgres-url>"
        hostpart = p.hostname or "unknown-host"
        if p.port:
            hostpart = f"{hostpart}:{p.port}"
        userpart = ""
        if p.username:
            userpart = f"{p.username}:***@"
        return urlunsplit((p.scheme, f"{userpart}{hostpart}", p.path, p.query, p.fragment))
    except Exception:  # noqa: BLE001
        return "<unredactable-postgres-url>"


def resolve_postgres_url(explicit_url: str | None = None, *, required: bool = False) -> str | None:
    """Resolve a normalized Postgres URL from ``explicit_url`` then the environment.

    Args:
        explicit_url: a URL given on the command line or in settings; wins when non-blank.
        required: when true, a failure to resolve raises instead of returning ``None``.

    Raises:
        PostgresUrlError: if ``required`` and nothing resolved.
    """
    if explicit_url and explicit_url.strip():
        return normalize_postgres_url(explicit_url)
    for key in POSTGRES_URL_ENV_KEYS:
        value = (os.environ.get(key) or "").strip()
        if value:
            return normalize_postgres_url(value)
    if required:
        raise PostgresUrlError(
            "No Postgres URL resolved. Pass one explicitly or set "
            + " / ".join(POSTGRES_URL_ENV_KEYS)
            + "."
        )
    return None
