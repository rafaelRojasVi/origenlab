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


def test_pool_never_shrinks_and_never_health_checks() -> None:
    """min_size == max_size (no idle shrink to reopen through TLS), and no `check` round trip."""
    with patch("origenlab_api.v2.connection_pool.psycopg_pool") as mock_mod:
        V2ConnectionPool(DSN, size=6)
        kwargs = mock_mod.ConnectionPool.call_args.kwargs
    assert kwargs["min_size"] == kwargs["max_size"] == 6
    assert "check" not in kwargs
    assert "max_idle" not in kwargs  # psycopg_pool only ever idles connections above min_size


def test_pool_size_defaults_to_four_and_refuses_zero() -> None:
    with patch("origenlab_api.v2.connection_pool.psycopg_pool") as mock_mod:
        pool = V2ConnectionPool(DSN)
        assert mock_mod.ConnectionPool.call_args.kwargs["min_size"] == 4
    assert pool.size == 4
    with pytest.raises(ValueError, match="at least 1"):
        V2ConnectionPool(DSN, size=0)


def test_pool_connections_never_prepare_and_keep_alive() -> None:
    """Auto-prepare costs a round trip, and every later rollback a DEALLOCATE ALL."""
    pool = _fake_pool(connect_kwargs=TLS_OPTIONS)
    options = pool.connect_options
    assert options["prepare_threshold"] is None
    assert options["keepalives"] == 1 and options["keepalives_idle"] == 60
    assert options["sslmode"] == "verify-full"  # the target's own options still win


def test_pool_size_setting_reads_the_environment(monkeypatch) -> None:
    from origenlab_api.settings import Settings

    monkeypatch.setenv("ORIGENLAB_V2_POOL_SIZE", "7")
    assert Settings(_env_file=None).v2_pool_size == 7
    for refused in ("0", "9"):
        monkeypatch.setenv("ORIGENLAB_V2_POOL_SIZE", refused)
        with pytest.raises(ValueError):
            Settings(_env_file=None)


# ───────────────────────────────────── first-round-trip retry (fake connections) ──


class _FakePipeline:
    def __init__(self, conn: "_RetryConn") -> None:
        self.conn = conn

    def sync(self) -> None:
        self.conn._flush()


class _FakePipelineManager:
    def __init__(self, conn: "_RetryConn") -> None:
        self.conn = conn

    def __enter__(self) -> _FakePipeline:
        self.conn.in_pipeline += 1
        return _FakePipeline(self.conn)

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.conn.in_pipeline -= 1
        if exc_type is None and not self.conn.in_pipeline:
            self.conn._flush()
        return False


class _RetryCursor:
    def __init__(self, conn: "_RetryConn") -> None:
        self.conn = conn
        self.rows: list[tuple] = []

    def execute(self, query: str, params: Any = None) -> "_RetryCursor":
        self.conn.statements.append(query)
        if self.conn.in_pipeline:
            self.conn.queued.append((self, query))
            if self.conn.fail_mid_pipeline:
                self.conn.broken = True
                raise __import__("psycopg").OperationalError("socket gone mid-body")
        else:
            self.conn._send([(self, query)])
        return self

    def fetchone(self) -> tuple | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[tuple]:
        return list(self.rows)

    def close(self) -> None:
        pass


class _FakePgconn:
    """The libpq side of a fake connection, on a real socket pair: the test plays the server.

    `server_says(b"...")` makes the idle socket readable; `server_closes()` sends the FIN.
    `consume_input` reads what is there; a FATAL notice reaches the connection's notice
    handlers, as libpq delivers an ErrorResponse that arrives while idle; EOF raises.
    """

    def __init__(self, owner: "_RetryConn") -> None:
        import socket

        import psycopg

        self._owner = owner
        self._client, self._server = socket.socketpair()
        self.status = psycopg.pq.ConnStatus.OK

    @property
    def socket(self) -> int:
        return self._client.fileno()

    def server_says(self, data: bytes) -> None:
        self._server.sendall(data)

    def server_closes(self) -> None:
        self._server.close()

    def consume_input(self) -> None:
        import psycopg

        data = self._client.recv(65536)
        if not data:
            self.status = psycopg.pq.ConnStatus.BAD
            raise psycopg.OperationalError("consuming input failed: server closed the connection unexpectedly")
        if b"FATAL" in data:
            diagnostic = MagicMock(severity="FATAL", severity_nonlocalized="FATAL", sqlstate="57P01")
            for handler in list(self._owner.notice_handlers):
                handler(diagnostic)


