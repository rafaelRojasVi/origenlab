"""Read-only, privacy-safe SQLite deep forensic audit (offline copy only for heavy phases)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from origenlab_email_pipeline.config import Settings, load_settings
from origenlab_email_pipeline.contacto_gmail_source import (
    CONTACTO_GMAIL_SOURCE_SQL_LIKE_VALUE,
    LEGACY_LABDELIVERY_SOURCE_LIKE,
    sql_predicate_contacto_gmail_source,
)

GiB = 1024**3
AUDIT_SCHEMA_VERSION = 2
MAX_VIOLATION_SAMPLES = 20

TriState = Literal["yes", "no", "not_assessed"]

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
        r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        r"(?:^|[\s\"'])(?:/home|/mnt|/var|/tmp|/Users|/opt)[^\s\"']+",
        r"(?:^|[\s\"'])[A-Za-z]:\\[^\s\"']+",
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

# structural_full is opt-in via --full-integrity-check only.
DEFAULT_PHASE_NAMES: frozenset[str] = frozenset(
    {
        "structural_quick",
        "physical_dbstat",
        "column_bytes",
        "duplicate_analysis",
        "usefulness_classification",
    }
)

PRODUCTION_LIGHT_PHASE_NAMES: frozenset[str] = frozenset({"structural_light"})

HEAVY_OFFLINE_PHASE_NAMES: frozenset[str] = frozenset(
    {
        "structural_quick",
        "physical_dbstat",
        "column_bytes",
        "duplicate_analysis",
        "usefulness_classification",
        "structural_full",
    }
)

ALL_PHASE_NAMES: frozenset[str] = (
    DEFAULT_PHASE_NAMES | PRODUCTION_LIGHT_PHASE_NAMES | frozenset({"structural_full", "summarize"})
)

PHASE_ORDER: tuple[str, ...] = (
    "structural_light",
    "structural_quick",
    "structural_full",
    "physical_dbstat",
    "column_bytes",
    "duplicate_analysis",
    "usefulness_classification",
)

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
    phases: frozenset[str] = field(default_factory=lambda: frozenset(DEFAULT_PHASE_NAMES))
    full_integrity_check: bool = False
    resume: bool = False
    output_dir: Path | None = None
    settings: Settings | None = None


@dataclass
class FileFingerprint:
    size_bytes: int
    mtime_ns: int
    device: int | None = None
    inode: int | None = None

    def to_dict(self) -> dict[str, int | None]:
        return {
            "size_bytes": self.size_bytes,
            "mtime_ns": self.mtime_ns,
            "device": self.device,
            "inode": self.inode,
        }


def _iso_now(now: datetime | None = None) -> str:
    dt = now or datetime.now(timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def resolved_production_sqlite_path(settings: Settings | None = None) -> Path:
    return (settings or load_settings()).resolved_sqlite_path().resolve()


def _stat_fingerprint(path: Path) -> FileFingerprint | None:
    if not path.is_file():
        return None
    st = path.stat()
    return FileFingerprint(
        size_bytes=int(st.st_size),
        mtime_ns=int(st.st_mtime_ns),
        device=int(st.st_dev),
        inode=int(st.st_ino),
    )


def is_configured_production_db(db: Path, settings: Settings | None = None) -> bool:
    prod = resolved_production_sqlite_path(settings)
    db_resolved = db.resolve()
    if db_resolved == prod:
        return True
    try:
        if os.path.samefile(db_resolved, prod):
            return True
    except OSError:
        pass
    db_fp = _stat_fingerprint(db_resolved)
    prod_fp = _stat_fingerprint(prod)
    if (
        db_fp is not None
        and prod_fp is not None
        and db_fp.device is not None
        and db_fp.inode is not None
        and db_fp.device == prod_fp.device
        and db_fp.inode == prod_fp.inode
    ):
        return True
    return False


def quote_identifier(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


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
        out[label] = _stat_fingerprint(path)
    return out


def fingerprints_equal(
    before: dict[str, FileFingerprint | None],
    after: dict[str, FileFingerprint | None],
) -> bool:
    return before == after


def _offline_phases_requested(options: AuditOptions) -> frozenset[str]:
    requested = set(options.phases)
    if options.full_integrity_check:
        requested.add("structural_full")
    return frozenset(requested & HEAVY_OFFLINE_PHASE_NAMES)


def validate_audit_access(options: AuditOptions) -> str | None:
    requested = set(options.phases)
    if options.full_integrity_check:
        requested.add("structural_full")

    offline_requested = bool(requested & HEAVY_OFFLINE_PHASE_NAMES)
    only_production_light = requested <= PRODUCTION_LIGHT_PHASE_NAMES

    if only_production_light and not offline_requested:
        if is_configured_production_db(options.db, options.settings) and not options.confirm_offline_copy:
            return None
        return None

    if offline_requested:
        if is_configured_production_db(options.db, options.settings):
            return (
                "offline audit phases refused: --db resolves to the configured production SQLite path "
                "(including samefile/device+inode aliases). Use a verified offline copy on separate storage."
            )
        if not options.confirm_offline_copy:
            return (
                "offline audit phases require --confirm-offline-copy acknowledging this is a "
                "verified offline/backup copy, not live production SQLite."
            )
    return None


def validate_heavy_access(options: AuditOptions) -> str | None:
    """Backward-compatible alias used by tests/CLI."""
    return validate_audit_access(options)


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


def _schema_inventory(conn: sqlite3.Connection) -> dict[str, Any]:
    tables = _table_names(conn)
    index_rows = execute_ro(
        conn,
        """
        SELECT name, tbl_name, sql
        FROM sqlite_master
        WHERE type='index' AND name NOT LIKE 'sqlite_%'
        ORDER BY tbl_name, name
        """,
    ).fetchall()
    indexes = [
        {
            "name": r["name"],
            "table": r["tbl_name"],
            "sql_present": bool(r["sql"]),
        }
        for r in index_rows
    ]
    return {"tables": tables, "indexes": indexes}


def _constant_time_storage_pragmas(conn: sqlite3.Connection) -> dict[str, Any]:
    page_size = int(execute_ro(conn, "PRAGMA page_size").fetchone()[0])
    page_count = int(execute_ro(conn, "PRAGMA page_count").fetchone()[0])
    freelist_count = int(execute_ro(conn, "PRAGMA freelist_count").fetchone()[0])
    journal_mode = str(execute_ro(conn, "PRAGMA journal_mode").fetchone()[0])
    auto_vacuum = int(execute_ro(conn, "PRAGMA auto_vacuum").fetchone()[0])
    return {
        "page_size": page_size,
        "page_count": page_count,
        "freelist_count": freelist_count,
        "allocated_bytes": page_size * page_count,
        "freelist_bytes": page_size * freelist_count,
        "journal_mode": journal_mode,
        "auto_vacuum": auto_vacuum,
    }


def run_structural_light(conn: sqlite3.Connection, db: Path) -> dict[str, Any]:
    started = time.perf_counter()
    pragmas = _constant_time_storage_pragmas(conn)
    inventory = _schema_inventory(conn)
    return {
        "phase": "structural_light",
        "exact": True,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "database_basename": db.name,
        **pragmas,
        **inventory,
        "note": (
            "Production-safe light mode: constant-time storage PRAGMAs and sqlite_master "
            "inventory only. No quick_check, foreign_key_check, or table COUNT scans."
        ),
    }


def _row_count_and_id_range(conn: sqlite3.Connection, table: str) -> dict[str, Any]:
    if not _has_table(conn, table):
        return {"exists": False}
    qtable = quote_identifier(table)
    count = int(execute_ro(conn, f"SELECT COUNT(*) FROM {qtable}").fetchone()[0])
    id_range: dict[str, Any] = {"min_id": None, "max_id": None}
    cols = {str(r[1]) for r in execute_ro(conn, f"PRAGMA table_info({qtable})")}
    if "id" in cols:
        row = execute_ro(conn, f"SELECT MIN(id), MAX(id) FROM {qtable}").fetchone()
        id_range = {"min_id": row[0], "max_id": row[1]}
    return {"exists": True, "row_count": count, **id_range}


def run_structural_quick(conn: sqlite3.Connection, db: Path) -> dict[str, Any]:
    started = time.perf_counter()
    quick = str(execute_ro(conn, "PRAGMA quick_check").fetchone()[0])
    fk_rows = execute_ro(conn, "PRAGMA foreign_key_check").fetchall()
    fk_total = len(fk_rows)
    fk_violations = [
        {
            "table": r[0],
            "rowid": r[1],
            "parent": r[2],
            "fk_index": r[3],
        }
        for r in fk_rows[:MAX_VIOLATION_SAMPLES]
    ]
    pragmas = _constant_time_storage_pragmas(conn)
    inventory = _schema_inventory(conn)
    table_stats: dict[str, Any] = {}
    for name in inventory["tables"]:
        table_stats[name] = _row_count_and_id_range(conn, name)

    return {
        "phase": "structural_quick",
        "exact": True,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "quick_check": quick,
        "quick_check_ok": quick == "ok",
        "foreign_key_violations_sample": fk_violations,
        "foreign_key_violation_count": fk_total,
        "foreign_key_violations_truncated": fk_total > MAX_VIOLATION_SAMPLES,
        "database_basename": db.name,
        "table_stats": table_stats,
        **pragmas,
        **inventory,
    }


def run_structural_full(conn: sqlite3.Connection) -> dict[str, Any]:
    started = time.perf_counter()
    rows = execute_ro(conn, "PRAGMA integrity_check").fetchall()
    messages = [str(r[0]) for r in rows]
    total_messages = len(messages)
    ok = total_messages == 1 and messages[0].lower() == "ok"
    sample = messages if ok else messages[:MAX_VIOLATION_SAMPLES]
    return {
        "phase": "structural_full",
        "exact": True,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "integrity_check_ok": ok,
        "integrity_message_count": total_messages,
        "integrity_messages_sample": sample,
        "integrity_messages_truncated": (not ok) and total_messages > MAX_VIOLATION_SAMPLES,
        "warning": "PRAGMA integrity_check can take hours on multi-GiB databases.",
    }


def _dbstat_object_type(name: str, master_type: str | None) -> str:
    if name.startswith("sqlite_autoindex"):
        return "autoindex"
    if master_type == "index":
        return "index"
    if master_type == "table":
        return "table"
    return "internal"


def run_physical_dbstat(conn: sqlite3.Connection) -> dict[str, Any]:
    started = time.perf_counter()
    by_object: list[dict[str, Any]] = []
    total_allocated = 0
    cur = execute_ro(
        conn,
        """
        SELECT
          d.name AS name,
          SUM(d.pgsize) AS allocated_bytes,
          m.type AS master_type
        FROM dbstat AS d
        LEFT JOIN sqlite_master AS m ON m.name = d.name
        GROUP BY d.name, m.type
        ORDER BY allocated_bytes DESC
        """,
    )
    for row in cur:
        nbytes = int(row["allocated_bytes"] or 0)
        total_allocated += nbytes
        kind = _dbstat_object_type(str(row["name"]), row["master_type"])
        by_object.append(
            {
                "name": row["name"],
                "allocated_bytes": nbytes,
                "allocated_gib": round(nbytes / GiB, 6),
                "kind": kind,
                "master_type": row["master_type"],
            }
        )

    pragmas = _constant_time_storage_pragmas(conn)
    file_bytes = int(pragmas["allocated_bytes"])
    freelist_bytes = int(pragmas["freelist_bytes"])
    active_bytes = max(0, file_bytes - freelist_bytes)
    reconcile_delta = file_bytes - (total_allocated + freelist_bytes)

    return {
        "phase": "physical_dbstat",
        "exact": True,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "objects": by_object,
        "tables_top": [o for o in by_object if o["kind"] == "table"][:25],
        "indexes_top": [o for o in by_object if o["kind"] == "index"][:25],
        "autoindexes_top": [o for o in by_object if o["kind"] == "autoindex"][:25],
        "reconciliation": {
            "page_file_bytes": file_bytes,
            "dbstat_object_bytes": total_allocated,
            "freelist_bytes": freelist_bytes,
            "active_bytes_estimate": active_bytes,
            "reconcile_delta_bytes": reconcile_delta,
        },
    }


def _blob_len_expr(column: str) -> str:
    qcol = quote_identifier(column)
    return f"COALESCE(length(CAST({qcol} AS BLOB)), 0)"


def _profile_text_column(conn: sqlite3.Connection, table: str, column: str) -> dict[str, Any] | None:
    if not _has_table(conn, table):
        return None
    qtable = quote_identifier(table)
    cols = {str(r[1]) for r in execute_ro(conn, f"PRAGMA table_info({qtable})")}
    if column not in cols:
        return None
    qcol = quote_identifier(column)
    row = execute_ro(
        conn,
        f"""
        SELECT
          COUNT(*) AS row_count,
          SUM(CASE WHEN {qcol} IS NULL OR length({qcol}) = 0 THEN 1 ELSE 0 END) AS null_or_empty_count,
          SUM({_blob_len_expr(column)}) AS aggregate_bytes,
          AVG({_blob_len_expr(column)}) AS avg_bytes,
          MAX({_blob_len_expr(column)}) AS max_bytes
        FROM {qtable}
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


