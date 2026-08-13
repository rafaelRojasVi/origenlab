"""T1-B source/provenance adapter contract tests."""

from dataclasses import replace

import pytest

from origenlab_email_pipeline.chilecompra_anexo_evidence.models import (
    ArchiveMemberRecord,
    AttachmentExtraction,
    AttachmentRecord,
    EvidenceChunk,
    TenderAttachmentBundle,
    sha256_text,
)
from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms import (
    COVERAGE_COMPLETE,
    COVERAGE_INCOMPLETE,
    coverage_from_bundle,
    resolve_evidence_chunk,
    resolve_evidence_chunks,
    term_evidence_from_span,
)


def _record(
    *,
    attachment_id: str = "att-1",
    outcome: str = "extraction_success",
    sha256: str = "a" * 64,
    evidence_attachment_id: str | None = None,
    archive_members: tuple[ArchiveMemberRecord, ...] = (),
) -> AttachmentRecord:
    return AttachmentRecord(
        attachment_id=attachment_id,
        tender_id="T-1",
        listing_ordinal=1,
        row_ordinal=1,
        portal_filename="bases.pdf",
        safe_filename="bases.pdf",
        tipo="Bases",
        descripcion="Bases administrativas y técnicas",
        fecha_adjunto="2026-08-01",
        content_type="application/pdf",
        detected_format="pdf",
        extension="pdf",
        byte_count=100,
        sha256=sha256,
        download_status="downloaded",
        outcome=outcome,
        role_tag="administrative_basis",
        evidence_attachment_id=evidence_attachment_id,
        extraction=AttachmentExtraction(
            outcome=outcome,
            detected_format="pdf",
            chunk_count=1,
            char_count=100,
            extractor="test",
        ),
        archive_members=archive_members,
    )


def _chunk(
    *,
    attachment_id: str = "att-1",
    text: str = "Presupuesto total $28.419.400 impuestos incluidos.",
    source_format: str = "pdf",
    locator_type: str = "pdf_page",
    locator: dict[str, object] | None = None,
    container_attachment_id: str | None = None,
    member_path: str | None = None,
) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id="chunk-1",
        attachment_id=attachment_id,
        tender_id="T-1",
        source_format=source_format,
        locator_type=locator_type,
        locator=(
            {"page": 5, "page_count": 100, "part": 1}
            if locator is None
            else locator
        ),
        text=text,
        text_sha256=sha256_text(text),
        char_count=len(text),
        ordinal=1,
        container_attachment_id=container_attachment_id,
        member_path=member_path,
    )


def _bundle(
    *,
    record: AttachmentRecord,
    chunk: EvidenceChunk,
    outcome_counts: dict[str, int] | None = None,
    incomplete_reason_codes: tuple[str, ...] = (),
) -> TenderAttachmentBundle:
    return TenderAttachmentBundle(
        tender_id="T-1",
        source_kind="test",
        listing_page_count=1,
        attachments_discovered=1,
        attachments_downloaded=1,
        attachments=(record,),
        chunks=(chunk,),
        bytes_downloaded=100,
        incomplete_reason_codes=incomplete_reason_codes,
        semantic_digest="source-digest",
        outcome_counts=outcome_counts or {record.outcome: 1},
    )


def test_coverage_projects_complete_bundle() -> None:
    bundle = _bundle(
        record=_record(),
        chunk=_chunk(),
    )

    coverage = coverage_from_bundle(bundle)

    assert coverage.is_complete is True
    assert coverage.fact_coverage_status == COVERAGE_COMPLETE
    assert coverage.unread_attachment_ids == ()


def test_coverage_projects_unread_attachment_fail_closed() -> None:
    record = _record(outcome="needs_ocr")
    bundle = _bundle(
        record=record,
        chunk=_chunk(),
        outcome_counts={"needs_ocr": 1},
    )

    coverage = coverage_from_bundle(bundle)

    assert coverage.is_complete is False
    assert coverage.fact_coverage_status == COVERAGE_INCOMPLETE
    assert coverage.unread_attachment_ids == ("att-1",)


def test_direct_chunk_resolves_manifest_provenance() -> None:
    bundle = _bundle(
        record=_record(),
        chunk=_chunk(),
    )

    source = resolve_evidence_chunk(bundle, bundle.chunks[0])

    assert source.source_attachment.attachment_id == "att-1"
    assert source.archive_member is None
    assert source.evidence_attachment_id == "att-1"
    assert source.safe_filename == "bases.pdf"
    assert source.attachment_sha256 == "a" * 64
    assert source.document_role == "administrative_basis"


