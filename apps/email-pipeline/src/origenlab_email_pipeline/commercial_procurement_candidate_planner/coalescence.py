"""Deterministic source coalescence and field-by-field precedence."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from origenlab_email_pipeline.commercial_procurement_candidate_planner.constants import (
    EVIDENCE_PLANE_ACQUISITION,
    EVIDENCE_PLANE_PR4,
    FIELD_PRECEDENCE_VERSION,
    TENDER_KEY_KIND_MERCADO_PUBLICO,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    CoalescenceConflict,
    CoalescedProcurementTender,
    ProcurementEvidenceRef,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.normalize import (
    field_capable,
    rank_score,
    stable_content_id,
)

SELECTABLE_FIELDS = (
    "status_code",
    "status_name",
    "close_timestamp",
    "publication_timestamp",
    "buyer_display",
    "buyer_source_id",
    "title",
)


def field_precedence_matrix() -> dict[str, Any]:
    order = [
        "ticket_detail",
        "ticket_summary",
        "ocds_release",
        "ocds_record",
        "pr4",
        "ocds_lista_index",
    ]
    return {
        "version": FIELD_PRECEDENCE_VERSION,
        "note": (
            "Field-by-field selection; no global best source. "
            "Lista-index stubs cannot override detailed tender fields. "
            "Contradictions become conflicts; newer sources do not erase them. "
            "Package creationDate is never tender publication. "
            "File mtime / build time are never acquisition provenance."
        ),
        "fields": {
            field: {
                "precedence_high_to_low": list(order),
                "lista_index_capable": field == "canonical_identity",
            }
            for field in ("canonical_identity",) + SELECTABLE_FIELDS
        },
        "constraints": [
            "completeness_and_exact_field_provenance_matter",
            "live_wins_freshness_only_when_timestamped_valid_current",
            "contradiction_preserved_as_conflict",
            "mtime_never_acquisition_provenance",
            "build_time_never_tender_time",
            "package_creationDate_not_tender_publication",
        ],
    }


def _norm_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text.casefold() if text else None


def _field_value(ref: ProcurementEvidenceRef, field: str) -> str | None:
    if field == "status_code":
        return ref.source_status_code
    if field == "status_name":
        return ref.source_status_name
    if field == "close_timestamp":
        return ref.close_timestamp_raw
    if field == "publication_timestamp":
        return ref.publication_timestamp_raw
    if field == "buyer_display":
        return ref.buyer_display_raw
    if field == "buyer_source_id":
        return ref.buyer_source_id
    if field == "title":
        return ref.title_raw
    return None


def _has_field(ref: ProcurementEvidenceRef, field: str) -> bool:
    if field in {"status_code", "status_name"}:
        return ref.has_status
    if field == "close_timestamp":
        return ref.has_close
    if field == "publication_timestamp":
        return ref.has_publication
    if field == "buyer_display":
        return ref.has_buyer_display
    if field == "buyer_source_id":
        return ref.has_buyer_source_id
    if field == "title":
        return ref.has_title
    return False


def _select_field(
    refs: list[ProcurementEvidenceRef], field: str
) -> tuple[str | None, str | None, list[CoalescenceConflict]]:
    """Select one field value; emit conflicts when authoritative values disagree."""
    candidates: list[ProcurementEvidenceRef] = []
    for ref in refs:
        if not field_capable(ref.source_rank_class, field):
            continue
        if not _has_field(ref, field):
            continue
        if _field_value(ref, field) is None:
            continue
        candidates.append(ref)
    if not candidates:
        return None, None, []

    # Group by normalized value among capable sources.
    by_value: dict[str, list[ProcurementEvidenceRef]] = defaultdict(list)
    for ref in candidates:
        raw = _field_value(ref, field)
        assert raw is not None
        by_value[_norm_text(raw) or raw].append(ref)

    conflicts: list[CoalescenceConflict] = []
    authoritative = [
        r
        for r in candidates
        if r.source_rank_class != "ocds_lista_index"
    ]
    auth_values = {
        _norm_text(_field_value(r, field)) or _field_value(r, field)
        for r in authoritative
        if _field_value(r, field) is not None
    }
    conflict_kind = None
    if field in {"status_code", "status_name"} and len(auth_values) > 1:
        conflict_kind = "status_conflict"
    elif field in {"close_timestamp", "publication_timestamp"} and len(auth_values) > 1:
        conflict_kind = "date_conflict"
    elif field in {"buyer_display", "buyer_source_id"} and len(auth_values) > 1:
        conflict_kind = "buyer_identity_conflict"

    if conflict_kind:
        conflict_id = stable_content_id(
            "conflict",
            {
                "kind": conflict_kind,
                "field": field,
                "key": refs[0].canonical_tender_key,
                "refs": sorted(r.evidence_ref_id for r in authoritative),
            },
        )
        conflicts.append(
            CoalescenceConflict(
                conflict_id=conflict_id,
                conflict_kind=conflict_kind,
                canonical_tender_key=refs[0].canonical_tender_key,
                coalesced_tender_id=None,
                evidence_ref_ids=tuple(sorted(r.evidence_ref_id for r in authoritative)),
                field_name=field,
                reason_codes=(conflict_kind, f"field_{field}"),
                detail={
                    "values": sorted(str(v) for v in auth_values if v is not None),
                },
            )
        )

    best = max(
        candidates,
        key=lambda r: (rank_score(r.source_rank_class), r.evidence_ref_id),
    )
    return _field_value(best, field), best.evidence_ref_id, conflicts


def _candidate_source_kind(refs: list[ProcurementEvidenceRef]) -> str:
    planes = {r.evidence_plane for r in refs}
    if planes == {EVIDENCE_PLANE_PR4}:
        return "pr4"
    if planes == {EVIDENCE_PLANE_ACQUISITION}:
        return "live_snapshot"
    return "both"


def _coalescence_status(
    refs: list[ProcurementEvidenceRef],
    conflicts: list[CoalescenceConflict],
) -> tuple[str, str]:
    planes = {r.evidence_plane for r in refs}
    live_refs = [r for r in refs if r.evidence_plane == EVIDENCE_PLANE_ACQUISITION]
    kinds = {c.conflict_kind for c in conflicts}

    if "status_conflict" in kinds:
        return "status_conflict", "authoritative_status_values_disagree"
    if "date_conflict" in kinds:
        return "date_conflict", "authoritative_date_values_disagree"
    if "buyer_identity_conflict" in kinds:
        return "buyer_identity_conflict", "authoritative_buyer_values_disagree"

    if planes == {EVIDENCE_PLANE_PR4}:
        return "pr4_only", "only_pr4_evidence"
    if planes == {EVIDENCE_PLANE_ACQUISITION}:
        if len(live_refs) > 1:
            # Check live agreement on status/close when present.
            return "multiple_live_sources_agree", "multiple_live_no_authoritative_conflict"
        return "live_only", "only_acquisition_evidence"

    # both planes
    if any(r.acquired_at_utc for r in live_refs):
        return "live_source_newer", "live_timestamped_without_contradiction"
    return "exact_agreement", "planes_agree_or_complement"


def coalesce_evidence_refs(
    refs: list[ProcurementEvidenceRef],
) -> tuple[list[CoalescedProcurementTender], list[CoalescenceConflict]]:
    by_key: dict[str, list[ProcurementEvidenceRef]] = defaultdict(list)
    for ref in refs:
        if not ref.canonical_tender_key:
            continue
        by_key[ref.canonical_tender_key].append(ref)

    tenders: list[CoalescedProcurementTender] = []
    all_conflicts: list[CoalescenceConflict] = []

    for key in sorted(by_key.keys()):
        group = sorted(by_key[key], key=lambda r: r.evidence_ref_id)
        # Duplicate live observation detection (same observation_id twice).
        seen_obs: dict[str, str] = {}
        group_conflicts: list[CoalescenceConflict] = []
        for ref in group:
            if ref.observation_id:
                prior = seen_obs.get(ref.observation_id)
                if prior and prior != ref.evidence_ref_id:
                    group_conflicts.append(
                        CoalescenceConflict(
                            conflict_id=stable_content_id(
                                "conflict",
                                {
                                    "kind": "duplicate_live_observation_conflict",
                                    "observation_id": ref.observation_id,
                                    "key": key,
                                },
                            ),
                            conflict_kind="duplicate_live_observation_conflict",
                            canonical_tender_key=key,
                            coalesced_tender_id=None,
                            evidence_ref_ids=(prior, ref.evidence_ref_id),
                            field_name=None,
                            reason_codes=("duplicate_live_observation_conflict",),
                            detail={},
                        )
                    )
                seen_obs[ref.observation_id] = ref.evidence_ref_id

        selected: dict[str, str | None] = {}
        provenance: dict[str, str] = {}
        for field in SELECTABLE_FIELDS:
            value, ref_id, field_conflicts = _select_field(group, field)
            selected[field] = value
            if ref_id:
                provenance[field] = ref_id
            group_conflicts.extend(field_conflicts)

        # Deduplicate conflicts by id.
        uniq: dict[str, CoalescenceConflict] = {
            c.conflict_id: c for c in group_conflicts
        }
        group_conflicts = list(uniq.values())

        status, precedence_reason = _coalescence_status(group, group_conflicts)
        # multiple live conflict when live-only and conflicts present
        live_refs = [r for r in group if r.evidence_plane == EVIDENCE_PLANE_ACQUISITION]
        if (
            status in {"status_conflict", "date_conflict", "buyer_identity_conflict"}
            and len(live_refs) > 1
            and EVIDENCE_PLANE_PR4 not in {r.evidence_plane for r in group}
        ):
            status = "multiple_live_sources_conflict"
            precedence_reason = "multiple_live_authoritative_conflict"

        pr4_ids = [r.pr4_procurement_id for r in group if r.pr4_procurement_id]
        snap_ids = sorted(
            {r.snapshot_id for r in group if r.snapshot_id}
        )
        obs_ids = sorted(
            {r.observation_id for r in group if r.observation_id}
        )
        tender_id = stable_content_id(
            "coalesced_tender",
            {
                "canonical_tender_key": key,
                "evidence_ref_ids": sorted(r.evidence_ref_id for r in group),
            },
        )
        bound_conflicts: list[CoalescenceConflict] = []
        for c in group_conflicts:
            bound_conflicts.append(
                CoalescenceConflict(
                    conflict_id=c.conflict_id,
                    conflict_kind=c.conflict_kind,
                    canonical_tender_key=c.canonical_tender_key,
                    coalesced_tender_id=tender_id,
                    evidence_ref_ids=c.evidence_ref_ids,
                    field_name=c.field_name,
                    reason_codes=c.reason_codes,
                    detail=dict(c.detail),
                )
            )

        tenders.append(
            CoalescedProcurementTender(
                coalesced_tender_id=tender_id,
                canonical_tender_key=key,
                tender_key_kind=TENDER_KEY_KIND_MERCADO_PUBLICO,
                candidate_source_kind=_candidate_source_kind(group),
                pr4_procurement_id=pr4_ids[0] if pr4_ids else None,
                acquisition_snapshot_ids=tuple(snap_ids),
                acquisition_observation_ids=tuple(obs_ids),
                coalescence_status=status,
                source_precedence_reason=precedence_reason,
                currentness_class="pending",
                lifecycle_class="pending",
                closing_soon_bucket="not_applicable",
                publication_timestamp_selected=selected.get("publication_timestamp"),
                close_timestamp_selected=selected.get("close_timestamp"),
                status_code_selected=selected.get("status_code"),
                status_name_selected=selected.get("status_name"),
                buyer_display_selected=selected.get("buyer_display"),
                buyer_source_id_selected=selected.get("buyer_source_id"),
                title_selected=selected.get("title"),
                selected_field_provenance=provenance,
                lifecycle_reason_codes=(),
                evidence_ref_ids=tuple(sorted(r.evidence_ref_id for r in group)),
                conflict_ids=tuple(sorted(c.conflict_id for c in bound_conflicts)),
            )
        )
        all_conflicts.extend(bound_conflicts)

    return tenders, all_conflicts
