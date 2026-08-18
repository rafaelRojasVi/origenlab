"""Explicit persistent operator annex import.

Preview remains non-mutating. This service is the narrow mutation boundary:
W1-gated ZIP validation/extraction followed by one atomic per-tender save.
No DB, Gmail, contact, or outreach state is touched.
"""

from __future__ import annotations

from dataclasses import dataclass

from origenlab_api.repositories.operator_tender_imports import (
    save_operator_tender_import,
)
from origenlab_api.schemas.tender_annex_preview import (
    TenderAnnexBundleImportResponse,
)
from origenlab_api.services.tender_annex_preview_service import (
    build_tender_annex_bundle_preview,
)
from origenlab_api.settings import Settings


@dataclass(frozen=True)
class TenderAnnexImportOutcome:
    w1_healthy: bool
    found_in_queue: bool
    rejected: bool
    persistence_failed: bool
    error: str | None
    response: TenderAnnexBundleImportResponse | None


def build_tender_annex_bundle_import(
    settings: Settings,
    tender_code: str,
    zip_bytes: bytes,
    *,
    declare_complete: bool,
) -> TenderAnnexImportOutcome:
    preview = build_tender_annex_bundle_preview(
        settings,
        tender_code,
        zip_bytes,
        declare_complete=declare_complete,
    )

    if not preview.w1_healthy or not preview.found_in_queue or preview.rejected:
        return TenderAnnexImportOutcome(
            w1_healthy=preview.w1_healthy,
            found_in_queue=preview.found_in_queue,
            rejected=preview.rejected,
            persistence_failed=False,
            error=preview.error,
            response=None,
        )

    assert preview.response is not None
    assert preview.raw is not None

    try:
        save_operator_tender_import(
            settings.resolved_operator_tender_import_dir(),
            tender_code,
            preview.raw,
        )
    except Exception as exc:  # noqa: BLE001 - mutation boundary fails closed
        return TenderAnnexImportOutcome(
            w1_healthy=True,
            found_in_queue=True,
            rejected=False,
            persistence_failed=True,
            error=f"{type(exc).__name__}: persistence failed",
            response=None,
        )

    response = TenderAnnexBundleImportResponse.model_validate(
        {
            **preview.response.model_dump(),
            "published": True,
            "persisted": True,
            "contact_authorization": False,
            "outreach_authorization": False,
        }
    )
    return TenderAnnexImportOutcome(
        w1_healthy=True,
        found_in_queue=True,
        rejected=False,
        persistence_failed=False,
        error=None,
        response=response,
    )


__all__ = [
    "TenderAnnexImportOutcome",
    "build_tender_annex_bundle_import",
]
