"""Read-only, privacy-safe SQLite deep forensic audit (offline copy only for heavy phases)."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from origenlab_email_pipeline.config import Settings, load_settings
from origenlab_email_pipeline.contacto_gmail_source import (
    CONTACTO_GMAIL_SOURCE_SQL_LIKE_VALUE,
    LEGACY_LABDELIVERY_SOURCE_LIKE,
    sql_predicate_contacto_gmail_source,
)

GiB = 1024**3
AUDIT_SCHEMA_VERSION = 1

# SQL statements that must never run against operator SQLite.
BLOCKED_SQL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bVACUUM\b",
        r"\bANALYZE\b",
        r"\bREINDEX\b",
        r"\bwal_checkpoint\b",
        r"\bDELETE\b",
        r"\bUPDATE\b",
        r"\bINSERT\b",
        r"\bCREATE\b",
        r"\bDROP\b",
        r"\bALTER\b",
    )
)

EMAIL_BODY_COLUMNS: tuple[str, ...] = (
    "body",
    "body_html",
    "body_text_raw",
    "body_text_clean",
    "full_body_clean",
    "top_reply_clean",
)

ATTACHMENT_EXTRACT_TEXT_COLUMNS: tuple[str, ...] = (
    "text_preview",
    "text_truncated",
)

PII_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "subject",
        "body",
        "body_html",
        "body_text_raw",
        "body_text_clean",
        "full_body_clean",
        "top_reply_clean",
        "sender",
        "recipients",
        "saved_path",
        "text_preview",
        "text_truncated",
        "raw_json",
        "details_json",
        "recipient_emails_json",
        "external_targets_json",
    }
)

PII_OUTPUT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"@example\.com",
        r"@origenlab\.cl",
        r"@labdelivery",
        r"/home/[^\s\"']+",
        r"/mnt/[^\s\"']+",
        r"secret-body",
        r"secret-subject",
    )
)

SOURCE_TIER_CASE_SQL = f"""
CASE
  WHEN lower(COALESCE(source_file, '')) LIKE '{CONTACTO_GMAIL_SOURCE_SQL_LIKE_VALUE}' THEN 'canonical_gmail'
  WHEN lower(COALESCE(source_file, '')) LIKE '{LEGACY_LABDELIVERY_SOURCE_LIKE}' THEN 'legacy_labdelivery'
  WHEN lower(COALESCE(source_file, '')) LIKE 'imap:%' THEN 'imap'
  WHEN lower(COALESCE(source_file, '')) LIKE 'gmail:%' THEN 'other_gmail'
  WHEN instr(source_file, '/') > 0 OR instr(source_file, char(92)) > 0 THEN 'mbox'
  ELSE 'unknown'
