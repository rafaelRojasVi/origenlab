"""Classify commercial intent and shadow change classes (H1-preserving)."""

from __future__ import annotations

from collections.abc import Sequence

from .capability import classify_classes
from .constants import (
    CAPABILITY_IN_SCOPE,
    CAPABILITY_OUT_OF_SCOPE,
    INTENT_ACCESSORY_OR_CONSUMABLE,
    INTENT_BUYER_EQUIPMENT_ACQUISITION,
    INTENT_CONFLICT_OR_REVIEW,
    INTENT_INCOMPLETE,
    INTENT_LABORATORY_CONTEXT,
    INTENT_METHOD_OR_EXAM,
    INTENT_NONE,
    INTENT_SERVICE_OR_MAINTENANCE,
    INTENT_SUPPLIER_REQUIRED_EQUIPMENT,
    SHADOW_ACCESSORY_OR_CONSUMABLE_ONLY,
    SHADOW_BUNDLED_PARTIAL_CAPABILITY,
    SHADOW_CONFLICT_OR_REVIEW,
    SHADOW_INCOMPLETE_NO_CONCLUSION,
    SHADOW_METHOD_OR_EXAM_ONLY,
    SHADOW_NEW_IN_SCOPE_OPPORTUNITY,
    SHADOW_NEW_OUT_OF_SCOPE_EQUIPMENT,
    SHADOW_SERVICE_OR_MAINTENANCE_ONLY,
    SHADOW_STRENGTHENED_EXISTING_OPPORTUNITY,
    SHADOW_SUPPLIER_REQUIRED_EQUIPMENT,
    SHADOW_UNCHANGED,
)
from .models import ClaimCapability

# Affirmative PR5D acquisition-compatible relevance classes.
_ACQUISITION_RELEVANCE = frozenset(
    {
        "strong_equipment_class",
        "possible_equipment_class",
        "compatible_equipment_class",
    }
)

# Explicit H1 reason — preferred over generic service/maintenance fallback.
_METHOD_EXAM_REASON = "diagnostic_method_or_exam_term_not_equipment"
_SUPPLIER_REQUIRED_REASON = "supplier_required_equipment_not_buyer_purchase"


def commercial_intent_class(
    *,
    relevance_class: str,
    negative_reason_codes: Sequence[str],
    positive_reason_codes: Sequence[str],
    ambiguity_reason_codes: Sequence[str],
    coverage_complete: bool,
    has_anexo_bundle: bool,
    equipment_classes: Sequence[str],
) -> str:
    """Map PR5D aggregate semantics to a commercial-intent label.

    Fail-closed: residual equipment classes alone never promote service,
    ambiguous, mixed, consumable, or laboratory-context aggregates into buyer
    acquisition. Affirmative acquisition-compatible relevance (or an explicit
    trusted strong-class positive under that relevance) is required.
    """
    del ambiguity_reason_codes  # reserved for future conflict nuance
    neg = set(negative_reason_codes or ())
    pos = set(positive_reason_codes or ())
    rel = relevance_class or ""
    classes = tuple(c for c in equipment_classes if c)

    if _SUPPLIER_REQUIRED_REASON in neg:
        return INTENT_SUPPLIER_REQUIRED_EQUIPMENT

    # Explicit method/exam reason beats generic service/maintenance fallback.
    if not classes and _METHOD_EXAM_REASON in neg:
        return INTENT_METHOD_OR_EXAM

    # Affirmative acquisition only — never "classes exist ⇒ buyable".
    if classes and rel in _ACQUISITION_RELEVANCE:
        return INTENT_BUYER_EQUIPMENT_ACQUISITION
    if (
        classes
        and "strong_equipment_class_match" in pos
        and rel in _ACQUISITION_RELEVANCE
    ):
        return INTENT_BUYER_EQUIPMENT_ACQUISITION

    if classes and rel == "laboratory_context_only":
        return INTENT_LABORATORY_CONTEXT
    if classes and rel == "service_or_maintenance_only":
        return INTENT_SERVICE_OR_MAINTENANCE
    if classes and rel in {"ambiguous", "mixed_requires_review"}:
        return INTENT_CONFLICT_OR_REVIEW
    if classes and rel == "consumable_or_reagent":
        return INTENT_ACCESSORY_OR_CONSUMABLE

    if rel == "laboratory_context_only":
        return INTENT_LABORATORY_CONTEXT
    if rel == "service_or_maintenance_only" or "service_or_maintenance_only" in neg:
        return INTENT_SERVICE_OR_MAINTENANCE
    if rel == "consumable_or_reagent" or "consumable_or_reagent_only" in neg:
        return INTENT_ACCESSORY_OR_CONSUMABLE
    if has_anexo_bundle and not coverage_complete and not classes:
        return INTENT_INCOMPLETE
    if not classes:
        return INTENT_NONE
    if rel in {"ambiguous", "mixed_requires_review"}:
        return INTENT_CONFLICT_OR_REVIEW
    return INTENT_NONE


