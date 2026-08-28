"""Strictly read-only connection to the canonical archive.

Two independent guarantees: SQLite URI mode=ro (never even opens for write,
and never creates a missing file) plus PRAGMA query_only=ON (belt-and-braces —
catches any accidental use of a connection object that bypassed the URI).
"""

from __future__ import annotations

from pathlib import Path
import sqlite3


def open_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA query_only = ON")
    return conn
