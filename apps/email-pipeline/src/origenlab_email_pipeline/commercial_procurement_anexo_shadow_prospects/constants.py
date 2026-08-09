"""ANEXO-P1 vocabularies: shadow change classes and safety flags.

SHADOW ONLY. This lane measures what prospect / queue memberships WOULD change
if annex-backed PR5D evidence were considered. It never mutates production
queues, never persists annex-derived prospect state, and never authorizes
contact or outreach. PR5F is not started here.
"""

from __future__ import annotations

from typing import Final


COMPARISON_VERSION: Final = "anexo_p1_v1"
COMPARISON_ALGORITHM: Final = "anexo_shadow_prospect_comparison_v1"
SHADOW_SOURCE_PLANE: Final = "anexo_shadow_measurement"

# --- shadow change classes ---------------------------------------------------
SHADOW_NEW_IN_SCOPE_OPPORTUNITY: Final = "shadow_new_in_scope_opportunity"
SHADOW_STRENGTHENED_EXISTING_OPPORTUNITY: Final = "shadow_strengthened_existing_opportunity"
SHADOW_NEW_OUT_OF_SCOPE_EQUIPMENT: Final = "shadow_new_out_of_scope_equipment"
SHADOW_BUNDLED_PARTIAL_CAPABILITY: Final = "shadow_bundled_partial_capability"
SHADOW_SERVICE_OR_MAINTENANCE_ONLY: Final = "shadow_service_or_maintenance_only"
SHADOW_ACCESSORY_OR_CONSUMABLE_ONLY: Final = "shadow_accessory_or_consumable_only"
SHADOW_METHOD_OR_EXAM_ONLY: Final = "shadow_method_or_exam_only"
SHADOW_SUPPLIER_REQUIRED_EQUIPMENT: Final = "shadow_supplier_required_equipment"
SHADOW_INCOMPLETE_NO_CONCLUSION: Final = "shadow_incomplete_no_conclusion"
SHADOW_UNCHANGED: Final = "shadow_unchanged"
SHADOW_CONFLICT_OR_REVIEW: Final = "shadow_conflict_or_review"

SHADOW_CHANGE_CLASSES: Final = (
    SHADOW_NEW_IN_SCOPE_OPPORTUNITY,
    SHADOW_STRENGTHENED_EXISTING_OPPORTUNITY,
    SHADOW_NEW_OUT_OF_SCOPE_EQUIPMENT,
    SHADOW_BUNDLED_PARTIAL_CAPABILITY,
    SHADOW_SERVICE_OR_MAINTENANCE_ONLY,
    SHADOW_ACCESSORY_OR_CONSUMABLE_ONLY,
    SHADOW_METHOD_OR_EXAM_ONLY,
    SHADOW_SUPPLIER_REQUIRED_EQUIPMENT,
    SHADOW_INCOMPLETE_NO_CONCLUSION,
    SHADOW_UNCHANGED,
    SHADOW_CONFLICT_OR_REVIEW,
)

# --- claim-level capability (catalog seed, never tender-text inference) ------
CAPABILITY_IN_SCOPE: Final = "in_scope"
CAPABILITY_OUT_OF_SCOPE: Final = "out_of_scope"
CAPABILITY_UNCLEAR: Final = "unclear"

CAPABILITY_CLASSES: Final = (
    CAPABILITY_IN_SCOPE,
    CAPABILITY_OUT_OF_SCOPE,
    CAPABILITY_UNCLEAR,
)

# --- commercial intent labels (H1 semantics) ---------------------------------
INTENT_BUYER_EQUIPMENT_ACQUISITION: Final = "buyer_equipment_acquisition"
INTENT_SUPPLIER_REQUIRED_EQUIPMENT: Final = "supplier_required_equipment"
INTENT_SERVICE_OR_MAINTENANCE: Final = "service_or_maintenance_only"
INTENT_ACCESSORY_OR_CONSUMABLE: Final = "accessory_or_consumable_only"
INTENT_METHOD_OR_EXAM: Final = "method_or_exam_only"
INTENT_LABORATORY_CONTEXT: Final = "laboratory_context_only"
INTENT_INCOMPLETE: Final = "incomplete_coverage"
INTENT_CONFLICT_OR_REVIEW: Final = "conflict_or_review"
INTENT_NONE: Final = "no_equipment_signal"

# --- queue delta labels ------------------------------------------------------
QUEUE_WOULD_ENTER_CURRENT: Final = "would_enter_current_opportunity_queue"
QUEUE_WOULD_LEAVE_CURRENT: Final = "would_leave_current_opportunity_queue"
QUEUE_WOULD_CHANGE_PRIORITY: Final = "would_change_priority"
QUEUE_WOULD_ENTER_MATCH_REVIEW: Final = "would_enter_match_review"
QUEUE_WOULD_ENTER_CONTACT_GAP: Final = "would_enter_contact_gap"
QUEUE_UNCHANGED: Final = "unchanged"

QUEUE_DELTA_CLASSES: Final = (
    QUEUE_WOULD_ENTER_CURRENT,
    QUEUE_WOULD_LEAVE_CURRENT,
    QUEUE_WOULD_CHANGE_PRIORITY,
    QUEUE_WOULD_ENTER_MATCH_REVIEW,
    QUEUE_WOULD_ENTER_CONTACT_GAP,
    QUEUE_UNCHANGED,
)

# Explicit false flags that every summary / artifact must carry.
SAFETY_FALSE_FLAGS: Final = (
    "contact_authorization",
    "outreach_authorization",
    "production_queue_mutated",
    "persisted",
    "pr5f_started",
    "annex_production_integration",
)

FORBIDDEN_CLI_FLAGS: Final = (
    "--apply",
    "--send",
    "--persist",
    "--network",
    "--ticket",
    "--gmail",
    "--postgres",
    "--outreach",
    "--schedule",
    "--label",
)

AS_OF_UTC_DEFAULT: Final = "2026-08-09T20:00:00Z"
PLANNER_VERSION: Final = "anexo_p1_shadow_v1"
FROZEN_52_DIGEST: Final = (
    "fec235f7845186f50298276ef19af5b335491409a249765300f5733d07b599a5"
)
