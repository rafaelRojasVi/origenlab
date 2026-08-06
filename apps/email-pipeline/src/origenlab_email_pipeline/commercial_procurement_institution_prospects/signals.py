"""Commercial evidence signal classification (purchase ≠ maintenance ≠ rental)."""

from __future__ import annotations

from origenlab_email_pipeline.commercial_procurement_institution_prospects.constants import (
    CONSUMABLE_RELEVANCE,
    EXCLUDED_RELEVANCE,
    INSTALLED_BASE_RELEVANCE,
    PURCHASE_RELEVANCE,
    RENTAL_RELEVANCE,
    REVIEW_RELEVANCE,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.constants import (
    RECURRENCE_OBSERVED_ONCE,
    RECURRENCE_REPEATED,
)


def classify_commercial_evidence_signal(relevance_class: str) -> str:
    rc = (relevance_class or "").strip()
    if rc in PURCHASE_RELEVANCE:
        return "equipment_purchase_signal"
    if rc in INSTALLED_BASE_RELEVANCE:
        return "installed_base_signal"
    if rc in RENTAL_RELEVANCE:
        return "rental_or_comodato_signal"
    if rc in CONSUMABLE_RELEVANCE:
        return "consumable_or_reagent_signal"
    if rc in REVIEW_RELEVANCE:
        return "review_required_signal"
    if rc in EXCLUDED_RELEVANCE:
        return "excluded_unrelated"
    return "review_required_signal"


def recurrence_label(distinct_tender_count: int) -> str:
    if distinct_tender_count >= 2:
        return RECURRENCE_REPEATED
    return RECURRENCE_OBSERVED_ONCE


def is_equipment_purchase_signal(signal: str) -> bool:
    return signal == "equipment_purchase_signal"


__all__ = [
    "classify_commercial_evidence_signal",
    "is_equipment_purchase_signal",
    "recurrence_label",
]
