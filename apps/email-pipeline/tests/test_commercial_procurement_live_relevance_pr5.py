"""PR5A live-relevance walkthrough redaction and deterministic selection tests."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from origenlab_email_pipeline.commercial_procurement.walkthrough_redaction import (
    assert_no_pii_leaks,
    contains_email_pattern,
)
from origenlab_email_pipeline.commercial_procurement_live_relevance.walkthrough import (
    build_pr5_walkthrough_bundle,
    pick_best_open_row,
    render_walkthrough_markdown,
    select_case_ids,
)

FIXTURES = Path(__file__).parent / "fixtures" / "commercial_procurement_live_relevance_pr5"


def test_pick_best_open_row_prefers_equipment_category() -> None:
    rows = list(
        __import__("csv").DictReader(
            (FIXTURES / "equipment_queue_open.csv").open(encoding="utf-8")
        )
    )
    best = pick_best_open_row(rows)
    assert best is not None
    assert best["validity_status"] == "open"
    assert best["equipment_category"] == "centrifuge"


def test_select_case_ids_deterministic() -> None:
    seeds = json.loads((FIXTURES / "case_seeds.json").read_text())
    a = select_case_ids(seeds)
    b = select_case_ids(seeds)
    assert a == b
    assert a["case_b"] == "p_fixture_hist_equip_001"
    assert a["case_c"] == "p_fixture_excl_001"


def test_walkthrough_bundle_redacts_and_marks_genuine_active() -> None:
    seeds = json.loads((FIXTURES / "case_seeds.json").read_text())
    as_of = datetime(2026, 8, 1, 12, 0, tzinfo=ZoneInfo("America/Santiago"))
    bundle = build_pr5_walkthrough_bundle(
        seeds=seeds,
        open_queue_csv=FIXTURES / "equipment_queue_open.csv",
        as_of=as_of,
    )
    assert bundle.summary["genuine_active_tenders_in_walkthrough"] is True
    assert bundle.case_a["genuine_live_active"] is True
    assert bundle.case_a["synthetic_active_overlay"] is False
    assert bundle.case_b["case_id"] == "B_historical_equipment"
    assert bundle.case_c["stages"][0]["result"] == "consumable_or_reagent"
    assert bundle.case_d["planned_pr5_rows"]
    assert bundle.case_e["planned_pr5_rows"]

    md = render_walkthrough_markdown(bundle)
    blob = md + json.dumps(
        {
            "a": bundle.case_a,
            "b": bundle.case_b,
            "c": bundle.case_c,
            "d": bundle.case_d,
            "e": bundle.case_e,
        },
        default=str,
    )
    assert not contains_email_pattern(blob)
    assert "Hospital Demo Sur" not in blob
    assert "9999-1-LE26" not in blob
    assert "mercadopublico.cl" not in blob
    assert_no_pii_leaks(blob)


def test_walkthrough_without_open_rows_reports_missing_active() -> None:
    seeds = json.loads((FIXTURES / "case_seeds.json").read_text())
    # empty csv path → missing
    bundle = build_pr5_walkthrough_bundle(
        seeds=seeds,
        open_queue_csv=None,
        as_of=datetime(2026, 8, 1, 12, 0, tzinfo=ZoneInfo("America/Santiago")),
    )
    assert bundle.summary["genuine_active_tenders_in_walkthrough"] is False
    assert bundle.case_a.get("genuine_live_active") is False
