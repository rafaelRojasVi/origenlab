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
class LifecycleCandidate:
    """One competing lifecycle value and the observation time behind it."""

    candidate_lifecycle_class: str
    candidate_source: str
    observed_at_utc: str | None
    reason_codes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_lifecycle_class": self.candidate_lifecycle_class,
            "candidate_source": self.candidate_source,
            "observed_at_utc": self.observed_at_utc,
            "reason_codes": list(self.reason_codes),
        }


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
    status_observed_at_utc: str | None = None
    lifecycle_observed_at_utc: str | None = None
    selected_observation_at_utc: str | None = None
    temporal_resolution_status: str = "not_applicable"
    candidates: tuple[LifecycleCandidate, ...] = ()

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
            "status_observed_at_utc": self.status_observed_at_utc,
            "lifecycle_observed_at_utc": self.lifecycle_observed_at_utc,
            "selected_observation_at_utc": self.selected_observation_at_utc,
            "temporal_resolution_status": self.temporal_resolution_status,
            "candidates": [c.to_dict() for c in self.candidates],
        }


def _timestamp_text(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt is not None else None


def _parse_observation(raw: str | None) -> datetime | None:
    if not raw:
        return None
    dt, _ = parse_tender_timestamp_raw(str(raw))
    if dt is not None:
        return dt
    try:
        parsed = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _lifecycle_candidates(
    tender: CoalescedProcurementTender,
    *,
    terminal: str | None,
    status_observed: datetime | None,
    lifecycle_observed: datetime | None,
) -> tuple[LifecycleCandidate, ...]:
    """Every competing lifecycle value considered, with its observation time."""
    candidates = [
        LifecycleCandidate(
            candidate_lifecycle_class=tender.lifecycle_class,
            candidate_source="coalesced_lifecycle_class",
            observed_at_utc=_timestamp_text(lifecycle_observed),
            reason_codes=tuple(tender.lifecycle_reason_codes),
        )
    ]
    if terminal is not None:
        candidates.append(
            LifecycleCandidate(
                candidate_lifecycle_class=terminal,
                candidate_source="selected_status_value",
                observed_at_utc=_timestamp_text(status_observed),
                reason_codes=("terminal_status_selected",),
            )
        )
    elif _is_openish(tender):
        candidates.append(
            LifecycleCandidate(
                candidate_lifecycle_class="active_open",
                candidate_source="selected_status_value",
                observed_at_utc=_timestamp_text(status_observed),
                reason_codes=("open_status_selected",),
            )
        )
    return tuple(candidates)


@dataclass(frozen=True)
class _TerminalChronology:
    terminal_wins: bool
    precedence_reason: str
    temporal_resolution_status: str
    reason_codes: tuple[str, ...]
    selected_at: datetime | None


def _terminal_chronology(
    *,
    source: str,
    terminal: str,
    status_observed: datetime | None,
    lifecycle_observed: datetime | None,
    close_dt: datetime | None,
    as_of: datetime,
) -> _TerminalChronology:
    """Decide whether a terminal status may override the source lifecycle."""
    if source not in KNOWN_OPEN:
        # Unknown or already-terminal sources carry no open claim to protect.
        return _TerminalChronology(
            terminal_wins=True,
            precedence_reason="authoritative_terminal_overrides_open_or_unknown",
            temporal_resolution_status="terminal_over_unknown",
            reason_codes=("authoritative_terminal_overrides_open_or_unknown",),
            selected_at=status_observed,
        )

    if status_observed is not None and lifecycle_observed is not None:
        if status_observed > lifecycle_observed:
            return _TerminalChronology(
                terminal_wins=True,
                precedence_reason="newer_terminal_observation_overrides_open",
                temporal_resolution_status="observed_chronology_applied",
                reason_codes=(
                    "authoritative_terminal_overrides_open_or_unknown",
                    "newer_terminal_observation_overrides_open",
                ),
                selected_at=status_observed,
            )
        if status_observed == lifecycle_observed:
            # Two contradictory claims observed at the same instant cannot be
            # ordered; neither may silently win.
            return _TerminalChronology(
                terminal_wins=False,
                precedence_reason="fail_closed_same_instant_open_and_terminal",
                temporal_resolution_status="temporal_review_required",
                reason_codes=(
                    "same_instant_open_and_terminal_observation",
                    "temporal_review_required",
                ),
                selected_at=status_observed,
            )
        return _TerminalChronology(
            terminal_wins=False,
            precedence_reason="fail_closed_older_terminal_observation",
            temporal_resolution_status="temporal_review_required",
            reason_codes=(
                "older_terminal_observation_must_not_override_newer_open",
                "temporal_review_required",
            ),
            selected_at=lifecycle_observed,
        )

    # No observation chronology at all: the terminal claim is not demonstrably
    # newer than the open claim, whatever the tender's own close date says.
    reason_codes = [
        "missing_observation_chronology",
        "temporal_review_required",
    ]
    if close_dt is not None and close_dt > as_of:
        reason_codes.append("terminal_status_conflicts_with_future_close")
    return _TerminalChronology(
        terminal_wins=False,
        precedence_reason="fail_closed_missing_observation_chronology",
        temporal_resolution_status="temporal_review_required",
        reason_codes=tuple(reason_codes),
        selected_at=None,
    )


def project_tender_lifecycle(
    tender: CoalescedProcurementTender,
    *,
    as_of_utc: str,
    status_observed_at_utc: str | None = None,
    lifecycle_observed_at_utc: str | None = None,
) -> LifecycleProjection:
    """
    Apply general lifecycle precedence:

    * A known lifecycle must not be replaced by ``status_unknown``.
    * Same-snapshot live open values may restore ``active_open`` when PR5C
      fail-closed to ``status_unknown`` solely on provenance/as-of pinning.
    * A terminal status overrides a known-open lifecycle only when the terminal
      observation is demonstrably at least as new. When a caller supplies no
      observation chronology and the tender's own values contradict the terminal
      claim (a close date still in the future), the projection fails closed to
      ``status_conflict`` instead of inventing a newer observation.
    * Lifecycle remains independent of commercial relevance.

    Acquisition stamps in the future are handled by the planner's temporal
    quarantine, never by rewriting timestamps here.
    """
    as_of = _parse_as_of(as_of_utc)
    source = tender.lifecycle_class
    plane = tender.candidate_source_kind
    reasons = list(tender.lifecycle_reason_codes)
    close_raw = tender.close_timestamp_selected
    close_dt, _ = parse_tender_timestamp_raw(close_raw)
    bucket = tender.closing_soon_bucket or "not_applicable"

    status_observed = _parse_observation(status_observed_at_utc)
    # A publication date says when the tender was announced, not when its
    # lifecycle was observed; it must never stand in for observation chronology.
    lifecycle_observed = _parse_observation(lifecycle_observed_at_utc)
    terminal = _terminal_from_status(tender)
    candidates = _lifecycle_candidates(
        tender,
        terminal=terminal,
        status_observed=status_observed,
        lifecycle_observed=lifecycle_observed,
    )

    base_kwargs = dict(
        source_lifecycle_class=source,
        source_status_code=tender.status_code_selected,
        source_status_name=tender.status_name_selected,
        source_close_timestamp=close_raw,
        observation_plane=plane,
        status_observed_at_utc=_timestamp_text(status_observed),
        lifecycle_observed_at_utc=_timestamp_text(lifecycle_observed),
        candidates=candidates,
    )

    if source == "status_conflict":
        return LifecycleProjection(
            projected_lifecycle_class="status_conflict",
            closing_soon_bucket="not_applicable",
            precedence_reason="preserve_status_conflict",
            conflict_reason="authoritative_status_conflict",
            reason_codes=tuple(reasons + ["preserve_status_conflict"]),
            temporal_resolution_status="source_status_conflict",
            **base_kwargs,
        )

    if terminal is not None and source != terminal:
        chronology = _terminal_chronology(
            source=source,
            terminal=terminal,
            status_observed=status_observed,
            lifecycle_observed=lifecycle_observed,
            close_dt=close_dt,
            as_of=as_of,
        )
        if chronology.terminal_wins:
            return LifecycleProjection(
                projected_lifecycle_class=terminal,
                closing_soon_bucket="not_applicable",
                precedence_reason=chronology.precedence_reason,
                conflict_reason=None,
                reason_codes=tuple(
                    reasons + list(chronology.reason_codes) + [f"terminal={terminal}"]
                ),
                selected_observation_at_utc=_timestamp_text(chronology.selected_at),
                temporal_resolution_status=chronology.temporal_resolution_status,
                **base_kwargs,
            )
        return LifecycleProjection(
            projected_lifecycle_class="status_conflict",
            closing_soon_bucket="not_applicable",
            precedence_reason=chronology.precedence_reason,
            conflict_reason="terminal_status_conflicts_with_open_values",
            reason_codes=tuple(
                reasons + list(chronology.reason_codes) + [f"terminal={terminal}"]
            ),
            selected_observation_at_utc=_timestamp_text(chronology.selected_at),
            temporal_resolution_status=chronology.temporal_resolution_status,
            **base_kwargs,
        )

    if source in KNOWN_OPEN and close_dt is not None and close_dt <= as_of:
        # An open claim whose own bidding window already elapsed, with no terminal
        # status to explain it, is stale evidence — not a current opportunity.
        return LifecycleProjection(
            projected_lifecycle_class="status_conflict",
            closing_soon_bucket="not_applicable",
            precedence_reason="fail_closed_stale_open_close_elapsed",
            conflict_reason="stale_open_close_elapsed_without_terminal_status",
            reason_codes=tuple(
                reasons
                + [
                    "stale_open_close_elapsed_without_terminal_status",
                    "temporal_review_required",
                ]
            ),
            selected_observation_at_utc=_timestamp_text(lifecycle_observed),
            temporal_resolution_status="temporal_review_required",
            **base_kwargs,
        )

    if source in KNOWN_LIFECYCLES and source not in WEAK_LIFECYCLES:
        return LifecycleProjection(
            projected_lifecycle_class=source,
            closing_soon_bucket=bucket,
            precedence_reason="preserve_known_lifecycle",
            conflict_reason=None,
            reason_codes=tuple(reasons + ["preserve_known_lifecycle"]),
            selected_observation_at_utc=_timestamp_text(lifecycle_observed),
            temporal_resolution_status="no_competing_terminal_status",
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
                selected_observation_at_utc=_timestamp_text(
                    status_observed or lifecycle_observed
                ),
                temporal_resolution_status="open_values_restored",
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
                selected_observation_at_utc=_timestamp_text(
                    status_observed or lifecycle_observed
                ),
                temporal_resolution_status="close_timestamp_missing",
                **base_kwargs,
            )

    return LifecycleProjection(
        projected_lifecycle_class=source,
        closing_soon_bucket=bucket,
        precedence_reason="passthrough_unresolved_lifecycle",
        conflict_reason=None,
        reason_codes=tuple(reasons + ["passthrough_unresolved_lifecycle"]),
        selected_observation_at_utc=_timestamp_text(lifecycle_observed),
        temporal_resolution_status="passthrough",
        **base_kwargs,
    )


def apply_lifecycle_precedence(
    tenders: list[CoalescedProcurementTender],
    *,
    as_of_utc: str,
    status_observed_at_by_tender: dict[str, str] | None = None,
    lifecycle_observed_at_by_tender: dict[str, str] | None = None,
) -> dict[str, LifecycleProjection]:
    """Map coalesced_tender_id → lifecycle projection (deterministic order)."""
    status_observed = status_observed_at_by_tender or {}
    lifecycle_observed = lifecycle_observed_at_by_tender or {}
    out: dict[str, LifecycleProjection] = {}
    for tender in sorted(tenders, key=lambda t: t.coalesced_tender_id):
        tid = tender.coalesced_tender_id
        out[tid] = project_tender_lifecycle(
            tender,
            as_of_utc=as_of_utc,
            status_observed_at_utc=status_observed.get(tid),
            lifecycle_observed_at_utc=lifecycle_observed.get(tid),
        )
    return out


__all__ = [
    "KNOWN_LIFECYCLES",
    "KNOWN_OPEN",
    "KNOWN_TERMINAL",
    "LifecycleCandidate",
    "LifecycleProjection",
    "WEAK_LIFECYCLES",
    "apply_lifecycle_precedence",
    "project_tender_lifecycle",
]
