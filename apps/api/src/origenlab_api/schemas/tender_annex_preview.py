"""Operator annex-bundle upload PREVIEW (read/compute-only, never persisted).

Reuses the exact same T1 structures (`TenderTermFact` / `TenderItemTerms` /
`TenderTermsCoverage`) published `tender_terms.py` already defines -- a
preview fact and a published fact are the same shape; only the wrapping
envelope (acquisition/archive provenance, `published=False`) differs. See
``services/tender_annex_preview_service.py`` and
``origenlab_email_pipeline.commercial_procurement_anexo_tender_terms.operator_annex_bundle_preview``
for where this data actually comes from.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from origenlab_api.schemas.tender_terms import (
    TenderItemTerms,
    TenderTermFact,
    TenderTermsCoverage,
)


class TenderAnnexAcquisitionInfo(BaseModel):
    """How this preview's bytes arrived, and how completely -- never inferred."""

    source: str = ""
    completeness_state: str = "unknown"
    completeness_reason: str = ""
    operator_declared_complete: bool = False


class TenderAnnexArchiveInfo(BaseModel):
    """ZIP-level facts. Deliberately no filesystem path anywhere in here."""

    sha256: str = ""
    attachments_discovered: int = 0
    attachments_downloaded: int = 0
    rejected_entries: list[str] = Field(default_factory=list)


class TenderAnnexBundlePreviewResponse(BaseModel):
    """Full structured preview of one operator-uploaded annex ZIP.

    ``published`` / ``persisted`` / ``contact_authorization`` /
    ``outreach_authorization`` are always ``False`` -- this endpoint computes
    a preview only; it never writes to disk/DB and never authorizes contact
    or outreach. Promotion to a real published tender-terms bundle is a
    separate, explicit operation (not part of this route).
    """

    result: str = "imported"
    tender_code: str = ""
    acquisition: TenderAnnexAcquisitionInfo = Field(
        default_factory=TenderAnnexAcquisitionInfo
    )
    archive: TenderAnnexArchiveInfo = Field(default_factory=TenderAnnexArchiveInfo)
    bundle_complete: bool = False
    incomplete_reason_codes: list[str] = Field(default_factory=list)
    coverage: TenderTermsCoverage | None = None
    tender_facts: list[TenderTermFact] = Field(default_factory=list)
    items: list[TenderItemTerms] = Field(default_factory=list)
    published: bool = False
    persisted: bool = False
    contact_authorization: bool = False
    outreach_authorization: bool = False


class TenderAnnexBundleImportResponse(TenderAnnexBundlePreviewResponse):
    """Explicit saved operator import.

    Same commercial-intelligence payload as preview, but ``published`` and
    ``persisted`` are true because this response is returned only after the
    per-tender overlay has been atomically written and read back.
    """

    published: bool = True
    persisted: bool = True


__all__ = [
    "TenderAnnexAcquisitionInfo",
    "TenderAnnexArchiveInfo",
    "TenderAnnexBundleImportResponse",
    "TenderAnnexBundlePreviewResponse",
]
