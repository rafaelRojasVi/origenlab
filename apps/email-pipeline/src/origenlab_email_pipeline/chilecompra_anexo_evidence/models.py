"""Deterministic records for the ChileCompra anexo evidence layer.

Every discovered attachment row gets exactly one :class:`AttachmentRecord` with
exactly one outcome, so a bundle can always distinguish "no equipment found"
from "we did not manage to read everything".
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from origenlab_email_pipeline.chilecompra_anexo_evidence.constants import (
    ANEXO_EVIDENCE_ATTACHMENT_ID_ALGORITHM,
    ANEXO_EVIDENCE_CHUNK_ID_ALGORITHM,
    COMPLETE_OUTCOMES,
)


def _sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_bytes(payload: bytes) -> str:
    """Content hash used for cache addressing and duplicate detection."""
    return _sha256_hex(payload)


def sha256_text(text: str) -> str:
    return _sha256_hex(text.encode("utf-8"))


def build_attachment_id(tender_id: str, listing_ordinal: int, row_ordinal: int) -> str:
    """Stable ID from portal position only, so filesystem order cannot change it."""
    seed = f"{ANEXO_EVIDENCE_ATTACHMENT_ID_ALGORITHM}|{tender_id}|{listing_ordinal}|{row_ordinal}"
    return _sha256_hex(seed.encode("utf-8"))[:24]


def build_chunk_id(attachment_id: str, member_path: str, locator_key: str, ordinal: int) -> str:
    seed = (
        f"{ANEXO_EVIDENCE_CHUNK_ID_ALGORITHM}|{attachment_id}|{member_path}"
        f"|{locator_key}|{ordinal}"
    )
    return _sha256_hex(seed.encode("utf-8"))[:24]


@dataclass(frozen=True)
class EvidenceChunk:
    """One bounded, addressable piece of extracted text."""

    chunk_id: str
    attachment_id: str
    tender_id: str
    source_format: str
    locator_type: str
    locator: dict[str, Any]
    text: str
    text_sha256: str
    char_count: int
    ordinal: int
    container_attachment_id: str | None = None
    member_path: str | None = None
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "attachment_id": self.attachment_id,
            "tender_id": self.tender_id,
            "container_attachment_id": self.container_attachment_id,
            "member_path": self.member_path,
            "source_format": self.source_format,
            "locator_type": self.locator_type,
            "locator": dict(self.locator),
            "text": self.text,
            "text_sha256": self.text_sha256,
            "char_count": self.char_count,
            "ordinal": self.ordinal,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class AttachmentExtraction:
    """Outcome of reading one attachment (or one archive member)."""

    outcome: str
    detected_format: str
    chunk_count: int
    char_count: int
    warnings: tuple[str, ...] = ()
    page_count: int | None = None
    sheet_count: int | None = None
    row_count: int | None = None
    extractor: str | None = None
    error_message: str | None = None

    @property
    def complete(self) -> bool:
        return self.outcome in COMPLETE_OUTCOMES

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "detected_format": self.detected_format,
            "chunk_count": self.chunk_count,
            "char_count": self.char_count,
            "warnings": list(self.warnings),
            "page_count": self.page_count,
            "sheet_count": self.sheet_count,
            "row_count": self.row_count,
            "extractor": self.extractor,
            "error_message": self.error_message,
        }


@dataclass(frozen=True)
class ArchiveMemberRecord:
    """One member inside a ZIP-like attachment; provenance is never flattened."""

    member_id: str
    container_attachment_id: str
    tender_id: str
    member_path: str
    member_sha256: str
    byte_count: int
    depth: int
    detected_format: str
    outcome: str
    warnings: tuple[str, ...] = ()
    duplicate_of_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "member_id": self.member_id,
            "container_attachment_id": self.container_attachment_id,
            "tender_id": self.tender_id,
            "member_path": self.member_path,
            "member_sha256": self.member_sha256,
            "byte_count": self.byte_count,
            "depth": self.depth,
            "detected_format": self.detected_format,
            "outcome": self.outcome,
            "warnings": list(self.warnings),
            "duplicate_of_sha256": self.duplicate_of_sha256,
        }


@dataclass(frozen=True)
class AttachmentRecord:
    """Provenance + outcome for exactly one portal attachment row."""

    attachment_id: str
    tender_id: str
    listing_ordinal: int
    row_ordinal: int
    portal_filename: str
    safe_filename: str
    tipo: str
    descripcion: str
    fecha_adjunto: str
    content_type: str
    detected_format: str
    extension: str
    byte_count: int
    sha256: str
    download_status: str
    outcome: str
    role_tag: str
    warnings: tuple[str, ...] = ()
    duplicate_of_attachment_id: str | None = None
    duplicate_kind: str | None = None
    # Structured extraction provenance: downstream resolves evidence by joining
    # chunks on ``evidence_attachment_id`` instead of parsing warning strings.
    extraction_reused_from_attachment_id: str | None = None
    evidence_attachment_id: str | None = None
    extraction: AttachmentExtraction | None = None
    archive_members: tuple[ArchiveMemberRecord, ...] = ()
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "attachment_id": self.attachment_id,
            "tender_id": self.tender_id,
            "listing_ordinal": self.listing_ordinal,
            "row_ordinal": self.row_ordinal,
            "portal_filename": self.portal_filename,
            "safe_filename": self.safe_filename,
            "tipo": self.tipo,
            "descripcion": self.descripcion,
            "fecha_adjunto": self.fecha_adjunto,
            "content_type": self.content_type,
            "detected_format": self.detected_format,
            "extension": self.extension,
            "byte_count": self.byte_count,
            "sha256": self.sha256,
            "download_status": self.download_status,
            "outcome": self.outcome,
            "role_tag": self.role_tag,
            "warnings": list(self.warnings),
            "duplicate_of_attachment_id": self.duplicate_of_attachment_id,
            "duplicate_kind": self.duplicate_kind,
            "extraction_reused_from_attachment_id": self.extraction_reused_from_attachment_id,
            "evidence_attachment_id": self.evidence_attachment_id,
            "extraction": self.extraction.to_dict() if self.extraction else None,
            "archive_members": [m.to_dict() for m in self.archive_members],
            "error_message": self.error_message,
        }


@dataclass(frozen=True)
class TenderAttachmentBundle:
    """Everything we know about one tender's anexos, including what we could not read."""

    tender_id: str
    source_kind: str
    listing_page_count: int
    attachments_discovered: int
    attachments_downloaded: int
    attachments: tuple[AttachmentRecord, ...]
    chunks: tuple[EvidenceChunk, ...]
    bytes_downloaded: int
    incomplete_reason_codes: tuple[str, ...] = ()
    semantic_digest: str = ""
    outcome_counts: dict[str, int] = field(default_factory=dict)
    # The exact gated attachment-view URL the source's ficha advertised, if
    # any (see acquire.SourceInventory.gated_attachment_navigation_url) --
    # pure navigation metadata for a human operator, never requested by this
    # codebase and never part of the bundle's semantic digest.
    gated_attachment_navigation_url: str | None = None

    @property
    def attachments_extraction_success(self) -> int:
        return self.outcome_counts.get("extraction_success", 0)

    @property
    def attachments_empty(self) -> int:
        return self.outcome_counts.get("extracted_empty", 0)

    @property
    def attachments_needs_ocr(self) -> int:
        return self.outcome_counts.get("needs_ocr", 0)

    @property
    def attachments_needs_converter(self) -> int:
        return self.outcome_counts.get("needs_converter", 0)

    @property
    def attachments_unsupported(self) -> int:
        return self.outcome_counts.get("unsupported_format", 0)

    @property
    def attachments_partial(self) -> int:
        return self.outcome_counts.get("partial_due_to_safety_limit", 0)

    @property
    def attachments_failed(self) -> int:
        return (
            self.outcome_counts.get("download_failed", 0)
            + self.outcome_counts.get("extraction_failed", 0)
            + self.outcome_counts.get("corrupt", 0)
            + self.outcome_counts.get("encrypted", 0)
        )

    @property
    def download_complete(self) -> bool:
        return (
            self.attachments_downloaded == self.attachments_discovered
            and not any(
                code.endswith("_budget_exceeded") for code in self.incomplete_reason_codes
            )
        )

    @property
    def extraction_complete(self) -> bool:
        if not self.download_complete:
            return False
        if self.incomplete_reason_codes:
            return False
        return all(
            record.outcome in COMPLETE_OUTCOMES for record in self.attachments
        )

    @property
    def bundle_complete(self) -> bool:
        return self.download_complete and self.extraction_complete

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "tender_id": self.tender_id,
            "source_kind": self.source_kind,
            "listing_page_count": self.listing_page_count,
            "attachments_discovered": self.attachments_discovered,
            "attachments_downloaded": self.attachments_downloaded,
            "attachments_extraction_success": self.attachments_extraction_success,
            "attachments_empty": self.attachments_empty,
            "attachments_needs_ocr": self.attachments_needs_ocr,
            "attachments_needs_converter": self.attachments_needs_converter,
            "attachments_unsupported": self.attachments_unsupported,
            "attachments_failed": self.attachments_failed,
            "attachments_partial": self.attachments_partial,
            "archive_children": sum(len(a.archive_members) for a in self.attachments),
            "chunk_count": len(self.chunks),
            "bytes_downloaded": self.bytes_downloaded,
            "download_complete": self.download_complete,
            "extraction_complete": self.extraction_complete,
            "bundle_complete": self.bundle_complete,
            "incomplete_reason_codes": list(self.incomplete_reason_codes),
            "outcome_counts": dict(sorted(self.outcome_counts.items())),
            "semantic_digest": self.semantic_digest,
        }
