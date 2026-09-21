"""D2b forward quote-numbering rules (pure; no database).

The owner-approved rules under test, recorded 2026-09-21:
  * one global sequence, seeded at 1235, never reset annually;
  * the year is display metadata only ("01235-26");
  * revision 1 unsuffixed, revisions 2/3/4/... -> A/B/C/...;
  * the legacy Labdelivery space stays separate;
  * 01500-26 is a historical exception and its serial is reserved.
"""

from __future__ import annotations

import pytest

from origenlab_api.quote_numbering import (
    HISTORICAL_OUTLIER_SERIALS,
    MAX_REVISION_NUMBER,
    ParsedQuoteNumber,
    QuoteRevisionLetterExhaustedError,
    QuoteSerialReservedError,
    is_legacy_labdelivery_quote_number,
    parse_origenlab_quote_number,
    render_document_number,
    render_quote_number,
    require_allocatable_serial,
    revision_letter,
)


# ---------------------------------------------------------------------------
# Revision letters
# ---------------------------------------------------------------------------


def test_revision_one_has_no_letter():
    assert revision_letter(1) is None


@pytest.mark.parametrize(
    ("revision_number", "expected"),
    [(2, "A"), (3, "B"), (4, "C"), (26, "Y"), (27, "Z")],
)
def test_revision_letters_follow_the_approved_sequence(revision_number, expected):
    assert revision_letter(revision_number) == expected


def test_revision_letter_space_ends_at_z_and_fails_closed():
    assert MAX_REVISION_NUMBER == 27

    with pytest.raises(QuoteRevisionLetterExhaustedError) as excinfo:
        revision_letter(MAX_REVISION_NUMBER + 1)

    # Never invents "AA" or wraps back to "A".
    assert "quote_revision_letter_exhausted" in str(excinfo.value)


def test_revision_number_below_one_is_rejected():
    with pytest.raises(ValueError):
        revision_letter(0)


# ---------------------------------------------------------------------------
# Human quote_number rendering
# ---------------------------------------------------------------------------


def test_seeded_serial_renders_the_decided_display_form():
    # The D2b worked example: seed 1235, issued 2026.
    assert (
        render_quote_number(serial=1235, pad_width=5, issue_year=2026)
        == "01235-26"
    )


def test_revisions_suffix_the_human_number_after_a_space():
    assert (
        render_quote_number(
            serial=1235, pad_width=5, issue_year=2026, revision_number=2
        )
        == "01235-26 A"
    )
    assert (
        render_quote_number(
            serial=1235, pad_width=5, issue_year=2026, revision_number=3
        )
        == "01235-26 B"
    )


def test_year_is_display_metadata_not_sequence_identity():
    """The same serial in a later year renders a different display string.

    This is the observable consequence of "one global sequence, no annual
    reset": crossing a year boundary changes only what is rendered, never
    which serial comes next.
    """
    assert render_quote_number(serial=1235, pad_width=5, issue_year=2027) == (
        "01235-27"
    )


def test_quote_number_never_carries_the_document_prefix():
    rendered = render_quote_number(serial=1235, pad_width=5, issue_year=2026)

    assert "CN" not in rendered


def test_rendered_quote_number_fits_the_durable_column():
    # commercial.customer_quote.quote_number is CHECK length <= 32.
    longest = render_quote_number(
        serial=9_999_999_999,
        pad_width=10,
        issue_year=2999,
        revision_number=MAX_REVISION_NUMBER,
    )

    assert len(longest) <= 32


@pytest.mark.parametrize("issue_year", [1999, 3000])
def test_implausible_issue_years_are_rejected(issue_year):
    with pytest.raises(ValueError):
        render_quote_number(serial=1235, pad_width=5, issue_year=issue_year)


# ---------------------------------------------------------------------------
# Drive document_number rendering
# ---------------------------------------------------------------------------


def test_document_number_is_prefix_plus_padded_serial():
    assert (
        render_document_number(document_prefix="CN", serial=1235, pad_width=5)
        == "CN01235"
    )


