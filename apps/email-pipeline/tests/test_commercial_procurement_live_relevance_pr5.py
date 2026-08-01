"""PR5A live-relevance — open classification, determinism, taxonomy, walkthrough."""

from __future__ import annotations

import json
import os
import random
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from origenlab_email_pipeline.commercial_procurement.walkthrough_redaction import (
    assert_no_pii_leaks,
    contains_email_pattern,
)
from origenlab_email_pipeline.commercial_procurement_live_relevance.artifact_open import (
    ArtifactProvenance,
    classify_artifact_row_open,
    pick_best_open_row,
)
from origenlab_email_pipeline.commercial_procurement_live_relevance.taxonomy import (
    validate_taxonomy_mapping_completeness,
)
from origenlab_email_pipeline.commercial_procurement_live_relevance.walkthrough import (
    build_pr5_walkthrough_bundle,
    render_walkthrough_markdown,
    select_case_ids,
)

FIXTURES = Path(__file__).parent / "fixtures" / "commercial_procurement_live_relevance_pr5"
SANTIAGO = ZoneInfo("America/Santiago")
AS_OF = datetime(2026, 8, 1, 12, 0, tzinfo=SANTIAGO)


def _prov(
    *,
    provenance_status: str = "valid",
    freshness_status: str = "recent",
) -> ArtifactProvenance:
    return ArtifactProvenance(
        path="/tmp/fixture.csv",
        sha256="abc",
        size_bytes=10,
        mtime_utc="2026-08-01T10:00:00Z",
        filename_date="2026-08-01",
        generated_at_utc="2026-08-01T10:00:00Z",
        source_query_metadata={"row_count": 1},
        manifest_status="canonical",
        artifact_age_seconds=3600.0,
        provenance_status=provenance_status,
        freshness_status=freshness_status,
    )


def _row(**overrides: str) -> dict[str, str]:
    base = {
        "codigo_licitacion": "9999-1-LE26",
        "buyer": "Hospital Demo Sur",
        "validity_status": "open",
        "chilecompra_status_code": "5",
        "chilecompra_status": "Publicada",
        "close_date": "2026-08-10T16:00:00",
        "equipment_category": "centrifuge",
        "fit_score": "90",
        "title": "Adquisicion centrifuga",
        "item_description": "Centrifuga refrigerada",
    }
    base.update(overrides)
    return base


def test_taxonomy_mapping_completeness() -> None:
    result = validate_taxonomy_mapping_completeness()
    assert result["ok"], result["errors"]


def test_open_past_close_is_not_recent_declared() -> None:
    oc = classify_artifact_row_open(
        _row(close_date="2026-07-01T16:00:00"),
        as_of=AS_OF,
        provenance=_prov(),
    )
    assert oc.open_class == "artifact_not_open"


def test_open_malformed_close_date() -> None:
    oc = classify_artifact_row_open(
        _row(close_date="not-a-date"),
        as_of=AS_OF,
        provenance=_prov(),
    )
    assert oc.open_class == "date_unparseable"


def test_code5_conflicting_status_name() -> None:
    oc = classify_artifact_row_open(
        _row(chilecompra_status="Cerrada"),
        as_of=AS_OF,
        provenance=_prov(),
    )
    assert oc.open_class == "status_or_date_conflict"


def test_code6_with_validity_open() -> None:
    oc = classify_artifact_row_open(
        _row(chilecompra_status_code="6", chilecompra_status="Cerrada"),
        as_of=AS_OF,
        provenance=_prov(),
    )
    assert oc.open_class == "status_or_date_conflict"


def test_close_equal_to_as_of_not_open() -> None:
    oc = classify_artifact_row_open(
        _row(close_date="2026-08-01T12:00:00"),
        as_of=AS_OF,
        provenance=_prov(),
    )
    assert oc.open_class == "artifact_not_open"


def test_future_close_america_santiago_recent() -> None:
    oc = classify_artifact_row_open(
        _row(close_date="2026-08-10T16:00:00"),
        as_of=AS_OF,
        provenance=_prov(),
    )
    assert oc.open_class == "recent_artifact_declared_open"
    assert oc.close_at_america_santiago is not None


def test_stale_artifact() -> None:
    oc = classify_artifact_row_open(
        _row(),
        as_of=AS_OF,
        provenance=_prov(freshness_status="stale"),
    )
    assert oc.open_class == "stale_artifact_declared_open"


def test_missing_provenance() -> None:
    oc = classify_artifact_row_open(
        _row(),
        as_of=AS_OF,
        provenance=_prov(provenance_status="insufficient"),
    )
    assert oc.open_class == "artifact_declared_open_unverified_provenance"


