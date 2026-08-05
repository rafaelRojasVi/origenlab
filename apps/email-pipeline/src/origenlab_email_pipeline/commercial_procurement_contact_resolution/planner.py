"""PR5E contact-resolution planner — compose PR5C+PR5D+PR2, read-only."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

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
    CONTACT_RESOLUTION_PLANNER_VERSION,
    FORBIDDEN_CLI_FLAGS,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.contact_search import (
    resolve_contacts_for_tender,
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



def reconcile_contact_resolution(*args, **kwargs):
    """Re-export exhaustive source-projected reconciliation."""
    from origenlab_email_pipeline.commercial_procurement_contact_resolution.reconcile import (
        reconcile_contact_resolution as _reconcile,
    )

    return _reconcile(*args, **kwargs)


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
        account_index=account_index,
        known_account_ids=known_accounts,
        identity_fingerprint=identity_fp,
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
