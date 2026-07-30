"""Stage mapping and chronological stage selection for PR3."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from origenlab_email_pipeline.commercial_opportunity.constants import (
    CONFIDENCE_RANK,
    DEAL_STATUS_SOURCE_SIDE,
    DEAL_STATUS_STAGE_MAP,
    DOCUMENT_TYPE_STAGE_MAP,
    EVENT_TYPE_STAGE_MAP,
    FULFILLMENT_COMPATIBLE_LATE_CLIENT_STAGES,
    HARD_TERMINAL_STAGES,
    SOURCE_SIDE_CLIENT,
    SOURCE_SIDE_SUPPLIER,
    STAGE_RANK,
    TERMINAL_STAGES,
)
from origenlab_email_pipeline.commercial_opportunity.models import StageCandidate
from origenlab_email_pipeline.commercial_opportunity.timestamps import (
    ParsedEventTime,
    instant_max_key,
    parse_event_time,
)


def map_deal_status(status: str) -> tuple[str | None, bool, bool]:
    """Map deal_status → (canonical_stage|None, is_lifecycle_terminal, is_hard_terminal).

    ``closed`` maps to unknown pending supporting evidence (handled by resolver).
    """
    key = (status or "").strip().lower()
    if key not in DEAL_STATUS_STAGE_MAP:
        return None, False, False
    stage, lifecycle_terminal = DEAL_STATUS_STAGE_MAP[key]
    hard = stage in HARD_TERMINAL_STAGES if stage else False
    return stage, lifecycle_terminal, hard


def deal_status_source_side(status: str) -> str:
    """Return ``client`` or ``supplier`` for a mapped deal_status (default client)."""
    key = (status or "").strip().lower()
    return DEAL_STATUS_SOURCE_SIDE.get(key, SOURCE_SIDE_CLIENT)


def deal_status_is_client_side(status: str) -> bool:
    return deal_status_source_side(status) == SOURCE_SIDE_CLIENT


def deal_status_is_supplier_side(status: str) -> bool:
    return deal_status_source_side(status) == SOURCE_SIDE_SUPPLIER


def map_event_type(event_type: str) -> tuple[str | None, bool, bool, bool]:
    """→ (stage|None, lifecycle_terminal, hard_terminal, client_side)."""
    key = (event_type or "").strip().lower()
    if key not in EVENT_TYPE_STAGE_MAP:
        return None, False, False, True
    stage, lifecycle_terminal, client_side = EVENT_TYPE_STAGE_MAP[key]
    hard = bool(stage and stage in HARD_TERMINAL_STAGES)
    return stage, lifecycle_terminal, hard, client_side


def map_document_type(document_type: str) -> tuple[str | None, bool, bool, bool]:
    """→ (stage|None, lifecycle_terminal, hard_terminal, client_side)."""
    key = (document_type or "").strip().lower()
    if key not in DOCUMENT_TYPE_STAGE_MAP:
        return None, False, False, True
    stage, lifecycle_terminal, client_side = DOCUMENT_TYPE_STAGE_MAP[key]
    hard = bool(stage and stage in HARD_TERMINAL_STAGES)
    return stage, lifecycle_terminal, hard, client_side


def _src_key(c: StageCandidate) -> str:
    return f"{c.source_table}|{c.source_record_id}|{c.evidence_id}"


def _tie_key(c: StageCandidate) -> tuple:
    """Higher is better for same-timestamp ties (not same-stage across time)."""
    return (
        1 if c.operator_confirmed else 0,
        CONFIDENCE_RANK.get(c.confidence, 0),
        STAGE_RANK.get(c.canonical_stage, 0),
        # Prefer lexicographically smaller source key: invert via negative ordinals
        tuple(-ord(ch) for ch in _src_key(c)[:80]),
    )


def _parsed(c: StageCandidate) -> ParsedEventTime:
    if isinstance(c.event_instant, datetime):
        return ParsedEventTime(
            original=c.event_at,
            instant=c.event_instant,
            precision="datetime",
            error=None,
        )
    return parse_event_time(c.event_at)


def _is_orderable(c: StageCandidate) -> bool:
    return _parsed(c).is_orderable


def _chrono_key(c: StageCandidate) -> tuple:
    """Ascending chronology with deterministic same-time ordering (UTC instants)."""
    p = _parsed(c)
    # Dated path only calls this for orderable candidates.
    assert p.instant is not None
    return (
        p.instant,
        -1 if c.operator_confirmed else 0,
        -CONFIDENCE_RANK.get(c.confidence, 0),
        -STAGE_RANK.get(c.canonical_stage, 0),
        _src_key(c),
    )


def _latest_key(c: StageCandidate) -> tuple:
    return (*instant_max_key(_parsed(c)), _tie_key(c))


def _is_compatible_late_client_prerequisite(
    selected: StageCandidate, later: StageCandidate
) -> bool:
    """Fulfillment + later dated client purchase_pending/won is corroboration, not regression."""
    return (
        selected.canonical_stage == "fulfillment"
        and later.client_side
        and later.canonical_stage in FULFILLMENT_COMPATIBLE_LATE_CLIENT_STAGES
    )


@dataclass
class StageSelectionStats:
    compatible_late_client_prerequisites: int = 0
    same_stage_winner_advanced_to_later_evidence: int = 0


@dataclass
class StageSelectionResult:
    winner: StageCandidate | None
    conflicts: list[tuple[str, StageCandidate, StageCandidate]] = field(
        default_factory=list
    )
    stats: StageSelectionStats = field(default_factory=StageSelectionStats)


def select_stage(
    candidates: list[StageCandidate],
) -> StageSelectionResult:
    """Select stage chronologically with explicit terminal/regression conflicts.

    Rules:
    - Hard-terminal contradictions (any orderable timestamps) emit
      ``conflicting_terminal_events``. Displayed terminal is the latest UTC instant.
    - Compatible progression uses UTC event chronology; later advances refine older stages.
    - Same canonical stage: later UTC instant wins; same-instant uses operator/confidence/key.
    - Later client purchase_pending/won after fulfillment is compatible (no regression).
    - Later lower-stage events otherwise emit ``stage_regression_prevented`` and do not regress.
    - Raw ISO strings are never compared lexicographically.
    """
    if not candidates:
        return StageSelectionResult(winner=None)

    conflicts: list[tuple[str, StageCandidate, StageCandidate]] = []
    stats = StageSelectionStats()
    dated = [c for c in candidates if _is_orderable(c)]
    undated = [c for c in candidates if not _is_orderable(c)]

    dated_hard = [c for c in dated if c.is_hard_terminal]
    if dated_hard:
        hard_stages = {c.canonical_stage for c in dated_hard}
        # Deterministic displayed terminal: latest UTC instant, then tie-breakers.
        winner = max(dated_hard, key=_latest_key)
        if len(hard_stages) > 1:
            others = [c for c in dated_hard if c.canonical_stage != winner.canonical_stage]
            other = max(others, key=_latest_key)
            conflicts.append(("conflicting_terminal_events", winner, other))
            by_instant: dict[datetime, list[StageCandidate]] = {}
            for c in dated_hard:
                inst = _parsed(c).instant
                if inst is None:
                    continue
                by_instant.setdefault(inst, []).append(c)
            for group in by_instant.values():
                if len({g.canonical_stage for g in group}) > 1:
                    ordered = sorted(group, key=_tie_key, reverse=True)
                    conflicts.append(("same_timestamp_stage_conflict", ordered[0], ordered[1]))

        winner_instant = _parsed(winner).instant
        for c in dated:
            if c is winner or c.is_hard_terminal:
                continue
            c_instant = _parsed(c).instant
            if (
                winner_instant is not None
                and c_instant is not None
                and c_instant > winner_instant
                and STAGE_RANK.get(c.canonical_stage, 0)
                < STAGE_RANK.get(winner.canonical_stage, 0)
            ):
                if _is_compatible_late_client_prerequisite(winner, c):
                    stats.compatible_late_client_prerequisites += 1
                else:
                    conflicts.append(("stage_regression_prevented", winner, c))
        return StageSelectionResult(winner=winner, conflicts=conflicts, stats=stats)

    if dated:
        ordered = sorted(dated, key=_chrono_key)
        current = ordered[0]
        for c in ordered[1:]:
            cur_rank = STAGE_RANK.get(current.canonical_stage, 0)
            new_rank = STAGE_RANK.get(c.canonical_stage, 0)
            if new_rank > cur_rank:
                current = c
            elif new_rank < cur_rank:
                if _is_compatible_late_client_prerequisite(current, c):
                    stats.compatible_late_client_prerequisites += 1
                else:
                    conflicts.append(("stage_regression_prevented", current, c))
            else:
                # Same stage: later UTC instant always advances; same instant → tie-breakers.
                cur_inst = _parsed(current).instant
                new_inst = _parsed(c).instant
                if (
                    new_inst is not None
                    and cur_inst is not None
                    and new_inst > cur_inst
                ):
                    current = c
                    stats.same_stage_winner_advanced_to_later_evidence += 1
                elif new_inst == cur_inst and _tie_key(c) > _tie_key(current):
                    current = c
        return StageSelectionResult(winner=current, conflicts=conflicts, stats=stats)

    if undated:
        winner = max(undated, key=_tie_key)
        return StageSelectionResult(winner=winner, conflicts=conflicts, stats=stats)
    return StageSelectionResult(winner=None, conflicts=conflicts, stats=stats)


def is_terminal_stage(stage: str) -> bool:
    return (stage or "").strip().lower() in TERMINAL_STAGES


def is_hard_terminal_stage(stage: str) -> bool:
    return (stage or "").strip().lower() in HARD_TERMINAL_STAGES
