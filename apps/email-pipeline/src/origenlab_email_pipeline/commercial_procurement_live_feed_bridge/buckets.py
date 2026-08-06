"""Declarative commercial opportunity buckets for live-backed PR5 review rows."""

from __future__ import annotations

from typing import Any, Mapping

from origenlab_email_pipeline.commercial_procurement_live_feed_bridge.constants import (
    LIVE_BACKED_SOURCE_KINDS,
    PRODUCT_FIT_RELEVANCE,
    REJECT_RELEVANCE,
    UNCERTAIN_LIFECYCLE_CLASSES,
    CURRENT_CURRENTNESS_CLASSES,
    HISTORICAL_LIFECYCLE_CLASSES,
    COMMERCIAL_BUCKETS,
)
from origenlab_email_pipeline.commercial_procurement_live_relevance.constants import (
    RELEVANCE_CLASSES,
    STRONG_RELEVANCE_CLASSES,
)


def bucket_policy_document() -> dict[str, Any]:
    return {
        "buckets": list(COMMERCIAL_BUCKETS),
        "evaluation_order": [
            "rejected",
            "historical_market_signal",
            "current_opportunity",
            "needs_review",
        ],
        "live_backed_source_kinds": sorted(LIVE_BACKED_SOURCE_KINDS),
        "reject_relevance": sorted(REJECT_RELEVANCE),
        "product_fit_relevance": sorted(PRODUCT_FIT_RELEVANCE),
        "strong_relevance_for_current": sorted(STRONG_RELEVANCE_CLASSES),
        "historical_lifecycle": sorted(HISTORICAL_LIFECYCLE_CLASSES),
        "uncertain_lifecycle": sorted(UNCERTAIN_LIFECYCLE_CLASSES),
        "current_currentness": sorted(CURRENT_CURRENTNESS_CLASSES),
        "rules": {
            "rejected": (
                "Live-backed AND relevance_class ∈ NEGATIVE_RELEVANCE_CLASSES "
                "(service/maintenance, rental/comodato, consumable, false-positive, unrelated)."
            ),
            "historical_market_signal": (
                "Live-backed AND relevance in product-fit set AND lifecycle ∈ "
                "{closed, awarded, cancelled}."
            ),
            "current_opportunity": (
                "Live-backed AND lifecycle=active_open AND currentness="
                "current_authoritative_snapshot AND relevance ∈ STRONG_RELEVANCE_CLASSES "
                "AND evidence_tier is not unresolved-only."
            ),
            "needs_review": (
                "All other live-backed rows (ambiguous, uncertain lifecycle, stale "
                "currentness, laboratory_context_only, incomplete evidence, conflicts)."
            ),
            "unknown_enum_fail_closed": True,
            "never_current_solely_from": [
                "equipment_keyword",
                "legacy_equipment_queue_membership",
                "buyer_or_contact_presence",
                "prior_email",
                "product_relevant_but_closed",
            ],
        },
    }


def classify_commercial_bucket(
    *,
    candidate_source_kind: str,
    lifecycle_class: str,
    currentness_class: str,
    relevance_class: str,
    evidence_tier: str | None = None,
) -> tuple[str, str]:
    """Return (bucket, reason_code). Unknown enums fail closed → needs_review."""
    if candidate_source_kind not in LIVE_BACKED_SOURCE_KINDS:
        raise ValueError(
            f"classify_commercial_bucket requires live-backed source_kind, "
            f"got {candidate_source_kind!r}"
        )
    if relevance_class not in RELEVANCE_CLASSES:
        return "needs_review", f"unknown_relevance_class:{relevance_class}"
    if relevance_class in REJECT_RELEVANCE:
        return "rejected", f"negative_relevance:{relevance_class}"
    if (
        relevance_class in PRODUCT_FIT_RELEVANCE
        and lifecycle_class in HISTORICAL_LIFECYCLE_CLASSES
    ):
        return "historical_market_signal", f"historical_lifecycle:{lifecycle_class}"
    if (
        lifecycle_class == "active_open"
        and currentness_class in CURRENT_CURRENTNESS_CLASSES
        and relevance_class in STRONG_RELEVANCE_CLASSES
        and (evidence_tier or "") != "unresolved_only"
    ):
        return "current_opportunity", "active_open_strong_current_evidence"
    if lifecycle_class in UNCERTAIN_LIFECYCLE_CLASSES:
        return "needs_review", f"uncertain_lifecycle:{lifecycle_class}"
    if currentness_class not in CURRENT_CURRENTNESS_CLASSES:
        return "needs_review", f"stale_or_unverified_currentness:{currentness_class}"
    if relevance_class == "ambiguous":
        return "needs_review", "ambiguous_relevance"
    if relevance_class == "laboratory_context_only":
        return "needs_review", "laboratory_context_only"
    if lifecycle_class == "active_open" and relevance_class in STRONG_RELEVANCE_CLASSES:
        # active but evidence_tier unresolved or other soft block
        return "needs_review", "active_but_inadequate_evidence_tier"
    return "needs_review", "live_backed_default_review"


def reconcile_bucket_counts(
    rows: list[Mapping[str, Any]],
) -> dict[str, Any]:
    counts = {b: 0 for b in COMMERCIAL_BUCKETS}
    for row in rows:
        bucket = str(row.get("commercial_bucket") or "")
        if bucket not in counts:
            raise ValueError(f"unknown commercial_bucket in row: {bucket!r}")
        counts[bucket] += 1
    total = sum(counts.values())
    if total != len(rows):
        raise ValueError("bucket counts do not sum to review population")
    return {"by_bucket": counts, "live_backed_review_population": total, "ok": True}
