"""Read-only identity audit of the staged quotation documents — what each PDF itself says.

The review queue (`quote_evidence_review`) proposes quote numbers from filename tokens.
This module reads the documents instead: it extracts the text of every staged PDF, pulls out
the client anchors the document states explicitly, and classifies how candidates relate to
each other. It is evidence for the operator, never a decision:

- **Only what the PDF says.** A client is anchored by an addressee line (`At.`, `Señores:`,
  `Cliente:` …), a RUT printed in that client block, or both. The issuer header (seller
  name, address, RUT) is never a client anchor. Identity is never taken from an address
  domain, a filename, a subject line, a shared brochure or a similar-looking name.
- **Original text is kept.** Names are normalized (accents, case, whitespace, legal
  suffixes) only to compare them; every field carries the exact line it came from, and every
  classification carries the lines that caused it.
- **Exact bytes first.** SHA-256 is the strongest duplicate key: identical bytes are one
  document, whatever the filenames say.
- **Numbers that disagree stay with a human.** A document whose own text carries more than
  one quote number, or whose filename token names a different serial than its text, gets no
  quote number here and is flagged for review. Nothing is merged, and no opportunity or quote
  is created — this module imports no database driver and writes no file.

Text comes from the PDF's own text layer; OCR runs only when a PDF has no usable text. This
module starts no process: the audit script supplies `pdftotext -layout` and `tesseract`,
and falls back to PyMuPDF's in-process text layer.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from itertools import combinations
from pathlib import Path
from typing import Any

from origenlab_api.v2.quote_evidence_review import ReviewItem, Staging

# --- vocabulary ------------------------------------------------------------------------

EXACT_DUPLICATE = "exact_duplicate"
SAME_CLIENT_SAME_QUOTE = "same_client_same_quote"
SAME_CLIENT_DIFFERENT_QUOTE = "same_client_different_quote"
DIFFERENT_EXPLICIT_CLIENT = "different_explicit_client"
CONFLICTING_CLIENT_EVIDENCE = "conflicting_client_evidence"
INSUFFICIENT_EVIDENCE = "insufficient_evidence"
BROCHURE_OR_GENERIC = "brochure_or_generic_document"
# A candidate whose single quotation is explicitly anchored and relates to no other
# candidate. Not a relation, so it sits outside the seven relation labels above.
UNIQUE_EXPLICIT_QUOTE = "unique_explicit_quote"

CLASSIFICATIONS = (
    EXACT_DUPLICATE,
    SAME_CLIENT_SAME_QUOTE,
    SAME_CLIENT_DIFFERENT_QUOTE,
    DIFFERENT_EXPLICIT_CLIENT,
    CONFLICTING_CLIENT_EVIDENCE,
    INSUFFICIENT_EVIDENCE,
    BROCHURE_OR_GENERIC,
    UNIQUE_EXPLICIT_QUOTE,
)

# Document classes.
DOC_QUOTATION = "quotation"
DOC_INSUFFICIENT = INSUFFICIENT_EVIDENCE
DOC_GENERIC = BROCHURE_OR_GENERIC
DOC_NOT_PDF = "not_pdf"

# Extraction methods.
METHOD_PDFTOTEXT = "pdftotext"
METHOD_PYMUPDF = "pymupdf"
METHOD_OCR = "ocr_tesseract"
METHOD_OCR_FAILED = "ocr_failed"
METHOD_UNREADABLE = "unreadable"
METHOD_MISSING = "file_missing"

# Review reasons.
REVIEW_QUOTE_NUMBER_CONFLICT = "quote_number_conflict"
REVIEW_FILENAME_NUMBER_MISMATCH = "filename_number_mismatch"
REVIEW_NO_CLIENT_ANCHOR = "no_client_anchor"
REVIEW_NO_TEXT = "no_usable_text"
REVIEW_CLIENT_CONFLICT = "client_conflict"
REVIEW_PROPOSAL_DISAGREES = "filename_proposal_disagrees_with_documents"
REVIEW_SEVERAL_QUOTES = "several_quotations_in_one_message"
REVIEW_NOT_STAGED = "not_staged"

MIN_USABLE_CHARS = 40  # non-whitespace characters below which a text layer is "no text"

# --- text extraction -----------------------------------------------------------------


@dataclass(frozen=True)
class Extraction:
    method: str
    text: str
    detail: str | None = None

    @property
    def usable(self) -> bool:
        return usable_text(self.text)


def usable_text(text: str) -> bool:
    return len(re.sub(r"\s", "", text or "")) >= MIN_USABLE_CHARS


def pymupdf_text(path: Path) -> str | None:
    """The PDF's own text layer, read in-process with PyMuPDF. None when it cannot be read."""
    try:
        import fitz  # PyMuPDF, via origenlab-email-pipeline
    except ImportError:
        return None
    try:
        with fitz.open(path) as doc:
            return "\n".join(page.get_text("text", sort=True) for page in doc)
    except Exception:  # noqa: BLE001 - any parser failure means "no text layer"
        return None


TextLayer = Callable[[Path], "str | None"]
OcrRunner = Callable[[Path], str]
DEFAULT_TEXT_LAYERS: tuple[tuple[str, TextLayer], ...] = ((METHOD_PYMUPDF, pymupdf_text),)


