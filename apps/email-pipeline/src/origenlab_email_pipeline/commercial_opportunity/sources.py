"""Read-only loaders for opportunity-stage source evidence (PR3)."""

from __future__ import annotations

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


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


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


def load_opportunity_signals(conn: sqlite3.Connection) -> list[SourceSignalRow]:
    if not _table_exists(conn, "opportunity_signals"):
        return []
    cols = _cols(conn, "opportunity_signals")
    # Prefer recovering email business time when joinable; never treat created_at as event time.
    has_email_id = "email_id" in cols
    email_join = ""
    email_date_expr = "NULL AS email_date"
    if has_email_id and _table_exists(conn, "emails"):
        email_cols = _cols(conn, "emails")
        date_col = None
        for candidate in ("date_iso", "internal_date", "sent_at", "received_at", "date"):
            if candidate in email_cols:
                date_col = candidate
                break
        if date_col:
            email_join = "LEFT JOIN emails em ON em.id = s.email_id"
            email_date_expr = f"em.{date_col} AS email_date"
    select_email_id = "s.email_id" if has_email_id else "NULL AS email_id"
    # Stable id column
    id_col = "id" if "id" in cols else None
    if id_col is None:
        return []
    for count_col in ("quote_email_count", "invoice_email_count", "purchase_email_count"):
        if count_col not in cols:
            # Still allow signal rows without counts
            pass
    q = f"""
        SELECT s.{id_col} AS signal_id,
               s.contact_email AS contact_email,
               s.organization_name AS organization_name,
               COALESCE(s.signal_type, s.classification, '') AS signal_type,
               s.created_at AS created_at,
               {select_email_id},
               {email_date_expr},
               COALESCE(s.quote_email_count, 0) AS quote_email_count,
               COALESCE(s.invoice_email_count, 0) AS invoice_email_count,
               COALESCE(s.purchase_email_count, 0) AS purchase_email_count
        FROM opportunity_signals s
        {email_join}
        ORDER BY s.{id_col} ASC
    """
    # Some fixtures use different column names — fall back gracefully.
    try:
        rows = conn.execute(q).fetchall()
    except sqlite3.OperationalError:
        return []
    out: list[SourceSignalRow] = []
    for r in rows:
        out.append(
            SourceSignalRow(
                signal_id=str(r["signal_id"]),
                contact_email=(str(r["contact_email"]).strip().lower() if r["contact_email"] else None),
                organization_name=(str(r["organization_name"]) if r["organization_name"] else None),
                signal_type=(str(r["signal_type"]) if r["signal_type"] else None),
                created_at=(str(r["created_at"]) if r["created_at"] else None),
                email_id=(int(r["email_id"]) if r["email_id"] is not None else None),
                email_date=(str(r["email_date"]) if r["email_date"] else None),
                quote_email_count=int(r["quote_email_count"] or 0),
                invoice_email_count=int(r["invoice_email_count"] or 0),
                purchase_email_count=int(r["purchase_email_count"] or 0),
            )
        )
    return out


def load_contact_master_history(conn: sqlite3.Connection) -> list[SourceContactMasterRow]:
    if not _table_exists(conn, "contact_master"):
        return []
    cols = _cols(conn, "contact_master")
    if "email" not in cols:
        return []
    id_expr = "CAST(id AS TEXT)" if "id" in cols else "email"
    org_expr = (
        "organization_name_guess"
        if "organization_name_guess" in cols
        else ("organization_name" if "organization_name" in cols else "NULL")
    )
    q = f"""
        SELECT {id_expr} AS contact_id,
               email,
               {org_expr} AS organization_name,
               COALESCE(quote_email_count, 0) AS quote_email_count,
               COALESCE(invoice_email_count, 0) AS invoice_email_count,
               COALESCE(purchase_email_count, 0) AS purchase_email_count,
               COALESCE(gmail_sent_count, 0) AS gmail_sent_count,
               COALESCE(gmail_received_count, 0) AS gmail_received_count
        FROM contact_master
        ORDER BY email ASC
    """
    try:
        rows = conn.execute(q).fetchall()
    except sqlite3.OperationalError:
        return []
    return [
        SourceContactMasterRow(
            contact_id=str(r["contact_id"]),
            email=(str(r["email"]).strip().lower() if r["email"] else None),
            organization_name=(str(r["organization_name"]) if r["organization_name"] else None),
            quote_email_count=int(r["quote_email_count"] or 0),
            invoice_email_count=int(r["invoice_email_count"] or 0),
            purchase_email_count=int(r["purchase_email_count"] or 0),
            gmail_sent_count=int(r["gmail_sent_count"] or 0),
            gmail_received_count=int(r["gmail_received_count"] or 0),
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
