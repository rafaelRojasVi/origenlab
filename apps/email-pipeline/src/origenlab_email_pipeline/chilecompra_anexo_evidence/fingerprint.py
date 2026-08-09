"""Deterministic semantic digest for an anexo evidence bundle.

The digest covers provenance and outcomes, plus chunk text hashes rather than
chunk text, so it is stable, order-independent, and safe to share.
"""

from __future__ import annotations

from typing import Iterable

from origenlab_email_pipeline.chilecompra_anexo_evidence.constants import (
    ANEXO_EVIDENCE_BUILDER_VERSION,
    ANEXO_EVIDENCE_EXTRACTOR_VERSION,
    ANEXO_EVIDENCE_SEMANTIC_DIGEST_ALGORITHM,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.models import (
    AttachmentRecord,
    EvidenceChunk,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
)


def attachment_semantic_payload(record: AttachmentRecord) -> dict[str, object]:
    return {
        "attachment_id": record.attachment_id,
        "listing_ordinal": record.listing_ordinal,
        "row_ordinal": record.row_ordinal,
        "safe_filename": record.safe_filename,
        "detected_format": record.detected_format,
        "byte_count": record.byte_count,
        "sha256": record.sha256,
        "download_status": record.download_status,
        "outcome": record.outcome,
        "role_tag": record.role_tag,
        "duplicate_of_attachment_id": record.duplicate_of_attachment_id,
        "duplicate_kind": record.duplicate_kind,
        "extraction_reused_from_attachment_id": record.extraction_reused_from_attachment_id,
        "evidence_attachment_id": record.evidence_attachment_id,
        "warnings": sorted(record.warnings),
        "archive_members": sorted(
            (
                {
                    "member_path": member.member_path,
                    "member_sha256": member.member_sha256,
                    "detected_format": member.detected_format,
                    "outcome": member.outcome,
                    "depth": member.depth,
                }
                for member in record.archive_members
            ),
            key=lambda item: (item["member_path"], item["member_sha256"]),
        ),
    }


def chunk_semantic_payload(chunk: EvidenceChunk) -> dict[str, object]:
    return {
        "chunk_id": chunk.chunk_id,
        "attachment_id": chunk.attachment_id,
        "container_attachment_id": chunk.container_attachment_id,
        "member_path": chunk.member_path,
        "source_format": chunk.source_format,
        "locator_type": chunk.locator_type,
        "locator": chunk.locator,
        "text_sha256": chunk.text_sha256,
        "char_count": chunk.char_count,
    }


def bundle_semantic_digest(
    *,
    tender_id: str,
    attachments: Iterable[AttachmentRecord],
    chunks: Iterable[EvidenceChunk],
    incomplete_reason_codes: Iterable[str],
) -> str:
    """Sorted by stable IDs so input ordering cannot change the digest."""
    attachment_payload = sorted(
        (attachment_semantic_payload(a) for a in attachments),
        key=lambda item: str(item["attachment_id"]),
    )
    chunk_payload = sorted(
        (chunk_semantic_payload(c) for c in chunks),
        key=lambda item: str(item["chunk_id"]),
    )
    payload = {
        "algorithm": ANEXO_EVIDENCE_SEMANTIC_DIGEST_ALGORITHM,
        "builder_version": ANEXO_EVIDENCE_BUILDER_VERSION,
        "extractor_version": ANEXO_EVIDENCE_EXTRACTOR_VERSION,
        "tender_id": tender_id,
        "attachments": attachment_payload,
        "chunks": chunk_payload,
        "incomplete_reason_codes": sorted(set(incomplete_reason_codes)),
    }
    return canonical_json_digest(payload)