class _RetryConn:
    """A connection that is dead on arrival (`dead`), or answers each query with its name.

    `dead` is a silent drop: the socket shows nothing, the first request fails. The probe
    cases use `pgconn.server_says` / `pgconn.server_closes` instead."""

    def __init__(self, name: str, *, dead: bool = False, error: Exception | None = None) -> None:
        import psycopg

        self.notice_handlers: list[Any] = []
        self.pgconn = _FakePgconn(self)
        self.name = name
        self.dead = dead
        self.error = error
        self.closed = False
        self.broken = False
        self.autocommit = False
        self.in_pipeline = 0
        self.fail_mid_pipeline = False
        self.queued: list[tuple[_RetryCursor, str]] = []
        self.statements: list[str] = []
        self.round_trips = 0
        self.commits = 0
        self.info = MagicMock(transaction_status=psycopg.pq.TransactionStatus.IDLE)

    def cursor(self, *args: Any, **kwargs: Any) -> _RetryCursor:
        return _RetryCursor(self)

    def pipeline(self) -> _FakePipelineManager:
        return _FakePipelineManager(self)

    def _flush(self) -> None:
        batch, self.queued = self.queued, []
        self._send(batch)

    def _send(self, batch: list[tuple[_RetryCursor, str]]) -> None:
        import psycopg

        self.round_trips += 1
        if self.dead:
            self.broken = True
            raise psycopg.OperationalError("server closed the connection unexpectedly")
        if self.error is not None:
            raise self.error
        for cursor, query in batch:
            cursor.rows = [(self.name, query)]

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    def add_notice_handler(self, handler: Any) -> None:
        self.notice_handlers.append(handler)

    def remove_notice_handler(self, handler: Any) -> None:
        self.notice_handlers.remove(handler)


class _FakeInnerPool:
    def __init__(self, conns: list[_RetryConn]) -> None:
        self.ready = list(conns)
        self.returned: list[_RetryConn] = []

    def getconn(self) -> _RetryConn:
        return self.ready.pop(0)

    def putconn(self, conn: _RetryConn) -> None:
        self.returned.append(conn)

    def open(self, wait: bool = False) -> None:
        pass


def _retry_pool(*conns: _RetryConn) -> tuple[V2ConnectionPool, _FakeInnerPool]:
    inner = _FakeInnerPool(list(conns))
    with patch("origenlab_api.v2.connection_pool.psycopg_pool") as mock_mod:
        mock_mod.ConnectionPool.return_value = inner
        pool = V2ConnectionPool(DSN, size=2)
    return pool, inner


def test_a_connection_dead_on_its_first_statement_is_replaced_and_the_statement_replayed() -> None:
    dead, fresh = _RetryConn("dead", dead=True), _RetryConn("fresh")
    pool, inner = _retry_pool(dead, fresh)
    with pool.connect(DSN) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("select 1")
            assert cur.fetchone() == ("fresh", "select 1")
        conn.autocommit = False
    assert dead.closed and inner.returned[0] is dead  # discarded, the pool replaces it
    assert inner.returned[-1] is fresh and fresh.autocommit is False
    assert fresh.statements == ["select 1"]


def test_a_dead_pipeline_is_replayed_whole_on_a_fresh_connection() -> None:
    dead, fresh = _RetryConn("dead", dead=True), _RetryConn("fresh")
    pool, inner = _retry_pool(dead, fresh)
    with pool.connect(DSN) as conn:
        conn.autocommit = True
        setup, a, b = conn.cursor(), conn.cursor(), conn.cursor()
        with conn.pipeline():
            setup.execute("begin read only")
            a.execute("select a")
            b.execute("select b")
            setup.execute("rollback")
        assert (a.fetchone(), b.fetchone()) == (("fresh", "select a"), ("fresh", "select b"))
    assert fresh.statements == ["begin read only", "select a", "select b", "rollback"]
    assert fresh.round_trips == 1, "the replayed pipeline is still one round trip"
    assert fresh.autocommit is False, "returned to the pool as the pool made it"


def test_the_retry_happens_once_only() -> None:
    pool, inner = _retry_pool(_RetryConn("dead", dead=True), _RetryConn("also-dead", dead=True))
    with pytest.raises(__import__("psycopg").OperationalError):
        with pool.connect(DSN) as conn:
            conn.cursor().execute("select 1")
    assert [c.name for c in inner.returned] == ["dead", "also-dead"]


