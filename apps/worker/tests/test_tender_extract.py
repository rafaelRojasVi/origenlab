"""Tender anexo text with page locators — V1's extraction, plus a thin locator layer (Task 8)."""

from __future__ import annotations

import io

import pytest

from origenlab_worker import v1_reuse
from origenlab_worker.tender.extract import AnnexPage, AnnexSetTooLarge, AnnexText, extract_annex, render_for_model

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _pdf(*page_texts: str) -> bytes:
    import pymupdf

    doc = pymupdf.open()
    for text in page_texts:
        page = doc.new_page()
        page.insert_text((72, 100), text, fontsize=14)
    data = doc.tobytes()
    doc.close()
    return data


def _scanned_pdf(text: str) -> bytes:
    """A PDF whose only content is a picture of the text: no text layer, so it needs OCR."""
    import pymupdf

    src = pymupdf.open()
    page = src.new_page()
    page.insert_text((72, 150), text, fontsize=36)
    png = page.get_pixmap(dpi=150).tobytes("png")
    src.close()
    out = pymupdf.open()
    page = out.new_page()
    page.insert_image(page.rect, stream=png)
    data = out.tobytes()
    out.close()
    return data


def test_a_two_page_pdf_gives_page_locators() -> None:
    annex = extract_annex("d1", "bases.pdf", PDF, _pdf("Bases administrativas pagina uno", "Plazo de entrega treinta dias"))
    assert annex.outcome == v1_reuse.OUTCOME_EXTRACTION_SUCCESS
    assert [p.locator for p in annex.pages] == ["p.1", "p.2"]
    assert "uno" in annex.pages[0].text and "treinta" in annex.pages[1].text
    assert annex.document_id == "d1" and annex.filename == "bases.pdf"


def test_a_docx_gives_paragraph_locators_and_table_cell_text() -> None:
    import docx

    d = docx.Document()
    d.add_paragraph("Requisitos tecnicos del equipo")
    d.add_paragraph("Balanza analitica de precision")
    table = d.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Capacidad"
    table.rows[0].cells[1].text = "220 g"
    buf = io.BytesIO()
    d.save(buf)
    annex = extract_annex("d2", "tecnico.docx", DOCX, buf.getvalue())
    assert annex.outcome == v1_reuse.OUTCOME_EXTRACTION_SUCCESS
    assert [p.locator for p in annex.pages][:2] == ["¶1", "¶2"]
    assert any("Capacidad" in p.text and "220 g" in p.text for p in annex.pages)


def test_an_xlsx_is_chunked_by_rows_with_a_sheet_range_locator() -> None:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Hoja1"
    for r in range(1, 41):
        ws.append([f"item {r}", r, "un", "x", "y", "z"])
    buf = io.BytesIO()
    wb.save(buf)
    annex = extract_annex("d3", "oferta.xlsx", XLSX, buf.getvalue())
    assert annex.outcome == v1_reuse.OUTCOME_EXTRACTION_SUCCESS
    assert len(annex.pages) >= 1
    assert annex.pages[0].locator.startswith("Hoja1!A1:F")
    assert annex.pages[-1].locator.endswith("F40")
    assert "item 40" in annex.pages[-1].text


def test_a_scanned_page_gives_text_through_ocr() -> None:
    annex = extract_annex("d4", "escaneado.pdf", PDF, _scanned_pdf("BALANZA ANALITICA"))
    assert annex.outcome in {v1_reuse.OUTCOME_EXTRACTION_SUCCESS, v1_reuse.OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT}
    assert annex.outcome != "failed"
    assert [p.locator for p in annex.pages] == ["p.1"]
    assert "BALANZA" in annex.pages[0].text.upper()


def test_a_legacy_doc_needs_a_converter_and_says_so() -> None:
    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512
    annex = extract_annex("d5", "formulario.doc", "application/msword", ole)
    assert annex.outcome == v1_reuse.OUTCOME_NEEDS_CONVERTER
    assert annex.pages == []
    assert annex.warnings


def test_a_pdf_with_no_text_and_no_readable_picture_is_never_an_empty_silent_result() -> None:
    import pymupdf

    doc = pymupdf.open()
    doc.new_page()
    data = doc.tobytes()
    doc.close()
    annex = extract_annex("d6", "vacio.pdf", PDF, data)
    assert annex.pages == []
    assert annex.outcome == v1_reuse.OUTCOME_NEEDS_CONVERTER
    assert annex.warnings


def test_render_for_model_marks_files_and_pages() -> None:
    a = AnnexText("d1", "bases.pdf", "extraction_success",
                  [AnnexPage("p.1", "uno"), AnnexPage("p.2", "dos")], [])
    text = render_for_model([a])
    assert "<<<ARCHIVO id=d1 nombre=bases.pdf>>>" in text
    assert "[[p.1]]\nuno" in text and "[[p.2]]\ndos" in text


def test_render_for_model_over_the_limit_raises_instead_of_truncating() -> None:
    a = AnnexText("d1", "grande.pdf", "extraction_success", [AnnexPage("p.1", "x" * 500)], [])
    with pytest.raises(AnnexSetTooLarge):
        render_for_model([a], max_chars=100)