def test_chunk_from_extraction_reused_attachment_fails_closed() -> None:
    record = _record(
        attachment_id="att-duplicate",
        evidence_attachment_id="att-original",
    )
    chunk = _chunk(attachment_id="att-duplicate")
    bundle = _bundle(record=record, chunk=chunk)

    with pytest.raises(
        ValueError,
        match="chunk originates from extraction-reused attachment",
    ):
        resolve_evidence_chunk(bundle, chunk)


def test_archive_member_uses_member_digest_and_container_metadata() -> None:
    member = ArchiveMemberRecord(
        member_id="member-1",
        container_attachment_id="zip-1",
        tender_id="T-1",
        member_path="docs/bases.pdf",
        member_sha256="m" * 64,
        byte_count=50,
        depth=1,
        detected_format="pdf",
        outcome="extraction_success",
    )
    record = replace(
        _record(
            attachment_id="zip-1",
            sha256="z" * 64,
            archive_members=(member,),
        ),
        portal_filename="anexos.zip",
        safe_filename="anexos.zip",
        detected_format="zip",
        extension="zip",
        content_type="application/zip",
    )
    chunk = _chunk(
        attachment_id="member-1",
        container_attachment_id="zip-1",
        member_path="docs/bases.pdf",
    )
    bundle = _bundle(record=record, chunk=chunk)

    source = resolve_evidence_chunk(bundle, chunk)

    assert source.source_attachment.attachment_id == "zip-1"
    assert source.archive_member == member
    assert source.evidence_attachment_id == "member-1"
    assert source.chunk.container_attachment_id == "zip-1"
    assert source.attachment_sha256 == "m" * 64
    assert source.safe_filename == "anexos.zip"
    assert source.detected_format == "pdf"


def test_archive_member_source_format_mismatch_fails_closed() -> None:
    member = ArchiveMemberRecord(
        member_id="member-1",
        container_attachment_id="zip-1",
        tender_id="T-1",
        member_path="docs/bases.pdf",
        member_sha256="m" * 64,
        byte_count=50,
        depth=1,
        detected_format="pdf",
        outcome="extraction_success",
    )
    record = replace(
        _record(
            attachment_id="zip-1",
            sha256="z" * 64,
            archive_members=(member,),
        ),
        portal_filename="anexos.zip",
        safe_filename="anexos.zip",
        detected_format="zip",
        extension="zip",
        content_type="application/zip",
    )
    chunk = _chunk(
        attachment_id="member-1",
        source_format="docx",
        locator_type="docx_paragraph",
        locator={"section": "body", "paragraph": 1},
        container_attachment_id="zip-1",
        member_path="docs/bases.pdf",
    )
    bundle = _bundle(record=record, chunk=chunk)

    with pytest.raises(
        ValueError,
        match="archive chunk source format mismatch",
    ):
        resolve_evidence_chunk(bundle, chunk)


def test_archive_member_without_manifest_provenance_fails_closed() -> None:
    record = replace(
        _record(attachment_id="zip-1"),
        detected_format="zip",
    )
    chunk = _chunk(
        attachment_id="missing-member",
        container_attachment_id="zip-1",
        member_path="docs/bases.pdf",
    )
    bundle = _bundle(record=record, chunk=chunk)

    with pytest.raises(ValueError, match="archive member provenance not found"):
        resolve_evidence_chunk(bundle, chunk)


def test_duplicate_attachment_ids_fail_closed() -> None:
    record = _record()
    chunk = _chunk()
    bundle = _bundle(record=record, chunk=chunk)
    bundle = replace(
        bundle,
        attachments=(record, record),
        attachments_discovered=2,
        attachments_downloaded=2,
    )

    with pytest.raises(ValueError, match="duplicate attachment id"):
        resolve_evidence_chunk(bundle, chunk)


def test_mutated_chunk_char_count_fails_closed() -> None:
    chunk = replace(_chunk(), char_count=1)
    bundle = _bundle(record=_record(), chunk=chunk)

    with pytest.raises(ValueError, match="chunk char count mismatch"):
        resolve_evidence_chunk(bundle, chunk)


def test_direct_chunk_source_format_mismatch_fails_closed() -> None:
    chunk = replace(_chunk(), source_format="docx")
    bundle = _bundle(record=_record(), chunk=chunk)

    with pytest.raises(
        ValueError,
        match="locator/source format mismatch|source format does not match",
    ):
        resolve_evidence_chunk(bundle, chunk)


