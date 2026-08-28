"""Canonical-schema SQLite fixture builder for historical_quote_register tests.

Uses the real production DDL (origenlab_email_pipeline.db.init_schema) so
fixtures can never silently drift from the schema this feature reads.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3

from origenlab_email_pipeline.db import init_schema


def build_fixture_db(tmp_path: Path, name: str = "fixture.sqlite") -> Path:
    db_path = tmp_path / name
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Use direct connection without WAL to ensure fingerprint changes are detectable
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    conn.commit()
    conn.close()
    return db_path


def open_writable_fixture(db_path: Path) -> sqlite3.Connection:
    """Writable connection for seeding fixture rows. Test-only — never used
    against the real canonical path anywhere in this feature's production code."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
