"""The quote reader's evaluation (owner-run switch-on gate): the comparison of a new reading with
the stored one, and the per-model summary. Every value is invented."""

from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "quote_read_eval.py"
spec = importlib.util.spec_from_file_location("quote_read_eval", SCRIPT)
qre = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qre)  # type: ignore[union-attr]

STORED = {"currency": "CLP", "net_total": "2236000",
          "lines": [("ACME1", Decimal("1236000")), ("SONIC100", Decimal("1000000"))]}


def test_an_identical_reading_agrees_on_the_document_and_every_line() -> None:
    new = {"currency": "CLP", "net_total": "2236000",
           "lines": [("ACME1", Decimal("1236000")), ("SONIC100", Decimal("1000000.0000"))]}
    assert qre.compare(STORED, new) == {"totals_matched": 2, "net_agrees": True, "currency_agrees": True, "lines_stored": 2,
                                        "lines_matched": 2, "line_count_agrees": True}


def test_a_different_total_misses_that_line_and_a_different_neto_the_document() -> None:
    new = {"currency": "CLP", "net_total": "2000000",
           "lines": [("ACME1", Decimal("1236000")), ("SONIC100", Decimal("764000"))]}
    out = qre.compare(STORED, new)
    assert (out["net_agrees"], out["lines_matched"]) == (False, 1)


def test_the_summary_reports_agreement_percentages_refusals_and_tokens() -> None:
    results = [
        {"model": "m", "outcome": "read", "net_agrees": True, "currency_agrees": True, "lines_stored": 2,
         "lines_matched": 2, "line_count_agrees": True, "input_tokens": 100, "output_tokens": 10},
        {"model": "m", "outcome": "read", "net_agrees": False, "currency_agrees": True, "lines_stored": 2,
         "lines_matched": 1, "line_count_agrees": True, "input_tokens": 100, "output_tokens": 10},
        {"model": "m", "outcome": "refused", "code": "unreadable", "lines_stored": 1},
    ]
    s = qre.summarize(results)["m"]
    assert s["documents"] == 3 and s["read"] == 2 and s["refused"] == {"unreadable": 1}
    assert s["net_agreement_pct"] == 33.3  # of all documents: a refusal counts against the gate
    assert s["line_agreement_pct"] == 60.0  # 3 matched of 5 stored lines
    assert s["input_tokens"] == 200 and s["passes_gate"] is False


def test_a_document_without_a_printed_neto_is_compared_on_its_non_optional_lines() -> None:
    stored = {"currency": "CLP", "net_total": None,
              "lines": [("A", Decimal("100"), False), ("B", Decimal("50"), True)]}
    assert qre.compare(stored, {"currency": "CLP", "net_total": "100", "lines": [("A", Decimal("100"), False)]})["net_agrees"]
    assert not qre.compare(stored, {"currency": "CLP", "net_total": "150", "lines": []})["net_agrees"]


def test_money_and_model_codes_are_judged_separately() -> None:
    stored = {"currency": "CLP", "net_total": None,
              "lines": [("S18N19G", Decimal("1385000"), False), ("R182", Decimal("85000"), False)]}
    new = {"currency": "CLP", "net_total": "1470000",
           "lines": [("S18N–19G", Decimal("1385000"), False), ("R182", Decimal("85000"), False)]}
    out = qre.compare(stored, new)
    assert out["totals_matched"] == 2 and out["lines_matched"] == 1 and out["net_agrees"]


def test_a_missing_pdf_does_not_count_against_the_reader() -> None:
    results = [
        {"model": "m", "outcome": "read", "net_agrees": True, "lines_stored": 2, "lines_matched": 1,
         "totals_matched": 2, "line_count_agrees": True},
        {"model": "m", "outcome": "refused", "code": "pdf_missing", "lines_stored": 3},
    ]
    s = qre.summarize(results)["m"]
    assert s["documents"] == 1 and s["pdf_missing"] == 1
    assert s["net_agreement_pct"] == 100.0 and s["total_agreement_pct"] == 100.0 and s["line_agreement_pct"] == 50.0
    assert s["passes_gate"] is True  # the gate is the money: neto and line totals
