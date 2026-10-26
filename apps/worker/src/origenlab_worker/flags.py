"""Feature switches the supervisor reads before it starts a child, kept apart from the children's
own modules so `mail_worker` imports none of them."""

from __future__ import annotations

from collections.abc import Mapping

ENV_BID_ENABLED = "ORIGENLAB_WORKER_BID_ENABLED"
ENV_CAPTURE_SCHEDULER = "ORIGENLAB_WORKER_CAPTURE_SCHEDULER_ENABLED"
ENV_PROSPECTS_ENABLED = "ORIGENLAB_WORKER_PROSPECTS_ENABLED"


def bid_enabled(env: Mapping[str, str]) -> bool:
    """`true` (any case, trimmed) starts the tender child; anything else leaves it off."""
    return (env.get(ENV_BID_ENABLED) or "").strip().lower() == "true"


def capture_enabled(env: Mapping[str, str]) -> bool:
    """`true` (any case, trimmed) starts the capture scheduler child."""
    return env.get(ENV_CAPTURE_SCHEDULER, "").strip().lower() == "true"


def prospects_enabled(env: Mapping[str, str]) -> bool:
    """`true` (any case, trimmed) enables the daily prospect search; anything else leaves it off.
    When off the sweep leaves queued runs pending and the API reports the feature as off."""
    return (env.get(ENV_PROSPECTS_ENABLED) or "").strip().lower() == "true"


__all__ = [
    "ENV_BID_ENABLED", "ENV_CAPTURE_SCHEDULER", "ENV_PROSPECTS_ENABLED",
    "bid_enabled", "capture_enabled", "prospects_enabled",
]
