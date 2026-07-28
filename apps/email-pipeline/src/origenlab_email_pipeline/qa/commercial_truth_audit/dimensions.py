"""Audit-only relationship / stage / safety / procurement dimension derivation.

These dimensions are evidence-gated candidates for later PRs. They are never
written to production schema and do not replace existing classifications.
"""

from __future__ import annotations

from typing import Any

from origenlab_email_pipeline.qa.commercial_truth_audit.constants import (
    CONSUMER_EMAIL_DOMAINS,
    FULFILLMENT_DEAL_STATUSES,
    INTERNAL_DOMAINS,
    PURCHASE_PENDING_DEAL_STATUSES,
    QUOTE_DEAL_STATUSES,
)
from origenlab_email_pipeline.qa.commercial_truth_audit.emails import normalize_valid_email
from origenlab_email_pipeline.qa.commercial_truth_audit.redaction import email_domain

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
    if classification == "net_new_safe_review" and normalize_valid_email(row.get("email")):
        return "eligible", "net_new_with_email"
    if classification == "research_only_contact_needed":
        return "unknown", "needs_email"
    return "unknown", "insufficient_safety_evidence"


def derive_procurement_context(row: dict[str, Any]) -> tuple[str, str]:
    """Return (procurement_context, reason_code). Never replaces relationship/stage."""
    classification = str(row.get("classification") or "").strip()
    has_tender = classification == "public_tender_review" or _truthy(row, "has_tender_evidence")
    if not has_tender:
        return "none", "no_tender_evidence"
    if _truthy(row, "tender_historical_only"):
        return "historical_tender", "historical_tender_flag"
    if _truthy(row, "tender_active"):
        return "tender_active", "active_tender_signal"
    if classification == "public_tender_review":
        # Prospect tender queue without explicit closed/historical flag.
        return "tender_active", "public_tender_review_classification"
    return "tender_watch", "tender_signal_watch"


def derive_relationship_state(row: dict[str, Any]) -> tuple[str, str]:
    """Return (relationship_state, reason_code). Tender does not overwrite customer evidence."""
    classification = str(row.get("classification") or "").strip()
    domain = (str(row.get("domain") or "").strip().lower() or email_domain(row.get("email")))
    if classification == "supplier_or_internal_block" or is_internal_domain(domain):
        return "supplier_or_internal", "internal_or_supplier"

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

    # Historical cumulative purchase/invoice counts prove customer relationship, not stage.
    if (invoice > 0 or purchase > 0) and not dormant:
        return "existing_customer", "invoice_or_purchase_history_counts"
    if (invoice > 0 or purchase > 0 or (labdelivery and quote > 0)) and dormant:
        return "dormant_customer", "stale_customer_evidence"
    if labdelivery and not origenlab:
        return "known_labdelivery", "legacy_labdelivery_mailbox"
    if origenlab or _int(row, "gmail_sent_count") > 0 or _int(row, "gmail_received_count") > 0:
        return "known_origenlab", "origenlab_gmail_history"
    if classification == "net_new_safe_review" and not labdelivery and not origenlab:
        return "net_new", "net_new_safe_review"
    return "unknown", "insufficient_relationship_evidence"


def _stage_result(
    stage: str,
    *,
    reason: str,
    evidence_type: str,
    evidence_at: str = "",
    evidence_source: str = "",
    confidence: str = "heuristic",
    is_current: bool = False,
) -> dict[str, Any]:
    return {
        "stage": stage,
        "reason": reason,
        "stage_evidence_type": evidence_type,
        "stage_evidence_at": evidence_at,
        "stage_evidence_source": evidence_source,
        "stage_confidence": confidence,
        "stage_is_current": int(bool(is_current)),
    }


