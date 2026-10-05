"""Process-wide psycopg3 connection pool for the V2 database.

Shape-compatible with ``psycopg.connect``: every V2 repository receives a callable
with the same ``connect(dsn, autocommit=False)`` context-manager signature and
never knows it is talking to a pool.

Session-state safety
--------------------
All ``SET`` statements used by V2 repositories are either transaction-level
(``SET TRANSACTION READ ONLY``, ``SET TRANSACTION ISOLATION LEVEL …``) or
transaction-local (``SET LOCAL statement_timeout``).  Both variants are reset
when the transaction ends (commit or rollback).  Like the ``psycopg.connect``
context it replaces, leaving the block commits an open transaction (rolls it back
on an exception), and the pool resets any connection still in a transaction
before reuse, so no session state leaks between requests.  A connection whose
``autocommit`` was switched on inside a block (``read_transaction.py`` does, to
send its own ``begin read only``) is switched back before it is returned; one that
cannot be is closed, and the pool replaces it.

Round trips
-----------
The hosted API is ~180 ms from its database, so what a checkout costs matters
(``tests/test_v2_roundtrip_budget.py``):

* **No health check on checkout.** ``psycopg_pool``'s ``check`` sends an empty
  query before every checkout — one round trip per request. A connection that died
  while idle is caught in two places instead, neither of which costs a round trip:

  1. **At checkout, a zero-round-trip probe** (:func:`_is_alive`). A pooler or a server
     that ends an idle connection says so on the socket (an error message, a FIN or a
     RST), so an idle connection whose socket is readable is suspect: the probe lets
     libpq read and parse what is there and discards the connection on a ``FATAL`` or if
     libpq now calls it closed or bad, then takes another (at most ``size + 1`` attempts). This is what
     keeps a **read** safe: a read queues its first statement in pipeline mode, and
     psycopg consumes the waiting FIN at that statement — inside the caller's pipeline
     body, where nothing can be replayed.
  2. **On the first round trip, a retry** (:class:`_CheckedOutConnection`) for the drop
     the socket cannot show yet — a path silently dropped by the network, whose failure
     only appears once a request has been sent. If that round trip raises
     ``psycopg.OperationalError`` *and* the connection is now broken, the connection is
     discarded and the work of that first round trip — and nothing else — is replayed
     once on a fresh connection. A failure after the first round trip, a body that fails
     inside a pipeline, or anything this wrapper cannot replay is raised as it always was.

  **The replay is safe only because nothing can have been committed** before the first
  round trip completed: :meth:`V2ConnectionPool.connect` refuses ``autocommit=True`` and
  resets any connection handed out in autocommit, so a statement sent on a fresh checkout
  runs inside a transaction psycopg opens (``BEGIN``), or inside the explicit ``begin
  transaction read only`` that ``read_transaction.py`` sends first. A dead connection's
  open transaction dies with it. Anything that would break this — a checkout used in
  autocommit for writes — must not be added.
* **The pool never shrinks.** ``min_size == max_size`` (``ORIGENLAB_V2_POOL_SIZE``,
  default 4), so a quiet period does not close connections that the next burst
  would have to reopen through TCP + TLS + SCRAM (~5 round trips each).
  ``max_idle`` only ever closes connections above ``min_size``, so it cannot apply;
  ``max_lifetime`` still recycles each connection about hourly, in the background.
* **TCP keepalives** keep an idle connection's path alive through NATs and detect a
  dead peer without a request having to find out.
* **No automatic server-side prepares.** psycopg prepares a statement the sixth time
  a connection runs it — one extra round trip — and every later rollback then sends
  ``DEALLOCATE ALL`` (another). The V2 reads end in a rollback and their queries
  take milliseconds, so preparing saves nothing and costs round trips.

Startup safety
--------------
The pool is opened with ``wait=False``: the background maintenance thread
starts without blocking.  If the database is unreachable at boot the pool
starts empty and the first request fails per-request, exactly as it does
today without a pool — after the checkout timeout
(``ORIGENLAB_V2_POOL_CHECKOUT_TIMEOUT_S``, default 10 s), which also bounds the
wait when every connection is busy.
"""
from __future__ import annotations

import logging
import select
import threading
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
import psycopg_pool

logger = logging.getLogger(__name__)

#: Reads the checkout probe makes at most while the idle socket stays readable.
_PROBE_READS = 4

