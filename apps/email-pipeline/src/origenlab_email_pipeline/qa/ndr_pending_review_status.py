"""Read-only NDR pending review status from latest ndr_review_queue_* artifacts."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

NDR_REVIEW_QUEUE_DIR_PREFIX = "ndr_review_queue_"
NDR_REVIEW_SUMMARY_FILENAME = "ndr_review_summary.json"

RECOMMENDED_ACTION_REVIEW_NDR = "review_ndr_allowlists"


def _queue_generated_at_sort_key(path: Path) -> tuple[int, float, str]:
    """Prefer queue generation time over filename suffix ordering.

    Named campaign queues may share a date prefix with the canonical queue,
    e.g. ``ndr_review_queue_2026_08_31_hielscher_14d``. Lexicographic
    filename ordering is therefore not chronological.
    """
    summary_path = path / NDR_REVIEW_SUMMARY_FILENAME
    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            raw = data.get("generated_at_utc", data.get("generated_at"))
            if isinstance(raw, str) and raw.strip():
                value = raw.strip()
                if value.endswith("Z"):
                    value = value[:-1] + "+00:00"
                parsed = datetime.fromisoformat(value)
                if parsed.tzinfo is not None:
                    generated = parsed.astimezone(timezone.utc)
                    return (1, generated.timestamp(), path.name)
    except (OSError, json.JSONDecodeError, ValueError):
        pass

    # Preserve deterministic legacy behavior only as a fallback for
    # directories without usable generation metadata.
    return (0, 0.0, path.name)


def find_latest_ndr_review_queue_dir(active_current: Path) -> Path | None:
    """Return the chronologically newest NDR queue under active/current."""
    if not active_current.is_dir():
        return None
    candidates = [
        path
        for path in active_current.iterdir()
        if path.is_dir() and path.name.startswith(NDR_REVIEW_QUEUE_DIR_PREFIX)
    ]
    if not candidates:
        return None
    return max(candidates, key=_queue_generated_at_sort_key)


def _empty_ndr_pending_review_status(*, parse_error: str | None = None) -> dict[str, Any]:
    return {
        "queue_exists": False,
        "pending_review": False,
        "queue_dir": None,
        "date_label": None,
        "generated_at_utc": None,
        "since_days": None,
        "candidates_total": None,
        "candidates_already_suppressed": None,
        "candidates_unsuppressed": None,
        "batch_counts": None,
        "allowlist_batch_a_count": None,
        "allowlist_batch_b_count": None,
        "batch_cde_count": None,
        "parse_error": parse_error,
    }


def load_ndr_review_summary_json(queue_dir: Path) -> tuple[dict[str, Any] | None, str | None]:
    summary_path = queue_dir / NDR_REVIEW_SUMMARY_FILENAME
    if not summary_path.is_file():
        return None, "missing_summary"
    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None, "malformed_summary"
    if not isinstance(data, dict):
        return None, "malformed_summary"
    return data, None


def _normalize_batch_counts(raw: Any) -> dict[str, int] | None:
    if not isinstance(raw, dict):
        return None
    normalized: dict[str, int] = {}
    for key in ("A", "B", "C", "D", "E"):
        value = raw.get(key)
        if value is None:
            normalized[key] = 0
        else:
            try:
                normalized[key] = int(value)
            except (TypeError, ValueError):
                return None
    return normalized


def build_ndr_pending_review_status(active_current: Path) -> dict[str, Any]:
    """Build read-only NDR review backlog from the latest on-disk queue artifact."""
    queue_dir = find_latest_ndr_review_queue_dir(active_current)
    if queue_dir is None:
        return _empty_ndr_pending_review_status()

    summary, parse_error = load_ndr_review_summary_json(queue_dir)
    if summary is None:
        return {
            **_empty_ndr_pending_review_status(parse_error=parse_error),
            "queue_exists": True,
            "queue_dir": str(queue_dir.resolve()),
        }

    batch_counts = _normalize_batch_counts(summary.get("batch_counts"))
    if batch_counts is None:
        return {
            **_empty_ndr_pending_review_status(parse_error="malformed_summary"),
            "queue_exists": True,
            "queue_dir": str(queue_dir.resolve()),
            "date_label": summary.get("date_label"),
            "generated_at_utc": summary.get("generated_at"),
            "since_days": summary.get("since_days"),
        }

    allowlist_a = int(summary.get("allowlist_batch_a_count") or 0)
    allowlist_b = int(summary.get("allowlist_batch_b_count") or 0)
    batch_cde_count = batch_counts["C"] + batch_counts["D"] + batch_counts["E"]
    pending_review = allowlist_a > 0 or allowlist_b > 0

    return {
        "queue_exists": True,
        "pending_review": pending_review,
        "queue_dir": str(queue_dir.resolve()),
        "date_label": summary.get("date_label"),
        "generated_at_utc": summary.get("generated_at"),
        "since_days": summary.get("since_days"),
        "candidates_total": summary.get("candidates_total"),
        "candidates_already_suppressed": summary.get("candidates_already_suppressed"),
        "candidates_unsuppressed": summary.get("candidates_unsuppressed"),
        "batch_counts": batch_counts,
        "allowlist_batch_a_count": allowlist_a,
        "allowlist_batch_b_count": allowlist_b,
        "batch_cde_count": batch_cde_count,
        "parse_error": None,
    }


def apply_ndr_recommended_action(
    *,
    verdict: str,
    recommended_action: str,
    ndr_pending_review: dict[str, Any],
) -> str:
    """Suggest NDR human review when automation is otherwise idle and allowlists exist."""
    if not ndr_pending_review.get("pending_review"):
        return recommended_action
    if recommended_action != "none":
        return recommended_action
    if verdict != "healthy":
        return recommended_action
    return RECOMMENDED_ACTION_REVIEW_NDR
