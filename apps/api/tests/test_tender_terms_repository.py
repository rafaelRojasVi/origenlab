"""``repositories/tender_terms.py`` -- canonical (case/whitespace-insensitive)
tender_id matching.

W1's ``current_opportunity_queue`` tender_code and T1's published
``tender_id`` are meant to refer to the same ChileCompra tender, but may
differ in case (e.g. operator-entered vs. portal-canonical casing). This
module is the sole T1 lookup boundary; matching must be deterministic
(strip + casefold on both sides) -- never fuzzy, never partial/prefix.
"""

from __future__ import annotations

from pathlib import Path

from origenlab_api.repositories.tender_terms import get_tender_terms

from test_procurement_tenders_api import _bundle, _explicit_fact, _write_t1_publication

_UPPER_TENDER_ID = "745712-19-LP26"
_LOWER_REQUEST_CODE = "745712-19-lp26"


def test_lookup_matches_case_insensitively(tmp_path: Path) -> None:
    bundle = _bundle(_UPPER_TENDER_ID, tender_facts=(_explicit_fact(),))
    t1_dest = tmp_path / "tender_terms"
    _write_t1_publication(t1_dest, [bundle])

    row, meta = get_tender_terms(t1_dest, _LOWER_REQUEST_CODE)
    assert row is not None
    assert row["tender_id"] == _UPPER_TENDER_ID
    assert meta.published is True
    assert meta.reduced_mode is False


def test_lookup_matches_with_surrounding_whitespace(tmp_path: Path) -> None:
    bundle = _bundle(_UPPER_TENDER_ID, tender_facts=(_explicit_fact(),))
    t1_dest = tmp_path / "tender_terms"
    _write_t1_publication(t1_dest, [bundle])

    row, meta = get_tender_terms(t1_dest, f"  {_LOWER_REQUEST_CODE}  ")
    assert row is not None
    assert row["tender_id"] == _UPPER_TENDER_ID


def test_unrelated_tender_id_does_not_match(tmp_path: Path) -> None:
    bundle = _bundle(_UPPER_TENDER_ID, tender_facts=(_explicit_fact(),))
    t1_dest = tmp_path / "tender_terms"
    _write_t1_publication(t1_dest, [bundle])

    row, meta = get_tender_terms(t1_dest, "999999-1-LE26")
    assert row is None
    assert meta.published is True  # publication itself is healthy...
    assert meta.canonical_reason == "tender_id_not_in_publication"  # ...just no row for this id


def test_single_character_difference_does_not_match(tmp_path: Path) -> None:
    """Guards against accidental fuzzy/prefix matching: canonical equality is
    strip+casefold ONLY, never partial."""
    bundle = _bundle(_UPPER_TENDER_ID, tender_facts=(_explicit_fact(),))
    t1_dest = tmp_path / "tender_terms"
    _write_t1_publication(t1_dest, [bundle])

    almost = _UPPER_TENDER_ID[:-1] + ("X" if _UPPER_TENDER_ID[-1] != "X" else "Y")
    row, meta = get_tender_terms(t1_dest, almost)
    assert row is None
    assert meta.canonical_reason == "tender_id_not_in_publication"