#: The pool size when ``ORIGENLAB_V2_POOL_SIZE`` is not set.
DEFAULT_POOL_SIZE = 4

#: Seconds a checkout waits for a connection when ``ORIGENLAB_V2_POOL_CHECKOUT_TIMEOUT_S`` is not set.
DEFAULT_CHECKOUT_TIMEOUT = 10.0

#: Keepalive probes after 60 s idle, every 15 s, 4 misses: a dead path is known in ~2 min.
_KEEPALIVES: dict[str, Any] = {
    "keepalives": 1,
    "keepalives_idle": 60,
    "keepalives_interval": 15,
    "keepalives_count": 4,
}


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
        size: int = DEFAULT_POOL_SIZE,
        connect_kwargs: dict[str, Any] | None = None,
        max_lifetime: float = 3600.0,
        checkout_timeout: float = DEFAULT_CHECKOUT_TIMEOUT,
    ) -> None:
        if size < 1:
            raise ValueError("V2ConnectionPool: size must be at least 1")
        if checkout_timeout <= 0:
            raise ValueError("V2ConnectionPool: checkout_timeout must be positive")
        kwargs: dict[str, Any] = {**_KEEPALIVES, **dict(connect_kwargs or {})}
        # Repositories always open connections with autocommit=False; enforce it.
        kwargs["autocommit"] = False
        # Never prepare server-side: see the module docstring ("Round trips").
        kwargs["prepare_threshold"] = None
        # Kept separately so connect_options is inspectable without accessing pool internals.
        self._connect_options: dict[str, Any] = dict(kwargs)
        self._size = size
        self._checkout_timeout = checkout_timeout
        self._pool = psycopg_pool.ConnectionPool(
            conninfo=dsn,
            min_size=size,
            max_size=size,
            kwargs=kwargs,
            max_lifetime=max_lifetime,
            timeout=checkout_timeout,  # what every getconn() waits at most, then PoolTimeout
            open=False,  # opened from the app lifespan, or on first use
        )
        self._open_lock = threading.Lock()
        self._opened = False

    @property
    def connect_options(self) -> dict[str, Any]:
        """The connect kwargs this pool passes to every new connection (includes TLS options).

        Exposed so tests and operators can confirm that TLS is wired without
        accessing ``psycopg_pool`` internals.
        """
        return dict(self._connect_options)

    @property
    def size(self) -> int:
        """Connections held open at all times (``min_size == max_size``)."""
        return self._size

    @property
    def checkout_timeout(self) -> float:
        """Seconds a checkout waits for a connection before ``PoolTimeout``."""
        return self._checkout_timeout

    def open(self) -> None:
        """Start pool maintenance without blocking for ``min_size`` connections.

        Called once from the FastAPI lifespan.  If the database is unreachable,
        the background thread retries silently; requests fail normally until
        connectivity is restored.
        """
        with self._open_lock:
            if not self._opened:
                self._pool.open(wait=False)
                self._opened = True

    def wait(self, timeout: float = 30.0) -> None:
        """Block until the pool holds all ``size`` connections (tests and warm-up only)."""
        self.open()
        self._pool.wait(timeout=timeout)

    def close(self) -> None:
        """Drain and shut down the pool.  Called from the FastAPI lifespan."""
        self._pool.close()

    def connect(self, dsn: str, *, autocommit: bool = False) -> Any:
        """Return a context manager that yields a pooled connection.

        Matches the ``psycopg.connect`` call signature:

        * ``dsn`` is accepted for API compatibility and ignored.
        * ``autocommit`` must be ``False`` (the only supported value).

        On exit an open transaction is committed (rolled back on an exception),
        exactly as with ``psycopg.connect``'s context; every repository already
        commits or rolls back explicitly.
        """
        if autocommit:
            raise ValueError(
                "V2ConnectionPool.connect: autocommit=True is not supported; "
                "every V2 repository uses explicit commit/rollback"
            )
        # The lifespan normally opens the pool; anything that builds the app without running it
        # (a TestClient outside `with`, a script) opens it here, on first use. Idempotent.
        if not self._opened:
            self.open()
        return self._checkout()

    # ------------------------------------------------------------------ checkout

    @contextmanager
    def _checkout(self) -> Iterator["_CheckedOutConnection"]:
        held = _CheckedOutConnection(self, self._getconn())
        try:
            try:
                yield held
            except BaseException:
                if not held.released:
                    _finish(held.raw, failed=True)
                raise
            if not held.released:
                _finish(held.raw, failed=False)
        finally:
            # Released: a retry already returned the dead connection and could not get another.
            if not held.released:
                self._putconn(held.raw)

    def _getconn(self) -> Any:
        """A live-looking connection from the pool, in the state the repositories expect.

        Each candidate is probed without a round trip (:func:`_is_alive`); a dead one is
        discarded — the pool opens its replacement in the background — and another taken, at
        most ``size + 1`` times (after an outage every idle connection may be dead). The last
        candidate is handed out unprobed: the first-round-trip retry still covers it.
        """
        conn = self._pool.getconn()
        for _ in range(self._size):
            if _is_alive(conn) and _reset_autocommit(conn):
                return conn
            self._discard(conn)
            conn = self._pool.getconn()
        _reset_autocommit(conn)
        return conn

    def _discard(self, conn: Any) -> None:
        """Return `conn` closed: the pool drops it and opens a replacement in the background."""
        try:
            conn.close()
        finally:
            self._pool.putconn(conn)

    def _putconn(self, conn: Any) -> None:
        # A connection switched to autocommit inside the block goes back as the pool made it,
        # or not at all: closed, the pool discards it and opens a replacement in the background.
        if not conn.closed:
            try:
                if conn.autocommit:
                    if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                        conn.rollback()
                    conn.autocommit = False
            except Exception:  # noqa: BLE001 - a connection we cannot reset is not reused
                logger.warning("V2 pool: closing a connection that could not be reset", exc_info=True)
                conn.close()
        self._pool.putconn(conn)



