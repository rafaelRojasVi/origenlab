"""Read-only NDR scan over contacto Gmail-ingested ``emails`` rows (no suppressions)."""

from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any

from origenlab_email_pipeline.contact_email_suppression import fetch_contact_email_suppression_row
from origenlab_email_pipeline.contacto_gmail_source import sql_predicate_contacto_gmail_source
from origenlab_email_pipeline.email_business_filters import classify_email
from origenlab_email_pipeline.ndr_bounce_extraction import (
    bounce_suppression_code_from_ndr_text,
    extract_failed_recipients_from_ndr,
)

PlannedEntry = tuple[str, str | None, int, str | None]
# recipient_email -> (code, date_iso, email_id, subject_snip)

_YYYY_MM_DD = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate_since_date_utc(value: str) -> str:
    """Validate a strict UTC calendar date ``YYYY-MM-DD`` for absolute NDR windows."""
    raw = (value or "").strip()
    if not _YYYY_MM_DD.fullmatch(raw):
        raise ValueError(f"since_date_utc must be YYYY-MM-DD, got {value!r}")
    try:
        date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"since_date_utc is not a valid calendar date: {value!r}") from exc
    return raw


def compute_scan_start_date_utc(
    *,
    since_days: int,
    as_of_utc: datetime | None = None,
) -> str:
    """UTC calendar lower bound matching SQLite ``date('now', '-N days')`` semantics."""
    days = int(since_days)
    if days < 0:
        raise ValueError(f"since_days must be >= 0, got {since_days!r}")
    as_of = (as_of_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return (as_of.date() - timedelta(days=days)).isoformat()


def _body_blob(row: tuple[Any, ...]) -> str:
    full_clean, text_clean, body = row
    return (
        str(full_clean or "")
        or str(text_clean or "")
        or str(body or "")
    )


def scan_ndr_planned_recipients(
    conn: sqlite3.Connection,
    *,
    since_days: int | None = None,
    since_date_utc: str | None = None,
    limit: int,
) -> tuple[dict[str, PlannedEntry], int, int]:
    """Scan contacto Gmail rows; return planned suppressions and scan stats.

    ``since_days`` uses SQLite calendar relative ``date('now', '-N days')``.
    ``since_date_utc`` freezes an absolute ``YYYY-MM-DD`` lower bound.
    The two options are mutually exclusive.
    """
    if since_days is not None and since_date_utc is not None:
        raise ValueError("since_date_utc and since_days must not both be active")

    pred = sql_predicate_contacto_gmail_source()
    date_filter = ""
    params: list[Any] = []
    if since_date_utc is not None:
        validated = validate_since_date_utc(since_date_utc)
        date_filter = "AND date_iso >= ?"
        params.append(validated)
    elif since_days is not None and since_days > 0:
        date_filter = "AND date_iso >= date('now', ?)"
        params.append(f"-{int(since_days)} days")

    sql = f"""
        SELECT sender, subject,
               full_body_clean, body_text_clean, body,
               folder, date_iso, id
        FROM emails
        WHERE {pred}
        {date_filter}
        ORDER BY COALESCE(date_iso, '') DESC
        LIMIT ?
    """
    params.append(int(limit))

    planned: dict[str, PlannedEntry] = {}
    skipped_no_rcpt = 0
    scanned = 0
    cur = conn.execute(sql, tuple(params))
    for sender, subject, full_clean, text_clean, body, _folder, date_iso, eid in cur:
        scanned += 1
        blob = _body_blob((full_clean, text_clean, body))
        cl = classify_email(sender=str(sender or ""), subject=str(subject or ""), body=blob)
        if "bounce_ndr" not in cl.get("tags", []):
            continue
        subj_l = str(subject or "").lower()
        if "notification (delay)" in subj_l or subj_l.strip().endswith("(delay)"):
            continue
        rcpts = extract_failed_recipients_from_ndr(blob)
        if not rcpts:
            skipped_no_rcpt += 1
            continue
        code = bounce_suppression_code_from_ndr_text(blob)
        subj_snip = (str(subject)[:100] + "…") if subject and len(str(subject)) > 100 else subject
        d_iso = str(date_iso) if date_iso else None
        for r in rcpts:
            prev = planned.get(r)
            if prev is None or (d_iso or "") > (prev[1] or ""):
                planned[r] = (code, d_iso, int(eid), subj_snip)
    return planned, scanned, skipped_no_rcpt


def summarize_ndr_backlog(
    conn: sqlite3.Connection,
    planned: dict[str, PlannedEntry],
) -> dict[str, Any]:
    """Classify planned NDR recipients vs existing suppressions (read-only)."""
    already_suppressed = 0
    net_new: list[dict[str, Any]] = []
    by_code: dict[str, int] = {}
    for email, (code, date_iso, email_id, subject_snip) in planned.items():
        by_code[code] = by_code.get(code, 0) + 1
        existing = fetch_contact_email_suppression_row(conn, email)
        if existing:
            already_suppressed += 1
            continue
        net_new.append(
            {
                "email": email,
                "proposed_code": code,
                "date_iso": date_iso,
                "email_id": email_id,
                "subject_snippet": subject_snip,
            }
        )
    net_new.sort(key=lambda r: (r.get("date_iso") or "", r["email"]))
    return {
        "planned_distinct": len(planned),
        "already_suppressed": already_suppressed,
        "net_new_count": len(net_new),
        "by_code": dict(sorted(by_code.items())),
        "net_new_rows": net_new,
    }
