"""Deterministic source coalescence and field-by-field precedence."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from origenlab_email_pipeline.commercial_procurement_candidate_planner.constants import (
    CANONICAL_KIND_MERCADO_PUBLICO,
    EVIDENCE_PLANE_ACQUISITION,
    EVIDENCE_PLANE_PR4,
    FIELD_PRECEDENCE_VERSION,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    CoalescenceConflict,
    CoalescedProcurementTender,
    ProcurementEvidenceRef,
    SelectedStatus,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.normalize import (
    coalesced_tender_id,
    field_capable,
    rank_score,
    stable_content_id,
    status_internally_inconsistent,
)

SELECTABLE_FIELDS = (
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
            "Field-by-field selection; status code/name are atomic. "
            "Timestamps compared as normalized UTC instants. "
            "Lista-index stubs cannot override detailed tender fields."
        ),
        "fields": {
            field: {
                "precedence_high_to_low": list(order),
                "lista_index_capable": field == "canonical_identity",
            }
            for field in ("canonical_identity", "status",) + SELECTABLE_FIELDS
        },
    }


def _norm_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text.casefold() if text else None


def _normalized_timestamp_key(ref: ProcurementEvidenceRef, field: str) -> str | None:
    if field == "close_timestamp":
        return ref.close_timestamp_utc
    if field == "publication_timestamp":
        return ref.publication_timestamp_utc
    return None


def _raw_timestamp(ref: ProcurementEvidenceRef, field: str) -> str | None:
    if field == "close_timestamp":
        return ref.close_timestamp_raw
    if field == "publication_timestamp":
        return ref.publication_timestamp_raw
    return None


def _field_value(ref: ProcurementEvidenceRef, field: str) -> str | None:
    if field == "close_timestamp":
        return ref.close_timestamp_utc or ref.close_timestamp_raw
    if field == "publication_timestamp":
        return ref.publication_timestamp_utc or ref.publication_timestamp_raw
    if field == "buyer_display":
        return ref.buyer_display_raw
    if field == "buyer_source_id":
        return ref.buyer_source_id
    if field == "title":
        return ref.title_raw
    return None


def _has_field(ref: ProcurementEvidenceRef, field: str) -> bool:
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


def _select_status(
    refs: list[ProcurementEvidenceRef],
) -> tuple[SelectedStatus | None, list[CoalescenceConflict]]:
    candidates = [
        r
        for r in refs
        if field_capable(r.source_rank_class, "status") and r.has_status
    ]
    if not candidates:
        return None, []

    conflicts: list[CoalescenceConflict] = []
    for r in candidates:
        if status_internally_inconsistent(r.source_status_code, r.source_status_name):
            conflicts.append(
                CoalescenceConflict(
                    conflict_id=stable_content_id(
                        "conflict",
                        {
                            "kind": "status_conflict",
                            "field": "status_internal",
                            "ref": r.evidence_ref_id,
                        },
                    ),
                    conflict_kind="status_conflict",
                    canonical_tender_key=r.canonical_tender_key,
                    coalesced_tender_id=None,
                    evidence_ref_ids=(r.evidence_ref_id,),
                    field_name="status",
                    reason_codes=("status_code_name_inconsistent",),
                    detail={
                        "status_code": r.source_status_code,
                        "status_name": r.source_status_name,
                    },
                )
            )

    authoritative = [r for r in candidates if r.source_rank_class != "ocds_lista_index"]
    by_pair: dict[tuple[str | None, str | None], list[ProcurementEvidenceRef]] = (
        defaultdict(list)
    )
    for r in authoritative:
        by_pair[
            (
                _norm_text(r.source_status_code),
                _norm_text(r.source_status_name),
            )
        ].append(r)
    if len(by_pair) > 1:
        conflicts.append(
            CoalescenceConflict(
                conflict_id=stable_content_id(
                    "conflict",
                    {
                        "kind": "status_conflict",
                        "field": "status",
                        "key": refs[0].canonical_tender_key,
                        "refs": sorted(r.evidence_ref_id for r in authoritative),
                    },
                ),
                conflict_kind="status_conflict",
                canonical_tender_key=refs[0].canonical_tender_key,
                coalesced_tender_id=None,
                evidence_ref_ids=tuple(
                    sorted(r.evidence_ref_id for r in authoritative)
                ),
                field_name="status",
                reason_codes=("status_conflict", "atomic_status_pair"),
                detail={
                    "pairs": sorted(
                        [
                            f"{c or ''}|{n or ''}"
                            for (c, n) in by_pair.keys()
                        ]
                    )
                },
            )
        )

    best = max(
        candidates,
        key=lambda r: (rank_score(r.source_rank_class), r.evidence_ref_id),
    )
    meaning = None
    code = (best.source_status_code or "").strip()
    name = _norm_text(best.source_status_name) or ""
    if code == "8" or name == "adjudicada":
        meaning = "awarded"
    elif code in {"7", "18", "19"} or name in {"desierta", "revocada", "suspendida"}:
        meaning = "cancelled"
    elif code == "6" or name == "cerrada":
        meaning = "closed"
    elif code == "5" or name in {"publicada", "publicada."}:
        meaning = "openish"

    selected = SelectedStatus(
        status_code=best.source_status_code,
        status_name=best.source_status_name,
        status_value=best.source_status_value or best.source_status_name,
        source_status_system=best.source_status_system,
        evidence_ref_id=best.evidence_ref_id,
        normalized_lifecycle_meaning=meaning,
        source_rank_class=best.source_rank_class,
        internally_inconsistent=status_internally_inconsistent(
            best.source_status_code, best.source_status_name
        ),
    )
    return selected, conflicts


def _select_field(
    refs: list[ProcurementEvidenceRef], field: str
) -> tuple[str | None, str | None, list[CoalescenceConflict]]:
    candidates = [
        r
        for r in refs
        if field_capable(r.source_rank_class, field)
        and _has_field(r, field)
        and _field_value(r, field) is not None
    ]
    if not candidates:
        return None, None, []

    conflicts: list[CoalescenceConflict] = []
    authoritative = [
        r for r in candidates if r.source_rank_class != "ocds_lista_index"
    ]

    if field in {"close_timestamp", "publication_timestamp"}:
        # Compare normalized UTC; timezone_unresolved refs do not auto-conflict.
        by_instant: dict[str, list[ProcurementEvidenceRef]] = defaultdict(list)
        unresolved_tz: list[ProcurementEvidenceRef] = []
        for r in authoritative:
            key = _normalized_timestamp_key(r, field)
            if key is None:
                if "timezone_unresolved" in r.timestamp_parse_reasons:
                    unresolved_tz.append(r)
                continue
            by_instant[key].append(r)
        if len(by_instant) > 1:
            field_reason = (
                "close_timestamp_conflict"
                if field == "close_timestamp"
                else "publication_timestamp_conflict"
            )
            conflicts.append(
                CoalescenceConflict(
                    conflict_id=stable_content_id(
                        "conflict",
                        {
                            "kind": "date_conflict",
                            "field": field,
                            "key": refs[0].canonical_tender_key,
                            "refs": sorted(r.evidence_ref_id for r in authoritative),
                        },
                    ),
                    conflict_kind="date_conflict",
                    canonical_tender_key=refs[0].canonical_tender_key,
                    coalesced_tender_id=None,
                    evidence_ref_ids=tuple(
                        sorted(r.evidence_ref_id for r in authoritative)
                    ),
                    field_name=field,
                    reason_codes=("date_conflict", field_reason),
                    detail={"utc_instants": sorted(by_instant.keys())},
                )
            )
        for r in unresolved_tz:
            conflicts.append(
                CoalescenceConflict(
                    conflict_id=stable_content_id(
                        "conflict",
                        {
                            "kind": "date_conflict",
                            "field": field,
                            "reason": "timezone_unresolved",
                            "ref": r.evidence_ref_id,
                        },
                    ),
                    conflict_kind="date_conflict",
                    canonical_tender_key=r.canonical_tender_key,
                    coalesced_tender_id=None,
                    evidence_ref_ids=(r.evidence_ref_id,),
                    field_name=field,
                    reason_codes=("timezone_unresolved", field),
                    detail={"raw": _raw_timestamp(r, field)},
                )
            )
    else:
        by_value: dict[str, list[ProcurementEvidenceRef]] = defaultdict(list)
        for r in authoritative:
            raw = _field_value(r, field)
            assert raw is not None
            by_value[_norm_text(raw) or raw].append(r)
        if field in {"buyer_display", "buyer_source_id"} and len(by_value) > 1:
            conflicts.append(
                CoalescenceConflict(
                    conflict_id=stable_content_id(
                        "conflict",
                        {
                            "kind": "buyer_identity_conflict",
                            "field": field,
                            "key": refs[0].canonical_tender_key,
                            "refs": sorted(r.evidence_ref_id for r in authoritative),
                        },
                    ),
                    conflict_kind="buyer_identity_conflict",
                    canonical_tender_key=refs[0].canonical_tender_key,
                    coalesced_tender_id=None,
                    evidence_ref_ids=tuple(
                        sorted(r.evidence_ref_id for r in authoritative)
                    ),
                    field_name=field,
                    reason_codes=("buyer_identity_conflict", f"field_{field}"),
                    detail={},
                )
            )

    best = max(
        candidates,
        key=lambda r: (rank_score(r.source_rank_class), r.evidence_ref_id),
    )
    # Prefer raw for display provenance of timestamps.
    if field in {"close_timestamp", "publication_timestamp"}:
        return _raw_timestamp(best, field), best.evidence_ref_id, conflicts
    return _field_value(best, field), best.evidence_ref_id, conflicts


def _candidate_source_kind(refs: list[ProcurementEvidenceRef]) -> str:
    planes = {r.evidence_plane for r in refs}
    if planes == {EVIDENCE_PLANE_PR4}:
        return "pr4"
    if planes == {EVIDENCE_PLANE_ACQUISITION}:
        return "live_snapshot"
    return "both"


def _select_tender_key_kind(refs: list[ProcurementEvidenceRef]) -> str:
    """Stable kind for coalesced identity.

    Cross-source-eligible keys (exact MP CodigoExterno shape) always use
    ``mercado_publico_codigo_externo`` — including PR4-only — so PR4-only → both
    keeps the same ``coalesced_tender_id``. Non-shape PR4 verified kinds keep
    their persisted Plane A kind.
    """
    live = [r for r in refs if r.evidence_plane == EVIDENCE_PLANE_ACQUISITION]
    pr4 = [r for r in refs if r.evidence_plane == EVIDENCE_PLANE_PR4]
    if any(r.cross_source_join_eligible for r in refs):
        return CANONICAL_KIND_MERCADO_PUBLICO
    if live:
        return CANONICAL_KIND_MERCADO_PUBLICO
    if pr4:
        kinds = sorted({r.tender_key_kind or "" for r in pr4 if r.tender_key_kind})
        if kinds:
            return kinds[0]
    return CANONICAL_KIND_MERCADO_PUBLICO


def _live_contributes_selected(
    live_refs: list[ProcurementEvidenceRef], provenance: dict[str, str]
) -> bool:
    live_ids = {r.evidence_ref_id for r in live_refs}
    return any(v in live_ids for v in provenance.values())


def _valid_acquired(ref: ProcurementEvidenceRef, as_of_utc_token: str | None) -> bool:
    # as_of not available in coalesce; require parseable aware timestamp string.
    if not ref.acquired_at_utc:
        return False
    from datetime import datetime

    try:
        s = ref.acquired_at_utc
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
    except ValueError:
        return False
    return dt.tzinfo is not None


def _overlapping_fields_agree(refs: list[ProcurementEvidenceRef]) -> bool:
    """True when all overlapping normalized fields among refs agree."""
    if len(refs) < 2:
        return True
    # status pair
    status_pairs = {
        (
            _norm_text(r.source_status_code),
            _norm_text(r.source_status_name),
        )
        for r in refs
        if r.has_status
    }
    if len(status_pairs) > 1:
        return False
    for field in ("close_timestamp", "publication_timestamp"):
        instants = {
            _normalized_timestamp_key(r, field)
            for r in refs
            if _has_field(r, field) and _normalized_timestamp_key(r, field)
        }
        if len(instants) > 1:
            return False
    for field in ("buyer_display", "buyer_source_id", "title"):
        vals = {
            _norm_text(_field_value(r, field))
            for r in refs
            if _has_field(r, field) and _field_value(r, field)
        }
        if len(vals) > 1:
            return False
    return True


def _coalescence_status(
    refs: list[ProcurementEvidenceRef],
    conflicts: list[CoalescenceConflict],
    provenance: dict[str, str],
) -> tuple[str, str]:
    planes = {r.evidence_plane for r in refs}
    live_refs = [r for r in refs if r.evidence_plane == EVIDENCE_PLANE_ACQUISITION]
    kinds = {c.conflict_kind for c in conflicts}

    if "status_conflict" in kinds:
        if len(live_refs) > 1 and EVIDENCE_PLANE_PR4 not in planes:
            return "multiple_live_sources_conflict", "multiple_live_status_conflict"
        return "status_conflict", "authoritative_status_values_disagree"
    if "date_conflict" in kinds:
        if len(live_refs) > 1 and EVIDENCE_PLANE_PR4 not in planes:
            return "multiple_live_sources_conflict", "multiple_live_date_conflict"
        return "date_conflict", "authoritative_date_values_disagree"
    if "buyer_identity_conflict" in kinds:
        if len(live_refs) > 1 and EVIDENCE_PLANE_PR4 not in planes:
            return "multiple_live_sources_conflict", "multiple_live_buyer_conflict"
        return "buyer_identity_conflict", "authoritative_buyer_values_disagree"

    if planes == {EVIDENCE_PLANE_PR4}:
        return "pr4_only", "only_pr4_evidence"
    if planes == {EVIDENCE_PLANE_ACQUISITION}:
        if len(live_refs) > 1:
            if _overlapping_fields_agree(live_refs):
                return (
                    "multiple_live_sources_agree",
                    "multiple_live_overlapping_fields_agree",
                )
            return (
                "multiple_live_sources_conflict",
                "multiple_live_overlapping_fields_disagree",
            )
        return "live_only", "only_acquisition_evidence"

    # both planes
    valid_live = [r for r in live_refs if _valid_acquired(r, None)]
    if (
        valid_live
        and _live_contributes_selected(valid_live, provenance)
        and not conflicts
    ):
        return "live_source_newer", "live_timestamped_selected_field_without_contradiction"
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
        # Deduplicate identical observation_id + identical payload digest.
        deduped: dict[str, ProcurementEvidenceRef] = {}
        for ref in sorted(by_key[key], key=lambda r: r.evidence_ref_id):
            if ref.observation_id:
                prior = deduped.get(ref.observation_id)
                if prior is not None:
                    if prior.source_payload_digest == ref.source_payload_digest:
                        continue  # identical refresh evidence
                    # Divergent content should have different observation IDs
                    # under PR5B; treat as separate if IDs collide anyway.
                deduped[ref.observation_id] = ref
            else:
                deduped[ref.evidence_ref_id] = ref
        group = sorted(deduped.values(), key=lambda r: r.evidence_ref_id)

        group_conflicts: list[CoalescenceConflict] = []
        selected_status, status_conflicts = _select_status(group)
        group_conflicts.extend(status_conflicts)

        selected: dict[str, str | None] = {}
        provenance: dict[str, str] = {}
        if selected_status and selected_status.evidence_ref_id:
            selected["status_code"] = selected_status.status_code
            selected["status_name"] = selected_status.status_name
            selected["status_value"] = selected_status.status_value
            selected["source_status_system"] = selected_status.source_status_system
            provenance["status"] = selected_status.evidence_ref_id
            provenance["status_code"] = selected_status.evidence_ref_id
            provenance["status_name"] = selected_status.evidence_ref_id

        for field in SELECTABLE_FIELDS:
            value, ref_id, field_conflicts = _select_field(group, field)
            selected[field] = value
            if ref_id:
                provenance[field] = ref_id
            group_conflicts.extend(field_conflicts)

        uniq: dict[str, CoalescenceConflict] = {
            c.conflict_id: c for c in group_conflicts
        }
        group_conflicts = list(uniq.values())

        status, precedence_reason = _coalescence_status(
            group, group_conflicts, provenance
        )
        kind = _select_tender_key_kind(group)
        tender_id = coalesced_tender_id(canonical_tender_key=key, tender_key_kind=kind)

        bound_conflicts = [
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
            for c in group_conflicts
        ]

        pr4_ids = sorted(
            {r.pr4_procurement_id for r in group if r.pr4_procurement_id}
        )
        snap_ids = sorted({r.snapshot_id for r in group if r.snapshot_id})
        obs_ids = sorted({r.observation_id for r in group if r.observation_id})

        tenders.append(
            CoalescedProcurementTender(
                coalesced_tender_id=tender_id,
                canonical_tender_key=key,
                tender_key_kind=kind,
                candidate_source_kind=_candidate_source_kind(group),
                pr4_procurement_id=pr4_ids[0] if pr4_ids else None,
                pr4_procurement_ids=tuple(pr4_ids),
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
                status_value_selected=selected.get("status_value"),
                source_status_system_selected=selected.get("source_status_system"),
                buyer_display_selected=selected.get("buyer_display"),
                buyer_source_id_selected=selected.get("buyer_source_id"),
                title_selected=selected.get("title"),
                selected_field_provenance=provenance,
                lifecycle_status_evidence_ref_id=None,
                lifecycle_close_evidence_ref_id=None,
                lifecycle_publication_evidence_ref_id=None,
                lifecycle_evidence_currentness_class=None,
                lifecycle_reason_codes=(),
                evidence_ref_ids=tuple(sorted(r.evidence_ref_id for r in group)),
                conflict_ids=tuple(sorted(c.conflict_id for c in bound_conflicts)),
            )
        )
        all_conflicts.extend(bound_conflicts)

    return tenders, all_conflicts