def _aggregate_within_row_redundant_body_bytes(conn: sqlite3.Connection) -> dict[str, Any]:
    if not _has_table(conn, "emails"):
        return {"row_count": 0, "aggregate_redundant_bytes": 0, "exact": True}
    col_list = ", ".join(EMAIL_BODY_COLUMNS)
    cur = execute_ro(conn, f"SELECT {col_list} FROM emails")
    redundant_total = 0
    row_count = 0
    for row in cur:
        row_count += 1
        fingerprints: list[tuple[int, str]] = []
        total_len = 0
        for col in EMAIL_BODY_COLUMNS:
            value = row[col]
            if value is None:
                continue
            blob = value if isinstance(value, bytes) else str(value).encode("utf-8")
            if not blob:
                continue
            total_len += len(blob)
            fingerprints.append((len(blob), hashlib.sha256(blob).hexdigest()))
        if not fingerprints:
            continue
        unique = {(length, digest) for length, digest in fingerprints}
        unique_len = sum(length for length, _ in unique)
        redundant_total += max(0, total_len - unique_len)
    return {
        "row_count": row_count,
        "aggregate_redundant_bytes": redundant_total,
        "exact": True,
        "note": "Within-row duplicate representations across the six body columns (aggregate only).",
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
    within_row = _aggregate_within_row_redundant_body_bytes(conn)
    return {
        "phase": "column_bytes",
        "exact": True,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "emails_body_columns": email_profiles,
        "attachment_extract_text_columns": extract_profiles,
        "within_row_body_redundancy": within_row,
    }


def _body_fingerprint_expr() -> str:
    parts = []
    for col in EMAIL_BODY_COLUMNS:
        qcol = quote_identifier(col)
        parts.append(
            f"printf('%d:', {_blob_len_expr(col)}) || COALESCE(CAST({qcol} AS BLOB), x'')"
        )
    return " || x'00' || ".join(parts)


def _body_bytes_sum_expr(alias: str = "e") -> str:
    return " + ".join(
        f"COALESCE(length(CAST({alias}.{quote_identifier(col)} AS BLOB)), 0)"
        for col in EMAIL_BODY_COLUMNS
    )


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

    # True SHA-256 fingerprints for duplicate message_id groups only.
    fingerprint_groups: dict[str, set[str]] = {}
    cur = execute_ro(
        conn,
        f"""
        SELECT lower(trim(e.message_id)) AS mid, e.id,
               {_body_fingerprint_expr()} AS payload
        FROM emails e
        WHERE {where_sql}
          AND e.message_id IS NOT NULL AND trim(e.message_id) != ''
          AND lower(trim(e.message_id)) IN (
            SELECT lower(trim(message_id))
            FROM emails
            WHERE message_id IS NOT NULL AND trim(message_id) != ''
            GROUP BY lower(trim(message_id))
            HAVING COUNT(*) > 1
          )
        ORDER BY mid, e.id
        """,
    )
    for row in cur:
        mid = str(row["mid"])
        payload = row["payload"]
        if isinstance(payload, memoryview):
            payload = bytes(payload)
        elif isinstance(payload, str):
            payload = payload.encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        fingerprint_groups.setdefault(mid, set()).add(digest)

    sha_exact_groups = sum(1 for fps in fingerprint_groups.values() if len(fps) == 1)
    sha_mixed_content_groups = sum(1 for fps in fingerprint_groups.values() if len(fps) > 1)

    repeated_body_bytes = execute_ro(
        conn,
        f"""
        SELECT SUM(extra_bytes) FROM (
          SELECT
            SUM({_body_bytes_sum_expr('e')}) - MIN({_body_bytes_sum_expr('e')}) AS extra_bytes
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
        "sha256_exact_duplicate_groups": sha_exact_groups,
        "sha256_same_length_different_content_groups": sha_mixed_content_groups,
        "estimated_repeated_body_bytes_in_sqlite": int(repeated_body_bytes or 0),
        "note": (
            "duplicate_message_id_groups counts repeated IDs only. "
            "sha256_exact_duplicate_groups requires identical SHA-256 fingerprints over "
            "length-prefixed body column values. Same-length different content remains a "
            "repeated message_id, not an exact duplicate."
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
              SUM((cnt - 1) * COALESCE(avg_size, 0)) AS duplicate_external_payload_bytes
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
                "duplicate_external_payload_bytes": int(row["duplicate_external_payload_bytes"] or 0),
                "interpretation": (
                    "attachments.size_bytes describes external attachment payload size, not bytes "
                    "stored inside SQLite. Reported for external-payload duplication only — never "
                    "counted as estimated SQLite file savings."
                ),
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
    qtable = quote_identifier(table)
    cols = {str(r[1]) for r in execute_ro(conn, f"PRAGMA table_info({qtable})")}
    return column in cols


def discover_email_reference_sources(conn: sqlite3.Connection) -> list[dict[str, str]]:
    discovered: list[dict[str, str]] = []
    candidates: list[tuple[str, str, str]] = [
        ("attachments", "email_id", "attachment_linkage"),
        ("document_master", "email_id", "mart_reference"),
        ("opportunity_signals", "email_id", "mart_reference"),
        ("commercial_email_signal_fact", "email_id", "commercial_reference"),
        ("commercial_deal_evidence", "email_id", "commercial_reference"),
        ("commercial_deal_evidence", "source_email_id", "commercial_reference"),
        ("commercial_deal_event", "source_email_id", "commercial_reference"),
        ("commercial_purchase_events", "source_email_id", "commercial_reference"),
    ]
    for table, column, role in candidates:
        if _table_has_column(conn, table, column):
            discovered.append({"table": table, "column": column, "role": role})
    return discovered


def _referenced_email_ids_sql(conn: sqlite3.Connection) -> tuple[str | None, list[dict[str, str]]]:
    discovered = discover_email_reference_sources(conn)
    parts = [
        f"SELECT {quote_identifier(item['column'])} AS email_id FROM {quote_identifier(item['table'])} "
        f"WHERE {quote_identifier(item['column'])} IS NOT NULL"
        for item in discovered
    ]
    if not parts:
        return None, discovered
    return " UNION ".join(parts), discovered


def _cohort_body_bytes(conn: sqlite3.Connection, where_sql: str) -> int:
    if not _has_table(conn, "emails"):
        return 0
    row = execute_ro(
        conn,
        f"SELECT SUM({_body_bytes_sum_expr('e')}) FROM emails e WHERE {where_sql}",
    ).fetchone()
    return int(row[0] or 0)


def run_usefulness_classification(conn: sqlite3.Connection) -> dict[str, Any]:
    started = time.perf_counter()
    if not _has_table(conn, "emails"):
        return {"phase": "usefulness_classification", "exists": False, "exact": True}

    tier_rows = execute_ro(
        conn,
        f"""
        SELECT tier, COUNT(*) AS row_count,
               SUM(body_bytes) AS aggregate_body_bytes
        FROM (
          SELECT {SOURCE_TIER_CASE_SQL} AS tier,
                 {_body_bytes_sum_expr('e')} AS body_bytes
          FROM emails e
        )
        GROUP BY tier
        ORDER BY row_count DESC
        """,
    ).fetchall()
    source_tiers = {
        str(r["tier"]): {
            "row_count": int(r["row_count"]),
            "aggregate_body_bytes": int(r["aggregate_body_bytes"] or 0),
        }
        for r in tier_rows
    }

    ref_sql, discovered_refs = _referenced_email_ids_sql(conn)
    total_emails = int(execute_ro(conn, "SELECT COUNT(*) FROM emails").fetchone()[0])
    referenced = 0
    historical_only = total_emails
    attachment_linked = 0
    operational_refs = 0

    if ref_sql:
        referenced = int(
            execute_ro(conn, f"SELECT COUNT(DISTINCT email_id) FROM ({ref_sql})").fetchone()[0]
        )
        historical_only = int(
            execute_ro(
                conn,
                f"SELECT COUNT(*) FROM emails e WHERE e.id NOT IN (SELECT email_id FROM ({ref_sql}))",
            ).fetchone()[0]
        )
        attachment_only_parts = [
            f"SELECT {quote_identifier(item['column'])} AS email_id FROM {quote_identifier(item['table'])} "
            f"WHERE {quote_identifier(item['column'])} IS NOT NULL"
            for item in discovered_refs
            if item["role"] == "attachment_linkage"
        ]
        operational_parts = [
            f"SELECT {quote_identifier(item['column'])} AS email_id FROM {quote_identifier(item['table'])} "
            f"WHERE {quote_identifier(item['column'])} IS NOT NULL"
            for item in discovered_refs
            if item["role"] != "attachment_linkage"
        ]
        if attachment_only_parts:
            attachment_linked = int(
                execute_ro(
                    conn,
                    f"SELECT COUNT(DISTINCT email_id) FROM ({' UNION '.join(attachment_only_parts)})",
                ).fetchone()[0]
            )
        if operational_parts:
            operational_refs = int(
                execute_ro(
                    conn,
                    f"SELECT COUNT(DISTINCT email_id) FROM ({' UNION '.join(operational_parts)})",
                ).fetchone()[0]
            )

    canonical_where = sql_predicate_contacto_gmail_source(table_alias="e", coalesce_null=False)
    cohort_bytes = {
        "canonical_gmail": _cohort_body_bytes(conn, canonical_where),
        "legacy_labdelivery": _cohort_body_bytes(
            conn, "lower(e.source_file) LIKE '%contacto@labdelivery%'"
        ),
        "referenced_by_discovered_tables": _cohort_body_bytes(
            conn, f"e.id IN (SELECT email_id FROM ({ref_sql}))" if ref_sql else "0"
        ),
        "historical_unreferenced": _cohort_body_bytes(
            conn,
            f"e.id NOT IN (SELECT email_id FROM ({ref_sql}))" if ref_sql else "1=1",
        ),
    }

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
            qtable = quote_identifier(table)
            mart_counts[table] = int(execute_ro(conn, f"SELECT COUNT(*) FROM {qtable}").fetchone()[0])

    commercial_counts: dict[str, int] = {}
    for table in COMMERCIAL_TABLES:
        if _has_table(conn, table):
            qtable = quote_identifier(table)
            commercial_counts[table] = int(
                execute_ro(conn, f"SELECT COUNT(*) FROM {qtable}").fetchone()[0]
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

    canonical_stats = _duplicate_message_id_stats(
        conn,
        cohort_label="canonical_gmail",
        where_sql=canonical_where,
    )
    deletion_review_candidates = {
        "duplicate_canonical_extra_rows": canonical_stats.get("duplicate_extra_rows", 0),
        "legacy_labdelivery_rows": source_tiers.get("legacy_labdelivery", {}).get("row_count", 0),
        "historical_only_unreferenced_rows": historical_only,
        "orphan_attachment_rows": orphan_attachments,
        "invalid_date_iso_rows": invalid_date_rows,
        "policy": (
            "Age or lack of references never classifies rows as automatically deletable; "
            "all counts require human review."
        ),
    }

    return {
        "phase": "usefulness_classification",
        "exact": True,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "source_tier_counts": source_tiers,
        "cohort_aggregate_body_bytes": cohort_bytes,
        "discovered_reference_sources": discovered_refs,
        "referenced_email_id_count": referenced,
        "attachment_linked_email_id_count": attachment_linked,
        "operational_reference_email_id_count": operational_refs,
        "historical_only_email_rows": historical_only,
        "recomputable_mart_table_counts": mart_counts,
        "commercial_table_counts": commercial_counts,
        "orphan_attachment_rows": orphan_attachments,
        "invalid_date_iso_rows": invalid_date_rows,
        "deletion_review_candidates": deletion_review_candidates,
    }


def _phase_status(report: dict[str, Any], phase: str) -> str:
    phase_data = (report.get("phases") or {}).get(phase)
    if not isinstance(phase_data, dict):
        return "not_run"
    return str(phase_data.get("status", "completed"))


def build_conclusions(report: dict[str, Any]) -> dict[str, Any]:
    phases = report.get("phases") or {}
    structural_light = phases.get("structural_light") or {}
    structural_quick = phases.get("structural_quick") or {}
    structural_full = phases.get("structural_full") or {}
    physical = phases.get("physical_dbstat") or {}
    column_bytes = phases.get("column_bytes") or {}
    duplicates = phases.get("duplicate_analysis") or {}
    usefulness = phases.get("usefulness_classification") or {}

    why_large: list[str] = []
    if structural_light or structural_quick:
        alloc = (structural_quick or structural_light).get("allocated_bytes", 0)
        freelist = (structural_quick or structural_light).get("freelist_bytes", 0)
        why_large.append(
            f"Allocated pages ~{alloc / GiB:.4f} GiB with freelist ~{freelist / GiB:.4f} GiB."
        )
    body_cols = column_bytes.get("emails_body_columns") or []
    if body_cols:
        top = sorted(body_cols, key=lambda x: x.get("aggregate_bytes", 0), reverse=True)[:3]
        for item in top:
            why_large.append(
                f"emails.{item['column']} aggregate ~{item['aggregate_bytes'] / GiB:.4f} GiB "
                f"(avg {item['avg_bytes']} B, max {item['max_bytes']} B)."
            )
    within_row = column_bytes.get("within_row_body_redundancy") or {}
    if within_row.get("aggregate_redundant_bytes"):
        why_large.append(
            "Within-row duplicate body-column representations aggregate ~"
            f"{within_row['aggregate_redundant_bytes'] / GiB:.4f} GiB."
        )
    if physical.get("tables_top"):
        t0 = physical["tables_top"][0]
        why_large.append(
            f"dbstat top table object {t0['name']} ~{t0['allocated_bytes'] / GiB:.4f} GiB."
        )
    tier_bytes = usefulness.get("cohort_aggregate_body_bytes") or {}
    if tier_bytes.get("canonical_gmail"):
        why_large.append(
            f"canonical_gmail body bytes ~{tier_bytes['canonical_gmail'] / GiB:.4f} GiB."
        )

    sqlite_integrity: TriState = "not_assessed"
    if _phase_status(report, "structural_full") == "completed":
        sqlite_integrity = "no" if structural_full.get("integrity_check_ok") else "yes"
    elif _phase_status(report, "structural_full") == "failed":
        sqlite_integrity = "not_assessed"

    foreign_key_violations: TriState = "not_assessed"
    if _phase_status(report, "structural_quick") == "completed":
        fk_count = int(structural_quick.get("foreign_key_violation_count") or 0)
        foreign_key_violations = "yes" if fk_count else "no"
    elif _phase_status(report, "structural_quick") == "failed":
        foreign_key_violations = "not_assessed"

    quick_check: TriState = "not_assessed"
    if _phase_status(report, "structural_quick") == "completed":
        quick_check = "no" if structural_quick.get("quick_check_ok") else "yes"

    duplication_present: TriState = "not_assessed"
    if _phase_status(report, "duplicate_analysis") == "completed":
        dup_exists = any(
            (c.get("duplicate_message_id_groups") or 0) > 0
            for c in (duplicates.get("message_id_cohorts") or [])
        )
        att = duplicates.get("attachment_sha256") or {}
        if (att.get("duplicate_sha256_groups") or 0) > 0:
            dup_exists = True
        duplication_present = "yes" if dup_exists else "no"

    savings_estimates: list[str] = []
    for cohort in duplicates.get("message_id_cohorts") or []:
        est = cohort.get("estimated_repeated_body_bytes_in_sqlite") or 0
        if est:
            savings_estimates.append(
                f"Estimate: {cohort['cohort']} repeated SQLite body bytes ~{est / GiB:.4f} GiB "
                "(duplicate rows beyond first per message_id)."
            )
    freelist = (structural_quick or structural_light).get("freelist_bytes")
    if freelist:
        savings_estimates.append(
            f"Estimate: freelist pages ~{freelist / GiB:.4f} GiB are reusable inside the file; "
            "compaction would require an offline plan."
        )

    return {
        "why_file_is_large": why_large,
        "sqlite_integrity_failure": sqlite_integrity,
        "foreign_key_violations": foreign_key_violations,
        "quick_check_failure": quick_check,
        "duplication_present": duplication_present,
        "operationally_used_rows": {
            "canonical_gmail_rows": (usefulness.get("source_tier_counts") or {})
            .get("canonical_gmail", {})
            .get("row_count"),
            "canonical_gmail_body_bytes": tier_bytes.get("canonical_gmail"),
            "referenced_email_ids": usefulness.get("referenced_email_id_count"),
            "operational_reference_email_ids": usefulness.get("operational_reference_email_id_count"),
            "attachment_linked_email_ids": usefulness.get("attachment_linked_email_id_count"),
        },
        "possible_space_savings_estimates": savings_estimates,
        "all_estimates_require_human_review": True,
        "phase_status": {name: _phase_status(report, name) for name in PHASE_ORDER},
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
        f"- Production path: {report.get('production_path')}",
        "",
        "## Phase timings",
    ]
    for name in PHASE_ORDER:
        phase = (report.get("phases") or {}).get(name)
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
    lines.append(f"### SQLite integrity failure: **{conclusions.get('sqlite_integrity_failure')}**")
    lines.append(f"### Foreign-key violations: **{conclusions.get('foreign_key_violations')}**")
    lines.append(f"### Quick-check failure: **{conclusions.get('quick_check_failure')}**")
    lines.append(f"### Duplication present: **{conclusions.get('duplication_present')}**")
    lines.append("### Operationally used")
    op = conclusions.get("operationally_used_rows") or {}
    for key, value in op.items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    lines.append("### Possible space savings (estimates only)")
    for item in conclusions.get("possible_space_savings_estimates") or ["(none computed)"]:
        lines.append(f"- {item}")
    lines.append("")
    lines.append(
        "> Estimates are not deletion approval. Age or lack of references never classifies data as deletable."
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


def checkpoint_identity(options: AuditOptions) -> dict[str, Any]:
    fps = fingerprint_db_files(options.db)
    return {
        "audit_schema_version": AUDIT_SCHEMA_VERSION,
        "database_basename": options.db.name,
        "file_fingerprint": {k: (v.to_dict() if v else None) for k, v in fps.items()},
        "phases_requested": sorted(options.phases),
        "full_integrity_check": options.full_integrity_check,
        "confirm_offline_copy": options.confirm_offline_copy,
        "production_path": is_configured_production_db(options.db, options.settings),
    }


def load_checkpoint(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_checkpoint(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def validate_resume_checkpoint(checkpoint: dict[str, Any], options: AuditOptions) -> None:
    if not checkpoint:
        raise RuntimeError("resume requested but checkpoint is missing")
    expected = checkpoint_identity(options)
    stored = checkpoint.get("identity") or {}
    for key, value in expected.items():
        if stored.get(key) != value:
            raise RuntimeError(
                f"checkpoint identity mismatch for {key}: stored={stored.get(key)!r} expected={value!r}"
            )


def ordered_phases(requested: frozenset[str], *, full_integrity_check: bool) -> list[str]:
    phases = set(requested)
    if full_integrity_check:
        phases.add("structural_full")
    return [name for name in PHASE_ORDER if name in phases]


def run_audit(
    options: AuditOptions,
    *,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    err = validate_audit_access(options)
    if err:
        raise PermissionError(err)

    if not options.db.is_file():
        raise FileNotFoundError(f"SQLite not found: {options.db}")

    output_dir = options.output_dir or (options.db.parent / "sqlite_deep_audit_out")
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = output_dir / "audit_sqlite_deep_checkpoint.json"
    checkpoint = load_checkpoint(checkpoint_path) if options.resume else {}
    if options.resume:
        validate_resume_checkpoint(checkpoint, options)

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
        "phase_order": ordered_phases(options.phases, full_integrity_check=options.full_integrity_check),
    }

    conn = connect_readonly(options.db)
    try:
        for phase_name in report["phase_order"]:
            if phase_name in completed_phases:
                continue
            started = clock()
            try:
                if phase_name == "structural_light":
                    result = run_structural_light(conn, options.db)
                elif phase_name == "structural_quick":
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
                checkpoint["identity"] = checkpoint_identity(options)
                checkpoint["phases"] = report["phases"]
                save_checkpoint(checkpoint_path, checkpoint)
            except sqlite3.Error as exc:
                report["phases"][phase_name] = {
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                    "elapsed_seconds": round(clock() - started, 3),
                }
                checkpoint["identity"] = checkpoint_identity(options)
                checkpoint["phases"] = report["phases"]
                save_checkpoint(checkpoint_path, checkpoint)
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
