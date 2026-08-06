"""PR5E.2 lifecycle precedence: known status must not be erased by status_unknown."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from origenlab_email_pipeline.commercial_procurement_candidate_planner.constants import (
    ACTIVE_STATUS_CODE,
    AWARDED_STATUS_CODES,
    AWARDED_STATUS_NAMES,
    CANCELLED_STATUS_CODES,
    CANCELLED_STATUS_NAMES,
    CLOSED_STATUS_CODES,
    CLOSED_STATUS_NAMES,
    PUBLICADA_STATUS_NAMES,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    CoalescedProcurementTender,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.normalize import (
    closing_bucket_for_delta,
    parse_tender_timestamp_raw,
    status_name_matches_expected,
)

KNOWN_TERMINAL = frozenset({"closed", "awarded", "cancelled"})
KNOWN_OPEN = frozenset({"active_open", "future_scheduled"})
KNOWN_LIFECYCLES = KNOWN_TERMINAL | KNOWN_OPEN | frozenset(
    {"status_conflict", "date_missing"}
)
WEAK_LIFECYCLES = frozenset({"status_unknown"})


def _parse_as_of(as_of_utc: str) -> datetime:
    raw = as_of_utc.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _is_openish(tender: CoalescedProcurementTender) -> bool:
    code = (tender.status_code_selected or "").strip()
    return code == ACTIVE_STATUS_CODE or status_name_matches_expected(
        tender.status_name_selected, PUBLICADA_STATUS_NAMES
    )


def _terminal_from_status(tender: CoalescedProcurementTender) -> str | None:
    code = (tender.status_code_selected or "").strip()
    name = tender.status_name_selected
    if code in AWARDED_STATUS_CODES or status_name_matches_expected(
        name, AWARDED_STATUS_NAMES
    ):
        return "awarded"
    if code in CANCELLED_STATUS_CODES or status_name_matches_expected(
        name, CANCELLED_STATUS_NAMES
    ):
        return "cancelled"
    if code in CLOSED_STATUS_CODES or status_name_matches_expected(
        name, CLOSED_STATUS_NAMES
    ):
        return "closed"
    return None


@dataclass(frozen=True)
class LifecycleProjection:
    """Projected lifecycle with full provenance for operator review."""

    source_lifecycle_class: str
    projected_lifecycle_class: str
    closing_soon_bucket: str
    precedence_reason: str
    conflict_reason: str | None
    source_status_code: str | None
    source_status_name: str | None
    source_close_timestamp: str | None
    observation_plane: str
    lifecycle_independent_of_relevance: bool = True
    reason_codes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_lifecycle_class": self.source_lifecycle_class,
            "projected_lifecycle_class": self.projected_lifecycle_class,
            "closing_soon_bucket": self.closing_soon_bucket,
            "precedence_reason": self.precedence_reason,
            "conflict_reason": self.conflict_reason,
            "source_status_code": self.source_status_code,
            "source_status_name": self.source_status_name,
            "source_close_timestamp": self.source_close_timestamp,
            "observation_plane": self.observation_plane,
            "lifecycle_independent_of_relevance": True,
            "reason_codes": list(self.reason_codes),
        }


def project_tender_lifecycle(
    tender: CoalescedProcurementTender,
    *,
    as_of_utc: str,
) -> LifecycleProjection:
    """
    Apply general lifecycle precedence:

    * A known lifecycle must not be replaced by ``status_unknown``.
    * Same-snapshot live open values may restore ``active_open`` when PR5C
      fail-closed to ``status_unknown`` solely on provenance/as-of pinning.
    * A newer authoritative terminal status may override an older open status
      (represented here by selected terminal status codes/names).
    * Lifecycle remains independent of commercial relevance.
    """
    as_of = _parse_as_of(as_of_utc)
    source = tender.lifecycle_class
    plane = tender.candidate_source_kind
    reasons = list(tender.lifecycle_reason_codes)
    close_raw = tender.close_timestamp_selected
    close_dt, _ = parse_tender_timestamp_raw(close_raw)
    bucket = tender.closing_soon_bucket or "not_applicable"

    base_kwargs = dict(
        source_lifecycle_class=source,
        source_status_code=tender.status_code_selected,
        source_status_name=tender.status_name_selected,
        source_close_timestamp=close_raw,
        observation_plane=plane,
    )

    if source == "status_conflict":
        return LifecycleProjection(
            projected_lifecycle_class="status_conflict",
            closing_soon_bucket="not_applicable",
            precedence_reason="preserve_status_conflict",
            conflict_reason="authoritative_status_conflict",
            reason_codes=tuple(reasons + ["preserve_status_conflict"]),
            **base_kwargs,
        )

    terminal = _terminal_from_status(tender)
    if terminal is not None:
        # Newer/authoritative terminal overrides any open or unknown projection.
        if source in KNOWN_OPEN or source in WEAK_LIFECYCLES or source != terminal:
            return LifecycleProjection(
                projected_lifecycle_class=terminal,
                closing_soon_bucket="not_applicable",
                precedence_reason="authoritative_terminal_overrides_open_or_unknown",
                conflict_reason=None,
                reason_codes=tuple(
                    reasons
                    + [
                        "authoritative_terminal_overrides_open_or_unknown",
                        f"terminal={terminal}",
                    ]
                ),
                **base_kwargs,
            )

    if source in KNOWN_LIFECYCLES and source not in WEAK_LIFECYCLES:
        return LifecycleProjection(
            projected_lifecycle_class=source,
            closing_soon_bucket=bucket,
            precedence_reason="preserve_known_lifecycle",
            conflict_reason=None,
            reason_codes=tuple(reasons + ["preserve_known_lifecycle"]),
            **base_kwargs,
        )

    # source is weak (status_unknown) or unexpected — attempt value-level restore.
    if source in WEAK_LIFECYCLES or source not in KNOWN_LIFECYCLES:
        if _is_openish(tender) and close_dt is not None and close_dt > as_of:
            if close_dt is not None:
                bucket = closing_bucket_for_delta(close_dt - as_of)
            return LifecycleProjection(
                projected_lifecycle_class="active_open",
                closing_soon_bucket=bucket,
                precedence_reason=(
                    "known_open_values_precede_status_unknown_provenance"
                ),
                conflict_reason=None,
                reason_codes=tuple(
                    reasons
                    + [
                        "known_open_values_precede_status_unknown_provenance",
                        "status_unknown_must_not_overwrite_known_open",
                        f"plane={plane}",
                    ]
                ),
                **base_kwargs,
            )
        if _is_openish(tender) and close_dt is None:
            return LifecycleProjection(
                projected_lifecycle_class="date_missing",
                closing_soon_bucket="not_applicable",
                precedence_reason="openish_status_missing_close_after_unknown",
                conflict_reason=None,
                reason_codes=tuple(
                    reasons + ["openish_status_missing_close_after_unknown"]
                ),
                **base_kwargs,
            )

    return LifecycleProjection(
        projected_lifecycle_class=source,
        closing_soon_bucket=bucket,
        precedence_reason="passthrough_unresolved_lifecycle",
        conflict_reason=None,
        reason_codes=tuple(reasons + ["passthrough_unresolved_lifecycle"]),
        **base_kwargs,
    )


def apply_lifecycle_precedence(
    tenders: list[CoalescedProcurementTender],
    *,
    as_of_utc: str,
) -> dict[str, LifecycleProjection]:
    """Map coalesced_tender_id → lifecycle projection (deterministic order)."""
    out: dict[str, LifecycleProjection] = {}
    for tender in sorted(tenders, key=lambda t: t.coalesced_tender_id):
        out[tender.coalesced_tender_id] = project_tender_lifecycle(
            tender, as_of_utc=as_of_utc
        )
    return out


__all__ = [
    "KNOWN_LIFECYCLES",
    "KNOWN_OPEN",
    "KNOWN_TERMINAL",
    "LifecycleProjection",
    "WEAK_LIFECYCLES",
    "apply_lifecycle_precedence",
    "project_tender_lifecycle",
]
