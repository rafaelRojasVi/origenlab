"""Internal contact search, ranking, and status selection for PR5E."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any, Mapping

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
    feature_flags_for_candidate,
    is_suitable_role,
    next_action_for_status,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.safety import (
    SafetySnapshot,
    evaluate_contact_safety,
    parse_usable_email,
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


def _stable_id(prefix: str, payload: Mapping[str, Any]) -> str:
    digest = canonical_json_digest(dict(payload))
    return f"{prefix}_{digest[:32]}"


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
) -> ContactResolutionSummary:
    policy = contact_resolution_policy_spec()
    status = CONTACT_RESOLUTION_DEFERRED
    payload = {
        "coalesced_tender_id": tender_id,
        "status": status,
        "organization_resolution_id": organization.organization_resolution_id,
        "reason": reason_code,
    }
    rid = _stable_id("crs", payload)
    next_action = next_action_for_status(
        status,
        lifecycle_class=relevance.lifecycle_class_echo or "",
        relevance_class=relevance.relevance_class,
        policy=policy,
    )
    sem = canonical_json_digest(
        {
            "contact_resolution_id": rid,
            "final_contact_status": status,
            "account_id": None,
            "selected_contact_id": None,
            "search_stages_completed": [],
            "next_action": next_action,
            "reason_code": reason_code,
        }
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
    """Policy-driven final status. Material ties become ambiguous before ID order."""
    conflicts: list[ContactResolutionConflict] = []
    material_tiers = set(policy["material_ambiguity_tiers"])
    selectable = [c for c in ranked if c.selectable]
    blocked = [c for c in ranked if c.safety_blocked or c.safety_unknown]

    if not ranked:
        return "no_contact_found", None, "internal_search_exhausted_empty", conflicts

    if selectable:
        top = selectable[0]
        peers = [
            c
            for c in selectable
            if c.ranking_tier == top.ranking_tier and c.ranking_tier in material_tiers
        ]
        if len(peers) > 1:
            # Material equivalence → ambiguous; contact_id must not silently resolve.
            conflicts.append(
                ContactResolutionConflict(
                    conflict_id=_stable_id(
                        "cconf",
                        {
                            "tender": tender_id,
                            "contacts": sorted(p.contact_id for p in peers),
                            "tier": top.ranking_tier,
                        },
                    ),
                    coalesced_tender_id=tender_id,
                    conflict_type="ambiguous_contact",
                    reason_code="multiple_competing_contacts",
                    subject_keys=tuple(sorted(p.contact_id for p in peers)),
                    evidence_ids=(),
                )
            )
            return (
                "ambiguous_contact",
                None,
                "multiple_competing_contacts",
                conflicts,
            )

        verified_tiers = set(policy["status_selection"]["verified_requires_tier"])
        exact_cfg = policy["status_selection"]["exact_buyer_verified_path"]
        if top.ranking_tier in verified_tiers:
            return (
                "existing_verified_contact",
                top,
                "verified_suitable_contact_selected",
                conflicts,
            )
        if top.ranking_tier == exact_cfg["tier"]:
            if (
                is_suitable_role(top.role_suitability)
                and top.verification_status == "explicit_verification"
            ):
                return (
                    "existing_verified_contact",
                    top,
                    "verified_suitable_contact_selected",
                    conflicts,
                )
            return (
                "existing_contact_needs_role_review",
                top,
                "contact_present_verification_or_role_incomplete",
                conflicts,
            )
        if top.ranking_tier in set(policy["status_selection"]["role_review_tiers"]):
            reason = (
                "suitable_role_without_explicit_verification"
                if top.ranking_tier == "suitable_role_unverified"
                else "contact_requires_role_review"
            )
            return (
                "existing_contact_needs_role_review",
                top,
                reason,
                conflicts,
            )
        return (
            "existing_contact_needs_role_review",
            top,
            "contact_requires_role_review",
            conflicts,
        )

    if any(c.ranking_tier == "role_known_email_missing" for c in ranked):
        return (
            "role_known_email_missing",
            None,
            "suitable_role_email_missing",
            conflicts,
        )
    if blocked and not selectable:
        return (
            "contact_blocked",
            None,
            "all_selectable_paths_blocked",
            conflicts,
        )
    # Exhausted internal search with non-selectable non-blocked paths.
    return (
        "contact_research_required",
        None,
        "internal_search_exhausted_no_selectable",
        conflicts,
    )


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
            ),
            [],
            [],
            [],
        )

    account_id = organization.account_id
    contacts = load_contacts_for_account(conn, account_id)
    stages = ["pr2_account_contacts"]
    if buyer_email_norm:
        stages.append("buyer_email_exact_match")
    stages.append("safety_gate")

    evidence_by_contact = load_contact_evidence(
        conn, [str(c["contact_id"]) for c in contacts]
    )
    evidence_out: list[ContactResolutionEvidence] = []
    candidates: list[ContactCandidate] = []

    provisional_key = {
        "coalesced_tender_id": tender_id,
        "account_id": account_id,
        "organization_resolution_id": organization.organization_resolution_id,
    }
    contact_resolution_id = _stable_id("crs", provisional_key)

    for row in contacts:
        contact_id = str(row["contact_id"])
        # Canonical parse only — non-empty invalid strings are not usable.
        email = parse_usable_email(row.get("normalized_email"))
        has_email = bool(email)
        role = row.get("role")
        suitability = classify_role_suitability(role if isinstance(role, str) else None)
        ev_rows = evidence_by_contact.get(contact_id, [])
        for ev in ev_rows:
            evidence_out.append(
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
        verified = contact_has_explicit_verification(
            identity_status=str(row.get("identity_status") or ""),
            identity_confidence=str(row.get("identity_confidence") or ""),
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
            account_membership=str(row.get("account_id") or "") == account_id,
            identity_status=str(row.get("identity_status") or ""),
            role_suitability=suitability,
            verified=verified,
            buyer_email_exact=buyer_exact,
            safety_blocked=bool(safety_result["safety_blocked"])
            and not bool(safety_result.get("safety_unknown")),
            safety_unknown=bool(safety_result.get("safety_unknown")),
        )
        # Email-missing contacts: safety_blocked false but not selectable by safety.
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

        cand_payload = {
            "contact_resolution_id": contact_resolution_id,
            "contact_id": contact_id,
            "account_id": account_id,
            "tier": tier,
        }
        candidates.append(
            ContactCandidate(
                candidate_id=_stable_id("ccand", cand_payload),
                contact_resolution_id=contact_resolution_id,
                coalesced_tender_id=tender_id,
                account_id=account_id,
                contact_id=contact_id,
                rank=0,
                ranking_tier=tier,
                role_raw_digest=_role_digest(role if isinstance(role, str) else None),
                role_suitability=suitability,
                identity_status=str(row.get("identity_status") or ""),
                identity_confidence=str(row.get("identity_confidence") or ""),
                has_usable_email=has_email,
                verification_status=(
                    "explicit_verification" if verified else "unverified"
                ),
                evidence_ids=tuple(sorted(str(e["evidence_id"]) for e in ev_rows)),
                suppression_result=str(safety_result["suppression_result"]),
                outreach_state_result=str(safety_result["outreach_state_result"]),
                safety_blocked=bool(safety_result["safety_blocked"]),
                safety_unknown=bool(safety_result.get("safety_unknown")),
                selectable=selectable,
                ranking_reason_codes=reasons,
            )
        )

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

    status, selected, reason, conflicts = _select_final_status(
        ranked=ranked, policy=policy, tender_id=tender_id
    )

    # contact_research_required only when search exhausted AND tender actionable.
    if status == "contact_research_required":
        from origenlab_email_pipeline.commercial_procurement_contact_resolution.policy import (
            tender_allows_actionable_research,
        )

        if not tender_allows_actionable_research(
            lifecycle_class=relevance.lifecycle_class_echo or "",
            relevance_class=relevance.relevance_class,
            policy=policy,
        ):
            # Keep analysis status but downgrade research claim to no_contact_found
            # semantics for non-actionable tenders with exhausted non-selectable paths.
            # Prefer explicit non-research status when no selectable contacts remain.
            if any(c.safety_blocked or c.safety_unknown for c in ranked):
                status = "contact_blocked"
                reason = "all_selectable_paths_blocked_non_actionable_tender"
            else:
                status = "no_contact_found"
                reason = "internal_search_exhausted_non_actionable_tender"

    suitable = [
        c
        for c in ranked
        if is_suitable_role(c.role_suitability) and c.has_usable_email
    ]
    blocked = [c for c in ranked if c.safety_blocked or c.safety_unknown]

    final_payload = {
        "coalesced_tender_id": tender_id,
        "status": status,
        "organization_resolution_id": organization.organization_resolution_id,
        "account_id": account_id,
        "selected_contact_id": selected.contact_id if selected else None,
        "reason": reason,
    }
    final_id = _stable_id("crs", final_payload)
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
        policy=policy,
    )
    sem = canonical_json_digest(
        {
            "contact_resolution_id": final_id,
            "final_contact_status": status,
            "account_id": account_id,
            "selected_contact_id": selected.contact_id if selected else None,
            "selected_candidate_id": selected.candidate_id if selected else None,
            "search_stages_completed": stages,
            "next_action": next_action,
            "reason_code": reason,
            "candidate_ids": [c.candidate_id for c in rewritten],
        }
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
        input_fingerprint=input_fingerprint,
        semantic_fingerprint=sem,
        rules_version=CONTACT_RESOLUTION_RULES_VERSION,
        resolver_version=CONTACT_RESOLVER_VERSION,
    )
    return summary, rewritten, evidence_out, conflicts
