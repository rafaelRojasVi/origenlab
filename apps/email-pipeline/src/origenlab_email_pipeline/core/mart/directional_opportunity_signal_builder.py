"""Message-level directional opportunity-signal bridge (upstream of PR3).

Bridges typed, per-message commercial intelligence (``commercial_email_signal_fact``)
into ``opportunity_signals`` rows that carry an exact ``email_id``. PR3's existing
signal loader (``commercial_opportunity.sources.load_opportunity_signals``) then
recovers ``emails.date_iso`` for these rows, so ``resolve_opportunities`` emits a
dated ``evidence_candidate`` instead of the undated ``commercial_history`` fallback.

This module does not touch PR3 and does not decide currentness — every row it
emits is deliberately still resolved by PR3 as ``stage_is_current=False`` (see
``commercial_opportunity/resolve.py``); this bridge only supplies dated, typed,
provenance-bearing evidence.

Conservative first slice: only two client-side warm-case roles are promoted to a
directional signal (``client_opportunity`` and ``quote_sent``). Every other role
— including supplier replies, admin threads, noise, and the ambiguous
``waiting_client`` / ``client_response`` roles — is intentionally omitted. Prefer
omission over a false positive; broadening this allowlist is a deliberate,
separate decision.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from origenlab_email_pipeline.business_mart import signal_row
from origenlab_email_pipeline.contacto_gmail_source import sql_predicate_contacto_gmail_source
from origenlab_email_pipeline.warm_case_role_classification import (
    infer_warm_case_role_category,
)
from origenlab_email_pipeline.warm_case_sender_rules import (
    contact_email_from_recipients,
    contact_email_from_sender,
    email_domain,
    is_internal_operator_contact,
    is_supplier_vendor_domain,
)

DIRECTIONAL_SIGNAL_SOURCE_TAG = "warm_case_direction_v1"

# warm-case role_category -> opportunity_signal signal_type.
# Deliberately narrow — see module docstring. Do not add roles here without a
# corresponding conservative test case (waiting_supplier, supplier_followup,
# supplier_quote_received, payment_admin, logistics_admin, internal_admin,
# system_noise, bounce_problem, deal_evidence_candidate, waiting_client and
# client_response are all intentionally excluded from this first slice).
_DIRECTIONAL_ROLE_TO_SIGNAL_TYPE: dict[str, str] = {
    "client_opportunity": "client_opportunity",
    "quote_sent": "quote_sent",
}

_DIRECTIONAL_SOURCE_SQL = """
    SELECT
      e.id AS email_id,
      e.date_iso,
      substr(COALESCE(e.subject, ''), 1, 140) AS subject_preview,
      COALESCE(e.sender, '') AS sender_preview,
      COALESCE(e.recipients, '') AS recipients_preview,
      substr(COALESCE(e.top_reply_clean, ''), 1, 800) AS body_snippet,
      e.source_file,
      agg.has_positive AS has_positive_signal,
      agg.has_suppression AS has_suppression_signal,
      agg.max_positive_strength,
      agg.contact_email,
      agg.org_domain
    FROM (
      SELECT
        email_id,
        MAX(CASE WHEN signal_kind = 'positive' THEN 1 ELSE 0 END) AS has_positive,
        MAX(CASE WHEN signal_kind = 'suppression' THEN 1 ELSE 0 END) AS has_suppression,
        MAX(CASE WHEN signal_kind = 'positive' THEN strength_score END) AS max_positive_strength,
        MAX(NULLIF(TRIM(LOWER(contact_email)), '')) AS contact_email,
        MAX(NULLIF(TRIM(LOWER(org_domain)), '')) AS org_domain
      FROM commercial_email_signal_fact
      GROUP BY email_id
    ) agg
    JOIN emails e ON e.id = agg.email_id
    WHERE {contact_where}
    ORDER BY agg.email_id ASC
