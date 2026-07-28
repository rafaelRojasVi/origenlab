"""Optional read-only Sent-folder campaign quality (canonical OrigenLab Gmail).

This is deliberately separate from prospect-source cohort metrics.

Does not reuse the hard-coded post_send_digest subject-fragment filter.
Campaign grouping is by normalized subject + calendar month of sent date
(deterministic, no invented campaign IDs).
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from typing import Any

from origenlab_email_pipeline.business_mart import emails_in
from origenlab_email_pipeline.campaigns.post_send_digest import scan_ndr_index
from origenlab_email_pipeline.contacto_gmail_source import sql_predicate_contacto_gmail_source
from origenlab_email_pipeline.qa.commercial_truth_audit.emails import (
    duplicate_email_stats,
    normalize_valid_email,
)
from origenlab_email_pipeline.qa.commercial_truth_audit.readonly import table_columns, table_exists

_INTERNAL_SUFFIXES = ("@origenlab.cl", "@labdelivery.cl")
_WS_RE = re.compile(r"\s+")


def _norm_subject(subject: str) -> str:
    s = _WS_RE.sub(" ", (subject or "").strip().lower())
    return s[:200]


def _campaign_key(subject: str, month: str) -> str:
    digest = hashlib.sha256(f"{subject}|{month}".encode("utf-8")).hexdigest()[:12]
    return f"subjmonth:{digest}"


def build_sent_campaign_quality(
    conn: sqlite3.Connection,
    *,
    since_days: int = 365,
    gmail_user: str = "contacto@origenlab.cl",
    sent_folders: tuple[str, ...] = ("[Gmail]/Enviados", "Sent", "Sent Mail"),
    subject_contains: str | None = None,
) -> list[dict[str, Any]]:
    """Scan canonical Sent rows and correlate with NDR index.

    Returns empty list when emails table lacks required columns.
    """
    if not table_exists(conn, "emails"):
        return []
    cols = table_columns(conn, "emails")
    needed = {"id", "subject", "recipients", "date_iso", "folder", "source_file"}
    if not needed.issubset(cols):
        return [
            {
                "campaign_key": "",
                "subject": "",
                "notes": "emails table missing required columns; sent_campaign_quality unavailable",
                "metric_confidence": "unavailable",
            }
        ]

    from origenlab_email_pipeline.outreach_ingest_sync import cutoff_date_str

    pred = sql_predicate_contacto_gmail_source()
    cutoff = cutoff_date_str(since_days=since_days)
    like_pat = f"gmail:{gmail_user.strip().lower()}/%"
    folders = tuple(f.strip() for f in sent_folders if f.strip())
    if not folders:
        return []
    ph = ",".join("?" * len(folders))
    rows = conn.execute(
        f"""
        SELECT id, subject, recipients, date_iso, folder
        FROM emails
        WHERE {pred}
          AND lower(source_file) LIKE ?
          AND folder IN ({ph})
          AND COALESCE(date_iso, '') >= ?
        ORDER BY date_iso DESC, id DESC
        """,
        (like_pat, *folders, cutoff),
    ).fetchall()

    # Optional subject selector (explicit); no hard-coded fragment allowlist.
    subject_filter = (subject_contains or "").strip().lower() or None

    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        subject = str(row[1] or "")
        if subject_filter and subject_filter not in subject.lower():
            continue
        date_iso = str(row[3] or "")
        month = date_iso[:7] if len(date_iso) >= 7 else "unknown"
        subj_n = _norm_subject(subject)
        key = _campaign_key(subj_n, month)
        g = groups.setdefault(
            key,
            {
                "campaign_key": key,
                "subject": subject[:300],
                "month": month,
                "message_ids": set(),
                "recipients": [],
            },
        )
        g["message_ids"].add(str(row[0]))
        for em in emails_in(str(row[2] or "")):
            if any(em.endswith(suf) for suf in _INTERNAL_SUFFIXES):
                continue
            valid = normalize_valid_email(em)
            if valid:
                g["recipients"].append(valid)

    ndr_index = scan_ndr_index(conn, since_days=since_days)

    # Current suppression (not pre-send unless proven).
    suppressed_now: set[str] = set()
    if table_exists(conn, "contact_email_suppression"):
        for (em,) in conn.execute("SELECT email FROM contact_email_suppression"):
            n = normalize_valid_email(em)
            if n:
                suppressed_now.add(n)

    out: list[dict[str, Any]] = []
    for key in sorted(groups):
        g = groups[key]
        recipients = list(g["recipients"])
        stats = duplicate_email_stats(recipients)
        uniq = set(recipients)
        hard_bounce = 0
        for em in uniq:
            hits = ndr_index.get(em) or []
            if any(str(h.get("failure_type") or "").startswith("hard") or "bounce" in str(h.get("failure_type") or "").lower() for h in hits):
                hard_bounce += 1
            elif hits:
                # Any NDR hit counts toward hard_bounce conservatively when failure_type absent.
                hard_bounce += 1
        current_suppressed = sum(1 for em in uniq if em in suppressed_now)
        delivered_without_ndr = max(0, stats["unique_valid_email_count"] - hard_bounce)
        out.append(
            {
                "campaign_key": key,
                "subject": g["subject"],
                "month": g["month"],
                "sent_message_count": len(g["message_ids"]),
                "recipient_occurrence_count": stats["valid_email_row_count"],
                "unique_recipient_count": stats["unique_valid_email_count"],
                "duplicate_recipient_occurrence_count": stats["duplicate_occurrence_count"],
                "duplicate_rate_of_valid_recipients": stats["duplicate_rate_of_valid_email_rows"],
                "hard_bounce_count": hard_bounce,
                "hard_bounce_rate": round(
                    100.0 * hard_bounce / stats["unique_valid_email_count"], 2
                )
                if stats["unique_valid_email_count"]
                else 0.0,
                "delivered_without_ndr_count": delivered_without_ndr,
                "human_reply_candidate_count": "",  # not inferred without dedicated reply scan
                "auto_reply_count": "",
                "suppressed_before_send_count_if_provable": "",
                "suppressed_before_send_notes": "unavailable_without_temporal_suppression_evidence",
                "current_suppressed_count": current_suppressed,
                "metric_confidence": "derived_medium_confidence",
                "grouping_method": "normalized_subject_plus_month",
                "notes": "Actual Sent-folder audit; independent of prospect cohort metrics.",
            }
        )
    out.sort(key=lambda r: (r.get("month") or "", r.get("campaign_key") or ""))
    return out
