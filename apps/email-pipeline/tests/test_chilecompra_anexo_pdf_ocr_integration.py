from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))

import origenlab_email_pipeline.chilecompra_anexo_evidence.extract as extract_module  # noqa: E402
from chilecompra_anexo_evidence.synthetic_documents import make_image_only_pdf  # noqa: E402
from origenlab_email_pipeline.chilecompra_anexo_evidence.constants import (
    OUTCOME_EXTRACTION_SUCCESS,
    OUTCOME_NEEDS_OCR,
    OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.extract import (
    ExtractionLimits,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.ocr import (
    OcrLimits,
    OcrResult,
)


def test_image_only_pdf_ocr_success_preserves_page_locator(monkeypatch) -> None:
    calls = 0

    def fake_ocr(payload: bytes, *, limits: OcrLimits) -> OcrResult:
        nonlocal calls
        calls += 1
        assert payload.startswith(b"\x89PNG\r\n\x1a\n")
        return OcrResult(
            text=f"PLAZO DE PAGO PAGINA {calls}",
            line_count=1,
            mean_score=0.99,
        )

    monkeypatch.setattr(extract_module, "extract_image_text", fake_ocr)

    output = extract_module.extract_payload(
        make_image_only_pdf(2),
        detected_format="pdf",
    )

    assert output.outcome == OUTCOME_EXTRACTION_SUCCESS
    assert output.extractor == "pymupdf_page_text+rapidocr_onnx"
    assert output.page_count == 2
    assert calls == 2

    assert [chunk.locator_type for chunk in output.chunks] == [
        "pdf_page",
        "pdf_page",
    ]
    assert [chunk.locator["page"] for chunk in output.chunks] == [1, 2]
    assert all(chunk.locator["page_count"] == 2 for chunk in output.chunks)


def test_image_only_pdf_ocr_failure_remains_needs_ocr(monkeypatch) -> None:
    monkeypatch.setattr(
        extract_module,
        "extract_image_text",
        lambda payload, *, limits: OcrResult(
            text="",
            line_count=0,
            mean_score=None,
            warning="ocr_failed:RuntimeError",
        ),
    )

    output = extract_module.extract_payload(
        make_image_only_pdf(2),
        detected_format="pdf",
    )

    assert output.outcome == OUTCOME_NEEDS_OCR
    assert output.chunks == []
    assert "pdf_no_extractable_text" in output.warnings
    assert any(
        warning.startswith("pdf_ocr_page:1:ocr_failed:") for warning in output.warnings
    )


def test_partial_pdf_ocr_keeps_text_but_retains_coverage_debt(monkeypatch) -> None:
    calls = 0

    def fake_ocr(payload: bytes, *, limits: OcrLimits) -> OcrResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            return OcrResult(
                text="GARANTIA DE FIEL CUMPLIMIENTO",
                line_count=1,
                mean_score=0.99,
            )
        return OcrResult(
            text="",
            line_count=0,
            mean_score=None,
            warning="ocr_insufficient_text",
        )

    monkeypatch.setattr(extract_module, "extract_image_text", fake_ocr)

    output = extract_module.extract_payload(
        make_image_only_pdf(2),
        detected_format="pdf",
    )

    assert output.outcome == OUTCOME_NEEDS_OCR
    assert len(output.chunks) == 1
    assert output.chunks[0].locator["page"] == 1
    assert "GARANTIA" in output.chunks[0].text
    assert "pdf_ocr_pages_unreadable:1/2" in output.warnings


def test_pdf_ocr_page_cap_is_explicit_partial_result(monkeypatch) -> None:
    monkeypatch.setattr(
        extract_module,
        "extract_image_text",
        lambda payload, *, limits: OcrResult(
            text="PLAZO DE ENTREGA 15 DIAS",
            line_count=1,
            mean_score=0.99,
        ),
    )

    output = extract_module.extract_payload(
        make_image_only_pdf(3),
        detected_format="pdf",
        limits=ExtractionLimits(
            ocr_limits=OcrLimits(max_pdf_pages=1),
        ),
    )

    assert output.outcome == OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT
    assert len(output.chunks) == 1
    assert output.chunks[0].locator["page"] == 1
    assert "pdf_ocr_page_limit:1/3" in output.warnings
