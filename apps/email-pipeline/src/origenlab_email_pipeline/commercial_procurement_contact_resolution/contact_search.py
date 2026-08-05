"""Internal contact search, ranking, and status selection for PR5E."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any, Mapping

from origenlab_email_pipeline.commercial_identity.normalize import (
    normalize_identity_email,
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
    classify_role_suitability,
    contact_resolution_policy_spec,
    is_suitable_role,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.safety import (
    SafetySnapshot,
    evaluate_contact_safety,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
    TenderRelevanceDecision,
)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
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
    if not contact_ids or not _table_exists(conn, "commercial_identity_evidence"):
        return {}
    out: dict[str, list[dict[str, Any]]] = {cid: [] for cid in contact_ids}
    placeholders = ",".join("?" for _ in contact_ids)
    rows = conn.execute(
        f"""
        SELECT evidence_id, subject_kind, subject_id, source_table, source_record_id,
               evidence_type, matching_reason_code, confidence
        FROM commercial_identity_evidence
        WHERE subject_kind = 'contact' AND subject_id IN ({placeholders})
        ORDER BY evidence_id
        """,
        tuple(contact_ids),
    )
    for r in rows:
        out.setdefault(str(r["subject_id"]), []).append(dict(r))
    return out


def _has_explicit_verification(
    *,
    identity_status: str,
    identity_confidence: str,
    evidence_rows: list[dict[str, Any]],
) -> bool:
    """Conservative verification: high-confidence resolved identity evidence only."""
    if identity_status != "resolved":
        return False
    if (identity_confidence or "").strip().lower() not in {"high", "high_confidence"}:
        return False
    for ev in evidence_rows:
        conf = str(ev.get("confidence") or "").strip().lower()
        if conf in {"high", "high_confidence"}:
            return True
    return False


def _assign_tier(
    *,
    role_suitability: str,
    has_email: bool,
    verified: bool,
    safety_blocked: bool,
    buyer_email_exact: bool,
) -> tuple[str, tuple[str, ...], bool]:
    reasons: list[str] = []
    if safety_blocked:
        return "blocked", ("safety_blocked",), False
    if buyer_email_exact and has_email:
        reasons.append("exact_buyer_email_match")
        selectable = True
        return "exact_buyer_email", tuple(reasons), selectable
    if not has_email and is_suitable_role(role_suitability):
        return "role_known_email_missing", ("suitable_role", "email_missing"), False
    if has_email and is_suitable_role(role_suitability) and verified:
        return (
            "verified_suitable",
            ("suitable_role", "explicit_verification", "usable_email"),
            True,
        )
    if has_email and is_suitable_role(role_suitability):
        return (
            "suitable_role_unverified",
            ("suitable_role", "usable_email", "verification_absent"),
            True,
        )
    if has_email:
        return "role_review", ("usable_email", "role_unknown_or_unsuitable"), True
    return "role_review", ("email_missing", "role_unknown"), False


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
    sem = canonical_json_digest(
        {
            "contact_resolution_id": rid,
            "final_contact_status": status,
            "account_id": None,
            "selected_contact_id": None,
            "search_stages_completed": [],
            "next_action": policy["next_action_by_status"][status],
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
        next_action=str(policy["next_action_by_status"][status]),
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

    # Provisional resolution id seed (finalized after status known).
    provisional_key = {
        "coalesced_tender_id": tender_id,
        "account_id": account_id,
        "organization_resolution_id": organization.organization_resolution_id,
    }
    contact_resolution_id = _stable_id("crs", provisional_key)

    for row in contacts:
        contact_id = str(row["contact_id"])
        email = normalize_identity_email(row.get("normalized_email") or "") or None
        if email is None and row.get("normalized_email"):
            email = str(row["normalized_email"]).strip().lower() or None
        has_email = bool(email)
        role = row.get("role")
        suitability = classify_role_suitability(role if isinstance(role, str) else None)
        ev_rows = evidence_by_contact.get(contact_id, [])
        for ev in ev_rows:
            evidence_out.append(
                ContactResolutionEvidence(
                    evidence_id=str(ev["evidence_id"]),
                    subject_kind="contact",
                    subject_id=contact_id,
                    source_table=str(ev.get("source_table") or "commercial_identity_evidence"),
                    source_record_id=str(ev.get("source_record_id") or ev["evidence_id"]),
                    matching_reason_code=str(ev.get("matching_reason_code") or ""),
                    confidence=str(ev.get("confidence") or ""),
                )
            )
        verified = _has_explicit_verification(
            identity_status=str(row.get("identity_status") or ""),
            identity_confidence=str(row.get("identity_confidence") or ""),
            evidence_rows=ev_rows,
        )
        safety_result = evaluate_contact_safety(
            email_norm=email,
            institution_name=institution_name,
            safety=safety,
        )
        buyer_exact = bool(
            buyer_email_norm and email and email == buyer_email_norm
        )
        tier, reasons, selectable = _assign_tier(
            role_suitability=suitability,
            has_email=has_email,
            verified=verified,
            safety_blocked=bool(safety_result["safety_blocked"]),
            buyer_email_exact=buyer_exact,
        )
        if safety_result["safety_blocked"]:
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
                rank=0,  # filled after sort
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
                selectable=selectable and not bool(safety_result["safety_blocked"]),
                ranking_reason_codes=reasons,
            )
        )

    tier_rank = {
        row["id"]: int(row["rank"]) for row in policy["ranking_tiers"]
    }
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
                selectable=c.selectable,
                ranking_reason_codes=c.ranking_reason_codes,
            )
        )

    conflicts: list[ContactResolutionConflict] = []
    selectable = [c for c in ranked if c.selectable]
    blocked = [c for c in ranked if c.safety_blocked]
    suitable = [
        c
        for c in ranked
        if is_suitable_role(c.role_suitability) and c.has_usable_email
    ]

    status: str
    selected: ContactCandidate | None = None
    reason: str
    if not ranked:
        status = "no_contact_found"
        reason = "internal_search_exhausted_empty"
    elif selectable:
        top = selectable[0]
        peers = [
            c
            for c in selectable
            if c.ranking_tier == top.ranking_tier
            and tier_rank.get(c.ranking_tier, 99) <= 2
        ]
        if len(peers) > 1 and top.ranking_tier in {
            "exact_buyer_email",
            "verified_suitable",
            "suitable_role_unverified",
        }:
            status = "ambiguous_contact"
            reason = "multiple_competing_contacts"
            conflicts.append(
                ContactResolutionConflict(
                    conflict_id=_stable_id(
                        "cconf",
                        {
                            "tender": tender_id,
                            "contacts": sorted(p.contact_id for p in peers),
                        },
                    ),
                    coalesced_tender_id=tender_id,
                    conflict_type="ambiguous_contact",
                    reason_code="multiple_competing_contacts",
                    subject_keys=tuple(sorted(p.contact_id for p in peers)),
                    evidence_ids=(),
                )
            )
        elif top.ranking_tier == "verified_suitable" or top.ranking_tier == "exact_buyer_email":
            # Exact buyer email still needs verification+suitable for verified status.
            if (
                top.ranking_tier == "exact_buyer_email"
                and is_suitable_role(top.role_suitability)
                and top.verification_status == "explicit_verification"
            ):
                status = "existing_verified_contact"
                reason = "verified_suitable_contact_selected"
                selected = top
            elif top.ranking_tier == "verified_suitable":
                status = "existing_verified_contact"
                reason = "verified_suitable_contact_selected"
                selected = top
            else:
                status = "existing_contact_needs_role_review"
                reason = "contact_present_verification_or_role_incomplete"
                selected = top
        elif top.ranking_tier == "suitable_role_unverified":
            status = "existing_contact_needs_role_review"
            reason = "suitable_role_without_explicit_verification"
            selected = top
        else:
            status = "existing_contact_needs_role_review"
            reason = "contact_requires_role_review"
            selected = top
    elif any(
        c.ranking_tier == "role_known_email_missing" for c in ranked
    ) and not selectable:
        status = "role_known_email_missing"
        reason = "suitable_role_email_missing"
    elif blocked and not selectable:
        status = "contact_blocked"
        reason = "all_selectable_paths_blocked"
    else:
        status = "contact_research_required"
        reason = "internal_search_exhausted_no_selectable"

    # Actionability note: research_required only when search exhausted with no selectable.
    if status == "no_contact_found":
        # Keep no_contact_found (searched, empty). contact_research_required is for
        # non-empty but non-selectable without total block — already handled above.
        pass

    final_payload = {
        "coalesced_tender_id": tender_id,
        "status": status,
        "organization_resolution_id": organization.organization_resolution_id,
        "account_id": account_id,
        "selected_contact_id": selected.contact_id if selected else None,
        "reason": reason,
    }
    final_id = _stable_id("crs", final_payload)
    # Rewrite candidate contact_resolution_id to final summary id.
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
                selectable=c.selectable,
                ranking_reason_codes=c.ranking_reason_codes,
            )
        )

    next_action = str(policy["next_action_by_status"][status])
    sem = canonical_json_digest(
        {
            "contact_resolution_id": final_id,
            "final_contact_status": status,
            "account_id": account_id,
            "selected_contact_id": selected.contact_id if selected else None,
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
