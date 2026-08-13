"""Resolve ANEXO-T1 evidence from canonical TenderAttachmentBundle records.

This module contains no commercial-term recognition. It only converts already
extracted annex chunks into trustworthy, addressable source evidence.

It fails closed when a chunk cannot be reconciled to its portal attachment or
archive-member provenance.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from origenlab_email_pipeline.chilecompra_anexo_evidence.constants import (
    COMPLETE_OUTCOMES,
    FORMAT_CSV,
    FORMAT_DOCX,
    FORMAT_PDF,
    FORMAT_TXT,
    FORMAT_XLS,
    FORMAT_XLSX,
    FORMAT_XML,
    FORMAT_ZIP,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.models import (
    ArchiveMemberRecord,
    AttachmentRecord,
    EvidenceChunk,
    TenderAttachmentBundle,
    sha256_text,
)

from .models import TenderTermsCoverage, TermEvidence

_LOCATOR_SOURCE_FORMATS: dict[str, frozenset[str]] = {
    "pdf_page": frozenset({FORMAT_PDF}),
    "docx_paragraph": frozenset({FORMAT_DOCX}),
    "docx_table": frozenset({FORMAT_DOCX}),
    # Legacy XLS deliberately reuses the row locator emitted for XLSX.
    "xlsx_rows": frozenset({FORMAT_XLSX, FORMAT_XLS}),
    "csv_rows": frozenset({FORMAT_CSV}),
    "text_span": frozenset({FORMAT_TXT}),
    "xml_text": frozenset({FORMAT_XML}),
}


@dataclass(frozen=True)
class ResolvedEvidenceChunk:
    """One canonical evidence chunk plus verified source provenance."""

    chunk: EvidenceChunk
    source_attachment: AttachmentRecord
    archive_member: ArchiveMemberRecord | None
    evidence_attachment_id: str
    safe_filename: str
    attachment_sha256: str
    detected_format: str
    document_role: str


def coverage_from_bundle(bundle: TenderAttachmentBundle) -> TenderTermsCoverage:
    """Project canonical evidence coverage into the T1 contract."""

    unread = tuple(
        sorted(
            record.attachment_id
            for record in bundle.attachments
            if record.outcome not in COMPLETE_OUTCOMES
        )
    )

    return TenderTermsCoverage(
        has_source_bundle=True,
        extraction_complete=bundle.extraction_complete,
        attachments_discovered=bundle.attachments_discovered,
        attachments_downloaded=bundle.attachments_downloaded,
        incomplete_reason_codes=tuple(bundle.incomplete_reason_codes),
        unread_attachment_ids=unread,
        outcome_counts=dict(bundle.outcome_counts),
    )


def _attachment_index(
    bundle: TenderAttachmentBundle,
) -> dict[str, AttachmentRecord]:
    attachments: dict[str, AttachmentRecord] = {}
    for record in bundle.attachments:
        if record.tender_id != bundle.tender_id:
            raise ValueError(
                "attachment tender does not match source bundle: "
                f"{record.attachment_id}"
            )
        if record.attachment_id in attachments:
            raise ValueError(
                f"duplicate attachment id: {record.attachment_id}"
            )
        attachments[record.attachment_id] = record
    return attachments


def _member_index(
    bundle: TenderAttachmentBundle,
) -> dict[str, ArchiveMemberRecord]:
    members: dict[str, ArchiveMemberRecord] = {}
    for record in bundle.attachments:
        for member in record.archive_members:
            if member.tender_id != bundle.tender_id:
                raise ValueError(
                    "archive member tender does not match source bundle: "
                    f"{member.member_id}"
                )
            if member.member_id in members:
                raise ValueError(
                    f"duplicate archive member id: {member.member_id}"
                )
            members[member.member_id] = member
    return members


def _positive_int(locator: dict[str, object], key: str) -> int:
    value = locator.get(key)
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 1
    ):
        raise ValueError(f"invalid locator field {key!r}: {value!r}")
    return value


def _nonempty_str(locator: dict[str, object], key: str) -> str:
    value = locator.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"invalid locator field {key!r}: {value!r}")
    return value


def _validate_locator(chunk: EvidenceChunk) -> None:
    locator_type = chunk.locator_type
    locator = chunk.locator

    allowed_formats = _LOCATOR_SOURCE_FORMATS.get(locator_type)
    if allowed_formats is None:
        raise ValueError(
            f"unsupported evidence locator type: {locator_type!r}"
        )

    if chunk.source_format not in allowed_formats:
        raise ValueError(
            "locator/source format mismatch: "
            f"{locator_type!r} cannot describe {chunk.source_format!r}"
        )

    if locator_type == "pdf_page":
        page = _positive_int(locator, "page")
        page_count = _positive_int(locator, "page_count")
        _positive_int(locator, "part")
        if page > page_count:
            raise ValueError(
                f"pdf locator page exceeds page_count: {page}>{page_count}"
            )

    elif locator_type == "docx_paragraph":
        _nonempty_str(locator, "section")
        _positive_int(locator, "paragraph")

    elif locator_type == "docx_table":
        _nonempty_str(locator, "section")
        _positive_int(locator, "table")

    elif locator_type == "xlsx_rows":
        _nonempty_str(locator, "sheet")
        start = _positive_int(locator, "start_row")
        end = _positive_int(locator, "end_row")
        if end < start:
            raise ValueError(
                f"row locator end precedes start: {start}>{end}"
            )

    elif locator_type == "csv_rows":
        start = _positive_int(locator, "start_row")
        end = _positive_int(locator, "end_row")
        if end < start:
            raise ValueError(
                f"row locator end precedes start: {start}>{end}"
            )

    elif locator_type in {"text_span", "xml_text"}:
        _positive_int(locator, "part")


def resolve_evidence_chunk(
    bundle: TenderAttachmentBundle,
    chunk: EvidenceChunk,
) -> ResolvedEvidenceChunk:
    """Resolve direct and archive-member chunks to canonical manifest evidence."""

    if chunk.tender_id != bundle.tender_id:
        raise ValueError(
            "chunk tender does not match source bundle: "
            f"{chunk.tender_id!r} != {bundle.tender_id!r}"
        )

    actual_text_sha = sha256_text(chunk.text)
    if actual_text_sha != chunk.text_sha256:
        raise ValueError(f"chunk text digest mismatch: {chunk.chunk_id}")

    if chunk.char_count != len(chunk.text):
        raise ValueError(f"chunk char count mismatch: {chunk.chunk_id}")

    _validate_locator(chunk)

    attachments = _attachment_index(bundle)
    members = _member_index(bundle)

    archive_member: ArchiveMemberRecord | None = None

    if chunk.container_attachment_id:
        source_attachment = attachments.get(chunk.container_attachment_id)
        if source_attachment is None:
            raise ValueError(
                "archive chunk container attachment not found: "
                f"{chunk.container_attachment_id}"
            )

        archive_member = members.get(chunk.attachment_id)
        if archive_member is None:
            raise ValueError(
                f"archive member provenance not found: {chunk.attachment_id}"
            )

        if archive_member.container_attachment_id != source_attachment.attachment_id:
            raise ValueError(
                f"archive member/container mismatch: {chunk.attachment_id}"
            )

        if chunk.member_path != archive_member.member_path:
            raise ValueError(
                f"archive member path mismatch: {chunk.attachment_id}"
            )

        if source_attachment.detected_format != FORMAT_ZIP:
            raise ValueError(
                "archive chunk container is not a ZIP attachment: "
                f"{source_attachment.attachment_id}"
            )

        if chunk.source_format != archive_member.detected_format:
            raise ValueError(
                "archive chunk source format mismatch: "
                f"{chunk.chunk_id}"
            )

        attachment_sha256 = archive_member.member_sha256
        detected_format = archive_member.detected_format

        # The evidence object is the extracted archive member itself. The
        # portal ZIP remains separately addressable through
        # container_attachment_id.
        evidence_attachment_id = chunk.attachment_id
    else:
        source_attachment = attachments.get(chunk.attachment_id)
        if source_attachment is None:
            raise ValueError(
                f"chunk attachment not found: {chunk.attachment_id}"
            )

        if chunk.source_format != source_attachment.detected_format:
            raise ValueError(
                "chunk source format does not match attachment: "
                f"{chunk.chunk_id}"
            )

        attachment_sha256 = source_attachment.sha256
        detected_format = source_attachment.detected_format

        evidence_attachment_id = (
            source_attachment.evidence_attachment_id
            or source_attachment.attachment_id
        )
        if evidence_attachment_id != source_attachment.attachment_id:
            raise ValueError(
                "chunk originates from extraction-reused attachment: "
                f"{source_attachment.attachment_id}"
            )

    if not attachment_sha256:
        raise ValueError(
            f"resolved evidence has no content digest: {chunk.chunk_id}"
        )

    return ResolvedEvidenceChunk(
        chunk=chunk,
        source_attachment=source_attachment,
        archive_member=archive_member,
        evidence_attachment_id=evidence_attachment_id,
        safe_filename=source_attachment.safe_filename,
        attachment_sha256=attachment_sha256,
        detected_format=detected_format,
        document_role=source_attachment.role_tag or "unknown",
    )


def resolve_evidence_chunks(
    bundle: TenderAttachmentBundle,
) -> tuple[ResolvedEvidenceChunk, ...]:
    """Resolve every bundle chunk deterministically."""

    ordered = sorted(
        bundle.chunks,
        key=lambda chunk: (
            chunk.attachment_id,
            chunk.ordinal,
            chunk.chunk_id,
        ),
    )
    return tuple(resolve_evidence_chunk(bundle, chunk) for chunk in ordered)


def _locator_display(
    source: ResolvedEvidenceChunk,
    *,
    char_start: int,
    char_end: int,
) -> str:
    chunk = source.chunk
    where = source.safe_filename
    if chunk.member_path:
        where = f"{where}::{chunk.member_path}"

    locator = chunk.locator

    if chunk.locator_type == "pdf_page":
        inner = f"page {locator.get('page')}"
        if locator.get("part") not in (None, 1):
            inner += f" part {locator.get('part')}"
    elif chunk.locator_type == "docx_paragraph":
        inner = (
            f"{locator.get('section', 'body')} "
            f"paragraph {locator.get('paragraph')}"
        )
    elif chunk.locator_type == "docx_table":
        inner = (
            f"{locator.get('section', 'body')} "
            f"table {locator.get('table')}"
        )
    elif chunk.locator_type == "xlsx_rows":
        inner = (
            f"sheet {locator.get('sheet')!r} "
            f"rows {locator.get('start_row')}-{locator.get('end_row')}"
        )
    elif chunk.locator_type == "csv_rows":
        inner = (
            f"rows {locator.get('start_row')}-{locator.get('end_row')}"
        )
    elif chunk.locator_type == "text_span":
        inner = f"text part {locator.get('part')}"
    elif chunk.locator_type == "xml_text":
        inner = f"XML text part {locator.get('part')}"
    else:
        # _validate_locator() makes this unreachable for canonical evidence.
        raise ValueError(
            f"unsupported evidence locator type: {chunk.locator_type!r}"
        )

    return f"{where} :: {inner} :: chars {char_start}-{char_end}"


def term_evidence_from_span(
    source: ResolvedEvidenceChunk,
    *,
    char_start: int,
    char_end: int,
) -> TermEvidence:
    """Create exact T1 provenance for a character span inside one chunk."""

    text = source.chunk.text
    if char_start < 0 or char_end <= char_start or char_end > len(text):
        raise ValueError(
            f"invalid evidence span {char_start}:{char_end} "
            f"for chunk {source.chunk.chunk_id}"
        )

    excerpt = text[char_start:char_end]
    excerpt_sha = sha256_text(excerpt)

    seed = (
        "anexo_t1_evidence\0"
        f"{source.chunk.chunk_id}\0"
        f"{char_start}\0{char_end}\0{excerpt_sha}"
    ).encode()
    evidence_id = hashlib.sha256(seed).hexdigest()[:32]

    locator = dict(source.chunk.locator)
    locator["char_start"] = char_start
    locator["char_end"] = char_end

    return TermEvidence(
        evidence_id=evidence_id,
        attachment_id=source.chunk.attachment_id,
        evidence_attachment_id=source.evidence_attachment_id,
        safe_filename=source.safe_filename,
        attachment_sha256=source.attachment_sha256,
        detected_format=source.detected_format,
        document_role=source.document_role,
        container_attachment_id=source.chunk.container_attachment_id,
        member_path=source.chunk.member_path,
        chunk_id=source.chunk.chunk_id,
        locator_type=source.chunk.locator_type,
        locator=locator,
        locator_display=_locator_display(
            source,
            char_start=char_start,
            char_end=char_end,
        ),
        evidence_excerpt=excerpt,
        evidence_text_sha256=excerpt_sha,
    )
