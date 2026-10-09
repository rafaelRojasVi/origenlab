"""Connection pool for the V2 database — unit tests.

These tests run without a real database.  They verify the pool's interface
contract, session-state safety claims, and that the TLS connect options from
V2DatabaseTarget are passed through to every new connection.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

from origenlab_api.v2.connection_pool import V2ConnectionPool
from origenlab_api.v2.remote_database import (
    RemoteTargetRefused,
    validate_remote_target,
)

DSN = "postgresql://origenlab_api:pw@localhost:5432/origenlab_test"
TLS_OPTIONS = {
    "sslmode": "verify-full",
    "sslrootcert": "/tmp/ca.pem",
    "connect_timeout": "10",
}


# ─────────────────────────────────────────────────────────────── interface ──


def test_pool_connect_is_a_context_manager() -> None:
    """pool.connect() must return an object usable as a context manager."""
    pool = _fake_pool()
    ctx = pool.connect(DSN, autocommit=False)
    assert hasattr(ctx, "__enter__") and hasattr(ctx, "__exit__")


def test_pool_connect_ignores_dsn_argument() -> None:
    """The DSN argument is accepted for API compatibility but ignored at call time."""
    pool = _fake_pool()
    # pool.connect with a completely different DSN must still work
    ctx = pool.connect("postgresql://x:y@other.host:5432/other")
    assert ctx is not None


def test_pool_connect_refuses_autocommit_true() -> None:
    """autocommit=True is unsupported: every V2 repository uses explicit commit/rollback."""
    pool = _fake_pool()
    with pytest.raises(ValueError, match="autocommit=True"):
        pool.connect(DSN, autocommit=True)


# ──────────────────────────────────────────── TLS connect options passthrough ──


def test_pool_passes_tls_options_to_new_connections() -> None:
    """All connect_options from V2DatabaseTarget are forwarded to psycopg_pool as kwargs."""
    seen_kwargs: dict[str, Any] = {}
    pool = _fake_pool(connect_kwargs=TLS_OPTIONS, capture=seen_kwargs)
    # open() triggers the pool to try to connect; check kwargs captured
    assert seen_kwargs.get("sslmode") == "verify-full"
    assert seen_kwargs.get("sslrootcert") == "/tmp/ca.pem"
    assert seen_kwargs.get("connect_timeout") == "10"
    assert seen_kwargs.get("autocommit") is False


def test_pool_without_tls_options_still_sets_autocommit_false() -> None:
    """Even without TLS options (local), autocommit=False is always set."""
    seen_kwargs: dict[str, Any] = {}
    _fake_pool(connect_kwargs={}, capture=seen_kwargs)
    assert seen_kwargs.get("autocommit") is False


# ─────────────────────────────────────────────── remote-target refusals ──


def test_remote_target_refusals_are_unchanged(tmp_path) -> None:
    """Pooling does not weaken the V2DatabaseTarget validation.

    The refusals in validate_remote_target run before the pool is created (at
    settings.v2_database_target() time), so they are unaffected by pooling.
    """
    from pathlib import Path

    ca = tmp_path / "ca.pem"
    ca.write_text("-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n", encoding="ascii")

    with pytest.raises(RemoteTargetRefused, match="transaction mode"):
        validate_remote_target(
            "postgresql://origenlab_api:pw@db.example.test:6543/origenlab",
            expected_host="db.example.test",
            ca_file=str(ca),
        )

    with pytest.raises(RemoteTargetRefused, match="runtime role"):
        validate_remote_target(
            "postgresql://postgres:pw@db.example.test:5432/origenlab",
            expected_host="db.example.test",
            ca_file=str(ca),
        )


# ────────────────────────────────────────────────── session-state safety ──


def test_no_session_set_commands_in_v2_repositories() -> None:
    """All SET commands in V2 repositories are transaction-level or LOCAL — audit.

    This test scans the source tree so that a future addition of a session-level
    SET is caught immediately.  Transaction-scoped and LOCAL sets are safe for
    connection pooling; session-level ones are not.
    """
    import ast
    from pathlib import Path

    src = Path(__file__).parents[1] / "src" / "origenlab_api" / "v2"

    session_level_violations: list[str] = []
    # Allowed patterns: set_config(..., true) or set_config(..., True)
    #   and these SQL patterns which are transaction-scoped:
    #     SET TRANSACTION ...
    #     SET LOCAL ...
    # Refused: bare SET <name> = ... without LOCAL or TRANSACTION
    #   (which would be session-level)
    allowed_sql_patterns = (
        "set transaction",
        "set local",
        "set constraints",  # SET CONSTRAINTS is transaction-scoped (PostgreSQL docs)
        "set_config(",  # PostgreSQL set_config with is_local=true
    )

    import re

    # Match .execute("set ..." ) or .execute('set ...' ) — only SQL passed to execute()
    _EXEC_SET = re.compile(
        r'\.execute\(\s*["\']set\s+(?P<rest>[^"\']+)["\']',
        re.IGNORECASE,
    )
    for path in src.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), start=1):
            m = _EXEC_SET.search(line)
            if not m:
                continue
            rest = m.group("rest").lower()
            if any(rest.startswith(p.removeprefix("set ")) for p in allowed_sql_patterns):
                continue
            session_level_violations.append(f"{path.name}:{i}: {line.strip()[:80]}")

    assert not session_level_violations, (
        "Session-level SET found (use SET LOCAL or SET TRANSACTION for connection pooling):\n"
        + "\n".join(session_level_violations)
    )


# ─────────────────────────────────────────── statement-count: overview ──


class _CountingCursor:
    """Fake psycopg cursor that records every SQL statement executed."""

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.description: list[tuple] = []
        self._result: list = []

    def execute(self, sql: str, params: Any = None) -> None:
        self.statements.append(sql.strip().split()[0].upper())  # first keyword
        # Feed synthetic results for the queries overview() and pipeline() run
        self._feed(sql)

    def fetchone(self) -> tuple | None:
        return self._result[0] if self._result else None

    def fetchall(self) -> list:
        return list(self._result)

    def _feed(self, sql: str) -> None:
        lower = sql.strip().lower()
        # Counts (combined or individual) → return zeros
        if "select count" in lower or "select (" in lower:
            if "crm.organization" in lower and "confirmation" in lower and "group by" in lower:
                self._result = []  # group by returns empty
                self.description = [("confirmation",), ("count",)]
            elif "crm.opportunity" in lower and "stage" in lower and "group by" in lower:
                self._result = []
                self.description = [("stage",), ("count",)]
            elif "filter (where" in lower:
                self._result = [(0, 0)]
                self.description = [("x",), ("y",)]
            elif "pdf_sha256" in lower:
                self._result = []
                self.description = [("pdf_sha256",)]
            elif "evidence.assertion" in lower and "kind" in lower:
                self._result = []
                self.description = [("kind",), ("resolution",), ("count",)]
            else:
                # Combined counts query: return 14 zeros
                self._result = [(0,) * 14]
                self.description = [("c",)] * 14
        else:
            self._result = []
            self.description = []

    def __enter__(self) -> "_CountingCursor":
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def __iter__(self):
        return iter(self._result)


class _CountingConn:
    """Fake psycopg connection backed by a single CountingCursor."""

    def __init__(self) -> None:
        self._cur = _CountingCursor()
        self.rolled_back = False

    def cursor(self) -> _CountingCursor:
        return self._cur

    def rollback(self) -> None:
        self.rolled_back = True

    def commit(self) -> None:
        pass

    @contextmanager
    def pipeline(self):
        yield

    def __enter__(self) -> "_CountingConn":
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    @property
    def statements(self) -> list[str]:
        return self._cur.statements


def _fake_connect_factory(conn: _CountingConn):
    """Returns a connect callable that always gives back the same fake conn."""

    @contextmanager
    def _connect(dsn: str, *, autocommit: bool = False):
        yield conn

    return _connect


def test_overview_statement_count_is_at_most_eight() -> None:
    """After the combined-counts optimisation, overview() runs ≤ 8 SQL statements.

    Before: 14 individual COUNTs + 5 other queries + 2 setup = 21 statements.
    After:  1 combined COUNT + 5 other queries + 2 setup     ≤  8 statements.
    """
    from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository

    conn = _CountingConn()
    repo = CrmWorkspaceRepository(connect=_fake_connect_factory(conn), dsn="unused")
    repo.overview()
    # Allow a small buffer for pipeline-grouped setup counts
    assert len(conn.statements) <= 8, (
        f"overview() used {len(conn.statements)} statements; expected ≤ 8. "
        f"Statements: {conn.statements}"
    )


def test_overview_output_shape_is_unchanged() -> None:
    """The combined-counts optimisation must not change the response shape."""
    from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository, _COUNT_SQL

    conn = _CountingConn()
    repo = CrmWorkspaceRepository(connect=_fake_connect_factory(conn), dsn="unused")
    result = repo.overview()
    assert "entities" in result
    assert "opportunities_by_stage" in result
    assert "drive_archive" in result
    entity_keys = {e["key"] for e in result["entities"]}
    assert entity_keys == set(_COUNT_SQL), "Every counted entity must appear in entities[]"
    for entity in result["entities"]:
        assert "provenance" in entity
        assert "count" in entity


def test_pipeline_statement_count_is_at_most_ten() -> None:
    """pipeline() runs ≤ 10 SQL statements: 2 setup + 8 data queries.

    With psycopg pipeline mode the 8 data queries are sent in 1 RTT (instead of
    8), but the SQL statement count stays at 10 — the test documents the bound.
    The seventh is «último contacto» (`_SQL_PIPELINE_CONTACT`), added to the same batch;
    the eighth is the open tasks (`_SQL_PIPELINE_TASKS`, «En pausa hasta…»).
    """
    from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository

    conn = _CountingConn()
    repo = CrmWorkspaceRepository(connect=_fake_connect_factory(conn), dsn="unused")
    result = repo.pipeline()
    assert len(conn.statements) <= 10, (
        f"pipeline() used {len(conn.statements)} statements; expected ≤ 10. "
        f"Statements: {conn.statements}"
    )
    assert "items" in result and "total" in result


def test_operator_session_statement_count_is_at_most_three() -> None:
    """operator_session() runs at most 3 SQL statements: setup (≤2) + 1 query."""
    from origenlab_api.v2.auth_session_store import AuthSessionStore

    conn = _CountingConn()
    conn._cur._result = [None]  # no session found

    store = AuthSessionStore(connect=_fake_connect_factory(conn), dsn="unused")
    result = store.operator_session(b"fake-hash")
    assert result is None
    assert len(conn.statements) <= 3, (
        f"operator_session() used {len(conn.statements)} statements; expected ≤ 3. "
        f"Statements: {conn.statements}"
    )


# ─────────────────────────────────── helpers ──────────────────────────────────


def _fake_pool(
    connect_kwargs: dict[str, Any] | None = None,
    capture: dict[str, Any] | None = None,
) -> "V2ConnectionPool":
    """Build a V2ConnectionPool backed by a mock psycopg_pool.ConnectionPool."""
    with patch("origenlab_api.v2.connection_pool.psycopg_pool") as mock_mod:
        mock_cp = MagicMock()
        mock_mod.ConnectionPool.return_value = mock_cp
        mock_mod.ConnectionPool.check_connection = MagicMock()

        if capture is not None:
            # Capture the kwargs passed to ConnectionPool constructor
            def _capture_kwargs(**kwargs):
                capture.update(kwargs)
                return mock_cp

            mock_mod.ConnectionPool.side_effect = lambda conninfo, **kwargs: (
                capture.update(kwargs) or mock_cp
            )

        pool = V2ConnectionPool(
            DSN,
            connect_kwargs=connect_kwargs or {},
        )
        if capture is not None:
            # Retrieve the actual kwargs from the call
            call_kwargs = mock_mod.ConnectionPool.call_args
            if call_kwargs is not None:
                capture.update(call_kwargs.kwargs.get("kwargs", {}))
        return pool


def _fake_pool_with_conn(conn: Any) -> "V2ConnectionPool":
    """Build a pool whose .connect() context manager yields the given connection."""

    class _FakePool(V2ConnectionPool):
        def connect(self, dsn: str, *, autocommit: bool = False) -> Any:
            if autocommit:
                raise ValueError("V2ConnectionPool.connect: autocommit=True is not supported")
            return _cm(conn)

    @contextmanager
    def _cm(c):
        try:
            yield c
        finally:
            if getattr(c, "_transaction_open", False):
                c.rollback()

    with patch("origenlab_api.v2.connection_pool.psycopg_pool"):
        pool = object.__new__(_FakePool)
        return pool


class _FakePooledConn:
    def __init__(self, on_rollback=None) -> None:
        self._transaction_open = False
        self._on_rollback = on_rollback

    def rollback(self) -> None:
        if self._on_rollback:
            self._on_rollback()
        self._transaction_open = False


# ── real database: the pool works even when no lifespan opened it ────────────

_API_DSN = __import__("os").environ.get("ORIGENLAB_V2_API_TEST_DSN", "")


@pytest.mark.skipif(not _API_DSN, reason="ORIGENLAB_V2_API_TEST_DSN names a disposable V2 database")
def test_connect_opens_the_pool_on_first_use_without_a_lifespan() -> None:
    """A TestClient without `with`, a script, a worker: nothing runs the lifespan, and the
    first request must still get a connection instead of `PoolClosed: not open yet`."""
    pool = V2ConnectionPool(_API_DSN)
    try:
        with pool.connect(_API_DSN) as conn:
            assert conn.execute("select 1").fetchone() == (1,)
            conn.rollback()
        pool.open()  # the lifespan arriving later is harmless
        with pool.connect(_API_DSN) as conn:
            assert conn.execute("select 2").fetchone() == (2,)
            conn.rollback()
    finally:
        pool.close()


@pytest.mark.skipif(not _API_DSN, reason="ORIGENLAB_V2_API_TEST_DSN names a disposable V2 database")
def test_pipeline_mode_reads_return_every_result_on_a_real_server() -> None:
    """The hot reads send several statements per round trip; prove the results come back."""
    pool = V2ConnectionPool(_API_DSN)
    try:
        with pool.connect(_API_DSN) as conn:
            a, b = conn.cursor(), conn.cursor()
            with conn.pipeline():
                a.execute("set transaction read only")
                a.execute("set local statement_timeout = 5000")
                b.execute("select current_setting('statement_timeout'), current_setting('transaction_read_only')")
            assert b.fetchone() == ("5s", "on")
            conn.rollback()
        with pool.connect(_API_DSN) as conn:
            # Transaction-local settings did not survive into the next checkout.
            assert conn.execute("select current_setting('transaction_read_only')").fetchone() == ("off",)
            conn.rollback()
    finally:
        pool.close()
