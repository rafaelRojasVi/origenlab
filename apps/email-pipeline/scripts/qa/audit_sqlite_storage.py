#!/usr/bin/env python3
"""Read-only SQLite storage audit (size, freelist, Sent history diagnostics).

Opens the database with URI ``mode=ro``. Never runs VACUUM, ANALYZE, REINDEX,
wal_checkpoint, DELETE, or any mutation.

``--storage-only`` emits constant-time storage metadata without querying
application tables (including ``emails``).

``--include-dbstat`` is opt-in and can cause heavy I/O on large databases
(e.g. ~127 GiB). Do not enable it against production SQLite without operator approval.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from origenlab_email_pipeline.config import load_settings
from origenlab_email_pipeline.contacto_gmail_source import sql_predicate_contacto_gmail_source
from origenlab_email_pipeline.operator_cli.sqlite_storage_monitor import (
    GiB,
    collect_sqlite_storage_telemetry,
)


def _connect_ro(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=120.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _bytes_or_none(path: Path) -> int | None:
    if not path.is_file():
        return None
    return path.stat().st_size


def _sent_where() -> str:
    pred = sql_predicate_contacto_gmail_source()
    return f"""
        {pred}
          AND (
            source_file LIKE '%Enviados%'
            OR source_file LIKE '%Sent%'
          )
    """


def _safe_count(conn: sqlite3.Connection, sql: str) -> int | None:
    try:
        return int(conn.execute(sql).fetchone()[0] or 0)
    except sqlite3.Error:
        return None


def run_storage_only_audit(db: Path) -> dict[str, Any]:
    """Constant-time storage metadata only — no application-table queries."""
    telem = collect_sqlite_storage_telemetry(db)
    return {
        "mode": "storage-only",
        "db_path_basename": telem["database_basename"],
        "database_size_bytes": telem["database_size_bytes"],
        "wal_size_bytes": telem["wal_size_bytes"],
        "shm_size_bytes": telem["shm_size_bytes"],
        "page_size": telem["page_size"],
        "page_count": telem["page_count"],
        "freelist_count": telem["freelist_count"],
        "allocated_bytes": telem["allocated_bytes"],
        "allocated_gib": telem["allocated_gib"],
        "freelist_unused_bytes": telem["freelist_bytes"],
        "freelist_unused_gib": telem["freelist_gib"],
        "freelist_bytes": telem["freelist_bytes"],
        "freelist_gib": telem["freelist_gib"],
        "active_page_count": telem["active_page_count"],
        "active_bytes": telem["active_bytes"],
        "active_gib": telem["active_gib"],
        "freelist_ratio_percent": telem["freelist_ratio_percent"],
        "auto_vacuum": telem["auto_vacuum"],
        "auto_vacuum_name": telem["auto_vacuum_name"],
        "journal_mode": telem["journal_mode"],
        "disk_total_bytes": telem["disk_total_bytes"],
        "disk_free_bytes": telem["disk_free_bytes"],
        "disk_used_bytes": telem["disk_used_bytes"],
        "disk_total_gib": telem["disk_total_gib"],
        "disk_free_gib": telem["disk_free_gib"],
        "disk_used_gib": telem["disk_used_gib"],
        "disk_free_percent": telem["disk_free_percent"],
        "dbstat_included": False,
        "mutation": False,
        "read_only": True,
        "captured_at_utc": telem["captured_at_utc"],
        "schema_version": telem["schema_version"],
    }


def run_quick_audit(conn: sqlite3.Connection, db: Path) -> dict[str, Any]:
    telem = collect_sqlite_storage_telemetry(db)
    page_size = int(telem["page_size"])
    page_count = int(telem["page_count"])
    freelist_count = int(telem["freelist_count"])
    allocated_bytes = int(telem["allocated_bytes"])
    freelist_bytes = int(telem["freelist_bytes"])

    wal_path = Path(str(db) + "-wal")
    shm_path = Path(str(db) + "-shm")

    sent_where = _sent_where()
    today = datetime.now(timezone.utc).date()
    future_cutoff = (today + timedelta(days=2)).isoformat()

    future_count = _safe_count(
        conn,
        f"""
        SELECT COUNT(*) FROM emails
        WHERE date_iso IS NOT NULL AND length(date_iso) >= 10
          AND substr(date_iso, 1, 10) > '{future_cutoff}'
        """,
    )

    future_meta: list[dict[str, Any]] = []
    if future_count and future_count > 0:
        cur = conn.execute(
            f"""
            SELECT id, date_iso, source_file, folder,
                   CASE
                     WHEN message_id IS NULL OR trim(message_id) = '' THEN 1
                     ELSE 0
                   END AS missing_message_id,
                   CASE
                     WHEN body IS NULL OR trim(body) = '' THEN 1
                     ELSE 0
                   END AS empty_body
            FROM emails
            WHERE date_iso IS NOT NULL AND length(date_iso) >= 10
              AND substr(date_iso, 1, 10) > '{future_cutoff}'
            ORDER BY date_iso DESC, id
            LIMIT 20
            """
        )
        for row in cur:
            future_meta.append(
                {
                    "id": row["id"],
                    "date_iso": row["date_iso"],
                    "source_file": row["source_file"],
                    "folder": row["folder"],
                    "missing_message_id": bool(row["missing_message_id"]),
                    "empty_body": bool(row["empty_body"]),
                }
            )

    return {
        "mode": "quick",
        "db_path_basename": db.name,
        "database_size_bytes": _bytes_or_none(db),
        "wal_size_bytes": _bytes_or_none(wal_path),
        "shm_size_bytes": _bytes_or_none(shm_path),
        "page_size": page_size,
        "page_count": page_count,
        "freelist_count": freelist_count,
        "allocated_bytes": allocated_bytes,
        "allocated_gib": round(allocated_bytes / GiB, 4),
        "freelist_unused_bytes": freelist_bytes,
        "freelist_unused_gib": round(freelist_bytes / GiB, 4),
        "email_row_count": _safe_count(conn, "SELECT COUNT(*) FROM emails"),
        "canonical_sent_row_count": _safe_count(
            conn, f"SELECT COUNT(*) FROM emails WHERE {sent_where}"
        ),
        "canonical_sent_distinct_message_id_count": _safe_count(
            conn,
            f"""
            SELECT COUNT(DISTINCT lower(trim(message_id)))
            FROM emails
            WHERE {sent_where}
              AND message_id IS NOT NULL AND trim(message_id) != ''
            """,
        ),
        "canonical_sent_missing_message_id_count": _safe_count(
            conn,
            f"""
            SELECT COUNT(*) FROM emails
            WHERE {sent_where}
              AND (message_id IS NULL OR trim(message_id) = '')
            """,
        ),
        "suspicious_future_date_count": future_count,
        "suspicious_future_date_rows_metadata": future_meta,
        "comparison_note": (
            "Stored canonical Sent history may exceed Gmail's current Sent label count "
            "(duplicate message_ids, missing message_ids, or retained historical rows). "
            "Do not assume equality with live Gmail Sent. Never VACUUM this database "
            "without an explicit storage plan and backup."
        ),
        "dbstat_included": False,
        "mutation": False,
        "read_only": True,
    }


def run_dbstat(conn: sqlite3.Connection, *, limit: int = 25) -> dict[str, Any]:
    """Expensive: scans allocated pages via dbstat. Opt-in only."""
    rows: list[dict[str, Any]] = []
    try:
        cur = conn.execute(
            """
            SELECT name, SUM(pgsize) AS allocated_bytes
            FROM dbstat
            GROUP BY name
            ORDER BY allocated_bytes DESC
            LIMIT ?
            """,
            (limit,),
        )
        for row in cur:
            rows.append(
                {
                    "name": row["name"],
                    "allocated_bytes": int(row["allocated_bytes"] or 0),
                    "allocated_gib": round(int(row["allocated_bytes"] or 0) / GiB, 4),
                }
            )
    except sqlite3.Error as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "warning": "dbstat unavailable or failed; no mutation was attempted.",
            "largest_objects": [],
        }
    return {
        "ok": True,
        "warning": (
            "dbstat scanned page allocations; this can be heavy I/O on multi‑GiB databases."
        ),
        "largest_objects": rows,
    }


def format_human(report: dict[str, Any]) -> str:
    lines = [
        "SQLite storage audit (read-only)",
        f"  mode: {report.get('mode')}",
        f"  database: {report.get('db_path_basename')}",
        f"  size_bytes: {report.get('database_size_bytes')}",
        f"  wal_size_bytes: {report.get('wal_size_bytes')}",
        f"  shm_size_bytes: {report.get('shm_size_bytes')}",
        f"  page_size: {report.get('page_size')}",
        f"  page_count: {report.get('page_count')}",
        f"  freelist_count: {report.get('freelist_count')}",
        f"  allocated_gib: {report.get('allocated_gib')}",
        f"  freelist_unused_gib: {report.get('freelist_unused_gib')}",
    ]
    if report.get("mode") == "storage-only":
        lines.extend(
            [
                f"  active_gib: {report.get('active_gib')}",
                f"  freelist_ratio_percent: {report.get('freelist_ratio_percent')}",
                f"  auto_vacuum_name: {report.get('auto_vacuum_name')}",
                f"  journal_mode: {report.get('journal_mode')}",
                f"  disk_free_gib: {report.get('disk_free_gib')}",
                f"  disk_free_percent: {report.get('disk_free_percent')}",
            ]
        )
    else:
        lines.extend(
            [
                f"  email_row_count: {report.get('email_row_count')}",
                f"  canonical_sent_row_count: {report.get('canonical_sent_row_count')}",
                f"  canonical_sent_distinct_message_id_count: "
                f"{report.get('canonical_sent_distinct_message_id_count')}",
                f"  canonical_sent_missing_message_id_count: "
                f"{report.get('canonical_sent_missing_message_id_count')}",
                f"  suspicious_future_date_count: {report.get('suspicious_future_date_count')}",
                f"  comparison_note: {report.get('comparison_note')}",
            ]
        )
    if report.get("dbstat"):
        lines.append(f"  dbstat: {report['dbstat'].get('warning') or report['dbstat']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only SQLite storage audit (no VACUUM / no mutations)."
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="SQLite path (default: OrigenLab settings resolved_sqlite_path)",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON on stdout")
    parser.add_argument(
        "--storage-only",
        action="store_true",
        help=(
            "Emit constant-time storage metadata only "
            "(no emails/Sent/future-date/COUNT/dbstat queries)."
        ),
    )
    parser.add_argument(
        "--include-dbstat",
        action="store_true",
        help=(
            "Expensive: scan dbstat for largest tables/indexes. "
            "Can cause heavy I/O on large databases; do not use on ~127 GiB DBs without approval."
        ),
    )
    parser.add_argument(
        "--include-subjects",
        action="store_true",
        help="Include subjects in future-date metadata (off by default).",
    )
    args = parser.parse_args(argv)

    if args.storage_only and (args.include_dbstat or args.include_subjects):
        print(
            "ERROR: --storage-only cannot be combined with --include-dbstat or --include-subjects.",
            file=sys.stderr,
        )
        return 2

    db = args.db or load_settings().resolved_sqlite_path()
    if not db.is_file():
        print(f"SQLite not found: {db}", file=sys.stderr)
        return 2

    if args.include_dbstat:
        print(
            "WARNING: --include-dbstat can scan a multi‑GiB database and cause heavy I/O.",
            file=sys.stderr,
        )

    if args.storage_only:
        report = run_storage_only_audit(db)
    else:
        conn = _connect_ro(db)
        try:
            report = run_quick_audit(conn, db)
            if args.include_subjects and report.get("suspicious_future_date_rows_metadata"):
                for meta in report["suspicious_future_date_rows_metadata"]:
                    row = conn.execute(
                        "SELECT subject FROM emails WHERE id = ?", (meta["id"],)
                    ).fetchone()
                    meta["subject"] = row["subject"] if row else None
            if args.include_dbstat:
                report["dbstat_included"] = True
                report["dbstat"] = run_dbstat(conn)
                report["mode"] = "quick+dbstat"
        finally:
            conn.close()

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(format_human(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
