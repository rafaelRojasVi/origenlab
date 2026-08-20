"""Structured tender-annex result produced by the trusted OriginLab local worker.

The local worker performs ZIP validation, document extraction, OCR, and T1 on
the operator workstation. This transport contains structured evidence only:
raw ZIP bytes, local filesystem paths, and Mercado Público opaque tokens are
never part of the contract.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

LOCAL_TENDER_ANNEX_IMPORT_CONTRACT_VERSION = "local_tender_annex_import_v1"
_SHA256_PATTERN = r"^[0-9a-fA-F]{64}$"


class LocalAttachmentProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    original_filename: str
    safe_filename: str
    byte_count: int = Field(ge=0)
    sha256: str = Field(pattern=_SHA256_PATTERN)
    content_type: str
    detected_format: str
    outcome: str


class LocalAcquisitionProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tender_code: str
    acquisition_source: Literal["operator_complete_bundle"]
    acquired_at: str
    completeness_state: Literal["complete", "incomplete", "unknown"]
    completeness_reason: str
    operator_supplied: Literal[True]
    source_semantic_digest: str = Field(pattern=_SHA256_PATTERN)
    attachments: list[LocalAttachmentProvenance] = Field(
        default_factory=list,
        max_length=500,
    )


class LocalArchiveInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    zip_sha256: str = Field(pattern=_SHA256_PATTERN)
    attachments_discovered: int = Field(ge=0)
    attachments_downloaded: int = Field(ge=0)
    rejected_entries: list[str] = Field(default_factory=list, max_length=500)


class LocalTenderAnnexRaw(BaseModel):
    """Canonical result of build_operator_annex_bundle_preview()."""

    model_config = ConfigDict(extra="forbid")

    result: Literal["imported"]
    tender_code: str
    provenance: LocalAcquisitionProvenance
    archive: LocalArchiveInfo
    bundle_complete: bool
    incomplete_reason_codes: list[str] = Field(default_factory=list)
    terms: dict[str, Any]
    published: Literal[False]
    persisted: Literal[False]
    contact_authorization: Literal[False]
    outreach_authorization: Literal[False]


class TenderAnnexLocalImportRequest(BaseModel):
    """Versioned workstation -> API structured-result envelope."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["local_tender_annex_import_v1"]
    tender_code: str
    operator_declared_complete: bool = False
    raw: LocalTenderAnnexRaw


__all__ = [
    "LOCAL_TENDER_ANNEX_IMPORT_CONTRACT_VERSION",
    "LocalAcquisitionProvenance",
    "LocalArchiveInfo",
    "LocalAttachmentProvenance",
    "LocalTenderAnnexRaw",
    "TenderAnnexLocalImportRequest",
]
