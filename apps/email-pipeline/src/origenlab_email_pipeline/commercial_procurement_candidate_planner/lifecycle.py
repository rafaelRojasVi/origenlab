"""Freshness, lifecycle, and closing-soon classification (no relevance)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from origenlab_email_pipeline.commercial_procurement_candidate_planner.constants import (
    ACTIVE_STATUS_CODE,
    AWARDED_STATUS_CODES,
    AWARDED_STATUS_NAMES,
    CANCELLED_STATUS_CODES,
    CANCELLED_STATUS_NAMES,
    CLOSED_STATUS_CODES,
    CLOSED_STATUS_NAMES,
    EVIDENCE_PLANE_ACQUISITION,
    EVIDENCE_PLANE_PR4,
    LIFECYCLE_POLICY_VERSION,
    PUBLICADA_STATUS_NAMES,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    CoalescedProcurementTender,
    ProcurementEvidenceRef,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.normalize import (
    closing_bucket_for_delta,
    hours_between,
    parse_acquisition_acquired_at,
    parse_tender_timestamp_raw,
)


def lifecycle_policy_document() -> dict[str, Any]:
    return {
        "version": LIFECYCLE_POLICY_VERSION,
        "timezone_policy": {
            "as_of": "timezone-aware UTC CLI --as-of-utc",
            "acquisition_timestamps": "timezone-aware AcquisitionPage.acquired_at_utc only",
            "naive_chilecompra_tender_timestamps": "America/Santiago wall time (PR5A/equipment policy)",
            "forbidden": [
                "machine_wall_clock",
                "file_mtime",
                "build_time",
                "package_creationDate_as_publication",
            ],
        },
        "active_open_requires": [
            "current_authoritative_snapshot",
            "open_or_publicada_status",
            "close_timestamp_strictly_after_as_of_utc",
        ],
        "notes": [
            "close_timestamp <= as_of_utc cannot be active_open",
            "stale open evidence must not become active_open",
            "line-item text cannot determine lifecycle",
            "PR4 workflow/review_status cannot determine ChileCompra lifecycle",
            "awarded/cancelled/deserted/revoked/suspended preserve source reason codes",
        ],
    }


def _name(value: str | None) -> str:
    return (value or "").strip().casefold()


def classify_currentness(
    *,
    tender: CoalescedProcurementTender,
    refs: list[ProcurementEvidenceRef],
    as_of_utc: datetime,
    freshness_threshold_hours: int,
) -> tuple[str, tuple[str, ...]]:
    live = [r for r in refs if r.evidence_plane == EVIDENCE_PLANE_ACQUISITION]
    pr4 = [r for r in refs if r.evidence_plane == EVIDENCE_PLANE_PR4]
    if not live:
        return "historical_pr4_only", ("pr4_only_no_acquisition_timestamp",)

    reasons: list[str] = []
    valid_ages: list[float] = []
    any_missing = False
    any_invalid = False
    for ref in live:
        dt, err = parse_acquisition_acquired_at(ref.acquired_at_utc, as_of_utc=as_of_utc)
        if err == "acquisition_timestamp_missing":
            any_missing = True
            reasons.append(f"{ref.evidence_ref_id}:acquisition_timestamp_missing")
            continue
        if err or dt is None:
            any_invalid = True
            reasons.append(f"{ref.evidence_ref_id}:acquisition_timestamp_invalid")
            continue
        valid_ages.append(hours_between(as_of_utc, dt))

    if valid_ages:
        newest_age = min(valid_ages)
        if newest_age <= float(freshness_threshold_hours):
            return "current_authoritative_snapshot", tuple(reasons) + (
                f"newest_acquisition_age_hours={newest_age:.4f}",
            )
        return "stale_authoritative_snapshot", tuple(reasons) + (
            f"newest_acquisition_age_hours={newest_age:.4f}",
            "exceeds_freshness_threshold",
        )
    if any_invalid:
        return "acquisition_timestamp_invalid", tuple(reasons)
    if any_missing:
        return "acquisition_timestamp_missing", tuple(reasons)
    if pr4:
        return "historical_pr4_only", ("live_refs_without_usable_timestamp",)
    return "acquisition_timestamp_missing", ("no_usable_acquisition_timestamp",)


def _status_lifecycle(
    *,
    status_code: str | None,
    status_name: str | None,
) -> tuple[str | None, str | None]:
    """Map structured status alone → awarded/cancelled/closed or None if open/unknown."""
    code = (status_code or "").strip()
    name = _name(status_name)
    if code in AWARDED_STATUS_CODES or name in AWARDED_STATUS_NAMES:
        return "awarded", "status_awarded"
    if code in CANCELLED_STATUS_CODES or name in CANCELLED_STATUS_NAMES:
        # Preserve exact source reason via status name/code in reason codes.
        if name == "desierta" or code == "7":
            return "cancelled", "status_desierta"
        if name == "revocada" or code == "18":
            return "cancelled", "status_revocada"
        if name == "suspendida" or code == "19":
            return "cancelled", "status_suspendida"
        return "cancelled", "status_cancelled"
    if code in CLOSED_STATUS_CODES or name in CLOSED_STATUS_NAMES:
        return "closed", "status_closed"
    return None, None


def classify_lifecycle(
    *,
    tender: CoalescedProcurementTender,
    currentness_class: str,
    as_of_utc: datetime,
    has_status_conflict: bool,
) -> tuple[str, str, tuple[str, ...]]:
    reasons: list[str] = []
    if has_status_conflict or tender.coalescence_status == "status_conflict":
        return "status_conflict", "not_applicable", ("authoritative_status_conflict",)

    mapped, map_reason = _status_lifecycle(
        status_code=tender.status_code_selected,
        status_name=tender.status_name_selected,
    )
    if mapped is not None:
        reasons.append(map_reason or mapped)
        return mapped, "not_applicable", tuple(reasons)

    close_raw = tender.close_timestamp_selected
    close_dt, close_err = parse_tender_timestamp_raw(close_raw)
    if close_err == "timezone_unresolved":
        return "status_unknown", "not_applicable", ("timezone_unresolved",)

    code = (tender.status_code_selected or "").strip()
    name = _name(tender.status_name_selected)
    is_openish = code == ACTIVE_STATUS_CODE or name in PUBLICADA_STATUS_NAMES

    if is_openish and close_dt is None:
        if close_raw:
            return "date_missing", "not_applicable", ("close_unparseable",)
        return "date_missing", "not_applicable", ("open_status_missing_close",)

    if close_dt is not None and close_dt <= as_of_utc:
        return "closed", "not_applicable", ("close_at_or_before_as_of",)

    if is_openish and close_dt is not None and close_dt > as_of_utc:
        if currentness_class != "current_authoritative_snapshot":
            reasons.append("stale_or_unverified_open_not_active")
            reasons.append(currentness_class)
            # Future close with open status but not current → not active_open.
            pub_dt, _ = parse_tender_timestamp_raw(tender.publication_timestamp_selected)
            if pub_dt is not None and pub_dt > as_of_utc:
                return "future_scheduled", "not_applicable", tuple(reasons) + (
                    "publication_after_as_of",
                )
            return "status_unknown", "not_applicable", tuple(reasons)
        bucket = closing_bucket_for_delta(close_dt - as_of_utc)
        return "active_open", bucket, ("current_open_with_future_close",)

    pub_dt, _ = parse_tender_timestamp_raw(tender.publication_timestamp_selected)
    if pub_dt is not None and pub_dt > as_of_utc:
        return "future_scheduled", "not_applicable", ("publication_after_as_of",)

    if not tender.status_code_selected and not tender.status_name_selected:
        if close_dt is None:
            return "status_unknown", "not_applicable", ("status_and_close_absent",)
        if close_dt > as_of_utc:
            return "future_scheduled", "not_applicable", ("future_close_status_unknown",)
        return "closed", "not_applicable", ("past_close_status_unknown",)

    return "status_unknown", "not_applicable", ("unmapped_status",)


def apply_lifecycle(
    tenders: list[CoalescedProcurementTender],
    *,
    refs_by_id: dict[str, ProcurementEvidenceRef],
    conflicts_by_id: dict[str, Any] | None = None,
    as_of_utc: datetime,
    freshness_threshold_hours: int,
) -> list[CoalescedProcurementTender]:
    from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
        CoalescenceConflict,
    )

    conflicts_by_id = conflicts_by_id or {}
    out: list[CoalescedProcurementTender] = []
    for tender in tenders:
        refs = [refs_by_id[i] for i in tender.evidence_ref_ids if i in refs_by_id]
        currentness, cur_reasons = classify_currentness(
            tender=tender,
            refs=refs,
            as_of_utc=as_of_utc,
            freshness_threshold_hours=freshness_threshold_hours,
        )
        conflict_kinds = {
            conflicts_by_id[cid].conflict_kind
            for cid in tender.conflict_ids
            if cid in conflicts_by_id
            and isinstance(conflicts_by_id[cid], CoalescenceConflict)
        }
        has_status_conflict = (
            tender.coalescence_status == "status_conflict"
            or "status_conflict" in conflict_kinds
        )
        life, bucket, life_reasons = classify_lifecycle(
            tender=tender,
            currentness_class=currentness,
            as_of_utc=as_of_utc,
            has_status_conflict=has_status_conflict,
        )
        out.append(
            CoalescedProcurementTender(
                coalesced_tender_id=tender.coalesced_tender_id,
                canonical_tender_key=tender.canonical_tender_key,
                tender_key_kind=tender.tender_key_kind,
                candidate_source_kind=tender.candidate_source_kind,
                pr4_procurement_id=tender.pr4_procurement_id,
                acquisition_snapshot_ids=tender.acquisition_snapshot_ids,
                acquisition_observation_ids=tender.acquisition_observation_ids,
                coalescence_status=tender.coalescence_status,
                source_precedence_reason=tender.source_precedence_reason,
                currentness_class=currentness,
                lifecycle_class=life,
                closing_soon_bucket=(
                    bucket if life == "active_open" else "not_applicable"
                ),
                publication_timestamp_selected=tender.publication_timestamp_selected,
                close_timestamp_selected=tender.close_timestamp_selected,
                status_code_selected=tender.status_code_selected,
                status_name_selected=tender.status_name_selected,
                buyer_display_selected=tender.buyer_display_selected,
                buyer_source_id_selected=tender.buyer_source_id_selected,
                title_selected=tender.title_selected,
                selected_field_provenance=dict(tender.selected_field_provenance),
                lifecycle_reason_codes=tuple(cur_reasons) + tuple(life_reasons),
                evidence_ref_ids=tender.evidence_ref_ids,
                conflict_ids=tender.conflict_ids,
            )
        )
    return out
