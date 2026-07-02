"""Equipment opportunity source metadata contract."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from origenlab_api.repositories.equipment_opportunities import fetch_equipment_opportunities


def test_local_equipment_csv_bridge_exposes_semantic_source_metadata(tmp_path: Path) -> None:
    active = tmp_path / "current"
    active.mkdir()
    filename = "equipment_first_operator_queue_20260702.csv"
    (active / "manifest.json").write_text(
        json.dumps({"canonical_files": [filename], "campaign_mode": "equipment_first"}),
        encoding="utf-8",
    )
    with (active / filename).open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "priority_rank",
                "codigo_licitacion",
                "buyer",
                "region",
                "close_date",
                "equipment_category",
                "item_description",
                "next_action",
                "safe_channel",
                "supplier_needed",
                "contact_status",
                "contact_email",
                "operator_note",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "priority_rank": "1",
                "codigo_licitacion": "123-1-LQ26",
                "buyer": "Hospital Demo",
                "region": "Los Ríos",
                "close_date": "2026-07-10",
                "equipment_category": "centrifuge",
                "item_description": "Centrífuga refrigerada",
                "next_action": "review_tender",
                "safe_channel": "mercado_publico_bid",
                "supplier_needed": "yes",
                "contact_status": "no_verified_buyer_email",
                "contact_email": "",
                "operator_note": "fixture",
            }
        )

    items, meta = fetch_equipment_opportunities(active, limit=5)

    assert len(items) == 1
    assert meta.data_source == "active_current_csv"
    assert meta.source_kind == "csv_artifact"
    assert meta.artifact_basename == filename
    assert meta.canonical_reason == "active_current_csv_fallback"
    assert meta.source_path.endswith(filename)
    assert meta.campaign_mode == "equipment_first"
    assert meta.read_only is True


def test_missing_local_equipment_queue_metadata_is_explicit(tmp_path: Path) -> None:
    active = tmp_path / "current"
    active.mkdir()
    (active / "manifest.json").write_text(json.dumps({"canonical_files": []}), encoding="utf-8")

    items, meta = fetch_equipment_opportunities(active, limit=5)

    assert items == []
    assert meta.reduced_mode is True
    assert meta.source_kind == "csv_artifact"
    assert meta.artifact_basename == ""
    assert meta.canonical_reason == "missing_active_current_queue"
    assert "not found" in meta.note
