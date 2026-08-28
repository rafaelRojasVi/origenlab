"""Customer-quote vs supplier-RFQ split.

Reuses the same supplier-domain exclusion and outbound-RFQ subject cues
already proven correct in warm_case_role_classification.py:274-289,322-334 —
this is a narrower, independently-testable wrapper around the same primitives,
not a reimplementation of the full warm-case waterfall.
"""

from __future__ import annotations

from typing import Literal

from origenlab_email_pipeline.warm_case_sender_rules import (
    contact_email_from_recipients,
    email_domain,
    is_supplier_vendor_domain,
)

SendDirection = Literal[
    "customer_quote_candidate", "supplier_rfq", "internal_only", "ambiguous"
]

_OUTBOUND_RFQ_CUES = (
    "request for quotation",
    "quotation request",
    "request for quote",
    "rfq",
)
_QUOTE_SUBJECT_CUES = ("cotiz", "quote", "presupuesto")


def classify_send_direction(
    *, recipients: str | None, subject: str | None,
) -> tuple[SendDirection, str]:
    contact = contact_email_from_recipients(recipients or "")
    if not contact:
        return "internal_only", ""

    subj_l = (subject or "").lower()

    if is_supplier_vendor_domain(email_domain(contact)):
        return "supplier_rfq", contact

    has_rfq_cue = any(cue in subj_l for cue in _OUTBOUND_RFQ_CUES)
    has_quote_cue = any(cue in subj_l for cue in _QUOTE_SUBJECT_CUES)

    if has_rfq_cue:
        return "supplier_rfq", contact
    if has_quote_cue:
        return "customer_quote_candidate", contact
    return "ambiguous", contact
