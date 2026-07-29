"""Read-only loaders for opportunity-stage source evidence (PR3).

Loaders follow production DDL contracts from ``business_mart_schema`` and the
commercial deal ledger. Schema mismatches fail closed with an explicit error —
they never silently return an empty result when the table exists.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from origenlab_email_pipeline.commercial_opportunity.models import (
    SourceContactMasterRow,
    SourceDealDocumentRow,
    SourceDealEventRow,
    SourceDealPaymentRow,
    SourceDealRow,
    SourceSignalRow,
)

REQUIRED_OPPORTUNITY_SIGNAL_COLS: frozenset[str] = frozenset(
    {
        "id",
        "signal_type",
        "entity_kind",
        "entity_key",
        "email_id",
        "attachment_id",
        "score",
        "details_json",
        "created_at",
    }
)

REQUIRED_CONTACT_MASTER_COMMERCIAL_COLS: frozenset[str] = frozenset(
    {
        "email",
        "first_seen_at",
        "last_seen_at",
        "total_emails",
        "inbound_emails",
        "outbound_emails",
        "quote_email_count",
        "invoice_email_count",
        "purchase_email_count",
        "business_doc_email_count",
        "quote_doc_count",
        "invoice_doc_count",
    }
)


class SourceSchemaError(RuntimeError):
    """Raised when a required source table exists but violates the production contract."""


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _require_columns(table: str, cols: set[str], required: frozenset[str]) -> None:
    missing = sorted(required - cols)
    if missing:
        raise SourceSchemaError(
            f"source_schema_incompatible:{table}: missing required columns {missing}"
        )


def _bounded_details(raw: object) -> dict[str, Any]:
    """Defensive parse of details_json — malformed JSON yields empty dict, never abort."""
    if raw is None:
        return {}
    text = str(raw).strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in list(parsed.items())[:32]:
        k = str(key)[:64]
        if isinstance(value, (str, int, float, bool)) or value is None:
            if isinstance(value, str) and len(value) > 200:
                out[k] = value[:200]
            else:
                out[k] = value
    return out


def load_deals(conn: sqlite3.Connection) -> list[SourceDealRow]:
    if not _table_exists(conn, "commercial_deal"):
        return []
    rows = conn.execute(
        """
        SELECT id, deal_key, deal_status, client_org_name, client_domain,
               client_contact_email, supplier_org_name, supplier_domain,
               confidence, created_at, updated_at
        FROM commercial_deal
        ORDER BY deal_key ASC, id ASC
        """
    ).fetchall()
    out: list[SourceDealRow] = []
    for r in rows:
        out.append(
            SourceDealRow(
                deal_id=int(r["id"]),
                deal_key=str(r["deal_key"]),
                deal_status=str(r["deal_status"] or ""),
                client_org_name=str(r["client_org_name"] or ""),
                client_domain=(str(r["client_domain"]).strip().lower() if r["client_domain"] else None),
                client_contact_email=(
                    str(r["client_contact_email"]).strip().lower() if r["client_contact_email"] else None
                ),
                supplier_org_name=(str(r["supplier_org_name"]) if r["supplier_org_name"] else None),
                supplier_domain=(
                    str(r["supplier_domain"]).strip().lower() if r["supplier_domain"] else None
                ),
                confidence=str(r["confidence"] or "needs_review"),
                created_at=(str(r["created_at"]) if r["created_at"] else None),
                updated_at=(str(r["updated_at"]) if r["updated_at"] else None),
            )
        )
    return out


def load_deal_events(conn: sqlite3.Connection) -> list[SourceDealEventRow]:
    if not _table_exists(conn, "commercial_deal_event") or not _table_exists(conn, "commercial_deal"):
        return []
    cols = _cols(conn, "commercial_deal_event")
    op_expr = "COALESCE(operator_confirmed, 0)" if "operator_confirmed" in cols else "0"
    rows = conn.execute(
        f"""
        SELECT e.id AS event_id, e.deal_id, d.deal_key, e.event_type, e.event_at,
               e.confidence, {op_expr} AS operator_confirmed,
               e.source_email_id, e.source_attachment_id, e.summary
        FROM commercial_deal_event e
        JOIN commercial_deal d ON d.id = e.deal_id
        ORDER BY d.deal_key ASC, e.id ASC
        """
    ).fetchall()
    out: list[SourceDealEventRow] = []
    for r in rows:
        out.append(
            SourceDealEventRow(
                event_id=int(r["event_id"]),
                deal_id=int(r["deal_id"]),
                deal_key=str(r["deal_key"]),
                event_type=str(r["event_type"] or ""),
                event_at=(str(r["event_at"]) if r["event_at"] else None),
                confidence=str(r["confidence"] or "needs_review"),
                operator_confirmed=(
                    bool(int(r["operator_confirmed"] or 0))
                    or str(r["confidence"] or "") == "operator_confirmed"
                ),
                source_email_id=(int(r["source_email_id"]) if r["source_email_id"] is not None else None),
                source_attachment_id=(
                    int(r["source_attachment_id"]) if r["source_attachment_id"] is not None else None
                ),
                summary=(str(r["summary"]) if r["summary"] else None),
            )
        )
    return out


def load_deal_documents(conn: sqlite3.Connection) -> list[SourceDealDocumentRow]:
    if not _table_exists(conn, "commercial_deal_document") or not _table_exists(conn, "commercial_deal"):
        return []
    rows = conn.execute(
        """
        SELECT doc.id AS document_id, doc.deal_id, d.deal_key, doc.document_type,
               doc.issued_at, doc.confidence, doc.source_email_id, doc.source_attachment_id
        FROM commercial_deal_document doc
        JOIN commercial_deal d ON d.id = doc.deal_id
        ORDER BY d.deal_key ASC, doc.id ASC
        """
    ).fetchall()
    return [
        SourceDealDocumentRow(
            document_id=int(r["document_id"]),
            deal_id=int(r["deal_id"]),
            deal_key=str(r["deal_key"]),
            document_type=str(r["document_type"] or ""),
            issued_at=(str(r["issued_at"]) if r["issued_at"] else None),
            confidence=str(r["confidence"] or "needs_review"),
            source_email_id=(int(r["source_email_id"]) if r["source_email_id"] is not None else None),
            source_attachment_id=(
                int(r["source_attachment_id"]) if r["source_attachment_id"] is not None else None
            ),
        )
        for r in rows
    ]


def load_deal_payments(conn: sqlite3.Connection) -> list[SourceDealPaymentRow]:
    if not _table_exists(conn, "commercial_deal_payment") or not _table_exists(conn, "commercial_deal"):
        return []
    rows = conn.execute(
        """
        SELECT p.id AS payment_id, p.deal_id, d.deal_key, p.direction, p.paid_at, p.confidence
        FROM commercial_deal_payment p
        JOIN commercial_deal d ON d.id = p.deal_id
        ORDER BY d.deal_key ASC, p.id ASC
        """
    ).fetchall()
    return [
        SourceDealPaymentRow(
            payment_id=int(r["payment_id"]),
            deal_id=int(r["deal_id"]),
            deal_key=str(r["deal_key"]),
            direction=str(r["direction"] or ""),
            paid_at=(str(r["paid_at"]) if r["paid_at"] else None),
            confidence=str(r["confidence"] or "needs_review"),
        )
        for r in rows
    ]


def _email_date_expr(conn: sqlite3.Connection) -> tuple[str, str]:
    """Return (join_sql, select_expr) for recovering business event time from emails."""
    if not _table_exists(conn, "emails"):
        return "", "NULL AS email_date"
    email_cols = _cols(conn, "emails")
    date_col = None
    for candidate in ("date_iso", "internal_date", "sent_at", "received_at", "date"):
        if candidate in email_cols:
            date_col = candidate
            break
    if not date_col:
        return "", "NULL AS email_date"
    return "LEFT JOIN emails em ON em.id = s.email_id", f"em.{date_col} AS email_date"


def load_opportunity_signals(conn: sqlite3.Connection) -> list[SourceSignalRow]:
    """Load production ``opportunity_signals`` rows.

    Never silently returns [] when the table exists but columns are wrong.
    """
    if not _table_exists(conn, "opportunity_signals"):
        return []
    cols = _cols(conn, "opportunity_signals")
    _require_columns("opportunity_signals", cols, REQUIRED_OPPORTUNITY_SIGNAL_COLS)
    email_join, email_date_expr = _email_date_expr(conn)
    q = f"""
        SELECT s.id AS signal_id,
               s.signal_type AS signal_type,
               s.entity_kind AS entity_kind,
               s.entity_key AS entity_key,
               s.created_at AS created_at,
               s.email_id AS email_id,
               s.attachment_id AS attachment_id,
               s.score AS score,
               s.details_json AS details_json,
               {email_date_expr}
        FROM opportunity_signals s
        {email_join}
        ORDER BY s.id ASC
    """
    rows = conn.execute(q).fetchall()
    out: list[SourceSignalRow] = []
    for r in rows:
        out.append(
            SourceSignalRow(
                signal_id=str(r["signal_id"]),
                signal_type=(str(r["signal_type"]) if r["signal_type"] is not None else None),
                entity_kind=str(r["entity_kind"] or "").strip().lower(),
                entity_key=str(r["entity_key"] or "").strip(),
                created_at=(str(r["created_at"]) if r["created_at"] else None),
                email_id=(int(r["email_id"]) if r["email_id"] is not None else None),
                attachment_id=(int(r["attachment_id"]) if r["attachment_id"] is not None else None),
                email_date=(str(r["email_date"]) if r["email_date"] else None),
                score=(float(r["score"]) if r["score"] is not None else None),
                details=_bounded_details(r["details_json"]),
            )
        )
    return out


def load_contact_master_history(conn: sqlite3.Connection) -> list[SourceContactMasterRow]:
    """Load contact_master using production commercial-history columns.

    Generic email totals alone do not qualify a row; callers filter via
    ``has_typed_commercial_evidence``.
    """
    if not _table_exists(conn, "contact_master"):
        return []
    cols = _cols(conn, "contact_master")
    _require_columns("contact_master", cols, REQUIRED_CONTACT_MASTER_COMMERCIAL_COLS)
    org_expr = "organization_name_guess" if "organization_name_guess" in cols else "NULL"
    domain_expr = "domain" if "domain" in cols else "NULL"
    q = f"""
        SELECT email AS contact_id,
               email,
               {org_expr} AS organization_name,
               {domain_expr} AS domain,
               COALESCE(quote_email_count, 0) AS quote_email_count,
               COALESCE(invoice_email_count, 0) AS invoice_email_count,
               COALESCE(purchase_email_count, 0) AS purchase_email_count,
               COALESCE(business_doc_email_count, 0) AS business_doc_email_count,
               COALESCE(quote_doc_count, 0) AS quote_doc_count,
               COALESCE(invoice_doc_count, 0) AS invoice_doc_count,
               COALESCE(total_emails, 0) AS total_emails,
               COALESCE(inbound_emails, 0) AS inbound_emails,
               COALESCE(outbound_emails, 0) AS outbound_emails,
               first_seen_at,
               last_seen_at
        FROM contact_master
        ORDER BY email ASC
    """
    rows = conn.execute(q).fetchall()
    return [
        SourceContactMasterRow(
            contact_id=str(r["contact_id"]),
            email=(str(r["email"]).strip().lower() if r["email"] else None),
            organization_name=(str(r["organization_name"]) if r["organization_name"] else None),
            quote_email_count=int(r["quote_email_count"] or 0),
            invoice_email_count=int(r["invoice_email_count"] or 0),
            purchase_email_count=int(r["purchase_email_count"] or 0),
            business_doc_email_count=int(r["business_doc_email_count"] or 0),
            quote_doc_count=int(r["quote_doc_count"] or 0),
            invoice_doc_count=int(r["invoice_doc_count"] or 0),
            total_emails=int(r["total_emails"] or 0),
            inbound_emails=int(r["inbound_emails"] or 0),
            outbound_emails=int(r["outbound_emails"] or 0),
            first_seen_at=(str(r["first_seen_at"]) if r["first_seen_at"] else None),
            last_seen_at=(str(r["last_seen_at"]) if r["last_seen_at"] else None),
        )
        for r in rows
    ]


def load_opportunity_sources(conn: sqlite3.Connection) -> dict[str, Any]:
    """Load all opportunity source planes (read-only)."""
    return {
        "deals": load_deals(conn),
        "events": load_deal_events(conn),
        "documents": load_deal_documents(conn),
        "payments": load_deal_payments(conn),
        "signals": load_opportunity_signals(conn),
        "contact_master": load_contact_master_history(conn),
    }