def _is_dead(conn: Any) -> bool:
    return bool(conn.closed or getattr(conn, "broken", False))


def _is_alive(conn: Any) -> bool:
    """Whether an idle pooled connection still looks usable — decided without a round trip.

    An idle connection has nothing to read. If its socket is readable, the server or the
    pooler sent something unasked: typically a ``FATAL`` error just before closing (idle
    timeout, ``pg_terminate_backend``, a restart) and then the close itself. ``consume_input``
    only reads the socket into libpq's buffer and parses nothing; ``is_busy`` then parses what
    was read — still no round trip — and libpq hands an error that arrived while idle to the
    notice handlers. The close shows as an error on a later read. So the probe reads and parses
    until the socket is quiet (a few reads at most) and calls the connection dead on a
    ``FATAL`` notice, a read error, or a status that is not ``OK``. Anything harmless (a
    notice, a parameter status, a ``NOTIFY`` — still delivered later — or a TLS record) leaves
    it alive.

    The socket is watched with ``select.poll``, never ``select.select``: that refuses any
    descriptor ≥ 1024, and would call every connection dead in a process holding that many.
    """
    if _is_dead(conn):
        return False
    pgconn = conn.pgconn
    fatal: list[str] = []

    def note(diagnostic: Any) -> None:
        if (getattr(diagnostic, "severity_nonlocalized", None) or diagnostic.severity) in ("FATAL", "PANIC"):
            fatal.append(diagnostic.sqlstate or "")

    conn.add_notice_handler(note)
    try:
        poller = select.poll()
        poller.register(pgconn.socket, select.POLLIN)
        for _ in range(_PROBE_READS):
            if not poller.poll(0):  # readable, hung up or in error: libpq's read tells which
                break
            pgconn.consume_input()
            pgconn.is_busy()  # parses what was read: an error sent while idle reaches `note`
    except (OSError, psycopg.OperationalError):
        return False
    finally:
        conn.remove_notice_handler(note)
    return not fatal and pgconn.status == psycopg.pq.ConnStatus.OK and not _is_dead(conn)


def _reset_autocommit(conn: Any) -> bool:
    """Hand a connection out with ``autocommit`` off (no round trip); False if that fails."""
    try:
        if conn.autocommit:
            conn.autocommit = False
    except Exception:  # noqa: BLE001 - such a connection is discarded, never handed out
        return False
    return True


def _finish(conn: Any, *, failed: bool) -> None:
    """What ``psycopg.Connection.__exit__`` does for a pooled connection, on the one we hold."""
    if conn.closed:
        return
    if failed:
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001 - the original exception is the one to raise
            logger.warning("V2 pool: rollback after a failed block also failed", exc_info=True)
    else:
        conn.commit()


