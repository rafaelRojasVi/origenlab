"""One read-only transaction in as few database round trips as the work allows.

The hosted API waits ~180 ms for every round trip to its database (`docs/STATUS.md`,
`tests/test_v2_roundtrip_budget.py`), and a read used to spend four of them on ceremony:
psycopg's ``BEGIN`` (in pipeline mode it synchronises right after it), the pipelined
``set transaction read only`` + ``set local statement_timeout``, and the final ``ROLLBACK``,
before and after the statements that do the work, each of which was a round trip of its own.

:func:`read_transaction` keeps every guarantee and drops the ceremony:

* The connection is switched to autocommit for the duration so that psycopg sends no
  ``BEGIN`` of its own; the transaction is opened explicitly with
  ``begin transaction read only``, and ``set local statement_timeout`` follows inside it.
  Both are queued in front of the **first** statement, in one pipeline: the setup costs no
  round trip at all. The server refuses a write with SQLSTATE 25006 exactly as before, and
  the timeout is transaction-local exactly as before.
* :meth:`ReadSession.batch` sends statements that do not depend on each other together, in
  one round trip, and with ``final=True`` puts the ``rollback`` in that same round trip: a
  page whose reads are independent costs **one** round trip, setup and teardown included.
* Statements that do depend on an earlier answer still go one at a time through
  :meth:`ReadSession.cursor`, which behaves like a psycopg cursor.
* The ``rollback`` that ends a transaction no batch closed is one round trip, and the
  connection always leaves in the state it arrived in (``autocommit`` off, idle): if that
  cannot be restored the pool closes and replaces it (`connection_pool.py`).

Results are identical to running the same statements one after the other in the same
read-only transaction: pipelining changes when the client waits, not what the server runs
or in which order.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Sequence

import psycopg

#: A statement and its parameters, as `cursor.execute` takes them.
Statement = tuple[str, Any]

_IDLE = psycopg.pq.TransactionStatus.IDLE


class ReadTransactionEnded(RuntimeError):
    """A statement was sent after the transaction's final batch had already rolled it back."""


class ReadSession:
    """A read-only transaction on one connection, opened lazily with the first statement."""

    def __init__(self, conn: Any, statement_timeout_ms: int) -> None:
        self._conn = conn
        self._setup = (
            "begin transaction read only",
            f"set local statement_timeout = {int(statement_timeout_ms)}",
        )
        self._state = "pending"  # pending → open → closed
        conn.autocommit = True
        self._control = conn.cursor()

    @property
    def connection(self) -> Any:
        return self._conn

    @property
    def database(self) -> str:
        """The database this connection is attached to — what ``current_database()`` answers,
        read from the connection itself (a PostgreSQL session is bound to one database at
        startup) instead of costing a round trip."""
        return str(self._conn.info.dbname)

    def cursor(self) -> "SessionCursor":
        return SessionCursor(self)

    def batch(self, statements: Sequence[Statement], *, final: bool = False) -> list[Any]:
        """Run independent statements in one round trip; return one executed cursor each.

        With ``final=True`` the transaction is rolled back in the same round trip, and any
        later statement raises :class:`ReadTransactionEnded`.
        """
        cursors = [self._conn.cursor() for _ in statements]
        self._send(list(zip(cursors, statements, strict=True)), final=final)
        return cursors

    def final(self, sql: str, params: Any = None) -> Any:
        """The transaction's last statement and its rollback, in one round trip."""
        return self.batch([(sql, params)], final=True)[0]

    def _send(self, work: list[tuple[Any, Statement]], *, final: bool = False) -> None:
        if self._state == "closed":
            raise ReadTransactionEnded("this read transaction has already been rolled back")
        if self._state == "open" and not final and len(work) == 1:
            cursor, (sql, params) = work[0]
            cursor.execute(sql, params)
            return
        with self._conn.pipeline():
            if self._state == "pending":
                for sql in self._setup:
                    self._control.execute(sql)
                self._state = "open"
            for cursor, (sql, params) in work:
                cursor.execute(sql, params)
            if final:
                self._control.execute("rollback")
                self._state = "closed"

    def close(self) -> None:
        """End the transaction if a final batch did not, and hand the connection back as found."""
        conn = self._conn
        self._state = "closed"
        if conn.closed:
            return
        try:
            if conn.info.transaction_status != _IDLE:
                conn.rollback()
        finally:
            conn.autocommit = False


class SessionCursor:
    """A psycopg-like cursor whose statements run inside its :class:`ReadSession`."""

    def __init__(self, session: ReadSession) -> None:
        self.session = session
        self._cursor = session.connection.cursor()

    def execute(self, query: str, params: Any = None) -> "SessionCursor":
        self.session._send([(self._cursor, (query, params))])
        return self

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)

    def __iter__(self) -> Iterator[Any]:
        return iter(self._cursor)

    def __enter__(self) -> "SessionCursor":
        return self

    def __exit__(self, *exc: Any) -> None:
        self._cursor.close()


@contextmanager
def read_transaction(connect: Any, dsn: str, statement_timeout_ms: int) -> Iterator[ReadSession]:
    """Check out a connection and hold one read-only transaction on it for the block."""
    with connect(dsn, autocommit=False) as conn:
        session = ReadSession(conn, statement_timeout_ms)
        try:
            yield session
        finally:
            session.close()


def run_together(cur: Any, statements: Sequence[Statement], *, final: bool = False) -> list[Any]:
    """Run independent statements on `cur`'s transaction in as few round trips as possible.

    Inside a :func:`read_transaction` this is :meth:`ReadSession.batch`. On a plain psycopg
    cursor — a command's read-write transaction — the statements share one pipeline on the
    same connection and transaction, which is never more round trips than running them one by
    one. ``final`` only applies to a read session. Returns one executed cursor per statement.
    """
    session = getattr(cur, "session", None)
    if isinstance(session, ReadSession):
        return session.batch(statements, final=final)
    conn = cur.connection
    cursors = [conn.cursor() for _ in statements]
    with conn.pipeline():
        for cursor, (sql, params) in zip(cursors, statements, strict=True):
            cursor.execute(sql, params)
    return cursors


def database_name(cur: Any) -> str:
    """The database `cur` reads, without a round trip (see :attr:`ReadSession.database`)."""
    session = getattr(cur, "session", None)
    if isinstance(session, ReadSession):
        return session.database
    return str(cur.connection.info.dbname)


def rows(cur: Any) -> list[dict[str, Any]]:
    """An executed cursor's rows as dicts keyed by column name."""
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
