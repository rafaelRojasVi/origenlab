"""Deterministic candidate planner fingerprints."""

from __future__ import annotations

from typing import Iterable

from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.constants import (
    CANDIDATE_BUILD_PLAN_FP_ALGORITHM,
    CANDIDATE_INPUT_SOURCE_FP_ALGORITHM,
    CANDIDATE_SEMANTIC_DIGEST_ALGORITHM,
    COALESCENCE_POLICY_VERSION,
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
    acquisition_snapshots: Iterable[dict[str, str]],
    evidence_refs: Iterable[ProcurementEvidenceRef],
    unresolved: Iterable[UnresolvedProcurementEvidence],
) -> str:
    """Hash PR4 deps + acquisition identities + accepted/unresolved observation ids.

    Excludes build time, output path, file mtime, and row order.
    """
    accepted_ids = sorted(
        {
            r.observation_id or r.pr4_procurement_id or r.evidence_ref_id
            for r in evidence_refs
        }
    )
    unresolved_ids = sorted({u.unresolved_id for u in unresolved})
    snaps = sorted(
        acquisition_snapshots,
        key=lambda s: (s.get("snapshot_id") or "", s.get("source_fingerprint") or ""),
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
        "acquisition_snapshots": snaps,
        "accepted_source_observation_identities": accepted_ids,
        "unresolved_evidence_ids": unresolved_ids,
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
    """Order-independent hash of coalesced rows / evidence / unresolved / conflicts."""
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