def test_live_verified_unreachable_without_flag() -> None:
    oc = classify_artifact_row_open(_row(), as_of=AS_OF, provenance=_prov())
    assert oc.open_class != "live_verified_open"


def test_pick_best_open_row_shuffled_determinism() -> None:
    rows = [
        _row(codigo_licitacion="1111-1-LE26", fit_score="50", equipment_category="balance"),
        _row(codigo_licitacion="2222-2-LE26", fit_score="95", equipment_category="centrifuge"),
        _row(codigo_licitacion="3333-3-LE26", fit_score="80", equipment_category="centrifuge"),
        _row(
            codigo_licitacion="4444-4-LE26",
            validity_status="open",
            close_date="2026-07-01T16:00:00",
            fit_score="99",
            equipment_category="centrifuge",
        ),
    ]
    prov = _prov()
    a, oa, _ = pick_best_open_row(rows, as_of=AS_OF, provenance=prov)
    shuffled = rows[:]
    random.Random(0).shuffle(shuffled)
    b, ob, _ = pick_best_open_row(shuffled, as_of=AS_OF, provenance=prov)
    assert a is not None and b is not None
    assert a["codigo_licitacion"] == b["codigo_licitacion"] == "2222-2-LE26"
    assert oa is not None and ob is not None
    assert oa.open_class == ob.open_class == "recent_artifact_declared_open"


def test_select_case_ids_deterministic() -> None:
    seeds = json.loads((FIXTURES / "case_seeds.json").read_text())
    assert select_case_ids(seeds) == select_case_ids(seeds)


def test_walkthrough_bundle_terminology_and_outcomes(tmp_path: Path) -> None:
    seeds = json.loads((FIXTURES / "case_seeds.json").read_text())
    src = (FIXTURES / "equipment_queue_open.csv").read_text(encoding="utf-8")
    csv_path = tmp_path / "equipment_first_operator_queue_chilecompra_api_20260801.csv"
    csv_path.write_text(src, encoding="utf-8")
    (
        tmp_path / "equipment_first_operator_queue_chilecompra_api_20260801.manifest.json"
    ).write_text(
        json.dumps(
            {
                "generated_at_utc": "2026-08-01T10:00:00Z",
                "row_count": 2,
                "source_kind": "chilecompra_api",
            }
        ),
        encoding="utf-8",
    )
    recent = (AS_OF - timedelta(hours=2)).timestamp()
    os.utime(csv_path, (recent, recent))

    bundle = build_pr5_walkthrough_bundle(
        seeds=seeds,
        open_queue_csv=csv_path,
        as_of=AS_OF,
        manifest={
            "canonical_files": ["equipment_first_operator_queue_20260801.csv"],
            "chilecompra_api_publish": {
                "published_queue": "equipment_first_operator_queue_20260801.csv",
                "source_manifest": (
                    "equipment_first_operator_queue_chilecompra_api_20260801.manifest.json"
                ),
            },
        },
        fixture_mode=True,
    )
    assert bundle.summary["live_verified_open_count"] == 0
    assert bundle.summary["current_status_independently_revalidated"] is False
    assert bundle.case_a.get("live_verified_open") is False
    assert any(
        r["planned_redacted_row"].get("candidate_outcome_state")
        == "account_resolution_required"
        for r in bundle.case_a.get("planned_pr5_rows") or []
    )
    tables = [r["proposed_table"] for r in bundle.case_c["planned_pr5_rows"]]
    assert "commercial_procurement_candidate_conflict" not in tables
    assert "commercial_procurement_candidate_evidence" in tables
    assert any(
        r["planned_redacted_row"].get("candidate_outcome_state") == "not_eligible"
        for r in bundle.case_d["planned_pr5_rows"]
    )
    assert any(
        r["planned_redacted_row"].get("candidate_outcome_state") == "not_eligible"
        for r in bundle.case_e["planned_pr5_rows"]
    )
    assert "hypothetical_contact_path" in bundle.case_d
    assert "hypothetical_contact_path" in bundle.case_e

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


def test_walkthrough_without_queue() -> None:
    seeds = json.loads((FIXTURES / "case_seeds.json").read_text())
    bundle = build_pr5_walkthrough_bundle(
        seeds=seeds, open_queue_csv=None, as_of=AS_OF, fixture_mode=True
    )
    assert bundle.summary["live_verified_open_count"] == 0
    assert bundle.case_a.get("live_verified_open") is False