def extract_text(
    path: Path,
    *,
    text_layers: Sequence[tuple[str, TextLayer]] = DEFAULT_TEXT_LAYERS,
    ocr: OcrRunner | None = None,
) -> Extraction:
    """Text layer first (the first reader that answers); OCR only when there is no usable
    text. Never raises. External tools (pdftotext, tesseract) are passed in by the caller —
    this module starts no process."""
    if not path.is_file():
        return Extraction(METHOD_MISSING, "", "stored file not found")
    text: str | None = None
    method = METHOD_UNREADABLE
    for name, reader in text_layers:
        text = reader(path)
        if text is not None:
            method = name
            break
    if text is not None and usable_text(text):
        return Extraction(method, text)
    if ocr is None:
        return Extraction(METHOD_UNREADABLE, text or "", "no usable text layer; OCR not available")
    try:
        ocr_text = ocr(path)
    except Exception as exc:  # noqa: BLE001 - recorded, never fatal
        return Extraction(METHOD_OCR_FAILED, text or "", f"OCR failed: {exc}")
    if not usable_text(ocr_text):
        return Extraction(METHOD_OCR_FAILED, ocr_text or "", "OCR produced no usable text")
    return Extraction(METHOD_OCR, ocr_text)


# --- field extraction ------------------------------------------------------------------

_MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
    "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}
_DATE_RE = re.compile(
    r"(?i)\b(\d{1,2})\s+de\s+(" + "|".join(_MONTHS) + r")\s+(?:de|del)\s+(\d{4})\b"
)
# "COTIZACIÓN N° 1005-26", "COTIZACIÓN N°011728A-25", "COTIZACION N°1338/2026"
_QUOTE_NO_RE = re.compile(
    r"(?i)\bCOTIZACI[OÓ]N\s*(?:N\s*[°ºo]|N[°º]?\.|Nro\.?|No\.|#)\s*[:.]?\s*"
    r"(?P<num>\d{1,7}[A-Z]?(?:\s*[-/]\s*\d{2,4})?)\b"
)
_ADDRESSEE_RE = re.compile(
    r"^\s*(?P<label>At\.|At:|Atn\.?:?|Atención\s*:|Señor(?:es|a)?\s*:|Sres?\.\s*:?|Sra\.\s*:?"
    r"|Cliente\s*:|Razón\s+social\s*:)\s*(?P<value>\S.*)$",
    re.IGNORECASE,
)
_RUT_RE = re.compile(r"(?i)\bR\.?\s?U\.?\s?T\.?\s*[:.]?\s*(?P<rut>\d{1,2}\.?\d{3}\.?\d{3}\s*-\s*[\dkK])\b")
_ADDRESS_RE = re.compile(r"(?i)^\s*(?:Dirección|Direccion|Domicilio)\s*:\s*(?P<value>\S.*)$")
_MODEL_RE = re.compile(r"(?i)\bmodelo\s+(?P<value>[^,\n]{2,80})")
_FILENAME_CN_RE = re.compile(r"(?i)\bCN\s*0*(?P<serial>\d{1,7})")
_COLUMN_GAP_RE = re.compile(r"\s{3,}")
_PERSON_ORG_SPLIT_RE = re.compile(r"\s+[-–—]\s+")
_LEGAL_SUFFIXES = (
    "sociedad anonima", "sociedad por acciones", "limitada", "ltda", "spa", "s p a", "sa", "s a",
    "eirl", "e i r l", "y cia", "cia",
)
_ORG_WORDS_RE = re.compile(
    r"(?i)\b(universidad|pontificia|laboratorio|laboratorios|instituto|hospital|centro|cl[ií]nica"
    r"|fundaci[oó]n|servicio|ministerio|facultad|departamento|escuela|colegio|empresa|sociedad"
    r"|spa|s\.?a\.?|ltda\.?|limitada|e\.?i\.?r\.?l\.?)\b"
)
_TABLE_HEAD_RE = re.compile(r"(?i)\b(detalle|descripci[oó]n)\b")
_FOOTER_RE = re.compile(r"(?i)^\s*(condiciones comerciales|total neto|nota\b|plazo de entrega)")


@dataclass(frozen=True)
class Found:
    """One extracted value and the verbatim line it was read from."""

    value: str
    line: str
    line_no: int

    def as_dict(self) -> dict[str, Any]:
        return {"value": self.value, "line": self.line, "line_no": self.line_no}


@dataclass(frozen=True)
class QuoteNumber:
    raw: str
    serial: int
    letter: str
    year: int | None
    evidence: Found

    @property
    def key(self) -> str:
        return f"{self.serial}{self.letter}" + (f"-{self.year % 100:02d}" if self.year is not None else "")


@dataclass(frozen=True)
class ClientAnchor:
    """The explicit client block of one document: the addressee line and what follows it."""

    addressee: Found
    contact: str | None
    organization: str | None
    rut: Found | None
    address: Found | None
    extra_lines: tuple[Found, ...]

    @property
    def organization_key(self) -> str | None:
        return normalize_client_name(self.organization) if self.organization else None

    @property
    def addressee_key(self) -> str:
        return normalize_client_name(self.addressee.value)

    @property
    def rut_key(self) -> str | None:
        return normalize_rut(self.rut.value) if self.rut else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "addressee": self.addressee.as_dict(),
            "contact": self.contact,
            "organization": self.organization,
            "organization_key": self.organization_key,
            "addressee_key": self.addressee_key,
            "rut": self.rut.as_dict() if self.rut else None,
            "rut_key": self.rut_key,
            "address": self.address.as_dict() if self.address else None,
            "extra_lines": [x.as_dict() for x in self.extra_lines],
        }


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def normalize_client_name(name: str) -> str:
    """Comparison key only: accents, case, punctuation, whitespace and a trailing legal suffix."""
    s = strip_accents(name or "").lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    changed = True
    while changed and s:
        changed = False
        for suffix in _LEGAL_SUFFIXES:
            if s == suffix:
                break
            if s.endswith(" " + suffix):
                s = s[: -len(suffix) - 1].strip()
                changed = True
                break
    return s