def test_document_number_revision_letter_attaches_without_a_separator():
    """Matches the sole real historical artifact, the "CN011728A" filename."""
    assert (
        render_document_number(
            document_prefix="CN", serial=11728, pad_width=5, revision_number=2
        )
        == "CN11728A"
    )


def test_document_number_carries_no_year():
    rendered = render_document_number(
        document_prefix="CN", serial=1235, pad_width=5
    )

    # The stem is exactly prefix + padded serial, nothing else -- no year
    # component and no separator of any kind.
    assert rendered == "CN01235"
    assert "-" not in rendered


def test_blank_document_prefix_is_rejected():
    with pytest.raises(ValueError):
        render_document_number(document_prefix="  ", serial=1235, pad_width=5)


# ---------------------------------------------------------------------------
# Parsing back
# ---------------------------------------------------------------------------


def test_parse_round_trips_an_unsuffixed_number():
    parsed = parse_origenlab_quote_number("01235-26")

    assert parsed == ParsedQuoteNumber(
        serial=1235, issue_year_2=26, revision_letter=None
    )


def test_parse_round_trips_a_revision():
    parsed = parse_origenlab_quote_number("01235-26 A")

    assert parsed == ParsedQuoteNumber(
        serial=1235, issue_year_2=26, revision_letter="A"
    )


def test_parse_recognises_the_historical_outlier():
    parsed = parse_origenlab_quote_number("01500-26")

    assert parsed is not None
    assert parsed.serial == 1500


@pytest.mark.parametrize(
    "value",
    [
        "CN01235",          # document stem, not a human number
        "COT-2026-014",     # legacy Labdelivery space
        "01235",            # no year
        "01235-2026",       # 4-digit year is not the rendering
        "01235-26A",        # missing the approved separator
        "01235-26 a",       # lowercase letter is not the approved rendering
        "",
    ],
)
def test_parse_returns_none_for_anything_that_is_not_this_format(value):
    assert parse_origenlab_quote_number(value) is None


# ---------------------------------------------------------------------------
# Legacy Labdelivery number space
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["COT-2026-014", "COT 2026 14", "cot-2020-7", "  COT-2017-00123  "],
)
def test_legacy_labdelivery_numbers_are_recognised(value):
    assert is_legacy_labdelivery_quote_number(value) is True


@pytest.mark.parametrize("value", ["01235-26", "CN01235", "01500-26", "COT"])
def test_origenlab_numbers_are_not_in_the_legacy_space(value):
    assert is_legacy_labdelivery_quote_number(value) is False


def test_the_two_number_spaces_do_not_overlap():
    """No string can be both a valid OrigenLab number and a legacy one."""
    for value in ["01235-26", "01235-26 A", "COT-2026-014", "COT 2026 14"]:
        in_origenlab = parse_origenlab_quote_number(value) is not None
        in_legacy = is_legacy_labdelivery_quote_number(value)

        assert not (in_origenlab and in_legacy), value


# ---------------------------------------------------------------------------
# The 01500-26 exception
# ---------------------------------------------------------------------------


def test_the_outlier_serial_is_reserved():
    assert 1500 in HISTORICAL_OUTLIER_SERIALS


def test_allocating_the_reserved_serial_fails_closed():
    with pytest.raises(QuoteSerialReservedError) as excinfo:
        require_allocatable_serial(1500)

    assert "quote_serial_reserved" in str(excinfo.value)


def test_the_guard_never_silently_advances_to_the_next_serial():
    """Failing closed, not skipping: the caller gets an error, not 1501."""
    with pytest.raises(QuoteSerialReservedError):
        require_allocatable_serial(1500)

    assert require_allocatable_serial(1501) == 1501


@pytest.mark.parametrize("serial", [1235, 1499, 1501, 9999])
def test_ordinary_serials_pass_the_guard_unchanged(serial):
    assert require_allocatable_serial(serial) == serial


def test_the_seed_is_clear_of_the_reserved_serial():
    """1235 (the approved seed) is allocatable, and 1500 is ahead of it."""
    assert require_allocatable_serial(1235) == 1235
    assert min(HISTORICAL_OUTLIER_SERIALS) > 1235
