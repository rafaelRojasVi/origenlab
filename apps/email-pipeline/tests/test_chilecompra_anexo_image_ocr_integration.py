from __future__ import annotations

import io

from PIL import Image

import origenlab_email_pipeline.chilecompra_anexo_evidence.extract as extract_module
from origenlab_email_pipeline.chilecompra_anexo_evidence.constants import (
    OUTCOME_EXTRACTION_SUCCESS,
    OUTCOME_NEEDS_OCR,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.ocr import OcrResult


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def test_direct_image_ocr_success_becomes_extractable_evidence(monkeypatch) -> None:
    monkeypatch.setattr(
        extract_module,
        "extract_image_text",
        lambda payload, *, limits: OcrResult(
            text="PLAZO DE PAGO 30 DIAS",
            line_count=1,
            mean_score=0.99,
        ),
    )

    output = extract_module.extract_payload(
        _png(),
        detected_format="image",
    )

    assert output.outcome == OUTCOME_EXTRACTION_SUCCESS
    assert output.extractor == "rapidocr_onnx"
    assert len(output.chunks) == 1
    assert output.chunks[0].locator_type == "image"
    assert output.chunks[0].locator == {"part": 1}
    assert output.chunks[0].text == "PLAZO DE PAGO 30 DIAS"


def test_direct_image_ocr_failure_remains_needs_ocr(monkeypatch) -> None:
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
        _png(),
        detected_format="image",
    )

    assert output.outcome == OUTCOME_NEEDS_OCR
    assert output.chunks == []
    assert "ocr_failed:RuntimeError" in output.warnings
    assert "image_attachment_requires_ocr" in output.warnings
