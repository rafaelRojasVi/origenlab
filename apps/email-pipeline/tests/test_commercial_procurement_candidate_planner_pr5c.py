"""PR5C coalescence / lifecycle planner tests (offline, no network)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

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
    accept_pr4_signal_identity,
    coalesced_tender_id,
    parse_as_of_utc,
    parse_tender_timestamp_raw,
    status_internally_inconsistent,
    utc_iso,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.output_safety import (
    ReportOutputError,
    require_reports_out_dir,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.plane_a_pr4 import (
    load_pr4_plane,
    pr4_signals_to_evidence,
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
        "semantic_plan_digest": "c" * 64,  # recomputed below
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
    # Authoritative PR4 digest — do not invent a second algorithm.
    rows = _read_semantic_rows_from_db(conn)
    digest = semantic_plan_digest(table_rows=rows)
    conn.execute(
        "INSERT OR REPLACE INTO commercial_procurement_build_meta(meta_key, meta_value) "
        "VALUES (?,?)",
        ("semantic_plan_digest", digest),
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


def _reports_out(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "email-pipeline"
    out = root / "reports" / "out" / "pr5c_test"
    out.mkdir(parents=True)
    return root, out


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
    tender_key_kind: str | None = None,
    cross_source: bool | None = None,
    payload_digest: str = "x",
) -> ProcurementEvidenceRef:
    pub_dt, pub_err = parse_tender_timestamp_raw(pub)
    close_dt, close_err = parse_tender_timestamp_raw(close)
    kind = tender_key_kind
    if kind is None:
        kind = (
            "codigo_externo"
            if plane == "pr4"
            else "mercado_publico_codigo_externo"
        )
    if cross_source is None:
        cross_source = is_mercado_publico_codigo_shape(key)
    return ProcurementEvidenceRef(
        evidence_ref_id=rid,
        evidence_plane=plane,
        source_kind="pr4" if plane == "pr4" else "mercado_publico_ticket_api",
        endpoint_kind="pr4_persisted_signal" if plane == "pr4" else "ticket_licitacion_detail",
        source_record_id=rid,
        canonical_tender_key=key,
        tender_key_kind=kind,
        cross_source_join_eligible=cross_source,
        snapshot_id=snap,
        observation_id=obs,
        acquired_at_utc=acquired,
        source_status_code=status_code,
        source_status_name=status_name,
        source_status_value=status_name,
        source_status_system="chilecompra",
        publication_timestamp_raw=pub,
        close_timestamp_raw=close,
        publication_timestamp_utc=utc_iso(pub_dt),
        close_timestamp_utc=utc_iso(close_dt),
        timestamp_parse_reasons=tuple(r for r in (pub_err, close_err) if r),
        buyer_display_raw=buyer,
        buyer_source_id=buyer_id,
        title_raw=None,
        source_payload_digest=payload_digest,
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


def _tender_shell(**kwargs) -> CoalescedProcurementTender:
    base = dict(
        coalesced_tender_id="t1",
        canonical_tender_key="4000-1-le26",
        tender_key_kind="mercado_publico_codigo_externo",
        candidate_source_kind="live_snapshot",
        pr4_procurement_id=None,
        pr4_procurement_ids=(),
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
        status_value_selected="Publicada",
        source_status_system_selected="chilecompra",
        buyer_display_selected=None,
        buyer_source_id_selected=None,
        title_selected=None,
        selected_field_provenance={},
        lifecycle_status_evidence_ref_id=None,
        lifecycle_close_evidence_ref_id=None,
        lifecycle_publication_evidence_ref_id=None,
        lifecycle_evidence_currentness_class=None,
        lifecycle_reason_codes=(),
        evidence_ref_ids=("e1",),
        conflict_ids=(),
    )
    base.update(kwargs)
    return CoalescedProcurementTender(**base)


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


@pytest.mark.parametrize(
    "kind",
    ["codigo_externo", "codigo_licitacion", "numero_adquisicion"],
)
def test_pr4_verified_key_kinds_accepted_without_mp_regex(kind: str) -> None:
    # Non-MP-shape suffix (3 digits) — Plane A still accepts.
    key, out_kind, reason, cross = accept_pr4_signal_identity(
        raw_key="1000-1-LE123",
        tender_key_kind=kind,
    )
    assert reason is None
    assert key == "1000-1-le123"
    assert out_kind == kind
    assert cross is False


def test_corrupt_pr4_key_typed_unresolved(tmp_path: Path) -> None:
    db = tmp_path / "pr4.sqlite"
    _seed_pr4_db(
        db,
        [
            {
                "procurement_id": "p_missing",
                "canonical_tender_key": "   ",
                "tender_key_kind": "codigo_externo",
                "status_code": "6",
                "status_name": "Cerrada",
            },
        ],
    )
    pr4 = load_pr4_plane(db)
    refs, unresolved = pr4_signals_to_evidence(pr4)
    assert refs == []
    assert unresolved[0].unresolved_reason == "pr4_canonical_key_missing"

    key, kind, reason, cross = accept_pr4_signal_identity(
        raw_key="1000-1-LE26",
        tender_key_kind="made_up_kind",
    )
    assert key is None and cross is False
    assert reason == "pr4_tender_key_kind_unsupported"
    assert kind == "made_up_kind"


def test_pr4_only_preserves_non_mp_kind() -> None:
    ref = _ref(
        rid="p",
        plane="pr4",
        key="1000-1-le123",
        rank="pr4",
        status_code="6",
        status_name="Cerrada",
        tender_key_kind="codigo_licitacion",
        cross_source=False,
        pr4_id="p1",
    )
    tenders, _ = coalesce_evidence_refs([ref])
    assert tenders[0].tender_key_kind == "codigo_licitacion"
    assert tenders[0].candidate_source_kind == "pr4"


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


# --- Stable coalesced ID ---


def test_stable_id_pr4_only_to_both() -> None:
    key = "2000-1-le26"
    pr4 = _ref(
        rid="p",
        plane="pr4",
        key=key,
        rank="pr4",
        status_code="5",
        status_name="Publicada",
        close="2026-08-10T18:00:00-04:00",
        pr4_id="proc1",
        tender_key_kind="codigo_externo",
        cross_source=True,
    )
    live = _ref(
        rid="l",
        plane="acquisition",
        key=key,
        rank="ticket_detail",
        status_code="5",
        status_name="Publicada",
        close="2026-08-10T18:00:00-04:00",
        acquired="2026-08-01T10:00:00Z",
        obs="obs1",
        snap="snap1",
    )
    t_pr4, _ = coalesce_evidence_refs([pr4])
    t_both, _ = coalesce_evidence_refs([pr4, live])
    assert t_pr4[0].coalesced_tender_id == t_both[0].coalesced_tender_id


def test_stable_id_second_snapshot_and_reorder() -> None:
    key = "2100-1-le26"
    a = _ref(
        rid="a",
        plane="acquisition",
        key=key,
        rank="ticket_detail",
        status_code="5",
        status_name="Publicada",
        close="2026-08-10T12:00:00Z",
        acquired="2026-08-01T10:00:00Z",
        obs="o1",
        snap="s1",
        payload_digest="d1",
    )
    b = _ref(
        rid="b",
        plane="acquisition",
        key=key,
        rank="ticket_detail",
        status_code="5",
        status_name="Publicada",
        close="2026-08-10T12:00:00Z",
        acquired="2026-08-01T11:00:00Z",
        obs="o2",
        snap="s2",
        payload_digest="d2",
    )
    t1, _ = coalesce_evidence_refs([a])
    t2, _ = coalesce_evidence_refs([a, b])
    t3, _ = coalesce_evidence_refs([b, a])
    assert t1[0].coalesced_tender_id == t2[0].coalesced_tender_id == t3[0].coalesced_tender_id


def test_stable_id_changes_with_canonical_key() -> None:
    a = coalesced_tender_id(
        canonical_tender_key="1000-1-le26",
        tender_key_kind="mercado_publico_codigo_externo",
    )
    b = coalesced_tender_id(
        canonical_tender_key="1000-2-le26",
        tender_key_kind="mercado_publico_codigo_externo",
    )
    assert a != b


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


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda s: s.__setitem__("parser_version", "nope"), "parser_version"),
        (lambda s: s.__setitem__("contract_version", "nope"), "contract_version"),
        (lambda s: s.__setitem__("snapshot_id", "procurement_snapshot_id_deadbeef"), "snapshot_id"),
        (
            lambda s: s["source_observations"].__setitem__(
                0,
                {**s["source_observations"][0], "observation_id": "wrong"},
            ),
            "observation_id",
        ),
        (
            lambda s: s["pages"].__setitem__(
                0, {**s["pages"][0], "page_id": "acquisition_page_id_deadbeef"}
            ),
            "page_id",
        ),
        (
            lambda s: s["tender_observations"].__setitem__(
                0,
                {
                    **s["tender_observations"][0],
                    "tender_observation_id": "procurement_tender_observation_id_dead",
                },
            ),
            "tender_observation_id",
        ),
        (
            lambda s: s["line_observations"].__setitem__(
                0,
                {
                    **s["line_observations"][0],
                    "line_observation_id": "procurement_line_observation_id_dead",
                },
            ),
            "line_observation_id",
        ),
        (
            lambda s: s["source_observations"].__setitem__(
                0, {**s["source_observations"][0], "page_id": "missing_page"}
            ),
            "page_id missing",
        ),
        (
            lambda s: s.__setitem__(
                "source_observations",
                s["source_observations"] + [s["source_observations"][0]],
            ),
            "duplicate source observation",
        ),
    ],
)
def test_snapshot_linkage_rejections(mutate, match) -> None:
    snap = _ticket_detail_snapshot()
    mutate(snap)
    with pytest.raises(AcquisitionPlaneError, match=match):
        materialize_acquisition_snapshot(snap)


def test_duplicate_snapshot_id_divergent_rejected(tmp_path: Path) -> None:
    from dataclasses import replace

    from origenlab_email_pipeline.commercial_procurement_candidate_planner.plane_b_acquisition import (
        dedupe_snapshots,
    )

    a = materialize_acquisition_snapshot(_ticket_detail_snapshot())
    b = materialize_acquisition_snapshot(
        _ticket_detail_snapshot(acquired_at_utc="2026-08-01T11:00:00Z")
    )
    b2 = replace(
        b,
        snapshot_id=a.snapshot_id,
        source_fingerprint=("f" * 64),
        normalized_semantic_digest=("e" * 64),
    )
    with pytest.raises(AcquisitionPlaneError, match="duplicate snapshot_id"):
        dedupe_snapshots([a, b2])
    # Exact duplicates are kept once.
    same = replace(a)
    out = dedupe_snapshots([a, same])
    assert len(out) == 1


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


def test_atomic_status_selection_same_evidence() -> None:
    ref = _ref(
        rid="a",
        plane="acquisition",
        key="3100-1-le26",
        rank="ticket_detail",
        status_code="5",
        status_name="Publicada",
        close="2026-08-10T12:00:00Z",
        acquired="2026-08-01T10:00:00Z",
        obs="o1",
        snap="s1",
    )
    tenders, _ = coalesce_evidence_refs([ref])
    prov = tenders[0].selected_field_provenance
    assert prov.get("status") == "a"
    assert prov.get("status_code") == "a"
    assert prov.get("status_name") == "a"


def test_inconsistent_status_code_name_conflict() -> None:
    assert status_internally_inconsistent("8", "Publicada")
    assert status_internally_inconsistent("5", "Adjudicada")
    assert not status_internally_inconsistent("5", "Publicada")
    assert not status_internally_inconsistent(
        "7", "Desierta (o art. 3 ó 9 Ley 19.886)"
    )
    ref = _ref(
        rid="a",
        plane="acquisition",
        key="3200-1-le26",
        rank="ticket_detail",
        status_code="8",
        status_name="Publicada",
        close="2026-08-10T12:00:00Z",
        acquired="2026-08-01T10:00:00Z",
        obs="o1",
        snap="s1",
    )
    _, conflicts = coalesce_evidence_refs([ref])
    assert any("status_code_name_inconsistent" in c.reason_codes for c in conflicts)


def test_timestamp_semantic_equality_no_conflict() -> None:
    a = _ref(
        rid="a",
        plane="acquisition",
        key="3300-1-le26",
        rank="ticket_detail",
        status_code="5",
        status_name="Publicada",
        close="2026-08-10T18:00:00-04:00",
        acquired="2026-08-01T10:00:00Z",
        obs="o1",
        snap="s1",
    )
    b = _ref(
        rid="b",
        plane="acquisition",
        key="3300-1-le26",
        rank="ticket_summary",
        status_code="5",
        status_name="Publicada",
        close="2026-08-10T22:00:00Z",
        acquired="2026-08-01T10:00:00Z",
        obs="o2",
        snap="s2",
    )
    _, conflicts = coalesce_evidence_refs([a, b])
    assert not any(c.conflict_kind == "date_conflict" for c in conflicts)


def test_naive_santiago_vs_aware_equivalent() -> None:
    dt_naive, err1 = parse_tender_timestamp_raw("2026-08-10 18:00:00")
    dt_aware, err2 = parse_tender_timestamp_raw("2026-08-10T18:00:00-04:00")
    assert err1 is None and err2 is None
    assert dt_naive == dt_aware


def test_genuine_close_instant_conflict() -> None:
    a = _ref(
        rid="a",
        plane="acquisition",
        key="3400-1-le26",
        rank="ticket_detail",
        status_code="5",
        status_name="Publicada",
        close="2026-08-10T12:00:00Z",
        acquired="2026-08-01T10:00:00Z",
        obs="o1",
        snap="s1",
    )
    b = _ref(
        rid="b",
        plane="acquisition",
        key="3400-1-le26",
        rank="ticket_summary",
        status_code="5",
        status_name="Publicada",
        close="2026-08-11T12:00:00Z",
        acquired="2026-08-01T10:00:00Z",
        obs="o2",
        snap="s2",
    )
    tenders, conflicts = coalesce_evidence_refs([a, b])
    assert any(
        "close_timestamp_conflict" in c.reason_codes for c in conflicts
    )
    as_of = parse_as_of_utc(AS_OF)
    out = apply_lifecycle(
        tenders,
        refs_by_id={"a": a, "b": b},
        conflicts_by_id={c.conflict_id: c for c in conflicts},
        as_of_utc=as_of,
        freshness_threshold_hours=48,
    )
    assert out[0].lifecycle_class != "active_open"
    assert "authoritative_close_date_conflict" in out[0].lifecycle_reason_codes


def test_timezone_unresolved_malformed() -> None:
    dt, err = parse_tender_timestamp_raw("not-a-timestamp")
    assert dt is None
    assert err == "timezone_unresolved"


def test_identical_repeated_observation_deduped() -> None:
    a = _ref(
        rid="a1",
        plane="acquisition",
        key="3500-1-le26",
        rank="ticket_detail",
        status_code="5",
        status_name="Publicada",
        close="2026-08-10T12:00:00Z",
        acquired="2026-08-01T10:00:00Z",
        obs="same_obs",
        snap="s1",
        payload_digest="same",
    )
    b = _ref(
        rid="a2",
        plane="acquisition",
        key="3500-1-le26",
        rank="ticket_detail",
        status_code="5",
        status_name="Publicada",
        close="2026-08-10T12:00:00Z",
        acquired="2026-08-01T10:00:00Z",
        obs="same_obs",
        snap="s1",
        payload_digest="same",
    )
    tenders, conflicts = coalesce_evidence_refs([a, b])
    assert len(tenders[0].evidence_ref_ids) == 1
    assert not any(
        c.conflict_kind == "duplicate_live_observation_conflict" for c in conflicts
    )


# --- Freshness / lifecycle ---


def test_stale_open_not_active_and_current_open_active() -> None:
    as_of = parse_as_of_utc(AS_OF)
    tender = _tender_shell(
        selected_field_provenance={"status": "e1", "status_code": "e1", "status_name": "e1", "close_timestamp": "e1"},
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
    assert out2[0].lifecycle_class == "active_open"
    assert out2[0].lifecycle_status_evidence_ref_id == "e1"
    assert out2[0].lifecycle_close_evidence_ref_id == "e1"


def test_close_exactly_at_as_of_is_closed() -> None:
    as_of = parse_as_of_utc("2026-08-01T12:00:00Z")
    life, bucket, _, _ = classify_lifecycle(
        tender=_tender_shell(
            close_timestamp_selected="2026-08-01T12:00:00Z",
            currentness_class="current_authoritative_snapshot",
            evidence_ref_ids=(),
            acquisition_snapshot_ids=(),
            acquisition_observation_ids=(),
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
        life, _, reasons, _ = classify_lifecycle(
            tender=_tender_shell(
                candidate_source_kind="pr4",
                pr4_procurement_id="p",
                pr4_procurement_ids=("p",),
                acquisition_snapshot_ids=(),
                acquisition_observation_ids=(),
                coalescence_status="pr4_only",
                currentness_class="historical_pr4_only",
                close_timestamp_selected=None,
                status_code_selected=code,
                status_name_selected=name,
                evidence_ref_ids=(),
            ),
            currentness_class="historical_pr4_only",
            as_of_utc=as_of,
            has_status_conflict=False,
        )
        assert life == expected
        assert any(reason_substr in r for r in reasons)


def test_lista_stub_cannot_freshen_pr4_lifecycle() -> None:
    as_of = parse_as_of_utc(AS_OF)
    pr4 = _ref(
        rid="p",
        plane="pr4",
        key="4100-1-le26",
        rank="pr4",
        status_code="5",
        status_name="Publicada",
        close="2026-08-10T18:00:00-04:00",
        pr4_id="p1",
    )
    lista = _ref(
        rid="l",
        plane="acquisition",
        key="4100-1-le26",
        rank="ocds_lista_index",
        acquired="2026-08-01T10:00:00Z",
        obs="lista1",
        snap="s1",
    )
    tenders, conflicts = coalesce_evidence_refs([pr4, lista])
    out = apply_lifecycle(
        tenders,
        refs_by_id={"p": pr4, "l": lista},
        conflicts_by_id={c.conflict_id: c for c in conflicts},
        as_of_utc=as_of,
        freshness_threshold_hours=48,
    )
    assert out[0].lifecycle_class != "active_open"
    # Lista cannot supply status; provenance stays on PR4.
    assert out[0].selected_field_provenance.get("status") == "p"


def test_buyer_only_live_cannot_freshen_lifecycle() -> None:
    as_of = parse_as_of_utc(AS_OF)
    pr4 = _ref(
        rid="p",
        plane="pr4",
        key="4200-1-le26",
        rank="pr4",
        status_code="5",
        status_name="Publicada",
        close="2026-08-10T18:00:00-04:00",
        pr4_id="p1",
    )
    buyer_only = _ref(
        rid="l",
        plane="acquisition",
        key="4200-1-le26",
        rank="ticket_detail",
        buyer="Fresh Buyer",
        acquired="2026-08-01T10:00:00Z",
        obs="b1",
        snap="s1",
    )
    tenders, conflicts = coalesce_evidence_refs([pr4, buyer_only])
    out = apply_lifecycle(
        tenders,
        refs_by_id={"p": pr4, "l": buyer_only},
        conflicts_by_id={c.conflict_id: c for c in conflicts},
        as_of_utc=as_of,
        freshness_threshold_hours=48,
    )
    assert out[0].lifecycle_class != "active_open"
    assert out[0].selected_field_provenance.get("status") == "p"


def test_status_and_close_from_separate_current_refs() -> None:
    as_of = parse_as_of_utc(AS_OF)
    status_ref = _ref(
        rid="s",
        plane="acquisition",
        key="4300-1-le26",
        rank="ticket_detail",
        status_code="5",
        status_name="Publicada",
        acquired="2026-08-01T10:00:00Z",
        obs="o1",
        snap="s1",
    )
    close_ref = _ref(
        rid="c",
        plane="acquisition",
        key="4300-1-le26",
        rank="ticket_summary",
        close="2026-08-10T18:00:00-04:00",
        acquired="2026-08-01T09:00:00Z",
        obs="o2",
        snap="s2",
    )
    tenders, conflicts = coalesce_evidence_refs([status_ref, close_ref])
    out = apply_lifecycle(
        tenders,
        refs_by_id={"s": status_ref, "c": close_ref},
        conflicts_by_id={c.conflict_id: c for c in conflicts},
        as_of_utc=as_of,
        freshness_threshold_hours=48,
    )
    assert out[0].lifecycle_class == "active_open"
    assert out[0].lifecycle_status_evidence_ref_id == "s"
    assert out[0].lifecycle_close_evidence_ref_id == "c"


def test_one_stale_lifecycle_ref_blocks_active_open() -> None:
    as_of = parse_as_of_utc(AS_OF)
    status_ref = _ref(
        rid="s",
        plane="acquisition",
        key="4400-1-le26",
        rank="ticket_detail",
        status_code="5",
        status_name="Publicada",
        acquired="2026-08-01T10:00:00Z",
        obs="o1",
        snap="s1",
    )
    stale_close = _ref(
        rid="c",
        plane="acquisition",
        key="4400-1-le26",
        rank="ticket_summary",
        close="2026-08-10T18:00:00-04:00",
        acquired="2026-07-01T09:00:00Z",
        obs="o2",
        snap="s2",
    )
    tenders, conflicts = coalesce_evidence_refs([status_ref, stale_close])
    out = apply_lifecycle(
        tenders,
        refs_by_id={"s": status_ref, "c": stale_close},
        conflicts_by_id={c.conflict_id: c for c in conflicts},
        as_of_utc=as_of,
        freshness_threshold_hours=48,
    )
    assert out[0].lifecycle_class != "active_open"


def test_naive_as_of_rejected() -> None:
    with pytest.raises(ValueError, match="malformed timezone"):
        parse_as_of_utc("2026-08-01T12:00:00")


def test_nonpositive_freshness_rejected(tmp_path: Path) -> None:
    db = tmp_path / "pr4.sqlite"
    _seed_pr4_db(
        db,
        [
            {
                "procurement_id": "p1",
                "canonical_tender_key": "1000-1-LE26",
                "status_code": "6",
                "status_name": "Cerrada",
            }
        ],
    )
    snap = _write_snap(tmp_path, "d.json", _ticket_detail_snapshot())
    with pytest.raises(ValueError, match="freshness_threshold_hours"):
        build_candidate_plan(
            sqlite_path=db,
            acquisition_snapshot_paths=[snap],
            as_of_utc=AS_OF,
            freshness_threshold_hours=0,
            run_context="local_fixture",
        )


# --- Report output safety ---


def test_report_output_valid_and_rejects(tmp_path: Path) -> None:
    root, out = _reports_out(tmp_path)
    resolved = require_reports_out_dir(out, repo_email_pipeline_root=root)
    assert resolved == out.resolve()

    docs = root / "docs" / "out"
    docs.mkdir(parents=True)
    with pytest.raises(ReportOutputError):
        require_reports_out_dir(docs, repo_email_pipeline_root=root)

    escape = root / "reports" / "out" / ".." / "secret"
    with pytest.raises(ReportOutputError):
        require_reports_out_dir(escape, repo_email_pipeline_root=root)

    # Symlink escape
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "reports" / "out" / "link_escape"
    link.symlink_to(outside)
    with pytest.raises(ReportOutputError):
        require_reports_out_dir(link, repo_email_pipeline_root=root)
    assert list(outside.iterdir()) == []


def test_write_plan_rejects_before_partial_files(tmp_path: Path) -> None:
    db = tmp_path / "pr4.sqlite"
    _seed_pr4_db(
        db,
        [
            {
                "procurement_id": "p1",
                "canonical_tender_key": "1000-1-LE26",
                "status_code": "6",
                "status_name": "Cerrada",
            }
        ],
    )
    snap = _write_snap(tmp_path, "d.json", _ticket_detail_snapshot())
    result = build_candidate_plan(
        sqlite_path=db,
        acquisition_snapshot_paths=[snap],
        as_of_utc=AS_OF,
        freshness_threshold_hours=48,
        run_context="local_fixture",
    )
    bad = tmp_path / "docs" / "leak"
    bad.mkdir(parents=True)
    with pytest.raises(ReportOutputError):
        write_plan_outputs(result, bad, repo_email_pipeline_root=tmp_path / "email-pipeline")
    assert list(bad.iterdir()) == []


# --- End-to-end planner ---


def test_end_to_end_plan_reconciliation_and_fingerprints(tmp_path: Path) -> None:
    db = tmp_path / "pr4.sqlite"
    detail = _ticket_detail_snapshot()
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
            "procurement_id": "p_non_mp_001",
            "canonical_tender_key": "1000-1-LE123",
            "tender_key_kind": "codigo_externo",
            "status_code": "6",
            "status_name": "Cerrada",
            "publication_at": "2026-01-01",
            "close_at": "2026-01-10",
            "buyer_name_raw": "Non MP Shape",
            "title": "Former gap shape",
            "procurement_context": "historical_tender",
        },
    ]
    _seed_pr4_db(db, signals)

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

    result = build_candidate_plan(
        sqlite_path=db,
        acquisition_snapshot_paths=[snap_path, lista_path],
        as_of_utc=AS_OF,
        freshness_threshold_hours=48,
        run_context="local_fixture",
    )
    assert result.aggregate_reconciliation["ok"] is True
    assert result.aggregate_reconciliation["no_silent_drop"] is True
    counts = result.aggregate_reconciliation["counts"]
    assert counts["pr4_signals_total"] == counts["pr4_coalesced"] + counts["pr4_unresolved"]
    assert counts["pr4_unresolved"] == 0
    kinds = {t.candidate_source_kind for t in result.coalesced_tenders}
    assert "pr4" in kinds
    assert "both" in kinds or "live_snapshot" in kinds
    assert any(u.unresolved_reason for u in result.unresolved)

    non_mp = next(
        t for t in result.coalesced_tenders if t.canonical_tender_key == "1000-1-le123"
    )
    assert non_mp.tender_key_kind == "codigo_externo"
    assert non_mp.candidate_source_kind == "pr4"

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

    root, out = _reports_out(tmp_path)
    write_plan_outputs(result, out, repo_email_pipeline_root=root)
    bundle = build_walkthrough_bundle(
        result,
        case_hints={"case_c_synthetic": True, "case_d_synthetic": True},
    )
    write_walkthrough(bundle, out, repo_email_pipeline_root=root)
    assert_no_pii_leaks(json.dumps(bundle))
    red = redact_coalesced_tender(result.coalesced_tenders[0].to_dict())
    assert "@" not in json.dumps(red)
    assert (out / "DATA_WALKTHROUGH.md").read_text().strip()


def test_complete_pr4_reconciliation(tmp_path: Path) -> None:
    db = tmp_path / "pr4.sqlite"
    _seed_pr4_db(
        db,
        [
            {
                "procurement_id": "ok1",
                "canonical_tender_key": "1000-1-LE26",
                "status_code": "6",
                "status_name": "Cerrada",
            },
            {
                "procurement_id": "ok2",
                "canonical_tender_key": "1000-1-LE123",
                "status_code": "6",
                "status_name": "Cerrada",
            },
            {
                "procurement_id": "bad",
                "canonical_tender_key": "",
                "tender_key_kind": "codigo_externo",
                "status_code": "6",
                "status_name": "Cerrada",
            },
        ],
    )
    snap = _write_snap(tmp_path, "d.json", _ticket_detail_snapshot())
    result = build_candidate_plan(
        sqlite_path=db,
        acquisition_snapshot_paths=[snap],
        as_of_utc=AS_OF,
        freshness_threshold_hours=48,
        run_context="local_fixture",
    )
    c = result.aggregate_reconciliation["counts"]
    assert c["pr4_signals_total"] == 3
    assert c["pr4_coalesced"] == 2
    assert c["pr4_unresolved"] == 1
    assert c["pr4_signals_total"] == c["pr4_coalesced"] + c["pr4_unresolved"]


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
