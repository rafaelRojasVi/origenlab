"""Operator annex-bundle upload PREVIEW service.

Gates on the same W1 ``current_opportunity_queue`` authority
``tender_terms_service.build_tender_detail_response`` uses (never
duplicated/reimplemented here) before doing any ZIP processing, so uploaded
T1 evidence can never make a tender "actionable" on its own and never gets
processed for a tender_code W1 does not recognize. All ZIP validation,
extraction, and T1 term-building is delegated to
``origenlab_email_pipeline.chilecompra_anexo_evidence.operator_import`` /
``commercial_procurement_anexo_tender_terms.operator_annex_bundle_preview``
(the exact same #493 domain seam the CLI uses) -- this module only adapts
between that domain result and the API's read-only HTTP contract.

Never publishes, never persists, never authorizes contact/outreach.
"""

from __future__ import annotations

from dataclasses import dataclass

from origenlab_email_pipeline.chilecompra_anexo_evidence.operator_import import (
    OperatorZipAttachmentSource,
)
from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms.operator_annex_bundle_preview import (
    build_operator_annex_bundle_preview,
)

from origenlab_api.repositories.institution_prospects import list_queue_rows
from origenlab_api.schemas.tender_annex_preview import (
    TenderAnnexAcquisitionInfo,
    TenderAnnexArchiveInfo,
    TenderAnnexBundlePreviewResponse,
)
from origenlab_api.settings import Settings

# One tender legitimately has more than one current_opportunity_queue row
# (e.g. balance + centrifuge on the same tender_code); this only needs to
# confirm *membership*, so a small cap is enough -- see
# tender_terms_service._MAX_QUEUE_ROWS_PER_TENDER for the identical rationale.
_MAX_QUEUE_ROWS_PER_TENDER = 200


@dataclass(frozen=True)
class TenderAnnexPreviewOutcome:
    """Tagged result so the route (not this service) decides HTTP status.

    Exactly one of the failure flags is meaningful at a time, checked by the
    route in this order: ``w1_healthy`` first (a degraded W1 feed must fail
    closed regardless of what the ZIP contains), then ``found_in_queue``,
    then ``rejected``.
    """

    w1_healthy: bool
    found_in_queue: bool
    rejected: bool
    error: str | None
    response: TenderAnnexBundlePreviewResponse | None


def _to_response(
    tender_code: str, raw: dict, *, operator_declared_complete: bool
) -> TenderAnnexBundlePreviewResponse:
    provenance = raw["provenance"]
    archive = raw["archive"]
    terms = raw["terms"]
    return TenderAnnexBundlePreviewResponse(
        result="imported",
        tender_code=tender_code,
        acquisition=TenderAnnexAcquisitionInfo(
            source=provenance["acquisition_source"],
            completeness_state=provenance["completeness_state"],
            completeness_reason=provenance["completeness_reason"],
            # The bare operator assertion this run was constructed with --
            # distinct from provenance["operator_supplied"], which only means
            # "this acquisition source is operator-supplied" and is True
            # regardless of whether completeness was declared.
            operator_declared_complete=operator_declared_complete,
        ),
        archive=TenderAnnexArchiveInfo(
            sha256=archive["zip_sha256"],
            attachments_discovered=archive["attachments_discovered"],
            attachments_downloaded=archive["attachments_downloaded"],
            rejected_entries=list(archive["rejected_entries"]),
        ),
        bundle_complete=raw["bundle_complete"],
        incomplete_reason_codes=list(raw["incomplete_reason_codes"]),
        coverage=terms["coverage"],
        tender_facts=terms["tender_facts"],
        items=terms["items"],
        published=False,
        persisted=False,
        contact_authorization=False,
        outreach_authorization=False,
    )


def build_tender_annex_bundle_preview(
    settings: Settings,
    tender_code: str,
    zip_bytes: bytes,
    *,
    declare_complete: bool,
) -> TenderAnnexPreviewOutcome:
    w1_dir = settings.resolved_institution_prospect_dir()
    rows, w1_meta, _total = list_queue_rows(
        w1_dir,
        "current_opportunity",
        limit=_MAX_QUEUE_ROWS_PER_TENDER,
        offset=0,
        tender_code=tender_code,
    )

    # --- Single enforcement point for the W1 actionability gate (mirrors
    # tender_terms_service.build_tender_detail_response exactly) ---
    w1_healthy = not w1_meta.reduced_mode
    found_in_queue = bool(rows) and w1_healthy

    if not w1_healthy or not found_in_queue:
        return TenderAnnexPreviewOutcome(
            w1_healthy=w1_healthy,
            found_in_queue=found_in_queue,
            rejected=False,
            error=None,
            response=None,
        )

    source = OperatorZipAttachmentSource.from_bytes(
        zip_bytes,
        tender_code=tender_code,
        declare_complete=declare_complete,
    )
    raw = build_operator_annex_bundle_preview(source, tender_code=tender_code)

    if raw["result"] == "rejected":
        return TenderAnnexPreviewOutcome(
            w1_healthy=True,
            found_in_queue=True,
            rejected=True,
            error=raw.get("error") or "The uploaded ZIP was rejected.",
            response=None,
        )

    return TenderAnnexPreviewOutcome(
        w1_healthy=True,
        found_in_queue=True,
        rejected=False,
        error=None,
        response=_to_response(tender_code, raw, operator_declared_complete=declare_complete),
    )


__all__ = ["TenderAnnexPreviewOutcome", "build_tender_annex_bundle_preview"]
