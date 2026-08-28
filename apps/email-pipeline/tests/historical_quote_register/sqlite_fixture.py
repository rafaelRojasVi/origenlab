"""Canonical-schema SQLite fixture builder for historical_quote_register tests.

Uses the real production DDL (origenlab_email_pipeline.db.init_schema) so
fixtures can never silently drift from the schema this feature reads.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3

from origenlab_email_pipeline.db import connect, init_schema


class _FixtureConnection:
    """Wrapper around sqlite3.Connection that ensures WAL checkpoint on close.

    Test fixture needs WAL checkpoint to flush writes to main file so
    fingerprint_source_db's Path.stat() can detect the changes, matching
    production behavior where writes to the real archive must be observable.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def __getattr__(self, name: str):
        return getattr(self._conn, name)

    def close(self) -> None:
        """Checkpoint WAL to main file before closing."""
        self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return self._conn.close()


def build_fixture_db(tmp_path: Path, name: str = "fixture.sqlite") -> Path:
    db_path = tmp_path / name
    conn = connect(db_path)
    init_schema(conn)
    conn.commit()
    conn.close()
    return db_path


def open_writable_fixture(db_path: Path) -> sqlite3.Connection:
    """Writable connection for seeding fixture rows. Test-only — never used
    against the real canonical path anywhere in this feature's production code."""
    conn = connect(db_path)
    return _FixtureConnection(conn)  # type: ignore
