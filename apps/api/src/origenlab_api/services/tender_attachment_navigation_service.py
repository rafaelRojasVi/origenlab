"""Ephemeral Mercado Público attachment-navigation service.

This service gates navigation on W1 ``current_opportunity_queue`` membership,
then resolves a current Mercado Público destination in memory.

The returned URL is never persisted or published by this service.
"""

from __future__ import annotations

from dataclasses import dataclass

from origenlab_email_pipeline.chilecompra_api import (
    resolve_licitacion_attachment_navigation,
)

from origenlab_api.repositories.institution_prospects import list_queue_rows
from origenlab_api.schemas.tender_attachment_navigation import (
    TenderAttachmentNavigationResponse,
)
from origenlab_api.settings import Settings

_MAX_QUEUE_ROWS_PER_TENDER = 200


@dataclass(frozen=True)
class TenderAttachmentNavigationOutcome:
    """Tagged result so the route owns HTTP status semantics."""

    w1_healthy: bool
    found_in_queue: bool
    response: TenderAttachmentNavigationResponse | None


def build_tender_attachment_navigation(
    settings: Settings,
    tender_code: str,
) -> TenderAttachmentNavigationOutcome:
    """Resolve one ephemeral navigation target after W1 authorization."""

    rows, w1_meta, _total = list_queue_rows(
        settings.resolved_institution_prospect_dir(),
        "current_opportunity",
        limit=_MAX_QUEUE_ROWS_PER_TENDER,
        offset=0,
        tender_code=tender_code,
    )

    w1_healthy = not w1_meta.reduced_mode
    found_in_queue = bool(rows) and w1_healthy

    if not w1_healthy or not found_in_queue:
        return TenderAttachmentNavigationOutcome(
            w1_healthy=w1_healthy,
            found_in_queue=found_in_queue,
            response=None,
        )

    destination = resolve_licitacion_attachment_navigation(tender_code)

    return TenderAttachmentNavigationOutcome(
        w1_healthy=True,
        found_in_queue=True,
        response=TenderAttachmentNavigationResponse(
            tender_code=tender_code,
            destination_kind=destination.destination_kind,
            url=destination.url,
            ephemeral=True,
        ),
    )


__all__ = [
    "TenderAttachmentNavigationOutcome",
    "build_tender_attachment_navigation",
]
