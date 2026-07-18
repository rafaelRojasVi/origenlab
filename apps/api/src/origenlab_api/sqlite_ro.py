"""Read-only SQLite opens for the operator API and recovery drills.

Default production opens use ``mode=ro`` (legacy). Recovery / offline candidates
must set ``ORIGENLAB_SQLITE_IMMUTABLE_RO=1`` so opens use ``mode=ro&immutable=1``
plus ``PRAGMA query_only=ON``, which avoids creating ``-wal``/``-shm`` beside
WAL-header snapshots that have no live sidecars.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def companion_paths(db: Path) -> dict[str, Path]:
    return {
        "wal": Path(str(db) + "-wal"),
        "shm": Path(str(db) + "-shm"),
        "journal": Path(str(db) + "-journal"),
    }


def existing_companions(db: Path) -> list[str]:
    return [kind for kind, path in companion_paths(db).items() if path.exists()]


def sqlite_readonly_uri(sqlite_path: Path, *, immutable: bool) -> str:
    resolved = sqlite_path.expanduser().resolve().as_posix()
    if immutable:
        return f"file:{resolved}?mode=ro&immutable=1"
    return f"file:{resolved}?mode=ro"


def open_sqlite_readonly(
    sqlite_path: Path,
    *,
    immutable: bool = False,
    query_only: bool = True,
) -> sqlite3.Connection:
    """Open SQLite for operator reads. Prefer ``immutable=True`` for offline candidates."""
    conn = sqlite3.connect(sqlite_readonly_uri(sqlite_path, immutable=immutable), uri=True)
    conn.row_factory = sqlite3.Row
    if query_only:
        conn.execute("PRAGMA query_only=ON")
    return conn
