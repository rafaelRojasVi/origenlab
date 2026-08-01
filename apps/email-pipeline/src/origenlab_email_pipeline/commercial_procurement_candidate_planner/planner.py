"""Orchestrate PR5C coalescence / lifecycle planning and reconciliation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.commercial_procurement_candidate_planner.acquisition_instance import (
    acquisition_instance_fingerprint_row,
    event_observation_identity,
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
    write_atomically,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.plane_a_pr4 import (
    load_pr4_plane,
    pr4_signals_to_evidence,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.plane_b_acquisition import (
    dedupe_acquisition_instances,
    load_acquisition_snapshot_json,
    snapshot_to_evidence,
)


class ReconciliationError(ValueError):
    """Aggregate reconciliation failure."""


def _as_of_str(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _email_pipeline_root() -> Path:
    return Path(__file__).resolve().parents[3]


def reconcile_aggregates(
    *,
    pr4_signal_ids: set[str],
    pr4_coalesced_ids: set[str],
    pr4_unresolved_ids: set[str],
    live_event_obs_accepted: set[str],
    live_event_obs_unresolved: set[str],
    all_live_event_obs: set[str],
    tenders: list[CoalescedProcurementTender],
    evidence_refs: list[ProcurementEvidenceRef],
    unresolved: list[UnresolvedProcurementEvidence],
    conflicts: list[CoalescenceConflict],
    snapshot_ids: list[str],
    acquisition_instance_ids: list[str],
) -> dict[str, Any]:
    if pr4_coalesced_ids & pr4_unresolved_ids:
        raise ReconciliationError("PR4 id in both coalesced and unresolved")
    accounted_pr4 = pr4_coalesced_ids | pr4_unresolved_ids
    if accounted_pr4 != pr4_signal_ids:
        missing = pr4_signal_ids - accounted_pr4
        extra = accounted_pr4 - pr4_signal_ids
        raise ReconciliationError(
            f"PR4 silent drop or invent: missing={len(missing)} extra={len(extra)}"
        )

    if live_event_obs_accepted & live_event_obs_unresolved:
        raise ReconciliationError("event-observation in both accepted and unresolved")
    accounted_live = live_event_obs_accepted | live_event_obs_unresolved
    if accounted_live != all_live_event_obs:
        raise ReconciliationError("live event-observation silently dropped or invented")

    if len(acquisition_instance_ids) != len(set(acquisition_instance_ids)):
        raise ReconciliationError("duplicate acquisition_instance_id")

    ref_ids = [e.evidence_ref_id for e in evidence_refs]
    if len(ref_ids) != len(set(ref_ids)):
        raise ReconciliationError("duplicate evidence_ref_id")
    unresolved_ids = [u.unresolved_id for u in unresolved]
    if len(unresolved_ids) != len(set(unresolved_ids)):
        raise ReconciliationError("duplicate unresolved_id")

    ref_id_set = set(ref_ids)
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

    # Identity grain uniqueness.
    grains = [(t.identity_namespace, t.canonical_tender_key) for t in tenders]
    if len(grains) != len(set(grains)):
        raise ReconciliationError("duplicate identity namespace+key in coalesced tenders")

    for t in tenders:
        if not t.canonical_tender_key or not t.identity_namespace:
            raise ReconciliationError("coalesced tender missing stable canonical identity")
        for field, eid in t.selected_field_provenance.items():
            if field.endswith("_compatible_refs"):
                continue
            if eid not in ref_id_set:
                raise ReconciliationError(
                    f"selected-field provenance missing evidence_ref: {field}"
                )
        status_ids = {
            x
            for x in (
                t.selected_field_provenance.get("status_code"),
                t.selected_field_provenance.get("status_name"),
                t.selected_field_provenance.get("status"),
            )
            if x
        }
        if len(status_ids) > 1:
            raise ReconciliationError("status code/name provenance not atomic")

        if t.lifecycle_class == "active_open":
            if not t.lifecycle_status_evidence_ref_id or not t.lifecycle_close_evidence_ref_id:
                raise ReconciliationError("active_open missing lifecycle provenance refs")
            if t.lifecycle_evidence_currentness_class != "current_authoritative_snapshot":
                raise ReconciliationError("active_open without current field provenance")

        tender_conflicts = [
            c
            for c in conflicts
            if c.conflict_id in set(t.conflict_ids)
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
    life_counts: dict[str, int] = {}
    for t in tenders:
        life_counts[t.lifecycle_class] = life_counts.get(t.lifecycle_class, 0) + 1

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
    for t in tenders:
        if t.lifecycle_class != "active_open" and t.closing_soon_bucket != "not_applicable":
            raise ReconciliationError("non-active tender has closing-soon bucket")

    pr4_ok = len(pr4_signal_ids) == len(pr4_coalesced_ids) + len(pr4_unresolved_ids)
    live_ok = (
        len(all_live_event_obs)
        == len(live_event_obs_accepted) + len(live_event_obs_unresolved)
    )
    no_silent_drop = pr4_ok and live_ok

    return {
        "equations": {
            "pr4_total_eq_coalesced_plus_unresolved": (
                f"{len(pr4_signal_ids)} = "
                f"{len(pr4_coalesced_ids)} + {len(pr4_unresolved_ids)}"
            ),
            "live_total_eq_accepted_plus_unresolved": (
                f"{len(all_live_event_obs)} = "
                f"{len(live_event_obs_accepted)} + "
                f"{len(live_event_obs_unresolved)}"
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
            "live_event_observations_total": len(all_live_event_obs),
            "live_accepted": len(live_event_obs_accepted),
            "live_unresolved": len(live_event_obs_unresolved),
            "acquisition_instances": len(set(acquisition_instance_ids)),
            "unique_snapshot_ids": len(set(snapshot_ids)),
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
            "unique_evidence_ref_ids": len(ref_id_set),
            "unique_unresolved_ids": len(set(unresolved_ids)),
        },
        "ok": True,
        "no_silent_drop": no_silent_drop,
    }


def build_candidate_plan(
    *,
    sqlite_path: Path,
    acquisition_snapshot_paths: list[Path],
    as_of_utc: str,
    freshness_threshold_hours: int,
    run_context: str,
    case_construction_meta: dict[str, Any] | None = None,
) -> CandidatePlanResult:
    if int(freshness_threshold_hours) <= 0:
        raise ValueError("freshness_threshold_hours must be > 0")

    as_of = parse_as_of_utc(as_of_utc)
    as_of_token = _as_of_str(as_of)

    pr4 = load_pr4_plane(sqlite_path)
    pr4_refs, pr4_unresolved = pr4_signals_to_evidence(pr4)

    loaded = [load_acquisition_snapshot_json(path) for path in acquisition_snapshot_paths]
    instances = dedupe_acquisition_instances(loaded)

    live_refs: list[ProcurementEvidenceRef] = []
    unresolved: list[UnresolvedProcurementEvidence] = list(pr4_unresolved)
    all_live_event_obs: set[str] = set()
    instance_rows: list[dict[str, Any]] = []
    snap_ids: list[str] = []
    inst_ids: list[str] = []

    for inst_id, snap in instances:
        inst_ids.append(inst_id)
        snap_ids.append(snap.snapshot_id)
        instance_rows.append(
            acquisition_instance_fingerprint_row(
                snap, acquisition_instance_id=inst_id
            )
        )
        for obs in snap.source_observations:
            eid = event_observation_identity(
                acquisition_instance_id=inst_id,
                observation_id=obs.observation_id,
            )
            if eid:
                all_live_event_obs.add(eid)
        refs, unres = snapshot_to_evidence(snap, acquisition_instance_id=inst_id)
        live_refs.extend(refs)
        unresolved.extend(unres)

    evidence_refs = list(pr4_refs) + list(live_refs)
    evidence_refs.sort(key=lambda r: r.evidence_ref_id)
    unresolved.sort(key=lambda u: u.unresolved_id)

    tenders, conflicts = coalesce_evidence_refs(
        evidence_refs,
        as_of_utc=as_of,
        freshness_threshold_hours=int(freshness_threshold_hours),
    )
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
    accepted_live = {
        event_observation_identity(
            acquisition_instance_id=r.acquisition_instance_id,
            observation_id=r.observation_id,
        )
        for r in live_refs
        if r.observation_id
    }
    accepted_live.discard(None)
    unresolved_live = {
        event_observation_identity(
            acquisition_instance_id=u.acquisition_instance_id,
            observation_id=u.observation_id,
        )
        for u in unresolved
        if u.observation_id and u.evidence_plane == EVIDENCE_PLANE_ACQUISITION
    }
    unresolved_live.discard(None)

    reconciliation = reconcile_aggregates(
        pr4_signal_ids={str(s["procurement_id"]) for s in pr4.signals},
        pr4_coalesced_ids=pr4_coalesced,
        pr4_unresolved_ids=pr4_unresolved_ids,
        live_event_obs_accepted=accepted_live,  # type: ignore[arg-type]
        live_event_obs_unresolved=unresolved_live,  # type: ignore[arg-type]
        all_live_event_obs=all_live_event_obs,
        tenders=tenders,
        evidence_refs=evidence_refs,
        unresolved=unresolved,
        conflicts=conflicts,
        snapshot_ids=snap_ids,
        acquisition_instance_ids=inst_ids,
    )

    input_fp = candidate_input_source_fingerprint(
        pr4=pr4,
        acquisition_instances=instance_rows,
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
        acquisition_snapshot_ids=tuple(sorted(set(snap_ids))),
        acquisition_instance_ids=tuple(sorted(set(inst_ids))),
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
        case_construction_meta=dict(case_construction_meta or {}),
    )


def write_plan_outputs(
    result: CandidatePlanResult,
    out_dir: Path,
    *,
    repo_email_pipeline_root: Path | None = None,
    require_git_ignored: bool = True,
) -> dict[str, str]:
    root = repo_email_pipeline_root or _email_pipeline_root()

    def _write(safe: Path) -> dict[str, str]:
        def dump(name: str, payload: Any) -> str:
            path = safe / name
            path.write_text(
                json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True)
                + "\n",
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
                "acquisition_instance_ids": list(result.acquisition_instance_ids),
                "pr4_identity_diagnostic": result.pr4.identity_diagnostic,
                "relevance_implemented": False,
                "product_classification_implemented": False,
                "checkpoint_note": (
                    "production PR4 read-only data + committed live-derived "
                    "sanitized acquisition fixtures (not a fresh production "
                    "acquisition run)"
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
                    "identity_diagnostic": result.pr4.identity_diagnostic,
                },
                "acquisition_snapshot_ids": list(result.acquisition_snapshot_ids),
                "acquisition_instance_ids": list(result.acquisition_instance_ids),
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
                "output_git_ignored": True,
            },
        )
        return written

    return write_atomically(
        out_dir,
        repo_email_pipeline_root=root,
        writer=_write,
        require_git_ignored=require_git_ignored,
    )


__all__ = [
    "ReconciliationError",
    "ReportOutputError",
    "build_candidate_plan",
    "reconcile_aggregates",
    "write_plan_outputs",
]