"""


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def _fetch_directional_source_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """One row per email_id with a typed commercial signal (never fails the build)."""
    if not _table_exists(conn, "commercial_email_signal_fact") or not _table_exists(conn, "emails"):
        return []
    contact_where = sql_predicate_contacto_gmail_source(table_alias="e", coalesce_null=True)
    sql = _DIRECTIONAL_SOURCE_SQL.format(contact_where=contact_where)
    prev_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql).fetchall()
    finally:
        conn.row_factory = prev_factory
    return [dict(r) for r in rows]


def _is_sent_folder(source_file: object) -> bool:
    value = str(source_file or "").lower()
    return (
        "enviados" in value
        or "sent mail" in value
        or "/sent" in value
    )


def _resolve_actual_external_contact(
    row: dict[str, Any],
) -> tuple[str, str] | None:
    """Resolve counterparty from Gmail truth, not stale mart identity."""
    if _is_sent_folder(row.get("source_file")):
        email = contact_email_from_recipients(
            str(row.get("recipients_preview") or "")
        )
    else:
        email = contact_email_from_sender(
            str(row.get("sender_preview") or "")
        )
        if is_internal_operator_contact(email):
            email = ""

    email = email.strip().lower()
    domain = email_domain(email)

    if not email or not domain:
        return None

    return email, domain


def _defensible_entity(row: dict[str, Any]) -> tuple[str, str] | None:
    """(entity_kind, entity_key): contact email first, else organization domain."""
    contact_email = row.get("contact_email")
    if isinstance(contact_email, str) and "@" in contact_email:
        return "contact", contact_email.strip().lower()
    org_domain = row.get("org_domain")
    if isinstance(org_domain, str) and org_domain.strip():
        return "organization", org_domain.strip().lower()
    return None


def _bounded_score(max_positive_strength: object) -> float:
    if max_positive_strength is None:
        return 0.5
    try:
        value = float(max_positive_strength)
    except (TypeError, ValueError):
        return 0.5
    return min(1.0, max(0.0, value))


def compute_directional_opportunity_signal_rows(conn: sqlite3.Connection) -> list[tuple]:
    """Message-level directional ``opportunity_signals`` rows (no SQLite writes).

    Read-only: derives rows in memory from ``commercial_email_signal_fact`` and
    ``emails``. Returns ``[]`` when ``commercial_email_signal_fact`` is absent —
    never raises, so the mart build stays functional without it.
    """
    source_rows = _fetch_directional_source_rows(conn)
    seen: set[tuple[int, str, str, str]] = set()
    out: list[tuple] = []
    for row in sorted(source_rows, key=lambda r: int(r["email_id"])):
        actual_contact = _resolve_actual_external_contact(row)

        # commercial_email_signal_fact may contain stale/wrong counterparty
        # identity. Classification and provenance for this bridge must use the
        # canonical Gmail sender/recipient row instead.
        if actual_contact is None:
            row["contact_email"] = ""
            row["org_domain"] = ""
        else:
            row["contact_email"], row["org_domain"] = actual_contact

        # This bridge is deliberately client-side only. Preserve the broader
        # warm-case classifier's existing semantics, but never promote a
        # known supplier/vendor counterparty into PR3 directional evidence.
        if (
            actual_contact is not None
            and is_supplier_vendor_domain(actual_contact[1])
        ):
            continue

        role = infer_warm_case_role_category(
            row,
            enrichment_available=True,
            include_noise=True,
        )
        signal_type = _DIRECTIONAL_ROLE_TO_SIGNAL_TYPE.get(role)
        if signal_type is None:
            continue
        entity = _defensible_entity(row)
        if entity is None:
            continue
        entity_kind, entity_key = entity
        email_id = int(row["email_id"])
        dedupe_key = (email_id, signal_type, entity_kind, entity_key)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        out.append(
            signal_row(
                signal_type=signal_type,
                entity_kind=entity_kind,
                entity_key=entity_key,
                email_id=email_id,
                score=_bounded_score(row.get("max_positive_strength")),
                details={
                    "source": DIRECTIONAL_SIGNAL_SOURCE_TAG,
                    "role_category": role,
                },
            )
        )
    return out
