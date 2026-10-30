"""Several quotations on one case are alternatives for the same request (owner 2026-10-30): the case shows
one amount — the won one once won — the others read «No elegida», and «Rentabilidad» counts one sale.

Every name and number is invented. The database tests have a database of their own (the `world` fixture
of test_v2_quote_economics, built again for this module), so the counts below hold only these quotes.
"""
from __future__ import annotations

import pytest

from origenlab_api.v2.case_won import quote_outcome
from origenlab_api.v2.crm_workspace import case_money
from test_v2_quote_commands import _case, _owner, _run
from test_v2_quote_economics import _full, _reads, world  # noqa: F401 - `world` is this module's fixture
from v2_command_harness import needs_db


def _sent(number: str, quote_id: str, sent_at: str, net: str) -> dict:
    return {"status": "sent", "quote_number": number, "_quote_id": quote_id, "revision_no": 1, "sent_at": sent_at,
            "is_active": True, "contents": {"currency": "CLP", "net_total": net}}


ALTERNATIVES = [_sent("00011-26", "q-small", "2026-10-01 10:00:00+00", "6000"),
                _sent("00012-26", "q-mid", "2026-10-01 10:05:00+00", "9000"),
                _sent("00013-26", "q-big", "2026-10-01 10:10:00+00", "11000")]


def test_an_open_case_with_alternatives_shows_the_last_sent_not_their_sum() -> None:
    [money] = case_money(ALTERNATIVES)
    assert (money["net_total"], money["quote_number"], money["quote_count"]) == ("11000", "00013-26", 3)


def test_a_won_case_shows_the_quotation_it_was_won_with_whichever_was_sent_last() -> None:
    [money] = case_money(ALTERNATIVES, ("q-small", 1))
    assert (money["net_total"], money["quote_number"], money["quote_count"]) == ("6000", "00011-26", 3)
    # A won revision that is not among the case's revisions falls back to the last sent.
    assert case_money(ALTERNATIVES, ("q-gone", 1))[0]["quote_number"] == "00013-26"


def test_once_won_the_other_quotations_are_not_chosen() -> None:
    assert quote_outcome("q-small", None) is None
    assert quote_outcome("q-small", "q-small") == "won"
    assert quote_outcome("q-big", "q-small") == "not_chosen"


def _approved_on(world: dict, case: str) -> dict:  # noqa: F811 - `world` is the fixture's value here
    created = _run(world, "create-quote", {"opportunity_id": case})
    saved = _run(world, "save-quote-draft", {"revision_id": created["revision_id"],
                                             "expected_version": created["version"], "draft": _full(world)})
    _run(world, "approve-quote-revision", {"revision_id": created["revision_id"], "expected_version": saved["version"]})
    return created


@pytest.fixture(scope="module")
def alternatives(world):  # noqa: F811
    """Two cases with two approved alternatives each; the second case won with its OLDER quotation."""
    dsn = world["dsn"]
    open_case = _case(dsn, world["operator"], world["institution"])
    won_case = _case(dsn, world["operator"], world["institution"])
    open_first, open_second = _approved_on(world, open_case), _approved_on(world, open_case)
    won_first, won_second = _approved_on(world, won_case), _approved_on(world, won_case)
    for stage in ("qualifying", "qualified", "quoting", "negotiating"):
        _owner(dsn, "update crm.opportunity set stage = %s where id = %s", (stage, won_case))
    _owner(dsn, "update crm.opportunity set stage = 'won', closed_at = now(), won_quote_id = %s, won_revision_no = 1 "
                "where id = %s", (won_first["quote_id"], won_case))
    return {"open": (open_first, open_second), "won": (won_first, won_second)}


@needs_db
def test_rentabilidad_counts_one_quotation_per_case(world, alternatives) -> None:  # noqa: F811
    report = _reads(world["dsn"]).economics_report(from_month=None, to_month=None)
    ids = {r["quote_id"] for r in report["rows"]}
    (open_first, open_second), (won_first, won_second) = alternatives["open"], alternatives["won"]
    # An open case: its newest alternative. A won case: the one it was won with, though older.
    assert open_second["quote_id"] in ids and open_first["quote_id"] not in ids
    assert won_first["quote_id"] in ids and won_second["quote_id"] not in ids
    assert report["totals"]["quotes"] == 2


@needs_db
def test_the_cotizador_list_marks_the_winner_and_the_not_chosen(world, alternatives) -> None:  # noqa: F811
    rows = {r["quote_id"]: r for r in _reads(world["dsn"]).quote_list(status="all", q=None, limit=50,
                                                                      cursor=None)["rows"]}
    (open_first, _), (won_first, won_second) = alternatives["open"], alternatives["won"]
    assert rows[won_first["quote_id"]]["outcome"] == "won"
    assert rows[won_second["quote_id"]]["outcome"] == "not_chosen"
    assert rows[open_first["quote_id"]]["outcome"] is None
