"""Text of an uploaded tender anexo, with the locator each passage came from.

V1's `chilecompra_anexo_evidence` does the reading (format detection by signature, bounded
per-format extractors, OCR for scanned pages, named outcomes instead of silent emptiness). This
module only turns V1's chunk locators into the short strings a person can find in the original
(`p.3`, `¶12`, `Hoja1!A1:F40`) and renders the set for the model. It never reads a file itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final

from origenlab_worker import v1_reuse

# An outcome that is not an error and not a text: the later checklist turns it into
# «Falta: convertir a Word (.docx)» (ruling R13).
WARNING_NO_TEXT: Final = "sin_texto_extraible"
_NO_TEXT_OUTCOMES: Final = frozenset({v1_reuse.OUTCOME_EXTRACTED_EMPTY, v1_reuse.OUTCOME_NEEDS_OCR})


class AnnexSetTooLarge(Exception):
    """The annexes together exceed what one model call may carry; the caller must split, not cut."""


@dataclass(frozen=True)
class AnnexPage:
    locator: str
    text: str


@dataclass
class AnnexText:
    document_id: str
    filename: str
    outcome: str
    pages: list[AnnexPage] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _column_letter(n: int) -> str:
    letters = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters or "A"


def _xlsx_width(text: str) -> int:
    widest = 1
    for line in text.split("\n"):
        if line.startswith("[r"):
            widest = max(widest, line.count(" | ") + 1)
    return widest


def _locator(locator_type: str, loc: dict[str, object], text: str) -> str:
    if locator_type == "pdf_page":
        return f"p.{loc['page']}"
    if locator_type == "docx_paragraph":
        section = str(loc.get("section", "body"))
        suffix = "" if section == "body" else f" ({section})"
        return f"¶{loc['paragraph']}{suffix}"
    if locator_type == "docx_table":
        section = str(loc.get("section", "body"))
        suffix = "" if section == "body" else f" ({section})"
        return f"Tabla {loc['table']}{suffix}"
    if locator_type == "xlsx_rows":
        last = _column_letter(_xlsx_width(text))
        return f"{loc['sheet']}!A{loc['start_row']}:{last}{loc['end_row']}"
    if locator_type == "csv_rows":
        return f"filas {loc['start_row']}-{loc['end_row']}"
    if locator_type == "image":
        return "imagen"
    return f"parte {loc.get('part', 1)}"


def extract_annex(document_id: str, filename: str, content_type: str, data: bytes) -> AnnexText:
    """Read one uploaded anexo. Never returns an empty result without an outcome and a warning."""
    detection = v1_reuse.detect_format(data, content_type=content_type, filename=filename)
    out = v1_reuse.extract_payload(data, detected_format=detection.detected_format)
    warnings = [*detection.warnings, *out.warnings]
    if out.error_message:
        warnings.append(f"error:{out.error_message}")

    pages: list[AnnexPage] = []
    for chunk in out.chunks:
        locator = _locator(chunk.locator_type, chunk.locator, chunk.text)
        if pages and pages[-1].locator == locator and chunk.locator_type in {"pdf_page", "docx_paragraph"}:
            # V1 splits one long page/paragraph into parts; the person sees one page.
            pages[-1] = AnnexPage(locator, pages[-1].text + chunk.text)
        else:
            pages.append(AnnexPage(locator, chunk.text))

    outcome = out.outcome
    if not pages and outcome in _NO_TEXT_OUTCOMES:
        warnings.append(f"{WARNING_NO_TEXT}:{outcome}")
        outcome = v1_reuse.OUTCOME_NEEDS_CONVERTER
    if not pages and outcome == v1_reuse.OUTCOME_NEEDS_CONVERTER and not warnings:
        warnings.append(WARNING_NO_TEXT)
    return AnnexText(document_id, filename, outcome, pages, warnings)


def _safe(value: str) -> str:
    return re.sub(r"[\r\n<>]", " ", value)


def render_for_model(annexes: list[AnnexText], *, max_chars: int = 600_000) -> str:
    blocks: list[str] = []
    for annex in annexes:
        lines = [f"<<<ARCHIVO id={_safe(annex.document_id)} nombre={_safe(annex.filename)}>>>"]
        for page in annex.pages:
            lines.append(f"[[{page.locator}]]\n{page.text}")
        blocks.append("\n".join(lines))
    text = "\n\n".join(blocks)
    if len(text) > max_chars:
        raise AnnexSetTooLarge(f"{len(text)} characters exceed the {max_chars} limit")
    return text
