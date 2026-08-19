from __future__ import annotations

import io
from types import SimpleNamespace

from PIL import Image

from origenlab_email_pipeline.chilecompra_anexo_evidence import ocr


def _png(width: int = 32, height: int = 32) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


class _FakeEngine:
    def __init__(self, result=None, error: Exception | None = None):
        self.result = result
        self.error = error

    def __call__(self, payload: bytes):
        if self.error is not None:
            raise self.error
        return self.result


def test_ocr_returns_normalized_text(monkeypatch) -> None:
    monkeypatch.setattr(
        ocr,
        "_engine",
        lambda *_: _FakeEngine(
            SimpleNamespace(
                txts=[
                    "PLAZO DE PAGO 30 DIAS",
                    "GARANTIA DE FIEL CUMPLIMIENTO 5 POR CIENTO",
                ],
                scores=[0.98, 0.96],
            )
        ),
    )

    result = ocr.extract_image_text(_png())

    assert result.usable is True
    assert result.line_count == 2
    assert result.text == (
        "PLAZO DE PAGO 30 DIAS\nGARANTIA DE FIEL CUMPLIMIENTO 5 POR CIENTO"
    )
    assert result.mean_score == 0.97
    assert result.warning is None


def test_ocr_failure_remains_soft_coverage_debt(monkeypatch) -> None:
    monkeypatch.setattr(
        ocr,
        "_engine",
        lambda *_: _FakeEngine(error=RuntimeError("engine failed")),
    )

    result = ocr.extract_image_text(_png())

    assert result.usable is False
    assert result.text == ""
    assert result.warning == "ocr_failed:RuntimeError"


def test_ocr_refuses_oversized_image_without_running_engine(monkeypatch) -> None:
    called = False

    def forbidden_engine(*_):
        nonlocal called
        called = True
        raise AssertionError("OCR engine must not run")

    monkeypatch.setattr(ocr, "_engine", forbidden_engine)

    payload = _png()
    result = ocr.extract_image_text(
        payload,
        limits=ocr.OcrLimits(max_image_bytes=len(payload) - 1),
    )

    assert called is False
    assert result.usable is False
    assert result.warning.startswith("ocr_image_bytes_limit:")


def test_ocr_refuses_excessive_dimensions_without_running_engine(monkeypatch) -> None:
    called = False

    def forbidden_engine(*_):
        nonlocal called
        called = True
        raise AssertionError("OCR engine must not run")

    monkeypatch.setattr(ocr, "_engine", forbidden_engine)

    result = ocr.extract_image_text(
        _png(width=64, height=32),
        limits=ocr.OcrLimits(max_image_side_pixels=50),
    )

    assert called is False
    assert result.usable is False
    assert result.warning == "ocr_image_side_limit:50/64x32"


def test_ocr_refuses_excessive_pixel_count_without_running_engine(monkeypatch) -> None:
    called = False

    def forbidden_engine(*_):
        nonlocal called
        called = True
        raise AssertionError("OCR engine must not run")

    monkeypatch.setattr(ocr, "_engine", forbidden_engine)

    result = ocr.extract_image_text(
        _png(width=20, height=20),
        limits=ocr.OcrLimits(max_image_pixels=399),
    )

    assert called is False
    assert result.usable is False
    assert result.warning == "ocr_image_pixels_limit:399/400"


def test_ocr_short_result_is_not_treated_as_complete(monkeypatch) -> None:
    monkeypatch.setattr(
        ocr,
        "_engine",
        lambda *_: _FakeEngine(
            SimpleNamespace(
                txts=["OK"],
                scores=[0.99],
            )
        ),
    )

    result = ocr.extract_image_text(_png())

    assert result.usable is False
    assert result.warning == "ocr_insufficient_text"
