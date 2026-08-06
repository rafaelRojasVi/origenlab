"""PR5D product-relevance planner constants (no contact/outreach/persistence)."""

from __future__ import annotations

from typing import Final

from origenlab_email_pipeline.commercial_procurement_live_relevance.constants import (
    NEGATIVE_RELEVANCE_CLASSES,
    RELEVANCE_CLASSES,
    STRONG_RELEVANCE_CLASSES,
)

PRODUCT_RELEVANCE_PLANNER_VERSION: Final = "procurement_product_relevance_planner_v1"
PRODUCT_RELEVANCE_RULES_VERSION: Final = "procurement_product_relevance_rules_v2"
PRODUCT_RELEVANCE_TAXONOMY_VERSION: Final = "procurement_product_relevance_taxonomy_v1"
PRODUCT_TEXT_ADAPTER_VERSION: Final = "procurement_product_text_adapter_v1"

RELEVANCE_INPUT_FP_ALGORITHM: Final = "product_relevance_input_fp_v1"
RELEVANCE_TAXONOMY_FP_ALGORITHM: Final = "product_relevance_taxonomy_fp_v1"
RELEVANCE_RULES_FP_ALGORITHM: Final = "product_relevance_rules_fp_v1"
RELEVANCE_BUILD_FP_ALGORITHM: Final = "product_relevance_build_fp_v1"
RELEVANCE_SEMANTIC_DIGEST_ALGORITHM: Final = "product_relevance_semantic_digest_v1"

# Re-export PR5A vocabulary — single taxonomy source of truth.
RELEVANCE_CLASSES_V1: Final = RELEVANCE_CLASSES
STRONG_RELEVANCE_CLASSES_V1: Final = STRONG_RELEVANCE_CLASSES
NEGATIVE_RELEVANCE_CLASSES_V1: Final = NEGATIVE_RELEVANCE_CLASSES

EVIDENCE_TIERS: Final = (
    "line_product_text",
    "tender_description",
    "title_only",
    "no_usable_product_text",
)

PRODUCT_RESOLUTION_STATUSES: Final = (
    "exact_catalog_verified",
    "equipment_class_only",
    "context_required_unresolved",
    "insufficient_product_text",
    "negative_class_only",
    "mixed_requires_review",
)

UNIT_LINK_STATUSES: Final = (
    "linked",
    "unresolved_missing_snapshot",
    "unresolved_missing_observation",
    "unresolved_empty_text",
    "unresolved_pr4_raw_not_loaded",
)

LABEL_STATUSES: Final = (
    "proposed",
    "reviewed",
    "gold",
    "rejected",
)

FORBIDDEN_CLI_FLAGS: Final = (
    "--apply",
    "--persist",
    "--network",
    "--ticket",
    "--gmail",
    "--postgres",
    "--outreach",
    "--schedule",
)

# Stable reason codes (positive / negative / ambiguity / aggregation).
POSITIVE_REASON_CODES: Final = (
    "exact_catalog_model_match",
    "strong_equipment_class_match",
    "compatible_equipment_class_match",
    "durable_equipment_with_accessories",
)

NEGATIVE_REASON_CODES: Final = (
    "consumable_centrifuge_tubes",
    "consumable_microscope_accessories",
    "consumable_or_reagent_only",
    "service_or_maintenance_only",
    "rental_or_comodato_only",
    "non_laboratory_incubator",
    "non_laboratory_false_positive",
    "centrifugal_pump_not_lab_centrifuge",
    "anthropometric_or_veterinary_scale_not_analytical_balance",
    "microscope_cover_or_accessory_not_purchase",
    "generic_insumos_without_equipment",
)

AMBIGUITY_REASON_CODES: Final = (
    "context_required_sonicator",
    "context_required_agitador",
    "context_required_homogenizer_family",
    "insufficient_product_text",
    "conflicting_line_evidence",
    "title_only_weak_signal",
    "anthropometric_or_veterinary_scale_not_analytical_balance",
)

AGGREGATION_REASON_CODES: Final = (
    "single_unit_decision",
    "strong_positive_survives_negative_lines",
    "mixed_positive_and_negative_requires_review",
    "all_units_negative",
    "all_units_ambiguous_or_empty",
    "no_usable_units",
)
