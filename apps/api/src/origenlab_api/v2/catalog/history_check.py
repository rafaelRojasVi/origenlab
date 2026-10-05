"""Quote history, pure: the template parser, the AI/template cross-check, the evidence payload.

The quote-economics study extracted each OrigenLab quote PDF twice: an AI extraction (one JSON
per PDF) and the PDF's text (`pdftotext -layout`). The text is parsed here deterministically, for
the two OrigenLab layouts, into `TemplateLine`s — item label, model, line total — and each AI line
is checked against them:

* `verified` — a template line with the same total and the same model key (`model_total_match`),
  or failing that the same item label (`item_total_match`, even when REF and model differ — owner
  ruling 2026-10-05), or failing that a template line with neither (`total_match`);
* `disputed` — the same model, or the same item label, but another total;
* `single_source` — only one side has the line (also an AI line with no total).

Layouts, as pdftotext prints them:

* docx/web template: header words `ITEM · REF · DETALLE · TOTAL` (centred, so possibly over three
  lines); rows separated by blank lines; a row's REF and its amount may sit on different lines of
  the row. The REF is whatever is printed in the REF column (within a few characters of it).
* xlsx master: header `ITEM · MARCA / REF. · DETALLE · CANT. · PRECIO UNIT. · TOTAL`; one printed
  line per row holding `<item> <brand> · <model> … <qty> … <amount>`.

An amount is a CLP amount at the end of a line: `$`, then digits in thousands groups separated by
`.` (the docx template) or `,` (the xlsx master) — one separator per amount, never decimals; `$ 0`
and `$ 500` count. Totals rows (neto, IVA, total, …) carry no
item and are skipped. A text in neither layout still gives its priced lines, with no item or model.

**No client identity.** `quote_document_payload` carries the quote's number, date, client *type*,
currency, net total, template and file hash — never the client's institution, unit, contact,
city or the document's conditions — so evidence payloads stay free of client identity.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Literal, NamedTuple

from origenlab_api.v2.catalog import importing
from origenlab_api.v2.catalog.keys import model_key

CheckStatus = Literal["verified", "disputed", "single_source"]
CURRENCIES = ("CLP", "USD", "EUR")
CLIENT_TYPES = ("university", "public_health", "public_other", "private_company", "research_center", "individual",
                "unknown")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_AMOUNT = re.compile(r"\$\s?(\d{1,3}(?:([.,])\d{3})?(?:\2\d{3})*)\s*$")
_CELL = re.compile(r"\S+(?: \S+)*")
_ITEM = re.compile(r"^(\d{1,3}(?:\.\d{1,3})*)\.?$")
_XLSX_ROW = re.compile(r"^\s*(?P<item>\d{1,3}(?:\.\d{1,3})*)\.?\s+(?P<brand>[^·\s][^·]*?)\s+·\s+(?P<model>\S+)")
_SUMMARY = re.compile(r"(?i)\b(?:sub\s*-?\s*total(?:es)?|total(?:es)?|neto|iva|descuentos?|dcto|monto)\b")
_WORD = re.compile(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]+\.?")
_DOCX_HEADER = frozenset({"ITEM", "REF", "DETALLE", "TOTAL"})
#: How far (in characters) a REF cell may start from the REF header's column.
_REF_SLACK = 5
#: A longer REF-column cell is description text.
_REF_MAX_WORDS, _REF_MAX_CHARS = 3, 30
#: How far right of the line start an item label may be printed.
_ITEM_MAX_COLUMN = 12


class TemplateLine(NamedTuple):
    item_label: str | None
    model: str | None
    line_total: Decimal


@dataclass(frozen=True)
class CheckedLine:
    """One line of the cross-check: an AI line (`ai_index`), a template-only line, or both."""
    ai_index: int | None
    template_index: int | None
    item_label: str | None
    model: str | None
    ai_total: Decimal | None
    template_total: Decimal | None
    check_status: CheckStatus
    #: Why a verified line matched: `model_total_match`, `item_total_match` (same item label and
    #: total, whatever REF and model say) or `total_match` (a bare template line); None otherwise.
    reason: str | None = None


# ------------------------------------------------------------------ parsing the text

def _amount(line: str) -> tuple[Decimal, int] | None:
    """The CLP amount a line ends with, and where it starts."""
    m = _AMOUNT.search(line.rstrip())
    if m is None:
        return None
    digits = m.group(1) if m.group(2) is None else m.group(1).replace(m.group(2), "")
    return Decimal(digits), m.start()


def _cells(line: str) -> list[tuple[int, str]]:
    """Cells of a layout line: runs of text separated by two or more spaces, with their columns."""
    return [(m.start(), m.group()) for m in _CELL.finditer(line)]


def _item(label: str | None) -> str | None:
    if label is None:
        return None
    m = _ITEM.match(label.strip())
    return m.group(1) if m else None


def _words(line: str) -> set[str]:
    return {w.upper().rstrip(".") for w in _WORD.findall(line)}


def _header(lines: list[str]) -> tuple[str, int | None, int]:
    """(layout, REF column, index of the header's last line): `xlsx`, `docx`, or `other` (no header).

    The docx header is the lines (at most three in a row, as centring splits it) that print only
    header words and together print all four.
    """
    for n, line in enumerate(lines):
        if {"MARCA", "CANT"} <= _words(line):
            return "xlsx", None, n
    for n in range(len(lines)):
        window = [(n + i, line) for i, line in enumerate(lines[n:n + 3])
                  if _words(line) and _words(line) <= _DOCX_HEADER]
        if window and set().union(*(_words(line) for _, line in window)) == _DOCX_HEADER:
            ref_col = next(m.start() for _, line in window for m in re.finditer(r"\bREF\b", line, re.IGNORECASE))
            return "docx", ref_col, window[-1][0]
    return "other", None, -1


def _ref(cell: str) -> str | None:
    """A REF cell as printed; a long cell is description text that starts in the REF column, whose
    first word is kept only if it looks like a code (letters and digits)."""
    words = cell.split()
    if len(words) <= _REF_MAX_WORDS and len(cell) <= _REF_MAX_CHARS:
        return cell
    first = words[0].rstrip(",;:")
    return first if re.search(r"\d", first) and re.search(r"[A-Za-z]", first) else None


def _blocks(lines: list[str]) -> list[list[str]]:
    blocks: list[list[str]] = [[]]
    for line in lines:
        if line.strip():
            blocks[-1].append(line)
        elif blocks[-1]:
            blocks.append([])
    return [b for b in blocks if b]


def _summary(text: str) -> bool:
    return bool(_SUMMARY.search(text))


def _docx_rows(block: list[str], ref_col: int | None) -> list[TemplateLine]:
    priced = [(n, _amount(line)) for n, line in enumerate(block)]
    priced = [(n, a) for n, a in priced if a is not None]
    if not priced:
        return []
    groups = [block] if len(priced) == 1 else [[block[n]] for n, _ in priced]
    rows = []
    for group in groups:
        total, item, refs, words = None, None, [], []
        for line in group:
            found = _amount(line)
            body = line[:found[1]] if found else line
            if found:
                total = found[0]
            for col, cell in _cells(body):
                label = _item(cell) if col <= _ITEM_MAX_COLUMN and item is None else None
                if label is not None and (ref_col is None or col < ref_col - 2):
                    item = label
                elif ref_col is not None and abs(col - ref_col) <= _REF_SLACK and (ref := _ref(cell)):
                    refs.append(ref)
                else:
                    words.append(cell)
        assert total is not None  # noqa: S101 - every group holds its amount line
        if item is None and not refs and _summary(" ".join(words)):
            continue
        rows.append(TemplateLine(item, " ".join(refs) or None, total))
    return rows


def _xlsx_row(line: str) -> TemplateLine | None:
    found = _amount(line)
    if found is None:
        return None
    body = line[:found[1]]
    m = _XLSX_ROW.match(body)
    if m is not None:
        return TemplateLine(m.group("item"), m.group("model"), found[0])
    if _summary(body):
        return None
    return TemplateLine(None, None, found[0])


def _other_row(line: str) -> TemplateLine | None:
    found = _amount(line)
    if found is None:
        return None
    cells = _cells(line[:found[1]])
    item = _item(cells[0][1]) if cells and cells[0][0] <= _ITEM_MAX_COLUMN else None
    if item is None and _summary(line[:found[1]]):
        return None
    return TemplateLine(item, None, found[0])


def parse_template_lines(text: str) -> list[TemplateLine]:
    """The priced rows of a quote's layout text, in print order: `{item_label, model, line_total}`."""
    lines = text.replace("\f", "\n").splitlines()
    layout, ref_col, header_end = _header(lines)
    body = lines[header_end + 1:]
    if layout == "xlsx":
        return [row for line in body if (row := _xlsx_row(line)) is not None]
    if layout == "docx":
        return [row for block in _blocks(body) for row in _docx_rows(block, ref_col)]
    return [row for line in lines if (row := _other_row(line)) is not None]


# ------------------------------------------------------------------ the cross-check

def _total(value: Any) -> Decimal | None:
    number = importing.to_decimal(value)
    return number


def _ai_item(value: Any) -> str | None:
    """The AI's item as a label: a number with an integer value (`1`, `1.0`) is `"1"`."""
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool) and value == int(value):
        return str(int(value))
    return str(value)