def test_an_error_on_a_live_connection_is_never_retried() -> None:
    import psycopg

    timeout = psycopg.errors.QueryCanceled("canceling statement due to statement timeout")
    live, spare = _RetryConn("live", error=timeout), _RetryConn("spare")
    pool, inner = _retry_pool(live, spare)
    with pytest.raises(psycopg.errors.QueryCanceled):
        with pool.connect(DSN) as conn:
            conn.cursor().execute("select pg_sleep(60)")
    assert inner.ready == [spare] and spare.statements == []


def test_only_the_first_round_trip_is_retried() -> None:
    import psycopg

    conn_a, spare = _RetryConn("a"), _RetryConn("spare")
    pool, inner = _retry_pool(conn_a, spare)
    with pytest.raises(psycopg.OperationalError):
        with pool.connect(DSN) as conn:
            cur = conn.cursor()
            cur.execute("insert one")
            conn_a.dead = True  # dies between two statements of the same transaction
            cur.execute("insert two")
    assert inner.ready == [spare], "a transaction that already did work is never replayed"


def test_a_failure_in_the_middle_of_a_pipeline_body_is_not_replayed() -> None:
    import psycopg

    dead, spare = _RetryConn("dead"), _RetryConn("spare")
    dead.fail_mid_pipeline = True
    pool, inner = _retry_pool(dead, spare)
    with pytest.raises(psycopg.OperationalError):
        with pool.connect(DSN) as conn:
            with conn.pipeline():
                conn.cursor().execute("select a")
                conn.cursor().execute("select b")  # never reached: the body cannot be resumed
    assert inner.ready == [spare]


def test_a_connection_known_dead_at_checkout_is_swapped_before_use() -> None:
    closed, fresh = _RetryConn("closed"), _RetryConn("fresh")
    closed.closed = True
    pool, inner = _retry_pool(closed, fresh)
    with pool.connect(DSN) as conn:
        assert conn.cursor().execute("select 1").fetchone() == ("fresh", "select 1")
    assert inner.returned == [closed, fresh] and closed.statements == []


def test_the_checkout_probe_discards_a_connection_the_server_closed_while_idle() -> None:
    """No round trip: the FIN on the idle socket is enough, and nothing is sent on it."""
    closed, fresh = _RetryConn("closed"), _RetryConn("fresh")
    closed.pgconn.server_says(b"E...FATAL: terminating connection due to administrator command")
    closed.pgconn.server_closes()
    pool, inner = _retry_pool(closed, fresh)
    with pool.connect(DSN) as conn:
        assert conn.cursor().execute("select 1").fetchone() == ("fresh", "select 1")
    assert closed.closed and inner.returned == [closed, fresh]
    assert closed.statements == [] and closed.round_trips == 0


def test_the_checkout_probe_trusts_a_fatal_message_before_the_close_arrives() -> None:
    """The FATAL can land a moment before the FIN; it alone condemns the connection."""
    dying, fresh = _RetryConn("dying"), _RetryConn("fresh")
    dying.pgconn.server_says(b"E...FATAL: terminating connection due to idle-session timeout")
    pool, inner = _retry_pool(dying, fresh)
    with pool.connect(DSN) as conn:
        assert conn.cursor().execute("select 1").fetchone() == ("fresh", "select 1")
    assert inner.returned[0] is dying and dying.statements == []
    assert dying.notice_handlers == [], "the probe leaves no handler behind"


def test_the_checkout_probe_keeps_a_connection_with_a_harmless_message() -> None:
    chatty, spare = _RetryConn("chatty"), _RetryConn("spare")
    chatty.pgconn.server_says(b"S...application_name")
    pool, inner = _retry_pool(chatty, spare)
    with pool.connect(DSN) as conn:
        assert conn.cursor().execute("select 1").fetchone() == ("chatty", "select 1")
    assert inner.ready == [spare]