def normalize_rut(raw: str) -> str:
    s = re.sub(r"[^0-9kK]", "", raw or "").upper()
    return f"{s[:-1].lstrip('0')}-{s[-1]}" if len(s) >= 2 else s


def _cell(line: str) -> str:
    """The first column of a layout line: text up to the first wide gap."""
    return _COLUMN_GAP_RE.split(line.strip(), maxsplit=1)[0].strip()


def _parse_quote_number(raw: str, found: Found) -> QuoteNumber:
    m = re.match(r"(?P<serial>\d+)(?P<letter>[A-Z]?)(?:\s*[-/]\s*(?P<year>\d{2,4}))?$", raw.upper())
    assert m is not None
    year = int(m.group("year")) if m.group("year") else None
    if year is not None and year < 100:
        year += 2000
    return QuoteNumber(raw=raw, serial=int(m.group("serial")), letter=m.group("letter"), year=year, evidence=found)


def _split_addressee(value: str) -> tuple[str | None, str | None]:
    """(contact, organization) as the line itself separates them; never guessed."""
    parts = [p.strip() for p in _PERSON_ORG_SPLIT_RE.split(value) if p.strip()]
    if len(parts) >= 2:
        return parts[0], " - ".join(parts[1:])
    if "@" in value:
        return value, None  # an address is a contact point, not a company
    if _ORG_WORDS_RE.search(value):
        return None, value
    return None, None  # a bare name: kept as the addressee, not assigned to either


@dataclass(frozen=True)
class DocumentFields:
    quote_numbers: tuple[QuoteNumber, ...]
    dates: tuple[Found, ...]
    clients: tuple[ClientAnchor, ...]
    products: tuple[Found, ...]
    mentions_quotation: bool


def extract_fields(text: str) -> DocumentFields:
    lines = (text or "").splitlines()
    numbers: list[QuoteNumber] = []
    seen_numbers: set[str] = set()
    dates: list[Found] = []
    clients: list[ClientAnchor] = []
    products: list[Found] = []

    for i, line in enumerate(lines):
        for m in _QUOTE_NO_RE.finditer(line):
            raw = re.sub(r"\s+", "", m.group("num"))
            qn = _parse_quote_number(raw, Found(raw, line.strip(), i + 1))
            if qn.key not in seen_numbers:
                seen_numbers.add(qn.key)
                numbers.append(qn)
        for m in _DATE_RE.finditer(line):
            try:
                iso = date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1))).isoformat()
            except ValueError:
                continue
            dates.append(Found(iso, line.strip(), i + 1))
        for m in _MODEL_RE.finditer(line):
            products.append(Found(_cell(m.group("value")), line.strip(), i + 1))

        am = _ADDRESSEE_RE.match(line)
        if not am:
            continue
        value = _cell(am.group("value"))
        if not value:
            continue
        addressee = Found(value, line.strip(), i + 1)
        rut = address = None
        extra: list[Found] = []
        # The client block: the lines directly under the addressee, up to a blank line.
        for j in range(i + 1, min(i + 5, len(lines))):
            nxt = lines[j]
            if not nxt.strip() or _ADDRESSEE_RE.match(nxt) or _TABLE_HEAD_RE.search(_cell(nxt)):
                break
            cell = _cell(nxt)
            rm = _RUT_RE.search(cell)
            dm = _ADDRESS_RE.match(cell)
            if rm and rut is None:
                rut = Found(rm.group("rut"), nxt.strip(), j + 1)
            elif dm and address is None:
                address = Found(_cell(dm.group("value")), nxt.strip(), j + 1)
            else:
                extra.append(Found(cell, nxt.strip(), j + 1))
        contact, org = _split_addressee(value)
        clients.append(ClientAnchor(addressee, contact, org, rut, address, tuple(extra)))

    # First described item: the first text line under the item table header.
    if not products:
        in_table = False
        for i, line in enumerate(lines):
            if not in_table:
                in_table = bool(_TABLE_HEAD_RE.search(line))
                continue
            if _FOOTER_RE.match(line):
                break
            cell = re.sub(r"^\s*(\d+\.?\s+)?", "", line).strip()
            cell = _cell(cell)
            if len(re.findall(r"[A-Za-zÁÉÍÓÚáéíóúñÑ]", cell)) >= 8:
                products.append(Found(cell, line.strip(), i + 1))
                break

    return DocumentFields(
        quote_numbers=tuple(numbers),
        dates=tuple(dates[:3]),
        clients=tuple(clients),
        products=tuple(products[:5]),
        mentions_quotation=bool(re.search(r"(?i)cotizaci[oó]n", text or "")),
    )


# --- documents -----------------------------------------------------------------------


