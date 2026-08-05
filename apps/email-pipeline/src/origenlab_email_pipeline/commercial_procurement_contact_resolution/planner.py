"""PR5E contact-resolution planner — compose PR5C+PR5D+PR2, read-only."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.commercial_procurement.sources import (
    load_identity_fingerprint_meta,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    CandidatePlanResult,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.output_safety import (
    write_atomically,
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


class ContactReconciliationError(ValueError):
    """Raised when PR5E reconciliation equations fail."""


def _open_ro(sqlite_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def reconcile_contact_resolution(
    *,
    relevance_decision_ids: list[str],
    organization_resolution_ids: list[str],
    contact_summary_ids: list[str],
    tender_ids_from_relevance: list[str],
    tender_ids_from_org: list[str],
    tender_ids_from_contact: list[str],
    organizations: list[OrganizationResolution],
    summaries: list[ContactResolutionSummary],
    candidates: list[ContactCandidate],
) -> dict[str, Any]:
    rel_set = set(relevance_decision_ids)
    org_set = set(organization_resolution_ids)
    crs_set = set(contact_summary_ids)
    t_rel = set(tender_ids_from_relevance)
    t_org = set(tender_ids_from_org)
    t_crs = set(tender_ids_from_contact)

    equations = {
        "pr5d_decisions_eq_organization_resolutions": len(rel_set) == len(org_set)
        and len(relevance_decision_ids) == len(organization_resolution_ids),
        "organization_resolutions_eq_contact_summaries": len(org_set) == len(crs_set)
        and len(organization_resolution_ids) == len(contact_summary_ids),
        "tender_ids_align": t_rel == t_org == t_crs,
        "one_org_per_tender": len(t_org) == len(organizations),
        "one_summary_per_tender": len(t_crs) == len(summaries),
    }

    failures: list[dict[str, Any]] = []
    org_by_tender = {o.coalesced_tender_id: o for o in organizations}
    cands_by_summary: dict[str, list[ContactCandidate]] = {}
    for c in candidates:
        cands_by_summary.setdefault(c.contact_resolution_id, []).append(c)

    seen_pairs: set[tuple[str, str]] = set()
    for s in summaries:
        org = org_by_tender.get(s.coalesced_tender_id)
        if org is None:
            failures.append({"error": "summary_missing_organization", "tender": s.coalesced_tender_id})
            continue
        if s.organization_resolution_id != org.organization_resolution_id:
            failures.append(
                {
                    "error": "summary_organization_id_mismatch",
                    "tender": s.coalesced_tender_id,
                }
            )
        cands = cands_by_summary.get(s.contact_resolution_id, [])
        if s.final_contact_status == CONTACT_RESOLUTION_DEFERRED:
            if cands:
                failures.append(
                    {
                        "error": "deferred_has_candidates",
                        "tender": s.coalesced_tender_id,
                    }
                )
            if s.search_stages_completed:
                failures.append(
                    {
                        "error": "deferred_has_search_stages",
                        "tender": s.coalesced_tender_id,
                    }
                )
            if org.account_id is not None and org.resolution_status == "linked":
                failures.append(
                    {
                        "error": "deferred_despite_linked_account",
                        "tender": s.coalesced_tender_id,
                    }
                )
        else:
            if org.resolution_status != "linked" or not org.account_id:
                failures.append(
                    {
                        "error": "contact_search_without_linked_account",
                        "tender": s.coalesced_tender_id,
                    }
                )
        if s.final_contact_status in {
            "existing_verified_contact",
            "existing_contact_needs_role_review",
            "role_known_email_missing",
            "contact_blocked",
            "ambiguous_contact",
        }:
            if s.considered_contact_count < 1 and s.final_contact_status != "ambiguous_contact":
                # ambiguous can theoretically be from org-level; contact-level needs candidates
                if s.final_contact_status != "ambiguous_contact" or not cands:
                    if s.final_contact_status in {
                        "existing_verified_contact",
                        "existing_contact_needs_role_review",
                        "role_known_email_missing",
                        "contact_blocked",
                    }:
                        failures.append(
                            {
                                "error": "existing_status_without_candidates",
                                "tender": s.coalesced_tender_id,
                                "status": s.final_contact_status,
                            }
                        )
        if s.selected_contact_id:
            sel = next(
                (c for c in cands if c.contact_id == s.selected_contact_id), None
            )
            if sel is None:
                failures.append(
                    {
                        "error": "selected_contact_missing_from_candidates",
                        "tender": s.coalesced_tender_id,
                    }
                )
            elif sel.safety_blocked or not sel.selectable:
                failures.append(
                    {
                        "error": "selected_contact_not_selectable",
                        "tender": s.coalesced_tender_id,
                    }
                )
            elif sel.account_id != s.account_id:
                failures.append(
                    {
                        "error": "selected_contact_account_mismatch",
                        "tender": s.coalesced_tender_id,
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
            if s.account_id and c.account_id != s.account_id:
                failures.append(
                    {
                        "error": "candidate_account_mismatch",
                        "candidate_id": c.candidate_id,
                    }
                )

    equations["invariant_failures_empty"] = not failures
    ok = all(equations.values())
    return {
        "ok": ok,
        "equations": equations,
        "failures": failures[:50],
        "relevance_decision_count": len(relevance_decision_ids),
        "organization_resolution_count": len(organization_resolution_ids),
        "contact_summary_count": len(contact_summary_ids),
        "candidate_count": len(candidates),
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
) -> ContactResolutionPlanResult:
    """Build a deterministic read-only contact-resolution plan for every PR5D tender."""
    relevance_plan = pr5d_plan or build_product_relevance_plan(
        sqlite_path=sqlite_path,
        acquisition_snapshot_paths=acquisition_snapshot_paths,
        as_of_utc=as_of_utc,
        freshness_threshold_hours=freshness_threshold_hours,
        run_context=run_context,
        labeling_queue_size=labeling_queue_size,
        pr5c_plan=pr5c_plan,
    )
    # Access PR5C via recomposition from relevance dependency fingerprints + rebuild
    # from the same inputs when pr5c_plan not injected.
    from origenlab_email_pipeline.commercial_procurement_candidate_planner.planner import (
        build_candidate_plan,
    )

    candidate_plan = pr5c_plan or build_candidate_plan(
        sqlite_path=sqlite_path,
        acquisition_snapshot_paths=acquisition_snapshot_paths,
        as_of_utc=as_of_utc,
        freshness_threshold_hours=freshness_threshold_hours,
        run_context=run_context,
    )
    tenders_by_id = {
        t.coalesced_tender_id: t for t in candidate_plan.coalesced_tenders
    }
    decisions: tuple[TenderRelevanceDecision, ...] = relevance_plan.tender_decisions

    conn = _open_ro(sqlite_path)
    try:
        identity_meta = load_identity_fingerprint_meta(conn)
        identity_fp = str(
            identity_meta.get("identity_fingerprint")
            or identity_meta.get("fingerprint")
            or "missing_identity_fingerprint"
        )
        account_index, known_accounts = open_account_index(conn)
        pr4_by_proc = load_pr4_resolutions_by_procurement(conn)
        safety = load_safety_snapshot(conn)

        organizations: list[OrganizationResolution] = []
        summaries: list[ContactResolutionSummary] = []
        candidates: list[ContactCandidate] = []
        evidence: list[ContactResolutionEvidence] = []
        conflicts: list[ContactResolutionConflict] = []

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
    finally:
        conn.close()

    organizations_t = tuple(
        sorted(organizations, key=lambda o: o.organization_resolution_id)
    )
    summaries_t = tuple(
        sorted(summaries, key=lambda s: s.contact_resolution_id)
    )
    candidates_t = tuple(sorted(candidates, key=lambda c: c.candidate_id))
    evidence_t = tuple(sorted(evidence, key=lambda e: e.evidence_id))
    conflicts_t = tuple(sorted(conflicts, key=lambda c: c.conflict_id))

    pr5d_semantic = str(
        relevance_plan.fingerprints.get("semantic_digest")
        or relevance_plan.fingerprints.get("semantic_fingerprint")
        or ""
    )
    fps = all_fingerprints(
        pr5c_semantic_digest=candidate_plan.semantic_digest,
        pr5d_semantic_digest=pr5d_semantic,
        identity_fingerprint=identity_fp,
        organization_resolutions=organizations_t,
        summaries=summaries_t,
        candidates=candidates_t,
    )
    # Stamp final input fingerprint onto summaries (semantic ids already stable).
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
        relevance_decision_ids=[d.decision_id for d in decisions],
        organization_resolution_ids=[
            o.organization_resolution_id for o in organizations_t
        ],
        contact_summary_ids=[s.contact_resolution_id for s in summaries_final],
        tender_ids_from_relevance=[d.coalesced_tender_id for d in decisions],
        tender_ids_from_org=[o.coalesced_tender_id for o in organizations_t],
        tender_ids_from_contact=[s.coalesced_tender_id for s in summaries_final],
        organizations=list(organizations_t),
        summaries=list(summaries_final),
        candidates=list(candidates_t),
    )
    if not recon["ok"]:
        raise ContactReconciliationError(
            f"contact resolution reconciliation failed: {recon['failures'][:3]}"
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
        "pr4_procurement_ids": "carry-forward linked account only when consistent across constituents",
        "commercial_identity_contact.role": (
            "sole role suitability authority; email local-part never used"
        ),
        "verification": (
            "existing_verified_contact requires high-confidence resolved identity "
            "evidence for the contact plus suitable role, usable email, and clear safety"
        ),
        "safety": "reuses marketing_export_context + candidate_export_gate (read-only)",
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
            "pr5c_semantic_digest": candidate_plan.semantic_digest,
            "pr5d_semantic_digest": pr5d_semantic,
            "pr5d_input_fingerprint": str(
                relevance_plan.fingerprints.get("input_fingerprint") or ""
            ),
            "pr5d_rules_fingerprint": str(
                relevance_plan.fingerprints.get("rules_fingerprint") or ""
            ),
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
    "ContactReconciliationError",
    "build_contact_resolution_plan",
    "reconcile_contact_resolution",
    "write_contact_resolution_outputs",
    "FORBIDDEN_CLI_FLAGS",
]
