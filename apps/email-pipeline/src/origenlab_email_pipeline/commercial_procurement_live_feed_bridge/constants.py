"""PR5B.2 live equipment-feed → PR5 review bridge constants."""

from __future__ import annotations

from typing import Final

from origenlab_email_pipeline.commercial_procurement_live_relevance.constants import (
    NEGATIVE_RELEVANCE_CLASSES,
    STRONG_RELEVANCE_CLASSES,
)

BRIDGE_PLANNER_VERSION: Final = "procurement_live_feed_bridge_pr5b2_v1"
BRIDGE_CONTRACT_VERSION: Final = "live_feed_bridge_contract_v1"
FIXTURE_ORIGIN: Final = "equipment_detail_cache_offline"

# Operational feed freshness — refresh cadence is ~12h; fail closed beyond this.
LIVE_FEED_MAX_AGE_HOURS: Final = 36

LIVE_BACKED_SOURCE_KINDS: Final = frozenset({"live_snapshot", "both"})

COMMERCIAL_BUCKETS: Final = (
    "current_opportunity",
    "needs_review",
    "historical_market_signal",
    "rejected",
)

HISTORICAL_LIFECYCLE_CLASSES: Final = frozenset(
    {"closed", "awarded", "cancelled"}
)

UNCERTAIN_LIFECYCLE_CLASSES: Final = frozenset(
    {
        "status_conflict",
        "date_missing",
        "status_unknown",
        "future_scheduled",
    }
)

CURRENT_CURRENTNESS_CLASSES: Final = frozenset(
    {"current_authoritative_snapshot"}
)

PRODUCT_FIT_RELEVANCE: Final = frozenset(STRONG_RELEVANCE_CLASSES) | {
    "laboratory_context_only"
}

REJECT_RELEVANCE: Final = frozenset(NEGATIVE_RELEVANCE_CLASSES)

FORBIDDEN_CLI_FLAGS: Final = (
    "--apply",
    "--persist",
    "--network",
    "--ticket",
    "--gmail",
    "--postgres",
    "--outreach",
    "--send",
    "--schedule",
    "--label",
)

REJECT_REASON_CODES: Final = (
    "cache_file_unreadable",
    "cache_json_invalid",
    "missing_listado",
    "missing_codigo_externo",
    "snapshot_incomplete",
    "unsupported_contract_version",
    "duplicate_tender_code",
    "parse_error",
)