@dataclass(frozen=True)
class DocumentIdentity:
    sha256: str
    filenames: tuple[str, ...]
    email_ids: tuple[int, ...]
    stored_path: str | None
    extraction_method: str
    extraction_detail: str | None
    text_chars: int
    document_class: str
    quote_number: QuoteNumber | None
    quote_numbers_found: tuple[QuoteNumber, ...]
    filename_serials: tuple[int, ...]
    document_date: Found | None
    clients: tuple[ClientAnchor, ...]
    products: tuple[Found, ...]
    review_reasons: tuple[str, ...]
    classification_evidence: tuple[str, ...]

    @property
    def client(self) -> ClientAnchor | None:
        """The anchor to compare on: the one carrying a RUT, else an organization, else the first."""
        if not self.clients or REVIEW_CLIENT_CONFLICT in self.review_reasons:
            return None
        return sorted(self.clients, key=lambda c: (c.rut is None, c.organization is None))[0]

    def as_dict(self) -> dict[str, Any]:
        def qn(q: QuoteNumber) -> dict[str, Any]:
            return {"raw": q.raw, "key": q.key, "serial": q.serial, "letter": q.letter,
                    "year": q.year, "evidence": q.evidence.as_dict()}

        return {
            "sha256": self.sha256,
            "filenames": list(self.filenames),
            "email_ids": list(self.email_ids),
            "stored_path": self.stored_path,
            "extraction_method": self.extraction_method,
            "extraction_detail": self.extraction_detail,
            "text_chars": self.text_chars,
            "document_class": self.document_class,
            "quote_number": qn(self.quote_number) if self.quote_number else None,
            "quote_numbers_found": [qn(q) for q in self.quote_numbers_found],
            "filename_serials": list(self.filename_serials),
            "document_date": self.document_date.as_dict() if self.document_date else None,
            "clients": [c.as_dict() for c in self.clients],
            "products": [p.as_dict() for p in self.products],
            "review_reasons": list(self.review_reasons),
            "classification_evidence": list(self.classification_evidence),
        }


def _found_from(d: dict[str, Any] | None) -> Found | None:
    return Found(d["value"], d["line"], int(d["line_no"])) if d else None


def _quote_number_from(d: dict[str, Any]) -> QuoteNumber:
    found = _found_from(d["evidence"])
    assert found is not None
    return QuoteNumber(raw=d["raw"], serial=int(d["serial"]), letter=d["letter"], year=d["year"], evidence=found)


def document_from_dict(d: dict[str, Any]) -> DocumentIdentity:
    """Rebuild one document from a written report (`DocumentIdentity.as_dict`), unchanged.
    The normalized keys are recomputed from the verbatim text, never read back."""
    clients = []
    for c in d["clients"]:
        addressee = _found_from(c["addressee"])
        assert addressee is not None
        clients.append(ClientAnchor(
            addressee=addressee, contact=c["contact"], organization=c["organization"],
            rut=_found_from(c["rut"]), address=_found_from(c["address"]),
            extra_lines=tuple(_found_from(x) for x in c["extra_lines"]),  # type: ignore[misc]
        ))
    return DocumentIdentity(
        sha256=d["sha256"],
        filenames=tuple(d["filenames"]),
        email_ids=tuple(int(x) for x in d["email_ids"]),
        stored_path=d["stored_path"],
        extraction_method=d["extraction_method"],
        extraction_detail=d["extraction_detail"],
        text_chars=int(d["text_chars"]),
        document_class=d["document_class"],
        quote_number=_quote_number_from(d["quote_number"]) if d["quote_number"] else None,
        quote_numbers_found=tuple(_quote_number_from(q) for q in d["quote_numbers_found"]),
        filename_serials=tuple(int(x) for x in d["filename_serials"]),
        document_date=_found_from(d["document_date"]),
        clients=tuple(clients),
        products=tuple(_found_from(p) for p in d["products"]),  # type: ignore[misc]
        review_reasons=tuple(d["review_reasons"]),
        classification_evidence=tuple(d["classification_evidence"]),
    )


def _client_keys_conflict(clients: tuple[ClientAnchor, ...]) -> bool:
    if len(clients) < 2:
        return False
    # An organization line and a bare person line complement each other (INCOMPARABLE);
    # two anchors that name different clients, or clash on a RUT, do not.
    return any(compare_clients(a, b)[0] in (DIFFERENT, CONFLICT) for a, b in combinations(clients, 2))


