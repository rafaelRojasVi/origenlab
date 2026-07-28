"""Audit-only relationship / commercial-stage / safety dimension derivation.

These dimensions are evidence-gated candidates for later PRs. They are never
written to production schema and do not replace existing classifications.
"""

from __future__ import annotations

from typing import Any

from origenlab_email_pipeline.qa.commercial_truth_audit.constants import (
    CONSUMER_EMAIL_DOMAINS,
    INTERNAL_DOMAINS,
)
from origenlab_email_pipeline.qa.commercial_truth_audit.redaction import email_domain, normalize_email

_BLOCKED_CLASS = frozenset(
    {
        "already_contacted_block",
        "bounced_block",
        "suppressed_block",
        "supplier_or_internal_block",
        "bounced_suppressed",
    }
)
_BOUNCE_PREFIX = "bounce_"


def _int(row: dict[str, Any], key: str) -> int:
    try:
        return int(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _truthy(row: dict[str, Any], key: str) -> bool:
    val = row.get(key)
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val != 0
    return bool(str(val or "").strip())


def is_consumer_email(email: str | None) -> bool:
    dom = email_domain(email)
    return bool(dom and dom in CONSUMER_EMAIL_DOMAINS)


def is_internal_domain(domain: str | None) -> bool:
    return bool(domain and domain.strip().lower() in INTERNAL_DOMAINS)


def derive_safety_state(row: dict[str, Any]) -> tuple[str, str]:
    """Return (safety_state, reason_code)."""
    classification = str(row.get("classification") or "").strip()
    suppression_reason = str(row.get("suppression_reason_code") or "").strip().lower()
    if suppression_reason.startswith(_BOUNCE_PREFIX) or suppression_reason in {
        "reported_non_delivery",
    }:
        return "bounced", f"suppression:{suppression_reason or 'bounce'}"
    if suppression_reason or _truthy(row, "domain_suppressed"):
        return "suppressed", f"suppression:{suppression_reason or 'domain'}"
    if _truthy(row, "is_blocked") or classification in _BLOCKED_CLASS:
        return "blocked", f"classification:{classification or 'is_blocked'}"
    if classification == "same_domain_contacted_review" or _truthy(row, "same_domain_contacted"):
        return "same_domain_review", "same_domain_evidence"
    if (
        _int(row, "gmail_sent_count") > 0
        or _int(row, "gmail_received_count") > 0
        or str(row.get("outreach_state") or "") in {"contacted", "replied", "snoozed"}
        or classification == "manual_outreach_sent"
    ):
        return "previously_contacted", "gmail_or_outreach_history"
    if classification == "net_new_safe_review" and normalize_email(row.get("email")):
        return "eligible", "net_new_with_email"
    if classification == "research_only_contact_needed":
        return "unknown", "needs_email"
    return "unknown", "insufficient_safety_evidence"


def derive_relationship_state(row: dict[str, Any]) -> tuple[str, str]:
    """Return (relationship_state, reason_code)."""
    classification = str(row.get("classification") or "").strip()
    domain = (str(row.get("domain") or "").strip().lower() or email_domain(row.get("email")))
    if classification == "supplier_or_internal_block" or is_internal_domain(domain):
        return "supplier_or_internal", "internal_or_supplier"
    if classification == "public_tender_review" or _truthy(row, "has_tender_evidence"):
        return "public_buyer", "tender_evidence"
    invoice = _int(row, "invoice_email_count")
    purchase = _int(row, "purchase_email_count")
    quote = _int(row, "quote_email_count")
    labdelivery = _truthy(row, "has_labdelivery_evidence")
    origenlab = _truthy(row, "has_origenlab_gmail_evidence")
    last_seen_days = row.get("days_since_last_seen")
    dormant = False
    if last_seen_days is not None:
        try:
            dormant = int(last_seen_days) >= 365
        except (TypeError, ValueError):
            dormant = False
    if (invoice > 0 or purchase > 0) and not dormant:
        return "existing_customer", "invoice_or_purchase_evidence"
    if (invoice > 0 or purchase > 0 or (labdelivery and quote > 0)) and dormant:
        return "dormant_customer", "stale_customer_evidence"
    if labdelivery and not origenlab:
        return "known_labdelivery", "legacy_labdelivery_mailbox"
    if origenlab or _int(row, "gmail_sent_count") > 0 or _int(row, "gmail_received_count") > 0:
        return "known_origenlab", "origenlab_gmail_history"
    if classification == "net_new_safe_review" and not labdelivery and not origenlab:
        return "net_new", "net_new_safe_review"
    return "unknown", "insufficient_relationship_evidence"


def derive_commercial_stage(row: dict[str, Any]) -> tuple[str, str]:
    """Return (commercial_stage, reason_code). Prefer multi-signal evidence over keywords."""
    classification = str(row.get("classification") or "").strip()
    sent = _int(row, "gmail_sent_count")
    received = _int(row, "gmail_received_count")
    quote = _int(row, "quote_email_count")
    invoice = _int(row, "invoice_email_count")
    purchase = _int(row, "purchase_email_count")
    quote_signals = _int(row, "quote_signal_count")
    procurement_signals = _int(row, "procurement_signal_count")
    technical_signals = _int(row, "technical_signal_count")
    has_deal = _truthy(row, "has_commercial_deal")
    deal_stage = str(row.get("deal_stage") or "").strip().lower()
    has_tender = classification == "public_tender_review" or _truthy(row, "has_tender_evidence")
    last_seen_days = row.get("days_since_last_seen")
    dormant = False
    if last_seen_days is not None:
        try:
            dormant = int(last_seen_days) >= 365
        except (TypeError, ValueError):
            dormant = False

    if has_tender:
        if _truthy(row, "tender_active"):
            return "tender_active", "active_tender_signal"
        return "tender_watch", "tender_signal"

    # Stale customer history is not an active fulfilment case.
    if dormant and (invoice > 0 or purchase > 0 or quote > 0):
        return "post_sale", "dormant_customer_history"

    if deal_stage in {"fulfillment", "fulfilled", "shipping"} or (invoice > 0 and purchase > 0 and sent > 0 and not dormant):
        if _truthy(row, "post_sale_hint"):
            return "post_sale", "invoice_purchase_post_sale"
        return "fulfillment", "invoice_and_purchase_evidence"

    if deal_stage in {"won", "closed_won"} or (purchase > 0 and invoice > 0):
        return "won", "purchase_and_invoice_evidence"

    if procurement_signals > 0 and invoice == 0 and (quote > 0 or quote_signals > 0 or has_deal):
        return "purchase_pending", "procurement_without_invoice"

    if technical_signals > 0 and received > 0 and quote > 0:
        return "technical_review", "technical_signal_with_quote_thread"

    if quote > 0 or quote_signals > 0:
        if sent > 0 and (quote_signals > 0 or _truthy(row, "quote_outbound")):
            return "quote_sent", "quote_outbound_evidence"
        if received > 0 or _truthy(row, "quote_inbound"):
            return "quote_requested", "quote_inbound_evidence"
        return "quote_preparing", "quote_evidence_ambiguous_direction"

    if received > 0 and (quote_signals > 0 or technical_signals > 0 or procurement_signals > 0):
        return "qualifying", "inbound_with_commercial_signals"

    if sent > 0 and received == 0:
        return "contacted_no_reply", "sent_without_reply"

    if classification == "net_new_safe_review" and normalize_email(row.get("email")):
        return "contact_planned", "ready_net_new"

    if classification == "research_only_contact_needed" or not normalize_email(row.get("email")):
        return "research_needed", "missing_email_or_research_only"

    if _truthy(row, "in_contact_master_only"):
        return "database_only", "mart_without_prospect_action"

    if sent > 0 or received > 0 or classification:
        return "unknown", "history_without_stage_evidence"
    return "unknown", "insufficient_stage_evidence"


def classify_already_contacted_breakdown(row: dict[str, Any]) -> tuple[str, str]:
    """Map an already_contacted row into a finer audit-only bucket."""
    stage, stage_reason = derive_commercial_stage(row)
    relationship, _ = derive_relationship_state(row)
    sent = _int(row, "gmail_sent_count")
    received = _int(row, "gmail_received_count")
    quote = _int(row, "quote_email_count") + _int(row, "quote_signal_count")
    purchase = _int(row, "purchase_email_count") + _int(row, "procurement_signal_count")
    invoice = _int(row, "invoice_email_count")

    if relationship == "dormant_customer" or (
        relationship == "known_labdelivery" and sent + received > 0 and quote == 0 and purchase == 0
    ):
        return "dormant", f"relationship:{relationship}"
    if stage in {"fulfillment", "post_sale", "won"} or (invoice > 0 and purchase > 0):
        return "fulfillment_or_post_sale", stage_reason
    if stage == "purchase_pending" or (purchase > 0 and invoice == 0):
        return "purchase_pending", stage_reason
    if relationship == "existing_customer" or (invoice > 0 or purchase > 0):
        return "existing_customer", f"relationship:{relationship}"
    if stage in {"quote_requested", "quote_preparing", "quote_sent", "technical_review"} or quote > 0:
        return "quotation_related", stage_reason
    if stage == "qualifying" or (received > 0 and (quote > 0 or purchase > 0 or _int(row, "technical_signal_count") > 0)):
        return "active_inquiry", stage_reason
    if sent > 0 and received == 0 and quote == 0 and purchase == 0 and invoice == 0:
        # Campaign / cold outreach recipient with no commercial depth.
        return "campaign_recipient_only", "sent_only_no_commercial_depth"
    return "undetermined", "insufficient_breakdown_evidence"


def enrich_audit_dimensions(row: dict[str, Any]) -> dict[str, Any]:
    """Attach audit-only dimensions and reason codes to a prospect evidence row."""
    out = dict(row)
    safety, safety_reason = derive_safety_state(out)
    relationship, relationship_reason = derive_relationship_state(out)
    stage, stage_reason = derive_commercial_stage(out)
    out["audit_safety_state"] = safety
    out["audit_safety_reason"] = safety_reason
    out["audit_relationship_state"] = relationship
    out["audit_relationship_reason"] = relationship_reason
    out["audit_commercial_stage"] = stage
    out["audit_commercial_stage_reason"] = stage_reason
    if str(out.get("commercial_action_bucket") or "") == "already_contacted":
        breakdown, breakdown_reason = classify_already_contacted_breakdown(out)
        out["audit_already_contacted_breakdown"] = breakdown
        out["audit_already_contacted_breakdown_reason"] = breakdown_reason
    else:
        out["audit_already_contacted_breakdown"] = ""
        out["audit_already_contacted_breakdown_reason"] = ""
    out["is_consumer_email"] = is_consumer_email(out.get("email"))
    return out
