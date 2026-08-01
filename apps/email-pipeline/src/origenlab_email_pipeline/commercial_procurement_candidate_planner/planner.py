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
    EVIDENCE_PLANE_ACQUISITION,
    EVIDENCE_PLANE_PR4,
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
from origenlab_email_pipeline.commercial_procurement_candidate_planner.output_safety import (
    ReportOutputError,
    require_reports_out_dir,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.plane_a_pr4 import (
    load_pr4_plane,
    pr4_signals_to_evidence,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.plane_b_acquisition import (
    dedupe_snapshots,
    load_acquisition_snapshot_json,
    snapshot_to_evidence,
)


class ReconciliationError(ValueError):
    """Aggregate reconciliation failure."""


def _as_of_str(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _email_pipeline_root() -> Path:
    # .../src/origenlab_email_pipeline/commercial_procurement_candidate_planner/planner.py
    return Path(__file__).resolve().parents[3]


def reconcile_aggregates(
    *,
    pr4_signal_ids: set[str],
    pr4_coalesced_ids: set[str],
    pr4_unresolved_ids: set[str],
    live_observation_ids_accepted: set[str],
    live_observation_ids_unresolved: set[str],
    all_live_observation_ids: set[str],
    tenders: list[CoalescedProcurementTender],
    evidence_refs: list[ProcurementEvidenceRef],
    unresolved: list[UnresolvedProcurementEvidence],
    conflicts: list[CoalescenceConflict],
    snapshot_ids: list[str],
) -> dict[str, Any]:
    # PR4 total = coalesced + unresolved (no silent drop).
    if pr4_coalesced_ids & pr4_unresolved_ids:
        raise ReconciliationError("PR4 id in both coalesced and unresolved")
    accounted_pr4 = pr4_coalesced_ids | pr4_unresolved_ids
    if accounted_pr4 != pr4_signal_ids:
        missing = pr4_signal_ids - accounted_pr4
        extra = accounted_pr4 - pr4_signal_ids
        raise ReconciliationError(
            f"PR4 silent drop or invent: missing={len(missing)} extra={len(extra)}"
        )

    # Live total = accepted + unresolved.
    if live_observation_ids_accepted & live_observation_ids_unresolved:
        raise ReconciliationError("observation appears in both accepted and unresolved")
    accounted_live = live_observation_ids_accepted | live_observation_ids_unresolved
    if accounted_live != all_live_observation_ids:
        raise ReconciliationError("live observation silently dropped or invented")

    # Unique snapshot IDs.
    if len(snapshot_ids) != len(set(snapshot_ids)):
        raise ReconciliationError("duplicate snapshot IDs after dedupe")

    # Unique evidence_ref_ids / unresolved IDs.
    ref_ids = [e.evidence_ref_id for e in evidence_refs]
    if len(ref_ids) != len(set(ref_ids)):
        raise ReconciliationError("duplicate evidence_ref_id")
    unresolved_ids = [u.unresolved_id for u in unresolved]
    if len(unresolved_ids) != len(set(unresolved_ids)):
        raise ReconciliationError("duplicate unresolved_id")

    ref_id_set = set(ref_ids)
    unresolved_ref_touch = {
        u.source_record_id for u in unresolved
    }  # informational only

    # Every evidence ref referenced exactly once by one coalesced tender.
    ref_owner: dict[str, str] = {}
    for t in tenders:
        for eid in t.evidence_ref_ids:
            if eid not in ref_id_set:
                raise ReconciliationError(f"missing evidence refs on tender: {eid}")
            if eid in ref_owner:
                raise ReconciliationError("evidence_ref referenced by multiple tenders")
            ref_owner[eid] = t.coalesced_tender_id
    if set(ref_owner.keys()) != ref_id_set:
        raise ReconciliationError("evidence_ref not referenced by any tender")

    # Unresolved evidence must not be referenced by a tender.
    unresolved_obs = {u.observation_id for u in unresolved if u.observation_id}
    for t in tenders:
        if set(t.acquisition_observation_ids) & unresolved_obs:
            raise ReconciliationError("unresolved observation referenced by tender")

    # Live accepted observations appear once on tenders.
    live_in_tenders: list[str] = []
    for t in tenders:
        live_in_tenders.extend(t.acquisition_observation_ids)
    if len(live_in_tenders) != len(set(live_in_tenders)):
        raise ReconciliationError("duplicate live observation across coalesced tenders")
    if set(live_in_tenders) != live_observation_ids_accepted:
        raise ReconciliationError("live observation ↔ coalesced tender membership mismatch")

    # Stable canonical identity + unique keys.
    keys = [t.canonical_tender_key for t in tenders]
    if len(keys) != len(set(keys)):
        raise ReconciliationError("duplicate canonical keys in coalesced tenders")
    for t in tenders:
        if not t.canonical_tender_key or not t.tender_key_kind:
            raise ReconciliationError("coalesced tender missing stable canonical identity")
        if not t.coalesced_tender_id:
            raise ReconciliationError("coalesced tender missing id")

    conflict_ids = {c.conflict_id for c in conflicts}
    for t in tenders:
        missing_c = set(t.conflict_ids) - conflict_ids
        if missing_c:
            raise ReconciliationError(f"missing conflicts on tender: {missing_c}")

        # Selected-field provenance IDs exist.
        for field, eid in t.selected_field_provenance.items():
            if eid not in ref_id_set:
                raise ReconciliationError(
                    f"selected-field provenance missing evidence_ref: {field}"
                )

        # Atomic status provenance.
        status_code_p = t.selected_field_provenance.get("status_code")
        status_name_p = t.selected_field_provenance.get("status_name")
        status_p = t.selected_field_provenance.get("status")
        if status_code_p or status_name_p or status_p:
            ids = {x for x in (status_code_p, status_name_p, status_p) if x}
            if len(ids) > 1:
                raise ReconciliationError("status code/name provenance not atomic")

        # active_open provenance must be current acquisition evidence.
        if t.lifecycle_class == "active_open":
            if not t.lifecycle_status_evidence_ref_id or not t.lifecycle_close_evidence_ref_id:
                raise ReconciliationError("active_open missing lifecycle provenance refs")
            sref = next(
                (e for e in evidence_refs if e.evidence_ref_id == t.lifecycle_status_evidence_ref_id),
                None,
            )
            cref = next(
                (e for e in evidence_refs if e.evidence_ref_id == t.lifecycle_close_evidence_ref_id),
                None,
            )
            if sref is None or cref is None:
                raise ReconciliationError("active_open provenance ref missing")
            if sref.evidence_plane != EVIDENCE_PLANE_ACQUISITION:
                raise ReconciliationError("active_open status provenance not acquisition")
            if cref.evidence_plane != EVIDENCE_PLANE_ACQUISITION:
                raise ReconciliationError("active_open close provenance not acquisition")
            if t.lifecycle_evidence_currentness_class != "current_authoritative_snapshot":
                raise ReconciliationError("active_open without current field provenance")

        # Close-date conflict never active_open.
        tender_conflicts = [
            conflicts_by_id
            for conflicts_by_id in (
                next((c for c in conflicts if c.conflict_id == cid), None)
                for cid in t.conflict_ids
            )
            if conflicts_by_id is not None
        ]
        if any(
            c.conflict_kind == "date_conflict"
            and (
                c.field_name == "close_timestamp"
                or "close_timestamp_conflict" in c.reason_codes
            )
            for c in tender_conflicts
        ):
            if t.lifecycle_class == "active_open":
                raise ReconciliationError("close-date-conflict tender is active_open")

        # candidate_source_kind agrees with plane membership.
        planes = {
            e.evidence_plane
            for e in evidence_refs
            if e.evidence_ref_id in set(t.evidence_ref_ids)
        }
        expected = (
            "pr4"
            if planes == {EVIDENCE_PLANE_PR4}
            else (
                "live_snapshot"
                if planes == {EVIDENCE_PLANE_ACQUISITION}
                else "both"
            )
        )
        if t.candidate_source_kind != expected:
            raise ReconciliationError("candidate_source_kind disagrees with plane membership")

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

    conflict_by_kind: dict[str, int] = {}
    conflict_by_field: dict[str, int] = {}
    for c in conflicts:
        conflict_by_kind[c.conflict_kind] = conflict_by_kind.get(c.conflict_kind, 0) + 1
        field = c.field_name or "none"
        conflict_by_field[field] = conflict_by_field.get(field, 0) + 1

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

    pr4_equation_ok = len(pr4_signal_ids) == len(pr4_coalesced_ids) + len(pr4_unresolved_ids)
    live_equation_ok = (
        len(all_live_observation_ids)
        == len(live_observation_ids_accepted) + len(live_observation_ids_unresolved)
    )
    no_silent_drop = pr4_equation_ok and live_equation_ok

    return {
        "equations": {
            "pr4_total_eq_coalesced_plus_unresolved": (
                f"{len(pr4_signal_ids)} = "
                f"{len(pr4_coalesced_ids)} + {len(pr4_unresolved_ids)}"
            ),
            "live_total_eq_accepted_plus_unresolved": (
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
            "no_silent_drop": no_silent_drop,
        },
        "counts": {
            "pr4_signals_total": len(pr4_signal_ids),
            "pr4_coalesced": len(pr4_coalesced_ids),
            "pr4_unresolved": len(pr4_unresolved_ids),
            "live_observations_total": len(all_live_observation_ids),
            "live_accepted": len(live_observation_ids_accepted),
            "live_unresolved": len(live_observation_ids_unresolved),
            "evidence_refs": len(evidence_refs),
            "coalesced_tenders": len(tenders),
            "unresolved_evidence": len(unresolved),
            "conflicts": len(conflicts),
            "conflict_by_kind": conflict_by_kind,
            "conflict_by_field": conflict_by_field,
            "candidate_source_kind": source_kind_counts,
            "lifecycle": life_counts,
            "closing_soon_active_open": closing_counts,
            "active_open": len(active),
            "unique_snapshot_ids": len(set(snapshot_ids)),
            "unique_evidence_ref_ids": len(ref_id_set),
            "unique_unresolved_ids": len(set(unresolved_ids)),
        },
        "ok": True,
        "no_silent_drop": no_silent_drop,
        "_debug_unresolved_touch": len(unresolved_ref_touch),
    }


def build_candidate_plan(
    *,
    sqlite_path: Path,
    acquisition_snapshot_paths: list[Path],
    as_of_utc: str,
    freshness_threshold_hours: int,
    run_context: str,
) -> CandidatePlanResult:
    if int(freshness_threshold_hours) <= 0:
        raise ValueError("freshness_threshold_hours must be > 0")

    as_of = parse_as_of_utc(as_of_utc)
    as_of_token = _as_of_str(as_of)

    pr4 = load_pr4_plane(sqlite_path)
    pr4_refs, pr4_unresolved = pr4_signals_to_evidence(pr4)

    loaded: list[AcquisitionSnapshot] = []
    for path in acquisition_snapshot_paths:
        loaded.append(load_acquisition_snapshot_json(path))
    snapshots = dedupe_snapshots(loaded)

    live_refs: list[ProcurementEvidenceRef] = []
    unresolved: list[UnresolvedProcurementEvidence] = list(pr4_unresolved)
    all_live_obs: set[str] = set()

    for snap in snapshots:
        for obs in snap.source_observations:
            all_live_obs.add(obs.observation_id)
        refs, unres = snapshot_to_evidence(snap)
        live_refs.extend(refs)
        unresolved.extend(unres)

    evidence_refs = list(pr4_refs) + list(live_refs)
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

    pr4_coalesced: set[str] = set()
    for t in tenders:
        pr4_coalesced.update(t.pr4_procurement_ids)
    pr4_unresolved_ids = {
        u.pr4_procurement_id for u in unresolved if u.pr4_procurement_id
    }
    accepted_live = {r.observation_id for r in live_refs if r.observation_id}
    unresolved_live = {
        u.observation_id
        for u in unresolved
        if u.observation_id and u.evidence_plane == EVIDENCE_PLANE_ACQUISITION
    }

    reconciliation = reconcile_aggregates(
        pr4_signal_ids={str(s["procurement_id"]) for s in pr4.signals},
        pr4_coalesced_ids=pr4_coalesced,
        pr4_unresolved_ids=pr4_unresolved_ids,
        live_observation_ids_accepted=accepted_live,
        live_observation_ids_unresolved=unresolved_live,
        all_live_observation_ids=all_live_obs,
        tenders=tenders,
        evidence_refs=evidence_refs,
        unresolved=unresolved,
        conflicts=conflicts,
        snapshot_ids=[s.snapshot_id for s in snapshots],
    )
    # Drop debug-only key from public reconciliation.
    reconciliation.pop("_debug_unresolved_touch", None)

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


def write_plan_outputs(
    result: CandidatePlanResult,
    out_dir: Path,
    *,
    repo_email_pipeline_root: Path | None = None,
) -> dict[str, str]:
    safe = require_reports_out_dir(
        out_dir,
        repo_email_pipeline_root=repo_email_pipeline_root or _email_pipeline_root(),
    )
    safe.mkdir(parents=True, exist_ok=True)

    def dump(name: str, payload: Any) -> str:
        path = safe / name
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
            "pr4_identity_diagnostic": result.pr4.identity_diagnostic,
            "relevance_implemented": False,
            "product_classification_implemented": False,
            "checkpoint_note": (
                "production PR4 read-only data + committed live-derived "
                "sanitized acquisition fixtures (not a fresh production acquisition run)"
            ),
        },
    )
    written["INPUT_MANIFEST.json"] = dump(
        "INPUT_MANIFEST.json",
        {
            "pr4": {
                "source_fingerprint": result.pr4.source_fingerprint,
                "build_plan_fingerprint": result.pr4.build_plan_fingerprint,
                "semantic_plan_digest": result.pr4.semantic_plan_digest,
                "readback_semantic_digest": result.pr4.readback_semantic_digest,
                "identity_fingerprint": result.pr4.identity_fingerprint,
                "schema_version": result.pr4.schema_version,
                "build_contract": result.pr4.build_contract,
                "as_of_date": result.pr4.as_of_date,
                "signal_count": len(result.pr4.signals),
                "account_resolution_count": len(result.pr4.account_resolutions),
                "evidence_count": len(result.pr4.evidence_rows),
                "conflict_count": len(result.pr4.conflict_rows),
                "identity_diagnostic": result.pr4.identity_diagnostic,
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
            "output_contained_in_reports_out": True,
        },
    )
    return written


__all__ = [
    "ReconciliationError",
    "ReportOutputError",
    "build_candidate_plan",
    "reconcile_aggregates",
    "write_plan_outputs",
]