def identify_document(
    *,
    sha256: str,
    filenames: Iterable[str],
    email_ids: Iterable[int],
    stored_path: str | None,
    extraction: Extraction,
) -> DocumentIdentity:
    filenames = tuple(sorted(set(filenames)))
    serials = tuple(sorted({int(m.group("serial")) for f in filenames for m in _FILENAME_CN_RE.finditer(f)}))
    fields = extract_fields(extraction.text if extraction.usable else "")
    reasons: list[str] = []
    evidence: list[str] = []

    if not extraction.usable:
        doc_class = DOC_INSUFFICIENT
        reasons.append(REVIEW_NO_TEXT)
        evidence.append(f"{extraction.method}: {extraction.detail or 'no usable text'}")
    elif fields.quote_numbers or (fields.clients and fields.mentions_quotation):
        if fields.clients:
            doc_class = DOC_QUOTATION
            evidence += [c.addressee.line for c in fields.clients]
        else:
            doc_class = DOC_INSUFFICIENT
            reasons.append(REVIEW_NO_CLIENT_ANCHOR)
            evidence += [q.evidence.line for q in fields.quote_numbers]
            evidence.append("no addressee line (At./Señores/Cliente) in the document")
    else:
        doc_class = DOC_GENERIC
        evidence.append("no quotation number and no addressee line in the document text")

    number: QuoteNumber | None = None
    if len(fields.quote_numbers) > 1:
        reasons.append(REVIEW_QUOTE_NUMBER_CONFLICT)
        evidence += [q.evidence.line for q in fields.quote_numbers]
    elif fields.quote_numbers:
        number = fields.quote_numbers[0]
        if serials and any(s != number.serial for s in serials):
            reasons.append(REVIEW_FILENAME_NUMBER_MISMATCH)
            evidence.append(f"filename serial(s) {', '.join(map(str, serials))} vs text: {number.evidence.line}")
            number = None
    if doc_class == DOC_QUOTATION and _client_keys_conflict(fields.clients):
        reasons.append(REVIEW_CLIENT_CONFLICT)

    return DocumentIdentity(
        sha256=sha256,
        filenames=filenames,
        email_ids=tuple(sorted(set(email_ids))),
        stored_path=stored_path,
        extraction_method=extraction.method,
        extraction_detail=extraction.detail,
        text_chars=len(re.sub(r"\s", "", extraction.text or "")),
        document_class=doc_class,
        quote_number=number,
        quote_numbers_found=fields.quote_numbers,
        filename_serials=serials,
        document_date=fields.dates[0] if fields.dates else None,
        clients=fields.clients,
        products=fields.products,
        review_reasons=tuple(reasons),
        classification_evidence=tuple(evidence),
    )


# --- comparison ----------------------------------------------------------------------

SAME = "same"
DIFFERENT = "different"
CONFLICT = "conflict"
INCOMPARABLE = "incomparable"


def compare_clients(a: ClientAnchor, b: ClientAnchor) -> tuple[str, str]:
    """Compare two explicit anchors. Returns (verdict, the anchor that decided it)."""
    if a.rut_key and b.rut_key:
        if a.rut_key == b.rut_key:
            return SAME, "rut"
        # Two different RUTs under the same printed organization cannot both be right.
        if a.organization_key and a.organization_key == b.organization_key:
            return CONFLICT, "rut"
        return DIFFERENT, "rut"
    if a.organization_key and b.organization_key:
        return (SAME if a.organization_key == b.organization_key else DIFFERENT), "organization"
    if not a.organization_key and not b.organization_key:
        return (SAME if a.addressee_key == b.addressee_key else DIFFERENT), "addressee"
    # One names an organization, the other only a person: nothing explicit to compare.
    return INCOMPARABLE, "none"


_PAIR_PRIORITY = (
    CONFLICTING_CLIENT_EVIDENCE,
    SAME_CLIENT_SAME_QUOTE,
    SAME_CLIENT_DIFFERENT_QUOTE,
    INSUFFICIENT_EVIDENCE,
    DIFFERENT_EXPLICIT_CLIENT,
)


@dataclass(frozen=True)
class DocumentPair:
    a: str
    b: str
    classification: str
    anchor: str
    needs_review: bool
    evidence: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"sha256_a": self.a, "sha256_b": self.b, "classification": self.classification,
                "anchor": self.anchor, "needs_human_review": self.needs_review,
                "evidence": list(self.evidence)}


def classify_document_pair(da: DocumentIdentity, db: DocumentIdentity) -> DocumentPair:
    """Two quotation documents. Identical bytes win; then client, then quote number."""
    if da.sha256 == db.sha256:
        return DocumentPair(da.sha256, db.sha256, EXACT_DUPLICATE, "sha256", False, (f"sha256 {da.sha256}",))
    lines = tuple(c.addressee.line for c in da.clients) + tuple(c.addressee.line for c in db.clients)
    if REVIEW_CLIENT_CONFLICT in da.review_reasons or REVIEW_CLIENT_CONFLICT in db.review_reasons:
        return DocumentPair(da.sha256, db.sha256, CONFLICTING_CLIENT_EVIDENCE, "addressee", True, lines)
    ca, cb = da.client, db.client
    assert ca is not None and cb is not None
    verdict, anchor = compare_clients(ca, cb)
    ev = list(lines)
    if anchor == "rut":
        ev += [c.rut.line for c in (ca, cb) if c.rut]
    qa, qb = da.quote_number, db.quote_number
    if qa and qb:
        ev += [qa.evidence.line, qb.evidence.line]
    if verdict == CONFLICT:
        return DocumentPair(da.sha256, db.sha256, CONFLICTING_CLIENT_EVIDENCE, anchor, True, tuple(ev))
    if verdict == INCOMPARABLE:
        return DocumentPair(da.sha256, db.sha256, INSUFFICIENT_EVIDENCE, anchor, True, tuple(ev))
    if verdict == DIFFERENT:
        if qa and qb and qa.key == qb.key:
            # One quotation number printed for two different clients.
            return DocumentPair(da.sha256, db.sha256, CONFLICTING_CLIENT_EVIDENCE, anchor, True, tuple(ev))
        return DocumentPair(da.sha256, db.sha256, DIFFERENT_EXPLICIT_CLIENT, anchor, False, tuple(ev))
    # Same explicit client.
    if not (qa and qb):
        ev += [q.evidence.line for d in (da, db) for q in d.quote_numbers_found]
        return DocumentPair(da.sha256, db.sha256, INSUFFICIENT_EVIDENCE, anchor, True, tuple(ev))
    if qa.key == qb.key:
        # Same client and number, different bytes: a resend or revision — the same quote.
        return DocumentPair(da.sha256, db.sha256, SAME_CLIENT_SAME_QUOTE, anchor, False, tuple(ev))
    return DocumentPair(da.sha256, db.sha256, SAME_CLIENT_DIFFERENT_QUOTE, anchor, False, tuple(ev))


