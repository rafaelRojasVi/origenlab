"""Process-wide psycopg3 connection pool for the V2 database.

Shape-compatible with ``psycopg.connect``: every V2 repository receives a callable
with the same ``connect(dsn, autocommit=False)`` context-manager signature and
never knows it is talking to a pool.

Session-state safety
--------------------
All ``SET`` statements used by V2 repositories are either transaction-level
(``SET TRANSACTION READ ONLY``, ``SET TRANSACTION ISOLATION LEVEL …``) or
transaction-local (``SET LOCAL statement_timeout``).  Both variants are reset
when the transaction ends (commit or rollback).  The pool's own
rollback-on-return always clears any open transaction before a connection is
reused, so no session state leaks between requests.

Startup safety
--------------
The pool is opened with ``wait=False``: the background maintenance thread
starts without blocking.  If the database is unreachable at boot the pool
starts empty and the first request fails per-request, exactly as it does
today without a pool.
"""
from __future__ import annotations

from typing import Any

import psycopg_pool


class V2ConnectionPool:
    """Wraps ``psycopg_pool.ConnectionPool`` with the repositories' ``connect`` API.

    Usage (in ``main.py``)::

        pool = V2ConnectionPool(target.dsn, connect_kwargs=target.connect_options)
        connect = pool.connect       # drop-in replacement for psycopg.connect
        app.state.v2_pool = pool
        # … pass connect to every repository …

    The ``dsn`` accepted by :meth:`connect` is ignored at call time — it is
    present only so the signature matches ``psycopg.connect``.  The pool was
    initialised with the validated, TLS-bound DSN.
    """

    def __init__(
        self,
        dsn: str,
        *,
        min_size: int = 1,
        max_size: int = 4,
        connect_kwargs: dict[str, Any] | None = None,
        max_idle: float = 300.0,
        max_lifetime: float = 3600.0,
    ) -> None:
        kwargs: dict[str, Any] = dict(connect_kwargs or {})
        # Repositories always open connections with autocommit=False; enforce it.
        kwargs["autocommit"] = False
        # Kept separately so connect_options is inspectable without accessing pool internals.
        self._connect_options: dict[str, Any] = dict(kwargs)
        self._pool = psycopg_pool.ConnectionPool(
            conninfo=dsn,
            min_size=min_size,
            max_size=max_size,
            kwargs=kwargs,
            max_idle=max_idle,
            max_lifetime=max_lifetime,
            check=psycopg_pool.ConnectionPool.check_connection,
            open=False,  # opened from the app lifespan, not at construction
        )

    @property
    def connect_options(self) -> dict[str, Any]:
        """The connect kwargs this pool passes to every new connection (includes TLS options).

        Exposed so tests and operators can confirm that TLS is wired without
        accessing ``psycopg_pool`` internals.
        """
        return dict(self._connect_options)

    def open(self) -> None:
        """Start pool maintenance without blocking for ``min_size`` connections.

        Called once from the FastAPI lifespan.  If the database is unreachable,
        the background thread retries silently; requests fail normally until
        connectivity is restored.
        """
        self._pool.open(wait=False)

    def close(self) -> None:
        """Drain and shut down the pool.  Called from the FastAPI lifespan."""
        self._pool.close()

    def connect(self, dsn: str, *, autocommit: bool = False) -> Any:
        """Return a context manager that yields a pooled connection.

        Matches the ``psycopg.connect`` call signature:

        * ``dsn`` is accepted for API compatibility and ignored.
        * ``autocommit`` must be ``False`` (the only supported value).

        On exit, ``psycopg_pool`` rolls back any open transaction before
        returning the connection to the pool, so no uncommitted state leaks.
        """
        if autocommit:
            raise ValueError(
                "V2ConnectionPool.connect: autocommit=True is not supported; "
                "every V2 repository uses explicit commit/rollback"
            )
        return self._pool.connection()
