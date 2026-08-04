"""Deterministic source coalescence and field-by-field precedence."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from origenlab_email_pipeline.commercial_procurement_candidate_planner.constants import (
    EVIDENCE_PLANE_ACQUISITION,
    EVIDENCE_PLANE_PR4,
    FIELD_PRECEDENCE_VERSION,
    IDENTITY_NS_MERCADO_PUBLICO,
    TIMESTAMP_PRECISION_UNRESOLVED,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    CoalescenceConflict,
    CoalescedProcurementTender,
    ProcurementEvidenceRef,
    SelectedStatus,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.normalize import (
    NormalizedTimestamp,
    coalesced_tender_id,
    evidence_acquisition_is_current,
    field_capable,
    normalize_buyer_identity,
    normalize_tender_timestamp,
    normalized_status_meaning,
    prefer_higher_precision,
    rank_score,
    stable_content_id,
    status_internally_inconsistent,
    timestamps_compatible,
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
            "Field-by-field selection; status compared by normalized meaning. "
            "Timestamps compared with precision-aware compatibility. "
            "Grouping uses (identity_namespace, canonical_tender_key)."
        ),
        "fields": {
            field: {
                "precedence_high_to_low": list(order),
                "lista_index_capable": field == "canonical_identity",
            }
            for field in ("canonical_identity", "status") + SELECTABLE_FIELDS
        },
    }


def _identity_key(ref: ProcurementEvidenceRef) -> tuple[str, str] | None:
    if not ref.canonical_tender_key or not ref.identity_namespace:
        return None
    return (ref.identity_namespace, ref.canonical_tender_key)


def _select_among_equal_rank(
    candidates: list[ProcurementEvidenceRef],
) -> ProcurementEvidenceRef:
    """At equal rank, prefer newest valid acquisition event, then evidence_ref_id."""
    return max(
        candidates,
        key=lambda r: (
            rank_score(r.source_rank_class),
            r.acquired_at_utc or "",
            r.evidence_ref_id,
        ),
    )


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
                    identity_namespace=r.identity_namespace,
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
    by_meaning: dict[str, list[ProcurementEvidenceRef]] = defaultdict(list)
    for r in authoritative:
        meaning = r.normalized_status_meaning or normalized_status_meaning(
            r.source_status_code, r.source_status_name
        )
        if meaning:
            by_meaning[meaning].append(r)
    if len(by_meaning) > 1:
        conflicts.append(
            CoalescenceConflict(
                conflict_id=stable_content_id(
                    "conflict",
                    {
                        "kind": "status_conflict",
                        "field": "status",
                        "key": refs[0].canonical_tender_key,
                        "namespace": refs[0].identity_namespace,
                        "refs": sorted(r.evidence_ref_id for r in authoritative),
                    },
                ),
                conflict_kind="status_conflict",
                canonical_tender_key=refs[0].canonical_tender_key,
                identity_namespace=refs[0].identity_namespace,
                coalesced_tender_id=None,
                evidence_ref_ids=tuple(
                    sorted(r.evidence_ref_id for r in authoritative)
                ),
                field_name="status",
                reason_codes=("status_conflict", "normalized_meaning_disagree"),
                detail={"meanings": sorted(by_meaning.keys())},
            )
        )

    # Prefer complete representation (code+name) at highest rank.
    def completeness(r: ProcurementEvidenceRef) -> int:
        return int(bool(r.source_status_code)) + int(bool(r.source_status_name))

    best = max(
        candidates,
        key=lambda r: (
            rank_score(r.source_rank_class),
            completeness(r),
            r.acquired_at_utc or "",
            r.evidence_ref_id,
        ),
    )
    meaning = best.normalized_status_meaning or normalized_status_meaning(
        best.source_status_code, best.source_status_name
    )
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


def _timestamp_for(ref: ProcurementEvidenceRef, field: str) -> NormalizedTimestamp:
    if field == "close_timestamp":
        return normalize_tender_timestamp(ref.close_timestamp_raw)
    return normalize_tender_timestamp(ref.publication_timestamp_raw)


def _select_field(
    refs: list[ProcurementEvidenceRef], field: str
) -> tuple[str | None, str | None, list[CoalescenceConflict], list[str]]:
    """Return (selected_raw, provenance_ref_id, conflicts, secondary_provenance_ids)."""
    capable = [
        r
        for r in refs
        if field_capable(r.source_rank_class, field)
    ]
    if field in {"close_timestamp", "publication_timestamp"}:
        candidates = [
            r
            for r in capable
            if (r.has_close if field == "close_timestamp" else r.has_publication)
        ]
    elif field == "buyer_display":
        candidates = [r for r in capable if r.has_buyer_display]
    elif field == "buyer_source_id":
        candidates = [r for r in capable if r.has_buyer_source_id]
    elif field == "title":
        candidates = [r for r in capable if r.has_title]
    else:
        candidates = []
    if not candidates:
        return None, None, [], []

    conflicts: list[CoalescenceConflict] = []
    secondary: list[str] = []
    authoritative = [
        r for r in candidates if r.source_rank_class != "ocds_lista_index"
    ]

    if field in {"close_timestamp", "publication_timestamp"}:
        valid: list[tuple[ProcurementEvidenceRef, NormalizedTimestamp]] = []
        for r in authoritative:
            nt = _timestamp_for(r, field)
            if nt.precision == TIMESTAMP_PRECISION_UNRESOLVED and nt.raw:
                # Unparseable → skip pairwise conflict; selection prefers valid.
                continue
            if nt.raw is None:
                continue
            valid.append((r, nt))

        # Precision-aware pairwise compatibility among valid values.
        for i in range(len(valid)):
            for j in range(i + 1, len(valid)):
                ri, nti = valid[i]
                rj, ntj = valid[j]
                compat = timestamps_compatible(nti, ntj)
                if compat is False:
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
                                    "namespace": refs[0].identity_namespace,
                                    "a": ri.evidence_ref_id,
                                    "b": rj.evidence_ref_id,
                                },
                            ),
                            conflict_kind="date_conflict",
                            canonical_tender_key=refs[0].canonical_tender_key,
                            identity_namespace=refs[0].identity_namespace,
                            coalesced_tender_id=None,
                            evidence_ref_ids=tuple(
                                sorted([ri.evidence_ref_id, rj.evidence_ref_id])
                            ),
                            field_name=field,
                            reason_codes=("date_conflict", field_reason),
                            detail={
                                "a_raw": nti.raw,
                                "b_raw": ntj.raw,
                                "a_precision": nti.precision,
                                "b_precision": ntj.precision,
                            },
                        )
                    )
        # Select highest-ranked valid timestamp; malformed higher rank cannot override.
        selectable = [
            (r, nt)
            for r, nt in (
                (r, _timestamp_for(r, field)) for r in candidates
            )
            if nt.raw and nt.precision != TIMESTAMP_PRECISION_UNRESOLVED
        ]
        if not selectable:
            return None, None, conflicts, []
        best_ref, best_nt = max(
            selectable,
            key=lambda pair: (
                rank_score(pair[0].source_rank_class),
                pair[0].acquired_at_utc or "",
                pair[0].evidence_ref_id,
            ),
        )
        # Among compatible peers, prefer higher precision representation.
        for r, nt in selectable:
            if r.evidence_ref_id == best_ref.evidence_ref_id:
                continue
            if timestamps_compatible(best_nt, nt) is True:
                preferred = prefer_higher_precision(best_nt, nt)
                if preferred is nt and preferred.precision != best_nt.precision:
                    secondary.append(best_ref.evidence_ref_id)
                    best_ref, best_nt = r, nt
                elif preferred is best_nt and nt.precision != best_nt.precision:
                    secondary.append(r.evidence_ref_id)
        raw = (
            best_ref.close_timestamp_raw
            if field == "close_timestamp"
            else best_ref.publication_timestamp_raw
        )
        return raw, best_ref.evidence_ref_id, conflicts, secondary

    if field in {"buyer_display", "buyer_source_id"}:
        # Prefer source IDs for identity; display variance alone is not conflict.
        id_refs = [r for r in authoritative if r.buyer_source_id]
        by_id: dict[str, list[ProcurementEvidenceRef]] = defaultdict(list)
        for r in id_refs:
            by_id[str(r.buyer_source_id).strip().casefold()].append(r)
        if len(by_id) > 1:
            conflicts.append(
                CoalescenceConflict(
                    conflict_id=stable_content_id(
                        "conflict",
                        {
                            "kind": "buyer_identity_conflict",
                            "field": "buyer_source_id",
                            "key": refs[0].canonical_tender_key,
                            "namespace": refs[0].identity_namespace,
                            "refs": sorted(r.evidence_ref_id for r in id_refs),
                        },
                    ),
                    conflict_kind="buyer_identity_conflict",
                    canonical_tender_key=refs[0].canonical_tender_key,
                    identity_namespace=refs[0].identity_namespace,
                    coalesced_tender_id=None,
                    evidence_ref_ids=tuple(sorted(r.evidence_ref_id for r in id_refs)),
                    field_name="buyer_source_id",
                    reason_codes=("buyer_identity_conflict", "buyer_source_id"),
                    detail={},
                )
            )
        elif not id_refs:
            norms: dict[str, list[ProcurementEvidenceRef]] = defaultdict(list)
            for r in authoritative:
                if field == "buyer_display" and r.buyer_display_raw:
                    n = normalize_buyer_identity(r.buyer_display_raw)
                    if n:
                        norms[n].append(r)
            if len(norms) > 1:
                conflicts.append(
                    CoalescenceConflict(
                        conflict_id=stable_content_id(
                            "conflict",
                            {
                                "kind": "buyer_identity_conflict",
                                "field": "buyer_display",
                                "key": refs[0].canonical_tender_key,
                                "namespace": refs[0].identity_namespace,
                                "refs": sorted(r.evidence_ref_id for r in authoritative),
                            },
                        ),
                        conflict_kind="buyer_identity_conflict",
                        canonical_tender_key=refs[0].canonical_tender_key,
                        identity_namespace=refs[0].identity_namespace,
                        coalesced_tender_id=None,
                        evidence_ref_ids=tuple(
                            sorted(r.evidence_ref_id for r in authoritative)
                        ),
                        field_name="buyer_display",
                        reason_codes=(
                            "buyer_identity_conflict",
                            "normalized_buyer_disagree",
                        ),
                        detail={},
                    )
                )

    best = _select_among_equal_rank(candidates)
    if field == "buyer_display":
        return best.buyer_display_raw, best.evidence_ref_id, conflicts, secondary
    if field == "buyer_source_id":
        return best.buyer_source_id, best.evidence_ref_id, conflicts, secondary
    if field == "title":
        return best.title_raw, best.evidence_ref_id, conflicts, secondary
    return None, None, conflicts, secondary


def _candidate_source_kind(refs: list[ProcurementEvidenceRef]) -> str:
    planes = {r.evidence_plane for r in refs}
    if planes == {EVIDENCE_PLANE_PR4}:
        return "pr4"
    if planes == {EVIDENCE_PLANE_ACQUISITION}:
        return "live_snapshot"
    return "both"


def _preserve_tender_key_kind(refs: list[ProcurementEvidenceRef]) -> str:
    """Display/provenance kind: preserve PR4 kind when present."""
    pr4 = [r for r in refs if r.evidence_plane == EVIDENCE_PLANE_PR4]
    if pr4:
        kinds = sorted({r.tender_key_kind or "" for r in pr4 if r.tender_key_kind})
        if kinds:
            return kinds[0]
    return IDENTITY_NS_MERCADO_PUBLICO


def _live_contributes_selected(
    live_refs: list[ProcurementEvidenceRef], provenance: dict[str, str]
) -> bool:
    live_ids = {r.evidence_ref_id for r in live_refs}
    return any(v in live_ids for v in provenance.values())


def _overlapping_fields_agree(refs: list[ProcurementEvidenceRef]) -> bool:
    if len(refs) < 2:
        return True
    meanings = {
        r.normalized_status_meaning
        or normalized_status_meaning(r.source_status_code, r.source_status_name)
        for r in refs
        if r.has_status
    }
    meanings.discard(None)
    if len(meanings) > 1:
        return False
    for field in ("close_timestamp", "publication_timestamp"):
        nts = [
            normalize_tender_timestamp(
                r.close_timestamp_raw
                if field == "close_timestamp"
                else r.publication_timestamp_raw
            )
            for r in refs
            if (
                r.has_close
                if field == "close_timestamp"
                else r.has_publication
            )
        ]
        for i in range(len(nts)):
            for j in range(i + 1, len(nts)):
                if timestamps_compatible(nts[i], nts[j]) is False:
                    return False
    id_vals = {
        (r.buyer_source_id or "").strip().casefold()
        for r in refs
        if r.has_buyer_source_id and r.buyer_source_id
    }
    if len(id_vals) > 1:
        return False
    return True


def _coalescence_status(
    refs: list[ProcurementEvidenceRef],
    conflicts: list[CoalescenceConflict],
    provenance: dict[str, str],
    *,
    as_of_utc: datetime | None,
    freshness_threshold_hours: int | None,
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

    # both planes — live_source_newer requires as-of-aware current acquisition.
    if as_of_utc is not None and freshness_threshold_hours is not None:
        current_live = []
        for r in live_refs:
            ok, _ = evidence_acquisition_is_current(
                acquired_at_utc=r.acquired_at_utc,
                as_of_utc=as_of_utc,
                freshness_threshold_hours=freshness_threshold_hours,
            )
            if ok:
                current_live.append(r)
        if (
            current_live
            and _live_contributes_selected(current_live, provenance)
            and not conflicts
        ):
            return (
                "live_source_newer",
                "live_timestamped_selected_field_without_contradiction",
            )
    return "exact_agreement", "planes_agree_or_complement"


def _dedupe_group(
    group: list[ProcurementEvidenceRef],
) -> list[ProcurementEvidenceRef]:
    """Deduplicate identical acquisition-instance + observation + payload."""
    by_event: dict[str, ProcurementEvidenceRef] = {}
    for ref in sorted(group, key=lambda r: r.evidence_ref_id):
        if ref.observation_id and ref.acquisition_instance_id:
            key = f"{ref.acquisition_instance_id}|{ref.observation_id}"
            prior = by_event.get(key)
            if prior is not None:
                if prior.source_payload_digest == ref.source_payload_digest:
                    continue  # exact repeated instance observation
                # Same event key with divergent payload should not happen; keep both
                # under distinct evidence_ref_ids by falling through with unique key.
                key = f"{key}|{ref.evidence_ref_id}"
            by_event[key] = ref
        elif ref.observation_id:
            key = ref.observation_id
            prior = by_event.get(key)
            if prior is not None and prior.source_payload_digest == ref.source_payload_digest:
                continue
            by_event[ref.evidence_ref_id] = ref
        else:
            by_event[ref.evidence_ref_id] = ref
    return sorted(by_event.values(), key=lambda r: r.evidence_ref_id)


def coalesce_evidence_refs(
    refs: list[ProcurementEvidenceRef],
    *,
    as_of_utc: datetime | None = None,
    freshness_threshold_hours: int | None = None,
) -> tuple[list[CoalescedProcurementTender], list[CoalescenceConflict]]:
    by_key: dict[tuple[str, str], list[ProcurementEvidenceRef]] = defaultdict(list)
    for ref in refs:
        ik = _identity_key(ref)
        if ik is None:
            continue
        by_key[ik].append(ref)

    tenders: list[CoalescedProcurementTender] = []
    all_conflicts: list[CoalescenceConflict] = []

    for namespace, key in sorted(by_key.keys()):
        group = _dedupe_group(by_key[(namespace, key)])
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

        buyer_display_variance = False
        for field in SELECTABLE_FIELDS:
            value, ref_id, field_conflicts, secondary = _select_field(group, field)
            selected[field] = value
            if ref_id:
                provenance[field] = ref_id
            if secondary:
                provenance[f"{field}_compatible_refs"] = ",".join(sorted(secondary))
            group_conflicts.extend(field_conflicts)

        # Detect buyer display variance when source IDs agree.
        id_agree = {
            (r.buyer_source_id or "").strip().casefold()
            for r in group
            if r.has_buyer_source_id and r.buyer_source_id
        }
        displays = {
            (r.buyer_display_raw or "").strip().casefold()
            for r in group
            if r.has_buyer_display and r.buyer_display_raw
        }
        if len(id_agree) == 1 and len(displays) > 1:
            buyer_display_variance = True

        uniq: dict[str, CoalescenceConflict] = {
            c.conflict_id: c for c in group_conflicts
        }
        group_conflicts = list(uniq.values())

        status, precedence_reason = _coalescence_status(
            group,
            group_conflicts,
            provenance,
            as_of_utc=as_of_utc,
            freshness_threshold_hours=freshness_threshold_hours,
        )
        tender_id = coalesced_tender_id(
            identity_namespace=namespace, canonical_tender_key=key
        )
        display_kind = _preserve_tender_key_kind(group)

        bound_conflicts = [
            CoalescenceConflict(
                conflict_id=c.conflict_id,
                conflict_kind=c.conflict_kind,
                canonical_tender_key=c.canonical_tender_key,
                identity_namespace=c.identity_namespace or namespace,
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
        inst_ids = sorted(
            {r.acquisition_instance_id for r in group if r.acquisition_instance_id}
        )
        obs_ids = sorted({r.observation_id for r in group if r.observation_id})

        tenders.append(
            CoalescedProcurementTender(
                coalesced_tender_id=tender_id,
                canonical_tender_key=key,
                identity_namespace=namespace,
                tender_key_kind=display_kind,
                candidate_source_kind=_candidate_source_kind(group),
                pr4_procurement_id=pr4_ids[0] if pr4_ids else None,
                pr4_procurement_ids=tuple(pr4_ids),
                acquisition_snapshot_ids=tuple(snap_ids),
                acquisition_instance_ids=tuple(inst_ids),
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
                buyer_display_variance=buyer_display_variance,
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
