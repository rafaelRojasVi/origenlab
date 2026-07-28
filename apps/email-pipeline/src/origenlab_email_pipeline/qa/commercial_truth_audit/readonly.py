"""Strict read-only SQLite helpers for the commercial truth audit."""

from __future__ import annotations

import sqlite3
from pathlib import Path


class CommercialTruthAuditPathError(ValueError):
    """Raised when required explicit paths are missing."""


def require_explicit_paths(*, sqlite_path: Path | None, output_dir: Path | None) -> tuple[Path, Path]:
    """Require explicit --sqlite-path and --output-dir (no silent production fallback)."""
    if sqlite_path is None:
        raise CommercialTruthAuditPathError(
            "--sqlite-path is required; refusing to fall back to ORIGENLAB_SQLITE_PATH / settings."
        )
    if output_dir is None:
        raise CommercialTruthAuditPathError(
            "--output-dir is required; refusing to invent a production report path."
        )
    resolved_db = Path(sqlite_path).expanduser().resolve()
    resolved_out = Path(output_dir).expanduser().resolve()
    if not resolved_db.is_file():
        raise CommercialTruthAuditPathError(f"SQLite path does not exist or is not a file: {resolved_db}")
    return resolved_db, resolved_out


def connect_sqlite_readonly(path: Path) -> sqlite3.Connection:
    """Open SQLite in URI read-only mode. Never creates or mutates the file."""
    conn = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    # Defense in depth: reject writes even if a caller forgets mode=ro.
    conn.execute("PRAGMA query_only = ON")
    return conn


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def table_columns(conn: sqlite3.Connection, name: str) -> set[str]:
    if not table_exists(conn, name):
        return set()
    return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({name})")}


def safe_count(conn: sqlite3.Connection, table: str) -> int:
    if not table_exists(conn, table):
        return 0
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
