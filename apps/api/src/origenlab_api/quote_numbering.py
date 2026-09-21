"""OrigenLab quote-numbering forward rules (D2b).

This module is the single home for *how* an OrigenLab quote identifier is
rendered and validated. It is pure: no I/O, no settings, no database. The
allocator that decides *which* serial comes next lives in
``origenlab_api.repositories.postgres.customer_quotes`` and stays there --
it is transactional and must not be reimplemented here.

Three things are deliberately kept apart in this module, because they were
decided on different evidence and carry different authority. The same split
is documented for humans in
``docs/business/BUSINESS_RULES_QUOTES_AND_SUPPLIERS.md`` §2.3.

1. **Historical evidence** -- what the real archive actually shows. Weak and
   incomplete: one confirmed Drive filename (``CN011728A``) proving that a
   revision letter suffixes the *document* stem with no separator, plus one
   manual numbering outlier (``01500-26``). Everything else about the
   historical series is unproven; see the register notes in
   ``apps/email-pipeline/src/origenlab_email_pipeline/historical_quote_register/quote_number.py``.

2. **Owner-approved forward rules** -- the D2b decision, recorded 2026-09-21.
   These govern every number OrigenLab allocates from now on. They are not
   retroactive and they do not describe the archive.

3. **Reserved serials** -- serials the OrigenLab series must never issue.
   Exactly one is reserved today: 1500, because the customer-facing
   document ``01500-26`` already exists (produced by manual error) and must
   never be issued again. Encoded as ``RESERVED_SERIALS``, which is a set
   rather than a single value so a second reservation is data, not code.

Forward rules, verbatim from the decision:

* One global sequence; never reset annually.
* The year is display metadata only -- it is rendered into the human
  ``quote_number`` and is never part of the sequence's identity.
* Revision 1 has no suffix. Revisions 2/3/4/... take A/B/C/..., and the
  letter sits immediately after the five-digit serial -- before the ``-YY``
  in the human number (``01235A-26``) and at the end of the document stem
  (``CN01235A``).
* Legacy Labdelivery numbering lives in a separate number space and never
  enters the OrigenLab series.
* The next number is never derived with ``MAX() + 1``; allocation is
  transactional and concurrency-safe.
* A reserved serial is skipped atomically inside the allocating
  transaction, so the series keeps moving (1499 -> 1501) and the reserved
  serial is never returned or persisted.
* Historical duplicate quotation numbers are never imported or "corrected".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "MAX_REVISION_NUMBER",
    "ORIGENLAB_QUOTE_NUMBER_RE",
    "RESERVED_SERIALS",
    "ParsedQuoteNumber",
    "QuoteNumberSpaceError",
    "QuoteRevisionLetterExhaustedError",
    "QuoteSerialReservedError",
    "is_legacy_labdelivery_quote_number",
    "is_reserved_serial",
    "parse_origenlab_quote_number",
    "render_document_number",
    "render_quote_number",
    "require_adoptable_quote_number",
    "require_allocatable_serial",
    "revision_letter",
]


class QuoteRevisionLetterExhaustedError(ValueError):
    """More revisions exist than the approved A..Z letter space allows.

    The D2b decision approved A/B/C/... for revisions 2/3/4/... and said
    nothing about what follows Z. Inventing "AA" (or wrapping) would be the
    system making a business decision, so this fails closed instead. A 27th
    revision of one quotation is not a situation to guess through.
    """


class QuoteSerialReservedError(ValueError):
    """A reserved serial was offered where it must never appear.

    This is *not* how the allocator handles reaching a reserved serial --
    that path skips it atomically and keeps going (see
    ``is_reserved_serial``). This error covers the two places where a
    reserved serial can only arrive by mistake:

    * adoption, where an operator typed ``01500-26`` into "Incorporar al
      CRM" -- adopting it is importing the historical numbering error the
      D2b decision says to leave alone;
    * ``require_allocatable_serial``, the allocator's post-condition, where
      it would mean the skip logic itself is broken.
    """


class QuoteNumberSpaceError(ValueError):
    """A number from the legacy Labdelivery space was offered to the
    OrigenLab series. The two spaces stay separate (D2b)."""


# --------------------------------------------------------------------------
# 3. Reserved serials.
# --------------------------------------------------------------------------
# Serials the OrigenLab series must never issue. This is an explicit, finite
# denylist of individual numbers -- never a range, and never derived from a
# pattern. A range would permanently burn valid future OrigenLab serials on
# the strength of a guess; each entry here has to earn its place with a named
# historical document.
#
# 1500 is reserved because the customer-facing quotation ``01500-26`` already
# exists. It was produced by a manual numbering error -- numbered far ahead of
# the real sequence, which at the time of the D2b decision sat at 1234
# (next = 1235) -- but it is a real document that reached a real customer, so
# the owner's decision is that it is left exactly as it is (not renamed, not
# deleted, not modified, not imported) and that the number is never issued a
# second time.
#
# The collision is concrete, not theoretical: ``commercial.customer_quote``
# has UNIQUE constraints on ``quote_number`` and ``document_number``, and
# ``document_number`` carries no year, so a generated ``CN01500`` would clash
# with the historical document's own stem the moment it were ever adopted.
#
# Reaching a reserved serial does not stop the business: the allocator skips
# it atomically inside its row-locked transaction and issues the next one
# (1499 -> 1501), recording the skip on the quote's append-only event.
RESERVED_SERIALS = frozenset({1500})


# --------------------------------------------------------------------------
# 2. Owner-approved forward rules.
# --------------------------------------------------------------------------
# Revision 1 renders no suffix; revision 2 is the first lettered one. The
# offset is (revision_number - 2) into A..Z, so revision 27 renders "Z" and
# revision 28 has no approved rendering.
_FIRST_LETTERED_REVISION = 2
_REVISION_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
MAX_REVISION_NUMBER = _FIRST_LETTERED_REVISION + len(_REVISION_ALPHABET) - 1

# The human customer-facing number: <padded serial><optional revision
# letter>-<2-digit issue year>, e.g. "01235-26" and "01235A-26". The letter
# sits immediately after the serial and before the year, matching the
# approved customer-facing format and the document stem's own suffixing. The
# year here is display metadata (D2b) -- parsing it back out never tells you
# anything about the sequence, only about when that quotation was issued.
ORIGENLAB_QUOTE_NUMBER_RE = re.compile(
    r"^(?P<serial>\d{1,10})(?P<letter>[A-Z])?-(?P<issue_year_2>\d{2})$"
)

# The legacy Labdelivery space. Shape mirrors the extraction regex in
# origenlab_email_pipeline.historical_quote_register.quote_number (COT-YYYY-NNN
# and its separator variants); it is duplicated rather than imported because
# the two have different jobs -- that one maximizes recall over noisy archive
# text, this one only has to recognize a number an operator typed into the
# adoption form. Widening one does not imply widening the other.
_LEGACY_LABDELIVERY_RE = re.compile(
    r"^\s*COT[-\s]?\d{4}[-\s]?\d{1,5}\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedQuoteNumber:
    """A human quote_number decomposed back into its parts.

    ``issue_year_2`` is the rendered 2-digit year exactly as it appears; it
    is display metadata and is deliberately not expanded to a 4-digit year,
    because the rendering is lossy and guessing the century back would be
    inventing information.
    """

    serial: int
    issue_year_2: int
    revision_letter: str | None


def revision_letter(revision_number: int) -> str | None:
    """The approved suffix letter for ``revision_number``.

    Revision 1 -> ``None`` (no suffix). Revision 2 -> "A", 3 -> "B", and so
    on through revision 27 -> "Z". Beyond that raises
    ``QuoteRevisionLetterExhaustedError`` rather than inventing a rendering.
    """
    if revision_number < 1:
        raise ValueError(f"revision_number must be >= 1, got {revision_number!r}")

    if revision_number < _FIRST_LETTERED_REVISION:
        return None

    if revision_number > MAX_REVISION_NUMBER:
        raise QuoteRevisionLetterExhaustedError(
            "quote_revision_letter_exhausted: revision "
            f"{revision_number} is past the approved A..Z revision space "
            f"(max {MAX_REVISION_NUMBER}); the suffix beyond 'Z' is not a "
            "decided business rule"
        )

    return _REVISION_ALPHABET[revision_number - _FIRST_LETTERED_REVISION]


def _padded_serial(serial: int, pad_width: int) -> str:
    if serial < 1:
        raise ValueError(f"serial must be >= 1, got {serial!r}")
    if not 1 <= pad_width <= 10:
        raise ValueError(f"pad_width must be in 1..10, got {pad_width!r}")
    return str(serial).zfill(pad_width)


def render_quote_number(
    *,
    serial: int,
    pad_width: int,
    issue_year: int,
    revision_number: int = 1,
) -> str:
    """The human customer-facing number, e.g. "01235-26" / "01235A-26".

    The revision letter attaches directly to the serial, *before* the year
    separator -- the approved format is ``01235A-26``, not ``01235-26 A``.
    The letter belongs to the quotation's identity, which the year does not;
    keeping it next to the serial also makes the human number and the
    document stem suffix the same way.

    ``issue_year`` is a full calendar year (the America/Santiago business
    year at allocation time); only its last two digits are rendered. The
    document prefix is never part of this value.
    """
    if not 2000 <= issue_year <= 2999:
        raise ValueError(f"issue_year must be in 2000..2999, got {issue_year!r}")

    stem = _padded_serial(serial, pad_width)
    letter = revision_letter(revision_number)

    if letter is not None:
        stem = f"{stem}{letter}"

    return f"{stem}-{issue_year % 100:02d}"


def render_document_number(
    *,
    document_prefix: str,
    serial: int,
    pad_width: int,
    revision_number: int = 1,
) -> str:
    """The Drive document stem, e.g. "CN01235" / "CN01235A".

    The revision letter attaches with no separator, matching the one piece
    of real historical evidence ("CN011728A"). Unlike the human
    quote_number this carries no year at all, so the letter simply ends the
    stem; in the human number the same letter sits before the "-YY"
    ("01235A-26").
    """
    prefix = document_prefix.strip()
    if not prefix:
        raise ValueError("document_prefix must not be blank")

    stem = f"{prefix}{_padded_serial(serial, pad_width)}"
    letter = revision_letter(revision_number)

    return stem if letter is None else f"{stem}{letter}"


def is_legacy_labdelivery_quote_number(value: str) -> bool:
    """True when ``value`` belongs to the legacy Labdelivery number space.

    Such a number is valid history but is never an OrigenLab series number
    (D2b keeps the two spaces separate).
    """
    return _LEGACY_LABDELIVERY_RE.fullmatch(value) is not None


def parse_origenlab_quote_number(value: str) -> ParsedQuoteNumber | None:
    """Decompose a human quote_number, or ``None`` if it is not one.

    Returning ``None`` is not an error: an adopted quote may legitimately
    carry a number that predates this format. The caller decides what that
    means.
    """
    match = ORIGENLAB_QUOTE_NUMBER_RE.fullmatch(value.strip())

    if match is None:
        return None

    return ParsedQuoteNumber(
        serial=int(match.group("serial")),
        issue_year_2=int(match.group("issue_year_2")),
        revision_letter=match.group("letter"),
    )


def is_reserved_serial(serial: int) -> bool:
    """True when ``serial`` must never be issued by the OrigenLab series.

    The allocator calls this on each serial its row-locked ``UPDATE`` hands
    back; a reserved one is skipped and the next is allocated, all inside
    the same transaction. Membership is an explicit set, so adding a second
    reservation is a one-line data change that every skip test then covers.
    """
    return serial in RESERVED_SERIALS


def require_allocatable_serial(serial: int) -> int:
    """Post-condition: the serial about to be rendered is not reserved.

    The allocator has already skipped past reservations by the time this
    runs, so raising here means the skip logic is broken rather than that
    the business needs a decision. It exists because the cost of a silent
    failure is a reissued customer-facing number, which cannot be taken
    back once the document is sent.
    """
    if is_reserved_serial(serial):
        raise QuoteSerialReservedError(
            f"quote_serial_reserved: serial {serial} is reserved and must "
            "never be allocated; the allocator should have skipped it (see "
            "docs/business/BUSINESS_RULES_QUOTES_AND_SUPPLIERS.md "
            "section 2.3)"
        )

    return serial


def require_adoptable_quote_number(quote_number: str) -> ParsedQuoteNumber | None:
    """Vet an operator-supplied number for "Incorporar al CRM".

    Adoption is the only way a number the series did not allocate reaches
    ``commercial.customer_quote``, so it is where the two D2b boundaries are
    enforced. Returns the parsed number when it is an OrigenLab one, or
    ``None`` when it is some other legitimate historical form -- a quotation
    predating this rendering is still adoptable.

    Two things are refused:

    * a number from the legacy Labdelivery space, because D2b keeps that
      space separate rather than folding it in;
    * a reserved serial (today only 1500), because adopting ``01500-26``
      *is* importing the historical numbering error the decision says to
      leave alone.

    The remaining hazard -- adopting a serial the series has not issued yet
    -- needs the series' own counter and is checked by the repository inside
    the allocation transaction.
    """
    value = quote_number.strip()

    if is_legacy_labdelivery_quote_number(value):
        raise QuoteNumberSpaceError(
            "adopted_quote_number_legacy_space: "
            "legacy Labdelivery numbering is a separate number space and "
            "is never adopted as an OrigenLab quote_number"
        )

    parsed = parse_origenlab_quote_number(value)

    if parsed is not None and is_reserved_serial(parsed.serial):
        raise QuoteSerialReservedError(
            f"adopted_quote_number_reserved_serial: serial {parsed.serial} "
            "is permanently reserved; the historical quotation carrying it "
            "is deliberately left outside the CRM and must not be imported "
            "(see docs/business/BUSINESS_RULES_QUOTES_AND_SUPPLIERS.md "
            "section 2.3)"
        )

    return parsed
