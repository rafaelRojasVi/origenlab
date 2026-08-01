"""PR5C coalescence / lifecycle planner tests (offline, no network)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from origenlab_email_pipeline.commercial_procurement.constants import (
    BUILD_CONTRACT,
    SCHEMA_VERSION,
)
from origenlab_email_pipeline.commercial_procurement.schema import (
    ensure_commercial_procurement_tables,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.identity import (
    is_mercado_publico_codigo_shape,
    normalize_mercado_publico_codigo,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.snapshot import (
    build_acquisition_snapshot,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.coalescence import (
    coalesce_evidence_refs,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.fingerprint import (
    candidate_build_plan_fingerprint,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.lifecycle import (
    apply_lifecycle,
    classify_lifecycle,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    CoalescedProcurementTender,
    ProcurementEvidenceRef,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.normalize import (
    accept_canonical_tender_key,
    parse_as_of_utc,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.plane_b_acquisition import (
    AcquisitionPlaneError,
    materialize_acquisition_snapshot,
    snapshot_to_evidence,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.planner import (
    build_candidate_plan,
    write_plan_outputs,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.redaction import (
    assert_no_pii_leaks,
    redact_coalesced_tender,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.walkthrough import (
    build_walkthrough_bundle,
    write_walkthrough,
)

FIXTURES = Path(__file__).parent / "fixtures"
LIVE = FIXTURES / "commercial_procurement_acquisition_live_contract"
ACQ = FIXTURES / "commercial_procurement_acquisition"
AS_OF = "2026-08-01T12:00:00Z"


def _seed_pr4_db(path: Path, signals: list[dict]) -> None:
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
    for sig in signals:
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
                sig.get("tender_key_kind", "codigo_externo"),
                sig.get("buyer_name_raw"),
                sig.get("buyer_name_norm"),
                sig.get("buyer_domain_norm"),
                None,
                None,
                sig.get("title"),
                sig.get("status_code"),
                sig.get("status_name"),
                sig.get("publication_at"),
                sig.get("close_at"),
                sig.get("procurement_context", "historical_tender"),
                sig.get("context_reason_code", "status_inactive_or_closed"),
                "high",
                1,
                json.dumps([sig.get("source_record_id", sig["procurement_id"])]),
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
    conn.commit()
    conn.close()


def _ticket_detail_snapshot(
    *,
    acquired_at_utc: str = "2026-08-01T10:00:00Z",
    fixture: Path | None = None,
    tender_code: str = "3544-1-LE26",
) -> dict:
    path = fixture or (LIVE / "ticket_detail_items_live_shape_v1.json")
    payload = json.loads(path.read_text())
    snap = build_acquisition_snapshot(
        source_kind="ticket_detail",
        payload=payload,
        fixture_origin="live_response_sanitized",
        acquired_at_utc=acquired_at_utc,
        tender_code=tender_code,
    )
    return snap.to_dict()


def _write_snap(tmp_path: Path, name: str, payload: dict) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _ref(
    *,
    rid: str,
    plane: str,
    key: str,
    rank: str,
    status_code: str | None = None,
    status_name: str | None = None,
    close: str | None = None,
    pub: str | None = None,
    buyer: str | None = None,
    buyer_id: str | None = None,
    acquired: str | None = None,
    obs: str | None = None,
    snap: str | None = None,
    pr4_id: str | None = None,
) -> ProcurementEvidenceRef:
    return ProcurementEvidenceRef(
        evidence_ref_id=rid,
        evidence_plane=plane,
        source_kind="pr4" if plane == "pr4" else "mercado_publico_ticket_api",
        endpoint_kind="pr4_persisted_signal" if plane == "pr4" else "ticket_licitacion_detail",
        source_record_id=rid,
        canonical_tender_key=key,
        snapshot_id=snap,
        observation_id=obs,
        acquired_at_utc=acquired,
        source_status_code=status_code,
        source_status_name=status_name,
        source_status_value=status_name,
        publication_timestamp_raw=pub,
        close_timestamp_raw=close,
        buyer_display_raw=buyer,
        buyer_source_id=buyer_id,
        title_raw=None,
        source_payload_digest="x",
        source_fingerprint="y",
        normalized_semantic_digest="z",
        field_provenance={},
        reason_codes=(),
        source_rank_class=rank,
        has_status=bool(status_code or status_name),
        has_close=bool(close),
        has_publication=bool(pub),
        has_buyer_display=bool(buyer),
        has_buyer_source_id=bool(buyer_id),
        has_title=False,
        pr4_procurement_id=pr4_id,
    )


# --- Canonical identity ---


def test_valid_mercado_publico_key_accepted() -> None:
    key, reject = accept_canonical_tender_key(
        candidate="9999-1-LE26",
        candidate_kind="mercado_publico_codigo_externo",
    )
    assert reject is None
    assert key == normalize_mercado_publico_codigo("9999-1-LE26")
    assert is_mercado_publico_codigo_shape(key)


def test_ocid_only_rejected_to_unresolved() -> None:
    key, reject = accept_canonical_tender_key(
        candidate=None,
        candidate_kind="none",
    )
    assert key is None
    assert reject == "live_canonical_candidate_missing"


def test_malformed_candidate_rejected() -> None:
    key, reject = accept_canonical_tender_key(
        candidate="not-a-code",
        candidate_kind="mercado_publico_codigo_externo",
    )
    assert key is None
    assert reject == "live_canonical_candidate_malformed"


def test_source_native_never_canonical() -> None:
    key, reject = accept_canonical_tender_key(
        candidate="ticket_api:codigo_externo:9999-1-le26",
        candidate_kind="none",
    )
    assert key is None
    assert reject in {
        "live_canonical_candidate_missing",
        "unsupported_candidate_kind",
    }


def test_grouping_independent_of_input_order() -> None:
    a = _ref(
        rid="e2",
        plane="pr4",
        key="1000-1-le26",
        rank="pr4",
        status_code="6",
        status_name="Cerrada",
        close="2026-01-01",
        pr4_id="p2",
    )
    b = _ref(
        rid="e1",
        plane="pr4",
        key="1000-1-le26",
        rank="pr4",
        status_code="6",
        status_name="Cerrada",
        close="2026-01-01",
        pr4_id="p1",
    )
    t1, _ = coalesce_evidence_refs([a, b])
    t2, _ = coalesce_evidence_refs([b, a])
    assert len(t1) == 1 and len(t2) == 1
    assert t1[0].coalesced_tender_id == t2[0].coalesced_tender_id


# --- Plane adapters ---


def test_materialize_snapshot_and_reject_fingerprint_mismatch(tmp_path: Path) -> None:
    snap = _ticket_detail_snapshot()
    materialize_acquisition_snapshot(snap)
    bad = dict(snap)
    bad["source_fingerprint"] = "0" * 64
    with pytest.raises(AcquisitionPlaneError, match="source_fingerprint"):
        materialize_acquisition_snapshot(bad)


def test_lista_index_without_mp_code_is_unresolved() -> None:
    payload = json.loads((LIVE / "ocds_range_live_shape_v1.json").read_text())
    snap = build_acquisition_snapshot(
        source_kind="ocds",
        payload=payload,
        fixture_origin="live_response_sanitized",
        year=2026,
        month=7,
        range_start=1,
        range_end=1,
        acquired_at_utc="2026-08-01T10:00:00Z",
    )
    refs, unresolved = snapshot_to_evidence(snap)
    assert refs == []
    assert unresolved
    assert unresolved[0].unresolved_reason in {
        "ocds_ocid_only_unresolved",
        "source_native_identity_not_canonical",
        "live_canonical_candidate_missing",
    }


def test_ocds_release_fixture_may_yield_canonical_or_unresolved() -> None:
    payload = json.loads((ACQ / "ocds_ocid_only.json").read_text())
    snap = build_acquisition_snapshot(
        source_kind="ocds",
        payload=payload,
        fixture_origin="synthetic_official_shape",
        acquired_at_utc="2026-08-01T10:00:00Z",
    )
    _refs, unresolved = snapshot_to_evidence(snap)
    assert unresolved


# --- Coalescence ---


def test_pr4_only_and_live_only_and_exact_agreement() -> None:
    pr4 = _ref(
        rid="p",
        plane="pr4",
        key="2000-1-le26",
        rank="pr4",
        status_code="5",
        status_name="Publicada",
        close="2026-08-10T18:00:00-04:00",
        buyer="Org A",
        pr4_id="proc1",
    )
    live = _ref(
        rid="l",
        plane="acquisition",
        key="2000-1-le26",
        rank="ticket_detail",
        status_code="5",
        status_name="Publicada",
        close="2026-08-10T18:00:00-04:00",
        buyer="Org A",
        acquired="2026-08-01T10:00:00Z",
        obs="obs1",
        snap="snap1",
    )
    t_pr4, _ = coalesce_evidence_refs([pr4])
    assert t_pr4[0].coalescence_status == "pr4_only"
    t_live, _ = coalesce_evidence_refs([live])
    assert t_live[0].coalescence_status == "live_only"
    t_both, _ = coalesce_evidence_refs([pr4, live])
    assert t_both[0].candidate_source_kind == "both"
    assert t_both[0].coalescence_status in {"exact_agreement", "live_source_newer"}


def test_status_and_date_and_buyer_conflicts() -> None:
    a = _ref(
        rid="a",
        plane="acquisition",
        key="3000-1-le26",
        rank="ticket_detail",
        status_code="5",
        status_name="Publicada",
        close="2026-08-10T12:00:00Z",
        buyer="Buyer A",
        buyer_id="a.cl",
        acquired="2026-08-01T10:00:00Z",
        obs="o1",
        snap="s1",
    )
    b = _ref(
        rid="b",
        plane="acquisition",
        key="3000-1-le26",
        rank="ticket_summary",
        status_code="8",
        status_name="Adjudicada",
        close="2026-08-11T12:00:00Z",
        buyer="Buyer B",
        buyer_id="b.cl",
        acquired="2026-08-01T10:00:00Z",
        obs="o2",
        snap="s2",
    )
    tenders, conflicts = coalesce_evidence_refs([a, b])
    kinds = {c.conflict_kind for c in conflicts}
    assert "status_conflict" in kinds
    assert "date_conflict" in kinds
    assert "buyer_identity_conflict" in kinds
    assert tenders[0].coalescence_status in {
        "status_conflict",
        "multiple_live_sources_conflict",
    }


# --- Freshness / lifecycle ---


def test_stale_open_not_active_and_current_open_active() -> None:
    as_of = parse_as_of_utc(AS_OF)
    tender = CoalescedProcurementTender(
        coalesced_tender_id="t1",
        canonical_tender_key="4000-1-le26",
        tender_key_kind="mercado_publico_codigo_externo",
        candidate_source_kind="live_snapshot",
        pr4_procurement_id=None,
        acquisition_snapshot_ids=("s",),
        acquisition_observation_ids=("o",),
        coalescence_status="live_only",
        source_precedence_reason="x",
        currentness_class="pending",
        lifecycle_class="pending",
        closing_soon_bucket="not_applicable",
        publication_timestamp_selected="2026-07-01T00:00:00-04:00",
        close_timestamp_selected="2026-08-05T18:00:00-04:00",
        status_code_selected="5",
        status_name_selected="Publicada",
        buyer_display_selected=None,
        buyer_source_id_selected=None,
        title_selected=None,
        selected_field_provenance={},
        lifecycle_reason_codes=(),
        evidence_ref_ids=("e1",),
        conflict_ids=(),
    )
    stale_ref = _ref(
        rid="e1",
        plane="acquisition",
        key="4000-1-le26",
        rank="ticket_detail",
        status_code="5",
        status_name="Publicada",
        close="2026-08-05T18:00:00-04:00",
        acquired="2026-07-01T10:00:00Z",
        obs="o",
        snap="s",
    )
    out = apply_lifecycle(
        [tender],
        refs_by_id={"e1": stale_ref},
        as_of_utc=as_of,
        freshness_threshold_hours=48,
    )
    assert out[0].currentness_class == "stale_authoritative_snapshot"
    assert out[0].lifecycle_class != "active_open"

    fresh_ref = _ref(
        rid="e1",
        plane="acquisition",
        key="4000-1-le26",
        rank="ticket_detail",
        status_code="5",
        status_name="Publicada",
        close="2026-08-05T18:00:00-04:00",
        acquired="2026-08-01T10:00:00Z",
        obs="o",
        snap="s",
    )
    out2 = apply_lifecycle(
        [tender],
        refs_by_id={"e1": fresh_ref},
        as_of_utc=as_of,
        freshness_threshold_hours=48,
    )
    assert out2[0].currentness_class == "current_authoritative_snapshot"
    assert out2[0].lifecycle_class == "active_open"
    assert out2[0].closing_soon_bucket in {"lt_24h", "d1_to_d3", "d4_to_d7", "gt_7d"}


def test_close_exactly_at_as_of_is_closed() -> None:
    as_of = parse_as_of_utc("2026-08-01T12:00:00Z")
    life, bucket, _ = classify_lifecycle(
        tender=CoalescedProcurementTender(
            coalesced_tender_id="t",
            canonical_tender_key="k",
            tender_key_kind="mercado_publico_codigo_externo",
            candidate_source_kind="live_snapshot",
            pr4_procurement_id=None,
            acquisition_snapshot_ids=(),
            acquisition_observation_ids=(),
            coalescence_status="live_only",
            source_precedence_reason="x",
            currentness_class="current_authoritative_snapshot",
            lifecycle_class="pending",
            closing_soon_bucket="not_applicable",
            publication_timestamp_selected=None,
            close_timestamp_selected="2026-08-01T12:00:00Z",
            status_code_selected="5",
            status_name_selected="Publicada",
            buyer_display_selected=None,
            buyer_source_id_selected=None,
            title_selected=None,
            selected_field_provenance={},
            lifecycle_reason_codes=(),
            evidence_ref_ids=(),
            conflict_ids=(),
        ),
        currentness_class="current_authoritative_snapshot",
        as_of_utc=as_of,
        has_status_conflict=False,
    )
    assert life == "closed"
    assert bucket == "not_applicable"


def test_awarded_and_cancelled_mappings() -> None:
    as_of = parse_as_of_utc(AS_OF)
    for code, name, expected, reason_substr in [
        ("8", "Adjudicada", "awarded", "awarded"),
        ("7", "Desierta", "cancelled", "desierta"),
        ("18", "Revocada", "cancelled", "revocada"),
        ("19", "Suspendida", "cancelled", "suspendida"),
        ("6", "Cerrada", "closed", "closed"),
    ]:
        life, _, reasons = classify_lifecycle(
            tender=CoalescedProcurementTender(
                coalesced_tender_id="t",
                canonical_tender_key="k",
                tender_key_kind="mercado_publico_codigo_externo",
                candidate_source_kind="pr4",
                pr4_procurement_id="p",
                acquisition_snapshot_ids=(),
                acquisition_observation_ids=(),
                coalescence_status="pr4_only",
                source_precedence_reason="x",
                currentness_class="historical_pr4_only",
                lifecycle_class="pending",
                closing_soon_bucket="not_applicable",
                publication_timestamp_selected=None,
                close_timestamp_selected=None,
                status_code_selected=code,
                status_name_selected=name,
                buyer_display_selected=None,
                buyer_source_id_selected=None,
                title_selected=None,
                selected_field_provenance={},
                lifecycle_reason_codes=(),
                evidence_ref_ids=(),
                conflict_ids=(),
            ),
            currentness_class="historical_pr4_only",
            as_of_utc=as_of,
            has_status_conflict=False,
        )
        assert life == expected
        assert any(reason_substr in r for r in reasons)


def test_naive_as_of_rejected() -> None:
    with pytest.raises(ValueError, match="malformed timezone"):
        parse_as_of_utc("2026-08-01T12:00:00")


# --- End-to-end planner ---


def test_end_to_end_plan_reconciliation_and_fingerprints(tmp_path: Path) -> None:
    db = tmp_path / "pr4.sqlite"
    detail = _ticket_detail_snapshot()
    # Discover canonical key from snapshot for synthetic overlap.
    snap_obj = materialize_acquisition_snapshot(detail)
    live_key = None
    for obs in snap_obj.source_observations:
        key, _ = accept_canonical_tender_key(
            candidate=obs.canonical_tender_key_candidate,
            candidate_kind=obs.canonical_candidate_kind,
        )
        if key:
            live_key = key
            break
    assert live_key

    signals = [
        {
            "procurement_id": "p_hist_001",
            "canonical_tender_key": "2581-11-LE26",
            "status_code": "8",
            "status_name": "Adjudicada",
            "publication_at": "2026-02-20",
            "close_at": "2026-02-26",
            "buyer_name_raw": "I MUNICIPALIDAD DE CONCHALI",
            "title": "Formularios QR",
            "procurement_context": "historical_tender",
        },
        {
            "procurement_id": "p_overlap_001",
            "canonical_tender_key": live_key,
            "status_code": "5",
            "status_name": "Publicada",
            "publication_at": "2026-07-01",
            "close_at": "2026-08-10",
            "buyer_name_raw": "Synthetic Overlap Buyer",
            "title": "Overlap",
            "procurement_context": "historical_tender",
        },
        {
            "procurement_id": "p_conflict_001",
            "canonical_tender_key": "5555-1-LE26",
            "status_code": "5",
            "status_name": "Publicada",
            "publication_at": "2026-07-01",
            "close_at": "2026-08-20",
            "buyer_name_raw": "Conflict Buyer PR4",
            "title": "Conflict",
            "procurement_context": "historical_tender",
        },
    ]
    _seed_pr4_db(db, signals)

    # Second live snapshot with conflicting status on 5555-1-LE26 via synthetic
    # mutation through materialize path: build from detail then note — instead
    # create a second evidence by duplicating detail observation key? Use
    # coalesce unit already covered. For e2e, add lista unresolved snapshot.
    snap_path = _write_snap(tmp_path, "detail.json", detail)
    lista = build_acquisition_snapshot(
        source_kind="ocds",
        payload=json.loads((LIVE / "ocds_range_live_shape_v1.json").read_text()),
        fixture_origin="live_response_sanitized",
        year=2026,
        month=7,
        range_start=1,
        range_end=1,
        acquired_at_utc="2026-08-01T10:00:00Z",
    ).to_dict()
    lista_path = _write_snap(tmp_path, "lista.json", lista)

    # Conflict synthetic: second ticket snapshot is hard; inject via second
    # PR4+live already exact. Build conflict by adding a live-only conflicting
    # pair in coalesce tests — e2e checks unresolved + overlap.

    result = build_candidate_plan(
        sqlite_path=db,
        acquisition_snapshot_paths=[snap_path, lista_path],
        as_of_utc=AS_OF,
        freshness_threshold_hours=48,
        run_context="local_fixture",
    )
    assert result.aggregate_reconciliation["ok"] is True
    kinds = {t.candidate_source_kind for t in result.coalesced_tenders}
    assert "pr4" in kinds
    assert "both" in kinds or "live_snapshot" in kinds
    assert any(u.unresolved_reason for u in result.unresolved)

    # Fingerprint stability under reordering of snapshot paths.
    result2 = build_candidate_plan(
        sqlite_path=db,
        acquisition_snapshot_paths=[lista_path, snap_path],
        as_of_utc=AS_OF,
        freshness_threshold_hours=48,
        run_context="local_fixture",
    )
    assert result.input_source_fingerprint == result2.input_source_fingerprint
    assert result.semantic_digest == result2.semantic_digest

    build_changed = candidate_build_plan_fingerprint(
        input_source_fingerprint=result.input_source_fingerprint,
        as_of_utc="2026-08-02T12:00:00Z",
        freshness_threshold_hours=48,
    )
    assert build_changed != result.build_plan_fingerprint
    fresh_changed = candidate_build_plan_fingerprint(
        input_source_fingerprint=result.input_source_fingerprint,
        as_of_utc=result.as_of_utc,
        freshness_threshold_hours=24,
    )
    assert fresh_changed != result.build_plan_fingerprint

    out = tmp_path / "out"
    write_plan_outputs(result, out)
    bundle = build_walkthrough_bundle(
        result,
        case_hints={"case_c_synthetic": True, "case_d_synthetic": True},
    )
    write_walkthrough(bundle, out)
    assert_no_pii_leaks(json.dumps(bundle))
    red = redact_coalesced_tender(result.coalesced_tenders[0].to_dict())
    assert "@" not in json.dumps(red)
    assert (out / "DATA_WALKTHROUGH.md").read_text().strip()


def test_cli_rejects_forbidden_flags(tmp_path: Path) -> None:
    import runpy
    import sys

    script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "commercial"
        / "build_commercial_procurement_candidate_plan.py"
    )
    old = sys.argv[:]
    try:
        sys.argv = [str(script), "--apply", "--sqlite-path", str(tmp_path / "x")]
        with pytest.raises(SystemExit):
            runpy.run_path(str(script), run_name="__main__")
    finally:
        sys.argv = old
