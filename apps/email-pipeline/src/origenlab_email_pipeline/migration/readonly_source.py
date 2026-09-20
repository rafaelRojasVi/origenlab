"""Provably read-only access to a V1 SQLite source database.

Three independent guarantees, each of which alone would stop a write:

1. the connection URI is ``mode=ro`` — SQLite never opens the file for writing
   and never creates a missing one;
2. ``PRAGMA query_only=ON`` is set **and read back** before any query runs, so a
   connection whose read-only mode cannot be proven is refused rather than used;
3. every read happens inside one ``BEGIN DEFERRED`` … ``COMMIT`` transaction, so
   all extracted facts come from one consistent snapshot, and
   ``PRAGMA data_version`` is compared across that window to detect a concurrent
   external commit (the ingest cron is never paused, stopped or modified —
   it is only observed).

The connection itself is opened through the repository's existing read-only
helper, :func:`origenlab_email_pipeline.qa.sqlite_deep_audit.connect_readonly`;
this module adds the proof and the transaction discipline on top of it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from origenlab_email_pipeline.qa.sqlite_deep_audit import connect_readonly

#: Never raise ``immutable=1`` here: the V1 source is the live database the
#: ingest cron writes to, and ``immutable`` would tell SQLite to ignore its WAL.
_IMMUTABLE = False


class ReadOnlyProofError(Exception):
    """Read-only mode could not be proven, so nothing was read.

    Messages are sanitized: they never carry an address or an absolute path.
    """


def read_only_uri(sqlite_path: Path) -> str:
    """Build the ``mode=ro`` URI, percent-encoding everything that breaks parsing."""
    resolved = Path(sqlite_path).expanduser().resolve()
    encoded = quote(resolved.as_posix(), safe="/")
    return f"file:{encoded}?mode=ro"


def assert_query_only_active(conn: sqlite3.Connection) -> None:
    """Refuse a connection that cannot prove ``PRAGMA query_only`` is active."""
    try:
        row = conn.execute("PRAGMA query_only").fetchone()
    except sqlite3.Error as exc:
        raise ReadOnlyProofError(
            f"could not read PRAGMA query_only ({type(exc).__name__})"
        ) from None
    if row is None or int(row[0]) != 1:
        raise ReadOnlyProofError("PRAGMA query_only is not active on this connection")


def open_source_readonly(sqlite_path: Path, *, timeout: float = 120.0) -> sqlite3.Connection:
    """Open the source ``mode=ro`` and prove ``query_only`` before returning it.

    Raises:
        ReadOnlyProofError: the file cannot be opened read-only, or ``query_only``
            cannot be proven. In both cases no query has run.
    """
    path = Path(sqlite_path).expanduser()
    if not path.is_file():
        raise ReadOnlyProofError("source database does not exist at the given path")
    try:
        conn = connect_readonly(path, timeout=timeout, immutable=_IMMUTABLE)
    except sqlite3.Error as exc:
        raise ReadOnlyProofError(
            f"could not open the source read-only ({type(exc).__name__})"
        ) from None
    try:
        assert_query_only_active(conn)
    except ReadOnlyProofError:
        conn.close()
        raise
    # Manual transaction control: the extractor owns one explicit read transaction.
    conn.isolation_level = None
    return conn


@dataclass(frozen=True)
class ReadTransaction:
    """A live single read transaction and the snapshot facts proving it held."""

    conn: sqlite3.Connection
    data_version_start: int
    uri_mode: str = "mode=ro"
    transaction: str = "single BEGIN (DEFERRED) read transaction; COMMIT ends it; no writes"

    def data_version_now(self) -> int:
        return int(self.conn.execute("PRAGMA data_version").fetchone()[0])


@contextmanager
def single_read_transaction(conn: sqlite3.Connection) -> Iterator[ReadTransaction]:
    """Run every read of one extraction inside one deferred read transaction.

    ``query_only`` is re-proven on entry — a caller cannot hand in a connection
    that bypassed :func:`open_source_readonly`.
    """
    assert_query_only_active(conn)
    changes_before = conn.total_changes
    conn.execute("BEGIN DEFERRED")
    try:
        # A DEFERRED transaction takes its read snapshot at the first read.
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        start = int(conn.execute("PRAGMA data_version").fetchone()[0])
        yield ReadTransaction(conn=conn, data_version_start=start)
    finally:
        conn.execute("COMMIT")
    if conn.total_changes != changes_before:
        raise ReadOnlyProofError("the source connection reported changes during the read")
