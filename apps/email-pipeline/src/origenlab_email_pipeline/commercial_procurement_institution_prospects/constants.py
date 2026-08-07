"""Constants for PR5E.1 institution prospect intelligence."""

from __future__ import annotations

from typing import Final

PLANNER_VERSION: Final[str] = "procurement_institution_prospect_planner_v1"
CONTRACT_VERSION: Final[str] = "institution_prospect_contract_v1"

FORBIDDEN_CLI_FLAGS: Final[tuple[str, ...]] = (
    "--apply",
    "--persist",
    "--network",
    "--ticket",
    "--gmail",
    "--postgres",
    "--outreach",
    "--send",
    "--schedule",
    "--label",
)

# Institution identity namespaces (ChileCompra buyer id ≠ OrigenLab account_id).
IDENTITY_KIND_ACCOUNT: Final[str] = "origenlab_account"
IDENTITY_KIND_CHILECOMPRA_BUYER_ID: Final[str] = "chilecompra_buyer_source_id"
IDENTITY_KIND_BUYER_NAME: Final[str] = "normalized_buyer_name"
IDENTITY_KIND_UNRESOLVED: Final[str] = "unresolved_buyer"
IDENTITY_KIND_REVIEW: Final[str] = "identity_review"

COMMERCIAL_EVIDENCE_SIGNALS: Final[tuple[str, ...]] = (
    "equipment_purchase_signal",
    "installed_base_signal",
    "rental_or_comodato_signal",
    "consumable_or_reagent_signal",
    "review_required_signal",
    "excluded_unrelated",
)

RECURRENCE_OBSERVED_ONCE: Final[str] = "observed_once"
RECURRENCE_REPEATED: Final[str] = "repeated_observed_demand"

CONTACT_GAP_STATUSES: Final[tuple[str, ...]] = (
    "existing_verified_contact",
    "existing_contact_needs_role_review",
    "role_known_email_missing",
    "contacts_present_but_blocked",
    "linked_account_no_contact",
    "account_unlinked",
    "account_ambiguous",
)

OPEN_LIFECYCLE: Final[frozenset[str]] = frozenset({"active_open"})
HISTORICAL_LIFECYCLE: Final[frozenset[str]] = frozenset(
    {"closed", "awarded", "cancelled"}
)

PURCHASE_RELEVANCE: Final[frozenset[str]] = frozenset(
    {
        "exact_catalog_product",
        "strong_equipment_class",
        "compatible_equipment_class",
    }
)
INSTALLED_BASE_RELEVANCE: Final[frozenset[str]] = frozenset(
    {"service_or_maintenance_only"}
)
RENTAL_RELEVANCE: Final[frozenset[str]] = frozenset({"rental_or_comodato"})
CONSUMABLE_RELEVANCE: Final[frozenset[str]] = frozenset({"consumable_or_reagent"})
REVIEW_RELEVANCE: Final[frozenset[str]] = frozenset(
    {"ambiguous", "laboratory_context_only"}
)
EXCLUDED_RELEVANCE: Final[frozenset[str]] = frozenset(
    {"unrelated", "non_laboratory_false_positive"}
)

AXIS_BANDS: Final[tuple[str, ...]] = ("none", "low", "medium", "high")

__all__ = [
    "AXIS_BANDS",
    "COMMERCIAL_EVIDENCE_SIGNALS",
    "CONSUMABLE_RELEVANCE",
    "CONTACT_GAP_STATUSES",
    "CONTRACT_VERSION",
    "EXCLUDED_RELEVANCE",
    "FORBIDDEN_CLI_FLAGS",
    "HISTORICAL_LIFECYCLE",
    "IDENTITY_KIND_ACCOUNT",
    "IDENTITY_KIND_BUYER_NAME",
    "IDENTITY_KIND_CHILECOMPRA_BUYER_ID",
    "IDENTITY_KIND_REVIEW",
    "IDENTITY_KIND_UNRESOLVED",
    "INSTALLED_BASE_RELEVANCE",
    "OPEN_LIFECYCLE",
    "PLANNER_VERSION",
    "PURCHASE_RELEVANCE",
    "RECURRENCE_OBSERVED_ONCE",
    "RECURRENCE_REPEATED",
    "RENTAL_RELEVANCE",
    "REVIEW_RELEVANCE",
]
