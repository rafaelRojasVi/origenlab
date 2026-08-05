"""Declarative ranking and status policy shared by execution + fingerprints."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from origenlab_email_pipeline.commercial_procurement_contact_resolution.constants import (
    LABORATORY_ROLE_TOKENS,
    NEXT_ACTIONS,
    PROCUREMENT_ROLE_TOKENS,
    SEARCH_STAGES,
    UNSUITABLE_ROLE_TOKENS,
)

# Identity statuses that may never be selected (account-incompatible / unresolved).
NONSELECTABLE_IDENTITY_STATUSES: frozenset[str] = frozenset(
    {
        "ambiguous",
        "unlinked",
        "needs_review",
        "internal_actor",
        "",
    }
)

SELECTABLE_IDENTITY_STATUSES: frozenset[str] = frozenset({"resolved"})

# Lifecycle / relevance classes that must not receive actionable research/outreach.
NON_ACTIONABLE_LIFECYCLE_CLASSES: frozenset[str] = frozenset(
    {
        "closed",
        "historical",
        "expired",
        "cancelled",
        "negative",
    }
)
NON_ACTIONABLE_RELEVANCE_CLASSES: frozenset[str] = frozenset(
    {
        "out_of_scope",
        "negative",
        "not_relevant",
        "ambiguous_relevance",
        "insufficient_evidence",
    }
)


def contact_resolution_policy_spec() -> dict[str, Any]:
    """Authoritative policy for ranking, status precedence, verification, actionability."""
    return {
        "version": "contact_resolution_policy_v1",
        "ranking_tiers": [
            {
                "id": "exact_buyer_email",
                "rank": 0,
                "requires": [
                    "usable_email",
                    "account_membership",
                    "identity_resolved",
                    "buyer_email_exact",
                    "not_safety_blocked",
                    "safety_known",
                ],
            },
            {
                "id": "verified_suitable",
                "rank": 1,
                "requires": [
                    "usable_email",
                    "account_membership",
                    "identity_resolved",
                    "suitable_role",
                    "explicit_verification",
                    "not_safety_blocked",
                    "safety_known",
                ],
            },
            {
                "id": "suitable_role_unverified",
                "rank": 2,
                "requires": [
                    "usable_email",
                    "account_membership",
                    "identity_resolved",
                    "suitable_role",
                    "not_safety_blocked",
                    "safety_known",
                ],
            },
            {
                "id": "role_review",
                "rank": 3,
                "requires": [
                    "usable_email",
                    "account_membership",
                    "identity_resolved",
                    "not_safety_blocked",
                    "safety_known",
                ],
            },
            {
                "id": "role_known_email_missing",
                "rank": 4,
                "requires": [
                    "account_membership",
                    "identity_resolved",
                    "suitable_role",
                    "email_missing",
                ],
            },
            {
                "id": "blocked",
                "rank": 90,
                "requires": ["account_membership", "safety_blocked_or_unknown"],
            },
            {
                "id": "identity_incompatible",
                "rank": 91,
                "requires": ["account_membership", "identity_not_resolved"],
            },
        ],
        # Material tiers where equal top candidates create ambiguity (before ID tie-break).
        "material_ambiguity_tiers": [
            "exact_buyer_email",
            "verified_suitable",
            "suitable_role_unverified",
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
        "non_actionable_next_action": "none",
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
            "id": "explicit_pr2_contact_identity_evidence",
            # Audited production PR2 types: only contact_identity is emitted for contacts.
            "accepted_evidence_types": ["contact_identity"],
            "accepted_matching_reason_codes": ["exact_email"],
            "accepted_confidence": ["high"],
            "required_contact_identity_status": ["resolved"],
            "required_contact_identity_confidence": ["high", "high_confidence"],
            "accepted_source_tables": ["contact_master"],
            "accepted_source_planes": ["contact_master"],
            "required_pointer_fields": [
                "evidence_type",
                "source_table",
                "source_record_id",
                "source_plane",
                "origin_plane",
                "evidence_at",
                "matching_reason_code",
                "confidence",
                "subject_kind",
                "subject_id",
            ],
            "note": (
                "Mere presence in commercial_identity_contact is insufficient. "
                "High confidence alone is insufficient. "
                "existing_verified_contact requires usable email, suitable role, "
                "resolved account-compatible identity, non-blocking safety, and at "
                "least one frozen PR2 evidence row matching this predicate."
            ),
        },
        "identity_policy": {
            "selectable_statuses": sorted(SELECTABLE_IDENTITY_STATUSES),
            "nonselectable_statuses": sorted(NONSELECTABLE_IDENTITY_STATUSES),
            "note": (
                "Ambiguous, conflicting, unresolved, needs_review, unlinked, or "
                "internal_actor identities must not be selected."
            ),
        },
        "actionability_policy": {
            "id": "historical_negative_not_actionable_leads",
            "non_actionable_lifecycle_classes": sorted(NON_ACTIONABLE_LIFECYCLE_CLASSES),
            "non_actionable_relevance_classes": sorted(NON_ACTIONABLE_RELEVANCE_CLASSES),
            "actionable_research_statuses": [
                "contact_research_required",
                "role_known_email_missing",
                "no_contact_found",
            ],
            "note": (
                "Contact dimension may be resolved for any PR5D tender for audit, "
                "but historical/closed/negative/ambiguous tenders must not receive "
                "actionable research/outreach next actions. PR5E never persists leads."
            ),
        },
        "status_selection": {
            "verified_requires_tier": ["verified_suitable"],
            "exact_buyer_verified_path": {
                "tier": "exact_buyer_email",
                "also_requires": ["suitable_role", "explicit_verification"],
            },
            "role_review_tiers": [
                "suitable_role_unverified",
                "role_review",
                "exact_buyer_email",
            ],
            "contact_research_required_when": (
                "internal_search_exhausted_and_tender_approved_for_research"
            ),
        },
    }


def classify_role_suitability(role: str | None) -> str:
    """Map an explicit role string to a suitability class (no local-part inference)."""
    text = (role or "").strip().casefold()
    if not text:
        return "unknown"
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


def evidence_satisfies_verification_policy(
    evidence_row: Mapping[str, Any],
    *,
    contact_id: str,
    policy: Mapping[str, Any] | None = None,
) -> bool:
    """Declarative verification predicate shared by execution and fingerprints."""
    spec = (policy or contact_resolution_policy_spec())["verification_policy"]
    if str(evidence_row.get("subject_kind") or "") != "contact":
        return False
    if str(evidence_row.get("subject_id") or "") != contact_id:
        return False
    if str(evidence_row.get("evidence_type") or "") not in set(
        spec["accepted_evidence_types"]
    ):
        return False
    if str(evidence_row.get("matching_reason_code") or "") not in set(
        spec["accepted_matching_reason_codes"]
    ):
        return False
    conf = str(evidence_row.get("confidence") or "").strip().lower()
    if conf not in {c.lower() for c in spec["accepted_confidence"]}:
        return False
    if str(evidence_row.get("source_table") or "") not in set(
        spec["accepted_source_tables"]
    ):
        return False
    if str(evidence_row.get("source_plane") or "") not in set(
        spec["accepted_source_planes"]
    ):
        return False
    # Pointer fields must be present (evidence_at may be empty string but key present).
    for field in spec["required_pointer_fields"]:
        if field not in evidence_row:
            return False
        if field in {
            "evidence_type",
            "source_table",
            "source_record_id",
            "source_plane",
            "origin_plane",
            "matching_reason_code",
            "confidence",
            "subject_kind",
            "subject_id",
        } and not str(evidence_row.get(field) or "").strip():
            return False
    return True


def contact_has_explicit_verification(
    *,
    identity_status: str,
    identity_confidence: str,
    evidence_rows: Sequence[Mapping[str, Any]],
    contact_id: str,
    policy: Mapping[str, Any] | None = None,
) -> bool:
    spec = (policy or contact_resolution_policy_spec())["verification_policy"]
    if identity_status not in set(spec["required_contact_identity_status"]):
        return False
    conf = (identity_confidence or "").strip().lower()
    if conf not in {c.lower() for c in spec["required_contact_identity_confidence"]}:
        return False
    return any(
        evidence_satisfies_verification_policy(ev, contact_id=contact_id, policy=policy)
        for ev in evidence_rows
    )


def feature_flags_for_candidate(
    *,
    has_usable_email: bool,
    account_membership: bool,
    identity_status: str,
    role_suitability: str,
    verified: bool,
    buyer_email_exact: bool,
    safety_blocked: bool,
    safety_unknown: bool,
) -> dict[str, bool]:
    identity_resolved = identity_status in SELECTABLE_IDENTITY_STATUSES
    return {
        "usable_email": has_usable_email,
        "email_missing": not has_usable_email,
        "account_membership": account_membership,
        "identity_resolved": identity_resolved,
        "identity_not_resolved": not identity_resolved,
        "suitable_role": is_suitable_role(role_suitability),
        "explicit_verification": verified,
        "buyer_email_exact": buyer_email_exact,
        "not_safety_blocked": (not safety_blocked) and (not safety_unknown),
        "safety_known": not safety_unknown,
        "safety_blocked": safety_blocked and not safety_unknown,
        "safety_blocked_or_unknown": safety_blocked or safety_unknown,
    }


def assign_ranking_tier(
    features: Mapping[str, bool],
    *,
    policy: Mapping[str, Any] | None = None,
) -> tuple[str, tuple[str, ...], bool]:
    """Assign the first matching ranking tier from declarative requires lists."""
    spec = policy or contact_resolution_policy_spec()
    reasons: list[str] = []
    for tier in spec["ranking_tiers"]:
        reqs = list(tier["requires"])
        if all(features.get(r, False) for r in reqs):
            reasons = list(reqs)
            selectable = tier["id"] not in {
                "blocked",
                "identity_incompatible",
                "role_known_email_missing",
            }
            if features.get("safety_blocked_or_unknown"):
                selectable = False
            if not features.get("identity_resolved"):
                selectable = False
            return str(tier["id"]), tuple(reasons), selectable
    return "identity_incompatible", ("no_tier_matched",), False


def tender_allows_actionable_research(
    *,
    lifecycle_class: str,
    relevance_class: str,
    policy: Mapping[str, Any] | None = None,
) -> bool:
    spec = (policy or contact_resolution_policy_spec())["actionability_policy"]
    life = (lifecycle_class or "").strip().lower()
    rel = (relevance_class or "").strip().lower()
    if life in {c.lower() for c in spec["non_actionable_lifecycle_classes"]}:
        return False
    if rel in {c.lower() for c in spec["non_actionable_relevance_classes"]}:
        return False
    return True


def next_action_for_status(
    status: str,
    *,
    lifecycle_class: str,
    relevance_class: str,
    policy: Mapping[str, Any] | None = None,
) -> str:
    spec = policy or contact_resolution_policy_spec()
    action = str(spec["next_action_by_status"][status])
    actionable_statuses = set(
        spec["actionability_policy"]["actionable_research_statuses"]
    )
    if status in actionable_statuses and not tender_allows_actionable_research(
        lifecycle_class=lifecycle_class,
        relevance_class=relevance_class,
        policy=spec,
    ):
        return str(spec["non_actionable_next_action"])
    return action
