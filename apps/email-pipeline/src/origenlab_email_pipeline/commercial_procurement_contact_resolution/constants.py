"""PR5E contact-resolution planner constants (read-only; no persistence/outreach)."""

from __future__ import annotations

from typing import Final

from origenlab_email_pipeline.commercial_procurement_live_relevance.constants import (
    CANDIDATE_OUTCOME_STATES,
    CONTACT_RESOLUTION_DEFERRED,
    CONTACT_RESOLUTION_STATUSES,
    CONTACT_RESOLVER_VERSION,
    CONTACT_STATUS_SEMANTICS,
)

CONTACT_RESOLUTION_PLANNER_VERSION: Final = "procurement_contact_resolution_planner_v1"
CONTACT_RESOLUTION_RULES_VERSION: Final = "procurement_contact_resolution_rules_v1"

CONTACT_INPUT_FP_ALGORITHM: Final = "contact_resolution_input_fp_v1"
CONTACT_RULES_FP_ALGORITHM: Final = "contact_resolution_rules_fp_v1"
CONTACT_BUILD_FP_ALGORITHM: Final = "contact_resolution_build_fp_v1"
CONTACT_SEMANTIC_DIGEST_ALGORITHM: Final = "contact_resolution_semantic_digest_v1"

# Re-export PR5A vocabulary — single source of truth.
CONTACT_RESOLUTION_STATUSES_V1: Final = CONTACT_RESOLUTION_STATUSES
CONTACT_RESOLUTION_DEFERRED_V1: Final = CONTACT_RESOLUTION_DEFERRED
CANDIDATE_OUTCOME_STATES_V1: Final = CANDIDATE_OUTCOME_STATES
CONTACT_STATUS_SEMANTICS_V1: Final = CONTACT_STATUS_SEMANTICS
CONTACT_RESOLVER_VERSION_V1: Final = CONTACT_RESOLVER_VERSION

ORGANIZATION_RESOLUTION_STATUSES: Final = (
    "linked",
    "unlinked",
    "ambiguous",
    "refused",
    "deferred_insufficient_buyer_fields",
)

ORGANIZATION_RESOLUTION_SOURCES: Final = (
    "pr4_linked_consistent",
    "pr4_linked_conflict",
    "pr4_candidate_conflict",
    "pr4_constituent_incomplete",
    "pr4_linked_stale",
    "pr4_constituents_not_unanimously_linked",
    "live_link_route",
    "buyer_fields_insufficient",
)

ROLE_SUITABILITY_CLASSES: Final = (
    "suitable_procurement",
    "suitable_laboratory",
    "unknown",
    "unsuitable",
)

SEARCH_STAGES: Final = (
    "pr2_account_contacts",
    "buyer_email_exact_match",
    "safety_gate",
)

NEXT_ACTIONS: Final = (
    "resolve_account",
    "use_existing_contact",
    "review_contact_role",
    "research_contact_if_active",
    "resolve_contact_ambiguity",
    "blocked_no_action",
    "none",
)

FORBIDDEN_CLI_FLAGS: Final = (
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

# Explicit role tokens audited from fixtures/tests — never inferred from email local-part.
PROCUREMENT_ROLE_TOKENS: Final = frozenset(
    {
        "compras",
        "adquisiciones",
        "adquisicion",
        "proveedores",
        "licitaciones",
        "contratacion",
        "buyer",
        "procurement",
        "purchasing",
        "jefa de adquisiciones",
        "jefe de adquisiciones",
        "jefe de compras",
        "jefa de compras",
    }
)

LABORATORY_ROLE_TOKENS: Final = frozenset(
    {
        "laboratorio",
        "laboratory",
        "lab manager",
        "jefe de laboratorio",
        "jefa de laboratorio",
        "analisis",
        "análisis",
    }
)

UNSUITABLE_ROLE_TOKENS: Final = frozenset(
    {
        "estudiante",
        "student",
        "pasante",
        "intern",
        "recepcion",
        "recepción",
        "contabilidad",
        "accounting",
        "rrhh",
        "recursos humanos",
    }
)
