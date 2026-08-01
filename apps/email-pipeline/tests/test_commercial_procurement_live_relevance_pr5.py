"""PR5A live-relevance — provenance, sanitization, redaction, taxonomy, walkthrough."""

from __future__ import annotations

import json
import os
import random
import sqlite3
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
    inspect_artifact_provenance,
    pick_best_open_row,
    sanitize_source_query_metadata,
)
from origenlab_email_pipeline.commercial_procurement_live_relevance.constants import (
    CONTACT_RESOLUTION_DEFERRED,
)
from origenlab_email_pipeline.commercial_procurement_live_relevance.production_cases import (
    assert_no_forbidden_identifier_leaks,
    select_production_cases,
)
from origenlab_email_pipeline.commercial_procurement_live_relevance.taxonomy import (
    CONTEXT_REQUIRED_ALIASES,
    validate_taxonomy_mapping_completeness,
)
from origenlab_email_pipeline.commercial_procurement_live_relevance.walkthrough import (
    build_case_a_from_open_queue_row,
    build_case_d_contact_research,
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
    ts_source: str = "generated_at_utc",
    ts_validation: str = "ok",
) -> ArtifactProvenance:
    return ArtifactProvenance(
        path="/tmp/fixture.csv",
        sha256="abc",
        size_bytes=10,
        mtime_utc="2026-08-01T10:00:00Z",
        filename_date="2026-08-01",
        generated_at_utc="2026-08-01T10:00:00Z",
        effective_artifact_timestamp_utc="2026-08-01T10:00:00Z",
        effective_timestamp_source=ts_source,
        generated_vs_mtime_delta_seconds=0.0,
        timestamp_validation_status=ts_validation,
        source_query_metadata={"row_count": 1, "estado": "activas"},
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


def _write_queue(
    tmp_path: Path,
    *,
    generated_at: str | None,
    mtime_offset_hours: float,
    filename: str = "equipment_first_operator_queue_chilecompra_api_20260801.csv",
    extra_meta: dict | None = None,
) -> Path:
    src = (FIXTURES / "equipment_queue_open.csv").read_text(encoding="utf-8")
    csv_path = tmp_path / filename
    csv_path.write_text(src, encoding="utf-8")
    meta = {"row_count": 2, "source_kind": "chilecompra_api", "estado": "activas"}
    if generated_at is not None:
        meta["generated_at_utc"] = generated_at
    if extra_meta:
        meta.update(extra_meta)
    (
        tmp_path / filename.replace(".csv", ".manifest.json")
    ).write_text(json.dumps(meta), encoding="utf-8")
    ts = (AS_OF - timedelta(hours=mtime_offset_hours)).timestamp()
    os.utime(csv_path, (ts, ts))
    return csv_path


def test_taxonomy_mapping_completeness() -> None:
    result = validate_taxonomy_mapping_completeness()
    assert result["ok"], result["errors"]


def test_taxonomy_context_required_aliases_structural() -> None:
    sonicator = next(a for a in CONTEXT_REQUIRED_ALIASES if a["source_alias"] == "sonicator")
    assert sonicator["resolution_kind"] == "context_required"
    assert set(sonicator["candidate_classes"]) == {
        "ultrasonic_processor",
        "ultrasonic_bath",
    }
    assert sonicator["disambiguation_rules"]
    homog = next(
        a for a in CONTEXT_REQUIRED_ALIASES if a["source_alias"] == "homogenizer_regex_hit"
    )
    assert "shaker" in homog["candidate_classes"]
    assert "vortex_mixer" in homog["candidate_classes"]
    assert "magnetic_stirrer" in homog["candidate_classes"]


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


def test_stale_generated_recent_mtime_not_recent_declared(tmp_path: Path) -> None:
    csv_path = _write_queue(
        tmp_path,
        generated_at="2026-07-20T10:00:00Z",
        mtime_offset_hours=1,
    )
    prov = inspect_artifact_provenance(
        csv_path,
        as_of=AS_OF,
        manifest={"canonical_files": [csv_path.name]},
    )
    assert prov.effective_timestamp_source == "generated_at_utc"
    assert prov.freshness_status == "stale"
    # mtime touch must not revive a stale acquisition into recent_declared_open.
    oc = classify_artifact_row_open(_row(), as_of=AS_OF, provenance=prov)
    assert oc.open_class != "recent_artifact_declared_open"
    assert oc.open_class in {
        "stale_artifact_declared_open",
        "artifact_declared_open_unverified_provenance",
    }


def test_recent_generated_old_mtime_within_delta(tmp_path: Path) -> None:
    csv_path = _write_queue(
        tmp_path,
        generated_at="2026-08-01T10:00:00Z",
        mtime_offset_hours=20,
    )
    prov = inspect_artifact_provenance(
        csv_path,
        as_of=AS_OF,
        manifest={"canonical_files": [csv_path.name]},
    )
    assert prov.provenance_status == "valid"
    assert prov.freshness_status == "recent"
    assert prov.effective_timestamp_source == "generated_at_utc"
    oc = classify_artifact_row_open(_row(), as_of=AS_OF, provenance=prov)
    assert oc.open_class == "recent_artifact_declared_open"


def test_future_generated_at_rejected(tmp_path: Path) -> None:
    csv_path = _write_queue(
        tmp_path,
        generated_at="2026-08-02T10:00:00Z",
        mtime_offset_hours=2,
    )
    prov = inspect_artifact_provenance(
        csv_path,
        as_of=AS_OF,
        manifest={"canonical_files": [csv_path.name]},
    )
    assert prov.provenance_status == "insufficient"
    oc = classify_artifact_row_open(_row(), as_of=AS_OF, provenance=prov)
    assert oc.open_class == "artifact_declared_open_unverified_provenance"


def test_malformed_generated_at(tmp_path: Path) -> None:
    csv_path = _write_queue(
        tmp_path,
        generated_at="not-a-timestamp",
        mtime_offset_hours=2,
    )
    prov = inspect_artifact_provenance(
        csv_path,
        as_of=AS_OF,
        manifest={"canonical_files": [csv_path.name]},
    )
    assert prov.provenance_status == "insufficient"


def test_filename_date_disagreement(tmp_path: Path) -> None:
    csv_path = _write_queue(
        tmp_path,
        generated_at="2026-08-01T10:00:00Z",
        mtime_offset_hours=2,
        filename="equipment_first_operator_queue_chilecompra_api_20260101.csv",
    )
    prov = inspect_artifact_provenance(
        csv_path,
        as_of=AS_OF,
        manifest={"canonical_files": [csv_path.name]},
    )
    assert prov.timestamp_validation_status == "filename_date_inconsistent"
    assert prov.provenance_status == "insufficient"


def test_mtime_only_cannot_qualify_recent(tmp_path: Path) -> None:
    src = (FIXTURES / "equipment_queue_open.csv").read_text(encoding="utf-8")
    csv_path = tmp_path / "orphan_queue.csv"
    csv_path.write_text(src, encoding="utf-8")
    ts = (AS_OF - timedelta(hours=1)).timestamp()
    os.utime(csv_path, (ts, ts))
    prov = inspect_artifact_provenance(csv_path, as_of=AS_OF, manifest=None)
    assert prov.effective_timestamp_source == "mtime_unverified_fallback"
    assert prov.provenance_status == "insufficient"
    assert prov.freshness_status == "unknown"
    oc = classify_artifact_row_open(_row(), as_of=AS_OF, provenance=prov)
    assert oc.open_class == "artifact_declared_open_unverified_provenance"


def test_sanitize_source_query_metadata_redacts_ticket() -> None:
    raw = {
        "estado": "activas",
        "fecha": "01082026",
        "source_kind": "chilecompra_api",
        "row_count": 12,
        "query": "https://api.mercadopublico.cl/servicios/v1/publico/licitaciones.json?ticket=SECRET&estado=activas",
        "nested": {"Ticket": "SECRET", "token": "abc", "ok": 1},
        "url": "https://example.com/path?api_key=SECRET&fecha=1",
        "encoded": "https://x/?ticket%3DSECRET",
    }
    clean = sanitize_source_query_metadata(raw)
    blob = json.dumps(clean)
    assert "SECRET" not in blob
    assert "ticket" not in blob.lower() or "ticket" not in str(clean.get("query", ""))
    assert clean.get("estado") == "activas"
    assert clean.get("fecha") == "01082026"
    assert clean.get("row_count") == 12
    assert clean.get("endpoint_path") == "/servicios/v1/publico/licitaciones.json" or (
        clean.get("endpoint_path") == "/path"
    )


def test_opaque_identifier_leak_detection() -> None:
    forbidden = {
        "p_4f103151",
        "account_123_real",
        "source-record-456",
        "contact_789_real",
        "9999-88-LE26",
    }
    safe = '{"procurement_id_redacted":"procurement_aabbccddeeff"}'
    assert_no_forbidden_identifier_leaks(safe, forbidden)
    for bad in forbidden:
        try:
            assert_no_forbidden_identifier_leaks(f"found {bad} here", forbidden)
            raise AssertionError(f"expected leak detection for {bad}")
        except AssertionError as exc:
            assert "forbidden" in str(exc).lower()


def test_case_a_deferred_contact_not_no_contact_found() -> None:
    from origenlab_email_pipeline.commercial_procurement_live_relevance.artifact_open import (
        classify_artifact_row_open,
    )

    row = _row()
    oc = classify_artifact_row_open(row, as_of=AS_OF, provenance=_prov())
    case = build_case_a_from_open_queue_row(
        row,
        artifact_path=Path("fixture.csv"),
        as_of=AS_OF,
        classification=oc,
        provenance=_prov(),
    )
    contact_row = next(
        r
        for r in case["planned_pr5_rows"]
        if r["proposed_table"] == "commercial_procurement_contact_resolution"
    )
    planned = contact_row["planned_redacted_row"]
    assert planned["final_contact_status"] == CONTACT_RESOLUTION_DEFERRED
    assert planned["search_stages_completed"] == []
    assert planned["considered_contact_count"] == 0
    assert planned["next_action"] == "resolve_account"
    assert planned["reason_code"] == "account_unresolved"
    assert planned["final_contact_status"] != "no_contact_found"
    assert case["contact_resolution_status"] == CONTACT_RESOLUTION_DEFERRED


def test_case_d_unavailable_when_no_zero_contact() -> None:
    case = build_case_d_contact_research(
        {
            "unavailable": True,
            "missing_reason": "no_linked_account_with_zero_pr2_contacts",
        }
    )
    assert case["unavailable"] is True
    assert case["planned_pr5_rows"] == []


def test_case_d_zero_contact_enforcement() -> None:
    case = build_case_d_contact_research(
        {
            "procurement_id_redacted": "procurement_aaaaaaaaaaaa",
            "contact_n": 0,
            "close_at": "2026-04-01",
            "link_route": "A_exact",
        }
    )
    assert case.get("unavailable") is not True
    try:
        build_case_d_contact_research({"contact_n": 2, "procurement_id_redacted": "x"})
        raise AssertionError("expected ValueError for contactful Case D")
    except ValueError:
        pass


def test_case_d_sql_zero_contact_and_shuffled_independence(tmp_path: Path) -> None:
    db = tmp_path / "cases.sqlite"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE commercial_procurement_signal (
          procurement_id TEXT PRIMARY KEY,
          canonical_tender_key TEXT,
          procurement_context TEXT,
          close_at TEXT,
          status_code TEXT,
          status_name TEXT,
          line_item_count INTEGER
        );
        CREATE TABLE commercial_procurement_account_resolution (
          procurement_id TEXT PRIMARY KEY,
          resolution_status TEXT,
          link_route TEXT,
          account_id TEXT,
          confidence REAL
        );
        CREATE TABLE commercial_procurement_evidence (
          subject_id TEXT,
          subject_kind TEXT,
          source_table TEXT,
          source_record_id TEXT
        );
        CREATE TABLE commercial_identity_contact (
          contact_id TEXT,
          account_id TEXT,
          role TEXT,
          display_name TEXT,
          normalized_email TEXT
        );
        CREATE TABLE external_leads_raw (
          source_record_id TEXT PRIMARY KEY,
          raw_json TEXT
        );
        CREATE TABLE contact_email_suppression (email_norm TEXT);
        """
    )
    # Insertion order deliberately opposite of procurement_id order for zero-contact.
    rows = [
        ("p_z", "t_z", "acct_with"),
        ("p_a", "t_a", "acct_zero_a"),
        ("p_m", "t_m", "acct_zero_m"),
    ]
    for pid, tk, acct in rows:
        conn.execute(
            "INSERT INTO commercial_procurement_signal VALUES (?,?,?,?,?,?,?)",
            (pid, tk, "historical_tender", "2026-01-01", "6", "Cerrada", 1),
        )
        conn.execute(
            "INSERT INTO commercial_procurement_account_resolution VALUES (?,?,?,?,?)",
            (pid, "linked", "A", acct, 1.0),
        )
        conn.execute(
            "INSERT INTO commercial_procurement_evidence VALUES (?,?,?,?)",
            (pid, "signal", "external_leads_raw", f"src_{pid}"),
        )
        conn.execute(
            "INSERT INTO external_leads_raw VALUES (?,?)",
            (f"src_{pid}", json.dumps({"desc": "compra centrifuga reactivo"})),
        )
    conn.execute(
        "INSERT INTO commercial_identity_contact VALUES (?,?,?,?,?)",
        ("c1", "acct_with", "compras", "N", "a@b.cl"),
    )
    conn.commit()

    sel1 = select_production_cases(
        conn, pr4_semantic_digest="d", pr2_identity_fingerprint="i"
    )
    assert sel1.case_d.get("unavailable") is not True
    assert sel1.case_d["contact_n"] == 0
    assert "procurement_id" not in sel1.case_d
    assert "typed_ids_before_redaction" not in sel1.selection_meta
    assert "redacted_selection_ids" in sel1.selection_meta
    # Deterministic: lowest procurement_id among zero-contact accounts.
    assert sel1.selection_meta["redacted_selection_ids"]["case_d"][
        "procurement_id_redacted"
    ].startswith("procurement_")

    # All linked accounts have contacts → Case D unavailable.
    conn.execute("DELETE FROM commercial_identity_contact")
    for acct in ("acct_zero_a", "acct_zero_m", "acct_with"):
        conn.execute(
            "INSERT INTO commercial_identity_contact VALUES (?,?,?,?,?)",
            (f"c_{acct}", acct, "r", "N", None),
        )
    conn.commit()
    sel2 = select_production_cases(
        conn, pr4_semantic_digest="d", pr2_identity_fingerprint="i"
    )
    assert sel2.case_d["unavailable"] is True
    assert sel2.selection_meta["case_d_available"] is False
    conn.close()


def test_select_case_ids_deterministic() -> None:
    seeds = json.loads((FIXTURES / "case_seeds.json").read_text())
    assert select_case_ids(seeds) == select_case_ids(seeds)


def test_walkthrough_bundle_terminology_and_outcomes(tmp_path: Path) -> None:
    seeds = json.loads((FIXTURES / "case_seeds.json").read_text())
    csv_path = _write_queue(
        tmp_path,
        generated_at="2026-08-01T10:00:00Z",
        mtime_offset_hours=2,
    )
    bundle = build_pr5_walkthrough_bundle(
        seeds=seeds,
        open_queue_csv=csv_path,
        as_of=AS_OF,
        manifest={
            "canonical_files": [csv_path.name],
            "chilecompra_api_publish": {
                "published_queue": csv_path.name,
                "source_manifest": csv_path.name.replace(".csv", ".manifest.json"),
            },
        },
        fixture_mode=True,
    )
    assert bundle.summary["live_verified_open_count"] == 0
    assert bundle.summary["current_status_independently_revalidated"] is False
    assert bundle.case_a.get("live_verified_open") is False
    assert bundle.case_a.get("contact_resolution_status") == CONTACT_RESOLUTION_DEFERRED
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
    assert "SECRET" not in blob
    assert_no_pii_leaks(blob)
    assert "contact_resolution_deferred" in blob
    assert "Field flow" in md or "field flow" in md.lower() or "provenance normalization" in md


def test_walkthrough_without_queue() -> None:
    seeds = json.loads((FIXTURES / "case_seeds.json").read_text())
    bundle = build_pr5_walkthrough_bundle(
        seeds=seeds, open_queue_csv=None, as_of=AS_OF, fixture_mode=True
    )
    assert bundle.summary["live_verified_open_count"] == 0
    assert bundle.case_a.get("live_verified_open") is False
