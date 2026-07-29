"""Stage mapping and deterministic stage selection for PR3."""

from __future__ import annotations

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


def map_deal_status(status: str) -> tuple[str | None, bool]:
    """Map commercial_deal.deal_status → (canonical_stage, is_lifecycle_terminal)."""
    key = (status or "").strip().lower()
    if key not in DEAL_STATUS_STAGE_MAP:
        return None, False
    stage, terminal = DEAL_STATUS_STAGE_MAP[key]
    return stage, terminal


def map_event_type(event_type: str) -> tuple[str | None, bool, bool]:
    """Map deal event type → (canonical_stage|None, is_lifecycle_terminal, client_side)."""
    key = (event_type or "").strip().lower()
    if key not in EVENT_TYPE_STAGE_MAP:
        return None, False, True
    return EVENT_TYPE_STAGE_MAP[key]


def map_document_type(document_type: str) -> tuple[str | None, bool, bool]:
    key = (document_type or "").strip().lower()
    if key not in DOCUMENT_TYPE_STAGE_MAP:
        return None, False, True
    return DOCUMENT_TYPE_STAGE_MAP[key]


def _is_hard_terminal(c: StageCandidate) -> bool:
    return c.canonical_stage in HARD_TERMINAL_STAGES and bool((c.event_at or "").strip())


def _rank(c: StageCandidate) -> tuple:
    has_ts = 1 if (c.event_at or "").strip() else 0
    hard_terminal = 1 if _is_hard_terminal(c) else 0
    op_conf = 1 if (c.operator_confirmed and has_ts) else 0
    stage_rank = STAGE_RANK.get(c.canonical_stage, 0) if has_ts else -1
    conf = CONFIDENCE_RANK.get(c.confidence, 0)
    tier = -c.precedence_tier
    ts = c.event_at or ""
    src = f"{c.source_table}|{c.source_record_id}|{c.evidence_id}"
    return (hard_terminal, op_conf, has_ts, stage_rank, ts, conf, tier, src)


def select_stage(
    candidates: list[StageCandidate],
) -> tuple[StageCandidate | None, list[tuple[str, StageCandidate, StageCandidate]]]:
    """Select best stage candidate; return (winner, conflict_pairs)."""
    if not candidates:
        return None, []

    conflicts: list[tuple[str, StageCandidate, StageCandidate]] = []

    dated_hard = [c for c in candidates if _is_hard_terminal(c)]
    if dated_hard:
        by_ts: dict[str, list[StageCandidate]] = {}
        for c in dated_hard:
            by_ts.setdefault(c.event_at or "", []).append(c)
        for _ts, group in by_ts.items():
            stages = {g.canonical_stage for g in group}
            if len(stages) > 1:
                ordered = sorted(group, key=_rank, reverse=True)
                conflicts.append(("same_timestamp_stage_conflict", ordered[0], ordered[1]))
                conflicts.append(("conflicting_terminal_events", ordered[0], ordered[1]))

        winner = max(dated_hard, key=lambda c: (c.event_at or "", _rank(c)))
        for c in candidates:
            if c is winner or not (c.event_at or "").strip() or _is_hard_terminal(c):
                continue
            if (c.event_at or "") > (winner.event_at or "") and STAGE_RANK.get(
                c.canonical_stage, 0
            ) < STAGE_RANK.get(winner.canonical_stage, 0):
                conflicts.append(("stage_regression_prevented", winner, c))
        return winner, conflicts

    dated = [c for c in candidates if (c.event_at or "").strip()]
    if dated:

        def dated_key(c: StageCandidate) -> tuple:
            op_conf = 1 if c.operator_confirmed else 0
            stage_rank = STAGE_RANK.get(c.canonical_stage, 0)
            conf = CONFIDENCE_RANK.get(c.confidence, 0)
            tier = -c.precedence_tier
            ts = c.event_at or ""
            src = f"{c.source_table}|{c.source_record_id}|{c.evidence_id}"
            return (op_conf, stage_rank, ts, conf, tier, tuple(-ord(ch) for ch in src[:64]))

        winner = max(dated, key=dated_key)
        return winner, conflicts

    winner = max(candidates, key=_rank)
    return winner, conflicts


def is_terminal_stage(stage: str) -> bool:
    return (stage or "").strip().lower() in TERMINAL_STAGES