def cross_check(ai_lines: list[dict[str, Any]], template_lines: list[TemplateLine]) -> list[CheckedLine]:
    """Every AI line (in order) with its status, then every template line no AI line matched.

    `ai_lines` are mappings with `item`, `model` and `line_total`. Each template line is used once.
    Verified pairs are settled first, over all lines, so a disputed pairing never takes the
    template line another AI line agrees with.
    """
    ai = [(_item(_ai_item(a.get("item"))), model_key(a.get("model")),
           _total(a.get("line_total"))) for a in ai_lines]
    tl = [(_item(t.item_label), model_key(t.model), t.line_total) for t in template_lines]
    unused = list(range(len(tl)))
    match: dict[int, tuple[int, CheckStatus, str | None]] = {}

    same_total = lambda a, t: a[2] == t[2]  # noqa: E731
    #: (accept, why a verified pair matched). Verified passes first, then the disputed ones.
    passes = [
        (lambda a, t: same_total(a, t) and a[1] is not None and t[1] == a[1], "model_total_match"),
        (lambda a, t: same_total(a, t) and a[0] is not None and t[0] == a[0], "item_total_match"),
        (lambda a, t: same_total(a, t) and t[1] is None and t[0] is None, "total_match"),
        (lambda a, t: a[1] is not None and t[1] == a[1], None),
        (lambda a, t: a[0] is not None and t[0] == a[0] and (t[1] is None or t[1] == a[1]), None),
    ]
    for accept, reason in passes:
        for i in range(len(ai)):
            if i in match or ai[i][2] is None:
                continue
            for j in unused:
                if accept(ai[i], tl[j]):
                    unused.remove(j)
                    verified = ai[i][2] == tl[j][2]
                    match[i] = (j, "verified" if verified else "disputed", reason if verified else None)
                    break

    out = []
    for i, a in enumerate(ai_lines):
        if i in match:
            j, status, why = match[i]
            out.append(CheckedLine(i, j, ai[i][0], a.get("model"), ai[i][2], tl[j][2], status, why))
        else:
            out.append(CheckedLine(i, None, ai[i][0], a.get("model"), ai[i][2], None, "single_source"))
    for j in unused:
        t = template_lines[j]
        out.append(CheckedLine(None, j, tl[j][0], t.model, None, t.line_total, "single_source"))
    return out


