"""Deterministic candidate planner fingerprints."""

from __future__ import annotations

from typing import Any, Iterable

from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.acquisition_instance import (
    event_observation_identity,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.constants import (
    CANDIDATE_BUILD_PLAN_FP_ALGORITHM,
    CANDIDATE_INPUT_SOURCE_FP_ALGORITHM,
    CANDIDATE_SEMANTIC_DIGEST_ALGORITHM,
    COALESCENCE_POLICY_VERSION,
    EVIDENCE_PLANE_ACQUISITION,
    LIFECYCLE_POLICY_VERSION,
    PLANNER_VERSION,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    CoalescenceConflict,
    CoalescedProcurementTender,
    Pr4PlaneBundle,
    ProcurementEvidenceRef,
    UnresolvedProcurementEvidence,
)


def candidate_input_source_fingerprint(
    *,
    pr4: Pr4PlaneBundle,
    acquisition_instances: Iterable[dict[str, Any]],
    evidence_refs: Iterable[ProcurementEvidenceRef],
    unresolved: Iterable[UnresolvedProcurementEvidence],
) -> str:
    """Hash PR4 deps + acquisition-instance event rows + accepted/unresolved ids.

    Includes acquired_at_utc via acquisition instance rows. Excludes build time,
    materialized_at_utc, file mtime, output path, and input order.
    """
    accepted_event_obs = sorted(
        {
            event_observation_identity(
                acquisition_instance_id=r.acquisition_instance_id,
                observation_id=r.observation_id,
            )
            or r.pr4_procurement_id
            or r.evidence_ref_id
            for r in evidence_refs
        }
    )
    unresolved_event_obs = sorted(
        {
            event_observation_identity(
                acquisition_instance_id=u.acquisition_instance_id,
                observation_id=u.observation_id,
            )
            or u.unresolved_id
            for u in unresolved
            if u.evidence_plane == EVIDENCE_PLANE_ACQUISITION or u.observation_id
        }
        | {u.unresolved_id for u in unresolved if u.evidence_plane != EVIDENCE_PLANE_ACQUISITION}
    )
    instances = sorted(
        acquisition_instances,
        key=lambda s: (
            s.get("acquisition_instance_id") or "",
            s.get("snapshot_id") or "",
        ),
    )
    payload = {
        "algorithm": CANDIDATE_INPUT_SOURCE_FP_ALGORITHM,
        "pr4": {
            "source_fingerprint": pr4.source_fingerprint,
            "build_plan_fingerprint": pr4.build_plan_fingerprint,
            "semantic_plan_digest": pr4.semantic_plan_digest,
            "identity_fingerprint": pr4.identity_fingerprint,
            "schema_version": pr4.schema_version,
            "build_contract": pr4.build_contract,
            "as_of_date": pr4.as_of_date,
            "signal_ids": sorted(str(s["procurement_id"]) for s in pr4.signals),
        },
        "acquisition_instances": instances,
        "accepted_event_observation_identities": accepted_event_obs,
        "unresolved_event_observation_identities": sorted(unresolved_event_obs),
    }
    return canonical_json_digest(payload)


def candidate_build_plan_fingerprint(
    *,
    input_source_fingerprint: str,
    as_of_utc: str,
    freshness_threshold_hours: int,
) -> str:
    payload = {
        "algorithm": CANDIDATE_BUILD_PLAN_FP_ALGORITHM,
        "input_source_fingerprint": input_source_fingerprint,
        "as_of_utc": as_of_utc,
        "freshness_threshold_hours": int(freshness_threshold_hours),
        "planner_version": PLANNER_VERSION,
        "lifecycle_policy_version": LIFECYCLE_POLICY_VERSION,
        "coalescence_policy_version": COALESCENCE_POLICY_VERSION,
    }
    return canonical_json_digest(payload)


def candidate_semantic_digest(
    *,
    tenders: Iterable[CoalescedProcurementTender],
    evidence_refs: Iterable[ProcurementEvidenceRef],
    unresolved: Iterable[UnresolvedProcurementEvidence],
    conflicts: Iterable[CoalescenceConflict],
) -> str:
    payload = {
        "algorithm": CANDIDATE_SEMANTIC_DIGEST_ALGORITHM,
        "coalesced_tenders": sorted(
            (t.to_dict() for t in tenders),
            key=lambda r: r["coalesced_tender_id"],
        ),
        "evidence_refs": sorted(
            (e.to_dict() for e in evidence_refs),
            key=lambda r: r["evidence_ref_id"],
        ),
        "unresolved_evidence": sorted(
            (u.to_dict() for u in unresolved),
            key=lambda r: r["unresolved_id"],
        ),
        "conflicts": sorted(
            (c.to_dict() for c in conflicts),
            key=lambda r: r["conflict_id"],
        ),
    }
    return canonical_json_digest(payload)
