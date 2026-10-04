"""V1-lane campaign declarations — campaigns being sent through the old systemd/email-pipeline
lane that are not yet visible in the V2 database.

This module reads ``v1_lane_campaigns.json`` (next to this file) and validates it. Every entry
represents a campaign the owner runs via the V1 systemd timer; V2 reads it to show the
schedule in the marketing calendar.

Validation rules:

- ``key``: non-empty slug (starts with alphanumeric, then alphanumeric or hyphens)
- ``name``: non-blank string
- ``channel``: must be ``"v1"``
- ``send_days``: non-empty list of YYYY-MM-DD ISO dates, strictly ascending, unique, span ≤ 31 days
- ``send_time``: HH:MM (24-hour, 00:00–23:59)
- ``promo_until``: YYYY-MM-DD, must be ≥ last send day

At runtime, a file that fails validation is logged as a warning and returns no entries — a typo
never stops the API. A unit test validates the committed file strictly so a typo fails CI
instead of production.

Lifecycle: after the last wave's real results are imported into V2 and the campaign appears in
``outbound.campaign``, remove the entry from this file.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]*$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^\d{2}:\d{2}$")

JSON_PATH = Path(__file__).with_name("v1_lane_campaigns.json")


@dataclass(frozen=True)
class V1LaneCampaign:
    key: str
    name: str
    channel: str
    send_days: tuple[str, ...]
    send_time: str
    promo_until: str


class V1LaneValidationError(ValueError):
    pass


def _validate_date(value: object, field: str) -> str:
    if not isinstance(value, str) or not _DATE_RE.match(value):
        raise V1LaneValidationError(f"{field}: expected YYYY-MM-DD, got {value!r}")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise V1LaneValidationError(f"{field}: {exc}") from exc
    return value


def validate_entry(entry: object) -> V1LaneCampaign:
    """Validate a single campaign entry dict. Raises :exc:`V1LaneValidationError` on any problem."""
    if not isinstance(entry, dict):
        raise V1LaneValidationError("entry must be a dict")

    key = entry.get("key")
    if not isinstance(key, str) or not key or not _SLUG_RE.match(key):
        raise V1LaneValidationError(
            f"key must be a non-empty slug (alphanumeric + hyphens, starts with alphanumeric), got {key!r}"
        )

    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        raise V1LaneValidationError(f"name must be a non-blank string, got {name!r}")

    channel = entry.get("channel")
    if channel != "v1":
        raise V1LaneValidationError(f"channel must be 'v1', got {channel!r}")

    send_time = entry.get("send_time")
    if not isinstance(send_time, str) or not _TIME_RE.match(send_time):
        raise V1LaneValidationError(f"send_time must be HH:MM (two-digit hour and minute), got {send_time!r}")
    h, m = int(send_time[:2]), int(send_time[3:])
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise V1LaneValidationError(f"send_time out of range: {send_time!r}")

    raw_days = entry.get("send_days")
    if not isinstance(raw_days, list) or not raw_days:
        raise V1LaneValidationError("send_days must be a non-empty list")

    send_days = [_validate_date(d, f"send_days[{i}]") for i, d in enumerate(raw_days)]

    for i in range(1, len(send_days)):
        if send_days[i] <= send_days[i - 1]:
            raise V1LaneValidationError(
                f"send_days must be strictly ascending and unique: {send_days[i - 1]!r} >= {send_days[i]!r}"
            )

    first_d = date.fromisoformat(send_days[0])
    last_d = date.fromisoformat(send_days[-1])
    span = (last_d - first_d).days
    if span > 31:
        raise V1LaneValidationError(
            f"send_days span {span} days; must be ≤ 31"
        )

    promo_until = _validate_date(entry.get("promo_until"), "promo_until")
    if promo_until < send_days[-1]:
        raise V1LaneValidationError(
            f"promo_until ({promo_until!r}) must be >= last send day ({send_days[-1]!r})"
        )

    return V1LaneCampaign(
        key=key,
        name=name,
        channel=channel,
        send_days=tuple(send_days),
        send_time=send_time,
        promo_until=promo_until,
    )


def validate_file(data: object) -> list[V1LaneCampaign]:
    """Validate the parsed JSON root. Raises :exc:`V1LaneValidationError` on any problem."""
    if not isinstance(data, dict):
        raise V1LaneValidationError("root must be a dict")
    campaigns = data.get("campaigns")
    if not isinstance(campaigns, list):
        raise V1LaneValidationError("root.campaigns must be a list")
    return [validate_entry(e) for e in campaigns]


def load_v1_lane_campaigns(path: Path = JSON_PATH) -> list[V1LaneCampaign]:
    """Load and validate the V1-lane campaign declarations.

    Returns an empty list and logs a warning when the file is missing or invalid.
    A bad file never stops the API; the unit test catches typos before production.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return validate_file(data)
    except Exception as exc:
        log.warning("v1_lane_campaigns: failed to load %s — %s", path, exc)
        return []


def as_dict(campaign: V1LaneCampaign) -> dict:
    """Serialise a :class:`V1LaneCampaign` to a plain dict for JSON responses."""
    return {
        "key": campaign.key,
        "name": campaign.name,
        "channel": campaign.channel,
        "send_days": list(campaign.send_days),
        "send_time": campaign.send_time,
        "promo_until": campaign.promo_until,
    }