# ---------------------------------------------------------------------- first-round-trip retry


class _CheckedOutConnection:
    """The connection a repository holds for one checkout: a psycopg connection that can retry
    its **first** round trip once on a fresh connection.

    Until a round trip succeeds, every operation that shapes the connection or the work is
    journalled: attribute sets (``autocommit``), cursors, statements, pipeline entries and
    exits. A round trip that then fails with ``OperationalError`` on a connection that is now
    closed or broken — a connection that died while idle in the pool — is replayed on a fresh
    one, journal and all, once. The first successful round trip ends the journal; from there
    on this object only delegates.

    The retry happens only where the whole first round trip is known: a statement outside
    pipeline mode, or the exit of the outermost pipeline. A failure raised in the middle of a
    pipeline body, a fetch or a commit inside a pipeline, the raw connection reached through a
    cursor's ``connection``, or any operation this class does not journal ends the journal, so
    nothing is ever replayed partially. A drop that the socket already shows is the checkout
    probe's job (:func:`_is_alive`), not this class's: a read sees it inside its pipeline body.

    Replaying is safe because nothing can have committed before the first round trip
    completed — the module docstring states the invariant (no autocommit checkouts; reads
    begin explicitly) that keeps it so.
    """

    #: Client-side attributes: reading them neither talks to the server nor needs replaying.
    _PASSIVE = frozenset({"info", "closed", "broken", "autocommit", "pgconn", "adapters",
                          "read_only", "isolation_level", "deferrable", "prepare_threshold",
                          "fileno"})

    def __init__(self, pool: V2ConnectionPool, conn: Any) -> None:
        self.__dict__.update(_pool=pool, _conn=conn, _journal=[], _pipelines=0, _released=False)

    # -- plumbing

    @property
    def raw(self) -> Any:
        """The psycopg connection currently held."""
        return self._conn

    @property
    def released(self) -> bool:
        """True once a retry gave the dead connection back to the pool and got no other.

        The dead connection stays held, closed, so that code on the way out (a session's
        ``close``, a command's ``rollback``) meets a closed connection rather than none and the
        error that ended the work surfaces unchanged. The checkout must not finish or return it
        a second time.
        """
        return self._released

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._conn, name)
        if name not in self._PASSIVE and callable(value):
            self._stop_journal()  # an operation we cannot replay: no retry from here on
        return value

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._conn, name, value)
        if self._journal is not None:
            self._journal.append(("set", name, value))

    def _stop_journal(self) -> None:
        self.__dict__["_journal"] = None

    def _round_trip(self, run: Any) -> Any:
        """Run one round trip; on a dead connection during the first, replay it once."""
        if self._journal is None:
            return run()
        try:
            result = run()
        except psycopg.OperationalError:
            if self._journal is None or not _is_dead(self._conn):
                self._stop_journal()
                raise
            result = self._replay()
        self._stop_journal()
        return result

    def _replay(self) -> Any:
        journal = self._journal or []
        self._stop_journal()
        logger.warning("V2 pool: a pooled connection was dead on its first round trip; "
                       "retrying once on a fresh connection")
        # From here the dead connection belongs to the pool again: if no fresh one can be had
        # (an outage), it stays held but released (see `released`), never returned twice.
        self.__dict__["_released"] = True
        self._pool._discard(self._conn)
        self.__dict__["_conn"] = self._pool._getconn()
        self.__dict__["_released"] = False
        stack: list[Any] = []
        result: Any = None
        for entry in journal:
            kind = entry[0]
            if kind == "set":
                setattr(self._conn, entry[1], entry[2])
            elif kind == "cursor":
                entry[1]._bind(self._conn)
            elif kind == "execute":
                cursor, method, args, kwargs = entry[1:]
                result = getattr(cursor._real, method)(*args, **kwargs)
            elif kind == "enter":
                manager = self._conn.pipeline()
                manager.__enter__()
                stack.append(manager)
            elif kind == "exit":
                stack.pop().__exit__(None, None, None)
        return result

    # -- the connection API the repositories use

    def cursor(self, *args: Any, **kwargs: Any) -> "_Cursor":
        cursor = _Cursor(self, args, kwargs)
        if self._journal is not None:
            self._journal.append(("cursor", cursor))
        return cursor

    def execute(self, query: Any, params: Any = None, **kwargs: Any) -> "_Cursor":
        return self.cursor().execute(query, params, **kwargs)

    def pipeline(self) -> "_Pipeline":
        return _Pipeline(self)

    def commit(self) -> None:
        # Outside a pipeline, a commit before any statement has nothing to send; inside one it
        # is a round trip in the middle of the body. Neither is replayed.
        if self._pipelines:
            self._stop_journal()
        self._conn.commit()

    def rollback(self) -> None:
        if self._released:
            # The transaction died with the dead connection; psycopg would raise "the connection
            # is closed" here and replace the error the caller is handling.
            return
        if self._pipelines:
            self._stop_journal()
        self._conn.rollback()


