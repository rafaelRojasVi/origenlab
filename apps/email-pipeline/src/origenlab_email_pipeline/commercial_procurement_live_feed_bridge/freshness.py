"""Freshness and coverage evaluation for the live equipment feed."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.commercial_procurement_candidate_planner.normalize import (
    parse_as_of_utc,
)
from origenlab_email_pipeline.commercial_procurement_live_feed_bridge.constants import (
    LIVE_FEED_MAX_AGE_HOURS,
)


OBSERVATION_ELIGIBLE = "eligible"
OBSERVATION_FUTURE = "future_observation"
OBSERVATION_MISSING_CHRONOLOGY = "missing_chronology"
OBSERVATION_INVALID_CHRONOLOGY = "invalid_chronology"

OBSERVATION_TEMPORAL_STATUSES = (
    OBSERVATION_ELIGIBLE,
    OBSERVATION_FUTURE,
    OBSERVATION_MISSING_CHRONOLOGY,
    OBSERVATION_INVALID_CHRONOLOGY,
)


class LiveFeedFreshnessError(ValueError):
    """Feed is too stale for a current-opportunity review."""

    def __init__(self, code: str, message: str, *, details: dict[str, Any]) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


def _parse_flexible_utc(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        return parse_as_of_utc(text.replace("+00:00", "Z"))
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object at {path}")
    return payload


def evaluate_feed_freshness(
    *,
    as_of_utc: str,
    refresh_state: dict[str, Any] | None,
    manifest: dict[str, Any] | None,
    max_age_hours: int = LIVE_FEED_MAX_AGE_HOURS,
    allow_stale: bool = False,
) -> dict[str, Any]:
    """Compute freshness; raise LiveFeedFreshnessError unless allow_stale."""
    as_of = parse_as_of_utc(as_of_utc)
    last_refresh = None
    if refresh_state:
        last_refresh = _parse_flexible_utc(
            str(refresh_state.get("last_successful_refresh_at") or "")
            or None
        )
    generated = None
    if manifest:
        generated = _parse_flexible_utc(
            str(manifest.get("generated_at_utc") or "") or None
        )
    anchor = last_refresh or generated
    age_hours = None
    anchor_temporal_status = OBSERVATION_MISSING_CHRONOLOGY
    if anchor is not None:
        if anchor > as_of:
            # A refresh stamped after the review as-of is not "negative age"; it is
            # an unusable anchor. Quarantine it instead of calling the feed fresh.
            anchor_temporal_status = OBSERVATION_FUTURE
        else:
            anchor_temporal_status = OBSERVATION_ELIGIBLE
            age_hours = (as_of - anchor).total_seconds() / 3600.0
    fresh_enough = age_hours is not None and age_hours <= float(max_age_hours)
    report = {
        "as_of_utc": as_of_utc,
        "last_successful_refresh_at": (
            last_refresh.strftime("%Y-%m-%dT%H:%M:%SZ") if last_refresh else None
        ),
        "manifest_generated_at_utc": (
            generated.strftime("%Y-%m-%dT%H:%M:%SZ") if generated else None
        ),
        "freshness_anchor_at_utc": (
            anchor.strftime("%Y-%m-%dT%H:%M:%SZ") if anchor else None
        ),
        "age_hours_at_as_of": age_hours,
        "freshness_anchor_temporal_status": anchor_temporal_status,
        "max_age_hours": max_age_hours,
        "fresh_enough": fresh_enough,
        "fetched_summaries": (manifest or {}).get("fetched_summaries")
        or (refresh_state or {}).get("fetched_summaries"),
        "candidate_summaries": (manifest or {}).get("candidate_summaries")
        or (refresh_state or {}).get("candidate_summaries"),
        "detail_cache_hits": (manifest or {}).get("detail_cache_hits")
        or (refresh_state or {}).get("detail_cache_hits"),
        "detail_requests": (manifest or {}).get("detail_requests")
        or (refresh_state or {}).get("detail_requests"),
        "detail_error_count": (manifest or {}).get("detail_error_count")
        or (refresh_state or {}).get("detail_error_count"),
        "output_rows": (manifest or {}).get("output_rows")
        or (refresh_state or {}).get("output_rows"),
        "max_details_note": (
            "Equipment feed detail cache is bounded by keyword prefilter and "
            "max_details; never complete ChileCompra coverage."
        ),
    }
    if not fresh_enough and not allow_stale:
        raise LiveFeedFreshnessError(
            "BLOCKED_STALE_LIVE_FEED",
            "live equipment feed is stale or missing refresh timestamp",
            details=report,
        )
    return report


def resolve_acquired_at_utc(
    *,
    manifest: dict[str, Any] | None,
    refresh_state: dict[str, Any] | None,
    as_of_utc: str | None = None,
) -> str | None:
    """Prefer manifest generation time as shared acquisition stamp for cache hits.

    The returned stamp is the observed acquisition fact and is never rewritten.
    ``as_of_utc`` is accepted for call-site symmetry only; observations later than
    the review as-of are quarantined downstream, not moved backwards in time.
    """
    del as_of_utc
    for src in (manifest, refresh_state):
        if not src:
            continue
        for key in (
            "generated_at_utc",
            "last_successful_refresh_started_at",
            "last_successful_refresh_at",
        ):
            dt = _parse_flexible_utc(str(src.get(key) or "") or None)
            if dt is not None:
                return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return None


def is_future_observation(
    acquired_at_utc: str | None, *, as_of_utc: str | None
) -> bool:
    """True when an acquisition stamp is later than the review as-of.

    A ``False`` here only means "not proven to be in the future"; it is not an
    eligibility verdict. Use :func:`is_temporally_eligible_observation` to decide
    whether an observation may inform current-opportunity recognition.
    """
    acquired = _parse_flexible_utc(acquired_at_utc)
    as_of = _parse_flexible_utc(as_of_utc)
    if acquired is None or as_of is None:
        return False
    return acquired > as_of


def observation_temporal_status(
    acquired_at_utc: str | None, *, as_of_utc: str | None
) -> str:
    """Classify one acquisition stamp against the review as-of, failing closed.

    Missing or unparseable chronology is never silently eligible: it is reported
    as ``missing_chronology`` / ``invalid_chronology`` so the caller quarantines
    the observation instead of treating absence of proof as proof of currency.
    """
    as_of = _parse_flexible_utc(as_of_utc)
    if as_of is None:
        return (
            OBSERVATION_MISSING_CHRONOLOGY
            if not as_of_utc
            else OBSERVATION_INVALID_CHRONOLOGY
        )
    if acquired_at_utc is None or not str(acquired_at_utc).strip():
        return OBSERVATION_MISSING_CHRONOLOGY
    acquired = _parse_flexible_utc(acquired_at_utc)
    if acquired is None:
        return OBSERVATION_INVALID_CHRONOLOGY
    if acquired > as_of:
        return OBSERVATION_FUTURE
    return OBSERVATION_ELIGIBLE


def is_temporally_eligible_observation(
    acquired_at_utc: str | None, *, as_of_utc: str | None
) -> bool:
    """True only when the acquisition stamp is present, valid and not future."""
    return (
        observation_temporal_status(acquired_at_utc, as_of_utc=as_of_utc)
        == OBSERVATION_ELIGIBLE
    )
