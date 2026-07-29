"""Centralized review_status derivation for commercial opportunities (PR3)."""

from __future__ import annotations

from origenlab_email_pipeline.commercial_opportunity.constants import (
    REVIEW_REQUIRING_CONFLICT_REASONS,
    REVIEW_STATUS_OK,
    REVIEW_STATUS_REQUIRED,
)
from origenlab_email_pipeline.commercial_opportunity.models import OpportunityConflictRecord


def derive_opportunity_review_status(
    *,
    opportunity_id: str,
    conflicts: list[OpportunityConflictRecord],
    deal_status: str | None = None,
) -> str:
    """Derive review_status from attached conflicts + deal_status.

    Any attached conflict whose reason is in ``REVIEW_REQUIRING_CONFLICT_REASONS``
    forces ``needs_review``. ``deal_status=needs_review`` also forces review even
    without a conflict row. Opportunities without review blockers remain ``ok``.
    """
    for conflict in conflicts:
        if conflict.opportunity_id != opportunity_id:
            continue
        if conflict.reason_code in REVIEW_REQUIRING_CONFLICT_REASONS:
            return REVIEW_STATUS_REQUIRED
    if (deal_status or "").strip().lower() == "needs_review":
        return REVIEW_STATUS_REQUIRED
    return REVIEW_STATUS_OK
