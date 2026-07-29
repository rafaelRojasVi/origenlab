"""Opportunity-source fingerprint for apply-time stale-plan detection."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from origenlab_email_pipeline.commercial_opportunity.constants import (
    OPPORTUNITY_SOURCE_FINGERPRINT_ALGORITHM_VERSION,
)
from origenlab_email_pipeline.commercial_opportunity.models import (
    SourceContactMasterRow,
    SourceDealDocumentRow,
    SourceDealEventRow,
    SourceDealPaymentRow,
    SourceDealRow,
    SourceSignalRow,
)


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def opportunity_source_fingerprint(
    *,
    deals: list[SourceDealRow],
    events: list[SourceDealEventRow],
    documents: list[SourceDealDocumentRow],
    payments: list[SourceDealPaymentRow],
    signals: list[SourceSignalRow],
    contact_master: list[SourceContactMasterRow],
) -> str:
    """Order-independent fingerprint of stage-relevant source content."""
    deal_rows = sorted(
        [
            {
                "deal_id": d.deal_id,
                "deal_key": d.deal_key,
                "deal_status": d.deal_status,
                "client_domain": d.client_domain,
                "client_contact_email_hash": hashlib.sha256(
                    (d.client_contact_email or "").encode("utf-8")
                ).hexdigest()[:16]
                if d.client_contact_email
                else None,
                "confidence": d.confidence,
                "created_at": d.created_at,
                "updated_at": d.updated_at,
            }
            for d in deals
        ],
        key=lambda x: (x["deal_key"], x["deal_id"]),
    )
    event_rows = sorted(
        [
            {
                "event_id": e.event_id,
                "deal_key": e.deal_key,
                "event_type": e.event_type,
                "event_at": e.event_at,
                "confidence": e.confidence,
                "operator_confirmed": e.operator_confirmed,
                "source_email_id": e.source_email_id,
                "source_attachment_id": e.source_attachment_id,
            }
            for e in events
        ],
        key=lambda x: (x["deal_key"], x["event_id"]),
    )
    doc_rows = sorted(
        [
            {
                "document_id": d.document_id,
                "deal_key": d.deal_key,
                "document_type": d.document_type,
                "issued_at": d.issued_at,
                "confidence": d.confidence,
                "source_email_id": d.source_email_id,
                "source_attachment_id": d.source_attachment_id,
            }
            for d in documents
        ],
        key=lambda x: (x["deal_key"], x["document_id"]),
    )
    pay_rows = sorted(
        [
            {
                "payment_id": p.payment_id,
                "deal_key": p.deal_key,
                "direction": p.direction,
                "paid_at": p.paid_at,
                "confidence": p.confidence,
            }
            for p in payments
        ],
        key=lambda x: (x["deal_key"], x["payment_id"]),
    )
    signal_rows = sorted(
        [
            {
                "signal_id": s.signal_id,
                "signal_type": s.signal_type,
                "entity_kind": s.entity_kind,
                "entity_key_hash": hashlib.sha256(s.entity_key.encode("utf-8")).hexdigest()[:16],
                "email_id": s.email_id,
                "attachment_id": s.attachment_id,
                "email_date": s.email_date,
                "created_at": s.created_at,
            }
            for s in signals
        ],
        key=lambda x: x["signal_id"],
    )
    history_rows = sorted(
        [
            {
                "email_hash": hashlib.sha256((cm.email or "").encode("utf-8")).hexdigest()[:16],
                "quote_email_count": cm.quote_email_count,
                "invoice_email_count": cm.invoice_email_count,
                "purchase_email_count": cm.purchase_email_count,
                "business_doc_email_count": cm.business_doc_email_count,
                "quote_doc_count": cm.quote_doc_count,
                "invoice_doc_count": cm.invoice_doc_count,
                "first_seen_at": cm.first_seen_at,
                "last_seen_at": cm.last_seen_at,
            }
            for cm in contact_master
            if cm.has_typed_commercial_evidence
        ],
        key=lambda x: x["email_hash"],
    )
    payload = {
        "algorithm": OPPORTUNITY_SOURCE_FINGERPRINT_ALGORITHM_VERSION,
        "deals": deal_rows,
        "events": event_rows,
        "documents": doc_rows,
        "payments": pay_rows,
        "signals": signal_rows,
        "contact_master_history": history_rows,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