def classify_shadow_change(
    *,
    current_classes: Sequence[str],
    shadow_classes: Sequence[str],
    capabilities: Sequence[ClaimCapability],
    intent: str,
    e2_change_class: str | None,
    e2_interpretation: str | None,
    coverage_complete: bool,
    has_anexo_bundle: bool,
    current_queue_keys: set[str],
    shadow_queue_keys: set[str],
    human_review_required: bool,
) -> str:
    """Choose one shadow change class for the tender."""
    cur = frozenset(current_classes)
    sh = frozenset(shadow_classes)
    new = sh - cur

    if intent == INTENT_SUPPLIER_REQUIRED_EQUIPMENT:
        return SHADOW_SUPPLIER_REQUIRED_EQUIPMENT

    caps_for_new = [c for c in capabilities if c.equipment_class in new] or list(
        capabilities
    )
    in_scope = [c for c in caps_for_new if c.capability == CAPABILITY_IN_SCOPE]
    out_scope = [c for c in caps_for_new if c.capability == CAPABILITY_OUT_OF_SCOPE]

    # Equipment-class outcomes beat title-level service/accessory suppressions
    # when annex recognition actually produced classes.
    if new and in_scope and out_scope:
        return SHADOW_BUNDLED_PARTIAL_CAPABILITY
    if new and in_scope and not cur:
        return SHADOW_NEW_IN_SCOPE_OPPORTUNITY
    if new and in_scope and cur:
        return SHADOW_STRENGTHENED_EXISTING_OPPORTUNITY
    if new and out_scope and not in_scope:
        return SHADOW_NEW_OUT_OF_SCOPE_EQUIPMENT

    if intent == INTENT_SERVICE_OR_MAINTENANCE:
        return SHADOW_SERVICE_OR_MAINTENANCE_ONLY
    if intent == INTENT_ACCESSORY_OR_CONSUMABLE:
        return SHADOW_ACCESSORY_OR_CONSUMABLE_ONLY
    if intent == INTENT_METHOD_OR_EXAM:
        return SHADOW_METHOD_OR_EXAM_ONLY
    if intent == INTENT_INCOMPLETE or (
        has_anexo_bundle
        and not coverage_complete
        and not new
        and e2_interpretation == "absence_unproven"
    ):
        return SHADOW_INCOMPLETE_NO_CONCLUSION
    if intent == INTENT_CONFLICT_OR_REVIEW or human_review_required:
        if (
            e2_change_class == "annex_created_conflict"
            or intent == INTENT_CONFLICT_OR_REVIEW
        ):
            return SHADOW_CONFLICT_OR_REVIEW

    if shadow_queue_keys - current_queue_keys and in_scope:
        return SHADOW_STRENGTHENED_EXISTING_OPPORTUNITY
    if sh == cur and intent in {INTENT_NONE, INTENT_LABORATORY_CONTEXT}:
        return SHADOW_UNCHANGED
    if sh == cur:
        return SHADOW_UNCHANGED
    if human_review_required:
        return SHADOW_CONFLICT_OR_REVIEW
    return SHADOW_UNCHANGED


def capability_partition(
    capabilities: Sequence[ClaimCapability],
) -> dict[str, list[str]]:
    return {
        "in_scope": sorted(
            c.equipment_class for c in capabilities if c.capability == CAPABILITY_IN_SCOPE
        ),
        "out_of_scope": sorted(
            c.equipment_class
            for c in capabilities
            if c.capability == CAPABILITY_OUT_OF_SCOPE
        ),
        "unclear": sorted(
            c.equipment_class
            for c in capabilities
            if c.capability not in {CAPABILITY_IN_SCOPE, CAPABILITY_OUT_OF_SCOPE}
        ),
    }


def claim_capabilities_for(classes: Sequence[str]) -> tuple[ClaimCapability, ...]:
    return classify_classes(tuple(classes))
