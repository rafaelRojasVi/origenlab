"""Tests for read-only NDR pending review status aggregation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from origenlab_email_pipeline.qa.ndr_pending_review_status import (
    RECOMMENDED_ACTION_REVIEW_NDR,
    apply_ndr_recommended_action,
    build_ndr_pending_review_status,
    find_latest_ndr_review_queue_dir,
)


def _write_queue(
    active_current: Path,
    *,
    date_label: str,
    summary: dict[str, object],
) -> Path:
    queue_dir = active_current / f"ndr_review_queue_{date_label}"
    queue_dir.mkdir(parents=True)
    (queue_dir / "ndr_review_summary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )
    return queue_dir


@pytest.fixture
def active_current(tmp_path: Path) -> Path:
    path = tmp_path / "active" / "current"
    path.mkdir(parents=True)
    return path


def test_find_latest_ndr_review_queue_dir_picks_newest(active_current: Path) -> None:
    _write_queue(
        active_current,
        date_label="2026_06_09",
        summary={"date_label": "2026_06_09", "batch_counts": {"A": 0, "B": 0, "C": 0, "D": 0, "E": 0}},
    )
    newer = _write_queue(
        active_current,
        date_label="2026_06_11",
        summary={"date_label": "2026_06_11", "batch_counts": {"A": 0, "B": 0, "C": 0, "D": 0, "E": 0}},
    )
    assert find_latest_ndr_review_queue_dir(active_current) == newer


def test_build_status_when_no_queue(active_current: Path) -> None:
    status = build_ndr_pending_review_status(active_current)
    assert status["queue_exists"] is False
    assert status["pending_review"] is False
    assert status["queue_dir"] is None
    assert status["parse_error"] is None


def test_build_status_from_summary_2026_06_11_shape(active_current: Path) -> None:
    queue_dir = _write_queue(
        active_current,
        date_label="2026_06_11",
        summary={
            "generated_at": "2026-06-11T21:43:08+00:00",
            "since_days": 1,
            "date_label": "2026_06_11",
            "candidates_total": 129,
            "candidates_already_suppressed": 53,
            "candidates_unsuppressed": 76,
            "batch_counts": {"A": 53, "B": 28, "C": 1, "D": 42, "E": 5},
            "allowlist_batch_a_count": 18,
            "allowlist_batch_b_count": 14,
        },
    )
    status = build_ndr_pending_review_status(active_current)
    assert status["queue_exists"] is True
    assert status["pending_review"] is True
    assert status["queue_dir"] == str(queue_dir.resolve())
    assert status["date_label"] == "2026_06_11"
    assert status["generated_at_utc"] == "2026-06-11T21:43:08+00:00"
    assert status["since_days"] == 1
    assert status["candidates_total"] == 129
    assert status["candidates_already_suppressed"] == 53
    assert status["candidates_unsuppressed"] == 76
    assert status["batch_counts"] == {"A": 53, "B": 28, "C": 1, "D": 42, "E": 5}
    assert status["allowlist_batch_a_count"] == 18
    assert status["allowlist_batch_b_count"] == 14
    assert status["batch_cde_count"] == 48
    assert status["parse_error"] is None


def test_pending_review_false_when_allowlists_empty(active_current: Path) -> None:
    _write_queue(
        active_current,
        date_label="2026_06_11",
        summary={
            "date_label": "2026_06_11",
            "batch_counts": {"A": 2, "B": 1, "C": 0, "D": 0, "E": 0},
            "allowlist_batch_a_count": 0,
            "allowlist_batch_b_count": 0,
            "candidates_total": 3,
            "candidates_already_suppressed": 3,
            "candidates_unsuppressed": 0,
        },
    )
    status = build_ndr_pending_review_status(active_current)
    assert status["pending_review"] is False


def test_missing_summary_marks_parse_error(active_current: Path) -> None:
    queue_dir = active_current / "ndr_review_queue_2026_06_11"
    queue_dir.mkdir()
    status = build_ndr_pending_review_status(active_current)
    assert status["queue_exists"] is True
    assert status["queue_dir"] == str(queue_dir.resolve())
    assert status["parse_error"] == "missing_summary"
    assert status["pending_review"] is False


def test_apply_ndr_recommended_action_only_when_healthy_and_idle() -> None:
    ndr = {"pending_review": True}
    assert (
        apply_ndr_recommended_action(
            verdict="healthy",
            recommended_action="none",
            ndr_pending_review=ndr,
        )
        == RECOMMENDED_ACTION_REVIEW_NDR
    )
    assert (
        apply_ndr_recommended_action(
            verdict="attention",
            recommended_action="wait_for_mirror_cooldown",
            ndr_pending_review=ndr,
        )
        == "wait_for_mirror_cooldown"
    )
    assert (
        apply_ndr_recommended_action(
            verdict="healthy",
            recommended_action="none",
            ndr_pending_review={"pending_review": False},
        )
        == "none"
    )


def test_find_latest_ndr_review_queue_uses_generated_at_not_suffix(
    active_current: Path,
) -> None:
    """A named same-day queue must not shadow a newer canonical queue."""
    _write_queue(
        active_current,
        date_label="2026_08_31_hielscher_14d",
        summary={
            "generated_at": "2026-08-31T15:32:42+00:00",
            "date_label": "2026_08_31",
            "batch_counts": {
                "A": 43,
                "B": 14,
                "C": 0,
                "D": 75,
                "E": 15,
            },
        },
    )

    canonical = _write_queue(
        active_current,
        date_label="2026_08_31",
        summary={
            "generated_at": "2026-08-31T21:54:18+00:00",
            "date_label": "2026_08_31",
            "batch_counts": {
                "A": 18,
                "B": 36,
                "C": 0,
                "D": 11,
                "E": 3,
            },
        },
    )

    assert find_latest_ndr_review_queue_dir(active_current) == canonical