def _aggregate(pairs: list[DocumentPair]) -> str | None:
    labels = {p.classification for p in pairs}
    for label in _PAIR_PRIORITY:
        if label in labels:
            return label
    return EXACT_DUPLICATE if labels == {EXACT_DUPLICATE} else None


# --- candidates ----------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateRelation:
    other_email_id: int
    classification: str
    needs_review: bool
    links: tuple[str, ...]
    document_pairs: tuple[DocumentPair, ...]
    shared_sha256: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "other_email_id": self.other_email_id,
            "classification": self.classification,
            "needs_human_review": self.needs_review,
            "links": list(self.links),
            "shared_sha256": list(self.shared_sha256),
            "document_pairs": [p.as_dict() for p in self.document_pairs],
        }


@dataclass
class CandidateIdentity:
    email_id: int
    queue: str
    gmail_thread_id: str | None
    proposed_quote_numbers: tuple[str, ...]
    documents: tuple[DocumentIdentity, ...]
    internal_classification: str | None = None
    internal_pairs: tuple[DocumentPair, ...] = ()
    relations: list[CandidateRelation] = field(default_factory=list)
    classification: str = INSUFFICIENT_EVIDENCE
    review_reasons: list[str] = field(default_factory=list)
    classification_evidence: list[str] = field(default_factory=list)

    @property
    def quotation_documents(self) -> tuple[DocumentIdentity, ...]:
        return tuple(d for d in self.documents if d.document_class == DOC_QUOTATION)

    @property
    def document_quote_numbers(self) -> tuple[str, ...]:
        return tuple(sorted({d.quote_number.key for d in self.quotation_documents if d.quote_number}))

    @property
    def needs_review(self) -> bool:
        return bool(self.review_reasons)

    def as_dict(self) -> dict[str, Any]:
        return {
            "email_id": self.email_id,
            "queue": self.queue,
            "gmail_thread_id": self.gmail_thread_id,
            "classification": self.classification,
            "needs_human_review": self.needs_review,
            "review_reasons": list(self.review_reasons),
            "classification_evidence": list(self.classification_evidence),
            "proposed_quote_numbers_from_filenames": list(self.proposed_quote_numbers),
            "document_quote_numbers": list(self.document_quote_numbers),
            "documents": [
                {"sha256": d.sha256, "document_class": d.document_class,
                 "filenames": list(d.filenames)}
                for d in self.documents
            ],
            "internal_classification": self.internal_classification,
            "internal_pairs": [p.as_dict() for p in self.internal_pairs],
            "relations": [r.as_dict() for r in self.relations],
        }


def _proposal_serials(proposed: Iterable[str]) -> set[int]:
    return {int(m.group("serial")) for p in proposed for m in _FILENAME_CN_RE.finditer(p)}


def _relation(a: CandidateIdentity, b: CandidateIdentity, links: tuple[str, ...]) -> CandidateRelation:
    shas_a = {d.sha256 for d in a.documents}
    shas_b = {d.sha256 for d in b.documents}
    shared = tuple(sorted(shas_a & shas_b))
    qa = {d.sha256: d for d in a.quotation_documents}
    qb = {d.sha256: d for d in b.quotation_documents}
    if qa and qb and set(qa) == set(qb):
        pairs = (DocumentPair(s, s, EXACT_DUPLICATE, "sha256", False, (f"sha256 {s}",)) for s in sorted(qa))
        return CandidateRelation(b.email_id, EXACT_DUPLICATE, False, links, tuple(pairs), shared)
    if not qa or not qb:
        # No quotation on one side: nothing explicit to compare. Say whether the only
        # thing joining them is a brochure or generic file.
        by_sha = {d.sha256: d for d in a.documents + b.documents}
        only_generic = bool(shared) and all(by_sha[s].document_class == DOC_GENERIC for s in shared)
        label = BROCHURE_OR_GENERIC if only_generic else INSUFFICIENT_EVIDENCE
        return CandidateRelation(b.email_id, label, False, links, (), shared)
    pairs = [classify_document_pair(x, y) for x in qa.values() for y in qb.values()]
    label = _aggregate(pairs) or INSUFFICIENT_EVIDENCE
    return CandidateRelation(
        b.email_id, label, any(p.needs_review for p in pairs), links, tuple(pairs), shared
    )


_CANDIDATE_RELATION_PRIORITY = (
    CONFLICTING_CLIENT_EVIDENCE,
    EXACT_DUPLICATE,
    SAME_CLIENT_SAME_QUOTE,
    SAME_CLIENT_DIFFERENT_QUOTE,
    DIFFERENT_EXPLICIT_CLIENT,
)


