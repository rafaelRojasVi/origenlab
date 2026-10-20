"""Feature switches the supervisor reads before it starts a child, kept apart from the children's
own modules so `mail_worker` imports none of them."""

from __future__ import annotations

from collections.abc import Mapping

ENV_BID_ENABLED = "ORIGENLAB_WORKER_BID_ENABLED"


def bid_enabled(env: Mapping[str, str]) -> bool:
    """`true` (any case, trimmed) starts the tender child; anything else leaves it off."""
    return (env.get(ENV_BID_ENABLED) or "").strip().lower() == "true"


__all__ = ["ENV_BID_ENABLED", "bid_enabled"]
