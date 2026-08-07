"""Focused tests for PR5B.2 live equipment feed bridge."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from origenlab_email_pipeline.commercial_identity.schema import (
    ensure_commercial_identity_tables,
)
from origenlab_email_pipeline.commercial_procurement.builder import (
    _read_semantic_rows_from_db,
)
from origenlab_email_pipeline.commercial_procurement.constants import (
    BUILD_CONTRACT,
    SCHEMA_VERSION,
)
from origenlab_email_pipeline.commercial_procurement.ids import semantic_plan_digest
from origenlab_email_pipeline.commercial_procurement.schema import (
    ensure_commercial_procurement_tables,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.ticket_api import (
    _extract_publication_date,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.planner import (
    build_candidate_plan,
)
from origenlab_email_pipeline.commercial_procurement_live_feed_bridge.adapter import (
    adapt_detail_cache_directory,
    adapt_detail_cache_file,
)
from origenlab_email_pipeline.commercial_procurement_live_feed_bridge.buckets import (
    bucket_policy_document,
    classify_commercial_bucket,
    reconcile_bucket_counts,
)
from origenlab_email_pipeline.commercial_procurement_live_feed_bridge.freshness import (
    LiveFeedFreshnessError,
    evaluate_feed_freshness,
)
from origenlab_email_pipeline.commercial_procurement_live_feed_bridge.planner import (
    build_live_feed_review,
)

FIXTURES = Path(__file__).parent / "fixtures"
LIVE = FIXTURES / "commercial_procurement_acquisition_live_contract"
AS_OF = "2026-08-01T19:00:30Z"


def _seed_pr4_db(path: Path, signals: list[dict] | None = None) -> None:
    conn = sqlite3.connect(path)
    ensure_commercial_procurement_tables(conn)
    meta = {
        "source_fingerprint": "a" * 64,
        "build_plan_fingerprint": "b" * 64,
        "semantic_plan_digest": "c" * 64,
        "schema_version": SCHEMA_VERSION,
        "build_contract": BUILD_CONTRACT,
        "identity_fingerprint": "d" * 64,
        "as_of_date": "2026-07-30",
        "source_fingerprint_algorithm": "procurement_source_fp_v1",
        "build_plan_fingerprint_algorithm": "procurement_build_plan_fp_v1",
        "semantic_plan_digest_algorithm": "procurement_semantic_plan_digest_v1",
    }
    for k, v in meta.items():
        conn.execute(
            "INSERT OR REPLACE INTO commercial_procurement_build_meta(meta_key, meta_value) "
            "VALUES (?,?)",
            (k, v),
        )
    conn.row_factory = sqlite3.Row
    for sig in signals or []:
        conn.execute(
            """
            INSERT INTO commercial_procurement_signal(
              procurement_id, source_system, canonical_tender_key, tender_key_kind,
              buyer_name_raw, buyer_name_norm, buyer_domain_norm, buyer_email_norm,
              region, title, status_code, status_name, publication_at, close_at,
              procurement_context, context_reason_code, confidence, line_item_count,
              constituent_source_ids_json, constituent_lines_fp, first_seen_at,
              last_seen_at, review_status
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                sig["procurement_id"],
                "chilecompra",
                sig["canonical_tender_key"],
                "codigo_externo",
                sig.get("buyer_name_raw"),
                sig.get("buyer_name_norm"),
                None,
                None,
                None,
                sig.get("title"),
                sig.get("status_code", "5"),
                sig.get("status_name", "Publicada"),
                sig.get("publication_at"),
                sig.get("close_at"),
                "historical_tender",
                "status_inactive_or_closed",
                "high",
                1,
                json.dumps([sig["procurement_id"]]),
                "lines_fp",
                None,
                None,
                "none",
            ),
        )
        conn.execute(
            """
            INSERT INTO commercial_procurement_account_resolution(
              resolution_id, procurement_id, resolution_status, account_id,
              link_route, confidence, reason_code, auto_link_allowed,
              review_status, candidate_account_ids_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                f"r_{sig['procurement_id']}",
                sig["procurement_id"],
                "unlinked",
                None,
                "F_no_match",
                "none",
                "no_match",
                0,
                "none",
                "[]",
            ),
        )
    rows = _read_semantic_rows_from_db(conn)
    digest = semantic_plan_digest(table_rows=rows)
    conn.execute(
        "INSERT OR REPLACE INTO commercial_procurement_build_meta(meta_key, meta_value) "
        "VALUES (?,?)",
        ("semantic_plan_digest", digest),
    )
    ensure_commercial_identity_tables(conn)
    conn.execute(
        "INSERT OR REPLACE INTO commercial_identity_build_meta(meta_key, meta_value) "
        "VALUES (?,?)",
        ("identity_fingerprint", "e" * 64),
    )
    for table_sql in (
        "CREATE TABLE IF NOT EXISTS emails (id INTEGER PRIMARY KEY, folder TEXT)",
        "CREATE TABLE IF NOT EXISTS contact_email_suppression (email TEXT)",
        "CREATE TABLE IF NOT EXISTS outreach_contact_state (email TEXT, state TEXT)",
        "CREATE TABLE IF NOT EXISTS contact_domain_suppression (domain TEXT)",
        "CREATE TABLE IF NOT EXISTS supplier_master (supplier_id TEXT)",
    ):
        conn.execute(table_sql)
    conn.commit()
    conn.close()


def _live_detail_payload() -> dict:
    return json.loads((LIVE / "ticket_detail_items_live_shape_v1.json").read_text())


def test_publication_date_reads_fechas_nested() -> None:
    licitacion = {
        "CodigoExterno": "1000-1-LE26",
        "Fechas": {"FechaPublicacion": "2026-07-03T10:54:26.077"},
    }
    assert _extract_publication_date(licitacion) == "2026-07-03T10:54:26.077"


def test_adapter_maps_multi_line_detail_cache(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    payload = _live_detail_payload()
    # Ensure Fechas publication present like production caches.
    listado = payload["Listado"][0]
    listado.setdefault("Fechas", {})
    listado["Fechas"]["FechaPublicacion"] = "2026-07-01T12:00:00"
    listado["FechaCierre"] = "2026-08-10T18:00:00-04:00"
    listado["CodigoEstado"] = "5"
    listado["Estado"] = "Publicada"
    (cache / "detail.json").write_text(
        json.dumps(payload, sort_keys=True), encoding="utf-8"
    )
    snap, rec = adapt_detail_cache_file(
        cache / "detail.json",
        acquired_at_utc="2026-08-01T10:00:00Z",
        materialized_at_utc="2026-08-01T10:00:01Z",
    )
    assert rec.status == "accepted"
    assert snap is not None
    assert len(snap.line_observations) >= 1
    assert snap.tender_observations[0].publication_timestamp_raw
    assert snap.tender_observations[0].buyer_display
    assert snap.completeness_status in {"complete", "partial"}


def test_adapter_rejects_missing_listado_and_accounts_all_files(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "bad.json").write_text(json.dumps({"Cantidad": 0}), encoding="utf-8")
    good = _live_detail_payload()
    (cache / "good.json").write_text(json.dumps(good, sort_keys=True), encoding="utf-8")
    staging = tmp_path / "staging"
    result = adapt_detail_cache_directory(
        cache,
        staging_dir=staging,
        acquired_at_utc="2026-08-01T10:00:00Z",
        materialized_at_utc="2026-08-01T10:00:01Z",
    )
    assert result.coverage["detail_cache_file_count"] == 2
    assert (
        result.coverage["accepted_snapshots"]
        + result.coverage["rejected_or_unresolved"]
        == 2
    )
    assert any(r.reason_code == "missing_listado" for r in result.records)


def test_duplicate_tender_code_rejected(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    payload = _live_detail_payload()
    (cache / "a.json").write_text(json.dumps(payload), encoding="utf-8")
    (cache / "b.json").write_text(json.dumps(payload), encoding="utf-8")
    result = adapt_detail_cache_directory(
        cache,
        staging_dir=tmp_path / "staging",
        acquired_at_utc="2026-08-01T10:00:00Z",
        materialized_at_utc="2026-08-01T10:00:01Z",
    )
    assert result.coverage["accepted_snapshots"] == 1
    assert any(r.reason_code == "duplicate_tender_code" for r in result.records)


@pytest.mark.parametrize(
    "lifecycle,currentness,relevance,expected",
    [
        (
            "active_open",
            "current_authoritative_snapshot",
            "strong_equipment_class",
            "current_opportunity",
        ),
        (
            "active_open",
            "current_authoritative_snapshot",
            "compatible_equipment_class",
            "current_opportunity",
        ),
        (
            "active_open",
            "current_authoritative_snapshot",
            "ambiguous",
            "needs_review",
        ),
        (
            "closed",
            "current_authoritative_snapshot",
            "strong_equipment_class",
            "historical_market_signal",
        ),
        (
            "awarded",
            "historical_pr4_only",
            "exact_catalog_product",
            "historical_market_signal",
        ),
        (
            "cancelled",
            "current_authoritative_snapshot",
            "compatible_equipment_class",
            "historical_market_signal",
        ),
        (
            "active_open",
            "current_authoritative_snapshot",
            "service_or_maintenance_only",
            "rejected",
        ),
        (
            "active_open",
            "current_authoritative_snapshot",
            "rental_or_comodato",
            "rejected",
        ),
        (
            "active_open",
            "current_authoritative_snapshot",
            "non_laboratory_false_positive",
            "rejected",
        ),
        (
            "status_conflict",
            "current_authoritative_snapshot",
            "strong_equipment_class",
            "needs_review",
        ),
        (
            "active_open",
            "stale_authoritative_snapshot",
            "strong_equipment_class",
            "needs_review",
        ),
        (
            "date_missing",
            "current_authoritative_snapshot",
            "strong_equipment_class",
            "needs_review",
        ),
    ],
)
def test_bucket_mapping_table(
    lifecycle: str, currentness: str, relevance: str, expected: str
) -> None:
    bucket, _reason = classify_commercial_bucket(
        candidate_source_kind="live_snapshot",
        lifecycle_class=lifecycle,
        currentness_class=currentness,
        relevance_class=relevance,
    )
    assert bucket == expected


def test_unknown_relevance_fails_closed_to_needs_review() -> None:
    bucket, reason = classify_commercial_bucket(
        candidate_source_kind="live_snapshot",
        lifecycle_class="active_open",
        currentness_class="current_authoritative_snapshot",
        relevance_class="not_a_real_class",
    )
    assert bucket == "needs_review"
    assert "unknown_relevance" in reason


def test_bucket_policy_document_lists_order() -> None:
    doc = bucket_policy_document()
    assert doc["evaluation_order"][0] == "rejected"
    assert "current_opportunity" in doc["buckets"]


def test_contact_unresolved_does_not_erase_current_opportunity_flag() -> None:
    bucket, _ = classify_commercial_bucket(
        candidate_source_kind="both",
        lifecycle_class="active_open",
        currentness_class="current_authoritative_snapshot",
        relevance_class="exact_catalog_product",
    )
    assert bucket == "current_opportunity"


def test_freshness_blocks_stale_feed() -> None:
    with pytest.raises(LiveFeedFreshnessError) as ei:
        evaluate_feed_freshness(
            as_of_utc="2026-08-05T19:00:30Z",
            refresh_state={"last_successful_refresh_at": "2026-08-01T00:00:00Z"},
            manifest=None,
            max_age_hours=36,
            allow_stale=False,
        )
    assert ei.value.code == "BLOCKED_STALE_LIVE_FEED"


def test_freshness_ok_when_recent() -> None:
    report = evaluate_feed_freshness(
        as_of_utc="2026-08-06T01:00:00Z",
        refresh_state={"last_successful_refresh_at": "2026-08-06T00:14:32Z"},
        manifest={"generated_at_utc": "2026-08-06T00:12:01+00:00", "fetched_summaries": 10},
        max_age_hours=36,
    )
    assert report["fresh_enough"] is True


def test_adapter_round_trip_through_pr5c_plane_b(tmp_path: Path) -> None:
    db = tmp_path / "pr4.sqlite"
    _seed_pr4_db(db, [])
    cache = tmp_path / "cache"
    cache.mkdir()
    payload = _live_detail_payload()
    listado = payload["Listado"][0]
    listado.setdefault("Fechas", {})["FechaPublicacion"] = "2026-07-01T12:00:00"
    # Keep close in the future relative to AS_OF for active_open possibility.
    listado["FechaCierre"] = "2026-08-10T18:00:00-04:00"
    listado["CodigoEstado"] = "5"
    listado["Estado"] = "Publicada"
    (cache / "t.json").write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    adapted = adapt_detail_cache_directory(
        cache,
        staging_dir=tmp_path / "staging",
        acquired_at_utc="2026-08-01T10:00:00Z",
        materialized_at_utc="2026-08-01T10:00:01Z",
    )
    assert adapted.snapshot_paths
    plan = build_candidate_plan(
        sqlite_path=db,
        acquisition_snapshot_paths=list(adapted.snapshot_paths),
        as_of_utc=AS_OF,
        freshness_threshold_hours=48,
        run_context="local_fixture",
    )
    kinds = {t.candidate_source_kind for t in plan.coalesced_tenders}
    assert "live_snapshot" in kinds or "both" in kinds
    live = [
        t
        for t in plan.coalesced_tenders
        if t.candidate_source_kind in {"live_snapshot", "both"}
    ]
    assert live


def test_live_plus_pr4_overlap_gives_both(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    payload = _live_detail_payload()
    listado = payload["Listado"][0]
    code = listado["CodigoExterno"]
    listado.setdefault("Fechas", {})["FechaPublicacion"] = "2026-07-01T12:00:00"
    listado["FechaCierre"] = "2026-08-10T18:00:00-04:00"
    listado["CodigoEstado"] = "5"
    listado["Estado"] = "Publicada"
    (cache / "t.json").write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    adapted = adapt_detail_cache_directory(
        cache,
        staging_dir=tmp_path / "staging",
        acquired_at_utc="2026-08-01T10:00:00Z",
        materialized_at_utc="2026-08-01T10:00:01Z",
    )
    db = tmp_path / "pr4.sqlite"
    _seed_pr4_db(
        db,
        [
            {
                "procurement_id": "p_overlap",
                "canonical_tender_key": code,
                "buyer_name_raw": "Overlap Buyer",
                "title": "Overlap",
                "publication_at": "2026-07-01",
                "close_at": "2026-08-10",
            }
        ],
    )
    plan = build_candidate_plan(
        sqlite_path=db,
        acquisition_snapshot_paths=list(adapted.snapshot_paths),
        as_of_utc=AS_OF,
        freshness_threshold_hours=48,
        run_context="local_fixture",
    )
    assert any(t.candidate_source_kind == "both" for t in plan.coalesced_tenders)


def test_end_to_end_bridge_review_bundle(tmp_path: Path) -> None:
    real_root = Path(__file__).resolve().parents[1]
    out = (
        real_root
        / "reports"
        / "out"
        / "active"
        / "current"
        / f"_pr5b2_test_bundle_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    )
    db = tmp_path / "pr4.sqlite"
    _seed_pr4_db(db, [])
    cache = tmp_path / "cache"
    cache.mkdir()
    payload = _live_detail_payload()
    listado = payload["Listado"][0]
    listado.setdefault("Fechas", {})["FechaPublicacion"] = "2026-07-01T12:00:00"
    listado["FechaCierre"] = "2026-08-10T18:00:00-04:00"
    listado["CodigoEstado"] = "5"
    listado["Estado"] = "Publicada"
    # Title with equipment-ish wording for relevance classifier.
    listado["Nombre"] = "Adquisicion de centrifuga de laboratorio"
    listado["Descripcion"] = "Equipo centrifuga de laboratorio clinica"
    if isinstance(listado.get("Items"), dict) and listado["Items"].get("Listado"):
        listado["Items"]["Listado"][0]["NombreProducto"] = "Centrifuga de laboratorio"
    (cache / "t.json").write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    result = build_live_feed_review(
        sqlite_path=db,
        detail_cache_dir=cache,
        as_of_utc=AS_OF,
        out_dir=out,
        run_context="local_fixture",
        freshness_threshold_hours=48,
        equipment_manifest_path=None,
        refresh_state_path=None,
        allow_stale_feed=True,
        max_feed_age_hours=36,
    )
    written = result["written"]
    summary = json.loads(Path(written["summary.json"]).read_text(encoding="utf-8"))
    assert summary["not_persisted"] is True
    assert summary["actionable_lead_count"] == 0
    assert summary["outreach_authorization_count"] == 0
    assert summary["live_backed_review_population"] >= 1
    assert sum(summary["by_commercial_bucket"].values()) == summary[
        "live_backed_review_population"
    ]
    packet = json.loads(
        Path(written["operator_review_packet.json"]).read_text(encoding="utf-8")
    )
    assert packet["rows"]
    for row in packet["rows"]:
        assert row["actionable_lead"] is False
        assert row["outreach_authorization"] is False
        assert row["candidate_source_kind"] in {"live_snapshot", "both"}
    # Cleanup test bundle under reports/out
    import shutil

    shutil.rmtree(out, ignore_errors=True)


def test_reconcile_bucket_counts_exact() -> None:
    rows = [
        {"commercial_bucket": "current_opportunity"},
        {"commercial_bucket": "needs_review"},
        {"commercial_bucket": "rejected"},
        {"commercial_bucket": "historical_market_signal"},
    ]
    recon = reconcile_bucket_counts(rows)
    assert recon["ok"] is True
    assert recon["live_backed_review_population"] == 4
