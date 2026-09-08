"""Quote-number extraction and the composite historical_quote_key.

Design §3.2/§3.3: quote number alone is never the key (collisions across
customers/years are possible); the year is already embedded in the extracted
number itself (COT-YYYY-NNN), so the key is (customer_domain, normalized
number) — no separate year math needed, which avoids a subtle bug where a
quote's *send* date and its *embedded* number year disagree near year
boundaries.

Step 1 empirical check (2026-08-28, read-only aggregate-count queries only —
see task-4-report.md for the full battery; no subject/filename text was ever
selected or printed):

- Sent-folder rows: 7,900 total, split across two disjoint eras — legacy
  mbox 2017-2020 (~6,233 rows) and Gmail 2026 (1,667 rows); no rows at all
  for 2021-2025 (archive gap).
- Of 1,082 subject-keyword quote-signal rows (cotiz.../presupuesto/oferta,
  case-insensitive) across the whole archive, the baseline `COT[-\\s]?YYYY
  [-\\s]?NNN` pattern matched **zero** — including when restricted to 2026,
  the one year matching the project owner's own example. Attachment
  filenames for Sent-folder emails matched zero too.
- Widened the separator class experimentally (any 0-3 non-alphanumeric
  characters between "COT" and 2-7 digits, i.e. dash/space/slash/dot/none)
  and still matched **zero** subjects — so this isn't a separator-choice
  problem; a "COT" token immediately adjacent to digits essentially does
  not occur in this archive's Sent subjects/filenames at all.
- Probed plausible alternate prefixes: "N°"/"Nº" near digits (3 rows, all
  2026), "OL-" (0), "PPTO-" (0), "REF-" (0), reversed number-then-year order
  (25 rows, but only 2 within a plausible 2020-2026 year — too sparse/
  ambiguous to be a confirmed convention, high false-positive risk from
  dates/prices). "OC-" had 18 rows but that prefix conventionally denotes a
  purchase order ("Orden de Compra"), a different document type than a
  quote — folding it in would risk mislabeling PO numbers as quote numbers,
  a correctness bug, so it was deliberately excluded rather than added.
- Conclusion: no alternate prefix cleared the noise floor, so `_QUOTE_NUMBER_RE`
  is kept as originally specified (matching the sole confirmed real example,
  "COT-2026-014") rather than widened on unsupported evidence. Real-world
  recall from subject/filename alone is expected to be low in this archive —
  flagged as a concern in task-4-report.md; the `document_text` parameter
  exists so a later extraction stage (parsing the quote PDF itself, where
  the number is far more likely to actually appear) can supply better
  recall without changing this module's contract.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Literal

QuoteNumberConfidence = Literal["high", "medium", "none"]

# Starting hypothesis from the user's own example ("COT-2026-014") — validated
# empirically against real Sent-folder subjects/filenames in Step 1 above.
# Kept unchanged in prefix/separator shape: no alternate prefix or separator
# convention cleared the noise floor in the real archive (see module
# docstring). Two mechanical fixes from Step 2/3 TDD (not from Step 1's
# widening, purely from making the brief's own example tests pass):
#   - sequence-number group is `\d{1,5}` (not `\d{2,5}`): a single-digit
#     sequence number (e.g. "COT 2026 7") must match so it can be zero-padded
#     below — {2,5} rejected it outright.
#   - trailing boundary is `(?!\d)` (not `\b`): a filename like
#     "COT-2026-014_rev1.pdf" has `_` immediately after the digits, and `_`
#     counts as a word character in regex `\b`, so no boundary exists there
#     and the old `\b` silently failed to match a realistic filename.
_QUOTE_NUMBER_RE = re.compile(r"\bCOT[-\s]?(\d{4})[-\s]?(\d{1,5})(?!\d)", re.IGNORECASE)


@dataclass(frozen=True)
class QuoteNumberMatch:
    raw_quote_number: str
    normalized_quote_number: str
    confidence: QuoteNumberConfidence
    matched_in: tuple[str, ...]


def extract_quote_number(
    *, subject: str | None, filename: str | None, document_text: str | None = None,
) -> QuoteNumberMatch | None:
    matched_in: list[str] = []
    raw: str | None = None
    for label, text in (
        ("subject", subject),
        ("filename", filename),
        ("document_text", document_text),
    ):
        if not text:
            continue
        m = _QUOTE_NUMBER_RE.search(text)
        if m:
            matched_in.append(label)
            if raw is None:
                raw = f"COT-{m.group(1)}-{m.group(2).zfill(3)}"

    if raw is None:
        return None

    confidence: QuoteNumberConfidence = "high" if "subject" in matched_in else "medium"
    return QuoteNumberMatch(
        raw_quote_number=raw,
        normalized_quote_number=raw.lower(),
        confidence=confidence,
        matched_in=tuple(matched_in),
    )


def build_historical_quote_key(
    *,
    customer_domain: str,
    quote_number_match: QuoteNumberMatch | None,
    send_email_id: int,
) -> str:
    if quote_number_match is not None:
        raw = f"{customer_domain.strip().lower()}|{quote_number_match.normalized_quote_number}"
    else:
        raw = f"{customer_domain.strip().lower()}|send-{send_email_id}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"hqk-{digest}"
