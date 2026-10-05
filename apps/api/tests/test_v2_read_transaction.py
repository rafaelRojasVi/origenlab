"""`read_transaction.py`: one read-only transaction in as few round trips as the work allows.

The guarantees the old setup made — read only, proven by the server; a transaction-local
statement timeout; nothing left behind on the connection — are proven here against a real
server, on the API's own login, because pipelining changes when they are sent. The round-trip
counts themselves are measured on the wire by `test_v2_roundtrip_budget.py`.
"""

from __future__ import annotations

import os

import pytest

from origenlab_api.v2.read_transaction import (
    ReadTransactionEnded,
    database_name,
    read_transaction,
    run_together,
)

_API_DSN = os.environ.get("ORIGENLAB_V2_API_TEST_DSN", "").strip()
needs_api_db = pytest.mark.skipif(not _API_DSN, reason="ORIGENLAB_V2_API_TEST_DSN names a disposable V2 database")


def _connect():
    import psycopg

    return psycopg.connect


@needs_api_db
def test_a_batch_runs_inside_one_read_only_transaction_with_its_timeout() -> None:
    with read_transaction(_connect(), _API_DSN, 4321) as session:
        settings, first, second = session.batch([
            ("select current_setting('transaction_read_only'), current_setting('statement_timeout')", None),
            ("select now()", None),
            ("select now()", None),
        ])
        assert settings.fetchone() == ("on", "4321ms")
        # One transaction: now() is the transaction's start, the same for both statements.
        assert first.fetchone() == second.fetchone()


@needs_api_db
def test_a_write_is_refused_by_the_server_in_a_batch_and_one_at_a_time() -> None:
    import psycopg

    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        with read_transaction(_connect(), _API_DSN, 5000) as session:
            session.batch([("select 1", None), ("create temp table rtt_probe (i int)", None)])
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        with read_transaction(_connect(), _API_DSN, 5000) as session:
            with session.cursor() as cur:
                cur.execute("select 1")
                cur.execute("create temp table rtt_probe (i int)")


@needs_api_db
def test_statements_one_at_a_time_share_the_transaction_the_first_one_opened() -> None:
    with read_transaction(_connect(), _API_DSN, 2500) as session:
        with session.cursor() as cur:
            cur.execute("select now(), current_setting('statement_timeout')")
            started, timeout = cur.fetchone()
            cur.execute("select now(), current_setting('transaction_read_only')")
            assert cur.fetchone() == (started, "on")
            assert timeout == "2500ms"


@needs_api_db
def test_the_connection_leaves_as_it_came_whatever_happened() -> None:
    import psycopg

    conn = psycopg.connect(_API_DSN)
    try:
        def same(dsn, autocommit=False):  # noqa: ANN001, ANN202 - the repositories' connect shape
            from contextlib import nullcontext

            return nullcontext(conn)

        with read_transaction(same, _API_DSN, 5000) as session:
            session.final("select 1")
        assert (conn.autocommit, conn.info.transaction_status) == (False, psycopg.pq.TransactionStatus.IDLE)

        with pytest.raises(psycopg.errors.DivisionByZero):
            with read_transaction(same, _API_DSN, 5000) as session:
                session.batch([("select 1/0", None), ("select 1", None)])
        assert (conn.autocommit, conn.info.transaction_status) == (False, psycopg.pq.TransactionStatus.IDLE)

        with pytest.raises(RuntimeError):
            with read_transaction(same, _API_DSN, 5000) as session:
                with session.cursor() as cur:
                    cur.execute("select 1")
                raise RuntimeError("the caller failed after a statement")
        assert (conn.autocommit, conn.info.transaction_status) == (False, psycopg.pq.TransactionStatus.IDLE)
        # Nothing transaction-local survived into the next use of the connection.
        assert conn.execute("select current_setting('transaction_read_only')").fetchone() == ("off",)
        conn.rollback()
    finally:
        conn.close()


@needs_api_db
def test_nothing_runs_after_the_final_batch() -> None:
    with read_transaction(_connect(), _API_DSN, 5000) as session:
        session.final("select 1")
        with pytest.raises(ReadTransactionEnded):
            session.batch([("select 2", None)])
        with pytest.raises(ReadTransactionEnded):
            session.cursor().execute("select 3")


@needs_api_db
def test_run_together_pipelines_inside_a_plain_read_write_transaction() -> None:
    import psycopg

    with psycopg.connect(_API_DSN) as conn, conn.cursor() as cur:
        cur.execute("select now()")
        (started,) = cur.fetchone()
        a, b = run_together(cur, [("select now()", None), ("select %s::int + 1", (41,))])
        assert a.fetchone() == (started,) and b.fetchone() == (42,)
        assert database_name(cur) == conn.info.dbname
        conn.rollback()


@needs_api_db
def test_the_database_label_is_what_the_server_calls_it() -> None:
    with read_transaction(_connect(), _API_DSN, 5000) as session:
        (server_says,) = session.final("select current_database()").fetchone()
        assert session.database == server_says
