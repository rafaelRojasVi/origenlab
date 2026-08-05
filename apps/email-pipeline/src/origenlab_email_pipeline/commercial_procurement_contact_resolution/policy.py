"""Declarative ranking, status precedence, verification, and actionability policy."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from origenlab_email_pipeline.commercial_procurement_candidate_planner.constants import (
    CURRENTNESS_CLASSES,
    LIFECYCLE_CLASSES,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.constants import (
    LABORATORY_ROLE_TOKENS,
    NEXT_ACTIONS,
    PROCUREMENT_ROLE_TOKENS,
    SEARCH_STAGES,
    UNSUITABLE_ROLE_TOKENS,
)
from origenlab_email_pipeline.commercial_procurement_live_relevance.constants import (
    NEGATIVE_RELEVANCE_CLASSES,
    RELEVANCE_CLASSES,
    STRONG_RELEVANCE_CLASSES,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.evaluation import (
    LABEL_STATUSES_METRIC_ELIGIBLE,
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

# Explicitly permitted deterministic eligibility (fail closed otherwise).
ACTIONABLE_LIFECYCLE_CLASSES: frozenset[str] = frozenset({"active_open"})
ACTIONABLE_CURRENTNESS_CLASSES: frozenset[str] = frozenset(
    {"current_authoritative_snapshot"}
)
ACTIONABLE_RELEVANCE_CLASSES: frozenset[str] = frozenset(STRONG_RELEVANCE_CLASSES)

# Next actions that imply approved lead / external research or outreach use.
GATED_EXTERNAL_OR_LEAD_ACTIONS: frozenset[str] = frozenset(
    {
        "use_existing_contact",
        "research_contact_if_active",
    }
)

# Weak / non-strong relevance classes that remain in the canonical vocabulary.
WEAK_RELEVANCE_CLASSES: frozenset[str] = frozenset(
    {
        "laboratory_context_only",
        "ambiguous",
    }
) | frozenset(NEGATIVE_RELEVANCE_CLASSES)


def contact_resolution_policy_spec() -> dict[str, Any]:
    """Authoritative policy for ranking, status precedence, verification, actionability."""
    return {
        "version": "contact_resolution_policy_v2",
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
        "material_ambiguity_tiers": [
            "exact_buyer_email",
            "verified_suitable",
            "suitable_role_unverified",
        ],
        # Executable final-status order — first matching applicable status wins.
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
                "Verification is recomputed from independently frozen PR2 evidence."
            ),
        },
        "identity_policy": {
            "selectable_statuses": sorted(SELECTABLE_IDENTITY_STATUSES),
            "nonselectable_statuses": sorted(NONSELECTABLE_IDENTITY_STATUSES),
        },
        "actionability_policy": {
            "id": "canonical_pr5a_pr5c_fail_closed_v2",
            "lifecycle_vocabulary": list(LIFECYCLE_CLASSES),
            "relevance_vocabulary": list(RELEVANCE_CLASSES),
            "currentness_vocabulary": list(CURRENTNESS_CLASSES),
            "actionable_lifecycle_classes": sorted(ACTIONABLE_LIFECYCLE_CLASSES),
            "actionable_relevance_classes": sorted(ACTIONABLE_RELEVANCE_CLASSES),
            "actionable_currentness_classes": sorted(ACTIONABLE_CURRENTNESS_CLASSES),
            "negative_relevance_classes": sorted(NEGATIVE_RELEVANCE_CLASSES),
            "weak_relevance_classes": sorted(WEAK_RELEVANCE_CLASSES),
            "required_validation_statuses": sorted(LABEL_STATUSES_METRIC_ELIGIBLE),
            "gated_next_actions": sorted(GATED_EXTERNAL_OR_LEAD_ACTIONS),
            "require_independently_reviewed": True,
            "unknown_values_non_actionable": True,
            "note": (
                "Unknown lifecycle/relevance/currentness fail closed. "
                "Only active_open + strong relevance + current authoritative "
                "currentness + reviewed/gold independent validation can unlock "
                "gated lead/research actions. Unreviewed PR5D predictions never "
                "imply approved leads. Historical truth comes from PR5C currentness."
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


def tender_passes_deterministic_eligibility(
    *,
    lifecycle_class: str,
    relevance_class: str,
    currentness_class: str,
    policy: Mapping[str, Any] | None = None,
) -> tuple[bool, tuple[str, ...]]:
    """Fail-closed eligibility from canonical PR5A/PR5C vocabularies."""
    spec = (policy or contact_resolution_policy_spec())["actionability_policy"]
    reasons: list[str] = []
    life = (lifecycle_class or "").strip()
    rel = (relevance_class or "").strip()
    curr = (currentness_class or "").strip()

    life_vocab = set(spec["lifecycle_vocabulary"])
    rel_vocab = set(spec["relevance_vocabulary"])
    curr_vocab = set(spec["currentness_vocabulary"])

    if not life or life not in life_vocab:
        reasons.append("lifecycle_unknown_or_noncanonical")
    elif life not in set(spec["actionable_lifecycle_classes"]):
        reasons.append(f"lifecycle_not_actionable:{life}")

    if not rel or rel not in rel_vocab:
        reasons.append("relevance_unknown_or_noncanonical")
    elif rel not in set(spec["actionable_relevance_classes"]):
        reasons.append(f"relevance_not_strong:{rel}")

    if not curr or curr not in curr_vocab:
        reasons.append("currentness_unknown_or_noncanonical")
    elif curr not in set(spec["actionable_currentness_classes"]):
        reasons.append(f"currentness_not_actionable:{curr}")

    return (not reasons), tuple(reasons)


def relevance_is_validated(
    *,
    label_status: str | None,
    independently_reviewed: bool,
    policy: Mapping[str, Any] | None = None,
) -> bool:
    spec = (policy or contact_resolution_policy_spec())["actionability_policy"]
    status = (label_status or "").strip().lower()
    if status not in {s.lower() for s in spec["required_validation_statuses"]}:
        return False
    if spec.get("require_independently_reviewed") and not independently_reviewed:
        return False
    return True


def tender_allows_gated_lead_or_research(
    *,
    lifecycle_class: str,
    relevance_class: str,
    currentness_class: str,
    label_status: str | None,
    independently_reviewed: bool,
    policy: Mapping[str, Any] | None = None,
) -> tuple[bool, tuple[str, ...]]:
    """Full fail-closed gate for lead/research next actions."""
    ok, reasons = tender_passes_deterministic_eligibility(
        lifecycle_class=lifecycle_class,
        relevance_class=relevance_class,
        currentness_class=currentness_class,
        policy=policy,
    )
    if not ok:
        return False, reasons
    if not relevance_is_validated(
        label_status=label_status,
        independently_reviewed=independently_reviewed,
        policy=policy,
    ):
        return False, ("relevance_unvalidated_or_unreviewed",)
    return True, ()


def next_action_for_status(
    status: str,
    *,
    lifecycle_class: str,
    relevance_class: str,
    currentness_class: str,
    label_status: str | None,
    independently_reviewed: bool,
    policy: Mapping[str, Any] | None = None,
) -> str:
    spec = policy or contact_resolution_policy_spec()
    action = str(spec["next_action_by_status"][status])
    gated = set(spec["actionability_policy"]["gated_next_actions"])
    if action in gated:
        allowed, _ = tender_allows_gated_lead_or_research(
            lifecycle_class=lifecycle_class,
            relevance_class=relevance_class,
            currentness_class=currentness_class,
            label_status=label_status,
            independently_reviewed=independently_reviewed,
            policy=spec,
        )
        if not allowed:
            return str(spec["non_actionable_next_action"])
    return action


# Back-compat alias used by older call sites during transition.
def tender_allows_actionable_research(
    *,
    lifecycle_class: str,
    relevance_class: str,
    currentness_class: str = "current_authoritative_snapshot",
    label_status: str | None = None,
    independently_reviewed: bool = False,
    policy: Mapping[str, Any] | None = None,
) -> bool:
    ok, _ = tender_allows_gated_lead_or_research(
        lifecycle_class=lifecycle_class,
        relevance_class=relevance_class,
        currentness_class=currentness_class,
        label_status=label_status,
        independently_reviewed=independently_reviewed,
        policy=policy,
    )
    return ok