# ------------------------------------------------------------------ the evidence payload

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DMY = re.compile(r"^(\d{1,2})([-/])(\d{1,2})\2(\d{4})$")


def normalise_quote_date(value: Any) -> str | None:
    """`YYYY-MM-DD`, from ISO or the extraction's `DD-MM-YYYY` / `DD/MM/YYYY`; None stays None.

    Anything else is refused: the history sorts this value as text, so it must be ISO.
    """
    if value is None:
        return None
    try:
        if not isinstance(value, str):
            raise ValueError
        text = value.strip()
        if _ISO.match(text):
            return date.fromisoformat(text).isoformat()
        m = _DMY.match(text)
        if m is None:
            raise ValueError
        return date(int(m.group(4)), int(m.group(3)), int(m.group(1))).isoformat()
    except ValueError:
        raise ValueError("date: not a date as YYYY-MM-DD, DD-MM-YYYY or DD/MM/YYYY") from None


def _optional_text(field: str, value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field}: not text")
    return value.strip() or None


def quote_document_payload(extraction: dict[str, Any], sha256: str) -> dict[str, Any]:
    """The `evidence.source_record` payload of one quote PDF — no client name, unit, contact or city."""
    if not _SHA256.match(sha256 or "") or extraction.get("sha256") != sha256:
        raise ValueError("sha256: the extraction is not of this file")
    currency = extraction.get("currency")
    if currency not in CURRENCIES:
        raise ValueError(f"currency: not one of {', '.join(CURRENCIES)}")
    client_type = extraction.get("client_type")
    if client_type is not None and client_type not in CLIENT_TYPES:
        raise ValueError("client_type: not a known client type")
    try:
        net = importing.to_decimal(extraction.get("subtotal_net"))
    except ValueError:
        raise ValueError("subtotal_net: not a number") from None
    if net is not None and (not net.is_finite() or net < 0):
        raise ValueError("subtotal_net: not a non-negative number")
    return {
        "printed_quote_number": _optional_text("quote_number", extraction.get("quote_number")),
        "quote_date": normalise_quote_date(extraction.get("date")),
        "client_type": client_type,
        "currency": currency,
        "net_total": None if net is None else importing.decimal_text(net),
        "template": _optional_text("template", extraction.get("template")),
        "file_sha256": sha256,
    }