def test_the_checkout_probe_gives_up_after_size_plus_one_candidates() -> None:
    """After an outage every idle connection may be dead: the probe tries `size + 1`, hands the
    last out unprobed, and the first-round-trip retry is the backstop."""
    candidates = [_RetryConn(f"dead-{i}", dead=True) for i in range(3)]
    for c in candidates:
        c.pgconn.server_closes()
    fresh = _RetryConn("fresh")
    pool, inner = _retry_pool(*candidates, fresh)  # size 2: three candidates at most
    with pool.connect(DSN) as conn:
        assert conn.cursor().execute("select 1").fetchone() == ("fresh", "select 1")
    assert [c.name for c in inner.returned] == ["dead-0", "dead-1", "dead-2", "fresh"]
    assert [c.round_trips for c in candidates] == [0, 0, 1], "only the unprobed one was tried"


def test_a_connection_handed_out_in_autocommit_is_reset_without_a_round_trip() -> None:
    left_on = _RetryConn("left-on")
    left_on.autocommit = True
    pool, inner = _retry_pool(left_on)
    with pool.connect(DSN) as conn:
        assert conn.autocommit is False
        assert left_on.round_trips == 0


def test_a_failed_replacement_returns_nothing_twice() -> None:
    """The fake twin of the real-server test: the dead connection goes back once, only once."""
    import psycopg_pool

    dead = _RetryConn("dead", dead=True)
    pool, inner = _retry_pool(dead)  # nothing to replace it with

    def outage() -> Any:
        raise psycopg_pool.PoolTimeout("couldn't get a connection")

    with pytest.raises(psycopg_pool.PoolTimeout):
        with pool.connect(DSN) as conn:
            inner.getconn = outage  # type: ignore[method-assign]
            conn.cursor().execute("select 1")
    assert inner.returned == [dead]


def test_reaching_the_raw_connection_through_a_cursor_ends_the_retry() -> None:
    """`run_together` sends work on `cursor.connection`, which the journal never sees."""
    import psycopg

    dead, spare = _RetryConn("dead", dead=True), _RetryConn("spare")
    pool, inner = _retry_pool(dead, spare)
    with pytest.raises(psycopg.OperationalError):
        with pool.connect(DSN) as conn:
            cur = conn.cursor()
            cur._real.connection = dead  # the fake cursor's raw connection
            assert cur.connection is dead
            cur.execute("select 1")
    assert inner.ready == [spare], "nothing replayed"


def test_the_block_commits_on_success_and_resets_autocommit_left_on() -> None:
    conn_a = _RetryConn("a")
    pool, inner = _retry_pool(conn_a)
    with pool.connect(DSN) as conn:
        conn.autocommit = True
        conn.cursor().execute("select 1")
    assert conn_a.commits == 1 and conn_a.autocommit is False and inner.returned == [conn_a]


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


# ─────────────────────────────────────────── round trips: hot reads (fakes) ──
#
# Fast guards without a database; `test_v2_roundtrip_budget.py` measures the same on the wire.


class _CountingCursor:
    """Fake psycopg cursor: records statements on its connection and answers with zeros."""

    def __init__(self, conn: "_CountingConn") -> None:
        self._conn = conn
        self.description: list[tuple] = []
        self._result: list = []

    def execute(self, sql: str, params: Any = None) -> "_CountingCursor":
        self._conn.statements.append(sql.strip().split()[0].upper())  # first keyword
        if not self._conn.in_pipeline:
            self._conn.round_trips += 1
        self._feed(sql)
        return self

    def fetchone(self) -> tuple | None:
        return self._result[0] if self._result else None

    def fetchall(self) -> list:
        return list(self._result)

    def close(self) -> None:
        pass

    def _feed(self, sql: str) -> None:
        lower = sql.strip().lower()
        if self._conn.preset is not None:
            self._result = list(self._conn.preset)
            self.description = [("c",)]
        elif "select count" in lower or "select (" in lower:
            if "group by" in lower:
                self._result = []
                self.description = [("k",), ("count",)]
            elif "filter (where" in lower:
                self._result = [(0, 0)]
                self.description = [("x",), ("y",)]
            else:
                # Combined counts query: return 14 zeros
                self._result = [(0,) * 14]
                self.description = [("c",)] * 14
        elif lower.startswith("select"):
            self._result = []
            self.description = [("c",)]
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
    """Fake psycopg connection: a statement outside a pipeline, or a pipeline exit, is a round trip."""

    def __init__(self, preset: list | None = None) -> None:
        import psycopg

        self.statements: list[str] = []
        self.round_trips = 0
        self.in_pipeline = 0
        self.closed = False
        self.autocommit = False
        self.preset = preset
        self.info = MagicMock(transaction_status=psycopg.pq.TransactionStatus.IDLE, dbname="fake")

    def cursor(self) -> _CountingCursor:
        return _CountingCursor(self)

    def rollback(self) -> None:
        self.round_trips += 1

    def commit(self) -> None:
        pass

    @contextmanager
    def pipeline(self):
        self.in_pipeline += 1
        try:
            yield
        finally:
            self.in_pipeline -= 1
            if not self.in_pipeline:
                self.round_trips += 1

    def __enter__(self) -> "_CountingConn":
        return self

    def __exit__(self, *args: Any) -> None:
        pass


