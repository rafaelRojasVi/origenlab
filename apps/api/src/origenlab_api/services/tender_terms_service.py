"""Tender-detail service: merges W1 actionability with T1 term intelligence.

W1's ``current_opportunity_queue`` read model is the sole actionability
authority. This service looks the tender up there first; T1 published terms
are only ever attached as additive detail on top of that lookup and can
never, by themselves, cause a tender to be treated as actionable.
"""

from __future__ import annotations

from origenlab_api.path_redaction import redact_mapping_path_fields
from origenlab_api.repositories.institution_prospects import list_queue_rows
from origenlab_api.repositories.tender_terms import get_tender_terms
from origenlab_api.schemas.institution_prospects import InstitutionProspectMeta
from origenlab_api.schemas.tender_terms import (
    TenderDetailResponse,
    TenderQueueRow,
    TenderTermsMeta,
)
from origenlab_api.settings import Settings

# W1 rows can never exceed the number of (institution, tender_code,
# equipment_category) combinations for one tender; this is a generous cap,
# not a real pagination limit — a single tender legitimately has more than
# one category row (e.g. balance + centrifuge on the same tender_code).
_MAX_QUEUE_ROWS_PER_TENDER = 200


def _redacted_queue_meta(meta: InstitutionProspectMeta) -> dict:
    return redact_mapping_path_fields(meta.model_dump())


def _redacted_t1_meta(meta: TenderTermsMeta) -> TenderTermsMeta:
    return TenderTermsMeta.model_validate(redact_mapping_path_fields(meta.model_dump()))


def build_tender_detail_response(settings: Settings, tender_code: str) -> TenderDetailResponse:
    w1_dir = settings.resolved_institution_prospect_dir()
    rows, w1_meta, _total = list_queue_rows(
        w1_dir,
        "current_opportunity",
        limit=_MAX_QUEUE_ROWS_PER_TENDER,
        offset=0,
        tender_code=tender_code,
    )

    # --- Single enforcement point for the W1 actionability gate ---
    #
    # T1 detail is only ever attached when W1 has *positively* established
    # current-opportunity membership for this tender_code: at least one
    # matching row was found, AND the W1 lookup itself was healthy (not
    # reduced_mode). A degraded/unavailable W1 feed must never be treated as
    # "tender confirmed absent" nor as "tender confirmed actionable" — T1
    # detail stays withheld either way until W1 is healthy again.
    w1_healthy = not w1_meta.reduced_mode
    found_in_queue = bool(rows) and w1_healthy
    w1_established_actionable = found_in_queue

    queue_row = rows[0] if rows else None
    queue_rows = [TenderQueueRow.from_row(row) for row in rows] if w1_established_actionable else []

    if w1_established_actionable:
        t1_dir = settings.resolved_tender_terms_dir()
        t1_bundle, t1_meta = get_tender_terms(t1_dir, tender_code)
    else:
        # W1 has not established this tender as actionable (either genuinely
        # absent from a healthy queue, or W1 itself is degraded). T1 facts
        # are never surfaced in either case.
        t1_bundle, t1_meta = None, TenderTermsMeta(
            canonical_reason="w1_actionability_not_established",
            reduced_mode=not w1_healthy,
            note=(
                "W1 current_opportunity_queue lookup unhealthy; T1 detail withheld."
                if not w1_healthy
                else "Tender not present in W1 current_opportunity_queue; T1 detail withheld."
            ),
        )

    tender_facts: list = []
    items: list = []
    coverage: dict | None = None
    t1_published = t1_bundle is not None
    if t1_bundle is not None:
        tender_facts = list(t1_bundle.get("tender_facts") or [])
        items = list(t1_bundle.get("items") or [])
        coverage = t1_bundle.get("coverage")

    return TenderDetailResponse(
        tender_code=tender_code,
        queue_meta=_redacted_queue_meta(w1_meta),
        found_in_queue=found_in_queue,
        queue_row=queue_row,
        queue_rows=queue_rows,
        t1_meta=_redacted_t1_meta(t1_meta),
        t1_published=t1_published,
        tender_facts=tender_facts,
        items=items,
        coverage=coverage,
    )


__all__ = ["build_tender_detail_response"]