def classify_candidates(staging: Staging, documents: dict[str, DocumentIdentity]) -> list[CandidateIdentity]:
    """One identity per staged Gmail candidate, related only through exact links."""
    cands: dict[int, CandidateIdentity] = {}
    for item in staging.items:
        docs = tuple(documents[d.sha256] for d in item.documents if d.sha256 in documents)
        cands[item.email_id] = CandidateIdentity(
            email_id=item.email_id,
            queue=item.queue,
            gmail_thread_id=item.gmail_thread_id,
            proposed_quote_numbers=item.proposed_quote_numbers,
            documents=docs,
        )

    # Which candidates to compare: only those joined by an exact link — the same document
    # bytes, the same thread id, the same printed client key or RUT, or the same printed
    # quote number. Never by domain, subject, filename or resemblance.
    buckets: dict[tuple[str, str], set[int]] = {}
    for c in cands.values():
        keys: set[tuple[str, str]] = {
            ("shared_generic_document" if d.document_class == DOC_GENERIC else "shared_document", d.sha256)
            for d in c.documents
        }
        if c.gmail_thread_id:
            keys.add(("thread", c.gmail_thread_id))
        for d in c.quotation_documents:
            for cl in d.clients:
                keys.add(("client", cl.organization_key or cl.addressee_key))
                if cl.rut_key:
                    keys.add(("rut", cl.rut_key))
            for q in d.quote_numbers_found:
                keys.add(("quote_number", q.key))
        for k in keys:
            buckets.setdefault(k, set()).add(c.email_id)
    links: dict[tuple[int, int], set[str]] = {}
    for (kind, _), ids in buckets.items():
        for x, y in combinations(sorted(ids), 2):
            links.setdefault((x, y), set()).add(kind)

    for (x, y), kinds in sorted(links.items()):
        lk = tuple(sorted(kinds))
        cands[x].relations.append(_relation(cands[x], cands[y], lk))
        cands[y].relations.append(_relation(cands[y], cands[x], lk))

    for c in cands.values():
        _finish_candidate(c)
    return sorted(cands.values(), key=lambda c: c.email_id)


def _finish_candidate(c: CandidateIdentity) -> None:
    quotes = c.quotation_documents
    reasons: list[str] = []
    evidence: list[str] = []
    for d in c.documents:
        for r in d.review_reasons:
            if d.document_class != DOC_GENERIC and r not in reasons:
                reasons.append(r)

    if len(quotes) >= 2:
        c.internal_pairs = tuple(classify_document_pair(x, y) for x, y in combinations(quotes, 2))
        c.internal_classification = _aggregate(list(c.internal_pairs))
        reasons.append(REVIEW_SEVERAL_QUOTES)

    doc_numbers = {d.quote_number.serial for d in quotes if d.quote_number}
    proposal = _proposal_serials(c.proposed_quote_numbers)
    if quotes and doc_numbers != proposal:
        reasons.append(REVIEW_PROPOSAL_DISAGREES)
        evidence.append(
            f"filename proposal {' '.join(c.proposed_quote_numbers) or '—'} vs document numbers "
            f"{' '.join(c.document_quote_numbers) or '—'}"
        )

    if not quotes:
        insufficient = [d for d in c.documents if d.document_class == DOC_INSUFFICIENT]
        if insufficient:
            c.classification = INSUFFICIENT_EVIDENCE
            evidence += [e for d in insufficient for e in d.classification_evidence]
        elif c.documents:
            c.classification = BROCHURE_OR_GENERIC
            evidence.append("every document is a brochure or generic document: "
                            + ", ".join(f for d in c.documents for f in d.filenames))
        else:
            c.classification = INSUFFICIENT_EVIDENCE
            evidence.append("no staged document")
    elif c.internal_classification in (CONFLICTING_CLIENT_EVIDENCE, DIFFERENT_EXPLICIT_CLIENT,
                                       SAME_CLIENT_DIFFERENT_QUOTE, SAME_CLIENT_SAME_QUOTE,
                                       INSUFFICIENT_EVIDENCE):
        c.classification = c.internal_classification
        evidence += [e for p in c.internal_pairs for e in p.evidence]
    else:
        rel_labels = {r.classification for r in c.relations}
        chosen = next((lb for lb in _CANDIDATE_RELATION_PRIORITY if lb in rel_labels), None)
        if chosen:
            c.classification = chosen
            for r in c.relations:
                if r.classification == chosen:
                    evidence.append(f"with {r.other_email_id} (via {', '.join(r.links)}):")
                    evidence += [e for p in r.document_pairs for e in p.evidence]
        else:
            c.classification = UNIQUE_EXPLICIT_QUOTE
            evidence += [e for d in quotes for e in d.classification_evidence]
    if c.classification == CONFLICTING_CLIENT_EVIDENCE and REVIEW_CLIENT_CONFLICT not in reasons:
        reasons.append(REVIEW_CLIENT_CONFLICT)
    if any(r.needs_review and r.classification == CONFLICTING_CLIENT_EVIDENCE for r in c.relations):
        if REVIEW_CLIENT_CONFLICT not in reasons:
            reasons.append(REVIEW_CLIENT_CONFLICT)
    c.review_reasons = reasons
    c.classification_evidence = list(dict.fromkeys(evidence))


# --- the audit -----------------------------------------------------------------------


def is_pdf(path: Path | None, filename: str) -> bool:
    if path is not None and path.is_file():
        with path.open("rb") as f:
            return f.read(5) == b"%PDF-"
    return filename.lower().endswith(".pdf")