def _fake_connect_factory(conn: _CountingConn):
    """Returns a connect callable that always gives back the same fake conn."""

    @contextmanager
    def _connect(dsn: str, *, autocommit: bool = False):
        yield conn

    return _connect


def test_overview_is_one_round_trip() -> None:
    """Setup, the 14 entity counts, five more reads and the rollback: one pipeline."""
    from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository

    conn = _CountingConn()
    repo = CrmWorkspaceRepository(connect=_fake_connect_factory(conn), dsn="unused")
    repo.overview()
    assert conn.round_trips == 1, conn.statements
    assert conn.statements[:2] == ["BEGIN", "SET"] and conn.statements[-1] == "ROLLBACK"
    assert conn.autocommit is False, "the connection goes back as it came"


def test_overview_output_shape_is_unchanged() -> None:
    """The batched overview keeps the response shape."""
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


def test_pipeline_is_one_round_trip() -> None:
    from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository

    conn = _CountingConn()
    repo = CrmWorkspaceRepository(connect=_fake_connect_factory(conn), dsn="unused")
    result = repo.pipeline()
    assert conn.round_trips == 1, conn.statements
    assert "items" in result and "total" in result


def test_operator_session_is_one_round_trip() -> None:
    """The per-request session read: setup, the one statement and the rollback together."""
    from origenlab_api.v2.auth_session_store import AuthSessionStore

    conn = _CountingConn(preset=[])  # no session found
    store = AuthSessionStore(connect=_fake_connect_factory(conn), dsn="unused")
    assert store.operator_session(b"fake-hash") is None
    assert conn.round_trips == 1, conn.statements
    assert conn.statements == ["BEGIN", "SET", "SELECT", "ROLLBACK"]


def test_profile_binding_is_one_round_trip() -> None:
    from origenlab_api.v2.profile_auth import ProfileAuthRepository

    conn = _CountingConn(preset=[])
    repo = ProfileAuthRepository(connect=_fake_connect_factory(conn), dsn="unused")
    assert repo.binding("00000000-0000-4000-8000-000000000001", None, b"h") == (None, None, None)
    assert conn.round_trips == 1, conn.statements


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


_MAINTENANCE_DSN = __import__("os").environ.get("ORIGENLAB_V2_TEST_DSN", "")
needs_both_dsns = pytest.mark.skipif(
    not (_API_DSN and _MAINTENANCE_DSN), reason="ORIGENLAB_V2_TEST_DSN and ORIGENLAB_V2_API_TEST_DSN are both required"
)


def _end_the_idle_backend(pool: V2ConnectionPool) -> int:
    """End the pool's one idle connection from the server side, as a pooler or an idle timeout
    would, and wait until the backend is gone. Returns its pid."""
    import time

    import psycopg

    with pool.connect(_API_DSN) as conn:
        pid = conn.execute("select pg_backend_pid()").fetchone()[0]
        conn.rollback()
    with psycopg.connect(_MAINTENANCE_DSN, autocommit=True) as admin:
        assert admin.execute("select pg_terminate_backend(%s)", (pid,)).fetchone() == (True,)
        for _ in range(100):
            if admin.execute("select count(*) from pg_stat_activity where pid = %s", (pid,)).fetchone() == (0,):
                break
            time.sleep(0.05)
        else:  # pragma: no cover - the server did not end it
            raise AssertionError("the backend did not exit")
    time.sleep(0.05)  # the FIN crosses the loopback
    return pid


def _read_pid_plain(pool: V2ConnectionPool) -> int:
    with pool.connect(_API_DSN) as conn:
        pid = conn.execute("select pg_backend_pid()").fetchone()[0]
        conn.rollback()
    return pid


