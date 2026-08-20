"""Persist a tender-annex result already processed by OriginLab Local.

This path never receives or processes ZIP bytes and never runs OCR. The local
workstation produces the canonical structured result; the API independently
revalidates its identity/safety/T1 contract before using the existing atomic
operator-import persistence boundary.
"""

from __future__ import annotations

import json

from origenlab_email_pipeline.chilecompra_anexo_evidence.redaction import (
    assert_no_portal_tokens,
)
from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms import (
    validate_tender_terms_row,
)

from origenlab_api.repositories.operator_tender_imports import (
    save_operator_tender_import,
)
from origenlab_api.schemas.tender_annex_local_import import (
    TenderAnnexLocalImportRequest,
)
from origenlab_api.schemas.tender_annex_preview import (
    TenderAnnexAcquisitionInfo,
    TenderAnnexArchiveInfo,
    TenderAnnexBundleImportResponse,
)
from origenlab_api.services.tender_annex_import_service import (
    TenderAnnexImportOutcome,
)
from origenlab_api.services.tender_terms_service import (
    build_tender_detail_response,
)
from origenlab_api.settings import Settings


def _canonical_code(value: str) -> str:
    return value.strip().casefold()


def _validated_raw(
    tender_code: str,
    request: TenderAnnexLocalImportRequest,
) -> tuple[dict, TenderAnnexBundleImportResponse]:
    expected = _canonical_code(tender_code)
    raw = request.raw

    if _canonical_code(request.tender_code) != expected:
        raise ValueError("request tender_code does not match route")
    if _canonical_code(raw.tender_code) != expected:
        raise ValueError("raw tender_code does not match route")
    if _canonical_code(raw.provenance.tender_code) != expected:
        raise ValueError("provenance tender_code does not match route")

    archive = raw.archive
    provenance = raw.provenance

    if archive.attachments_downloaded > archive.attachments_discovered:
        raise ValueError("downloaded attachment count exceeds discovered count")
    if archive.attachments_downloaded != len(provenance.attachments):
        raise ValueError("attachment provenance count does not match downloaded count")

    if raw.bundle_complete:
        expected_completeness = "complete"
    elif request.operator_declared_complete:
        expected_completeness = "incomplete"
    else:
        expected_completeness = "unknown"

    if provenance.completeness_state != expected_completeness:
        raise ValueError(
            "provenance completeness state does not match local import declaration"
        )

    raw_dict = raw.model_dump(mode="json")

    # Revalidate the critical T1 contract independently of the workstation.
    terms = validate_tender_terms_row(raw_dict["terms"])
    if _canonical_code(str(terms.get("tender_id") or "")) != expected:
        raise ValueError("T1 tender_id does not match route")

    # The outer acquisition/archive envelope and the independently validated
    # T1 row must describe the exact same canonical source bundle. Otherwise
    # a malformed or tampered local result could persist internally
    # inconsistent provenance even though the T1 row itself is valid.
    if (
        provenance.source_semantic_digest.casefold()
        != str(terms["source_bundle_semantic_digest"]).casefold()
    ):
        raise ValueError("provenance semantic digest does not match T1 source bundle")

    coverage = terms["coverage"]

    if archive.attachments_discovered != coverage["attachments_discovered"]:
        raise ValueError("archive discovered count does not match T1 coverage")

    if archive.attachments_downloaded != coverage["attachments_downloaded"]:
        raise ValueError("archive downloaded count does not match T1 coverage")

    if list(raw.incomplete_reason_codes) != list(coverage["incomplete_reason_codes"]):
        raise ValueError("bundle incomplete reasons do not match T1 coverage")

    if raw.bundle_complete != coverage["is_complete"]:
        raise ValueError("bundle completeness does not match T1 coverage")

    # No opaque Mercado Público token may cross the persistence boundary.
    assert_no_portal_tokens(
        json.dumps(raw_dict, ensure_ascii=True, sort_keys=True),
        where="local_tender_annex_import",
    )

    response = TenderAnnexBundleImportResponse(
        result="imported",
        tender_code=tender_code,
        acquisition=TenderAnnexAcquisitionInfo(
            source=provenance.acquisition_source,
            completeness_state=provenance.completeness_state,
            completeness_reason=provenance.completeness_reason,
            operator_declared_complete=request.operator_declared_complete,
        ),
        archive=TenderAnnexArchiveInfo(
            sha256=archive.zip_sha256,
            attachments_discovered=archive.attachments_discovered,
            attachments_downloaded=archive.attachments_downloaded,
            rejected_entries=list(archive.rejected_entries),
        ),
        bundle_complete=raw.bundle_complete,
        incomplete_reason_codes=list(raw.incomplete_reason_codes),
        coverage=terms.get("coverage"),
        tender_facts=terms.get("tender_facts") or [],
        items=terms.get("items") or [],
        published=True,
        persisted=True,
        contact_authorization=False,
        outreach_authorization=False,
    )

    return raw_dict, response


def build_tender_annex_local_import(
    settings: Settings,
    tender_code: str,
    request: TenderAnnexLocalImportRequest,
) -> TenderAnnexImportOutcome:
    """Validate + persist locally computed evidence without server-side OCR."""

    # Reuse the merged W1+T1 tender-detail service as the actionability gate.
    # Local processing may never make a non-W1 tender actionable.
    detail = build_tender_detail_response(settings, tender_code)
    w1_healthy = not bool(detail.queue_meta.get("reduced_mode"))
    found_in_queue = bool(detail.found_in_queue) and w1_healthy

    if not w1_healthy or not found_in_queue:
        return TenderAnnexImportOutcome(
            w1_healthy=w1_healthy,
            found_in_queue=found_in_queue,
            rejected=False,
            persistence_failed=False,
            error=None,
            response=None,
        )

    try:
        raw, response = _validated_raw(tender_code, request)
    except Exception:  # noqa: BLE001 - untrusted local transport fails closed
        return TenderAnnexImportOutcome(
            w1_healthy=True,
            found_in_queue=True,
            rejected=True,
            persistence_failed=False,
            error="Invalid structured local annex import",
            response=None,
        )

    try:
        save_operator_tender_import(
            settings.resolved_operator_tender_import_dir(),
            tender_code,
            raw,
        )
    except Exception as exc:  # noqa: BLE001 - persistence boundary fails closed
        return TenderAnnexImportOutcome(
            w1_healthy=True,
            found_in_queue=True,
            rejected=False,
            persistence_failed=True,
            error=f"{type(exc).__name__}: persistence failed",
            response=None,
        )

    return TenderAnnexImportOutcome(
        w1_healthy=True,
        found_in_queue=True,
        rejected=False,
        persistence_failed=False,
        error=None,
        response=response,
    )


__all__ = ["build_tender_annex_local_import"]
