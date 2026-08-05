"""Internal contact search, ranking, and status selection for PR5E."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any, Mapping, Sequence

from origenlab_email_pipeline.commercial_procurement.sources import (
    assert_active_read_transaction,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.constants import (
    CONTACT_RESOLUTION_DEFERRED,
    CONTACT_RESOLUTION_RULES_VERSION,
    CONTACT_RESOLVER_VERSION,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.frozen_sources import (
    FrozenSourceIndex,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.models import (
    ContactCandidate,
    ContactResolutionConflict,
    ContactResolutionEvidence,
    ContactResolutionSummary,
    OrganizationResolution,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.policy import (
    assign_ranking_tier,
    classify_role_suitability,
    contact_has_explicit_verification,
    contact_resolution_policy_spec,
    exact_buyer_path_qualifies,
    feature_flags_for_candidate,
    is_role_review_tier,
    is_suitable_role,
    next_action_for_status,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.safety import (
    SafetySnapshot,
    evaluate_contact_safety,
    parse_usable_email,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.stable_ids import (
    candidate_id_for,
    conflict_id_for,
    contact_resolution_id_deferred,
    contact_resolution_id_linked,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
    TenderRelevanceDecision,
)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    assert_active_read_transaction(conn)
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return bool(row)


def _role_digest(role: str | None) -> str:
    raw = (role or "").strip()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _validation_status_echo(label_status: str | None) -> str:
    return (label_status or "").strip()


def recompute_summary_semantic_fingerprint(
    *,
    contact_resolution_id: str,
    final_contact_status: str,
    account_id: str | None,
    selected_contact_id: str | None,
    selected_candidate_id: str | None,
    search_stages_completed: Sequence[str],
    next_action: str,
    reason_code: str,
    candidate_ids: Sequence[str] | None = None,
) -> str:
    """Recompute the per-summary semantic fingerprint used by contact search."""
    if final_contact_status == CONTACT_RESOLUTION_DEFERRED:
        return canonical_json_digest(
            {
                "contact_resolution_id": contact_resolution_id,
                "final_contact_status": final_contact_status,
                "account_id": None,
                "selected_contact_id": None,
                "search_stages_completed": [],
                "next_action": next_action,
                "reason_code": reason_code,
            }
        )
    return canonical_json_digest(
        {
            "contact_resolution_id": contact_resolution_id,
            "final_contact_status": final_contact_status,
            "account_id": account_id,
            "selected_contact_id": selected_contact_id,
            "selected_candidate_id": selected_candidate_id,
            "search_stages_completed": list(search_stages_completed),
            "next_action": next_action,
            "reason_code": reason_code,
            "candidate_ids": list(candidate_ids or ()),
        }
    )


def load_contacts_for_account(
    conn: sqlite3.Connection, account_id: str
) -> list[dict[str, Any]]:
    assert_active_read_transaction(conn)
    if not _table_exists(conn, "commercial_identity_contact"):
        return []
    rows = conn.execute(
        """
        SELECT contact_id, normalized_email, display_name, role, account_id,
               account_link_method, identity_confidence, identity_status, email_domain
        FROM commercial_identity_contact
        WHERE account_id = ?
        ORDER BY contact_id
        """,
        (account_id,),
    )
    return [dict(r) for r in rows]


def load_contact_evidence(
    conn: sqlite3.Connection, contact_ids: list[str]
) -> dict[str, list[dict[str, Any]]]:
    assert_active_read_transaction(conn)
    if not contact_ids or not _table_exists(conn, "commercial_identity_evidence"):
        return {}
    out: dict[str, list[dict[str, Any]]] = {cid: [] for cid in contact_ids}
    placeholders = ",".join("?" for _ in contact_ids)
    rows = conn.execute(
        f"""
        SELECT evidence_id, subject_kind, subject_id, source_table, source_record_id,
               source_plane, origin_plane, evidence_type, evidence_at,
               matching_reason_code, confidence
        FROM commercial_identity_evidence
        WHERE subject_kind = 'contact' AND subject_id IN ({placeholders})
        ORDER BY evidence_id
        """,
        tuple(contact_ids),
    )
    for r in rows:
        out.setdefault(str(r["subject_id"]), []).append(dict(r))
    return out


def load_evidence_index(
    conn: sqlite3.Connection, evidence_ids: list[str]
) -> dict[str, dict[str, Any]]:
    """Frozen PR2 evidence rows keyed by evidence_id for pointer resolution."""
    assert_active_read_transaction(conn)
    if not evidence_ids or not _table_exists(conn, "commercial_identity_evidence"):
        return {}
    placeholders = ",".join("?" for _ in evidence_ids)
    rows = conn.execute(
        f"""
        SELECT evidence_id, subject_kind, subject_id, source_table, source_record_id,
               source_plane, origin_plane, evidence_type, evidence_at,
               matching_reason_code, confidence
        FROM commercial_identity_evidence
        WHERE evidence_id IN ({placeholders})
        """,
        tuple(evidence_ids),
    )
    return {str(r["evidence_id"]): dict(r) for r in rows}


def deferred_summary(
    *,
    tender_id: str,
    relevance: TenderRelevanceDecision,
    organization: OrganizationResolution,
    input_fingerprint: str,
    reason_code: str,
    currentness_class: str,
    label_status: str | None = None,
    independently_reviewed: bool = False,
) -> ContactResolutionSummary:
    policy = contact_resolution_policy_spec()
    status = CONTACT_RESOLUTION_DEFERRED
    rid = contact_resolution_id_deferred(
        coalesced_tender_id=tender_id,
        organization_resolution_id=organization.organization_resolution_id,
        reason=reason_code,
    )
    next_action = next_action_for_status(
        status,
        lifecycle_class=relevance.lifecycle_class_echo or "",
        relevance_class=relevance.relevance_class,
        currentness_class=currentness_class,
        label_status=label_status,
        independently_reviewed=independently_reviewed,
        policy=policy,
    )
    sem = recompute_summary_semantic_fingerprint(
        contact_resolution_id=rid,
        final_contact_status=status,
        account_id=None,
        selected_contact_id=None,
        selected_candidate_id=None,
        search_stages_completed=[],
        next_action=next_action,
        reason_code=reason_code,
    )
    return ContactResolutionSummary(
        contact_resolution_id=rid,
        coalesced_tender_id=tender_id,
        relevance_decision_id=relevance.decision_id,
        organization_resolution_id=organization.organization_resolution_id,
        account_id=None,
        final_contact_status=status,
        selected_contact_id=None,
        selected_candidate_id=None,
        search_stages_completed=(),
        next_action=next_action,
        reason_code=reason_code,
        considered_contact_count=0,
        suitable_contact_count=0,
        blocked_contact_count=0,
        relevance_class_echo=relevance.relevance_class,
        lifecycle_class_echo=relevance.lifecycle_class_echo or "",
        currentness_class_echo=currentness_class,
        relevance_validation_status_echo=_validation_status_echo(label_status),
        input_fingerprint=input_fingerprint,
        semantic_fingerprint=sem,
        rules_version=CONTACT_RESOLUTION_RULES_VERSION,
        resolver_version=CONTACT_RESOLVER_VERSION,
    )


def _select_final_status(
    *,
    ranked: list[ContactCandidate],
    policy: Mapping[str, Any],
    tender_id: str,
) -> tuple[str, ContactCandidate | None, str, list[ContactResolutionConflict]]:
    """Policy-driven final status via ``status_precedence``.

    Build the set of applicable statuses, then return the first entry in
    ``policy["status_precedence"]`` that is applicable. Material ties become
    ambiguous before contact_id order can resolve them.
    """
    conflicts: list[ContactResolutionConflict] = []
    # status -> (selected_candidate, reason_code)
    applicable: dict[str, tuple[ContactCandidate | None, str]] = {}
    material_tiers = set(policy["material_ambiguity_tiers"])
    selectable = [c for c in ranked if c.selectable]

    if not ranked:
        applicable["no_contact_found"] = (None, "internal_search_exhausted_empty")
    elif selectable:
        top = selectable[0]
        peers = [
            c
            for c in selectable
            if c.ranking_tier == top.ranking_tier and c.ranking_tier in material_tiers
        ]
        if len(peers) > 1:
            conflicts.append(
                ContactResolutionConflict(
                    conflict_id=conflict_id_for(
                        tender_id=tender_id,
                        contact_ids=[p.contact_id for p in peers],
                        tier=top.ranking_tier,
                    ),
                    coalesced_tender_id=tender_id,
                    conflict_type="ambiguous_contact",
                    reason_code="multiple_competing_contacts",
                    subject_keys=tuple(sorted(p.contact_id for p in peers)),
                    evidence_ids=(),
                )
            )
            applicable["ambiguous_contact"] = (
                None,
                "multiple_competing_contacts",
            )
        else:
            verified_tiers = set(policy["status_selection"]["verified_requires_tier"])
            exact_cfg = policy["status_selection"]["exact_buyer_verified_path"]
            if top.ranking_tier in verified_tiers:
                applicable["existing_verified_contact"] = (
                    top,
                    "verified_suitable_contact_selected",
                )
            elif exact_buyer_path_qualifies(top, policy=policy):
                applicable["existing_verified_contact"] = (
                    top,
                    "verified_suitable_contact_selected",
                )
            elif is_role_review_tier(top.ranking_tier, policy=policy):
                if top.ranking_tier == "suitable_role_unverified":
                    reason = "suitable_role_without_explicit_verification"
                else:
                    reason = "contact_requires_role_review"
                if top.ranking_tier == exact_cfg["tier"]:
                    reason = "contact_present_verification_or_role_incomplete"
                applicable["existing_contact_needs_role_review"] = (top, reason)
            else:
                # Fail closed: treat as role review if somehow selectable outside lists
                applicable["existing_contact_needs_role_review"] = (
                    top,
                    "contact_requires_role_review",
                )
    else:
        # Non-selectable ranked set — accumulate statuses; precedence chooses.
        if any(c.ranking_tier == "role_known_email_missing" for c in ranked):
            applicable["role_known_email_missing"] = (
                None,
                "suitable_role_email_missing",
            )
        if any(c.safety_blocked or c.safety_unknown for c in ranked):
            applicable["contact_blocked"] = (
                None,
                "all_selectable_paths_blocked",
            )
        if (
            "role_known_email_missing" not in applicable
            and "contact_blocked" not in applicable
        ):
            applicable["contact_research_required"] = (
                None,
                "internal_search_exhausted_no_selectable",
            )

    for status in policy["status_precedence"]:
        if status in applicable:
            selected, reason = applicable[status]
            return status, selected, reason, conflicts

    # Fail closed — should be unreachable when precedence covers all statuses.
    return "no_contact_found", None, "status_precedence_exhausted", conflicts


# Public alias for tests / call sites that prefer a non-underscore name.
select_final_status = _select_final_status


def _evidence_from_rows(
    contact_id: str, ev_rows: list[Mapping[str, Any]]
) -> list[ContactResolutionEvidence]:
    out: list[ContactResolutionEvidence] = []
    for ev in ev_rows:
        out.append(
            ContactResolutionEvidence(
                evidence_id=str(ev["evidence_id"]),
                subject_kind=str(ev.get("subject_kind") or "contact"),
                subject_id=contact_id,
                source_table=str(ev.get("source_table") or ""),
                source_record_id=str(ev.get("source_record_id") or ""),
                source_plane=str(ev.get("source_plane") or ""),
                origin_plane=str(ev.get("origin_plane") or ""),
                evidence_type=str(ev.get("evidence_type") or ""),
                evidence_at=str(ev.get("evidence_at") or ""),
                matching_reason_code=str(ev.get("matching_reason_code") or ""),
                confidence=str(ev.get("confidence") or ""),
            )
        )
    return out


def _build_candidate(
    *,
    contact_resolution_id: str,
    tender_id: str,
    account_id: str,
    contact_id: str,
    email: str | None,
    role: str | None,
    identity_status: str,
    identity_confidence: str,
    ev_rows: list[Mapping[str, Any]],
    evidence_ids: tuple[str, ...],
    buyer_email_norm: str | None,
    institution_name: str | None,
    safety: SafetySnapshot,
    policy: Mapping[str, Any],
) -> ContactCandidate:
    has_email = bool(email)
    suitability = classify_role_suitability(role if isinstance(role, str) else None)
    # Always recompute — never trust a pre-set verification_status.
    verified = contact_has_explicit_verification(
        identity_status=identity_status,
        identity_confidence=identity_confidence,
        evidence_rows=ev_rows,
        contact_id=contact_id,
        policy=policy,
    )
    safety_result = evaluate_contact_safety(
        email_norm=email,
        institution_name=institution_name,
        safety=safety,
    )
    buyer_exact = bool(buyer_email_norm and email and email == buyer_email_norm)
    features = feature_flags_for_candidate(
        has_usable_email=has_email,
        account_membership=True,
        identity_status=identity_status,
        role_suitability=suitability,
        verified=verified,
        buyer_email_exact=buyer_exact,
        safety_blocked=bool(safety_result["safety_blocked"])
        and not bool(safety_result.get("safety_unknown")),
        safety_unknown=bool(safety_result.get("safety_unknown")),
    )
    # Email-missing contacts: not selectable by safety, but not safety-blocked.
    if not has_email:
        features = dict(features)
        features["not_safety_blocked"] = True
        features["safety_known"] = True
        features["safety_blocked"] = False
        features["safety_blocked_or_unknown"] = False

    tier, reasons, selectable = assign_ranking_tier(features, policy=policy)
    if not safety_result.get("selectable_by_safety", False) and has_email:
        selectable = False
    if features.get("identity_not_resolved"):
        selectable = False

    return ContactCandidate(
        candidate_id=candidate_id_for(
            contact_id=contact_id,
            account_id=account_id,
            ranking_tier=tier,
        ),
        contact_resolution_id=contact_resolution_id,
        coalesced_tender_id=tender_id,
        account_id=account_id,
        contact_id=contact_id,
        rank=0,
        ranking_tier=tier,
        role_raw_digest=_role_digest(role if isinstance(role, str) else None),
        role_suitability=suitability,
        identity_status=identity_status,
        identity_confidence=identity_confidence,
        has_usable_email=has_email,
        verification_status=(
            "explicit_verification" if verified else "unverified"
        ),
        evidence_ids=evidence_ids,
        suppression_result=str(safety_result["suppression_result"]),
        outreach_state_result=str(safety_result["outreach_state_result"]),
        safety_blocked=bool(safety_result["safety_blocked"]),
        safety_unknown=bool(safety_result.get("safety_unknown")),
        selectable=selectable,
        ranking_reason_codes=reasons,
    )


def _rank_candidates(
    candidates: list[ContactCandidate],
    *,
    policy: Mapping[str, Any],
) -> list[ContactCandidate]:
    tier_rank = {row["id"]: int(row["rank"]) for row in policy["ranking_tiers"]}
    # Sort by material tier only; contact_id is a stable final tie-breaker AFTER
    # material ambiguity is evaluated separately on equal top tiers.
    candidates.sort(
        key=lambda c: (
            tier_rank.get(c.ranking_tier, 99),
            0 if c.selectable else 1,
            c.contact_id,
        )
    )
    ranked: list[ContactCandidate] = []
    for i, c in enumerate(candidates):
        ranked.append(
            ContactCandidate(
                candidate_id=c.candidate_id,
                contact_resolution_id=c.contact_resolution_id,
                coalesced_tender_id=c.coalesced_tender_id,
                account_id=c.account_id,
                contact_id=c.contact_id,
                rank=i + 1,
                ranking_tier=c.ranking_tier,
                role_raw_digest=c.role_raw_digest,
                role_suitability=c.role_suitability,
                identity_status=c.identity_status,
                identity_confidence=c.identity_confidence,
                has_usable_email=c.has_usable_email,
                verification_status=c.verification_status,
                evidence_ids=c.evidence_ids,
                suppression_result=c.suppression_result,
                outreach_state_result=c.outreach_state_result,
                safety_blocked=c.safety_blocked,
                safety_unknown=c.safety_unknown,
                selectable=c.selectable,
                ranking_reason_codes=c.ranking_reason_codes,
            )
        )
    return ranked


def project_candidates_from_frozen(
    *,
    tender_id: str,
    account_id: str,
    contact_resolution_id: str,
    frozen_index: FrozenSourceIndex,
    safety: SafetySnapshot,
    buyer_email_norm: str | None,
    institution_name: str | None,
    policy: Mapping[str, Any] | None = None,
) -> tuple[list[ContactCandidate], list[ContactResolutionEvidence]]:
    """Project every frozen contact for account into candidates + evidence.

    Candidate set MUST equal frozen_index.contact_ids_for_account(account_id).
    Evidence IDs on each candidate MUST equal the frozen contact's evidence_ids exactly.
    Verification recomputed from that exact declared-and-frozen evidence set.
    Safety via evaluate_contact_safety(frozen.email_norm, institution_name, safety).
    """
    policy_spec = policy if policy is not None else contact_resolution_policy_spec()
    evidence_out: list[ContactResolutionEvidence] = []
    candidates: list[ContactCandidate] = []
    for contact_id in frozen_index.contact_ids_for_account(account_id):
        proj = frozen_index.contacts_by_id.get(contact_id)
        if proj is None:
            continue
        sorted_eids = tuple(sorted(proj.evidence_ids))
        frozen_evs = []
        for eid in sorted_eids:
            fe = frozen_index.evidence_by_id.get(eid)
            if fe is not None:
                frozen_evs.append(fe)
        ev_dicts = [fe.to_dict() for fe in frozen_evs]
        evidence_out.extend(_evidence_from_rows(contact_id, ev_dicts))
        email = proj.email_norm if proj.has_usable_email else None
        role = proj.role_raw
        candidates.append(
            _build_candidate(
                contact_resolution_id=contact_resolution_id,
                tender_id=tender_id,
                account_id=account_id,
                contact_id=contact_id,
                email=email,
                role=role if isinstance(role, str) else None,
                identity_status=proj.identity_status,
                identity_confidence=proj.identity_confidence,
                ev_rows=ev_dicts,
                evidence_ids=sorted_eids,
                buyer_email_norm=buyer_email_norm,
                institution_name=institution_name,
                safety=safety,
                policy=policy_spec,
            )
        )
    return candidates, evidence_out


def project_linked_contact_resolution(
    *,
    tender_id: str,
    relevance: TenderRelevanceDecision,
    organization: OrganizationResolution,
    frozen_index: FrozenSourceIndex,
    safety: SafetySnapshot,
    buyer_email_norm: str | None,
    institution_name: str | None,
    input_fingerprint: str,
    currentness_class: str,
    label_status: str | None = None,
    independently_reviewed: bool = False,
) -> tuple[
    ContactResolutionSummary,
    list[ContactCandidate],
    list[ContactResolutionEvidence],
    list[ContactResolutionConflict],
]:
    """Frozen-only linked-account path (construction + reconciliation)."""
    policy = contact_resolution_policy_spec()
    account_id = organization.account_id
    assert account_id is not None

    stages = ["pr2_account_contacts"]
    if buyer_email_norm:
        stages.append("buyer_email_exact_match")
    stages.append("safety_gate")

    # Placeholder parent; rewritten after final status selection.
    candidates, evidence_out = project_candidates_from_frozen(
        tender_id=tender_id,
        account_id=account_id,
        contact_resolution_id="",
        frozen_index=frozen_index,
        safety=safety,
        buyer_email_norm=buyer_email_norm,
        institution_name=institution_name,
        policy=policy,
    )

    ranked = _rank_candidates(candidates, policy=policy)
    status, selected, reason, conflicts = _select_final_status(
        ranked=ranked, policy=policy, tender_id=tender_id
    )
    # Do NOT rewrite contact_research_required based on actionability —
    # next_action_for_status gates gated research to ``none``.

    suitable = [
        c
        for c in ranked
        if is_suitable_role(c.role_suitability) and c.has_usable_email
    ]
    blocked = [c for c in ranked if c.safety_blocked or c.safety_unknown]

    final_id = contact_resolution_id_linked(
        coalesced_tender_id=tender_id,
        organization_resolution_id=organization.organization_resolution_id,
        account_id=account_id,
        status=status,
        selected_contact_id=selected.contact_id if selected else None,
        reason=reason,
    )
    rewritten: list[ContactCandidate] = []
    for c in ranked:
        rewritten.append(
            ContactCandidate(
                candidate_id=c.candidate_id,
                contact_resolution_id=final_id,
                coalesced_tender_id=c.coalesced_tender_id,
                account_id=c.account_id,
                contact_id=c.contact_id,
                rank=c.rank,
                ranking_tier=c.ranking_tier,
                role_raw_digest=c.role_raw_digest,
                role_suitability=c.role_suitability,
                identity_status=c.identity_status,
                identity_confidence=c.identity_confidence,
                has_usable_email=c.has_usable_email,
                verification_status=c.verification_status,
                evidence_ids=c.evidence_ids,
                suppression_result=c.suppression_result,
                outreach_state_result=c.outreach_state_result,
                safety_blocked=c.safety_blocked,
                safety_unknown=c.safety_unknown,
                selectable=c.selectable,
                ranking_reason_codes=c.ranking_reason_codes,
            )
        )

    next_action = next_action_for_status(
        status,
        lifecycle_class=relevance.lifecycle_class_echo or "",
        relevance_class=relevance.relevance_class,
        currentness_class=currentness_class,
        label_status=label_status,
        independently_reviewed=independently_reviewed,
        policy=policy,
    )
    sem = recompute_summary_semantic_fingerprint(
        contact_resolution_id=final_id,
        final_contact_status=status,
        account_id=account_id,
        selected_contact_id=selected.contact_id if selected else None,
        selected_candidate_id=selected.candidate_id if selected else None,
        search_stages_completed=stages,
        next_action=next_action,
        reason_code=reason,
        candidate_ids=[c.candidate_id for c in rewritten],
    )
    summary = ContactResolutionSummary(
        contact_resolution_id=final_id,
        coalesced_tender_id=tender_id,
        relevance_decision_id=relevance.decision_id,
        organization_resolution_id=organization.organization_resolution_id,
        account_id=account_id,
        final_contact_status=status,
        selected_contact_id=selected.contact_id if selected else None,
        selected_candidate_id=selected.candidate_id if selected else None,
        search_stages_completed=tuple(stages),
        next_action=next_action,
        reason_code=reason,
        considered_contact_count=len(rewritten),
        suitable_contact_count=len(suitable),
        blocked_contact_count=len(blocked),
        relevance_class_echo=relevance.relevance_class,
        lifecycle_class_echo=relevance.lifecycle_class_echo or "",
        currentness_class_echo=currentness_class,
        relevance_validation_status_echo=_validation_status_echo(label_status),
        input_fingerprint=input_fingerprint,
        semantic_fingerprint=sem,
        rules_version=CONTACT_RESOLUTION_RULES_VERSION,
        resolver_version=CONTACT_RESOLVER_VERSION,
    )
    return summary, rewritten, evidence_out, conflicts


def resolve_contacts_for_tender(
    *,
    tender_id: str,
    relevance: TenderRelevanceDecision,
    organization: OrganizationResolution,
    conn: sqlite3.Connection,
    safety: SafetySnapshot,
    buyer_email_norm: str | None,
    institution_name: str | None,
    input_fingerprint: str,
    currentness_class: str,
    label_status: str | None = None,
    independently_reviewed: bool = False,
    frozen_index: FrozenSourceIndex | None = None,
) -> tuple[
    ContactResolutionSummary,
    list[ContactCandidate],
    list[ContactResolutionEvidence],
    list[ContactResolutionConflict],
]:
    policy = contact_resolution_policy_spec()
    if organization.resolution_status != "linked" or not organization.account_id:
        reason = (
            "account_unresolved"
            if organization.resolution_status
            != "deferred_insufficient_buyer_fields"
            else "insufficient_buyer_fields"
        )
        return (
            deferred_summary(
                tender_id=tender_id,
                relevance=relevance,
                organization=organization,
                input_fingerprint=input_fingerprint,
                reason_code=reason,
                currentness_class=currentness_class,
                label_status=label_status,
                independently_reviewed=independently_reviewed,
            ),
            [],
            [],
            [],
        )

    if frozen_index is not None:
        return project_linked_contact_resolution(
            tender_id=tender_id,
            relevance=relevance,
            organization=organization,
            frozen_index=frozen_index,
            safety=safety,
            buyer_email_norm=buyer_email_norm,
            institution_name=institution_name,
            input_fingerprint=input_fingerprint,
            currentness_class=currentness_class,
            label_status=label_status,
            independently_reviewed=independently_reviewed,
        )

    account_id = organization.account_id
    stages = ["pr2_account_contacts"]
    if buyer_email_norm:
        stages.append("buyer_email_exact_match")
    stages.append("safety_gate")

    # Placeholder parent; rewritten after final status selection.
    contact_resolution_id = ""

    evidence_out: list[ContactResolutionEvidence] = []
    candidates: list[ContactCandidate] = []

    contacts = load_contacts_for_account(conn, account_id)
    evidence_by_contact = load_contact_evidence(
        conn, [str(c["contact_id"]) for c in contacts]
    )
    for row in contacts:
        contact_id = str(row["contact_id"])
        email = parse_usable_email(row.get("normalized_email"))
        role = row.get("role")
        ev_rows = evidence_by_contact.get(contact_id, [])
        evidence_out.extend(_evidence_from_rows(contact_id, ev_rows))
        candidates.append(
            _build_candidate(
                contact_resolution_id=contact_resolution_id,
                tender_id=tender_id,
                account_id=account_id,
                contact_id=contact_id,
                email=email,
                role=role if isinstance(role, str) else None,
                identity_status=str(row.get("identity_status") or ""),
                identity_confidence=str(row.get("identity_confidence") or ""),
                ev_rows=ev_rows,
                evidence_ids=tuple(
                    sorted(str(e["evidence_id"]) for e in ev_rows)
                ),
                buyer_email_norm=buyer_email_norm,
                institution_name=institution_name,
                safety=safety,
                policy=policy,
            )
        )

    ranked = _rank_candidates(candidates, policy=policy)
    status, selected, reason, conflicts = _select_final_status(
        ranked=ranked, policy=policy, tender_id=tender_id
    )
    # Do NOT rewrite contact_research_required based on actionability —
    # next_action_for_status gates gated research to ``none``.

    suitable = [
        c
        for c in ranked
        if is_suitable_role(c.role_suitability) and c.has_usable_email
    ]
    blocked = [c for c in ranked if c.safety_blocked or c.safety_unknown]

    final_id = contact_resolution_id_linked(
        coalesced_tender_id=tender_id,
        organization_resolution_id=organization.organization_resolution_id,
        account_id=account_id,
        status=status,
        selected_contact_id=selected.contact_id if selected else None,
        reason=reason,
    )
    rewritten: list[ContactCandidate] = []
    for c in ranked:
        rewritten.append(
            ContactCandidate(
                candidate_id=c.candidate_id,
                contact_resolution_id=final_id,
                coalesced_tender_id=c.coalesced_tender_id,
                account_id=c.account_id,
                contact_id=c.contact_id,
                rank=c.rank,
                ranking_tier=c.ranking_tier,
                role_raw_digest=c.role_raw_digest,
                role_suitability=c.role_suitability,
                identity_status=c.identity_status,
                identity_confidence=c.identity_confidence,
                has_usable_email=c.has_usable_email,
                verification_status=c.verification_status,
                evidence_ids=c.evidence_ids,
                suppression_result=c.suppression_result,
                outreach_state_result=c.outreach_state_result,
                safety_blocked=c.safety_blocked,
                safety_unknown=c.safety_unknown,
                selectable=c.selectable,
                ranking_reason_codes=c.ranking_reason_codes,
            )
        )

    next_action = next_action_for_status(
        status,
        lifecycle_class=relevance.lifecycle_class_echo or "",
        relevance_class=relevance.relevance_class,
        currentness_class=currentness_class,
        label_status=label_status,
        independently_reviewed=independently_reviewed,
        policy=policy,
    )
    sem = recompute_summary_semantic_fingerprint(
        contact_resolution_id=final_id,
        final_contact_status=status,
        account_id=account_id,
        selected_contact_id=selected.contact_id if selected else None,
        selected_candidate_id=selected.candidate_id if selected else None,
        search_stages_completed=stages,
        next_action=next_action,
        reason_code=reason,
        candidate_ids=[c.candidate_id for c in rewritten],
    )
    summary = ContactResolutionSummary(
        contact_resolution_id=final_id,
        coalesced_tender_id=tender_id,
        relevance_decision_id=relevance.decision_id,
        organization_resolution_id=organization.organization_resolution_id,
        account_id=account_id,
        final_contact_status=status,
        selected_contact_id=selected.contact_id if selected else None,
        selected_candidate_id=selected.candidate_id if selected else None,
        search_stages_completed=tuple(stages),
        next_action=next_action,
        reason_code=reason,
        considered_contact_count=len(rewritten),
        suitable_contact_count=len(suitable),
        blocked_contact_count=len(blocked),
        relevance_class_echo=relevance.relevance_class,
        lifecycle_class_echo=relevance.lifecycle_class_echo or "",
        currentness_class_echo=currentness_class,
        relevance_validation_status_echo=_validation_status_echo(label_status),
        input_fingerprint=input_fingerprint,
        semantic_fingerprint=sem,
        rules_version=CONTACT_RESOLUTION_RULES_VERSION,
        resolver_version=CONTACT_RESOLVER_VERSION,
    )
    return summary, rewritten, evidence_out, conflicts