def _read_pid_final(pool: V2ConnectionPool) -> int:
    from origenlab_api.v2.read_transaction import read_transaction

    with read_transaction(pool.connect, _API_DSN, 5000) as session:
        return session.final("select pg_backend_pid()").fetchone()[0]


def _read_pid_batch(pool: V2ConnectionPool) -> int:
    from origenlab_api.v2.read_transaction import read_transaction

    with read_transaction(pool.connect, _API_DSN, 5000) as session:
        pid, one = session.batch([("select pg_backend_pid()", None), ("select 1", None)])
        assert one.fetchone() == (1,)
        return pid.fetchone()[0]


def _read_pid_cursor(pool: V2ConnectionPool) -> int:
    from origenlab_api.v2.read_transaction import read_transaction

    with read_transaction(pool.connect, _API_DSN, 5000) as session:
        with session.cursor() as cur:
            cur.execute("select pg_backend_pid()")
            return cur.fetchone()[0]


@needs_both_dsns
@pytest.mark.parametrize("read", [_read_pid_plain, _read_pid_final, _read_pid_batch, _read_pid_cursor],
                         ids=["plain-execute", "read-final", "read-batch", "read-cursor"])
def test_a_backend_ended_while_idle_never_reaches_the_caller(read) -> None:
    """The case the health check used to cover: the server ends an idle pooled connection. A
    read transaction queues its first statement in pipeline mode, where psycopg consumes the
    waiting FIN at that statement — inside the body, beyond the exit retry — so the checkout's
    zero-round-trip probe must have discarded the connection already."""
    pool = V2ConnectionPool(_API_DSN, size=1)
    try:
        pool.wait()
        before = _end_the_idle_backend(pool)
        assert read(pool) != before
    finally:
        pool.close()


@needs_both_dsns
def test_the_session_check_survives_a_backend_ended_while_idle() -> None:
    """Every request starts with the session check; it must not be the request that finds out."""
    from origenlab_api.v2.auth_session_store import AuthSessionStore
    from origenlab_api.v2.profile_auth import ProfileAuthRepository

    pool = V2ConnectionPool(_API_DSN, size=1)
    try:
        pool.wait()
        _end_the_idle_backend(pool)
        assert ProfileAuthRepository(pool.connect, _API_DSN).binding(
            "00000000-0000-4000-8000-000000000001", None, b"no-such-session") == (None, None, None)
        _end_the_idle_backend(pool)
        assert AuthSessionStore(pool.connect, _API_DSN).operator_session(b"no-such-session") is None
    finally:
        pool.close()


@needs_both_dsns
def test_a_failed_replacement_never_returns_the_dead_connection_twice(monkeypatch) -> None:
    """The retry returns the dead connection, then asks for a fresh one. If that request fails
    (an outage: PoolTimeout), the checkout must not hand the dead one back a second time — the
    pool would schedule a second replacement and outgrow its size for good."""
    import time

    import psycopg
    import psycopg_pool

    from origenlab_api.v2 import connection_pool as module

    name = "rtt-replace-" + __import__("uuid").uuid4().hex[:8]
    pool = V2ConnectionPool(_API_DSN, size=1, connect_kwargs={"application_name": name})
    try:
        pool.wait()
        # A silent drop: the probe cannot see it, the first round trip fails at its end.
        monkeypatch.setattr(module, "_is_alive", lambda conn: not (conn.closed or conn.broken))
        _end_the_idle_backend(pool)
        real_getconn = pool._getconn
        calls = {"n": 0}

        def getconn_then_outage():  # type: ignore[no-untyped-def]
            calls["n"] += 1
            if calls["n"] > 1:
                raise psycopg_pool.PoolTimeout("couldn't get a connection after 30.00 sec")
            return real_getconn()

        monkeypatch.setattr(pool, "_getconn", getconn_then_outage)
        with pytest.raises(psycopg_pool.PoolTimeout):
            with pool.connect(_API_DSN) as conn:
                conn.execute("select 1")
        monkeypatch.undo()
        pool.wait()
        time.sleep(0.5)  # let any (wrongly) scheduled second replacement land
        stats = pool._pool.get_stats()
        assert stats["pool_size"] <= 1 and stats.get("pool_available", 0) <= 1, stats
        with psycopg.connect(_MAINTENANCE_DSN, autocommit=True) as admin:
            (backends,) = admin.execute(
                "select count(*) from pg_stat_activity where application_name = %s", (name,)).fetchone()
        assert backends <= 1
    finally:
        pool.close()