@pytest.mark.parametrize(
    ("source_format", "locator_type", "locator"),
    [
        ("pdf", "pdf_page", {"page": 1, "page_count": 2, "part": 1}),
        ("docx", "docx_paragraph", {"section": "body", "paragraph": 3}),
        ("docx", "docx_table", {"section": "header1", "table": 1}),
        (
            "xlsx",
            "xlsx_rows",
            {"sheet": "Specs", "start_row": 2, "end_row": 8},
        ),
        (
            "xls",
            "xlsx_rows",
            {"sheet": "Legacy", "start_row": 1, "end_row": 4},
        ),
        ("csv", "csv_rows", {"start_row": 1, "end_row": 20}),
        ("txt", "text_span", {"part": 2}),
        ("xml", "xml_text", {"part": 1}),
    ],
)
def test_canonical_locator_shapes_resolve(
    source_format: str,
    locator_type: str,
    locator: dict[str, object],
) -> None:
    record = replace(
        _record(),
        detected_format=source_format,
        extension=source_format,
    )
    chunk = _chunk(
        source_format=source_format,
        locator_type=locator_type,
        locator=locator,
    )
    bundle = _bundle(record=record, chunk=chunk)

    source = resolve_evidence_chunk(bundle, chunk)

    assert source.detected_format == source_format


@pytest.mark.parametrize(
    ("source_format", "locator_type", "locator", "message"),
    [
        (
            "pdf",
            "pdf_page",
            {"page": 3, "page_count": 2, "part": 1},
            "page exceeds page_count",
        ),
        (
            "docx",
            "docx_paragraph",
            {"section": "", "paragraph": 1},
            "invalid locator field",
        ),
        (
            "csv",
            "csv_rows",
            {"start_row": 10, "end_row": 2},
            "row locator end precedes start",
        ),
        (
            "pdf",
            "future_locator",
            {"part": 1},
            "unsupported evidence locator type",
        ),
    ],
)
def test_malformed_locator_fails_closed(
    source_format: str,
    locator_type: str,
    locator: dict[str, object],
    message: str,
) -> None:
    record = replace(
        _record(),
        detected_format=source_format,
        extension=source_format,
    )
    chunk = _chunk(
        source_format=source_format,
        locator_type=locator_type,
        locator=locator,
    )
    bundle = _bundle(record=record, chunk=chunk)

    with pytest.raises(ValueError, match=message):
        resolve_evidence_chunk(bundle, chunk)


def test_mutated_chunk_text_digest_fails_closed() -> None:
    chunk = replace(_chunk(), text_sha256="0" * 64)
    bundle = _bundle(record=_record(), chunk=chunk)

    with pytest.raises(ValueError, match="chunk text digest mismatch"):
        resolve_evidence_chunk(bundle, chunk)


def test_resolve_evidence_chunks_is_deterministic() -> None:
    early = replace(
        _chunk(text="Oferta válida por 90 días."),
        chunk_id="chunk-a",
        ordinal=1,
    )
    late = replace(
        _chunk(text="Plazo máximo de entrega 120 días."),
        chunk_id="chunk-z",
        ordinal=2,
    )

    bundle = _bundle(
        record=_record(),
        chunk=late,
    )
    bundle = replace(
        bundle,
        chunks=(late, early),
    )

    resolved = resolve_evidence_chunks(bundle)

    assert [row.chunk.chunk_id for row in resolved] == [
        "chunk-a",
        "chunk-z",
    ]


def test_term_evidence_span_has_exact_excerpt_and_locator() -> None:
    text = "Oferta válida por 90 días desde la apertura electrónica."
    chunk = _chunk(text=text)
    bundle = _bundle(record=_record(), chunk=chunk)
    source = resolve_evidence_chunk(bundle, chunk)

    start = text.index("90 días")
    end = start + len("90 días")

    evidence = term_evidence_from_span(
        source,
        char_start=start,
        char_end=end,
    )

    assert evidence.evidence_excerpt == "90 días"
    assert evidence.evidence_text_sha256 == sha256_text("90 días")
    assert evidence.locator["page"] == 5
    assert evidence.locator["char_start"] == start
    assert evidence.locator["char_end"] == end
    assert "page 5" in evidence.locator_display
    assert f"chars {start}-{end}" in evidence.locator_display
    assert len(evidence.evidence_id) == 32


def test_invalid_term_evidence_span_fails_closed() -> None:
    chunk = _chunk()
    bundle = _bundle(record=_record(), chunk=chunk)
    source = resolve_evidence_chunk(bundle, chunk)

    with pytest.raises(ValueError, match="invalid evidence span"):
        term_evidence_from_span(
            source,
            char_start=-1,
            char_end=5,
        )
