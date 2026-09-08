"""Before/after evidence that this run never mutated the canonical archive.

Not a lock — the read-only connection mode already makes mutation by this
code impossible. This is proof for the manifest, and a way to notice (not
prevent) concurrent production ingestion running during our read.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3


@dataclass(frozen=True)
class SourceFingerprint:
    path: str
    size_bytes: int
    mtime_ns: int
    quick_check: str
    schema_version: int


def fingerprint_source_db(db_path: Path, conn: sqlite3.Connection) -> SourceFingerprint:
    stat = Path(db_path).stat()
    quick = conn.execute("PRAGMA quick_check(1)").fetchone()
    schema_version = conn.execute("PRAGMA schema_version").fetchone()
    return SourceFingerprint(
        path=str(db_path),
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        quick_check=str(quick[0]) if quick else "unknown",
        schema_version=int(schema_version[0]) if schema_version else -1,
    )
