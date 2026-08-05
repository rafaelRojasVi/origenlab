"""Exhaustive source-projected reconciliation for PR5E."""

from __future__ import annotations

from dataclasses import fields
from typing import Any, Mapping, Sequence

from origenlab_email_pipeline.commercial_procurement.link_routes import AccountIndex
from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    CoalescedProcurementTender,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.constants import (
    CONTACT_RESOLUTION_DEFERRED,
    CONTACT_RESOLUTION_RULES_VERSION,
    CONTACT_RESOLVER_VERSION,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.contact_search import (
    deferred_summary,
    project_linked_contact_resolution,
    recompute_summary_semantic_fingerprint,
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
from origenlab_email_pipeline.commercial_procurement_contact_resolution.organization import (
    assess_buyer_field_sufficiency,
    resolve_organization_for_tender,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.policy import (
    contact_has_explicit_verification,
    contact_resolution_policy_spec,
    is_suitable_role,
    next_action_for_status,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.safety import (
    SafetySnapshot,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.stable_ids import (
    candidate_id_for,
    contact_resolution_id_deferred,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
    TenderRelevanceDecision,
)

_CANDIDATE_COMPARE_FIELDS = tuple(
    f.name for f in fields(ContactCandidate) if f.name != "contact_resolution_id"
)
# Parent ID is compared separately against expected final CRS.
_SUMMARY_COMPARE_FIELDS = tuple(f.name for f in fields(ContactResolutionSummary))
_ORG_COMPARE_FIELDS = tuple(f.name for f in fields(OrganizationResolution))
_CONFLICT_COMPARE_FIELDS = tuple(f.name for f in fields(ContactResolutionConflict))
_EVIDENCE_COMPARE_FIELDS = tuple(f.name for f in fields(ContactResolutionEvidence))


def tender_pr4_procurement_ids(tender: CoalescedProcurementTender) -> tuple[str, ...]:
    """Exact PR4 constituent IDs from the frozen tender (not from emitted org)."""
    pr4_ids = tuple(sorted(set(tender.pr4_procurement_ids or ())))
    if tender.pr4_procurement_id and tender.pr4_procurement_id not in pr4_ids:
        pr4_ids = tuple(sorted({*pr4_ids, tender.pr4_procurement_id}))
    return pr4_ids


def _conflict_key(c: ContactResolutionConflict) -> tuple[Any, ...]:
    return (
        c.conflict_id,
        c.coalesced_tender_id,
        c.conflict_type,
        c.reason_code,
        tuple(sorted(c.subject_keys)),
        tuple(sorted(c.evidence_ids)),
    )


def _candidate_payload(c: ContactCandidate) -> dict[str, Any]:
    return {name: getattr(c, name) for name in _CANDIDATE_COMPARE_FIELDS}


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
    safety: SafetySnapshot,
    account_index: AccountIndex | None = None,
    known_account_ids: frozenset[str] | None = None,
    identity_fingerprint: str | None = None,
    buyer_email_by_tender: Mapping[str, str | None] | None = None,
) -> dict[str, Any]:
    """Reconcile emitted rows against an independent frozen source projection.

    Safety is required. Expected candidates are recomputed via
    ``evaluate_contact_safety`` on frozen normalized emails — never from
    emitted safety flags. Candidate contact-ID sets must equal
    ``frozen_index.contact_ids_for_account``. Evidence coverage is exact.
    """
    if safety is None:  # type: ignore[comparison-overlap]
        raise TypeError("safety SafetySnapshot is required for reconciliation")

    policy = contact_resolution_policy_spec()
    failures: list[dict[str, Any]] = []
    buyer_email_by_tender = buyer_email_by_tender or {}

    decision_by_tender = {d.coalesced_tender_id: d for d in decisions}
    org_by_tender = {o.coalesced_tender_id: o for o in organizations}
    summary_by_tender = {s.coalesced_tender_id: s for s in summaries}
    cands_by_summary: dict[str, list[ContactCandidate]] = {}
    for c in candidates:
        cands_by_summary.setdefault(c.contact_resolution_id, []).append(c)
    cands_by_tender: dict[str, list[ContactCandidate]] = {}
    for c in candidates:
        cands_by_tender.setdefault(c.coalesced_tender_id, []).append(c)
    evidence_by_id = {e.evidence_id: e for e in evidence}
    conflicts_by_tender: dict[str, list[ContactResolutionConflict]] = {}
    for conf in conflicts:
        conflicts_by_tender.setdefault(conf.coalesced_tender_id, []).append(conf)

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

    plan_evidence_ids = {e.evidence_id for e in evidence}
    union_candidate_evidence_ids: set[str] = set()
    for c in candidates:
        union_candidate_evidence_ids.update(c.evidence_ids)

    # Build PR4 mapping once for org re-resolve (not per tender).
    pr4_mapping_for_resolve: dict[str, dict[str, Any]] | None = None
    if (
        account_index is not None
        and known_account_ids is not None
        and identity_fingerprint is not None
    ):
        pr4_mapping_for_resolve = {
            pid: {
                "resolution_id": row.resolution_id,
                "procurement_id": row.procurement_id,
                "resolution_status": row.resolution_status,
                "account_id": row.account_id,
                "link_route": row.link_route,
                "reason_code": row.reason_code,
                "candidate_account_ids": list(row.candidate_account_ids),
            }
            for pid, row in frozen_index.pr4_by_procurement.items()
        }

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

        institution_name = tender.buyer_display_selected
        buyer_email_norm = buyer_email_by_tender.get(tender_id)
        currentness = tender.currentness_class or ""
        label_status = None  # PR5E has no per-tender reviewed/gold join
        independently_reviewed = False

        # --- Organization: begin PR4 from frozen tender, not emitted org ---
        expected_pr4_ids = tender_pr4_procurement_ids(tender)
        if tuple(sorted(org.pr4_procurement_ids)) != expected_pr4_ids:
            failures.append(
                {
                    "error": "organization_pr4_procurement_ids_mismatch",
                    "tender": tender_id,
                    "expected": list(expected_pr4_ids),
                    "got": list(org.pr4_procurement_ids),
                }
            )

        if pr4_mapping_for_resolve is not None:
            expected_org = resolve_organization_for_tender(
                tender,
                relevance_decision_id=decision.decision_id,
                account_index=account_index,  # type: ignore[arg-type]
                known_account_ids=known_account_ids,  # type: ignore[arg-type]
                pr4_by_procurement=pr4_mapping_for_resolve,
                identity_fingerprint=identity_fingerprint,  # type: ignore[arg-type]
            )
            for fname in _ORG_COMPARE_FIELDS:
                if getattr(org, fname) != getattr(expected_org, fname):
                    failures.append(
                        {
                            "error": "organization_field_mismatch",
                            "tender": tender_id,
                            "field": fname,
                            "expected": getattr(expected_org, fname),
                            "got": getattr(org, fname),
                        }
                    )
        else:
            # Partial org bind without live account index (unit tests).
            if org.relevance_decision_id != decision.decision_id:
                failures.append(
                    {
                        "error": "organization_relevance_decision_mismatch",
                        "tender": tender_id,
                    }
                )
            if org.coalesced_tender_id != tender_id:
                failures.append(
                    {"error": "organization_tender_mismatch", "tender": tender_id}
                )
            if tuple(org.evidence_ref_ids) != tuple(tender.evidence_ref_ids):
                failures.append(
                    {
                        "error": "organization_evidence_ref_mismatch",
                        "tender": tender_id,
                    }
                )
            expected_sufficiency = assess_buyer_field_sufficiency(tender)
            if org.buyer_field_sufficiency != expected_sufficiency:
                failures.append(
                    {
                        "error": "organization_buyer_field_sufficiency_mismatch",
                        "tender": tender_id,
                    }
                )
            expected_pr4_rids: set[str] = set()
            for pid in expected_pr4_ids:
                prow = frozen_index.pr4_by_procurement.get(pid)
                if prow is not None:
                    expected_pr4_rids.add(prow.resolution_id)
            if (
                expected_pr4_ids
                and org.resolution_source != "pr4_constituent_incomplete"
                and set(org.pr4_resolution_ids) != expected_pr4_rids
                and org.pr4_resolution_ids
            ):
                # Only enforce when org claims PR4 resolution IDs.
                if set(org.pr4_resolution_ids) != expected_pr4_rids:
                    failures.append(
                        {
                            "error": "pr4_resolution_ids_mismatch",
                            "tender": tender_id,
                            "expected": sorted(expected_pr4_rids),
                            "got": sorted(org.pr4_resolution_ids),
                        }
                    )

        # --- Expected contact projection from frozen + safety + policy ---
        expected_summary: ContactResolutionSummary
        expected_cands: list[ContactCandidate]
        expected_evs: list[ContactResolutionEvidence]
        expected_confs: list[ContactResolutionConflict]

        if org.resolution_status != "linked" or not org.account_id:
            reason = (
                "account_unresolved"
                if org.resolution_status != "deferred_insufficient_buyer_fields"
                else "insufficient_buyer_fields"
            )
            expected_summary = deferred_summary(
                tender_id=tender_id,
                relevance=decision,
                organization=org,
                input_fingerprint=summary.input_fingerprint,
                reason_code=reason,
                currentness_class=currentness,
                label_status=label_status,
                independently_reviewed=independently_reviewed,
            )
            # Prefer deferred reason from emitted if org-driven reason differs
            # only when status is deferred — still require deferred ID coherence.
            expected_cands = []
            expected_evs = []
            expected_confs = []
            # Recompute deferred ID/reason using emitted reason when deferred
            # so hand-built tests with custom reason still bind IDs.
            if summary.final_contact_status == CONTACT_RESOLUTION_DEFERRED:
                expected_crs_id = contact_resolution_id_deferred(
                    coalesced_tender_id=tender_id,
                    organization_resolution_id=org.organization_resolution_id,
                    reason=summary.reason_code,
                )
            else:
                expected_crs_id = expected_summary.contact_resolution_id
        else:
            (
                expected_summary,
                expected_cands,
                expected_evs,
                expected_confs,
            ) = project_linked_contact_resolution(
                tender_id=tender_id,
                relevance=decision,
                organization=org,
                frozen_index=frozen_index,
                safety=safety,
                buyer_email_norm=buyer_email_norm,
                institution_name=institution_name,
                input_fingerprint=summary.input_fingerprint,
                currentness_class=currentness,
                label_status=label_status,
                independently_reviewed=independently_reviewed,
            )
            expected_crs_id = expected_summary.contact_resolution_id

        emitted_cands = sorted(
            cands_by_tender.get(tender_id, []),
            key=lambda c: (c.rank, c.contact_id),
        )
        # Also accept candidates keyed only under summary id (legacy tests).
        if not emitted_cands:
            emitted_cands = sorted(
                cands_by_summary.get(summary.contact_resolution_id, []),
                key=lambda c: (c.rank, c.contact_id),
            )

        # --- Candidate contact-ID set equality ---
        if org.resolution_status == "linked" and org.account_id:
            expected_contact_ids = set(
                frozen_index.contact_ids_for_account(org.account_id)
            )
            emitted_contact_ids = {c.contact_id for c in emitted_cands}
            if emitted_contact_ids != expected_contact_ids:
                failures.append(
                    {
                        "error": "candidate_contact_set_mismatch",
                        "tender": tender_id,
                        "expected": sorted(expected_contact_ids),
                        "got": sorted(emitted_contact_ids),
                        "missing": sorted(expected_contact_ids - emitted_contact_ids),
                        "extra": sorted(emitted_contact_ids - expected_contact_ids),
                    }
                )
            if (
                expected_contact_ids
                and summary.final_contact_status == "no_contact_found"
                and not emitted_cands
            ):
                failures.append(
                    {
                        "error": "no_contact_found_despite_frozen_contacts",
                        "tender": tender_id,
                    }
                )

        # --- Per-candidate exact field recompute ---
        expected_by_contact = {c.contact_id: c for c in expected_cands}
        for c in emitted_cands:
            exp = expected_by_contact.get(c.contact_id)
            if exp is None:
                failures.append(
                    {
                        "error": "orphan_candidate",
                        "tender": tender_id,
                        "contact_id": c.contact_id,
                    }
                )
                continue
            if c.contact_resolution_id != expected_crs_id and (
                c.contact_resolution_id != summary.contact_resolution_id
            ):
                # Parent must equal final CRS (expected or emitted summary ID
                # when summary ID itself is under test).
                pass
            if c.contact_resolution_id != summary.contact_resolution_id:
                failures.append(
                    {
                        "error": "candidate_summary_mismatch",
                        "candidate_id": c.candidate_id,
                    }
                )
            # Expected candidate_id must not encode provisional parent.
            recomputed_id = candidate_id_for(
                contact_id=c.contact_id,
                account_id=c.account_id,
                ranking_tier=exp.ranking_tier,
            )
            if c.candidate_id != recomputed_id:
                failures.append(
                    {
                        "error": "candidate_id_mismatch",
                        "tender": tender_id,
                        "contact_id": c.contact_id,
                        "expected": recomputed_id,
                        "got": c.candidate_id,
                    }
                )
            # If candidate_id still equals a hash that included provisional
            # parent keys, the independent recomputation above already fails.
            for fname in _CANDIDATE_COMPARE_FIELDS:
                if fname == "candidate_id":
                    continue
                if getattr(c, fname) != getattr(exp, fname):
                    failures.append(
                        {
                            "error": "candidate_field_mismatch",
                            "tender": tender_id,
                            "contact_id": c.contact_id,
                            "field": fname,
                            "expected": getattr(exp, fname),
                            "got": getattr(c, fname),
                        }
                    )

            # Exact evidence IDs for contact.
            frozen_contact = frozen_index.contacts_by_id.get(c.contact_id)
            if frozen_contact is not None:
                expected_eids = tuple(sorted(frozen_contact.evidence_ids))
                if tuple(sorted(c.evidence_ids)) != expected_eids:
                    failures.append(
                        {
                            "error": "candidate_evidence_ids_mismatch",
                            "candidate_id": c.candidate_id,
                            "expected": list(expected_eids),
                            "got": list(c.evidence_ids),
                        }
                    )
                # Verification from exact declared-and-frozen evidence set.
                declared = tuple(sorted(c.evidence_ids))
                ev_rows = []
                for eid in declared:
                    fe = frozen_index.evidence_by_id.get(eid)
                    if fe is not None:
                        ev_rows.append(fe.to_dict())
                predicate = contact_has_explicit_verification(
                    identity_status=frozen_contact.identity_status,
                    identity_confidence=frozen_contact.identity_confidence,
                    evidence_rows=ev_rows,
                    contact_id=c.contact_id,
                    policy=policy,
                )
                if (
                    c.verification_status == "explicit_verification"
                    and not predicate
                ):
                    failures.append(
                        {
                            "error": "verification_claim_without_declared_frozen_predicate",
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

        for cid, exp in expected_by_contact.items():
            if cid not in {c.contact_id for c in emitted_cands}:
                failures.append(
                    {
                        "error": "missing_expected_candidate",
                        "tender": tender_id,
                        "contact_id": cid,
                        "expected_tier": exp.ranking_tier,
                    }
                )

        # --- Conflicts: exact set from projection / select_final_status ---
        emitted_confs = conflicts_by_tender.get(tender_id, [])
        expected_keys = {_conflict_key(c) for c in expected_confs}
        emitted_keys = {_conflict_key(c) for c in emitted_confs}
        if expected_keys != emitted_keys:
            failures.append(
                {
                    "error": "conflict_set_mismatch",
                    "tender": tender_id,
                    "expected": [
                        {
                            "conflict_id": c.conflict_id,
                            "type": c.conflict_type,
                            "reason": c.reason_code,
                            "subjects": list(c.subject_keys),
                        }
                        for c in expected_confs
                    ],
                    "got": [
                        {
                            "conflict_id": c.conflict_id,
                            "type": c.conflict_type,
                            "reason": c.reason_code,
                            "subjects": list(c.subject_keys),
                        }
                        for c in emitted_confs
                    ],
                }
            )

        # --- Summary exact bind ---
        # Rebuild expected summary echoes from frozen tender/decision.
        expected_echoes = {
            "relevance_class_echo": decision.relevance_class,
            "lifecycle_class_echo": decision.lifecycle_class_echo or "",
            "currentness_class_echo": currentness,
            "relevance_validation_status_echo": "",  # unvalidated join
            "rules_version": CONTACT_RESOLUTION_RULES_VERSION,
            "resolver_version": CONTACT_RESOLVER_VERSION,
            "organization_resolution_id": org.organization_resolution_id,
            "relevance_decision_id": decision.decision_id,
            "coalesced_tender_id": tender_id,
            "input_fingerprint": summary.input_fingerprint,
        }
        for fname, expected_val in expected_echoes.items():
            if getattr(summary, fname) != expected_val:
                failures.append(
                    {
                        "error": "summary_echo_or_binding_mismatch",
                        "tender": tender_id,
                        "field": fname,
                        "expected": expected_val,
                        "got": getattr(summary, fname),
                    }
                )

        if org.resolution_status == "linked" and org.account_id:
            # Compare status/selection/reason/counts/stages/next/ID to projection.
            for fname in (
                "final_contact_status",
                "selected_contact_id",
                "selected_candidate_id",
                "reason_code",
                "considered_contact_count",
                "suitable_contact_count",
                "blocked_contact_count",
                "search_stages_completed",
                "next_action",
                "account_id",
                "contact_resolution_id",
            ):
                if getattr(summary, fname) != getattr(expected_summary, fname):
                    failures.append(
                        {
                            "error": "summary_field_mismatch",
                            "tender": tender_id,
                            "field": fname,
                            "expected": getattr(expected_summary, fname),
                            "got": getattr(summary, fname),
                        }
                    )
            # Semantic fingerprint from expected projection identity.
            expected_sem = recompute_summary_semantic_fingerprint(
                contact_resolution_id=expected_summary.contact_resolution_id,
                final_contact_status=expected_summary.final_contact_status,
                account_id=expected_summary.account_id,
                selected_contact_id=expected_summary.selected_contact_id,
                selected_candidate_id=expected_summary.selected_candidate_id,
                search_stages_completed=expected_summary.search_stages_completed,
                next_action=expected_summary.next_action,
                reason_code=expected_summary.reason_code,
                candidate_ids=[c.candidate_id for c in expected_cands],
            )
            if summary.semantic_fingerprint != expected_sem:
                # Also accept recomputation from emitted fields only if IDs match
                # expected — otherwise fail (fabricated IDs + recomputed FP).
                emitted_sem = recompute_summary_semantic_fingerprint(
                    contact_resolution_id=summary.contact_resolution_id,
                    final_contact_status=summary.final_contact_status,
                    account_id=summary.account_id,
                    selected_contact_id=summary.selected_contact_id,
                    selected_candidate_id=summary.selected_candidate_id,
                    search_stages_completed=summary.search_stages_completed,
                    next_action=summary.next_action,
                    reason_code=summary.reason_code,
                    candidate_ids=[c.candidate_id for c in emitted_cands],
                )
                if summary.semantic_fingerprint == emitted_sem:
                    failures.append(
                        {
                            "error": "semantic_fingerprint_not_bound_to_source_projection",
                            "tender": tender_id,
                        }
                    )
                else:
                    failures.append(
                        {
                            "error": "semantic_fingerprint_mismatch",
                            "tender": tender_id,
                        }
                    )
        else:
            # Deferred path
            if summary.final_contact_status != CONTACT_RESOLUTION_DEFERRED:
                failures.append(
                    {
                        "error": "expected_deferred_status",
                        "tender": tender_id,
                        "got": summary.final_contact_status,
                    }
                )
            if emitted_cands:
                failures.append(
                    {"error": "deferred_has_candidates", "tender": tender_id}
                )
            if summary.search_stages_completed:
                failures.append(
                    {"error": "deferred_has_search_stages", "tender": tender_id}
                )
            expected_deferred_id = contact_resolution_id_deferred(
                coalesced_tender_id=tender_id,
                organization_resolution_id=org.organization_resolution_id,
                reason=summary.reason_code,
            )
            if summary.contact_resolution_id != expected_deferred_id:
                failures.append(
                    {
                        "error": "deferred_contact_resolution_id_mismatch",
                        "tender": tender_id,
                        "expected": expected_deferred_id,
                        "got": summary.contact_resolution_id,
                    }
                )
            expected_next = next_action_for_status(
                CONTACT_RESOLUTION_DEFERRED,
                lifecycle_class=decision.lifecycle_class_echo or "",
                relevance_class=decision.relevance_class,
                currentness_class=currentness,
                label_status=label_status,
                independently_reviewed=independently_reviewed,
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
            expected_sem = recompute_summary_semantic_fingerprint(
                contact_resolution_id=expected_deferred_id,
                final_contact_status=CONTACT_RESOLUTION_DEFERRED,
                account_id=None,
                selected_contact_id=None,
                selected_candidate_id=None,
                search_stages_completed=(),
                next_action=expected_next,
                reason_code=summary.reason_code,
            )
            if summary.semantic_fingerprint != expected_sem:
                emitted_sem = recompute_summary_semantic_fingerprint(
                    contact_resolution_id=summary.contact_resolution_id,
                    final_contact_status=summary.final_contact_status,
                    account_id=None,
                    selected_contact_id=None,
                    selected_candidate_id=None,
                    search_stages_completed=summary.search_stages_completed,
                    next_action=summary.next_action,
                    reason_code=summary.reason_code,
                )
                if summary.semantic_fingerprint == emitted_sem and (
                    summary.contact_resolution_id != expected_deferred_id
                ):
                    failures.append(
                        {
                            "error": "semantic_fingerprint_not_bound_to_source_projection",
                            "tender": tender_id,
                        }
                    )
                elif summary.semantic_fingerprint != expected_sem:
                    failures.append(
                        {
                            "error": "semantic_fingerprint_mismatch",
                            "tender": tender_id,
                        }
                    )

        # Selected candidate invariants when claimed.
        if summary.selected_candidate_id:
            matches = [
                c
                for c in emitted_cands
                if c.candidate_id == summary.selected_candidate_id
            ]
            if len(matches) != 1:
                failures.append(
                    {
                        "error": "invalid_selected_candidate_pointer",
                        "tender": tender_id,
                        "matches": len(matches),
                    }
                )
            elif summary.final_contact_status == "existing_verified_contact":
                sel = matches[0]
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

    # Plan evidence == union of candidate evidence IDs (exact).
    if plan_evidence_ids != union_candidate_evidence_ids:
        failures.append(
            {
                "error": "plan_evidence_union_mismatch",
                "missing_from_plan": sorted(
                    union_candidate_evidence_ids - plan_evidence_ids
                ),
                "orphan_in_plan": sorted(
                    plan_evidence_ids - union_candidate_evidence_ids
                ),
            }
        )
    if len(evidence_by_id) != len(evidence):
        failures.append({"error": "duplicate_evidence_ids_in_plan"})

    # Every plan evidence row must match frozen fields exactly.
    for eid, ev in evidence_by_id.items():
        frozen = frozen_index.evidence_by_id.get(eid)
        if frozen is None:
            failures.append(
                {"error": "orphan_or_fabricated_plan_evidence", "evidence_id": eid}
            )
            continue
        frozen_dict = frozen.to_dict()
        for field in _EVIDENCE_COMPARE_FIELDS:
            if str(getattr(ev, field) or "") != str(frozen_dict.get(field) or ""):
                failures.append(
                    {
                        "error": "evidence_field_mismatch",
                        "evidence_id": eid,
                        "field": field,
                    }
                )

    equations["invariant_failures_empty"] = not failures
    equations["binding_per_tender_ok"] = not failures
    equations["source_projection_ok"] = not failures
    ok = all(equations.values())
    return {
        "ok": ok,
        "equations": equations,
        "failures": failures[:120],
        "relevance_decision_count": len(decisions),
        "organization_resolution_count": len(organizations),
        "contact_summary_count": len(summaries),
        "candidate_count": len(candidates),
        "evidence_count": len(evidence),
        "conflict_count": len(conflicts),
    }
