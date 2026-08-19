"""Contract tests for the bounded multi-document ChileCompra anexo evidence lane.

All fixtures are synthetic and local; nothing here touches the network, Gmail,
SQLite, or Postgres.
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))

from chilecompra_anexo_evidence.synthetic_documents import (  # noqa: E402
    make_csv,
    make_docx,
    make_encrypted_member_zip,
    make_fake_legacy_ole,
    make_image_only_pdf,
    make_pdf,
    make_traversal_zip,
    make_xlsx,
    make_zip,
    make_zip_bomb,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence import (  # noqa: E402
    ArchiveLimits,
    ContentAddressedCache,
    EvidenceBuildConfig,
    ExtractionLimits,
    LocalAttachmentSource,
    build_summary,
    build_tender_bundle,
    detect_format,
    extract_payload,
    role_tag_for,
    write_evidence_outputs,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.acquire import (  # noqa: E402
    AttachmentBudgetError,
    PortalAttachmentSource,
    SourceInventory,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.archive import (  # noqa: E402
    ArchiveSafetyError,
    is_unsafe_member_path,
    iter_archive_members,
    preflight_archive,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.constants import (  # noqa: E402
    OUTCOME_CORRUPT,
    OUTCOME_DOWNLOAD_FAILED,
    OUTCOME_EXTRACTED_EMPTY,
    OUTCOME_EXTRACTION_SUCCESS,
    OUTCOME_NEEDS_CONVERTER,
    OUTCOME_NEEDS_OCR,
    OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT,
    OUTCOME_UNSUPPORTED_FORMAT,
    REASON_ATTACHMENT_COUNT_BUDGET_EXCEEDED,
    REASON_GATED_LISTING_UNREACHABLE,
)
import origenlab_email_pipeline.chilecompra_anexo_evidence.extract as extract_module  # noqa: E402
from origenlab_email_pipeline.chilecompra_anexo_evidence.ocr import OcrResult  # noqa: E402

from origenlab_email_pipeline.chilecompra_anexo_evidence.redaction import (  # noqa: E402
    ArtifactRedactionError,
    assert_no_portal_tokens,
    redact_portal_tokens,
)

_SPEC = "ESPECTROFOTOMETRO UV VIS"


def _source(items, **kwargs):
    return LocalAttachmentSource(items=list(items), **kwargs)


def _bundle(items, **kwargs):
    return build_tender_bundle("T-TEST", _source(items, **kwargs))


def _all_text(bundle) -> str:
    return "\n".join(chunk.text for chunk in bundle.chunks)


def _outcomes(bundle) -> list[str]:
    return [record.outcome for record in bundle.attachments]


# --- 1-4, 6: inventory, accounting, and scale ---------------------------------


def test_zero_attachments_yields_empty_but_complete_bundle() -> None:
    bundle = _bundle([])
    assert bundle.attachments_discovered == 0
    assert bundle.attachments == ()
    assert bundle.bundle_complete is True
    assert bundle.incomplete_reason_codes == ()


def test_single_pdf_attachment_extracts_and_completes() -> None:
    bundle = _bundle(
        [("bases.pdf", "application/pdf", make_pdf(2, marker=_SPEC, marker_page=2))]
    )
    assert bundle.attachments_discovered == 1
    assert bundle.attachments_downloaded == 1
    assert _outcomes(bundle) == [OUTCOME_EXTRACTION_SUCCESS]
    assert bundle.bundle_complete is True
    assert _SPEC in _all_text(bundle)


def test_multiple_listing_pages_are_counted_and_preserved() -> None:
    items = [
        (f"doc_{i}.txt", "text/plain", f"contenido {i}".encode()) for i in range(6)
    ]
    source = _source(items, listing_page_count=3, listing_ordinals=[0, 0, 1, 1, 2, 2])
    bundle = build_tender_bundle("T-MULTI", source)
    assert bundle.listing_page_count == 3
    assert sorted({r.listing_ordinal for r in bundle.attachments}) == [0, 1, 2]
    assert bundle.attachments_discovered == 6
    assert bundle.bundle_complete is True


def test_seventy_five_attachments_all_accounted_for() -> None:
    items = [
        (f"anexo_{i:03d}.txt", "text/plain", f"linea {i}".encode()) for i in range(75)
    ]
    bundle = build_tender_bundle(
        "T-BULK",
        _source(items),
        config=EvidenceBuildConfig(),
    )
    assert bundle.attachments_discovered == 75
    assert bundle.attachments_downloaded == 75
    assert len(bundle.attachments) == 75
    assert all(o == OUTCOME_EXTRACTION_SUCCESS for o in _outcomes(bundle))
    assert bundle.bundle_complete is True
    # Every discovered row has a unique provenance record.
    assert len({r.attachment_id for r in bundle.attachments}) == 75


def test_streaming_source_does_not_retain_every_payload(monkeypatch) -> None:
    """The builder must consume payloads one at a time, not materialize a list."""
    live_payloads: list[int] = []
    max_live = 0

    class _TrackingSource:
        source_kind = "tracking"

        def inventory(self):
            return _source(
                [(f"f{i}.txt", "text/plain", b"x" * 1024) for i in range(40)]
            ).inventory()

        def iter_payloads(self, inventory):
            nonlocal max_live
            from origenlab_email_pipeline.chilecompra_anexo_evidence.acquire import (
                AcquiredAttachment,
            )

            for stub in inventory.stubs:
                payload = b"contenido " * 100
                live_payloads.append(len(payload))
                max_live = max(max_live, len(live_payloads))
                yield AcquiredAttachment(
                    stub=stub, payload=payload, content_type="text/plain"
                )
                live_payloads.clear()

    bundle = build_tender_bundle("T-STREAM", _TrackingSource())
    assert bundle.attachments_discovered == 40
    assert max_live == 1


# --- 5: budget preflight ------------------------------------------------------


def test_count_budget_failure_is_explicit_and_reports_discovered_count() -> None:
    class _BudgetSource:
        source_kind = "budget"

        def __init__(self, count: int, limit: int) -> None:
            self.count = count
            self.limit = limit

        def inventory(self):
            return _source(
                [(f"f{i}.txt", "text/plain", b"x") for i in range(self.count)]
            ).inventory()

        def iter_payloads(self, inventory):
            raise AttachmentBudgetError(
                REASON_ATTACHMENT_COUNT_BUDGET_EXCEEDED,
                f"discovered {self.count} attachments but max_attachment_count={self.limit}",
                discovered=self.count,
            )
            yield  # pragma: no cover - generator contract

    bundle = build_tender_bundle("T-BUDGET", _BudgetSource(80, 50))
    assert bundle.attachments_discovered == 80
    assert bundle.attachments_downloaded == 0
    assert len(bundle.attachments) == 80, "no row may be silently dropped"
    assert set(_outcomes(bundle)) == {OUTCOME_DOWNLOAD_FAILED}
    assert REASON_ATTACHMENT_COUNT_BUDGET_EXCEEDED in bundle.incomplete_reason_codes
    assert bundle.bundle_complete is False
    assert "80" in (bundle.attachments[0].error_message or "")


def test_source_level_incompleteness_survives_into_the_bundle_alongside_a_real_read() -> (
    None
):
    """Fixture C: SourceInventory-level incompleteness (e.g. PortalAttachmentSource
    reporting a gated listing it could not enumerate, per chilecompra_api's
    has_gated_attachment_listing_hint) is not tied to any one attachment
    outcome, yet must still make it into TenderAttachmentBundle unchanged --
    the one row that WAS read is not penalized, but the bundle as a whole
    cannot claim complete coverage."""
    bundle = _bundle(
        [("FORMULARIO_OBLIGATORIO_1234.docx", "text/plain", b"contenido real leido")],
        incomplete_reason_codes=(REASON_GATED_LISTING_UNREACHABLE,),
    )
    assert bundle.attachments_discovered == 1
    assert bundle.attachments_downloaded == 1
    assert bundle.attachments[0].outcome == OUTCOME_EXTRACTION_SUCCESS
    assert REASON_GATED_LISTING_UNREACHABLE in bundle.incomplete_reason_codes
    assert bundle.download_complete is True
    assert bundle.extraction_complete is False
    assert bundle.bundle_complete is False


def test_semantic_digest_changes_when_source_level_incompleteness_changes() -> None:
    """The digest must not silently equate a fully-covered bundle with one
    that has identical attachments/chunks but an undiscovered gated listing
    -- two consumers diffing digests across a re-crawl need to see this
    surface appear, not a false "nothing changed"."""
    items = [
        ("FORMULARIO_OBLIGATORIO_1234.docx", "text/plain", b"contenido real leido")
    ]
    complete = _bundle(items)
    gated = _bundle(items, incomplete_reason_codes=(REASON_GATED_LISTING_UNREACHABLE,))
    assert complete.bundle_complete is True
    assert gated.bundle_complete is False
    # Same attachments and chunk content either way.
    assert [a.sha256 for a in complete.attachments] == [
        a.sha256 for a in gated.attachments
    ]
    assert [c.text_sha256 for c in complete.chunks] == [
        c.text_sha256 for c in gated.chunks
    ]
    assert complete.semantic_digest != gated.semantic_digest


def test_source_inventory_defaults_to_no_incomplete_reason_codes() -> None:
    inventory = SourceInventory(listing_page_count=1, stubs=())
    assert inventory.incomplete_reason_codes == ()


def test_portal_source_preflights_count_before_downloading(monkeypatch) -> None:
    from origenlab_email_pipeline import chilecompra_api

    calls: list[str] = []

    class _FakeInventory:
        attachments = tuple(range(5))
        listing_urls = ("u",)
        listing_form_fields: dict = {}

    source = PortalAttachmentSource(detail_url="https://x", max_attachment_count=2)
    source._inventory = _FakeInventory()
    source._session = object()
    monkeypatch.setattr(
        chilecompra_api,
        "download_portal_attachment",
        lambda *a, **k: calls.append("download"),
    )
    inventory = SourceInventory(listing_page_count=1, stubs=())
    with pytest.raises(AttachmentBudgetError) as exc:
        list(source.iter_payloads(inventory))
    assert exc.value.reason_code == REASON_ATTACHMENT_COUNT_BUDGET_EXCEEDED
    assert exc.value.discovered == 5
    assert calls == [], "no body may be fetched once the count budget fails"


# --- 7-9: duplicates and revisions -------------------------------------------


def test_same_name_same_bytes_keeps_both_rows_and_reuses_extraction() -> None:
    payload = make_csv(5, marker=_SPEC, marker_row=2)
    bundle = _bundle(
        [("anexo.csv", "text/csv", payload), ("anexo.csv", "text/csv", payload)]
    )
    assert len(bundle.attachments) == 2
    first, second = bundle.attachments
    assert first.sha256 == second.sha256
    assert second.duplicate_of_attachment_id == first.attachment_id
    assert second.duplicate_kind == "same_name_same_bytes"
    assert second.extraction.chunk_count == 0
    assert any(
        w.startswith("extraction_reused_from:") for w in second.extraction.warnings
    )
    assert bundle.bundle_complete is True


def test_different_name_same_bytes_keeps_both_rows() -> None:
    payload = make_csv(4)
    bundle = _bundle(
        [("uno.csv", "text/csv", payload), ("dos.csv", "text/csv", payload)]
    )
    assert len(bundle.attachments) == 2
    assert bundle.attachments[1].duplicate_kind == "different_name_same_bytes"
    assert (
        bundle.attachments[1].duplicate_of_attachment_id
        == bundle.attachments[0].attachment_id
    )


def test_same_name_different_bytes_are_distinct_documents() -> None:
    bundle = _bundle(
        [
            ("bases.csv", "text/csv", make_csv(3, marker="EQUIPO A", marker_row=1)),
            ("bases.csv", "text/csv", make_csv(3, marker="EQUIPO B", marker_row=1)),
        ]
    )
    first, second = bundle.attachments
    assert first.sha256 != second.sha256
    assert second.duplicate_of_attachment_id is None
    assert "same_name_different_content" in second.warnings
    text = _all_text(bundle)
    assert "EQUIPO A" in text and "EQUIPO B" in text


def test_contradictory_specs_are_both_preserved() -> None:
    bundle = _bundle(
        [
            ("v1.txt", "text/plain", "requiere 2 centrifugas".encode()),
            ("v2.txt", "text/plain", "requiere 5 centrifugas".encode()),
        ]
    )
    text = _all_text(bundle)
    assert "2 centrifugas" in text and "5 centrifugas" in text
    assert len(bundle.attachments) == 2


# --- 10-13: naming and misleading metadata -----------------------------------


def test_unicode_filenames_are_preserved_and_sanitized() -> None:
    bundle = _bundle([("ANEXO N°2 REQUERIMIENTOS ÑU.txt", "text/plain", b"texto")])
    record = bundle.attachments[0]
    assert record.portal_filename == "ANEXO N°2 REQUERIMIENTOS ÑU.txt"
    assert record.safe_filename == "ANEXO N°2 REQUERIMIENTOS ÑU.txt"
    assert record.outcome == OUTCOME_EXTRACTION_SUCCESS


def test_misleading_extension_is_detected_by_signature() -> None:
    zip_payload = make_zip([("real.txt", b"contenido")])
    detection = detect_format(zip_payload, filename="requerimientos.pdf")
    assert detection.detected_format == "zip"
    assert detection.evidence == "zip_container"
    assert any(w.startswith("extension_mismatch:") for w in detection.warnings)


def test_misleading_content_type_is_recorded_as_warning() -> None:
    pdf = make_pdf(1)
    detection = detect_format(
        pdf, content_type="application/vnd.ms-excel", filename="x.pdf"
    )
    assert detection.detected_format == "pdf"
    assert any(w.startswith("content_type_mismatch:") for w in detection.warnings)


def test_valid_non_pdf_binary_is_not_rejected() -> None:
    docx_payload = make_docx(paragraph="documento word valido")
    bundle = _bundle([("bases.docx", "application/octet-stream", docx_payload)])
    assert bundle.attachments[0].detected_format == "docx"
    assert bundle.attachments[0].outcome == OUTCOME_EXTRACTION_SUCCESS


# --- 14-17: PDF behavior ------------------------------------------------------


def test_pdf_equipment_spec_beyond_page_200_is_extracted() -> None:
    payload = make_pdf(205, marker=_SPEC, marker_page=203)
    output = extract_payload(payload, detected_format="pdf")
    assert output.outcome == OUTCOME_EXTRACTION_SUCCESS
    assert output.page_count == 205
    assert any(_SPEC in chunk.text for chunk in output.chunks)
    pages = {chunk.locator["page"] for chunk in output.chunks}
    assert 203 in pages and max(pages) == 205


def test_pdf_page_limit_reports_partial_not_success() -> None:
    payload = make_pdf(12)
    output = extract_payload(
        payload, detected_format="pdf", limits=ExtractionLimits(max_pdf_pages=4)
    )
    assert output.outcome == OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT
    assert any(w.startswith("pdf_page_limit:") for w in output.warnings)
    assert output.page_count == 12


def test_image_only_pdf_is_needs_ocr_and_keeps_page_count(monkeypatch) -> None:
    monkeypatch.setattr(
        extract_module,
        "extract_image_text",
        lambda payload, *, limits: OcrResult(
            text="",
            line_count=0,
            mean_score=None,
            warning="ocr_insufficient_text",
        ),
    )

    output = extract_payload(make_image_only_pdf(3), detected_format="pdf")

    assert output.outcome == OUTCOME_NEEDS_OCR
    assert output.page_count == 3
    assert output.chunks == []
    assert "pdf_no_extractable_text" in output.warnings


def test_corrupt_pdf_is_reported_as_corrupt() -> None:
    output = extract_payload(b"%PDF-1.7 not really a pdf", detected_format="pdf")
    assert output.outcome == OUTCOME_CORRUPT
    assert output.error_message


def test_encrypted_pdf_is_reported_as_encrypted() -> None:
    fitz = pytest.importorskip("fitz")
    document = fitz.open()
    document.new_page().insert_text((72, 72), "secreto")
    payload = document.tobytes(
        encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u"
    )
    document.close()
    output = extract_payload(payload, detected_format="pdf")
    assert output.outcome == "encrypted"


# --- 18-20: DOCX behavior -----------------------------------------------------


def test_docx_spec_in_paragraph_is_extracted() -> None:
    output = extract_payload(make_docx(paragraph=_SPEC), detected_format="docx")
    assert output.outcome == OUTCOME_EXTRACTION_SUCCESS
    assert any(_SPEC in c.text for c in output.chunks)
    assert {c.locator_type for c in output.chunks} == {"docx_paragraph"}


def test_docx_spec_only_in_table_is_extracted() -> None:
    payload = make_docx(
        paragraph="Introduccion",
        table_rows=[["Equipo", "Cantidad"], [_SPEC, "3"]],
    )
    output = extract_payload(payload, detected_format="docx")
    table_chunks = [c for c in output.chunks if c.locator_type == "docx_table"]
    assert table_chunks, "table content must not be dropped"
    assert any(_SPEC in c.text for c in table_chunks)


def test_docx_spec_in_header_and_footer_is_extracted() -> None:
    payload = make_docx(paragraph="cuerpo", header=f"HDR {_SPEC}", footer="FTR ANEXO")
    output = extract_payload(payload, detected_format="docx")
    sections = {c.locator["section"] for c in output.chunks}
    assert any(s.startswith("header") for s in sections)
    assert any(s.startswith("footer") for s in sections)
    joined = "\n".join(c.text for c in output.chunks)
    assert f"HDR {_SPEC}" in joined and "FTR ANEXO" in joined


def test_docx_embedded_object_forces_incomplete() -> None:
    payload = make_docx(paragraph="con objeto incrustado", embedded=True)
    output = extract_payload(payload, detected_format="docx")
    assert "embedded_content_unprocessed" in output.warnings
    assert output.outcome == OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT


# --- 21-25: XLSX behavior -----------------------------------------------------


def test_xlsx_spec_on_fifth_sheet_after_row_100_and_column_12() -> None:
    payload = make_xlsx(
        ["S1", "S2", "S3", "S4", "S5"],
        cells={"S5": [(150, 15, _SPEC)]},
    )
    output = extract_payload(payload, detected_format="xlsx")
    assert output.outcome == OUTCOME_EXTRACTION_SUCCESS
    assert output.sheet_count == 5
    hit = [c for c in output.chunks if _SPEC in c.text]
    assert hit, "sheet 5 / row 150 / column 15 must still be extracted"
    assert hit[0].locator["sheet"] == "S5"
    assert hit[0].locator["start_row"] <= 150 <= hit[0].locator["end_row"]


def test_xlsx_hidden_sheet_is_included_and_flagged() -> None:
    payload = make_xlsx(
        ["Visible", "Oculta"],
        cells={"Oculta": [(2, 2, "BANO TERMOSTATICO")]},
        hidden_sheets=("Oculta",),
    )
    output = extract_payload(payload, detected_format="xlsx")
    assert any("BANO TERMOSTATICO" in c.text for c in output.chunks)
    assert any(w.startswith("hidden_sheet_included:") for w in output.warnings)


def test_xlsx_merged_cell_value_is_preserved() -> None:
    payload = make_xlsx(["S1"], merged={"S1": ("A1:C1", "TITULO FUSIONADO")})
    output = extract_payload(payload, detected_format="xlsx")
    assert any("TITULO FUSIONADO" in c.text for c in output.chunks)


def test_xlsx_cell_budget_reports_partial() -> None:
    payload = make_xlsx(["S1"], cells={"S1": [(r, 1, f"v{r}") for r in range(1, 200)]})
    output = extract_payload(
        payload, detected_format="xlsx", limits=ExtractionLimits(max_xlsx_cells=10)
    )
    assert output.outcome == OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT
    assert "xlsx_cell_limit_reached" in output.warnings


def test_malformed_xlsx_is_corrupt_not_empty() -> None:
    payload = make_zip([("xl/worksheets/sheet1.xml", b"<not-valid")])
    output = extract_payload(payload, detected_format="xlsx")
    assert output.outcome in {OUTCOME_CORRUPT, "extraction_failed"}
    assert output.outcome != OUTCOME_EXTRACTED_EMPTY


# --- 26: legacy XLS -----------------------------------------------------------


def test_legacy_ole_payload_is_detected_and_never_silently_skipped() -> None:
    payload = make_fake_legacy_ole()
    detection = detect_format(payload, filename="planilla.xls")
    assert detection.detected_format == "xls"
    output = extract_payload(payload, detected_format="xls")
    assert output.outcome in {OUTCOME_CORRUPT, OUTCOME_NEEDS_CONVERTER}


def test_legacy_doc_is_needs_converter() -> None:
    payload = make_fake_legacy_ole()
    detection = detect_format(payload, filename="bases.doc")
    assert detection.detected_format == "doc"
    output = extract_payload(payload, detected_format="doc")
    assert output.outcome == OUTCOME_NEEDS_CONVERTER


def test_xls_without_xlrd_reports_dependency_unavailable(monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "xlrd":
            raise ImportError("no xlrd")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    output = extract_payload(make_fake_legacy_ole(), detected_format="xls")
    assert output.outcome == OUTCOME_NEEDS_CONVERTER
    assert "dependency_unavailable:xlrd" in output.warnings


# --- 27-29: CSV / TXT / XML ---------------------------------------------------


def test_csv_beyond_200_rows_still_yields_late_evidence() -> None:
    payload = make_csv(500, marker=_SPEC, marker_row=460)
    output = extract_payload(payload, detected_format="csv")
    assert output.outcome == OUTCOME_EXTRACTION_SUCCESS
    assert any(_SPEC in c.text for c in output.chunks)
    assert output.row_count and output.row_count > 200


def test_csv_row_limit_is_explicitly_partial() -> None:
    payload = make_csv(50)
    output = extract_payload(
        payload, detected_format="csv", limits=ExtractionLimits(max_csv_rows=10)
    )
    assert output.outcome == OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT


def test_txt_extraction_with_non_utf8_encoding() -> None:
    payload = "requerimiento técnico ñ".encode("cp1252")
    output = extract_payload(payload, detected_format="txt")
    assert output.outcome == OUTCOME_EXTRACTION_SUCCESS
    assert "requerimiento" in output.chunks[0].text
    assert any(w.startswith("decoded_as:") for w in output.warnings)


def test_xml_text_extraction_and_malformed_failure() -> None:
    good = b"<root><item>CENTRIFUGA</item><item>MICROSCOPIO</item></root>"
    output = extract_payload(good, detected_format="xml")
    assert output.outcome == OUTCOME_EXTRACTION_SUCCESS
    assert "CENTRIFUGA" in output.chunks[0].text

    bad = extract_payload(b"<root><unclosed>", detected_format="xml")
    assert bad.outcome == "extraction_failed"
    assert bad.error_message


# --- 30-38: archives ----------------------------------------------------------


def test_zip_with_mixed_formats_produces_children_for_every_member() -> None:
    payload = make_zip(
        [
            ("bases.pdf", make_pdf(1, marker="BASES", marker_page=1)),
            ("especificaciones.docx", make_docx(paragraph=_SPEC)),
            (
                "requerimientos.xlsx",
                make_xlsx(["S1"], cells={"S1": [(1, 1, "MICROSCOPIO")]}),
            ),
            ("formulario.csv", make_csv(3, marker="CENTRIFUGA", marker_row=1)),
        ]
    )
    bundle = _bundle([("anexo.zip", "application/zip", payload)])
    record = bundle.attachments[0]
    assert record.detected_format == "zip"
    paths = sorted(m.member_path for m in record.archive_members)
    assert paths == [
        "bases.pdf",
        "especificaciones.docx",
        "formulario.csv",
        "requerimientos.xlsx",
    ]
    assert all(m.outcome == OUTCOME_EXTRACTION_SUCCESS for m in record.archive_members)
    assert record.outcome == OUTCOME_EXTRACTION_SUCCESS
    text = _all_text(bundle)
    for needle in ("BASES", _SPEC, "MICROSCOPIO", "CENTRIFUGA"):
        assert needle in text
    # Provenance is never flattened.
    assert all(c.container_attachment_id == record.attachment_id for c in bundle.chunks)
    assert {c.member_path for c in bundle.chunks} == set(paths)


def test_duplicate_zip_member_names_are_both_recorded() -> None:
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("dup.txt", b"primero")
        archive.writestr("dup.txt", b"segundo")
    bundle = _bundle([("dupes.zip", "application/zip", buffer.getvalue())])
    members = bundle.attachments[0].archive_members
    assert len(members) == 2
    assert {m.member_path for m in members} == {"dup.txt", "dup.txt#2"}
    assert any("duplicate_member_path" in m.warnings for m in members)


def test_nested_zip_is_expanded_within_depth_limit() -> None:
    inner = make_zip([("interno.txt", b"EQUIPO INTERNO")])
    outer = make_zip([("interno.zip", inner), ("externo.txt", b"EQUIPO EXTERNO")])
    bundle = _bundle([("anidado.zip", "application/zip", outer)])
    members = bundle.attachments[0].archive_members
    paths = sorted(m.member_path for m in members)
    assert "interno.zip" in paths
    assert "interno.zip::interno.txt" in paths
    text = _all_text(bundle)
    assert "EQUIPO INTERNO" in text and "EQUIPO EXTERNO" in text


def test_archive_depth_limit_is_partial_not_silent() -> None:
    deepest = make_zip([("hoja.txt", b"HOJA PROFUNDA")])
    level2 = make_zip([("n2.zip", deepest)])
    level1 = make_zip([("n1.zip", level2)])
    config = EvidenceBuildConfig(archive_limits=ArchiveLimits(max_depth=1))
    bundle = build_tender_bundle(
        "T-DEPTH", _source([("raiz.zip", "application/zip", level1)]), config=config
    )
    record = bundle.attachments[0]
    assert record.outcome == OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT
    assert "HOJA PROFUNDA" not in _all_text(bundle)
    assert bundle.bundle_complete is False
    assert any("archive" in code for code in bundle.incomplete_reason_codes)


def test_archive_member_count_limit_rejects_expansion() -> None:
    payload = make_zip([(f"m{i}.txt", b"x") for i in range(20)])
    preflight = preflight_archive(payload, limits=ArchiveLimits(max_members=5))
    assert preflight.rejected is True
    assert "archive_member_limit" in preflight.reason_codes


def test_archive_uncompressed_byte_limit_rejects_expansion() -> None:
    payload = make_zip([("grande.txt", b"A" * 20000)])
    preflight = preflight_archive(
        payload, limits=ArchiveLimits(max_total_uncompressed_bytes=1000)
    )
    assert preflight.rejected is True
    assert "archive_bytes_limit" in preflight.reason_codes


def test_zip_bomb_ratio_is_rejected_before_decompression() -> None:
    payload = make_zip_bomb(member_bytes=4 * 1024 * 1024)
    preflight = preflight_archive(
        payload, limits=ArchiveLimits(max_compression_ratio=50.0)
    )
    assert preflight.rejected is True
    assert "archive_ratio_limit" in preflight.reason_codes

    bundle = build_tender_bundle(
        "T-BOMB",
        _source([("bomba.zip", "application/zip", payload)]),
        config=EvidenceBuildConfig(
            archive_limits=ArchiveLimits(max_compression_ratio=50.0)
        ),
    )
    record = bundle.attachments[0]
    assert record.outcome == OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT
    assert record.archive_members == ()
    assert bundle.bundle_complete is False


def test_path_traversal_member_is_refused_but_recorded() -> None:
    bundle = _bundle([("traversal.zip", "application/zip", make_traversal_zip())])
    members = bundle.attachments[0].archive_members
    escaped = [m for m in members if ".." in m.member_path]
    assert escaped, "traversal member must still be accounted for"
    assert all("unsafe_member_path" in m.warnings for m in escaped)
    assert all(m.member_sha256 == "" for m in escaped)
    assert any(m.member_path == "safe.txt" for m in members)
    assert is_unsafe_member_path("../../escape.txt") is True
    assert is_unsafe_member_path("/etc/passwd") is True
    assert is_unsafe_member_path("C:/windows/system32") is True
    assert is_unsafe_member_path("carpeta/archivo.txt") is False


def test_encrypted_archive_member_is_reported_not_skipped() -> None:
    bundle = _bundle([("cifrado.zip", "application/zip", make_encrypted_member_zip())])
    members = bundle.attachments[0].archive_members
    encrypted = [m for m in members if m.outcome == "encrypted"]
    assert encrypted, "encrypted member must produce a record"
    assert bundle.bundle_complete is False


def test_unreadable_zip_container_raises_archive_safety_error() -> None:
    with pytest.raises(ArchiveSafetyError):
        preflight_archive(b"PK\x03\x04 broken", limits=ArchiveLimits())
    with pytest.raises(ArchiveSafetyError):
        iter_archive_members(b"PK\x03\x04 broken", limits=ArchiveLimits())


# --- 39-40: unknown formats and role tags ------------------------------------


def test_unknown_binary_is_unsupported_and_named() -> None:
    bundle = _bundle([("raro.bin", "application/octet-stream", bytes(range(8)) * 40)])
    record = bundle.attachments[0]
    assert record.outcome == OUTCOME_UNSUPPORTED_FORMAT
    assert record.detected_format == "unknown"
    assert bundle.bundle_complete is False
    assert OUTCOME_UNSUPPORTED_FORMAT in bundle.incomplete_reason_codes


def test_administrative_file_is_extracted_not_filtered() -> None:
    payload = make_docx(paragraph=f"Bases administrativas. Se requiere {_SPEC}.")
    bundle = _bundle(
        [("BASES ADMINISTRATIVAS.docx", "application/octet-stream", payload)]
    )
    record = bundle.attachments[0]
    assert record.role_tag == "administrative_basis"
    assert record.outcome == OUTCOME_EXTRACTION_SUCCESS
    assert _SPEC in _all_text(bundle)


def test_role_tags_are_hints_only() -> None:
    assert role_tag_for("Especificaciones tecnicas.pdf") == "technical_spec"
    assert role_tag_for("Bases administrativas.pdf") == "administrative_basis"
    assert role_tag_for("Oferta economica.xlsx") == "economic_form"
    assert role_tag_for("Formulario 3.docx") == "template"
    assert role_tag_for("archivo.bin") == "unknown"


# --- 41-45: determinism, reuse, resilience, completeness ---------------------


def test_input_order_does_not_change_semantic_digest() -> None:
    items = [
        ("a.txt", "text/plain", b"alpha"),
        ("b.txt", "text/plain", b"beta"),
        ("c.txt", "text/plain", b"gamma"),
    ]
    forward = build_tender_bundle(
        "T-DET",
        LocalAttachmentSource(items=list(items), listing_ordinals=[0, 0, 0]),
    )
    shuffled = [items[2], items[0], items[1]]
    # Same portal positions, different iteration order of the source list.
    reordered_source = LocalAttachmentSource(items=shuffled, listing_ordinals=[0, 0, 0])
    reordered = build_tender_bundle("T-DET", reordered_source)
    assert forward.attachments_discovered == reordered.attachments_discovered
    assert sorted(c.text_sha256 for c in forward.chunks) == sorted(
        c.text_sha256 for c in reordered.chunks
    )


def test_repeated_build_is_byte_identical() -> None:
    items = [
        ("bases.pdf", "application/pdf", make_pdf(2, marker=_SPEC, marker_page=2)),
        ("datos.csv", "text/csv", make_csv(20)),
    ]
    first = _bundle(items)
    second = _bundle(items)
    assert first.semantic_digest == second.semantic_digest
    assert [c.chunk_id for c in first.chunks] == [c.chunk_id for c in second.chunks]


def test_content_hash_cache_reuses_stored_payload(tmp_path) -> None:
    cache = ContentAddressedCache(root=tmp_path / "cache")
    payload = make_csv(5)
    digest, path = cache.put(payload)
    assert path.is_file()
    again_digest, again_path = cache.put(payload)
    assert (digest, path) == (again_digest, again_path)
    assert cache.read(digest) == payload
    path.write_bytes(b"tampered")
    assert cache.read(digest) is None, "hash mismatch must invalidate the cache entry"


def test_failed_attachment_does_not_erase_successful_siblings() -> None:
    source = _source(
        [
            ("ok.txt", "text/plain", b"CENTRIFUGA OK"),
            ("roto.txt", "text/plain", b"no importa"),
            ("ok2.txt", "text/plain", b"MICROSCOPIO OK"),
        ],
        failures={1: "ChileCompraHttpError: HTTP 500"},
    )
    bundle = build_tender_bundle("T-MIXED", source)
    outcomes = _outcomes(bundle)
    assert outcomes[0] == OUTCOME_EXTRACTION_SUCCESS
    assert outcomes[1] == OUTCOME_DOWNLOAD_FAILED
    assert outcomes[2] == OUTCOME_EXTRACTION_SUCCESS
    text = _all_text(bundle)
    assert "CENTRIFUGA OK" in text and "MICROSCOPIO OK" in text
    assert bundle.attachments_downloaded == 2
    assert bundle.bundle_complete is False


def test_incomplete_bundle_never_reports_extraction_complete() -> None:
    cases = [
        ("scan.pdf", "application/pdf", make_image_only_pdf(1)),
        ("raro.bin", "application/octet-stream", bytes(range(8)) * 40),
        ("legacy.doc", "application/msword", make_fake_legacy_ole()),
    ]
    for item in cases:
        bundle = _bundle([item])
        assert bundle.extraction_complete is False, item[0]
        assert bundle.bundle_complete is False, item[0]
        assert bundle.incomplete_reason_codes


def test_every_discovered_row_has_exactly_one_outcome() -> None:
    from origenlab_email_pipeline.chilecompra_anexo_evidence.constants import (
        ALL_OUTCOMES,
    )

    bundle = _bundle(
        [
            ("a.pdf", "application/pdf", make_pdf(1, marker="A", marker_page=1)),
            ("b.pdf", "application/pdf", make_image_only_pdf(1)),
            ("c.bin", "application/octet-stream", bytes(range(8)) * 40),
            ("d.doc", "application/msword", make_fake_legacy_ole()),
            ("e.csv", "text/csv", make_csv(2)),
        ]
    )
    assert len(bundle.attachments) == bundle.attachments_discovered == 5
    for record in bundle.attachments:
        assert record.outcome in ALL_OUTCOMES
    assert sum(bundle.outcome_counts.values()) == 5


# --- 46 + artifacts: token redaction and output bundle -----------------------


def test_portal_tokens_are_redacted_in_free_text() -> None:
    raw = "https://www.mercadopublico.cl/x.aspx?qs=SECRETO123&enc=OTRO456"
    redacted = redact_portal_tokens(raw)
    assert "SECRETO123" not in redacted and "OTRO456" not in redacted
    assert_no_portal_tokens(redacted, where="test")
    with pytest.raises(ArtifactRedactionError):
        assert_no_portal_tokens(raw, where="test")


def test_output_bundle_is_written_atomically_without_tokens(tmp_path) -> None:
    root = tmp_path / "email-pipeline"
    (root / "reports" / "out").mkdir(parents=True)
    zip_payload = make_zip(
        [
            ("bases.pdf", make_pdf(1, marker="BASES", marker_page=1)),
            ("d.csv", make_csv(3)),
        ]
    )
    bundle = _bundle(
        [
            ("bases.pdf", "application/pdf", make_pdf(2, marker=_SPEC, marker_page=2)),
            ("anexo.zip", "application/zip", zip_payload),
            ("scan.pdf", "application/pdf", make_image_only_pdf(1)),
        ]
    )
    out_dir = root / "reports" / "out" / "active" / "current" / "anexo_evidence_test"
    written = write_evidence_outputs(
        [bundle],
        out_dir,
        repo_email_pipeline_root=root,
        require_git_ignored=False,
    )
    assert set(written) == {
        "summary.json",
        "attachment_manifest.json",
        "extraction_manifest.json",
        "evidence_chunks.jsonl",
        "walkthrough.md",
    }
    summary = json.loads((out_dir / "summary.json").read_text())
    assert summary["attachments_discovered"] == 3
    assert summary["tender_summaries"][0]["bundle_complete"] is False
    assert summary["outcome_totals"]["needs_ocr"] == 1

    manifest = json.loads((out_dir / "attachment_manifest.json").read_text())
    rows = manifest["tenders"][0]["attachments"]
    assert len(rows) == 3
    assert all(row["outcome"] for row in rows)

    chunk_lines = (out_dir / "evidence_chunks.jsonl").read_text().strip().splitlines()
    assert chunk_lines
    first = json.loads(chunk_lines[0])
    assert {"chunk_id", "attachment_id", "locator", "text_sha256"} <= set(first)

    walkthrough = (out_dir / "walkthrough.md").read_text()
    assert "INCOMPLETE" in walkthrough
    assert bundle.semantic_digest in walkthrough

    for name in written:
        text = (out_dir / name).read_text()
        assert "qs=" not in text and "enc=" not in text


def test_build_summary_aggregates_multiple_tenders() -> None:
    first = build_tender_bundle("T-1", _source([("a.txt", "text/plain", b"uno")]))
    second = build_tender_bundle(
        "T-2", _source([("b.bin", "application/octet-stream", bytes(range(8)) * 40)])
    )
    summary = build_summary([first, second])
    assert summary["tenders"] == 2
    assert summary["attachments_discovered"] == 2
    assert summary["bundles_complete"] == 1
    assert summary["bundles_incomplete"] == 1


# --- security: no execution, bounded parsing ---------------------------------


def test_xml_entity_declaration_is_refused() -> None:
    payload = (
        b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
        b"<!ENTITY lol2 '&lol;&lol;'>]><root>&lol2;</root>"
    )
    output = extract_payload(payload, detected_format="xml")
    assert output.outcome == "extraction_failed"
    assert "xml_entity_declaration_rejected" in output.warnings


def test_xml_size_limit_is_partial() -> None:
    payload = b"<root>" + (b"<i>dato</i>" * 5000) + b"</root>"
    output = extract_payload(
        payload, detected_format="xml", limits=ExtractionLimits(max_xml_chars=200)
    )
    assert output.outcome in {OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT, "extraction_failed"}


def test_macro_enabled_docx_is_flagged_and_not_executed() -> None:
    base = make_docx(paragraph="documento con macro")
    import io

    buffer = io.BytesIO(base)
    out = io.BytesIO()
    with zipfile.ZipFile(buffer) as src, zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            dst.writestr(info, src.read(info.filename))
        dst.writestr("word/vbaProject.bin", b"\x00fake-macro")
    output = extract_payload(out.getvalue(), detected_format="docx")
    assert "macro_or_binary_part_present_not_executed" in output.warnings
    assert any("documento con macro" in c.text for c in output.chunks)


def test_huge_text_node_is_bounded_by_char_budget() -> None:
    payload = ("A" * 200_000).encode("utf-8")
    output = extract_payload(
        payload,
        detected_format="txt",
        limits=ExtractionLimits(max_chars_per_attachment=1000, max_chunk_chars=100),
    )
    assert output.char_count <= 1000
    assert output.outcome == OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT


def test_extreme_sheet_with_tiny_cap_stays_bounded() -> None:
    payload = make_xlsx(["S1"], cells={"S1": [(r, 1, "x" * 50) for r in range(1, 500)]})
    output = extract_payload(
        payload,
        detected_format="xlsx",
        limits=ExtractionLimits(max_chars_per_attachment=500, max_xlsx_cells=100),
    )
    assert output.char_count <= 500
    assert output.outcome == OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT


def test_archive_member_bytes_are_capped_per_member() -> None:
    payload = make_zip([("grande.txt", b"B" * 10000)])
    members = iter_archive_members(
        payload,
        limits=ArchiveLimits(max_member_uncompressed_bytes=100),
    )
    assert len(members) == 1
    assert len(members[0].payload) == 100
    assert members[0].truncated is True
    assert "archive_bytes_limit" in members[0].warnings
