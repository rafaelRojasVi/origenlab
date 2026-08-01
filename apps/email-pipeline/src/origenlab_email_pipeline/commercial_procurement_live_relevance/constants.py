"""PR5A design constants — status, relevance, contact, and outcome vocabularies."""

from __future__ import annotations

from typing import Final

RELEVANCE_CLASSIFIER_VERSION: Final = "procurement_relevance_v1"
ACTIVE_CLASSIFIER_VERSION: Final = "procurement_active_santiago_v1"
CONTACT_RESOLVER_VERSION: Final = "procurement_contact_resolution_v1"
PROPOSED_SCHEMA_VERSION: Final = "commercial_procurement_candidate_v1_design"
AS_OF_TIMEZONE: Final = "America/Santiago"

# Lifecycle status (urgency is a separate closing_soon_bucket dimension).
ACTIVE_STATUS_CLASSES: Final = (
    "active_open",
    "future_scheduled",
    "closed",
    "awarded",
    "cancelled",
    "status_conflict",
    "date_missing",
    "status_unknown",
)

# Analytical closing-soon buckets (not lifecycle classes; not outreach gates).
CLOSING_SOON_BUCKETS: Final = (
    "lt_24h",
    "d1_to_d3",
    "d4_to_d7",
    "gt_7d",
    "not_applicable",
)

RELEVANCE_CLASSES: Final = (
    "exact_catalog_product",
    "strong_equipment_class",
    "compatible_equipment_class",
    "laboratory_context_only",
    "consumable_or_reagent",
    "service_or_maintenance_only",
    "rental_or_comodato",
    "non_laboratory_false_positive",
    "ambiguous",
    "unrelated",
)

STRONG_RELEVANCE_CLASSES: Final = frozenset(
    {
        "exact_catalog_product",
        "strong_equipment_class",
        "compatible_equipment_class",
    }
)

NEGATIVE_RELEVANCE_CLASSES: Final = frozenset(
    {
        "consumable_or_reagent",
        "service_or_maintenance_only",
        "rental_or_comodato",
        "non_laboratory_false_positive",
        "unrelated",
    }
)

CONTACT_RESOLUTION_STATUSES: Final = (
    "existing_verified_contact",
    "existing_contact_needs_role_review",
    "role_known_email_missing",
    "contact_research_required",
    "ambiguous_contact",
    "no_contact_found",
    "contact_blocked",
)

CANDIDATE_OUTCOME_STATES: Final = (
    "relevant_tender",
    "account_resolution_required",
    "contact_research_candidate",
    "outreach_review_candidate",
    "not_eligible",
)

CANDIDATE_SOURCE_KINDS: Final = (
    "pr4",
    "live_snapshot",
    "both",
)

COALESCENCE_STATUSES: Final = (
    "exact_agreement",
    "live_source_newer",
    "pr4_only",
    "live_only",
    "status_conflict",
    "date_conflict",
    "buyer_identity_conflict",
)

# Reuse ChileCompra codes already shared by PR4 / equipment-first API client.
ACTIVE_CHILECOMPRA_STATUS_CODE: Final = "5"
INACTIVE_CHILECOMPRA_STATUS_CODES: Final = frozenset({"6", "7", "8", "18", "19"})
INACTIVE_CHILECOMPRA_STATUS_NAMES: Final = frozenset(
    {
        "cerrada",
        "desierta",
        "adjudicada",
        "revocada",
        "suspendida",
    }
)
PUBLICADA_STATUS_NAME: Final = "publicada"
