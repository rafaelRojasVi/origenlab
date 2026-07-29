"""Stage mapping and chronological stage selection for PR3."""

from __future__ import annotations

from datetime import datetime

from origenlab_email_pipeline.commercial_opportunity.constants import (
    CONFIDENCE_RANK,
    DEAL_STATUS_STAGE_MAP,
    DOCUMENT_TYPE_STAGE_MAP,
    EVENT_TYPE_STAGE_MAP,
    HARD_TERMINAL_STAGES,
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
    """Higher is better for same-timestamp / same-stage ties."""
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


def select_stage(
    candidates: list[StageCandidate],
) -> tuple[StageCandidate | None, list[tuple[str, StageCandidate, StageCandidate]]]:
    """Select stage chronologically with explicit terminal/regression conflicts.

    Rules:
    - Hard-terminal contradictions (any orderable timestamps) emit
      ``conflicting_terminal_events``. Displayed terminal is the latest UTC instant.
    - Compatible progression uses UTC event chronology; later advances refine older stages.
    - Operator confirmation breaks ties; it does not outrank a later lifecycle advance.
    - Later lower-stage events emit ``stage_regression_prevented`` and do not regress.
    - Raw ISO strings are never compared lexicographically.
    """
    if not candidates:
        return None, []

    conflicts: list[tuple[str, StageCandidate, StageCandidate]] = []
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
                conflicts.append(("stage_regression_prevented", winner, c))
        return winner, conflicts

    if dated:
        ordered = sorted(dated, key=_chrono_key)
        current = ordered[0]
        for c in ordered[1:]:
            cur_rank = STAGE_RANK.get(current.canonical_stage, 0)
            new_rank = STAGE_RANK.get(c.canonical_stage, 0)
            if new_rank > cur_rank:
                current = c
            elif new_rank < cur_rank:
                conflicts.append(("stage_regression_prevented", current, c))
            else:
                if _tie_key(c) > _tie_key(current):
                    current = c
        return current, conflicts

    if undated:
        winner = max(undated, key=_tie_key)
        return winner, conflicts
    return None, conflicts


def is_terminal_stage(stage: str) -> bool:
    return (stage or "").strip().lower() in TERMINAL_STAGES


def is_hard_terminal_stage(stage: str) -> bool:
    return (stage or "").strip().lower() in HARD_TERMINAL_STAGES
