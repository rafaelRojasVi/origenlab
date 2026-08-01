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
    CoalescenceConflict,
    CoalescedProcurementTender,
    ProcurementEvidenceRef,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.normalize import (
    closing_bucket_for_delta,
    evidence_acquisition_is_current,
    field_capable,
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
            "selected_status_from_current_acquisition_evidence_ref",
            "selected_close_from_current_acquisition_evidence_ref",
            "both_refs_source_capable_for_their_fields",
            "no_unresolved_status_conflict",
            "no_unresolved_close_date_conflict",
            "open_or_publicada_status",
            "close_timestamp_strictly_after_as_of_utc",
        ],
        "notes": [
            "close_timestamp <= as_of_utc cannot be active_open",
            "lista-index stubs cannot freshen PR4 status/date fields",
            "buyer/title-only live evidence cannot freshen lifecycle",
            "status and close may come from different live refs when both current",
            "terminal PR4 historical statuses remain awarded/closed/cancelled without fresh live",
            "line-item text cannot determine lifecycle",
            "PR4 workflow/review_status cannot determine ChileCompra lifecycle",
            "buyer conflict does not by itself alter lifecycle",
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
    """Tender-level currentness (informational). Active_open uses field provenance."""
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
    from origenlab_email_pipeline.commercial_procurement_candidate_planner.normalize import (
        status_name_matches_expected,
    )

    code = (status_code or "").strip()
    if code in AWARDED_STATUS_CODES or status_name_matches_expected(
        status_name, AWARDED_STATUS_NAMES
    ):
        return "awarded", "status_awarded"
    if code in CANCELLED_STATUS_CODES or status_name_matches_expected(
        status_name, CANCELLED_STATUS_NAMES
    ):
        if status_name_matches_expected(status_name, frozenset({"desierta"})) or code == "7":
            return "cancelled", "status_desierta"
        if status_name_matches_expected(status_name, frozenset({"revocada"})) or code == "18":
            return "cancelled", "status_revocada"
        if status_name_matches_expected(status_name, frozenset({"suspendida"})) or code == "19":
            return "cancelled", "status_suspendida"
        return "cancelled", "status_cancelled"
    if code in CLOSED_STATUS_CODES or status_name_matches_expected(
        status_name, CLOSED_STATUS_NAMES
    ):
        return "closed", "status_closed"
    return None, None


def _ref_current_and_capable(
    *,
    ref: ProcurementEvidenceRef | None,
    field_name: str,
    as_of_utc: datetime,
    freshness_threshold_hours: int,
) -> tuple[bool, str]:
    if ref is None:
        return False, "lifecycle_evidence_ref_missing"
    if ref.evidence_plane != EVIDENCE_PLANE_ACQUISITION:
        return False, "lifecycle_evidence_not_acquisition"
    if not field_capable(ref.source_rank_class, field_name):
        return False, f"lifecycle_evidence_not_capable_{field_name}"
    ok, cls = evidence_acquisition_is_current(
        acquired_at_utc=ref.acquired_at_utc,
        as_of_utc=as_of_utc,
        freshness_threshold_hours=freshness_threshold_hours,
    )
    if not ok:
        return False, cls
    return True, cls


def classify_lifecycle(
    *,
    tender: CoalescedProcurementTender,
    currentness_class: str,
    as_of_utc: datetime,
    has_status_conflict: bool,
    has_close_date_conflict: bool = False,
    has_publication_date_conflict: bool = False,
    status_ref: ProcurementEvidenceRef | None = None,
    close_ref: ProcurementEvidenceRef | None = None,
    publication_ref: ProcurementEvidenceRef | None = None,
    freshness_threshold_hours: int = 48,
) -> tuple[str, str, tuple[str, ...], str | None]:
    """Return (lifecycle_class, closing_bucket, reasons, lifecycle_evidence_currentness)."""
    reasons: list[str] = []
    if has_status_conflict or tender.coalescence_status == "status_conflict":
        return (
            "status_conflict",
            "not_applicable",
            ("authoritative_status_conflict",),
            None,
        )

    if has_close_date_conflict:
        return (
            "status_unknown",
            "not_applicable",
            ("authoritative_close_date_conflict",),
            None,
        )

    mapped, map_reason = _status_lifecycle(
        status_code=tender.status_code_selected,
        status_name=tender.status_name_selected,
    )
    if mapped is not None:
        reasons.append(map_reason or mapped)
        if currentness_class == "historical_pr4_only":
            reasons.append("historical_evidence_not_currently_verified")
        return mapped, "not_applicable", tuple(reasons), None

    close_raw = tender.close_timestamp_selected
    close_dt, close_err = parse_tender_timestamp_raw(close_raw)
    if close_err == "timezone_unresolved":
        return "status_unknown", "not_applicable", ("timezone_unresolved",), None

    code = (tender.status_code_selected or "").strip()
    from origenlab_email_pipeline.commercial_procurement_candidate_planner.normalize import (
        status_name_matches_expected,
    )

    is_openish = code == ACTIVE_STATUS_CODE or status_name_matches_expected(
        tender.status_name_selected, PUBLICADA_STATUS_NAMES
    )

    if is_openish and close_dt is None:
        if close_raw:
            return "date_missing", "not_applicable", ("close_unparseable",), None
        return "date_missing", "not_applicable", ("open_status_missing_close",), None

    if close_dt is not None and close_dt <= as_of_utc:
        return "closed", "not_applicable", ("close_at_or_before_as_of",), None

    pub_dt, _ = parse_tender_timestamp_raw(tender.publication_timestamp_selected)

    if is_openish and close_dt is not None and close_dt > as_of_utc:
        status_ok, status_cls = _ref_current_and_capable(
            ref=status_ref,
            field_name="status",
            as_of_utc=as_of_utc,
            freshness_threshold_hours=freshness_threshold_hours,
        )
        close_ok, close_cls = _ref_current_and_capable(
            ref=close_ref,
            field_name="close_timestamp",
            as_of_utc=as_of_utc,
            freshness_threshold_hours=freshness_threshold_hours,
        )
        life_cur = (
            "current_authoritative_snapshot"
            if status_ok and close_ok
            else "stale_or_unverified_field_provenance"
        )
        if status_ok and close_ok:
            bucket = closing_bucket_for_delta(close_dt - as_of_utc)
            return (
                "active_open",
                bucket,
                (
                    "current_open_with_future_close",
                    f"status_provenance={status_cls}",
                    f"close_provenance={close_cls}",
                ),
                life_cur,
            )
        reasons.append("stale_or_unverified_open_not_active")
        reasons.append(currentness_class)
        reasons.append(f"status_provenance={status_cls}")
        reasons.append(f"close_provenance={close_cls}")
        if (
            not has_publication_date_conflict
            and pub_dt is not None
            and pub_dt > as_of_utc
        ):
            return (
                "future_scheduled",
                "not_applicable",
                tuple(reasons) + ("publication_after_as_of",),
                life_cur,
            )
        return "status_unknown", "not_applicable", tuple(reasons), life_cur

    if has_publication_date_conflict:
        return (
            "status_unknown",
            "not_applicable",
            ("authoritative_publication_date_conflict",),
            None,
        )

    if pub_dt is not None and pub_dt > as_of_utc:
        return "future_scheduled", "not_applicable", ("publication_after_as_of",), None

    if not tender.status_code_selected and not tender.status_name_selected:
        if close_dt is None:
            return "status_unknown", "not_applicable", ("status_and_close_absent",), None
        if close_dt > as_of_utc:
            return (
                "future_scheduled",
                "not_applicable",
                ("future_close_status_unknown",),
                None,
            )
        return "closed", "not_applicable", ("past_close_status_unknown",), None

    return "status_unknown", "not_applicable", ("unmapped_status",), None


def apply_lifecycle(
    tenders: list[CoalescedProcurementTender],
    *,
    refs_by_id: dict[str, ProcurementEvidenceRef],
    conflicts_by_id: dict[str, Any] | None = None,
    as_of_utc: datetime,
    freshness_threshold_hours: int,
) -> list[CoalescedProcurementTender]:
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
        conflict_objs = [
            conflicts_by_id[cid]
            for cid in tender.conflict_ids
            if cid in conflicts_by_id
            and isinstance(conflicts_by_id[cid], CoalescenceConflict)
        ]
        conflict_kinds = {c.conflict_kind for c in conflict_objs}
        has_status_conflict = (
            tender.coalescence_status == "status_conflict"
            or "status_conflict" in conflict_kinds
        )
        has_close = any(
            c.conflict_kind == "date_conflict"
            and (
                c.field_name == "close_timestamp"
                or "close_timestamp_conflict" in c.reason_codes
            )
            for c in conflict_objs
        )
        has_pub = any(
            c.conflict_kind == "date_conflict"
            and (
                c.field_name == "publication_timestamp"
                or "publication_timestamp_conflict" in c.reason_codes
            )
            for c in conflict_objs
        )

        prov = tender.selected_field_provenance
        status_ref_id = prov.get("status") or prov.get("status_code")
        close_ref_id = prov.get("close_timestamp")
        pub_ref_id = prov.get("publication_timestamp")
        status_ref = refs_by_id.get(status_ref_id) if status_ref_id else None
        close_ref = refs_by_id.get(close_ref_id) if close_ref_id else None
        pub_ref = refs_by_id.get(pub_ref_id) if pub_ref_id else None

        life, bucket, life_reasons, life_cur = classify_lifecycle(
            tender=tender,
            currentness_class=currentness,
            as_of_utc=as_of_utc,
            has_status_conflict=has_status_conflict,
            has_close_date_conflict=has_close,
            has_publication_date_conflict=has_pub,
            status_ref=status_ref,
            close_ref=close_ref,
            publication_ref=pub_ref,
            freshness_threshold_hours=freshness_threshold_hours,
        )
        out.append(
            CoalescedProcurementTender(
                coalesced_tender_id=tender.coalesced_tender_id,
                canonical_tender_key=tender.canonical_tender_key,
                tender_key_kind=tender.tender_key_kind,
                candidate_source_kind=tender.candidate_source_kind,
                pr4_procurement_id=tender.pr4_procurement_id,
                pr4_procurement_ids=tender.pr4_procurement_ids,
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
                status_value_selected=tender.status_value_selected,
                source_status_system_selected=tender.source_status_system_selected,
                buyer_display_selected=tender.buyer_display_selected,
                buyer_source_id_selected=tender.buyer_source_id_selected,
                title_selected=tender.title_selected,
                selected_field_provenance=dict(tender.selected_field_provenance),
                lifecycle_status_evidence_ref_id=status_ref_id,
                lifecycle_close_evidence_ref_id=close_ref_id,
                lifecycle_publication_evidence_ref_id=pub_ref_id,
                lifecycle_evidence_currentness_class=life_cur,
                lifecycle_reason_codes=tuple(cur_reasons) + tuple(life_reasons),
                evidence_ref_ids=tender.evidence_ref_ids,
                conflict_ids=tender.conflict_ids,
            )
        )
    return out