class _Cursor:
    """A cursor of a :class:`_CheckedOutConnection`, rebound to the fresh connection on a retry."""

    def __init__(self, owner: _CheckedOutConnection, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        self._owner = owner
        self._args = args
        self._kwargs = kwargs
        self._real = owner.raw.cursor(*args, **kwargs)

    #: Client-side cursor attributes: reading them sends nothing and needs no replay.
    _PASSIVE = frozenset({"description", "rowcount", "rownumber", "statusmessage", "pgresult", "closed",
                          "query", "params", "format", "row_factory", "close", "nextset", "scroll"})

    def _bind(self, conn: Any) -> None:
        self._real = conn.cursor(*self._args, **self._kwargs)

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._real, name)
        # `connection` hands out the raw connection (`read_transaction.run_together` uses it):
        # work sent through it is not journalled, so it ends the retry. So does any operation
        # this class does not journal (`stream`, `copy`, ...).
        if name == "connection" or (callable(value) and name not in self._PASSIVE):
            self._owner._stop_journal()
        return value

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *exc: Any) -> None:
        self._real.close()

    def __iter__(self) -> Iterator[Any]:
        self._fetching()
        return iter(self._real)

    def _fetching(self) -> None:
        # Inside a pipeline a fetch is a round trip of its own, in the middle of the body.
        if self._owner._pipelines and self._owner._journal is not None:
            self._owner._stop_journal()

    def fetchone(self) -> Any:
        self._fetching()
        return self._real.fetchone()

    def fetchmany(self, *args: Any, **kwargs: Any) -> Any:
        self._fetching()
        return self._real.fetchmany(*args, **kwargs)

    def fetchall(self) -> Any:
        self._fetching()
        return self._real.fetchall()

    def execute(self, *args: Any, **kwargs: Any) -> "_Cursor":
        self._run("execute", args, kwargs)
        return self

    def executemany(self, *args: Any, **kwargs: Any) -> None:
        self._run("executemany", args, kwargs)

    def _run(self, method: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        owner = self._owner
        if owner._journal is None:
            getattr(self._real, method)(*args, **kwargs)
            return
        owner._journal.append(("execute", self, method, args, kwargs))
        if owner._pipelines:
            # Queued; the round trip is the pipeline's exit. A failure here, mid-body, is final.
            try:
                getattr(self._real, method)(*args, **kwargs)
            except BaseException:
                owner._stop_journal()
                raise
            return
        owner._round_trip(lambda: getattr(self._real, method)(*args, **kwargs))


class _Pipeline:
    """``conn.pipeline()`` on a :class:`_CheckedOutConnection`: its exit is a round trip."""

    def __init__(self, owner: _CheckedOutConnection) -> None:
        self._owner = owner
        self._manager: Any = None
        self._pipeline: Any = None

    def __enter__(self) -> "_Pipeline":
        owner = self._owner
        if owner._journal is not None:
            owner._journal.append(("enter",))
        self._manager = owner.raw.pipeline()
        self._pipeline = self._manager.__enter__()
        owner.__dict__["_pipelines"] += 1
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        owner = self._owner
        owner.__dict__["_pipelines"] -= 1
        if exc_type is not None:
            owner._stop_journal()
            self._manager.__exit__(exc_type, exc, tb)
            return None
        if owner._journal is not None:
            owner._journal.append(("exit",))
        if owner._journal is None or owner._pipelines:
            self._manager.__exit__(None, None, None)
            return None
        manager = self._manager
        owner._round_trip(lambda: manager.__exit__(None, None, None))
        return None

    def sync(self) -> None:
        self._owner._stop_journal()
        self._pipeline.sync()
