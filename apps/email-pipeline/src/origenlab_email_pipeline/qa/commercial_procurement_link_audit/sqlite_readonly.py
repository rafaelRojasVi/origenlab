"""Strict read-only SQLite + output-path safety for the PR4 procurement-link audit."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from origenlab_email_pipeline.qa.commercial_procurement_link_audit.constants import (
    DEFAULT_OUTPUT_DIRNAME,
    DEFAULT_REPORT_ROOT_REL,
)


class ProcurementLinkAuditPathError(ValueError):
    """Raised when required explicit paths are missing or unsafe."""


def email_pipeline_root() -> Path:
    # .../src/origenlab_email_pipeline/qa/commercial_procurement_link_audit/sqlite_readonly.py
    return Path(__file__).resolve().parents[4]


def default_report_root() -> Path:
    return (email_pipeline_root() / DEFAULT_REPORT_ROOT_REL).resolve()


def default_production_output_dir() -> Path:
    return (email_pipeline_root() / "reports/out/active/current" / DEFAULT_OUTPUT_DIRNAME).resolve()


def require_explicit_paths(
    *,
    sqlite_path: Path | None,
    output_dir: Path | None,
    allow_output_outside_report_root: bool = False,
    report_root: Path | None = None,
) -> tuple[Path, Path]:
    if sqlite_path is None:
        raise ProcurementLinkAuditPathError(
            "--sqlite-path is required; refusing to fall back to ORIGENLAB_SQLITE_PATH / settings."
        )
    if output_dir is None:
        raise ProcurementLinkAuditPathError(
            "--output-dir is required; refusing to invent a production report path."
        )
    resolved_db = Path(sqlite_path).expanduser().resolve()
    resolved_out = Path(output_dir).expanduser().resolve()
    if not resolved_db.is_file():
        raise ProcurementLinkAuditPathError(
            f"SQLite path does not exist or is not a file: {resolved_db}"
        )

    root = (report_root or default_report_root()).resolve()
    if not allow_output_outside_report_root:
        try:
            resolved_out.relative_to(root)
        except ValueError as exc:
            raise ProcurementLinkAuditPathError(
                f"--output-dir must be under the gitignored report root {root} "
                f"(got {resolved_out}). Pass --allow-output-outside-report-root to override."
            ) from exc
    return resolved_db, resolved_out


def connect_readonly(sqlite_path: Path) -> sqlite3.Connection:
    """Open production SQLite in URI mode=ro with query_only=ON."""
    uri = f"file:{sqlite_path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    # Refuse accidental writes even if a caller drops query_only.
    conn.execute("PRAGMA query_only")
    return conn


def assert_no_write_connection(conn: sqlite3.Connection) -> None:
    row = conn.execute("PRAGMA query_only").fetchone()
    if row is None or int(row[0]) != 1:
        raise ProcurementLinkAuditPathError("SQLite connection is not query_only; refusing audit.")
