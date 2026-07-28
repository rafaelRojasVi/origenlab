"""Strict read-only SQLite helpers and output-path safety for the commercial truth audit."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from origenlab_email_pipeline.qa.commercial_truth_audit.constants import DEFAULT_REPORT_ROOT_REL


class CommercialTruthAuditPathError(ValueError):
    """Raised when required explicit paths are missing or unsafe."""


def email_pipeline_root() -> Path:
    """``apps/email-pipeline`` package root (parents[4] from this file)."""
    # .../src/origenlab_email_pipeline/qa/commercial_truth_audit/readonly.py
    return Path(__file__).resolve().parents[4]


def default_report_root() -> Path:
    return (email_pipeline_root() / DEFAULT_REPORT_ROOT_REL).resolve()


def require_explicit_paths(
    *,
    sqlite_path: Path | None,
    output_dir: Path | None,
    allow_output_outside_report_root: bool = False,
    report_root: Path | None = None,
) -> tuple[Path, Path]:
    """Require explicit --sqlite-path and --output-dir (no silent production fallback).

    By default, ``output_dir`` must resolve under the gitignored ``reports/out/`` tree.
    Pass ``allow_output_outside_report_root=True`` only for deliberate local/tmp runs.
    """
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

    root = (report_root or default_report_root()).resolve()
    if not allow_output_outside_report_root:
        try:
            resolved_out.relative_to(root)
        except ValueError as exc:
            raise CommercialTruthAuditPathError(
                f"--output-dir must be under the gitignored report root {root} "
                f"(got {resolved_out}). Pass --allow-output-outside-report-root to override."
            ) from exc
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
