"""Declarative ranking and status policy shared by execution + fingerprints."""

from __future__ import annotations

from typing import Any

from origenlab_email_pipeline.commercial_procurement_contact_resolution.constants import (
    LABORATORY_ROLE_TOKENS,
    NEXT_ACTIONS,
    PROCUREMENT_ROLE_TOKENS,
    SEARCH_STAGES,
    UNSUITABLE_ROLE_TOKENS,
)


def contact_resolution_policy_spec() -> dict[str, Any]:
    """Authoritative policy for ranking, status precedence, and next actions."""
    return {
        "version": "contact_resolution_policy_v1",
        "ranking_tiers": [
            {
                "id": "exact_buyer_email",
                "rank": 0,
                "requires": ["usable_email", "account_membership", "buyer_email_exact"],
            },
            {
                "id": "verified_suitable",
                "rank": 1,
                "requires": [
                    "usable_email",
                    "account_membership",
                    "suitable_role",
                    "explicit_verification",
                    "not_safety_blocked",
                ],
            },
            {
                "id": "suitable_role_unverified",
                "rank": 2,
                "requires": [
                    "usable_email",
                    "account_membership",
                    "suitable_role",
                    "not_safety_blocked",
                ],
            },
            {
                "id": "role_review",
                "rank": 3,
                "requires": ["usable_email", "account_membership", "not_safety_blocked"],
            },
            {
                "id": "role_known_email_missing",
                "rank": 4,
                "requires": ["account_membership", "suitable_role", "email_missing"],
            },
            {
                "id": "blocked",
                "rank": 90,
                "requires": ["account_membership", "safety_blocked"],
            },
        ],
        "status_precedence": [
            "contact_resolution_deferred",
            "ambiguous_contact",
            "contact_blocked",
            "existing_verified_contact",
            "existing_contact_needs_role_review",
            "role_known_email_missing",
            "contact_research_required",
            "no_contact_found",
        ],
        "next_action_by_status": {
            "contact_resolution_deferred": "resolve_account",
            "existing_verified_contact": "use_existing_contact",
            "existing_contact_needs_role_review": "review_contact_role",
            "role_known_email_missing": "research_contact_if_active",
            "contact_research_required": "research_contact_if_active",
            "ambiguous_contact": "resolve_contact_ambiguity",
            "no_contact_found": "research_contact_if_active",
            "contact_blocked": "blocked_no_action",
        },
        "search_stages": list(SEARCH_STAGES),
        "next_actions": list(NEXT_ACTIONS),
        "role_token_authority": {
            "procurement": sorted(PROCUREMENT_ROLE_TOKENS),
            "laboratory": sorted(LABORATORY_ROLE_TOKENS),
            "unsuitable": sorted(UNSUITABLE_ROLE_TOKENS),
            "note": (
                "Role suitability uses only the explicit contact.role field. "
                "Email local-parts and display names never establish suitability."
            ),
        },
        "verification_policy": {
            "id": "explicit_verification_required_for_existing_verified_contact",
            "accepted_evidence_types": [
                "contact_identity_resolved_high_confidence",
            ],
            "note": (
                "Mere presence in commercial_identity_contact is insufficient. "
                "existing_verified_contact requires usable email, suitable role, "
                "account membership, non-blocking safety, and explicit verification "
                "evidence (high-confidence resolved identity evidence for the contact)."
            ),
        },
        "actionability_policy": {
            "id": "historical_negative_not_actionable_leads",
            "note": (
                "Contact dimension may be resolved for any PR5D tender for audit, "
                "but historical/closed/negative/ambiguous tenders must not become "
                "actionable leads. PR5E never persists leads."
            ),
        },
    }


def classify_role_suitability(role: str | None) -> str:
    """Map an explicit role string to a suitability class (no local-part inference)."""
    text = (role or "").strip().casefold()
    if not text:
        return "unknown"
    # Normalize separators for token containment.
    compact = " ".join(text.replace("/", " ").replace("|", " ").split())
    for token in UNSUITABLE_ROLE_TOKENS:
        if token in compact:
            return "unsuitable"
    for token in PROCUREMENT_ROLE_TOKENS:
        if token in compact:
            return "suitable_procurement"
    for token in LABORATORY_ROLE_TOKENS:
        if token in compact:
            return "suitable_laboratory"
    return "unknown"


def is_suitable_role(suitability: str) -> bool:
    return suitability in {"suitable_procurement", "suitable_laboratory"}
