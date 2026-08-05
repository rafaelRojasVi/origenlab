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
    resolve_contacts_for_tender,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.fingerprint import (
    all_fingerprints,
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
    load_pr4_resolutions_by_procurement,
    open_account_index,
    resolve_organization_for_tender,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.policy import (
    is_suitable_role,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.redaction import (
    assert_no_raw_pii,
    redact_summary_for_share,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.safety import (
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


def reconcile_contact_resolution(
    *,
    decisions: Sequence[TenderRelevanceDecision],
    tenders_by_id: Mapping[str, CoalescedProcurementTender],
    organizations: Sequence[OrganizationResolution],
    summaries: Sequence[ContactResolutionSummary],
    candidates: Sequence[ContactCandidate],
    evidence: Sequence[ContactResolutionEvidence],
    conflicts: Sequence[ContactResolutionConflict],
    pr4_by_procurement: Mapping[str, Mapping[str, Any]],
    frozen_evidence_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Binding reconciliation over actual frozen objects (not parallel ID lists)."""
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
        if summary.account_id != org.account_id and summary.final_contact_status != CONTACT_RESOLUTION_DEFERRED:
            # Deferred must have null account; linked search must match org account.
            if org.resolution_status == "linked":
                failures.append(
                    {
                        "error": "summary_account_mismatch_organization",
                        "tender": tender_id,
                    }
                )

        cands = cands_by_summary.get(summary.contact_resolution_id, [])
        ranks = sorted(c.rank for c in cands)
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

        if summary.final_contact_status == "ambiguous_contact":
            if len(cands) < 2:
                failures.append(
                    {
                        "error": "ambiguous_without_competing_candidates",
                        "tender": tender_id,
                    }
                )
            if not conflicts_by_tender.get(tender_id):
                failures.append(
                    {
                        "error": "ambiguous_without_conflict_row",
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
                frozen = frozen_evidence_by_id.get(eid)
                if frozen is None:
                    failures.append(
                        {
                            "error": "evidence_pointer_unresolved",
                            "evidence_id": eid,
                        }
                    )
                elif str(frozen.get("subject_id") or "") != c.contact_id:
                    failures.append(
                        {
                            "error": "frozen_evidence_subject_mismatch",
                            "evidence_id": eid,
                        }
                    )

        # PR4 resolution pointers must resolve when present.
        for rid in org.pr4_resolution_ids:
            if not any(
                str(row.get("resolution_id")) == rid
                for row in pr4_by_procurement.values()
            ):
                failures.append(
                    {
                        "error": "pr4_resolution_pointer_unresolved",
                        "resolution_id": rid,
                        "tender": tender_id,
                    }
                )
        for pid in org.pr4_procurement_ids:
            if pid not in pr4_by_procurement and org.resolution_source.startswith("pr4_"):
                # Incomplete source already typed; pointer still recorded.
                if org.resolution_source != "pr4_constituent_incomplete":
                    failures.append(
                        {
                            "error": "pr4_procurement_pointer_unresolved",
                            "procurement_id": pid,
                            "tender": tender_id,
                        }
                    )

    # Duplicate evidence ids across plan should still be unique rows.
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
        key = f"{s.lifecycle_class_echo}|{s.relevance_class_echo}|{s.final_contact_status}"
        key_counts[key] += 1
    return {
        "note": "lifecycle|relevance|contact_status",
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
    # Freeze PR5C once and inject into PR5D.
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
            pr4_by_proc = load_pr4_resolutions_by_procurement(conn)
            safety = load_safety_snapshot(conn, gmail_user=gmail_user)

            organizations: list[OrganizationResolution] = []
            summaries: list[ContactResolutionSummary] = []
            candidates: list[ContactCandidate] = []
            evidence: list[ContactResolutionEvidence] = []
            conflicts: list[ContactResolutionConflict] = []
            frozen_evidence: dict[str, dict[str, Any]] = {}

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
                )
                summaries.append(summary)
                candidates.extend(cands)
                evidence.extend(evs)
                conflicts.extend(confs)
                for ev in evs:
                    # Pointer resolution against frozen PR2 rows already loaded via search.
                    frozen_evidence[ev.evidence_id] = {
                        "evidence_id": ev.evidence_id,
                        "subject_kind": ev.subject_kind,
                        "subject_id": ev.subject_id,
                        "source_table": ev.source_table,
                        "source_record_id": ev.source_record_id,
                        "source_plane": ev.source_plane,
                        "origin_plane": ev.origin_plane,
                        "evidence_type": ev.evidence_type,
                        "evidence_at": ev.evidence_at,
                        "matching_reason_code": ev.matching_reason_code,
                        "confidence": ev.confidence,
                    }
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
    # Evidence rows are PR2-global; dedupe when multiple tenders share contacts.
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
        pr4_by_procurement=pr4_by_proc,
        frozen_evidence_by_id=frozen_evidence,
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
    "reconcile_contact_resolution",
    "write_contact_resolution_outputs",
    "FORBIDDEN_CLI_FLAGS",
]
