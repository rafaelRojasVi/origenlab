"""Bounded local OCR fallback for ChileCompra annex evidence.

OCR is an evidence-extraction fallback only. It does not infer commercial
terms and an OCR failure never proves that a term is absent.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class OcrLimits:
    """Resource and acceptance limits for a single OCR operation."""

    max_image_bytes: int = 16 * 1024 * 1024
    max_pdf_pages: int = 50
    max_image_pixels: int = 25_000_000
    max_image_side_pixels: int = 10_000
    inference_max_side_pixels: int = 2_000
    min_text_chars: int = 8
    min_text_score: float = 0.50


@dataclass(frozen=True)
class OcrResult:
    text: str
    line_count: int
    mean_score: float | None
    warning: str | None = None

    @property
    def usable(self) -> bool:
        return bool(self.text.strip())


@lru_cache(maxsize=8)
def _engine(inference_max_side_pixels: int, min_text_score: float):
    from rapidocr import RapidOCR

    return RapidOCR(
        params={
            "Rec.lang_type": "es",
            "Global.max_side_len": inference_max_side_pixels,
            "Global.text_score": min_text_score,
        }
    )


def _image_dimensions(payload: bytes) -> tuple[int, int]:
    from PIL import Image

    with Image.open(io.BytesIO(payload)) as image:
        return int(image.width), int(image.height)


def extract_image_text(
    payload: bytes,
    *,
    limits: OcrLimits | None = None,
) -> OcrResult:
    """OCR one bounded image payload, failing soft on engine/runtime errors."""

    active = limits or OcrLimits()

    if not payload:
        return OcrResult(
            text="",
            line_count=0,
            mean_score=None,
            warning="ocr_empty_image",
        )

    if len(payload) > active.max_image_bytes:
        return OcrResult(
            text="",
            line_count=0,
            mean_score=None,
            warning=f"ocr_image_bytes_limit:{active.max_image_bytes}/{len(payload)}",
        )

    try:
        width, height = _image_dimensions(payload)
    except Exception as exc:  # noqa: BLE001 - malformed image remains coverage debt
        return OcrResult(
            text="",
            line_count=0,
            mean_score=None,
            warning=f"ocr_image_unreadable:{type(exc).__name__}",
        )

    if width <= 0 or height <= 0:
        return OcrResult(
            text="",
            line_count=0,
            mean_score=None,
            warning="ocr_image_invalid_dimensions",
        )

    if max(width, height) > active.max_image_side_pixels:
        return OcrResult(
            text="",
            line_count=0,
            mean_score=None,
            warning=(
                f"ocr_image_side_limit:{active.max_image_side_pixels}/{width}x{height}"
            ),
        )

    pixels = width * height
    if pixels > active.max_image_pixels:
        return OcrResult(
            text="",
            line_count=0,
            mean_score=None,
            warning=f"ocr_image_pixels_limit:{active.max_image_pixels}/{pixels}",
        )

    try:
        result = _engine(
            active.inference_max_side_pixels,
            active.min_text_score,
        )(payload)

        texts = [
            str(value).strip() for value in (result.txts or ()) if str(value).strip()
        ]
        scores = [float(value) for value in (result.scores or ()) if value is not None]
    except Exception as exc:  # noqa: BLE001 - OCR failures remain coverage debt
        return OcrResult(
            text="",
            line_count=0,
            mean_score=None,
            warning=f"ocr_failed:{type(exc).__name__}",
        )

    text = "\n".join(texts).strip()
    mean_score = (sum(scores) / len(scores)) if scores else None

    if len(text) < active.min_text_chars:
        return OcrResult(
            text="",
            line_count=len(texts),
            mean_score=mean_score,
            warning="ocr_insufficient_text",
        )

    return OcrResult(
        text=text,
        line_count=len(texts),
        mean_score=mean_score,
    )
