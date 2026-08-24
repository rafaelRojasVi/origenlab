"""Read-only SQL and DB fetch helpers for commercial intelligence v1 build.

Orchestration, roll-up aggregation, and writes stay in ``build_commercial_intel_v1.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

from origenlab_email_pipeline.warm_case_sender_rules import INTERNAL_OPERATOR_DOMAINS

SQL_COMMERCIAL_EMAIL_SIGNAL_FACT_FOR_ROLLUP = """
SELECT email_id, sent_at, contact_email, org_domain, signal_code, signal_kind, reason_code,
       confidence_score, strength_score
FROM commercial_email_signal_fact
""".strip()

SQL_ORG_ROLLUP_FOR_OPPORTUNITY_INSERT = """
SELECT org_domain, quote_signal_count, procurement_signal_count, technical_signal_count,
       repeated_interaction_count, confidence_score, strength_score, is_suppressed,
       suppression_reason_codes, evidence_email_count
FROM commercial_org_signal_rollup
WHERE evidence_email_count >= 2
""".strip()

SQL_ORG_ROLLUP_FOR_ORG_CANDIDATES = """
SELECT org_domain, confidence_score, strength_score, evidence_email_count, last_seen_at,
       suppression_reason_codes, is_suppressed, positive_signal_count
FROM commercial_org_signal_rollup
WHERE evidence_email_count >= 2
""".strip()

SQL_CONTACT_ROLLUP_FOR_CONTACT_CANDIDATES = """
SELECT contact_email, org_domain, confidence_score, strength_score, evidence_email_count, last_seen_at,
       suppression_reason_codes, is_suppressed, positive_signal_count
FROM commercial_contact_signal_rollup
WHERE evidence_email_count >= 2
""".strip()

SQL_OPPORTUNITY_FACT_FOR_OPP_CANDIDATES = """
SELECT opportunity_key, org_domain, confidence_score, strength_score, evidence_email_count, is_suppressed,
       suppression_summary, top_signal_codes
FROM commercial_opportunity_fact
WHERE evidence_email_count >= 2
""".strip()

SQL_CANDIDATE_SUPPRESSED_COUNTS = """
SELECT
  (SELECT COUNT(*) FROM organization_candidate WHERE status='suppressed'),
  (SELECT COUNT(*) FROM contact_candidate WHERE status='suppressed'),
  (SELECT COUNT(*) FROM opportunity_candidate WHERE status='suppressed')
""".strip()


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def derive_internal_domains(conn: sqlite3.Connection, *, max_n: int = 4) -> set[str]:
    """Return authoritative operator domains for commercial direction.

    Sender frequency is not an identity signal: suppliers, customers,
    marketplaces, and relay domains may legitimately dominate the archive.
    """
    _ = conn, max_n
    return set(INTERNAL_OPERATOR_DOMAINS)


def derive_vendor_domains(conn: sqlite3.Connection, *, min_rows: int = 4) -> set[str]:
    if not table_exists(conn, "contact_master"):
        return set()
    rows = conn.execute(
        """
        SELECT domain
        FROM contact_master
        WHERE domain IS NOT NULL
          AND length(trim(domain)) > 0
          AND (invoice_email_count + purchase_email_count) >= ?
          AND quote_email_count = 0
        """,
        (min_rows,),
    ).fetchall()
    return {str(r[0]).lower().strip() for r in rows if r and r[0]}


def derive_existing_client_domains(conn: sqlite3.Connection, *, min_total: int = 25) -> set[str]:
    if not table_exists(conn, "organization_master"):
        return set()
    rows = conn.execute(
        """
        SELECT domain
        FROM organization_master
        WHERE domain IS NOT NULL
          AND length(trim(domain)) > 0
          AND total_emails >= ?
          AND (quote_email_count >= 2 OR invoice_email_count >= 2 OR purchase_email_count >= 2)
        """,
        (min_total,),
    ).fetchall()
    return {str(r[0]).lower().strip() for r in rows if r and r[0]}


def selected_email_where_clause(
    last_watermark: int, reprocess_days: int | None
) -> tuple[str, tuple[object, ...]]:
    if reprocess_days is None:
        return "WHERE id > ?", (last_watermark,)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=reprocess_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return "WHERE id > ? OR (date_iso IS NOT NULL AND date_iso >= ?)", (last_watermark, cutoff)


def fetch_emails_for_commercial_build(
    conn: sqlite3.Connection,
    *,
    rebuild: bool,
    last_watermark: int,
    reprocess_days: int | None,
) -> list[sqlite3.Row]:
    """Rows for signal derivation; sets ``conn.row_factory = sqlite3.Row`` while fetching."""
    prev_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        if rebuild:
            return conn.execute(
                """
                SELECT id, source_file, date_iso, sender, recipients, subject,
                       COALESCE(top_reply_clean, '') AS top_reply_clean,
                       COALESCE(full_body_clean, '') AS full_body_clean
                FROM emails
                ORDER BY id
                """
            ).fetchall()
        where_sql, params = selected_email_where_clause(last_watermark, reprocess_days)
        return conn.execute(
            f"""
            SELECT id, source_file, date_iso, sender, recipients, subject,
                   COALESCE(top_reply_clean, '') AS top_reply_clean,
                   COALESCE(full_body_clean, '') AS full_body_clean
            FROM emails
            {where_sql}
            ORDER BY id
            """,
            params,
        ).fetchall()
    finally:
        conn.row_factory = prev_factory


def fetch_commercial_email_signal_fact_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    prev_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(SQL_COMMERCIAL_EMAIL_SIGNAL_FACT_FOR_ROLLUP).fetchall()
    finally:
        conn.row_factory = prev_factory


def fetch_org_rollup_for_opportunity_insert(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    prev_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(SQL_ORG_ROLLUP_FOR_OPPORTUNITY_INSERT).fetchall()
    finally:
        conn.row_factory = prev_factory


def fetch_org_rollup_for_org_candidates(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    prev_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(SQL_ORG_ROLLUP_FOR_ORG_CANDIDATES).fetchall()
    finally:
        conn.row_factory = prev_factory


def fetch_contact_rollup_for_contact_candidates(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    prev_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(SQL_CONTACT_ROLLUP_FOR_CONTACT_CANDIDATES).fetchall()
    finally:
        conn.row_factory = prev_factory


def fetch_opportunity_fact_for_opp_candidates(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    prev_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(SQL_OPPORTUNITY_FACT_FOR_OPP_CANDIDATES).fetchall()
    finally:
        conn.row_factory = prev_factory


def fetch_candidate_suppressed_counts(conn: sqlite3.Connection) -> tuple[int, int, int]:
    row = conn.execute(SQL_CANDIDATE_SUPPRESSED_COUNTS).fetchone()
    if not row:
        return 0, 0, 0
    return int(row[0] or 0), int(row[1] or 0), int(row[2] or 0)
