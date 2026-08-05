"""PR5E contact-resolution planner — compose PR5C+PR5D+PR2, read-only."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from origenlab_email_pipeline.commercial_procurement.builder import (
    assert_no_write_connection,
    connect_production_readonly,
)
from origenlab_email_pipeline.commercial_procurement.sources import (
    disable_require_active_read_transaction,
    enable_require_active_read_transaction,
    load_identity_fingerprint_meta,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    CandidatePlanResult,
    CoalescedProcurementTender,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.output_safety import (
    write_atomically,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.planner import (
    build_candidate_plan,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.constants import (
    CONTACT_RESOLUTION_DEFERRED,
    CONTACT_RESOLUTION_PLANNER_VERSION,
    FORBIDDEN_CLI_FLAGS,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.contact_search import (
    recompute_summary_semantic_fingerprint,
    resolve_contacts_for_tender,
    select_final_status,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.fingerprint import (
    all_fingerprints,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.frozen_sources import (
    FrozenSourceIndex,
    load_frozen_source_index,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.models import (
    ContactCandidate,
    ContactResolutionConflict,
    ContactResolutionEvidence,
    ContactResolutionPlanResult,
    ContactResolutionSummary,
    OrganizationResolution,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.organization import (
    Pr4ProvenanceError,
    load_pr4_resolutions_by_procurement,
    open_account_index,
    resolve_organization_for_tender,
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
from origenlab_email_pipeline.commercial_procurement_contact_resolution.redaction import (
    assert_no_raw_pii,
    redact_summary_for_share,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.safety import (
    SafetySnapshot,
    load_safety_snapshot,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
    ProductRelevancePlanResult,
    TenderRelevanceDecision,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.planner import (
    build_product_relevance_plan,
)

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

_EVIDENCE_COMPARE_FIELDS = (
    "evidence_type",
    "source_table",
    "source_plane",
    "source_record_id",
    "origin_plane",
    "evidence_at",
    "matching_reason_code",
    "confidence",
    "subject_kind",
    "subject_id",
)


class ContactReconciliationError(ValueError):
    """Raised when PR5E reconciliation equations fail."""


class ContactDependencyError(ValueError):
    """Raised when required dependency fingerprints/digests are missing."""


def _require_digest(name: str, value: str | None) -> str:
    raw = (value or "").strip()
    if not raw or raw.startswith("missing_"):
        raise ContactDependencyError(f"missing or placeholder digest for {name}")
    if not _HEX64_RE.match(raw):
        raise ContactDependencyError(
            f"malformed digest for {name}: expected 64 lowercase hex chars"
        )
    return raw


def empty_frozen_source_index() -> FrozenSourceIndex:
    """Empty frozen index for unit tests that exercise deferred / binding paths."""
    return FrozenSourceIndex(
        contacts_by_id={},
        evidence_by_id={},
        contacts_by_account={},
        pr4_by_procurement={},
        known_account_ids=frozenset(),
        source_fingerprint="",
    )


def _candidate_sort_key(
    c: ContactCandidate, *, tier_rank: Mapping[str, int]
) -> tuple[int, int, str]:
    return (
        tier_rank.get(c.ranking_tier, 99),
        0 if c.selectable else 1,
        c.contact_id,
    )


def _expected_linked_search_stages() -> tuple[str, ...]:
    # Planner never passes buyer_email_norm today.
    return ("pr2_account_contacts", "safety_gate")


def _recompute_tier_from_frozen(
    c: ContactCandidate,
    *,
    frozen_index: FrozenSourceIndex,
    policy: Mapping[str, Any],
) -> str | None:
    proj = frozen_index.contacts_by_id.get(c.contact_id)
    if proj is None:
        return None
    ev_rows = []
    for eid in proj.evidence_ids:
        fe = frozen_index.evidence_by_id.get(eid)
        if fe is not None:
            ev_rows.append(fe.to_dict())
    verified = contact_has_explicit_verification(
        identity_status=proj.identity_status,
        identity_confidence=proj.identity_confidence,
        evidence_rows=ev_rows,
        contact_id=c.contact_id,
        policy=policy,
    )
    suitability = classify_role_suitability(
        proj.role_raw if isinstance(proj.role_raw, str) else None
    )
    features = feature_flags_for_candidate(
        has_usable_email=proj.has_usable_email,
        account_membership=True,
        identity_status=proj.identity_status,
        role_suitability=suitability,
        verified=verified,
        buyer_email_exact=False,
        safety_blocked=bool(c.safety_blocked) and not bool(c.safety_unknown),
        safety_unknown=bool(c.safety_unknown),
    )
    if not proj.has_usable_email:
        features = dict(features)
        features["not_safety_blocked"] = True
        features["safety_known"] = True
        features["safety_blocked"] = False
        features["safety_blocked_or_unknown"] = False
    tier, _, _ = assign_ranking_tier(features, policy=policy)
    return tier


def reconcile_contact_resolution(
    *,
    decisions: Sequence[TenderRelevanceDecision],
    tenders_by_id: Mapping[str, CoalescedProcurementTender],
    organizations: Sequence[OrganizationResolution],
    summaries: Sequence[ContactResolutionSummary],
    candidates: Sequence[ContactCandidate],
    evidence: Sequence[ContactResolutionEvidence],
    conflicts: Sequence[ContactResolutionConflict],
    frozen_index: FrozenSourceIndex,
    safety: SafetySnapshot | None = None,
) -> dict[str, Any]:
    """Complete binding reconciliation against independently frozen PR2/PR4 sources."""
    del safety  # reserved; candidate safety flags checked via stored values
    policy = contact_resolution_policy_spec()
    tier_rank = {row["id"]: int(row["rank"]) for row in policy["ranking_tiers"]}
    material_tiers = set(policy["material_ambiguity_tiers"])
    failures: list[dict[str, Any]] = []

    decision_by_tender = {d.coalesced_tender_id: d for d in decisions}
    org_by_tender = {o.coalesced_tender_id: o for o in organizations}
    summary_by_tender = {s.coalesced_tender_id: s for s in summaries}
    cands_by_summary: dict[str, list[ContactCandidate]] = {}
    for c in candidates:
        cands_by_summary.setdefault(c.contact_resolution_id, []).append(c)
    evidence_by_id = {e.evidence_id: e for e in evidence}
    conflicts_by_tender: dict[str, list[ContactResolutionConflict]] = {}
    for conf in conflicts:
        conflicts_by_tender.setdefault(conf.coalesced_tender_id, []).append(conf)

    frozen_contacts = frozen_index.contacts_by_id
    frozen_evidence = frozen_index.evidence_by_id
    pr4_by_procurement = frozen_index.pr4_by_procurement

    tender_ids = set(decision_by_tender)
    equations = {
        "pr5d_decisions_eq_organization_resolutions": len(decisions) == len(organizations)
        and tender_ids == set(org_by_tender),
        "organization_resolutions_eq_contact_summaries": len(organizations)
        == len(summaries)
        and set(org_by_tender) == set(summary_by_tender),
        "tender_ids_align": tender_ids
        == set(org_by_tender)
        == set(summary_by_tender)
        == set(tenders_by_id).intersection(tender_ids),
        "one_org_per_tender": len(org_by_tender) == len(organizations),
        "one_summary_per_tender": len(summary_by_tender) == len(summaries),
        "one_decision_per_tender": len(decision_by_tender) == len(decisions),
    }

    seen_pairs: set[tuple[str, str]] = set()
    for tender_id, decision in decision_by_tender.items():
        if tender_id not in tenders_by_id:
            failures.append(
                {"error": "missing_coalesced_tender", "tender": tender_id}
            )
            continue
        tender = tenders_by_id[tender_id]
        org = org_by_tender.get(tender_id)
        summary = summary_by_tender.get(tender_id)
        if org is None:
            failures.append({"error": "missing_organization", "tender": tender_id})
            continue
        if summary is None:
            failures.append({"error": "missing_contact_summary", "tender": tender_id})
            continue

        if org.relevance_decision_id != decision.decision_id:
            failures.append(
                {
                    "error": "organization_relevance_decision_mismatch",
                    "tender": tender_id,
                    "expected": decision.decision_id,
                    "got": org.relevance_decision_id,
                }
            )
        if summary.relevance_decision_id != decision.decision_id:
            failures.append(
                {
                    "error": "summary_relevance_decision_mismatch",
                    "tender": tender_id,
                    "expected": decision.decision_id,
                    "got": summary.relevance_decision_id,
                }
            )
        if summary.organization_resolution_id != org.organization_resolution_id:
            failures.append(
                {
                    "error": "summary_organization_id_mismatch",
                    "tender": tender_id,
                }
            )
        if (
            summary.account_id != org.account_id
            and summary.final_contact_status != CONTACT_RESOLUTION_DEFERRED
        ):
            if org.resolution_status == "linked":
                failures.append(
                    {
                        "error": "summary_account_mismatch_organization",
                        "tender": tender_id,
                    }
                )

        cands = sorted(
            cands_by_summary.get(summary.contact_resolution_id, []),
            key=lambda c: c.rank,
        )
        ranks = [c.rank for c in cands]
        if cands and ranks != list(range(1, len(cands) + 1)):
            failures.append(
                {
                    "error": "candidate_rank_drift",
                    "tender": tender_id,
                    "ranks": ranks,
                }
            )
        if len({c.rank for c in cands}) != len(cands):
            failures.append(
                {"error": "duplicate_candidate_ranks", "tender": tender_id}
            )

        if summary.considered_contact_count != len(cands):
            failures.append(
                {
                    "error": "considered_count_drift",
                    "tender": tender_id,
                    "expected": len(cands),
                    "got": summary.considered_contact_count,
                }
            )
        suitable_actual = sum(
            1
            for c in cands
            if is_suitable_role(c.role_suitability) and c.has_usable_email
        )
        if summary.suitable_contact_count != suitable_actual:
            failures.append(
                {
                    "error": "suitable_count_drift",
                    "tender": tender_id,
                }
            )
        blocked_actual = sum(
            1 for c in cands if c.safety_blocked or c.safety_unknown
        )
        if summary.blocked_contact_count != blocked_actual:
            failures.append(
                {
                    "error": "blocked_count_drift",
                    "tender": tender_id,
                }
            )

        if summary.final_contact_status == CONTACT_RESOLUTION_DEFERRED:
            if cands:
                failures.append(
                    {"error": "deferred_has_candidates", "tender": tender_id}
                )
            if summary.search_stages_completed:
                failures.append(
                    {"error": "deferred_has_search_stages", "tender": tender_id}
                )
            if org.account_id is not None and org.resolution_status == "linked":
                failures.append(
                    {
                        "error": "deferred_despite_linked_account",
                        "tender": tender_id,
                    }
                )
        else:
            if org.resolution_status != "linked" or not org.account_id:
                failures.append(
                    {
                        "error": "contact_search_without_linked_account",
                        "tender": tender_id,
                    }
                )

        if summary.final_contact_status in {
            "existing_verified_contact",
            "existing_contact_needs_role_review",
            "role_known_email_missing",
            "contact_blocked",
            "ambiguous_contact",
        }:
            if not cands:
                failures.append(
                    {
                        "error": "existing_status_without_candidates",
                        "tender": tender_id,
                        "status": summary.final_contact_status,
                    }
                )

        for i in range(len(cands) - 1):
            a, b = cands[i], cands[i + 1]
            ka = _candidate_sort_key(a, tier_rank=tier_rank)
            kb = _candidate_sort_key(b, tier_rank=tier_rank)
            if ka > kb:
                failures.append(
                    {
                        "error": "candidate_rank_order_inverted",
                        "tender": tender_id,
                        "left": a.candidate_id,
                        "right": b.candidate_id,
                    }
                )

        for c in cands:
            pair = (c.contact_resolution_id, c.contact_id)
            if pair in seen_pairs:
                failures.append(
                    {
                        "error": "duplicate_contact_resolution_contact_pair",
                        "pair": list(pair),
                    }
                )
            seen_pairs.add(pair)
            if c.coalesced_tender_id != tender_id:
                failures.append(
                    {
                        "error": "candidate_tender_mismatch",
                        "candidate_id": c.candidate_id,
                    }
                )
            if summary.account_id and c.account_id != summary.account_id:
                failures.append(
                    {
                        "error": "candidate_account_mismatch",
                        "candidate_id": c.candidate_id,
                    }
                )
            if c.contact_resolution_id != summary.contact_resolution_id:
                failures.append(
                    {
                        "error": "candidate_summary_mismatch",
                        "candidate_id": c.candidate_id,
                    }
                )

            if len(c.evidence_ids) != len(set(c.evidence_ids)):
                failures.append(
                    {
                        "error": "duplicate_evidence_ids_on_candidate",
                        "candidate_id": c.candidate_id,
                    }
                )

            frozen_contact = frozen_contacts.get(c.contact_id)
            if frozen_contact is None:
                failures.append(
                    {
                        "error": "candidate_missing_frozen_contact",
                        "candidate_id": c.candidate_id,
                        "contact_id": c.contact_id,
                    }
                )
            else:
                if frozen_contact.account_id != c.account_id:
                    failures.append(
                        {
                            "error": "candidate_frozen_account_mismatch",
                            "candidate_id": c.candidate_id,
                        }
                    )
                if summary.account_id and frozen_contact.account_id != summary.account_id:
                    failures.append(
                        {
                            "error": "frozen_contact_summary_account_mismatch",
                            "candidate_id": c.candidate_id,
                        }
                    )
                frozen_eids = set(frozen_contact.evidence_ids)
                cand_eids = set(c.evidence_ids)
                if cand_eids != frozen_eids:
                    for eid in c.evidence_ids:
                        fe = frozen_evidence.get(eid)
                        if fe is None:
                            failures.append(
                                {
                                    "error": "evidence_pointer_unresolved",
                                    "evidence_id": eid,
                                    "candidate_id": c.candidate_id,
                                }
                            )
                        elif fe.subject_id != c.contact_id:
                            failures.append(
                                {
                                    "error": "frozen_evidence_subject_mismatch",
                                    "evidence_id": eid,
                                }
                            )
                    if not cand_eids.issubset(frozen_eids):
                        failures.append(
                            {
                                "error": "candidate_evidence_ids_not_subset_of_frozen",
                                "candidate_id": c.candidate_id,
                            }
                        )

                recomputed_tier = _recompute_tier_from_frozen(
                    c, frozen_index=frozen_index, policy=policy
                )
                if recomputed_tier is not None and recomputed_tier != c.ranking_tier:
                    failures.append(
                        {
                            "error": "ranking_tier_mismatch_vs_frozen",
                            "candidate_id": c.candidate_id,
                            "expected": recomputed_tier,
                            "got": c.ranking_tier,
                        }
                    )

                frozen_ev_rows = [
                    frozen_evidence[eid].to_dict()
                    for eid in frozen_contact.evidence_ids
                    if eid in frozen_evidence
                ]
                predicate_verified = contact_has_explicit_verification(
                    identity_status=frozen_contact.identity_status,
                    identity_confidence=frozen_contact.identity_confidence,
                    evidence_rows=frozen_ev_rows,
                    contact_id=c.contact_id,
                    policy=policy,
                )
                if (
                    c.verification_status == "explicit_verification"
                    and not predicate_verified
                ):
                    failures.append(
                        {
                            "error": "verification_claim_without_frozen_predicate",
                            "candidate_id": c.candidate_id,
                        }
                    )
                if predicate_verified and c.verification_status != "explicit_verification":
                    failures.append(
                        {
                            "error": "frozen_verification_without_candidate_claim",
                            "candidate_id": c.candidate_id,
                        }
                    )

            for eid in c.evidence_ids:
                ev = evidence_by_id.get(eid)
                if ev is None:
                    failures.append(
                        {
                            "error": "missing_evidence",
                            "evidence_id": eid,
                            "candidate_id": c.candidate_id,
                        }
                    )
                    continue
                if ev.subject_id != c.contact_id:
                    failures.append(
                        {
                            "error": "evidence_contact_mismatch",
                            "evidence_id": eid,
                        }
                    )
                frozen = frozen_evidence.get(eid)
                if frozen is None:
                    failures.append(
                        {
                            "error": "evidence_pointer_unresolved",
                            "evidence_id": eid,
                        }
                    )
                    continue
                frozen_dict = frozen.to_dict()
                for field in _EVIDENCE_COMPARE_FIELDS:
                    plan_val = getattr(ev, field)
                    frozen_val = frozen_dict.get(field)
                    if str(plan_val or "") != str(frozen_val or ""):
                        failures.append(
                            {
                                "error": "evidence_field_mismatch",
                                "evidence_id": eid,
                                "field": field,
                                "plan": plan_val,
                                "frozen": frozen_val,
                            }
                        )

        if summary.final_contact_status != CONTACT_RESOLUTION_DEFERRED:
            status_r, selected_r, _, _ = select_final_status(
                ranked=cands, policy=policy, tender_id=tender_id
            )
            if status_r != summary.final_contact_status:
                failures.append(
                    {
                        "error": "final_status_mismatch",
                        "tender": tender_id,
                        "expected": status_r,
                        "got": summary.final_contact_status,
                    }
                )
            sel_id = selected_r.candidate_id if selected_r else None
            if sel_id != summary.selected_candidate_id:
                failures.append(
                    {
                        "error": "selected_candidate_mismatch_vs_recompute",
                        "tender": tender_id,
                        "expected": sel_id,
                        "got": summary.selected_candidate_id,
                    }
                )

        if summary.final_contact_status == "ambiguous_contact":
            if len(cands) < 2:
                failures.append(
                    {
                        "error": "ambiguous_without_competing_candidates",
                        "tender": tender_id,
                    }
                )
            conf_rows = conflicts_by_tender.get(tender_id, [])
            if not conf_rows:
                failures.append(
                    {
                        "error": "ambiguous_without_conflict_row",
                        "tender": tender_id,
                    }
                )
            elif cands:
                selectable = [c for c in cands if c.selectable]
                if selectable:
                    top = selectable[0]
                    peers = [
                        c
                        for c in selectable
                        if c.ranking_tier == top.ranking_tier
                        and c.ranking_tier in material_tiers
                    ]
                    expected_subjects = tuple(sorted(p.contact_id for p in peers))
                    for conf in conf_rows:
                        if conf.conflict_type == "ambiguous_contact":
                            if tuple(sorted(conf.subject_keys)) != expected_subjects:
                                failures.append(
                                    {
                                        "error": "ambiguous_conflict_subjects_mismatch",
                                        "tender": tender_id,
                                        "expected": list(expected_subjects),
                                        "got": list(conf.subject_keys),
                                    }
                                )

        label_status = summary.relevance_validation_status_echo or None
        if not label_status:
            label_status = None
        expected_next = next_action_for_status(
            summary.final_contact_status,
            lifecycle_class=summary.lifecycle_class_echo
            or decision.lifecycle_class_echo
            or "",
            relevance_class=summary.relevance_class_echo
            or decision.relevance_class
            or "",
            currentness_class=summary.currentness_class_echo
            or (tender.currentness_class or ""),
            label_status=label_status,
            independently_reviewed=False,
            policy=policy,
        )
        if summary.next_action != expected_next:
            failures.append(
                {
                    "error": "next_action_mismatch",
                    "tender": tender_id,
                    "expected": expected_next,
                    "got": summary.next_action,
                }
            )

        if summary.final_contact_status == CONTACT_RESOLUTION_DEFERRED:
            if summary.search_stages_completed:
                failures.append(
                    {"error": "deferred_has_search_stages", "tender": tender_id}
                )
        elif org.resolution_status == "linked" and org.account_id:
            expected_stages = _expected_linked_search_stages()
            if tuple(summary.search_stages_completed) != expected_stages:
                failures.append(
                    {
                        "error": "search_stages_mismatch",
                        "tender": tender_id,
                        "expected": list(expected_stages),
                        "got": list(summary.search_stages_completed),
                    }
                )

        expected_pr4_rids: set[str] = set()
        for pid in org.pr4_procurement_ids:
            prow = pr4_by_procurement.get(pid)
            if prow is None:
                if org.resolution_source.startswith("pr4_") and (
                    org.resolution_source != "pr4_constituent_incomplete"
                ):
                    failures.append(
                        {
                            "error": "pr4_procurement_pointer_unresolved",
                            "procurement_id": pid,
                            "tender": tender_id,
                        }
                    )
            else:
                expected_pr4_rids.add(prow.resolution_id)
        if (
            org.pr4_procurement_ids
            and org.resolution_source != "pr4_constituent_incomplete"
            and set(org.pr4_resolution_ids) != expected_pr4_rids
        ):
            failures.append(
                {
                    "error": "pr4_resolution_ids_mismatch",
                    "tender": tender_id,
                    "expected": sorted(expected_pr4_rids),
                    "got": sorted(org.pr4_resolution_ids),
                }
            )
        for rid in org.pr4_resolution_ids:
            if not any(row.resolution_id == rid for row in pr4_by_procurement.values()):
                failures.append(
                    {
                        "error": "pr4_resolution_pointer_unresolved",
                        "resolution_id": rid,
                        "tender": tender_id,
                    }
                )

        if summary.selected_candidate_id:
            matches = [
                c for c in cands if c.candidate_id == summary.selected_candidate_id
            ]
            if len(matches) != 1:
                failures.append(
                    {
                        "error": "invalid_selected_candidate_pointer",
                        "tender": tender_id,
                        "matches": len(matches),
                    }
                )
            else:
                sel = matches[0]
                if sel.contact_id != summary.selected_contact_id:
                    failures.append(
                        {
                            "error": "selected_contact_disagreement",
                            "tender": tender_id,
                        }
                    )
                if sel.safety_blocked or sel.safety_unknown or not sel.selectable:
                    failures.append(
                        {
                            "error": "selected_contact_not_selectable",
                            "tender": tender_id,
                        }
                    )
                if sel.account_id != summary.account_id:
                    failures.append(
                        {
                            "error": "selected_contact_account_mismatch",
                            "tender": tender_id,
                        }
                    )
                if summary.final_contact_status == "existing_verified_contact":
                    if sel.verification_status != "explicit_verification":
                        failures.append(
                            {
                                "error": "verified_status_without_verification",
                                "tender": tender_id,
                            }
                        )
                    if not is_suitable_role(sel.role_suitability):
                        failures.append(
                            {
                                "error": "verified_status_without_suitable_role",
                                "tender": tender_id,
                            }
                        )
                    if not sel.has_usable_email:
                        failures.append(
                            {
                                "error": "verified_status_without_usable_email",
                                "tender": tender_id,
                            }
                        )
        elif summary.selected_contact_id:
            failures.append(
                {
                    "error": "selected_contact_without_candidate_pointer",
                    "tender": tender_id,
                }
            )

        recomputed_sem = recompute_summary_semantic_fingerprint(
            contact_resolution_id=summary.contact_resolution_id,
            final_contact_status=summary.final_contact_status,
            account_id=summary.account_id,
            selected_contact_id=summary.selected_contact_id,
            selected_candidate_id=summary.selected_candidate_id,
            search_stages_completed=summary.search_stages_completed,
            next_action=summary.next_action,
            reason_code=summary.reason_code,
            candidate_ids=[c.candidate_id for c in cands],
        )
        if recomputed_sem != summary.semantic_fingerprint:
            failures.append(
                {
                    "error": "semantic_fingerprint_mismatch",
                    "tender": tender_id,
                }
            )

    if len(evidence_by_id) != len(evidence):
        failures.append({"error": "duplicate_evidence_ids_in_plan"})

    equations["invariant_failures_empty"] = not failures
    equations["binding_per_tender_ok"] = not failures
    ok = all(equations.values())
    return {
        "ok": ok,
        "equations": equations,
        "failures": failures[:80],
        "relevance_decision_count": len(decisions),
        "organization_resolution_count": len(organizations),
        "contact_summary_count": len(summaries),
        "candidate_count": len(candidates),
        "evidence_count": len(evidence),
        "conflict_count": len(conflicts),
    }


def _cross_tab(
    summaries: list[ContactResolutionSummary],
) -> dict[str, Any]:
    key_counts: Counter[str] = Counter()
    for s in summaries:
        validation = s.relevance_validation_status_echo or "unvalidated"
        key = (
            f"{s.lifecycle_class_echo}|{s.relevance_class_echo}|"
            f"{validation}|{s.final_contact_status}"
        )
        key_counts[key] += 1
    return {
        "note": "lifecycle|relevance|validation|contact_status",
        "counts": dict(sorted(key_counts.items())),
    }


def build_contact_resolution_plan(
    *,
    sqlite_path: Path,
    acquisition_snapshot_paths: list[Path],
    as_of_utc: str,
    freshness_threshold_hours: int,
    run_context: str,
    labeling_queue_size: int = 200,
    pr5d_plan: ProductRelevancePlanResult | None = None,
    pr5c_plan: CandidatePlanResult | None = None,
    gmail_user: str | None = None,
) -> ContactResolutionPlanResult:
    """Build a deterministic read-only contact-resolution plan for every PR5D tender."""
    candidate_plan = pr5c_plan or build_candidate_plan(
        sqlite_path=sqlite_path,
        acquisition_snapshot_paths=acquisition_snapshot_paths,
        as_of_utc=as_of_utc,
        freshness_threshold_hours=freshness_threshold_hours,
        run_context=run_context,
    )
    relevance_plan = pr5d_plan or build_product_relevance_plan(
        sqlite_path=sqlite_path,
        acquisition_snapshot_paths=acquisition_snapshot_paths,
        as_of_utc=as_of_utc,
        freshness_threshold_hours=freshness_threshold_hours,
        run_context=run_context,
        labeling_queue_size=labeling_queue_size,
        pr5c_plan=candidate_plan,
    )

    pr5c_digest = _require_digest("pr5c_semantic_digest", candidate_plan.semantic_digest)
    recorded_pr5c = _require_digest(
        "pr5d.pr5c_semantic_digest", relevance_plan.pr5c_semantic_digest
    )
    if recorded_pr5c != pr5c_digest:
        raise ContactDependencyError(
            "PR5D recorded PR5C semantic digest does not equal injected CandidatePlanResult"
        )
    pr5d_semantic = _require_digest(
        "pr5d_semantic_digest",
        str(
            relevance_plan.fingerprints.get("semantic_digest")
            or relevance_plan.fingerprints.get("semantic_fingerprint")
            or ""
        ),
    )

    tenders_by_id = {
        t.coalesced_tender_id: t for t in candidate_plan.coalesced_tenders
    }
    decisions: tuple[TenderRelevanceDecision, ...] = relevance_plan.tender_decisions

    # PR5D operational evaluation records do not join to coalesced tender ids.
    # Absence of per-tender validation fails closed for gated next actions.
    _ = relevance_plan.operational_evaluation_records
    _ = relevance_plan.evaluation_meta

    conn = connect_production_readonly(sqlite_path)
    try:
        assert_no_write_connection(conn)
        conn.execute("BEGIN DEFERRED")
        enable_require_active_read_transaction(conn)
        try:
            identity_meta = load_identity_fingerprint_meta(conn)
            identity_fp = _require_digest(
                "identity_fingerprint",
                str(
                    identity_meta.get("identity_fingerprint")
                    or identity_meta.get("fingerprint")
                    or ""
                ),
            )
            account_index, known_accounts = open_account_index(conn)
            try:
                pr4_by_proc = load_pr4_resolutions_by_procurement(conn)
            except Pr4ProvenanceError as exc:
                raise ContactDependencyError(
                    f"PR4 provenance load failed: {exc}"
                ) from exc
            safety = load_safety_snapshot(conn, gmail_user=gmail_user)

            pass1: list[
                tuple[
                    TenderRelevanceDecision,
                    CoalescedProcurementTender,
                    OrganizationResolution,
                ]
            ] = []
            account_ids: set[str] = set()
            pr4_procurement_ids: set[str] = set()

            for decision in sorted(decisions, key=lambda d: d.coalesced_tender_id):
                tender = tenders_by_id.get(decision.coalesced_tender_id)
                if tender is None:
                    raise ContactReconciliationError(
                        f"missing coalesced tender for decision {decision.decision_id}"
                    )
                org = resolve_organization_for_tender(
                    tender,
                    relevance_decision_id=decision.decision_id,
                    account_index=account_index,
                    known_account_ids=known_accounts,
                    pr4_by_procurement=pr4_by_proc,
                    identity_fingerprint=identity_fp,
                )
                pass1.append((decision, tender, org))
                if org.account_id:
                    account_ids.add(org.account_id)
                pr4_procurement_ids.update(tender.pr4_procurement_ids or ())
                pr4_procurement_ids.update(org.pr4_procurement_ids or ())

            frozen_index = load_frozen_source_index(
                conn,
                account_ids=account_ids,
                pr4_procurement_ids=pr4_procurement_ids,
                known_account_ids=known_accounts,
            )

            # Pass 2 — fail closed: no per-tender PR5D validation join.
            organizations: list[OrganizationResolution] = []
            summaries: list[ContactResolutionSummary] = []
            candidates: list[ContactCandidate] = []
            evidence: list[ContactResolutionEvidence] = []
            conflicts: list[ContactResolutionConflict] = []

            for decision, tender, org in pass1:
                organizations.append(org)
                summary, cands, evs, confs = resolve_contacts_for_tender(
                    tender_id=tender.coalesced_tender_id,
                    relevance=decision,
                    organization=org,
                    conn=conn,
                    safety=safety,
                    buyer_email_norm=None,
                    institution_name=tender.buyer_display_selected,
                    input_fingerprint="provisional",
                    currentness_class=tender.currentness_class or "",
                    label_status=None,
                    independently_reviewed=False,
                    frozen_index=frozen_index,
                )
                summaries.append(summary)
                candidates.extend(cands)
                evidence.extend(evs)
                conflicts.extend(confs)
        finally:
            if conn.in_transaction:
                conn.rollback()
            disable_require_active_read_transaction(conn)
    finally:
        conn.close()

    organizations_t = tuple(
        sorted(organizations, key=lambda o: o.organization_resolution_id)
    )
    summaries_t = tuple(
        sorted(summaries, key=lambda s: s.contact_resolution_id)
    )
    candidates_t = tuple(sorted(candidates, key=lambda c: c.candidate_id))
    evidence_dedup: dict[str, ContactResolutionEvidence] = {}
    for e in evidence:
        evidence_dedup.setdefault(e.evidence_id, e)
    evidence_t = tuple(sorted(evidence_dedup.values(), key=lambda e: e.evidence_id))
    conflicts_t = tuple(sorted(conflicts, key=lambda c: c.conflict_id))

    pr4_ids = sorted(
        {
            rid
            for o in organizations_t
            for rid in o.pr4_resolution_ids
        }
    )
    contact_ids = sorted({c.contact_id for c in candidates_t})
    evidence_ids = sorted({e.evidence_id for e in evidence_t})

    fps = all_fingerprints(
        pr5c_semantic_digest=pr5c_digest,
        pr5d_semantic_digest=pr5d_semantic,
        identity_fingerprint=identity_fp,
        safety_fingerprint=safety.safety_fingerprint,
        frozen_source_fingerprint=frozen_index.source_fingerprint,
        organization_resolutions=organizations_t,
        summaries=summaries_t,
        candidates=candidates_t,
        evidence=evidence_t,
        conflicts=conflicts_t,
        pr4_resolution_ids=pr4_ids,
        pr2_contact_ids=contact_ids,
        pr2_evidence_ids=evidence_ids,
    )
    summaries_final = tuple(
        ContactResolutionSummary(
            contact_resolution_id=s.contact_resolution_id,
            coalesced_tender_id=s.coalesced_tender_id,
            relevance_decision_id=s.relevance_decision_id,
            organization_resolution_id=s.organization_resolution_id,
            account_id=s.account_id,
            final_contact_status=s.final_contact_status,
            selected_contact_id=s.selected_contact_id,
            selected_candidate_id=s.selected_candidate_id,
            search_stages_completed=s.search_stages_completed,
            next_action=s.next_action,
            reason_code=s.reason_code,
            considered_contact_count=s.considered_contact_count,
            suitable_contact_count=s.suitable_contact_count,
            blocked_contact_count=s.blocked_contact_count,
            relevance_class_echo=s.relevance_class_echo,
            lifecycle_class_echo=s.lifecycle_class_echo,
            currentness_class_echo=s.currentness_class_echo,
            relevance_validation_status_echo=s.relevance_validation_status_echo,
            input_fingerprint=fps["input_fingerprint"],
            semantic_fingerprint=s.semantic_fingerprint,
            rules_version=s.rules_version,
            resolver_version=s.resolver_version,
        )
        for s in summaries_t
    )

    recon = reconcile_contact_resolution(
        decisions=list(decisions),
        tenders_by_id=tenders_by_id,
        organizations=list(organizations_t),
        summaries=list(summaries_final),
        candidates=list(candidates_t),
        evidence=list(evidence_t),
        conflicts=list(conflicts_t),
        frozen_index=frozen_index,
        safety=safety,
    )
    if not recon["ok"]:
        raise ContactReconciliationError(
            f"contact resolution reconciliation failed: {recon['failures'][:5]}"
        )

    org_status = Counter(o.resolution_status for o in organizations_t)
    org_source = Counter(o.resolution_source for o in organizations_t)
    contact_status = Counter(s.final_contact_status for s in summaries_final)
    next_actions = Counter(s.next_action for s in summaries_final)
    stages = Counter(
        stage
        for s in summaries_final
        for stage in s.search_stages_completed
    )

    field_audit = {
        "buyer_display_selected": "coalesced display string; normalized via safe_org_normalized",
        "buyer_source_id_selected": (
            "provenance only — treated as institutional-domain candidate when "
            "dot-separated; never as PR2 account_id"
        ),
        "pr4_procurement_ids": (
            "carry-forward only when every constituent PR4 resolution exists, "
            "is linked to the same PR2 account still present in the frozen identity "
            "input, and candidate-account conflicts are absent"
        ),
        "commercial_identity_contact.role": (
            "sole role suitability authority; email local-part never used"
        ),
        "verification": (
            "existing_verified_contact requires accepted PR2 evidence_type="
            "contact_identity + matching_reason_code=exact_email + high confidence "
            "+ contact_master source plane/table, plus suitable role, usable "
            "canonical email, resolved identity, and clear safety"
        ),
        "safety": (
            "full marketing_export_context GateContext (Sent, internal domains, "
            "email/domain suppression, outreach states, supplier/noise). "
            "Incomplete truth → safety_unknown / non-selectable"
        ),
        "usable_email": "normalize_export_email / emails_in must succeed",
        "relevance_validation": (
            "Absence of per-tender PR5D validation (label_status=None, "
            "independently_reviewed=False) fails closed for gated lead/research actions"
        ),
    }

    counts = {
        "pr5d_relevance_decisions": len(decisions),
        "organization_resolutions": len(organizations_t),
        "contact_summaries": len(summaries_final),
        "contact_candidates": len(candidates_t),
        "by_organization_status": dict(sorted(org_status.items())),
        "by_organization_source": dict(sorted(org_source.items())),
        "by_contact_status": dict(sorted(contact_status.items())),
        "by_next_action": dict(sorted(next_actions.items())),
        "by_search_stage": dict(sorted(stages.items())),
        "considered_contacts": len(candidates_t),
        "suitable_contacts": sum(s.suitable_contact_count for s in summaries_final),
        "selected_contacts": sum(
            1 for s in summaries_final if s.selected_contact_id
        ),
        "ambiguous_contacts": contact_status.get("ambiguous_contact", 0),
        "blocked_contacts": contact_status.get("contact_blocked", 0),
        "lifecycle_relevance_contact_crosstab": _cross_tab(list(summaries_final)),
        "safety_truth_complete": safety.truth_complete,
        "note": (
            "PR5D proposed/reviewed/gold remains 200/0/0 elsewhere; "
            "PR5E rows never imply unreviewed relevance predictions are validated leads."
        ),
    }

    from origenlab_email_pipeline.commercial_procurement_contact_resolution.walkthrough import (
        build_contact_resolution_walkthrough,
    )

    walkthrough = build_contact_resolution_walkthrough(
        organizations=organizations_t,
        summaries=summaries_final,
        candidates=candidates_t,
    )

    return ContactResolutionPlanResult(
        as_of_utc=as_of_utc,
        run_context=run_context,
        planner_version=CONTACT_RESOLUTION_PLANNER_VERSION,
        organization_resolutions=organizations_t,
        contact_summaries=summaries_final,
        contact_candidates=candidates_t,
        evidence=evidence_t,
        conflicts=conflicts_t,
        reconciliation=recon,
        fingerprints=fps,
        dependency_fingerprints={
            "identity_fingerprint": identity_fp,
            "pr5c_semantic_digest": pr5c_digest,
            "pr5d_semantic_digest": pr5d_semantic,
            "pr5d_input_fingerprint": _require_digest(
                "pr5d_input_fingerprint",
                str(relevance_plan.fingerprints.get("input_fingerprint") or ""),
            ),
            "pr5d_rules_fingerprint": _require_digest(
                "pr5d_rules_fingerprint",
                str(relevance_plan.fingerprints.get("rules_fingerprint") or ""),
            ),
            "safety_fingerprint": safety.safety_fingerprint,
            "frozen_source_fingerprint": frozen_index.source_fingerprint,
        },
        counts=counts,
        field_sufficiency_audit=field_audit,
        walkthrough=walkthrough,
    )


def write_contact_resolution_outputs(
    result: ContactResolutionPlanResult,
    out_dir: Path,
    *,
    include_walkthrough: bool = True,
    require_git_ignored: bool = True,
) -> dict[str, str]:
    """Atomically publish plan artifacts under reports/out."""
    root = Path(__file__).resolve().parents[3]

    def _writer(dest: Path) -> dict[str, str]:
        dest.mkdir(parents=True, exist_ok=True)
        summary = result.to_summary_dict()
        shareable = redact_summary_for_share(summary)
        assert_no_raw_pii(json.dumps(shareable, ensure_ascii=True))
        (dest / "summary.json").write_text(
            json.dumps(shareable, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        operational = {
            "organization_resolutions": [
                o.to_dict() for o in result.organization_resolutions
            ],
            "contact_summaries": [s.to_dict() for s in result.contact_summaries],
            "contact_candidates": [c.to_dict() for c in result.contact_candidates],
            "evidence": [e.to_dict() for e in result.evidence],
            "conflicts": [c.to_dict() for c in result.conflicts],
            "reconciliation": result.reconciliation,
            "fingerprints": result.fingerprints,
            "dependency_fingerprints": result.dependency_fingerprints,
            "counts": result.counts,
            "not_persisted": True,
        }
        (dest / "operational_contact_resolution_plan.json").write_text(
            json.dumps(operational, indent=2, sort_keys=True, ensure_ascii=True)
            + "\n",
            encoding="utf-8",
        )
        written = {
            "summary": str(dest / "summary.json"),
            "operational_plan": str(
                dest / "operational_contact_resolution_plan.json"
            ),
            "manifest": str(dest / "manifest.json"),
        }
        if include_walkthrough:
            (dest / "walkthrough.json").write_text(
                json.dumps(
                    result.walkthrough, indent=2, sort_keys=True, ensure_ascii=True
                )
                + "\n",
                encoding="utf-8",
            )
            (dest / "walkthrough.md").write_text(
                str(result.walkthrough.get("markdown") or ""),
                encoding="utf-8",
            )
            written["walkthrough_json"] = str(dest / "walkthrough.json")
            written["walkthrough_md"] = str(dest / "walkthrough.md")
        manifest = {
            "planner_version": result.planner_version,
            "as_of_utc": result.as_of_utc,
            "run_context": result.run_context,
            "fingerprints": result.fingerprints,
            "dependency_fingerprints": result.dependency_fingerprints,
            "not_persisted": True,
            "forbidden_flags": list(FORBIDDEN_CLI_FLAGS),
        }
        (dest / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        return written

    return write_atomically(
        out_dir,
        repo_email_pipeline_root=root,
        writer=_writer,
        require_git_ignored=require_git_ignored,
    )


__all__ = [
    "ContactDependencyError",
    "ContactReconciliationError",
    "build_contact_resolution_plan",
    "empty_frozen_source_index",
    "reconcile_contact_resolution",
    "write_contact_resolution_outputs",
    "FORBIDDEN_CLI_FLAGS",
]
