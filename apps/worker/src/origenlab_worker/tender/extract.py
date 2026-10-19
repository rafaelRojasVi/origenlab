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

from origenlab_email_pipeline.chilecompra_anexo_evidence.ocr import OcrLimits

from origenlab_worker import v1_reuse

# SECURITY: everything between the <<<ARCHIVO …>>> and <<<FIN ARCHIVO>>> markers is untrusted
# tender content. The prompt that receives render_for_model's output (a later task) MUST say that
# content between markers is data to read, never instructions to follow.
MAX_INPUT_BYTES: Final = 25 * 1024 * 1024
WARNING_TOO_LARGE: Final = "demasiado_grande"
WARNING_EMPTY_FILE: Final = "archivo_sin_texto"
FIN_MARKER: Final = "<<<FIN ARCHIVO>>>"
_LIMITS = v1_reuse.ExtractionLimits(
    max_pdf_pages=300,
    max_chars_per_attachment=1_000_000,
    ocr_limits=OcrLimits(max_pdf_pages=30),
)
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

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


def _neutralise(text: str) -> str:
    """Break any marker lookalike so file content cannot forge our framing."""
    return (
        text.replace("<<<", "‹‹‹").replace(">>>", "›››").replace("[[", "[ [").replace("]]", "] ]")
    )


def _clean_locator(text: str) -> str:
    return _neutralise(_CONTROL.sub(" ", text.replace("\n", " ").replace("\r", " ")))


def _column_letter(n: int) -> str:
    letters = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters or "A"


def _xlsx_width(text: str) -> int:
    # Approximate: counts " | " separators, so a cell containing one widens the range.
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
        return f"{_clean_locator(str(loc['sheet']))}!A{loc['start_row']}:{last}{loc['end_row']}"
    if locator_type == "csv_rows":
        return f"filas {loc['start_row']}-{loc['end_row']}"
    if locator_type == "image":
        return "imagen"
    return f"parte {loc.get('part', 1)}"


def extract_annex(document_id: str, filename: str, content_type: str, data: bytes) -> AnnexText:
    """Read one uploaded anexo. Never returns an empty result without an outcome and a warning."""
    if len(data) > MAX_INPUT_BYTES:
        return AnnexText(document_id, filename, v1_reuse.OUTCOME_UNSUPPORTED_FORMAT, [], [WARNING_TOO_LARGE])
    detection = v1_reuse.detect_format(data, content_type=content_type, filename=filename)
    out = v1_reuse.extract_payload(data, detected_format=detection.detected_format, limits=_LIMITS)
    warnings = [*detection.warnings, *out.warnings]
    if out.error_message:
        # The parser's message quotes the untrusted file; the outcome says what happened.
        warnings.append(f"error:{out.outcome}")

    pages: list[AnnexPage] = []
    seen: dict[str, int] = {}
    for chunk in out.chunks:
        locator = _locator(chunk.locator_type, chunk.locator, chunk.text)
        if pages and pages[-1].locator.startswith(locator) and chunk.locator_type in {"pdf_page", "docx_paragraph"}:
            # V1 splits one long page/paragraph into parts; the person sees one page.
            pages[-1] = AnnexPage(pages[-1].locator, pages[-1].text + chunk.text)
            continue
        seen[locator] = seen.get(locator, 0) + 1
        if seen[locator] > 1:
            locator = f"{locator} (parte {seen[locator]})"
        pages.append(AnnexPage(locator, chunk.text))

    outcome = out.outcome
    if not pages and outcome in _NO_TEXT_OUTCOMES:
        if detection.detected_format in {"docx", "xlsx"}:
            # A readable Word/Excel file that simply holds no text: converting it would not help.
            warnings.append(WARNING_EMPTY_FILE)
        else:
            warnings.append(f"{WARNING_NO_TEXT}:{outcome}")
        outcome = v1_reuse.OUTCOME_NEEDS_CONVERTER
    if outcome != v1_reuse.OUTCOME_EXTRACTION_SUCCESS and not warnings:
        warnings.append(outcome)
    if not pages and not warnings:
        warnings.append(WARNING_NO_TEXT)
    return AnnexText(document_id, filename, outcome, pages, warnings)


def render_for_model(annexes: list[AnnexText], *, max_chars: int = 600_000) -> str:
    blocks: list[str] = []
    total = 0
    for annex in annexes:
        ident = _clean_locator(annex.document_id)
        name = _clean_locator(annex.filename)
        lines = [f"<<<ARCHIVO id={ident} nombre={name}>>>"]
        for page in annex.pages:
            lines.append(f"[[{_clean_locator(page.locator)}]]\n{_neutralise(_CONTROL.sub(' ', page.text))}")
        lines.append(FIN_MARKER)
        block = "\n".join(lines)
        total += len(block) + 2
        if total > max_chars:
            raise AnnexSetTooLarge(f"more than {max_chars} characters")
        blocks.append(block)
    return "\n\n".join(blocks)