END
"""

HEAVY_PHASE_NAMES: frozenset[str] = frozenset(
    {
        "physical_dbstat",
        "column_bytes",
        "duplicate_analysis",
        "usefulness_classification",
        "structural_full",
    }
)

LIGHT_PHASE_NAMES: frozenset[str] = frozenset({"structural_quick", "summarize"})

ALL_PHASE_NAMES: frozenset[str] = HEAVY_PHASE_NAMES | LIGHT_PHASE_NAMES

MART_TABLES: tuple[str, ...] = (
    "email_mart_features",
    "contact_master",
    "organization_master",
    "document_master",
    "opportunity_signals",
)

COMMERCIAL_TABLES: tuple[str, ...] = (
    "commercial_email_signal_fact",
    "commercial_deal",
    "commercial_deal_evidence",
)


@dataclass
class AuditOptions:
    db: Path
    confirm_offline_copy: bool = False
    phases: frozenset[str] = field(default_factory=lambda: frozenset(ALL_PHASE_NAMES))
    full_integrity_check: bool = False
    resume: bool = False
    output_dir: Path | None = None
    settings: Settings | None = None


@dataclass
class FileFingerprint:
    size_bytes: int
    mtime_ns: int

    def to_dict(self) -> dict[str, int]:
        return {"size_bytes": self.size_bytes, "mtime_ns": self.mtime_ns}


def _iso_now(now: datetime | None = None) -> str:
    dt = now or datetime.now(timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def resolved_production_sqlite_path(settings: Settings | None = None) -> Path:
    return (settings or load_settings()).resolved_sqlite_path().resolve()


def is_configured_production_db(db: Path, settings: Settings | None = None) -> bool:
    return db.resolve() == resolved_production_sqlite_path(settings)


def assert_sql_allowed(sql: str) -> None:
    for pattern in BLOCKED_SQL_PATTERNS:
        if pattern.search(sql):
            raise ValueError(f"blocked SQL pattern in statement: {sql[:120]}")


def connect_readonly(db: Path, *, timeout: float = 120.0) -> sqlite3.Connection:
    uri = f"file:{db.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def execute_ro(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
    assert_sql_allowed(sql)
    return conn.execute(sql, params)


def fingerprint_db_files(db: Path) -> dict[str, FileFingerprint | None]:
    out: dict[str, FileFingerprint | None] = {}
    for label, path in (
        ("database", db),
        ("wal", Path(str(db) + "-wal")),
        ("shm", Path(str(db) + "-shm")),
    ):
        if path.is_file():
            st = path.stat()
            out[label] = FileFingerprint(size_bytes=int(st.st_size), mtime_ns=int(st.st_mtime_ns))
        else:
            out[label] = None
    return out


def fingerprints_equal(
    before: dict[str, FileFingerprint | None],
    after: dict[str, FileFingerprint | None],
) -> bool:
    return before == after


def validate_heavy_access(options: AuditOptions) -> str | None:
    """Return error message when heavy phases are not permitted."""
    heavy_requested = bool(options.phases & HEAVY_PHASE_NAMES)
    if options.full_integrity_check:
        heavy_requested = True
    if not heavy_requested:
        return None
    if is_configured_production_db(options.db, options.settings):
        return (
            "heavy audit phases refused: --db resolves to the configured production SQLite path. "
            "Use a verified offline copy on separate storage."
        )
    if not options.confirm_offline_copy:
        return (
            "heavy audit phases require --confirm-offline-copy acknowledging this is a "
            "verified offline/backup copy, not live production SQLite."
        )
    return None


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = execute_ro(
        conn,
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def _table_names(conn: sqlite3.Connection) -> list[str]:
    rows = execute_ro(
        conn,
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name",
    ).fetchall()
    return [str(r[0]) for r in rows]


def _index_inventory(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = execute_ro(
        conn,
        """
        SELECT name, tbl_name, sql
        FROM sqlite_master
        WHERE type='index' AND name NOT LIKE 'sqlite_%'
        ORDER BY tbl_name, name
        """,
    ).fetchall()
    return [
        {
            "name": r["name"],
            "table": r["tbl_name"],
            "sql_present": bool(r["sql"]),
        }
        for r in rows
    ]


def _row_count_and_id_range(conn: sqlite3.Connection, table: str) -> dict[str, Any]:
    if not _has_table(conn, table):
        return {"exists": False}
    count = int(execute_ro(conn, f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    id_range: dict[str, Any] = {"min_id": None, "max_id": None}
    if table == "emails" or execute_ro(
        conn, f"PRAGMA table_info({table})"
    ).fetchall():
        cols = {str(r[1]) for r in execute_ro(conn, f"PRAGMA table_info({table})")}
        if "id" in cols:
            row = execute_ro(conn, f"SELECT MIN(id), MAX(id) FROM {table}").fetchone()
            id_range = {"min_id": row[0], "max_id": row[1]}
    return {"exists": True, "row_count": count, **id_range}


def run_structural_quick(conn: sqlite3.Connection, db: Path) -> dict[str, Any]:
    started = time.perf_counter()
    quick = execute_ro(conn, "PRAGMA quick_check").fetchone()[0]
    fk_rows = execute_ro(conn, "PRAGMA foreign_key_check").fetchall()
    fk_violations = [
        {
            "table": r[0],
            "rowid": r[1],
            "parent": r[2],
            "fk_index": r[3],
        }
        for r in fk_rows
    ]
    page_size = int(execute_ro(conn, "PRAGMA page_size").fetchone()[0])
    page_count = int(execute_ro(conn, "PRAGMA page_count").fetchone()[0])
    freelist_count = int(execute_ro(conn, "PRAGMA freelist_count").fetchone()[0])

    tables = _table_names(conn)
    table_stats: dict[str, Any] = {}
    for name in tables:
        table_stats[name] = _row_count_and_id_range(conn, name)

    return {
        "phase": "structural_quick",
        "exact": True,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "quick_check": str(quick),
        "foreign_key_violations": fk_violations,
        "foreign_key_violation_count": len(fk_violations),
        "page_size": page_size,
        "page_count": page_count,
        "freelist_count": freelist_count,
        "allocated_bytes": page_size * page_count,
        "freelist_bytes": page_size * freelist_count,
        "database_basename": db.name,
        "tables": tables,
        "indexes": _index_inventory(conn),
        "table_stats": table_stats,
        "structural_corruption_detected": quick != "ok" or bool(fk_violations),
    }


def run_structural_full(conn: sqlite3.Connection) -> dict[str, Any]:
    started = time.perf_counter()
    rows = execute_ro(conn, "PRAGMA integrity_check").fetchall()
    messages = [str(r[0]) for r in rows]
    ok = len(messages) == 1 and messages[0].lower() == "ok"
    return {
        "phase": "structural_full",
        "exact": True,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "integrity_check_ok": ok,
        "integrity_messages": messages if not ok else ["ok"],
        "warning": "PRAGMA integrity_check can take hours on multi-GiB databases.",
        "structural_corruption_detected": not ok,
    }


def run_physical_dbstat(conn: sqlite3.Connection) -> dict[str, Any]:
    started = time.perf_counter()
    by_object: list[dict[str, Any]] = []
    total_allocated = 0
    cur = execute_ro(
        conn,
        """
        SELECT name, SUM(pgsize) AS allocated_bytes
        FROM dbstat
        GROUP BY name
        ORDER BY allocated_bytes DESC
        """,
    )
    for row in cur:
        nbytes = int(row["allocated_bytes"] or 0)
        total_allocated += nbytes
        by_object.append(
            {
                "name": row["name"],
                "allocated_bytes": nbytes,
                "allocated_gib": round(nbytes / GiB, 6),
                "kind": _object_kind(str(row["name"])),
            }
        )

    page_size = int(execute_ro(conn, "PRAGMA page_size").fetchone()[0])
    page_count = int(execute_ro(conn, "PRAGMA page_count").fetchone()[0])
    freelist_count = int(execute_ro(conn, "PRAGMA freelist_count").fetchone()[0])
    file_bytes = page_size * page_count
    freelist_bytes = page_size * freelist_count
    active_bytes = max(0, file_bytes - freelist_bytes)
    reconcile_delta = file_bytes - (total_allocated + freelist_bytes)

    tables = [o for o in by_object if o["kind"] == "table"]
    indexes = [o for o in by_object if o["kind"] == "index"]

    return {
        "phase": "physical_dbstat",
        "exact": True,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "objects": by_object,
        "tables_top": tables[:25],
        "indexes_top": indexes[:25],
        "reconciliation": {
            "page_file_bytes": file_bytes,
            "dbstat_object_bytes": total_allocated,
            "freelist_bytes": freelist_bytes,
            "active_bytes_estimate": active_bytes,
            "reconcile_delta_bytes": reconcile_delta,
        },
    }


def _object_kind(name: str) -> str:
    if name.startswith("sqlite_autoindex"):
        return "autoindex"
    # Heuristic: indexes often prefixed idx_ or end with specific patterns; fallback via sqlite_master
    return "table"


def _profile_text_column(conn: sqlite3.Connection, table: str, column: str) -> dict[str, Any] | None:
    if not _has_table(conn, table):
        return None
    cols = {str(r[1]) for r in execute_ro(conn, f"PRAGMA table_info({table})")}
    if column not in cols:
        return None
    row = execute_ro(
        conn,
        f"""
        SELECT
          COUNT(*) AS row_count,
          SUM(CASE WHEN {column} IS NULL OR length({column}) = 0 THEN 1 ELSE 0 END) AS null_or_empty_count,
          SUM(length(CAST({column} AS BLOB))) AS aggregate_bytes,
          AVG(length(CAST({column} AS BLOB))) AS avg_bytes,
          MAX(length(CAST({column} AS BLOB))) AS max_bytes
        FROM {table}
        """,
    ).fetchone()
    return {
        "table": table,
        "column": column,
        "row_count": int(row["row_count"] or 0),
        "null_or_empty_count": int(row["null_or_empty_count"] or 0),
        "aggregate_bytes": int(row["aggregate_bytes"] or 0),
        "avg_bytes": round(float(row["avg_bytes"] or 0.0), 2),
        "max_bytes": int(row["max_bytes"] or 0),
        "exact": True,
    }


def run_column_bytes(conn: sqlite3.Connection) -> dict[str, Any]:
    started = time.perf_counter()
    email_profiles = [
        p
        for col in EMAIL_BODY_COLUMNS
        if (p := _profile_text_column(conn, "emails", col)) is not None
    ]
    extract_profiles = [
        p
        for col in ATTACHMENT_EXTRACT_TEXT_COLUMNS
        if (p := _profile_text_column(conn, "attachment_extracts", col)) is not None
    ]
    return {
        "phase": "column_bytes",
        "exact": True,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "emails_body_columns": email_profiles,
        "attachment_extract_text_columns": extract_profiles,
    }


def _duplicate_message_id_stats(
    conn: sqlite3.Connection,
    *,
    cohort_label: str,
    where_sql: str,
) -> dict[str, Any]:
    if not _has_table(conn, "emails"):
        return {"cohort": cohort_label, "exists": False}
    total = int(execute_ro(conn, f"SELECT COUNT(*) FROM emails e WHERE {where_sql}").fetchone()[0])
    dup_groups = int(
        execute_ro(
            conn,
            f"""
            SELECT COUNT(*) FROM (
              SELECT lower(trim(e.message_id)) AS mid
              FROM emails e
              WHERE {where_sql}
                AND e.message_id IS NOT NULL AND trim(e.message_id) != ''
              GROUP BY lower(trim(e.message_id))
              HAVING COUNT(*) > 1
            )
            """,
        ).fetchone()[0]
    )
    extra_rows = execute_ro(
        conn,
        f"""
        SELECT SUM(cnt - 1) FROM (
          SELECT COUNT(*) AS cnt
          FROM emails e
          WHERE {where_sql}
            AND e.message_id IS NOT NULL AND trim(e.message_id) != ''
          GROUP BY lower(trim(e.message_id))
          HAVING COUNT(*) > 1
        )
        """,
    ).fetchone()[0]
    exact_dup_groups = int(
        execute_ro(
            conn,
            f"""
            SELECT COUNT(*) FROM (
              SELECT lower(trim(e.message_id)) AS mid
              FROM emails e
              WHERE {where_sql}
                AND e.message_id IS NOT NULL AND trim(e.message_id) != ''
              GROUP BY lower(trim(e.message_id))
              HAVING COUNT(*) > 1
                AND COUNT(DISTINCT length(CAST(e.body AS BLOB)) || ':' || length(CAST(e.full_body_clean AS BLOB))) = 1
            )
            """,
        ).fetchone()[0]
    )
    repeated_body_bytes = execute_ro(
        conn,
        f"""
        SELECT SUM(extra_bytes) FROM (
          SELECT
            SUM(
              length(CAST(e.body AS BLOB)) +
              length(CAST(e.body_html AS BLOB)) +
              length(CAST(e.body_text_raw AS BLOB)) +
              length(CAST(e.body_text_clean AS BLOB)) +
              length(CAST(e.full_body_clean AS BLOB)) +
              length(CAST(e.top_reply_clean AS BLOB))
            ) - MIN(
              length(CAST(e.body AS BLOB)) +
              length(CAST(e.body_html AS BLOB)) +
              length(CAST(e.body_text_raw AS BLOB)) +
              length(CAST(e.body_text_clean AS BLOB)) +
              length(CAST(e.full_body_clean AS BLOB)) +
              length(CAST(e.top_reply_clean AS BLOB))
            ) AS extra_bytes
          FROM emails e
          WHERE {where_sql}
            AND e.message_id IS NOT NULL AND trim(e.message_id) != ''
          GROUP BY lower(trim(e.message_id))
          HAVING COUNT(*) > 1
        )
        """,
    ).fetchone()[0]
    return {
        "cohort": cohort_label,
        "exists": True,
        "row_count": total,
        "duplicate_message_id_groups": dup_groups,
        "duplicate_extra_rows": int(extra_rows or 0),
        "exact_duplicate_body_groups": exact_dup_groups,
        "estimated_repeated_body_bytes": int(repeated_body_bytes or 0),
        "note": (
            "duplicate_message_id_groups counts repeated IDs; exact_duplicate_body_groups "
            "requires identical body length fingerprints across duplicates."
        ),
        "exact": True,
    }


def run_duplicate_analysis(conn: sqlite3.Connection) -> dict[str, Any]:
    started = time.perf_counter()
    canonical_where = sql_predicate_contacto_gmail_source(table_alias="e", coalesce_null=False)
    cohorts = [
        _duplicate_message_id_stats(conn, cohort_label="canonical_gmail", where_sql=canonical_where),
        _duplicate_message_id_stats(
            conn,
            cohort_label="legacy_labdelivery",
            where_sql="lower(e.source_file) LIKE '%contacto@labdelivery%'",
        ),
        _duplicate_message_id_stats(
            conn,
            cohort_label="all_emails",
            where_sql="1=1",
        ),
    ]
    attachment_dupes: dict[str, Any] = {"exists": _has_table(conn, "attachments")}
    if attachment_dupes["exists"]:
        row = execute_ro(
            conn,
            """
            SELECT
              COUNT(*) AS duplicate_sha_groups,
              SUM(cnt - 1) AS duplicate_extra_rows,
              SUM((cnt - 1) * COALESCE(avg_size, 0)) AS estimated_duplicate_bytes
            FROM (
              SELECT sha256, COUNT(*) AS cnt, AVG(COALESCE(size_bytes, 0)) AS avg_size
              FROM attachments
              WHERE sha256 IS NOT NULL AND trim(sha256) != ''
              GROUP BY sha256
              HAVING COUNT(*) > 1
            )
            """,
        ).fetchone()
        attachment_dupes.update(
            {
                "duplicate_sha256_groups": int(row["duplicate_sha_groups"] or 0),
                "duplicate_extra_rows": int(row["duplicate_extra_rows"] or 0),
                "estimated_duplicate_bytes": int(row["estimated_duplicate_bytes"] or 0),
                "exact": True,
            }
        )
    return {
        "phase": "duplicate_analysis",
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "message_id_cohorts": cohorts,
        "attachment_sha256": attachment_dupes,
    }


def _table_has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    if not _has_table(conn, table):
        return False
    cols = {str(r[1]) for r in execute_ro(conn, f"PRAGMA table_info({table})")}
    return column in cols


def _referenced_email_ids_sql(conn: sqlite3.Connection) -> str | None:
    parts: list[str] = []
    if _table_has_column(conn, "attachments", "email_id"):
        parts.append("SELECT email_id FROM attachments WHERE email_id IS NOT NULL")
    for table in ("document_master", "opportunity_signals", "commercial_email_signal_fact"):
        if _table_has_column(conn, table, "email_id"):
            parts.append(f"SELECT email_id FROM {table} WHERE email_id IS NOT NULL")
    for table, column in (
        ("commercial_deal_evidence", "email_id"),
        ("commercial_deal_evidence", "source_email_id"),
        ("commercial_deal_event", "source_email_id"),
        ("commercial_purchase_events", "source_email_id"),
    ):
        if _table_has_column(conn, table, column):
            parts.append(f"SELECT {column} AS email_id FROM {table} WHERE {column} IS NOT NULL")
    if not parts:
        return None
    return " UNION ".join(parts)


def run_usefulness_classification(conn: sqlite3.Connection) -> dict[str, Any]:
    started = time.perf_counter()
    if not _has_table(conn, "emails"):
        return {"phase": "usefulness_classification", "exists": False, "exact": True}

    tier_rows = execute_ro(
        conn,
        f"""
        SELECT tier, COUNT(*) AS row_count
        FROM (
          SELECT {SOURCE_TIER_CASE_SQL} AS tier
          FROM emails
        )
        GROUP BY tier
        ORDER BY row_count DESC
        """,
    ).fetchall()
    source_tiers = {str(r["tier"]): int(r["row_count"]) for r in tier_rows}

    ref_sql = _referenced_email_ids_sql(conn)
    referenced = 0
    historical_only = int(execute_ro(conn, "SELECT COUNT(*) FROM emails").fetchone()[0])
    if ref_sql:
        referenced = int(
            execute_ro(
                conn,
                f"SELECT COUNT(DISTINCT email_id) FROM ({ref_sql})",
            ).fetchone()[0]
        )
        historical_only = int(
            execute_ro(
                conn,
                f"""
                SELECT COUNT(*) FROM emails e
                WHERE e.id NOT IN (SELECT email_id FROM ({ref_sql}))
                """,
            ).fetchone()[0]
        )

    orphan_attachments = 0
    if _has_table(conn, "attachments"):
        orphan_attachments = int(
            execute_ro(
                conn,
                """
                SELECT COUNT(*) FROM attachments a
                LEFT JOIN emails e ON e.id = a.email_id
                WHERE e.id IS NULL
                """,
            ).fetchone()[0]
        )

    mart_counts: dict[str, int] = {}
    for table in MART_TABLES:
        if _has_table(conn, table):
            mart_counts[table] = int(execute_ro(conn, f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    commercial_counts: dict[str, int] = {}
    for table in COMMERCIAL_TABLES:
        if _has_table(conn, table):
            commercial_counts[table] = int(
                execute_ro(conn, f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            )

    invalid_date_rows = int(
        execute_ro(
            conn,
            """
            SELECT COUNT(*) FROM emails
            WHERE date_iso IS NOT NULL AND trim(date_iso) != ''
              AND (
                length(date_iso) < 10
                OR substr(date_iso, 1, 4) GLOB '[0-9][0-9][0-9][0-9]' = 0
              )
            """,
        ).fetchone()[0]
    )

    deletion_review_candidates = {
        "duplicate_canonical_extra_rows": _duplicate_message_id_stats(
            conn,
            cohort_label="canonical_gmail",
            where_sql=sql_predicate_contacto_gmail_source(table_alias="e", coalesce_null=False),
        ).get("duplicate_extra_rows", 0),
        "legacy_labdelivery_rows": source_tiers.get("legacy_labdelivery", 0),
        "historical_only_unreferenced_rows": historical_only,
        "orphan_attachment_rows": orphan_attachments,
        "invalid_date_iso_rows": invalid_date_rows,
        "policy": "Age alone never classifies rows as deletable; all counts require human review.",
    }

    return {
        "phase": "usefulness_classification",
        "exact": True,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "source_tier_counts": source_tiers,
        "referenced_email_id_count": referenced,
        "historical_only_email_rows": historical_only,
        "recomputable_mart_table_counts": mart_counts,
        "commercial_table_counts": commercial_counts,
        "orphan_attachment_rows": orphan_attachments,
        "invalid_date_iso_rows": invalid_date_rows,
        "deletion_review_candidates": deletion_review_candidates,
    }


def build_conclusions(report: dict[str, Any]) -> dict[str, Any]:
    structural = report.get("phases", {}).get("structural_quick", {})
    physical = report.get("phases", {}).get("physical_dbstat", {})
    column_bytes = report.get("phases", {}).get("column_bytes", {})
    duplicates = report.get("phases", {}).get("duplicate_analysis", {})
    usefulness = report.get("phases", {}).get("usefulness_classification", {})

    why_large: list[str] = []
    if structural:
        why_large.append(
            f"Allocated pages ~{structural.get('allocated_bytes', 0) / GiB:.4f} GiB "
            f"with freelist ~{structural.get('freelist_bytes', 0) / GiB:.4f} GiB."
        )
    body_cols = column_bytes.get("emails_body_columns") or []
    if body_cols:
        top = sorted(body_cols, key=lambda x: x.get("aggregate_bytes", 0), reverse=True)[:3]
        for item in top:
            why_large.append(
                f"emails.{item['column']} aggregate ~{item['aggregate_bytes'] / GiB:.4f} GiB "
                f"(avg {item['avg_bytes']} B, max {item['max_bytes']} B)."
            )
    if physical.get("tables_top"):
        t0 = physical["tables_top"][0]
        why_large.append(
            f"dbstat top table/index object {t0['name']} ~{t0['allocated_bytes'] / GiB:.4f} GiB."
        )

    corruption = bool(
        structural.get("structural_corruption_detected")
        or (report.get("phases", {}).get("structural_full") or {}).get(
            "structural_corruption_detected"
        )
    )

    dup_exists = any(
        (c.get("duplicate_message_id_groups") or 0) > 0
        for c in (duplicates.get("message_id_cohorts") or [])
    )
    att = duplicates.get("attachment_sha256") or {}
    if (att.get("duplicate_sha256_groups") or 0) > 0:
        dup_exists = True

    operational = usefulness.get("source_tier_counts", {}).get("canonical_gmail", 0)
    savings_estimates: list[str] = []
    for cohort in duplicates.get("message_id_cohorts") or []:
        est = cohort.get("estimated_repeated_body_bytes") or 0
        if est:
            savings_estimates.append(
                f"Estimate: {cohort['cohort']} repeated body representations ~{est / GiB:.4f} GiB "
                "(duplicate rows beyond first per message_id)."
            )
    if structural.get("freelist_bytes"):
        savings_estimates.append(
            f"Estimate: freelist pages ~{structural['freelist_bytes'] / GiB:.4f} GiB are reusable "
            "inside the file; compaction would require an offline plan."
        )
    if att.get("estimated_duplicate_bytes"):
        savings_estimates.append(
            f"Estimate: duplicate attachment sha256 groups ~{att['estimated_duplicate_bytes'] / GiB:.4f} GiB."
        )

    return {
        "why_file_is_large": why_large,
        "structural_corruption": corruption,
        "duplication_present": dup_exists,
        "operationally_used_rows": {
            "canonical_gmail_rows": operational,
            "referenced_email_ids": usefulness.get("referenced_email_id_count"),
        },
        "possible_space_savings_estimates": savings_estimates,
        "all_estimates_require_human_review": True,
    }


def render_markdown_summary(report: dict[str, Any]) -> str:
    conclusions = report.get("conclusions") or {}
    lines = [
        "# SQLite deep forensic audit (sanitized)",
        "",
        f"- Database: `{report.get('database_basename')}`",
        f"- Captured: {report.get('captured_at_utc')}",
        f"- Read-only: {report.get('read_only')}",
        f"- Offline copy confirmed: {report.get('confirm_offline_copy')}",
        f"- Production path refused for heavy phases: {report.get('production_path')}",
        "",
        "## Phase timings",
    ]
    for name, phase in sorted((report.get("phases") or {}).items()):
        if not isinstance(phase, dict):
            continue
        exact = phase.get("exact", True)
        lines.append(
            f"- `{name}`: {phase.get('elapsed_seconds', '?')}s "
            f"({'exact' if exact else 'sampled'}) status={phase.get('status', 'completed')}"
        )
    lines.extend(["", "## Conclusions", ""])
    lines.append("### Why the file is large")
    for item in conclusions.get("why_file_is_large") or ["(no attribution data)"]:
        lines.append(f"- {item}")
    lines.append("")
    lines.append(
        f"### Structural corruption: **{'yes' if conclusions.get('structural_corruption') else 'no'}**"
    )
    lines.append(
        f"### Duplication present: **{'yes' if conclusions.get('duplication_present') else 'no'}**"
    )
    lines.append("### Operationally used")
    op = conclusions.get("operationally_used_rows") or {}
    lines.append(f"- canonical_gmail_rows: {op.get('canonical_gmail_rows')}")
    lines.append(f"- referenced_email_ids: {op.get('referenced_email_ids')}")
    lines.append("")
    lines.append("### Possible space savings (estimates only)")
    for item in conclusions.get("possible_space_savings_estimates") or ["(none computed)"]:
        lines.append(f"- {item}")
    lines.append("")
    lines.append(
        "> Estimates are not deletion approval. Age alone never classifies data as deletable."
    )
    return "\n".join(lines) + "\n"


def scan_for_pii_leaks(payload: Any, *, path: str = "$") -> list[str]:
    violations: list[str] = []

    def walk(obj: Any, p: str) -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                key_s = str(key)
                if key_s in PII_FIELD_NAMES:
                    violations.append(f"{p}.{key_s}: forbidden field present")
                walk(value, f"{p}.{key_s}")
        elif isinstance(obj, list):
            for idx, item in enumerate(obj):
                walk(item, f"{p}[{idx}]")
        elif isinstance(obj, str):
            for pattern in PII_OUTPUT_PATTERNS:
                if pattern.search(obj):
                    violations.append(f"{p}: matched privacy pattern {pattern.pattern}")
                    break

    walk(payload, path)
    return violations


def load_checkpoint(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_checkpoint(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_audit(
    options: AuditOptions,
    *,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    err = validate_heavy_access(options)
    if err:
        raise PermissionError(err)

    if not options.db.is_file():
        raise FileNotFoundError(f"SQLite not found: {options.db}")

    output_dir = options.output_dir or (options.db.parent / "sqlite_deep_audit_out")
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = output_dir / "audit_sqlite_deep_checkpoint.json"
    checkpoint = load_checkpoint(checkpoint_path) if options.resume else {}
    completed_phases = {
        k
        for k, v in (checkpoint.get("phases") or {}).items()
        if isinstance(v, dict) and v.get("status") == "completed"
    }

    before_fp = fingerprint_db_files(options.db)
    report: dict[str, Any] = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "captured_at_utc": _iso_now(),
        "database_basename": options.db.name,
        "read_only": True,
        "mutation": False,
        "confirm_offline_copy": options.confirm_offline_copy,
        "production_path": is_configured_production_db(options.db, options.settings),
        "file_fingerprint_before": {
            k: (v.to_dict() if v else None) for k, v in before_fp.items()
        },
        "phases": dict(checkpoint.get("phases") or {}),
    }

    phases_to_run = list(options.phases)
    if options.full_integrity_check and "structural_full" not in phases_to_run:
        phases_to_run.append("structural_full")

    conn = connect_readonly(options.db)
    try:
        for phase_name in phases_to_run:
            if phase_name in completed_phases:
                continue
            started = clock()
            try:
                if phase_name == "structural_quick":
                    result = run_structural_quick(conn, options.db)
                elif phase_name == "structural_full":
                    result = run_structural_full(conn)
                elif phase_name == "physical_dbstat":
                    result = run_physical_dbstat(conn)
                elif phase_name == "column_bytes":
                    result = run_column_bytes(conn)
                elif phase_name == "duplicate_analysis":
                    result = run_duplicate_analysis(conn)
                elif phase_name == "usefulness_classification":
                    result = run_usefulness_classification(conn)
                elif phase_name == "summarize":
                    continue
                else:
                    raise ValueError(f"unknown phase: {phase_name}")
                result["status"] = "completed"
                result["elapsed_seconds"] = round(clock() - started, 3)
                report["phases"][phase_name] = result
                checkpoint["phases"] = report["phases"]
                checkpoint["database_basename"] = options.db.name
                save_checkpoint(checkpoint_path, checkpoint)
            except sqlite3.Error as exc:
                report["phases"][phase_name] = {
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                    "elapsed_seconds": round(clock() - started, 3),
                }
                raise
    finally:
        conn.close()

    report["conclusions"] = build_conclusions(report)
    after_fp = fingerprint_db_files(options.db)
    report["file_fingerprint_after"] = {
        k: (v.to_dict() if v else None) for k, v in after_fp.items()
    }
    if not fingerprints_equal(before_fp, after_fp):
        raise RuntimeError("database file mutation detected (size/mtime changed)")

    violations = scan_for_pii_leaks(report)
    if violations:
        raise RuntimeError(f"privacy leak detected: {violations[:5]}")

    report["privacy_scan_ok"] = True
    return report


def write_outputs(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "audit_sqlite_deep.json"
    md_path = output_dir / "audit_sqlite_deep.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown_summary(report), encoding="utf-8")
    return json_path, md_path