def audit_documents(
    staging: Staging,
    documents_root: Path,
    *,
    ocr: OcrRunner | None = None,
    extractor: Callable[[Path], Extraction] | None = None,
) -> dict[str, DocumentIdentity]:
    """Extract and identify every distinct staged document (by exact SHA-256), once."""
    seen: dict[str, dict[str, Any]] = {}
    for item in staging.items:
        for d in item.documents:
            if not d.bytes_hash_verified:
                continue  # only verified bytes are audited
            s = seen.setdefault(d.sha256, {"filenames": set(), "emails": set(), "stored_path": d.stored_path})
            s["filenames"].add(d.filename)
            s["emails"].add(item.email_id)
    out: dict[str, DocumentIdentity] = {}
    for sha, s in sorted(seen.items()):
        path = (documents_root / s["stored_path"]) if s["stored_path"] else None
        fname = sorted(s["filenames"])[0]
        if not is_pdf(path, fname):
            extraction = Extraction(DOC_NOT_PDF, "", "not a PDF; outside this audit")
            ident = identify_document(sha256=sha, filenames=s["filenames"], email_ids=s["emails"],
                                      stored_path=s["stored_path"], extraction=extraction)
            out[sha] = _replace_class(ident, DOC_NOT_PDF)
            continue
        if path is None:
            extraction = Extraction(METHOD_MISSING, "", "no stored path")
        elif extractor is not None:
            extraction = extractor(path)
        else:
            extraction = extract_text(path, ocr=ocr)
        out[sha] = identify_document(sha256=sha, filenames=s["filenames"], email_ids=s["emails"],
                                     stored_path=s["stored_path"], extraction=extraction)
    return out


def _replace_class(d: DocumentIdentity, cls: str) -> DocumentIdentity:
    return replace(d, document_class=cls, review_reasons=(), classification_evidence=("not a PDF",))


def build_report(
    staging: Staging,
    documents: dict[str, DocumentIdentity],
    *,
    generated_at: str,
    focus_email_ids: Iterable[int] = (),
) -> dict[str, Any]:
    candidates = classify_candidates(staging, documents)
    by_class: dict[str, int] = {c: 0 for c in CLASSIFICATIONS}
    for c in candidates:
        by_class[c.classification] += 1
    doc_classes: dict[str, int] = {}
    methods: dict[str, int] = {}
    for d in documents.values():
        doc_classes[d.document_class] = doc_classes.get(d.document_class, 0) + 1
        methods[d.extraction_method] = methods.get(d.extraction_method, 0) + 1
    staged = {c.email_id for c in candidates}
    return {
        "generated_at": generated_at,
        "scope": "staged Gmail candidates Q1–Q3; verified document bytes only; read-only",
        "writes": "this report only; no database, no Gmail, no Drive, no ledger, no staging file",
        "summary": {
            "candidates": len(candidates),
            "candidates_needing_review": sum(1 for c in candidates if c.needs_review),
            "by_classification": by_class,
            "distinct_documents": len(documents),
            "documents_by_class": dict(sorted(doc_classes.items())),
            "documents_by_extraction": dict(sorted(methods.items())),
        },
        "focus": [
            ({"email_id": e, "classification": None, "review_reasons": [REVIEW_NOT_STAGED]}
             if e not in staged else
             next(c for c in candidates if c.email_id == e).as_dict())
            for e in focus_email_ids
        ],
        "candidates": [c.as_dict() for c in candidates],
        "documents": [d.as_dict() for d in sorted(documents.values(), key=lambda d: d.sha256)],
    }


CSV_FIELDS = (
    "email_id", "queue", "candidate_classification", "needs_human_review", "review_reasons",
    "proposed_quote_numbers", "document_quote_numbers", "related", "sha256", "filenames",
    "document_class", "extraction_method", "quote_number_text", "quote_number_line",
    "document_date", "date_line", "addressee_line", "contact", "organization", "rut",
    "address", "products",
)


def report_csv_rows(report: dict[str, Any]) -> list[dict[str, str]]:
    """One row per (candidate, document), with the verbatim evidence lines."""
    docs = {d["sha256"]: d for d in report["documents"]}
    rows: list[dict[str, str]] = []
    for c in report["candidates"]:
        related = "; ".join(f"{r['other_email_id']}:{r['classification']}" for r in c["relations"])
        for cd in c["documents"] or [{"sha256": ""}]:
            d = docs.get(cd["sha256"], {})
            qn = d.get("quote_number") or (d.get("quote_numbers_found") or [None])[0]
            cl = (d.get("clients") or [None])[0]
            dt = d.get("document_date")
            rows.append({
                "email_id": str(c["email_id"]),
                "queue": c["queue"],
                "candidate_classification": c["classification"],
                "needs_human_review": str(c["needs_human_review"]).lower(),
                "review_reasons": " ".join(c["review_reasons"]),
                "proposed_quote_numbers": " ".join(c["proposed_quote_numbers_from_filenames"]),
                "document_quote_numbers": " ".join(c["document_quote_numbers"]),
                "related": related,
                "sha256": d.get("sha256", ""),
                "filenames": " | ".join(d.get("filenames", [])),
                "document_class": d.get("document_class", ""),
                "extraction_method": d.get("extraction_method", ""),
                "quote_number_text": " ".join(q["raw"] for q in d.get("quote_numbers_found", [])),
                "quote_number_line": qn["evidence"]["line"] if qn else "",
                "document_date": dt["value"] if dt else "",
                "date_line": dt["line"] if dt else "",
                "addressee_line": " || ".join(x["addressee"]["line"] for x in d.get("clients", [])),
                "contact": (cl or {}).get("contact") or "",
                "organization": (cl or {}).get("organization") or "",
                "rut": ((cl or {}).get("rut") or {}).get("value", ""),
                "address": ((cl or {}).get("address") or {}).get("value", ""),
                "products": " | ".join(p["value"] for p in d.get("products", [])),
            })
    return rows