def derive_commercial_stage(row: dict[str, Any]) -> dict[str, Any]:
    """Derive commercial stage with explicit evidence metadata.

    Lifetime ``quote_email_count`` / ``invoice_email_count`` / ``purchase_email_count``
    are treated as historical cumulative evidence and do **not** alone prove current
    fulfilment, won, post_sale, or purchase_pending.
    """
    classification = str(row.get("classification") or "").strip()
    sent = _int(row, "gmail_sent_count")
    received = _int(row, "gmail_received_count")
    quote = _int(row, "quote_email_count")
    invoice = _int(row, "invoice_email_count")
    purchase = _int(row, "purchase_email_count")
    quote_signals = _int(row, "quote_signal_count")
    procurement_signals = _int(row, "procurement_signal_count")
    technical_signals = _int(row, "technical_signal_count")
    deal_stage = str(row.get("selected_commercial_deal_stage") or row.get("deal_stage") or "").strip().lower()
    deal_at = str(row.get("selected_deal_updated_at") or row.get("latest_commercial_deal_at") or "").strip()
    deal_source = str(row.get("commercial_deal_match_type") or "commercial_deal")
    has_dated_deal = bool(deal_stage and deal_at)
    recent_logistics = _truthy(row, "recent_logistics_evidence")
    recent_po = _truthy(row, "recent_purchase_order_evidence")
    recent_invoice_event = _truthy(row, "recent_invoice_payment_evidence")
    last_seen_days = row.get("days_since_last_seen")
    dormant = False
    if last_seen_days is not None:
        try:
            dormant = int(last_seen_days) >= 365
        except (TypeError, ValueError):
            dormant = False

    # Explicit current deal stage wins when timestamped.
    if has_dated_deal:
        if deal_stage in FULFILLMENT_DEAL_STATUSES:
            return _stage_result(
                "fulfillment",
                reason=f"explicit_deal_status:{deal_stage}",
                evidence_type="commercial_deal_status",
                evidence_at=deal_at,
                evidence_source=deal_source,
                confidence="derived_high_confidence",
                is_current=True,
            )
        if deal_stage in {"closed"} and deal_stage != "cancelled":
            # closed without logistics may be won/post_sale — still current only with date.
            return _stage_result(
                "won",
                reason=f"explicit_deal_status:{deal_stage}",
                evidence_type="commercial_deal_status",
                evidence_at=deal_at,
                evidence_source=deal_source,
                confidence="derived_medium_confidence",
                is_current=True,
            )
        if deal_stage in PURCHASE_PENDING_DEAL_STATUSES:
            return _stage_result(
                "purchase_pending",
                reason=f"explicit_deal_status:{deal_stage}",
                evidence_type="commercial_deal_status",
                evidence_at=deal_at,
                evidence_source=deal_source,
                confidence="derived_high_confidence",
                is_current=True,
            )
        if deal_stage in QUOTE_DEAL_STATUSES:
            return _stage_result(
                "quote_sent" if deal_stage == "quoted" else "quote_preparing",
                reason=f"explicit_deal_status:{deal_stage}",
                evidence_type="commercial_deal_status",
                evidence_at=deal_at,
                evidence_source=deal_source,
                confidence="derived_high_confidence",
                is_current=True,
            )

    # Recent explicit operational events (must be pre-flagged by evidence loader).
    if recent_logistics:
        return _stage_result(
            "fulfillment",
            reason="recent_logistics_or_shipment_evidence",
            evidence_type="logistics_event",
            evidence_at=str(row.get("recent_logistics_at") or ""),
            evidence_source=str(row.get("recent_logistics_source") or "explicit_flag"),
            confidence="derived_high_confidence",
            is_current=True,
        )
    if recent_po:
        return _stage_result(
            "purchase_pending",
            reason="recent_purchase_order_evidence",
            evidence_type="purchase_order_event",
            evidence_at=str(row.get("recent_purchase_order_at") or ""),
            evidence_source=str(row.get("recent_purchase_order_source") or "explicit_flag"),
            confidence="derived_high_confidence",
            is_current=True,
        )
    if recent_invoice_event and (quote_signals > 0 or has_dated_deal or _truthy(row, "has_commercial_deal")):
        return _stage_result(
            "purchase_pending",
            reason="recent_invoice_with_active_opportunity",
            evidence_type="invoice_payment_event",
            evidence_at=str(row.get("recent_invoice_payment_at") or ""),
            evidence_source=str(row.get("recent_invoice_payment_source") or "explicit_flag"),
            confidence="derived_medium_confidence",
            is_current=True,
        )

    # Untimestamped deal status cannot prove current operational stage.
    if deal_stage and not deal_at:
        return _stage_result(
            "unknown",
            reason="deal_status_without_usable_timestamp",
            evidence_type="commercial_deal_status_undated",
            evidence_source=deal_source,
            confidence="unavailable",
            is_current=False,
        )

    # Historical cumulative counts → history stages, not current fulfilment.
    if dormant and (invoice > 0 or purchase > 0):
        return _stage_result(
            "customer_history",
            reason="stale_purchase_invoice_counts",
            evidence_type="lifetime_mart_counts",
            evidence_source="contact_master",
            confidence="derived_medium_confidence",
            is_current=False,
        )
    if invoice > 0 or purchase > 0:
        return _stage_result(
            "customer_history",
            reason="lifetime_purchase_invoice_counts_not_current_stage",
            evidence_type="lifetime_mart_counts",
            evidence_source="contact_master",
            confidence="derived_medium_confidence",
            is_current=False,
        )
    if quote > 0 or quote_signals > 0 or procurement_signals > 0 or technical_signals > 0:
        # Signals without dates remain commercial history / weak heuristics.
        if quote_signals > 0 and sent > 0 and _truthy(row, "quote_outbound"):
            return _stage_result(
                "commercial_history",
                reason="undated_quote_signal_with_sent_history",
                evidence_type="commercial_signal_undated",
                evidence_source="commercial_contact_signal_rollup",
                confidence="heuristic",
                is_current=False,
            )
        return _stage_result(
            "commercial_history",
            reason="undated_commercial_signals_or_quote_counts",
            evidence_type="lifetime_or_undated_signals",
            evidence_source="mart_or_intel",
            confidence="heuristic",
            is_current=False,
        )

    if technical_signals > 0 and received > 0:
        return _stage_result(
            "qualifying",
            reason="inbound_technical_signal_undated",
            evidence_type="commercial_signal_undated",
            confidence="heuristic",
            is_current=False,
        )

    if sent > 0 and received == 0:
        return _stage_result(
            "contacted_no_reply",
            reason="sent_without_reply",
            evidence_type="gmail_counts",
            confidence="derived_medium_confidence",
            is_current=False,
        )

    if classification == "net_new_safe_review" and normalize_valid_email(row.get("email")):
        return _stage_result(
            "contact_planned",
            reason="ready_net_new",
            evidence_type="prospect_classification",
            confidence="derived_medium_confidence",
            is_current=True,
        )

    if classification == "research_only_contact_needed" or not normalize_valid_email(row.get("email")):
        return _stage_result(
            "research_needed",
            reason="missing_email_or_research_only",
            evidence_type="prospect_classification",
            confidence="derived_medium_confidence",
            is_current=False,
        )

    if _truthy(row, "in_contact_master_only"):
        return _stage_result(
            "database_only",
            reason="mart_without_prospect_action",
            evidence_type="contact_master",
            confidence="derived_medium_confidence",
            is_current=False,
        )

    if sent > 0 or received > 0 or classification:
        return _stage_result(
            "unknown",
            reason="history_without_current_stage_evidence",
            evidence_type="insufficient",
            confidence="unavailable",
            is_current=False,
        )
    return _stage_result(
        "unknown",
        reason="insufficient_stage_evidence",
        evidence_type="insufficient",
        confidence="unavailable",
        is_current=False,
    )


