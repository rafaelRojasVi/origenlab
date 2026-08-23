"""Commercial opportunity lifecycle API service (ARCH-2B)."""

from __future__ import annotations

from origenlab_api.backends.factory import RepositoryBundle, get_repository_bundle
from origenlab_api.schemas.commercial_opportunities import (
    CommercialOpportunitiesResponse,
    CommercialOpportunityDetailResponse,
)
from origenlab_api.settings import Settings


def build_commercial_opportunities_response(
    settings: Settings,
    *,
    repos: RepositoryBundle | None = None,
    limit: int = 50,
    offset: int = 0,
    canonical_stage: str | None = None,
    record_kind: str | None = None,
    review_status: str | None = None,
    account_id: str | None = None,
    primary_contact_id: str | None = None,
) -> CommercialOpportunitiesResponse:
    bundle = repos or get_repository_bundle(settings)

    items, meta = bundle.commercial_opportunity.list_commercial(
        limit=limit,
        offset=offset,
        canonical_stage=canonical_stage,
        record_kind=record_kind,
        review_status=review_status,
        account_id=account_id,
        primary_contact_id=primary_contact_id,
    )

    return CommercialOpportunitiesResponse(meta=meta, items=items)


def build_commercial_opportunity_detail_response(
    settings: Settings,
    opportunity_id: str,
    *,
    repos: RepositoryBundle | None = None,
) -> CommercialOpportunityDetailResponse | None:
    bundle = repos or get_repository_bundle(settings)
    return bundle.commercial_opportunity.get_commercial_detail(opportunity_id)
