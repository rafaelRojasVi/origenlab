"""Orchestrate PR5C coalescence / lifecycle planning and reconciliation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.commercial_procurement_acquisition.models import (
    AcquisitionSnapshot,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.coalescence import (
    coalesce_evidence_refs,
    field_precedence_matrix,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.constants import (
    PLANNER_VERSION,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.fingerprint import (
    candidate_build_plan_fingerprint,
    candidate_input_source_fingerprint,
    candidate_semantic_digest,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.lifecycle import (
    apply_lifecycle,
    lifecycle_policy_document,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    CandidatePlanResult,
    CoalescenceConflict,
    CoalescedProcurementTender,
    ProcurementEvidenceRef,
    UnresolvedProcurementEvidence,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.normalize import (
    parse_as_of_utc,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.plane_a_pr4 import (
    load_pr4_plane,
    pr4_signals_to_evidence_refs,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.plane_b_acquisition import (
    load_acquisition_snapshot_json,
    snapshot_to_evidence,
)


class ReconciliationError(ValueError):
    """Aggregate reconciliation failure."""


def _as_of_str(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def reconcile_aggregates(
    *,
    pr4_signal_ids: set[str],
    accepted_pr4_procurement_ids: set[str],
    live_observation_ids_accepted: set[str],
    live_observation_ids_unresolved: set[str],
    all_live_observation_ids: set[str],
    tenders: list[CoalescedProcurementTender],
    evidence_refs: list[ProcurementEvidenceRef],
    unresolved: list[UnresolvedProcurementEvidence],
    conflicts: list[CoalescenceConflict],
) -> dict[str, Any]:
    # Every accepted PR4 signal in exactly one coalesced tender.
    pr4_in_tenders: list[str] = []
    for t in tenders:
        if t.pr4_procurement_id:
            pr4_in_tenders.append(t.pr4_procurement_id)
    if len(pr4_in_tenders) != len(set(pr4_in_tenders)):
        raise ReconciliationError("duplicate PR4 procurement_id across coalesced tenders")
    if set(pr4_in_tenders) != accepted_pr4_procurement_ids:
        raise ReconciliationError("PR4 signal ↔ coalesced tender membership mismatch")

    # Live accepted observations appear once.
    live_in_tenders: list[str] = []
    for t in tenders:
        live_in_tenders.extend(t.acquisition_observation_ids)
    if len(live_in_tenders) != len(set(live_in_tenders)):
        raise ReconciliationError("duplicate live observation across coalesced tenders")
    if set(live_in_tenders) != live_observation_ids_accepted:
        raise ReconciliationError("live observation ↔ coalesced tender membership mismatch")

    unresolved_obs = {
        u.observation_id for u in unresolved if u.observation_id
    }
    if unresolved_obs != live_observation_ids_unresolved:
        raise ReconciliationError("unresolved live observation set mismatch")

    if live_observation_ids_accepted & live_observation_ids_unresolved:
        raise ReconciliationError("observation appears in both accepted and unresolved")

    accounted = live_observation_ids_accepted | live_observation_ids_unresolved
    if accounted != all_live_observation_ids:
        raise ReconciliationError("live observation silently dropped or invented")

    # Duplicate canonical keys must not produce duplicate coalesced rows.
    keys = [t.canonical_tender_key for t in tenders]
    if len(keys) != len(set(keys)):
        raise ReconciliationError("duplicate canonical keys in coalesced tenders")

    # Evidence references exist.
    ref_ids = {e.evidence_ref_id for e in evidence_refs}
    for t in tenders:
        missing = set(t.evidence_ref_ids) - ref_ids
        if missing:
            raise ReconciliationError(f"missing evidence refs on tender: {missing}")

    conflict_ids = {c.conflict_id for c in conflicts}
    for t in tenders:
        missing_c = set(t.conflict_ids) - conflict_ids
        if missing_c:
            raise ReconciliationError(f"missing conflicts on tender: {missing_c}")
    for c in conflicts:
        if c.coalesced_tender_id and c.coalesced_tender_id not in {
            t.coalesced_tender_id for t in tenders
        }:
            raise ReconciliationError("conflict subject tender missing")

    source_kind_counts = {
        "pr4": sum(1 for t in tenders if t.candidate_source_kind == "pr4"),
        "live_snapshot": sum(
            1 for t in tenders if t.candidate_source_kind == "live_snapshot"
        ),
        "both": sum(1 for t in tenders if t.candidate_source_kind == "both"),
    }
    if sum(source_kind_counts.values()) != len(tenders):
        raise ReconciliationError("candidate_source_kind counts do not sum")

    life_counts: dict[str, int] = {}
    for t in tenders:
        life_counts[t.lifecycle_class] = life_counts.get(t.lifecycle_class, 0) + 1
    if sum(life_counts.values()) != len(tenders):
        raise ReconciliationError("lifecycle counts do not sum")

    active = [t for t in tenders if t.lifecycle_class == "active_open"]
    closing_counts: dict[str, int] = {}
    for t in active:
        closing_counts[t.closing_soon_bucket] = (
            closing_counts.get(t.closing_soon_bucket, 0) + 1
        )
    if sum(closing_counts.values()) != len(active):
        raise ReconciliationError("closing-soon counts do not reconcile to active_open")
    for t in tenders:
        if t.lifecycle_class != "active_open" and t.closing_soon_bucket != "not_applicable":
            raise ReconciliationError("non-active tender has closing-soon bucket")

    return {
        "equations": {
            "accepted_pr4_signals_eq_coalesced_with_pr4": (
                f"{len(accepted_pr4_procurement_ids)} = {len(pr4_in_tenders)}"
            ),
            "accepted_live_obs_eq_coalesced_obs": (
                f"{len(live_observation_ids_accepted)} = {len(live_in_tenders)}"
            ),
            "rejected_live_obs_eq_unresolved": (
                f"{len(live_observation_ids_unresolved)} = {len(unresolved_obs)}"
            ),
            "no_silent_drop": (
                f"{len(all_live_observation_ids)} = "
                f"{len(live_observation_ids_accepted)} + "
                f"{len(live_observation_ids_unresolved)}"
            ),
            "lifecycle_sum": f"{sum(life_counts.values())} = {len(tenders)}",
            "closing_soon_sum_active": (
                f"{sum(closing_counts.values())} = {len(active)}"
            ),
            "source_kind_sum": (
                f"{sum(source_kind_counts.values())} = {len(tenders)}"
            ),
        },
        "counts": {
            "pr4_signals_total": len(pr4_signal_ids),
            "pr4_signals_accepted": len(accepted_pr4_procurement_ids),
            "evidence_refs": len(evidence_refs),
            "coalesced_tenders": len(tenders),
            "unresolved_evidence": len(unresolved),
            "conflicts": len(conflicts),
            "candidate_source_kind": source_kind_counts,
            "lifecycle": life_counts,
            "closing_soon_active_open": closing_counts,
            "active_open": len(active),
        },
        "ok": True,
    }


def build_candidate_plan(
    *,
    sqlite_path: Path,
    acquisition_snapshot_paths: list[Path],
    as_of_utc: str,
    freshness_threshold_hours: int,
    run_context: str,
) -> CandidatePlanResult:
    as_of = parse_as_of_utc(as_of_utc)
    as_of_token = _as_of_str(as_of)

    pr4 = load_pr4_plane(sqlite_path)
    pr4_refs, _skipped = pr4_signals_to_evidence_refs(pr4)

    snapshots: list[AcquisitionSnapshot] = []
    live_refs: list[ProcurementEvidenceRef] = []
    unresolved: list[UnresolvedProcurementEvidence] = []
    all_live_obs: set[str] = set()

    for path in acquisition_snapshot_paths:
        snap = load_acquisition_snapshot_json(path)
        snapshots.append(snap)
        for obs in snap.source_observations:
            all_live_obs.add(obs.observation_id)
        refs, unres = snapshot_to_evidence(snap)
        live_refs.extend(refs)
        unresolved.extend(unres)

    evidence_refs = list(pr4_refs) + list(live_refs)
    # Stable order for determinism.
    evidence_refs.sort(key=lambda r: r.evidence_ref_id)
    unresolved.sort(key=lambda u: u.unresolved_id)

    tenders, conflicts = coalesce_evidence_refs(evidence_refs)
    refs_by_id = {r.evidence_ref_id: r for r in evidence_refs}
    conflicts_by_id = {c.conflict_id: c for c in conflicts}
    tenders = apply_lifecycle(
        tenders,
        refs_by_id=refs_by_id,
        conflicts_by_id=conflicts_by_id,
        as_of_utc=as_of,
        freshness_threshold_hours=freshness_threshold_hours,
    )
    tenders.sort(key=lambda t: t.coalesced_tender_id)
    conflicts.sort(key=lambda c: c.conflict_id)

    accepted_pr4 = {r.pr4_procurement_id for r in pr4_refs if r.pr4_procurement_id}
    accepted_live = {
        r.observation_id for r in live_refs if r.observation_id
    }
    unresolved_live = {u.observation_id for u in unresolved if u.observation_id}

    reconciliation = reconcile_aggregates(
        pr4_signal_ids={str(s["procurement_id"]) for s in pr4.signals},
        accepted_pr4_procurement_ids=accepted_pr4,
        live_observation_ids_accepted=accepted_live,
        live_observation_ids_unresolved=unresolved_live,
        all_live_observation_ids=all_live_obs,
        tenders=tenders,
        evidence_refs=evidence_refs,
        unresolved=unresolved,
        conflicts=conflicts,
    )

    snap_fp_rows = [
        {
            "snapshot_id": s.snapshot_id,
            "source_fingerprint": s.source_fingerprint,
            "normalized_semantic_digest": s.normalized_semantic_digest,
            "completeness_status": s.completeness_status,
        }
        for s in snapshots
    ]
    input_fp = candidate_input_source_fingerprint(
        pr4=pr4,
        acquisition_snapshots=snap_fp_rows,
        evidence_refs=evidence_refs,
        unresolved=unresolved,
    )
    build_fp = candidate_build_plan_fingerprint(
        input_source_fingerprint=input_fp,
        as_of_utc=as_of_token,
        freshness_threshold_hours=freshness_threshold_hours,
    )
    semantic = candidate_semantic_digest(
        tenders=tenders,
        evidence_refs=evidence_refs,
        unresolved=unresolved,
        conflicts=conflicts,
    )

    return CandidatePlanResult(
        evidence_refs=tuple(evidence_refs),
        coalesced_tenders=tuple(tenders),
        unresolved=tuple(unresolved),
        conflicts=tuple(conflicts),
        pr4=pr4,
        acquisition_snapshot_ids=tuple(s.snapshot_id for s in snapshots),
        as_of_utc=as_of_token,
        freshness_threshold_hours=int(freshness_threshold_hours),
        input_source_fingerprint=input_fp,
        build_plan_fingerprint=build_fp,
        semantic_digest=semantic,
        aggregate_reconciliation=reconciliation,
        field_precedence_matrix=field_precedence_matrix(),
        lifecycle_policy=lifecycle_policy_document(),
        run_context=run_context,
        planner_version=PLANNER_VERSION,
    )


def write_plan_outputs(result: CandidatePlanResult, out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)

    def dump(name: str, payload: Any) -> str:
        path = out_dir / name
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        return str(path)

    written: dict[str, str] = {}
    written["RUN_MANIFEST.json"] = dump(
        "RUN_MANIFEST.json",
        {
            "planner_version": result.planner_version,
            "run_context": result.run_context,
            "as_of_utc": result.as_of_utc,
            "freshness_threshold_hours": result.freshness_threshold_hours,
            "input_source_fingerprint": result.input_source_fingerprint,
            "build_plan_fingerprint": result.build_plan_fingerprint,
            "semantic_digest": result.semantic_digest,
            "acquisition_snapshot_ids": list(result.acquisition_snapshot_ids),
            "relevance_implemented": False,
            "product_classification_implemented": False,
        },
    )
    written["INPUT_MANIFEST.json"] = dump(
        "INPUT_MANIFEST.json",
        {
            "pr4": {
                "source_fingerprint": result.pr4.source_fingerprint,
                "build_plan_fingerprint": result.pr4.build_plan_fingerprint,
                "semantic_plan_digest": result.pr4.semantic_plan_digest,
                "identity_fingerprint": result.pr4.identity_fingerprint,
                "schema_version": result.pr4.schema_version,
                "build_contract": result.pr4.build_contract,
                "as_of_date": result.pr4.as_of_date,
                "signal_count": len(result.pr4.signals),
                "account_resolution_count": len(result.pr4.account_resolutions),
                "evidence_count": len(result.pr4.evidence_rows),
                "conflict_count": len(result.pr4.conflict_rows),
            },
            "acquisition_snapshot_ids": list(result.acquisition_snapshot_ids),
        },
    )
    written["COALESCED_TENDERS.json"] = dump(
        "COALESCED_TENDERS.json",
        [t.to_dict() for t in result.coalesced_tenders],
    )
    written["EVIDENCE_REFS.json"] = dump(
        "EVIDENCE_REFS.json",
        [e.to_dict() for e in result.evidence_refs],
    )
    written["UNRESOLVED_EVIDENCE.json"] = dump(
        "UNRESOLVED_EVIDENCE.json",
        [u.to_dict() for u in result.unresolved],
    )
    written["CONFLICTS.json"] = dump(
        "CONFLICTS.json",
        [c.to_dict() for c in result.conflicts],
    )
    written["AGGREGATE_RECONCILIATION.json"] = dump(
        "AGGREGATE_RECONCILIATION.json",
        result.aggregate_reconciliation,
    )
    written["FIELD_PRECEDENCE_MATRIX.json"] = dump(
        "FIELD_PRECEDENCE_MATRIX.json",
        result.field_precedence_matrix,
    )
    written["LIFECYCLE_POLICY.json"] = dump(
        "LIFECYCLE_POLICY.json",
        result.lifecycle_policy,
    )
    written["NO_MUTATION_PROOF.json"] = dump(
        "NO_MUTATION_PROOF.json",
        {
            "sqlite_mode": "ro",
            "pragma_query_only": True,
            "ddl": False,
            "dml": False,
            "network": False,
            "ticket_api": False,
            "ocds_acquisition": False,
            "gmail": False,
            "postgres": False,
            "outreach": False,
            "schedule": False,
            "relevance": False,
            "apply": False,
        },
    )
    return written