def classify_already_contacted_breakdown(row: dict[str, Any]) -> tuple[str, str]:
    """Map an already_contacted row into a finer audit-only bucket."""
    stage_info = derive_commercial_stage(row)
    stage = str(stage_info["stage"])
    stage_reason = str(stage_info["reason"])
    relationship, _ = derive_relationship_state(row)
    sent = _int(row, "gmail_sent_count")
    received = _int(row, "gmail_received_count")
    quote = _int(row, "quote_email_count") + _int(row, "quote_signal_count")
    purchase = _int(row, "purchase_email_count") + _int(row, "procurement_signal_count")
    invoice = _int(row, "invoice_email_count")
    is_current = bool(stage_info.get("stage_is_current"))

    if relationship == "dormant_customer" or (
        relationship == "known_labdelivery" and sent + received > 0 and quote == 0 and purchase == 0
    ):
        return "dormant", f"relationship:{relationship}"
    if stage in {"fulfillment", "post_sale", "won"} and is_current:
        return "current_fulfillment_or_post_sale", stage_reason
    if stage in {"customer_history", "commercial_history"} or (
        (invoice > 0 or purchase > 0) and not is_current
    ):
        return "customer_or_commercial_history", stage_reason
    if stage == "purchase_pending" and is_current:
        return "purchase_pending", stage_reason
    if relationship == "existing_customer":
        return "existing_customer", f"relationship:{relationship}"
    if stage in {"quote_requested", "quote_preparing", "quote_sent", "technical_review"} and is_current:
        return "quotation_related", stage_reason
    if stage == "qualifying" or (received > 0 and quote > 0):
        return "active_inquiry", stage_reason
    if sent > 0 and received == 0 and quote == 0 and purchase == 0 and invoice == 0:
        return "campaign_recipient_only", "sent_only_no_commercial_depth"
    return "undetermined", "insufficient_breakdown_evidence"


def enrich_audit_dimensions(row: dict[str, Any]) -> dict[str, Any]:
    """Attach audit-only dimensions and reason codes to a prospect evidence row."""
    out = dict(row)
    safety, safety_reason = derive_safety_state(out)
    relationship, relationship_reason = derive_relationship_state(out)
    procurement, procurement_reason = derive_procurement_context(out)
    stage_info = derive_commercial_stage(out)
    out["audit_safety_state"] = safety
    out["audit_safety_reason"] = safety_reason
    out["audit_relationship_state"] = relationship
    out["audit_relationship_reason"] = relationship_reason
    out["audit_procurement_context"] = procurement
    out["audit_procurement_reason"] = procurement_reason
    out["audit_commercial_stage"] = stage_info["stage"]
    out["audit_commercial_stage_reason"] = stage_info["reason"]
    out["stage_evidence_type"] = stage_info["stage_evidence_type"]
    out["stage_evidence_at"] = stage_info["stage_evidence_at"]
    out["stage_evidence_source"] = stage_info["stage_evidence_source"]
    out["stage_confidence"] = stage_info["stage_confidence"]
    out["stage_is_current"] = stage_info["stage_is_current"]
    if str(out.get("commercial_action_bucket") or "") == "already_contacted":
        breakdown, breakdown_reason = classify_already_contacted_breakdown(out)
        out["audit_already_contacted_breakdown"] = breakdown
        out["audit_already_contacted_breakdown_reason"] = breakdown_reason
    else:
        out["audit_already_contacted_breakdown"] = ""
        out["audit_already_contacted_breakdown_reason"] = ""
    out["is_consumer_email"] = is_consumer_email(out.get("email"))
    return out
