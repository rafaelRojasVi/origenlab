"""PR5B acquisition contract correction tests."""

from __future__ import annotations

import copy
import json
import random
from pathlib import Path

import pytest

from origenlab_email_pipeline.chilecompra_api import normalize_licitacion_detail_items
from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.fingerprint import (
    acquisition_normalized_semantic_digest,
    acquisition_source_fingerprint,
    payload_digests,
    query_identity_from_model,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.identity import (
    normalize_mercado_publico_codigo,
    ocds_canonical_candidate,
    ticket_canonical_candidate,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.normalize import (
    tender_observation_to_normalized_row,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.ocds import (
    OcdsParseError,
    build_ocds_month_query,
    build_ocds_month_snapshot,
    build_ocds_query,
    detect_range_anomalies,
    parse_ocds_package,
    plan_ocds_ranges,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.redaction import (
    assert_no_ticket_leak,
    sanitize_mapping,
    ticket_safety_flags,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.snapshot import (
    build_acquisition_snapshot,
    build_partial_detail_run,
    snapshot_manifest,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.ticket_api import (
    TicketApiParseError,
    build_ticket_detail_query,
    build_ticket_summary_query,
    parse_ticket_detail_payload,
    parse_ticket_summary_payload,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.walkthrough import (
    build_walkthrough_cases,
)

FIXTURES = Path(__file__).parent / "fixtures" / "commercial_procurement_acquisition"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# --- identity ---


def test_same_codigo_produces_equal_canonical_candidates() -> None:
    ticket = ticket_canonical_candidate("9999-1-LE26")
    ocds = ocds_canonical_candidate(
        ocid="ocds-x",
        release_id="r1",
        tender_id="9999-1-LE26",
        release_kind="historical_release",
    )
    assert ticket.canonical_tender_key_candidate == ocds.canonical_tender_key_candidate
    assert ticket.canonical_tender_key_candidate == "9999-1-le26"
    assert ticket.source_native_tender_key != ocds.source_native_tender_key
    assert not ticket.canonical_tender_key_candidate.startswith("ticket:")
    assert "ocds-tender:" not in (ocds.canonical_tender_key_candidate or "")


def test_different_ids_do_not_share_canonical_candidate() -> None:
    a = ticket_canonical_candidate("9999-1-LE26")
    b = ticket_canonical_candidate("9999-2-LE26")
    assert a.canonical_tender_key_candidate != b.canonical_tender_key_candidate


def test_ocid_only_has_no_false_mp_candidate() -> None:
    cand = ocds_canonical_candidate(
        ocid="ocds-synth-ocid-only",
        release_id="r1",
        tender_id=None,
        release_kind="historical_release",
    )
    assert cand.canonical_tender_key_candidate is None
    assert cand.canonical_candidate_reason == "unresolved_ocid_only_no_mp_codigo"
    parsed = parse_ocds_package(_load("ocds_ocid_only.json"))
    assert parsed[3][0].canonical_tender_key_candidate is None


def test_case_and_whitespace_normalization() -> None:
    a = ticket_canonical_candidate(" 9999-1-LE26 ")
    b = ticket_canonical_candidate("9999-1-le26")
    assert a.canonical_tender_key_candidate == b.canonical_tender_key_candidate
    assert normalize_mercado_publico_codigo("9999-1- LE26") == "9999-1-le26"


def test_malformed_codigo_remains_source_native_unresolved() -> None:
    cand = ticket_canonical_candidate("NOT-A-VALID-CODE")
    assert cand.canonical_tender_key_candidate is None
    assert cand.canonical_candidate_reason == "malformed_codigo_externo_unresolved"
    assert cand.source_native_tender_key.startswith("ticket_api:codigo_externo:")


# --- ticket summary / detail ---


def test_ticket_summary_list_envelope() -> None:
    payload = _load("ticket_summary_list.json")
    _q, page, sources, tenders, lines, diag = parse_ticket_summary_payload(payload)
    assert page.parser_status == "ok"
    assert len(sources) == 2
    assert len(tenders) == 2
    assert lines == []
    assert diag["lines_emitted"] == 0
    assert sources[0].source_native_tender_key.startswith("ticket_api:codigo_externo:")
    assert sources[0].canonical_candidate_kind == "mercado_publico_codigo_externo"


def test_ticket_summary_nested_single() -> None:
    _q, page, sources, tenders, _lines, _ = parse_ticket_summary_payload(
        _load("ticket_summary_single.json")
    )
    assert page.response_item_count == 1
    assert sources[0].canonical_tender_key_candidate == "9999-3-le26"


def test_ticket_empty_null_listado() -> None:
    _q, page, sources, tenders, lines, _ = parse_ticket_summary_payload(
        _load("ticket_empty.json")
    )
    assert sources == []
    assert tenders == []
    assert lines == []
    assert page.response_item_count == 0


def test_ticket_malformed_shape() -> None:
    _q, page, sources, _, _, diag = parse_ticket_summary_payload(
        _load("ticket_malformed_shape.json")
    )
    assert page.completeness_status == "malformed_response"
    assert sources == []
    assert "error" in diag


def test_ticket_detail_lines_and_stable_ids() -> None:
    payload = _load("ticket_detail_items.json")
    a = parse_ticket_detail_payload(payload, tender_code="9999-1-LE26")
    shuffled = json.loads(json.dumps(payload))
    items = shuffled["Listado"][0]["Items"]["Listado"]
    random.Random(0).shuffle(items)
    b = parse_ticket_detail_payload(shuffled, tender_code="9999-1-LE26")
    assert [x.line_observation_id for x in a[4]] == [
        x.line_observation_id for x in b[4]
    ]
    assert len(a[4]) == 2
    assert a[1].completeness_status == "complete"


def test_ticket_detail_hardening_statuses() -> None:
    base = _load("ticket_detail_items.json")

    empty = copy.deepcopy(base)
    empty["Listado"] = []
    empty["Cantidad"] = 0
    assert parse_ticket_detail_payload(empty, tender_code="9999-1-LE26")[1].completeness_status == (
        "detail_empty"
    )

    multi = copy.deepcopy(base)
    multi["Listado"] = [base["Listado"][0], base["Listado"][0]]
    multi["Cantidad"] = 2
    assert (
        parse_ticket_detail_payload(multi, tender_code="9999-1-LE26")[1].completeness_status
        == "detail_multiple_results"
    )
    assert parse_ticket_detail_payload(multi, tender_code="9999-1-LE26")[2] == []

    mismatch = copy.deepcopy(base)
    mismatch["Listado"][0]["CodigoExterno"] = "9999-9-LE26"
    assert (
        parse_ticket_detail_payload(mismatch, tender_code="9999-1-LE26")[
            1
        ].completeness_status
        == "detail_code_mismatch"
    )
    assert parse_ticket_detail_payload(mismatch, tender_code="9999-1-LE26")[3] == []

    total_bad = copy.deepcopy(base)
    total_bad["Cantidad"] = 5
    assert (
        parse_ticket_detail_payload(total_bad, tender_code="9999-1-LE26")[
            1
        ].completeness_status
        == "source_total_mismatch"
    )

    assert (
        parse_ticket_detail_payload(["not", "object"], tender_code="9999-1-LE26")[
            1
        ].completeness_status
        == "malformed_response"
    )


def test_ticket_detail_compatibility_with_existing_normalizer() -> None:
    payload = _load("ticket_detail_items.json")
    lic = payload["Listado"][0]
    existing = normalize_licitacion_detail_items(lic)
    _q, _p, _s, tenders, lines, _ = parse_ticket_detail_payload(
        payload, tender_code="9999-1-LE26"
    )
    adapted = [
        tender_observation_to_normalized_row(
            tenders[0], line=line, tender_code="9999-1-LE26"
        )
        for line in lines
    ]
    assert len(adapted) == len(existing)
    for e, a in zip(existing, adapted, strict=True):
        for key in (
            "codigo",
            "title",
            "descripcion",
            "buyer",
            "region",
            "fecha_publicacion",
            "close_date",
            "chilecompra_status_code",
            "chilecompra_status",
            "line_description",
            "unspsc_code",
            "unidad",
            "cantidad",
            "producto",
        ):
            assert e[key] == a[key], key


def test_ticket_payload_with_ticket_key_is_malformed_and_secret_free() -> None:
    payload = dict(_load("ticket_summary_list.json"))
    payload["ticket"] = "SECRET"
    _q, page, *_rest = parse_ticket_summary_payload(payload)
    assert page.completeness_status == "malformed_response"
    assert "SECRET" not in json.dumps(page.to_dict())


def test_query_identity_validation() -> None:
    q = build_ticket_summary_query(estado=" Activas ", fecha_ddmmaaaa="01082026")
    assert q.estado == "activas"
    assert q.fecha_ddmmaaaa == "01082026"
    with pytest.raises(Exception):
        build_ticket_summary_query(fecha_ddmmaaaa="not-a-date")
    with pytest.raises(TicketApiParseError):
        build_ticket_detail_query(tender_code="  ")
    with pytest.raises(OcdsParseError):
        build_ocds_query(year=2026, month=13, range_start=0, range_end=0)
    q0 = build_ocds_query(year=2026, month=1, range_start=0, range_end=0)
    assert q0.endpoint_path.endswith("/0/1")
    with pytest.raises(OcdsParseError):
        build_ocds_query(year=2026, month=1, range_start=-1, range_end=0)
    with pytest.raises(OcdsParseError):
        build_ocds_query(year=2026, month=1, range_start=5, range_end=4)
    with pytest.raises(OcdsParseError):
        build_ocds_query(year=2026, month=1, range_start=0, range_end=1001)


def test_sanitize_mapping_strips_ticket_url() -> None:
    clean = sanitize_mapping(
        {
            "estado": "activas",
            "query": (
                "https://api.mercadopublico.cl/servicios/v1/publico/"
                "licitaciones.json?ticket=SECRET&estado=activas"
            ),
            "nested": {"Ticket": "SECRET", "token": "x", "authorization": "Bearer x"},
        }
    )
    blob = json.dumps(clean)
    assert "SECRET" not in blob
    assert "Bearer" not in blob
    assert clean.get("estado") == "activas"
    assert clean.get("endpoint_path") == "/servicios/v1/publico/licitaciones.json"


def test_ticket_safety_flags_shape() -> None:
    flags = ticket_safety_flags(ticket_configured=True)
    assert flags == {
        "ticket_configured": True,
        "ticket_used_for_request": False,
        "ticket_persisted": False,
        "ticket_logged": False,
        "authenticated_request_performed": False,
    }
    assert "ticket_value_accessed" not in flags


def test_acquisition_query_has_no_extra() -> None:
    q = build_ticket_detail_query(tender_code="9999-1-LE26")
    assert "extra" not in q.to_dict()
    assert "extra" not in q.identity_payload()


# --- malformed fingerprints ---


def test_malformed_payload_digests_differ_for_different_inputs() -> None:
    d1, p1, _ = payload_digests({"bad": 1})
    d2, p2, _ = payload_digests({"bad": 2})
    assert d1 != d2
    assert p1 != p2
    s1, _, _ = payload_digests("not-json-object")
    s2, _, _ = payload_digests(["also", "bad"])
    assert s1 != s2


def test_malformed_bytes_whitespace_vs_canonical() -> None:
    obj = {"Listado": "not-a-list"}
    compact = json.dumps(obj, separators=(",", ":")).encode()
    pretty = (json.dumps(obj, indent=2) + "\n").encode()
    d_c, _, b_c = payload_digests(obj, original_bytes=compact)
    d_p, _, b_p = payload_digests(obj, original_bytes=pretty)
    assert d_c == d_p
    assert b_c != b_p


def test_error_text_does_not_replace_payload_identity() -> None:
    payload: list[str] = ["broken", "list"]
    page_a = parse_ticket_summary_payload(payload)[1]
    page_b = parse_ticket_summary_payload(payload)[1]
    assert page_a.completeness_status == "malformed_response"
    assert page_a.raw_canonical_json_digest == page_b.raw_canonical_json_digest
    assert page_a.error_message is not None
    assert page_a.error_message not in page_a.raw_canonical_json_digest
    other = parse_ticket_summary_payload(["different", "broken"])[1]
    assert other.raw_canonical_json_digest != page_a.raw_canonical_json_digest


# --- AcquisitionRun ---


def test_partial_detail_run_uses_distinct_query_ids() -> None:
    summary = _load("ticket_summary_list.json")
    detail = _load("ticket_detail_items.json")
    result = build_partial_detail_run(
        summary_payload=summary,
        detail_success_payload=detail,
        detail_failure_code="9999-2-LE26",
        detail_failure_error="fail wording A",
        fixture_origin="synthetic_official_shape",
        materialized_at_utc="2026-08-01T00:00:00Z",
    )
    run = result.run
    assert run.run_completeness == "partial_detail_failure"
    assert run.completed_detail_count == 1
    assert run.failed_detail_count == 1
    q_a = result.detail_success_snapshot.query.acquisition_query_id
    q_b = result.failed_detail_attempt.query.acquisition_query_id
    assert q_a != q_b
    assert result.failed_detail_attempt.snapshot_id is None
    assert result.failed_detail_attempt.query.tender_code == "9999-2-le26"
    assert build_ticket_detail_query(tender_code="9999-2-LE26").acquisition_query_id == q_b
    result2 = build_partial_detail_run(
        summary_payload=summary,
        detail_success_payload=detail,
        detail_failure_code="9999-2-LE26",
        detail_failure_error="different sanitized wording",
        fixture_origin="synthetic_official_shape",
        materialized_at_utc="2099-01-01T00:00:00Z",
    )
    assert result.failed_page.page_id is not None
    assert result2.failed_page.page_id is not None
    assert result.failed_page.page_id == result2.failed_page.page_id


# --- OCDS ---


def test_ocds_single_and_multiple_releases() -> None:
    single = parse_ocds_package(_load("ocds_single_release.json"))
    assert len(single[2]) == 1
    assert single[2][0].source_status_system == "ocds"
    assert single[2][0].canonical_tender_key_candidate == "9999-1-le26"
    assert len(single[4]) == 1
    multi = parse_ocds_package(_load("ocds_multiple_releases.json"))
    assert len(multi[2]) == 2
    assert len(multi[4]) == 3


def test_ocds_empty_page_is_neutral_not_terminal() -> None:
    empty = parse_ocds_package(_load("ocds_empty_page.json"))
    assert empty[1].completeness_status == "empty_page"
    mal = parse_ocds_package(_load("ocds_malformed_shape.json"))
    assert mal[1].completeness_status == "malformed_response"


def test_ocds_records_policy_b_no_duplicate_compiled() -> None:
    _q, page, sources, tenders, lines, diag = parse_ocds_package(
        _load("ocds_records_with_compiled.json")
    )
    assert page.parser_status == "ok"
    kinds = [s.release_kind for s in sources]
    assert kinds.count("historical_release") == 2
    assert kinds.count("compiled_release") == 0  # duplicate of historical-01
    assert all(s.record_id == "record-synth-0001" for s in sources)
    assert tenders[0].procurement_method == "open"
    assert tenders[0].related_processes
    assert any(line.additional_classifications for line in lines)
    assert diag["records_policy"].startswith("B_")


def test_ocds_range_planner() -> None:
    assert plan_ocds_ranges(year=2026, month=1, source_reported_total=0) == []
    assert plan_ocds_ranges(year=2026, month=1, source_reported_total=1001) == [
        {"year": 2026, "month": 1, "start": 0, "end": 999},
        {"year": 2026, "month": 1, "start": 1000, "end": 1000},
    ]
    with pytest.raises(OcdsParseError):
        plan_ocds_ranges(year=2026, month=1, source_reported_total=-1)
    with pytest.raises(OcdsParseError):
        plan_ocds_ranges(year=2026, month=0, source_reported_total=1)


def test_range_anomaly_detection() -> None:
    planned = plan_ocds_ranges(year=2026, month=1, source_reported_total=1001)
    issues = detect_range_anomalies(
        planned,
        [
            {"start": 0, "end": 999},
            {"start": 0, "end": 999},
            {"start": 900, "end": 1100},
        ],
    )
    assert any("duplicate_page" in i for i in issues)
    assert any("missing_range" in i for i in issues)


def _page_payload(release_id: str, tender_id: str = "9999-1-LE26") -> dict:
    base = _load("ocds_single_release.json")
    base = json.loads(json.dumps(base))
    base["releases"][0]["id"] = release_id
    base["releases"][0]["tender"]["id"] = tender_id
    return base


def test_ocds_month_snapshot_complete_and_shuffled() -> None:
    planned = plan_ocds_ranges(year=2026, month=8, source_reported_total=2, page_size=1)
    p1 = parse_ocds_package(
        _page_payload("r1", "9999-1-LE26"), year=2026, month=8, range_start=0, range_end=0
    )
    p2 = parse_ocds_package(
        _page_payload("r2", "9999-2-LE26"), year=2026, month=8, range_start=1, range_end=1
    )
    a = build_ocds_month_snapshot(
        planned_ranges=planned,
        parsed_pages=[p1, p2],
        source_reported_total=2,
        materialized_at_utc="2026-08-01T00:00:00Z",
    )
    b = build_ocds_month_snapshot(
        planned_ranges=planned,
        parsed_pages=[p2, p1],
        source_reported_total=2,
        materialized_at_utc="2099-01-01T00:00:00Z",
    )
    assert a.completeness_status == "complete"
    assert a.source_fingerprint == b.source_fingerprint
    assert a.normalized_semantic_digest == b.normalized_semantic_digest
    assert a.snapshot_id == b.snapshot_id


def test_ocds_month_empty_middle_and_terminal() -> None:
    planned = [
        {"year": 2026, "month": 8, "start": 0, "end": 0},
        {"year": 2026, "month": 8, "start": 1, "end": 1},
        {"year": 2026, "month": 8, "start": 2, "end": 2},
    ]
    p1 = parse_ocds_package(
        _page_payload("r1"), year=2026, month=8, range_start=0, range_end=0
    )
    empty = parse_ocds_package(
        _load("ocds_empty_page.json"), year=2026, month=8, range_start=1, range_end=1
    )
    p3 = parse_ocds_package(
        _page_payload("r3", "9999-3-LE26"), year=2026, month=8, range_start=2, range_end=2
    )
    mid = build_ocds_month_snapshot(
        planned_ranges=planned,
        parsed_pages=[p1, empty, p3],
        source_reported_total=3,
    )
    assert mid.completeness_status == "incomplete_range"

    # Valid terminal empty: filled page then empty beyond total.
    planned2 = [
        {"year": 2026, "month": 8, "start": 0, "end": 0},
        {"year": 2026, "month": 8, "start": 1, "end": 1},
    ]
    term = build_ocds_month_snapshot(
        planned_ranges=planned2,
        parsed_pages=[
            parse_ocds_package(
                _page_payload("r1"), year=2026, month=8, range_start=0, range_end=0
            ),
            parse_ocds_package(
                _load("ocds_empty_page.json"),
                year=2026,
                month=8,
                range_start=1,
                range_end=1,
            ),
        ],
        source_reported_total=1,
    )
    assert term.completeness_status == "terminal_empty_page"


def test_ocds_month_missing_duplicate_overlap() -> None:
    planned = plan_ocds_ranges(year=2026, month=8, source_reported_total=2, page_size=1)
    p1 = parse_ocds_package(
        _page_payload("r1"), year=2026, month=8, range_start=0, range_end=0
    )
    missing = build_ocds_month_snapshot(
        planned_ranges=planned, parsed_pages=[p1], source_reported_total=2
    )
    assert missing.completeness_status == "incomplete_range"

    with pytest.raises(OcdsParseError, match="duplicate child page"):
        build_ocds_month_snapshot(
            planned_ranges=planned,
            parsed_pages=[p1, p1],
            source_reported_total=2,
        )

    with pytest.raises(OcdsParseError, match="overlap"):
        build_ocds_month_snapshot(
            planned_ranges=[
                {"year": 2026, "month": 8, "start": 0, "end": 1},
                {"year": 2026, "month": 8, "start": 1, "end": 2},
            ],
            parsed_pages=[
                parse_ocds_package(
                    _page_payload("r1"), year=2026, month=8, range_start=0, range_end=1
                ),
            ],
            source_reported_total=3,
        )


def test_ocds_month_source_total_mismatch() -> None:
    planned = plan_ocds_ranges(year=2026, month=8, source_reported_total=2, page_size=1)
    # Over-filled child pages: plan covers total=2, but item counts exceed it.
    over = {
        "releases": [
            {
                "ocid": "o-a",
                "id": "r-a",
                "tender": {"id": "9999-1-LE26", "status": "active"},
            },
            {
                "ocid": "o-b",
                "id": "r-b",
                "tender": {"id": "9999-2-LE26", "status": "active"},
            },
        ]
    }
    pages = [
        parse_ocds_package(over, year=2026, month=8, range_start=0, range_end=0),
        parse_ocds_package(
            _page_payload("r2", "9999-2-LE26"),
            year=2026,
            month=8,
            range_start=1,
            range_end=1,
        ),
    ]
    bad = build_ocds_month_snapshot(
        planned_ranges=planned, parsed_pages=pages, source_reported_total=2
    )
    assert bad.completeness_status == "source_total_mismatch"
    good_pages = [
        parse_ocds_package(
            _page_payload("r1"), year=2026, month=8, range_start=0, range_end=0
        ),
        parse_ocds_package(
            _page_payload("r2", "9999-2-LE26"),
            year=2026,
            month=8,
            range_start=1,
            range_end=1,
        ),
    ]
    good = build_ocds_month_snapshot(
        planned_ranges=planned, parsed_pages=good_pages, source_reported_total=2
    )
    assert good.completeness_status == "complete"


# --- semantic digests ---


def test_fingerprints_stable_and_sensitive() -> None:
    payload = _load("ticket_summary_list.json")
    a = build_acquisition_snapshot(
        source_kind="ticket_summary",
        payload=payload,
        fixture_origin="synthetic_official_shape",
        materialized_at_utc="2026-08-01T00:00:00Z",
    )
    b = build_acquisition_snapshot(
        source_kind="ticket_summary",
        payload=payload,
        fixture_origin="synthetic_official_shape",
        materialized_at_utc="2099-01-01T00:00:00Z",
    )
    assert a.source_fingerprint == b.source_fingerprint
    assert a.normalized_semantic_digest == b.normalized_semantic_digest
    assert a.snapshot_id == b.snapshot_id

    reordered = {
        "Version": payload["Version"],
        "Listado": payload["Listado"],
        "Cantidad": payload["Cantidad"],
        "FechaCreacion": payload["FechaCreacion"],
    }
    c = build_acquisition_snapshot(
        source_kind="ticket_summary",
        payload=reordered,
        fixture_origin="synthetic_official_shape",
        materialized_at_utc="2026-08-01T00:00:00Z",
    )
    assert c.normalized_semantic_digest == a.normalized_semantic_digest

    changed = json.loads(json.dumps(payload))
    changed["Listado"][0]["Nombre"] = "CHANGED TITLE"
    d = build_acquisition_snapshot(
        source_kind="ticket_summary",
        payload=changed,
        fixture_origin="synthetic_official_shape",
    )
    assert d.normalized_semantic_digest != a.normalized_semantic_digest


def test_semantic_digest_sensitivity_matrix() -> None:
    base = _load("ocds_single_release.json")

    def digest_for(mutator) -> str:
        payload = json.loads(json.dumps(base))
        mutator(payload)
        snap = build_acquisition_snapshot(
            source_kind="ocds",
            payload=payload,
            fixture_origin="synthetic_official_shape",
            year=2026,
            month=8,
            range_start=1,
            range_end=1,
            materialized_at_utc="2026-08-01T00:00:00Z",
        )
        return snap.normalized_semantic_digest

    baseline = digest_for(lambda _p: None)
    assert digest_for(lambda p: p["releases"][0]["buyer"].__setitem__("name", "OTHER")) != baseline
    assert (
        digest_for(lambda p: p["releases"][0]["buyer"].__setitem__("id", "OTHER-ID"))
        != baseline
    )
    assert (
        digest_for(
            lambda p: p["releases"][0]["tender"].__setitem__("procurementMethod", "selective")
        )
        != baseline
    )
    assert (
        digest_for(
            lambda p: p["releases"][0]["tender"]["tenderPeriod"].__setitem__(
                "endDate", "2099-01-01T00:00:00Z"
            )
        )
        != baseline
    )
    assert (
        digest_for(lambda p: p["releases"][0]["tender"].__setitem__("status", "cancelled"))
        != baseline
    )
    assert (
        digest_for(
            lambda p: p["releases"][0]["tender"]["items"][0]["classification"].__setitem__(
                "id", "99999999"
            )
        )
        != baseline
    )
    assert (
        digest_for(
            lambda p: p["releases"][0]["tender"]["items"][0].__setitem__(
                "additionalClassifications",
                [{"scheme": "UNSPSC", "id": "111", "description": "x"}],
            )
        )
        != baseline
    )
    assert (
        digest_for(
            lambda p: p["releases"][0].__setitem__(
                "relatedProcesses",
                [{"id": "x", "identifier": "y", "title": "z", "scheme": "s"}],
            )
        )
        != baseline
    )


def test_tender_related_processes_ignored_for_canonical_location() -> None:
    base = _load("ocds_single_release.json")
    mutated = json.loads(json.dumps(base))
    mutated["releases"][0]["tender"]["relatedProcesses"] = [
        {"id": "nonstandard", "identifier": "ignored", "title": "tender-level"}
    ]
    other = build_acquisition_snapshot(
        source_kind="ocds",
        payload=mutated,
        fixture_origin="synthetic_official_shape",
        year=2026,
        month=8,
        range_start=1,
        range_end=1,
        materialized_at_utc="2026-08-01T00:00:00Z",
    )
    # Canonical relatedProcesses are release-level only; tender nesting is ignored.
    assert other.tender_observations[0].related_processes == ()
    assert (
        other.tender_observations[0].field_provenance["related_processes"]
        == "release.relatedProcesses"
    )


def test_path_does_not_influence_digest() -> None:
    payload = _load("ticket_summary_list.json")
    a = build_acquisition_snapshot(
        source_kind="ticket_summary",
        payload=payload,
        fixture_origin="synthetic_official_shape",
    )
    compact = json.dumps(payload, separators=(",", ":")).encode()
    b = build_acquisition_snapshot(
        source_kind="ticket_summary",
        payload=json.loads(compact),
        fixture_origin="synthetic_official_shape",
        original_bytes=compact,
    )
    assert a.normalized_semantic_digest == b.normalized_semantic_digest


def test_fixture_origin_separate_from_completeness() -> None:
    snap = build_acquisition_snapshot(
        source_kind="ticket_summary",
        payload=_load("ticket_summary_list.json"),
        fixture_origin="synthetic_official_shape",
    )
    assert snap.fixture_origin == "synthetic_official_shape"
    assert snap.completeness_status == "complete"


def test_walkthrough_bundle_no_ticket() -> None:
    bundle = build_walkthrough_cases(FIXTURES)
    blob = json.dumps(bundle)
    assert_no_ticket_leak(blob)
    assert bundle["authenticated_request_performed"] is False
    assert bundle["ticket_used_for_request"] is False
    assert bundle["cases"]["B"]["compatibility"]["codigo_match"] is True
    assert bundle["cases"]["E"]["coalesced"] is False
    assert bundle["cases"]["E"]["candidates_equal"] is True
    assert "ticket_value_accessed" not in bundle
    assert bundle["cases"]["D"]["stages"][-1]["result"] == "snapshot_id=None"
    assert bundle["cases"]["C"]["stages"]


def test_canonical_digest_length() -> None:
    assert len(canonical_json_digest({"b": 1, "a": 2})) == 64


def test_ocds_month_query_identity_separate_from_page_width() -> None:
    month_q = build_ocds_month_query(year=2026, month=8)
    assert month_q.endpoint_kind == "ocds_lista_agno_mes_month"
    assert month_q.range_start is None and month_q.range_end is None
    assert month_q.endpoint_path == "/APISOCDS/OCDS/listaOCDSAgnoMes/2026/8"
    # Page queries still enforce max width.
    with pytest.raises(OcdsParseError):
        build_ocds_query(year=2026, month=8, range_start=0, range_end=1001)
    # Month assembly for totals > 1000 must not use a wide page query.
    for total, page_size in ((1001, 1000), (2000, 1000), (2001, 1000), (2000, 500)):
        planned = plan_ocds_ranges(
            year=2026, month=8, source_reported_total=total, page_size=page_size
        )
        assert all(r["end"] - r["start"] + 1 <= 1000 for r in planned)
        pages = []
        for r in planned:
            width = r["end"] - r["start"] + 1
            # Compact synthetic page: one release representing the page fill count
            # via response_item_count from parser (use empty + override via parse of N).
            payload = {
                "releases": [
                    {
                        "ocid": f"o-{r['start']}-{i}",
                        "id": f"rel-{r['start']}-{i}",
                        "tender": {"id": f"9999-{i % 97}-LE26", "status": "active"},
                    }
                    for i in range(width)
                ]
            }
            pages.append(
                parse_ocds_package(
                    payload,
                    year=2026,
                    month=8,
                    range_start=r["start"],
                    range_end=r["end"],
                )
            )
        snap = build_ocds_month_snapshot(
            planned_ranges=planned,
            parsed_pages=pages,
            source_reported_total=total,
            materialized_at_utc="2026-08-01T00:00:00Z",
        )
        assert snap.query.endpoint_kind == "ocds_lista_agno_mes_month"
        assert snap.query.range_start is None
        assert snap.completeness_status == "complete"
        shuffled = list(reversed(pages))
        snap2 = build_ocds_month_snapshot(
            planned_ranges=planned,
            parsed_pages=shuffled,
            source_reported_total=total,
            materialized_at_utc="2099-01-01T00:00:00Z",
        )
        assert snap.source_fingerprint == snap2.source_fingerprint
        assert snap.snapshot_id == snap2.snapshot_id


def test_month_assembly_child_validation() -> None:
    planned = plan_ocds_ranges(year=2026, month=8, source_reported_total=2, page_size=1)
    p1 = parse_ocds_package(
        _page_payload("r1"), year=2026, month=8, range_start=0, range_end=0
    )
    p2 = parse_ocds_package(
        _page_payload("r2", "9999-2-LE26"),
        year=2026,
        month=8,
        range_start=1,
        range_end=1,
    )
    # Shuffled valid children OK.
    ok = build_ocds_month_snapshot(
        planned_ranges=planned, parsed_pages=[p2, p1], source_reported_total=2
    )
    assert ok.completeness_status == "complete"

    mixed_month = parse_ocds_package(
        _page_payload("r2", "9999-2-LE26"),
        year=2026,
        month=7,
        range_start=1,
        range_end=1,
    )
    with pytest.raises(OcdsParseError, match="year/month"):
        build_ocds_month_snapshot(
            planned_ranges=planned, parsed_pages=[p1, mixed_month], source_reported_total=2
        )

    mixed_year = parse_ocds_package(
        _page_payload("r2", "9999-2-LE26"),
        year=2025,
        month=8,
        range_start=1,
        range_end=1,
    )
    with pytest.raises(OcdsParseError, match="year/month"):
        build_ocds_month_snapshot(
            planned_ranges=planned, parsed_pages=[p1, mixed_year], source_reported_total=2
        )

    from origenlab_email_pipeline.commercial_procurement_acquisition.models import (
        AcquisitionPage,
    )

    mismatched = AcquisitionPage(**{**p2[1].to_dict(), "range_position": {"start": 9, "end": 9}})
    with pytest.raises(OcdsParseError, match="range_position"):
        build_ocds_month_snapshot(
            planned_ranges=planned,
            parsed_pages=[p1, (p2[0], mismatched, p2[2], p2[3], p2[4], p2[5])],
            source_reported_total=2,
        )

    id_mismatch = AcquisitionPage(
        **{**p2[1].to_dict(), "acquisition_query_id": "acquisition_query_id_deadbeef"}
    )
    with pytest.raises(OcdsParseError, match="acquisition_query_id"):
        build_ocds_month_snapshot(
            planned_ranges=planned,
            parsed_pages=[p1, (p2[0], id_mismatch, p2[2], p2[3], p2[4], p2[5])],
            source_reported_total=2,
        )

    with pytest.raises(OcdsParseError, match="duplicate planned range"):
        build_ocds_month_snapshot(
            planned_ranges=[planned[0], planned[0]],
            parsed_pages=[p1],
            source_reported_total=1,
        )

    from origenlab_email_pipeline.commercial_procurement_acquisition.ticket_api import (
        build_ticket_summary_query,
    )

    ticket_q = build_ticket_summary_query()
    with pytest.raises(OcdsParseError, match="source_kind"):
        build_ocds_month_snapshot(
            planned_ranges=planned,
            parsed_pages=[
                p1,
                (ticket_q, p2[1], p2[2], p2[3], p2[4], p2[5]),
            ],
            source_reported_total=2,
        )


def test_ticket_summary_envelope_hardening() -> None:
    assert parse_ticket_summary_payload({})[1].completeness_status == "malformed_response"
    assert (
        parse_ticket_summary_payload({"foo": "bar"})[1].completeness_status
        == "malformed_response"
    )
    assert (
        parse_ticket_summary_payload({"Listado": "invalid"})[1].completeness_status
        == "malformed_response"
    )
    nested_bad = {"Listado": {"Licitacion": "nope"}}
    assert (
        parse_ticket_summary_payload(nested_bad)[1].completeness_status
        == "malformed_response"
    )

    valid = _load("ticket_summary_single.json")
    mixed = json.loads(json.dumps(valid))
    # Ensure Listado is a list with one valid + one missing code.
    entry = mixed["Listado"]["Licitacion"] if isinstance(mixed["Listado"], dict) else mixed["Listado"][0]
    if isinstance(mixed["Listado"], dict):
        mixed["Listado"] = [entry, {"Nombre": "no-code"}]
        mixed["Cantidad"] = 2
    else:
        mixed["Listado"] = [mixed["Listado"][0], {"Nombre": "no-code"}]
        mixed["Cantidad"] = 2
    _q, page, sources, tenders, _lines, diag = parse_ticket_summary_payload(mixed)
    assert page.completeness_status == "partial_page_failure"
    assert len(sources) == 1
    assert diag["rejected_entry_count"] == 1
    assert diag["rejected_entries"][0]["reason_code"] == "missing_codigo_externo"

    all_bad = {"Listado": [{"Nombre": "a"}, {"Nombre": "b"}], "Cantidad": 2}
    _q2, page2, sources2, _, _, diag2 = parse_ticket_summary_payload(all_bad)
    assert page2.completeness_status == "partial_page_failure"
    assert sources2 == []
    assert diag2["rejected_entry_count"] == 2

    empty = parse_ticket_summary_payload({"Listado": [], "Cantidad": 0})
    assert empty[1].completeness_status == "complete"
    assert empty[2] == []
    null_listado = parse_ticket_summary_payload(_load("ticket_empty.json"))
    assert null_listado[1].completeness_status == "complete"


def test_malformed_preserves_acquired_at() -> None:
    ts = "2026-08-01T12:00:00Z"
    ticket_page = parse_ticket_summary_payload(["bad"], acquired_at_utc=ts)[1]
    assert ticket_page.acquired_at_utc == ts
    ocds_page = parse_ocds_package({"not": "ocds"}, acquired_at_utc=ts)[1]
    assert ocds_page.acquired_at_utc == ts


def test_zero_record_ocds_month() -> None:
    a = build_ocds_month_snapshot(
        planned_ranges=[],
        parsed_pages=[],
        source_reported_total=0,
        year=2026,
        month=8,
        materialized_at_utc="2026-08-01T00:00:00Z",
    )
    b = build_ocds_month_snapshot(
        planned_ranges=[],
        parsed_pages=[],
        source_reported_total=0,
        year=2026,
        month=8,
        materialized_at_utc="2099-01-01T00:00:00Z",
    )
    assert a.completeness_status == "complete"
    assert a.pages == ()
    assert a.source_observations == ()
    assert a.source_reported_total == 0
    assert a.diagnostics["total_items"] == 0
    assert a.query.endpoint_kind == "ocds_lista_agno_mes_month"
    assert a.source_fingerprint == b.source_fingerprint
    assert a.normalized_semantic_digest == b.normalized_semantic_digest
    assert a.snapshot_id == b.snapshot_id
    assert snapshot_manifest(a, ticket_configured=False)["source_reported_total"] == 0
    assert snapshot_manifest(a, ticket_configured=False)["source_reported_total"] is not None
    assert a.to_dict()["source_reported_total"] == 0

    with pytest.raises(OcdsParseError):
        build_ocds_month_snapshot(
            planned_ranges=[], parsed_pages=[], source_reported_total=None, year=2026, month=8
        )
    with pytest.raises(OcdsParseError):
        build_ocds_month_snapshot(
            planned_ranges=[], parsed_pages=[], source_reported_total=5, year=2026, month=8
        )
    with pytest.raises(OcdsParseError):
        build_ocds_month_snapshot(
            planned_ranges=[],
            parsed_pages=[
                parse_ocds_package(
                    _page_payload("r1"), year=2026, month=8, range_start=0, range_end=0
                )
            ],
            source_reported_total=0,
            year=2026,
            month=8,
        )
    with pytest.raises(OcdsParseError):
        build_ocds_month_snapshot(
            planned_ranges=[], parsed_pages=[], source_reported_total=0
        )


def test_ocds_envelope_collection_validation() -> None:
    assert (
        parse_ocds_package({"releases": "invalid"})[1].completeness_status
        == "malformed_response"
    )
    assert (
        parse_ocds_package({"releases": {}})[1].completeness_status == "malformed_response"
    )
    assert (
        parse_ocds_package({"records": "invalid"})[1].completeness_status
        == "malformed_response"
    )
    assert (
        parse_ocds_package({"records": {}})[1].completeness_status == "malformed_response"
    )

    mixed = {
        "releases": [
            {
                "ocid": "o1",
                "id": "r1",
                "tender": {"id": "9999-1-LE26", "status": "active"},
            },
            "not-an-object",
        ]
    }
    _q, page, sources, tenders, _lines, diag = parse_ocds_package(mixed)
    assert page.completeness_status == "partial_page_failure"
    assert len(sources) == 1
    assert diag["rejected_entry_count"] == 1
    assert diag["rejected_entries"][0]["reason_code"] == "release_entry_not_object"
    assert "not-an-object" not in json.dumps(diag)

    all_bad = {"releases": ["x", 2]}
    _q2, page2, sources2, _, _, diag2 = parse_ocds_package(all_bad)
    assert page2.completeness_status == "partial_page_failure"
    assert sources2 == []
    assert diag2["rejected_entry_count"] == 2

    assert parse_ocds_package({"releases": []})[1].completeness_status == "empty_page"
    assert parse_ocds_package({"records": []})[1].completeness_status == "empty_page"

    # Malformed collection must not become terminal_empty_page in monthly assembly.
    planned = [{"year": 2026, "month": 8, "start": 0, "end": 0}]
    mal = parse_ocds_package({"releases": "bad"}, year=2026, month=8, range_start=0, range_end=0)
    snap = build_ocds_month_snapshot(
        planned_ranges=planned, parsed_pages=[mal], source_reported_total=1
    )
    assert snap.completeness_status == "malformed_response"


def test_planned_range_continuity() -> None:
    with pytest.raises(OcdsParseError, match="gap"):
        build_ocds_month_snapshot(
            planned_ranges=[
                {"year": 2026, "month": 8, "start": 0, "end": 999},
                {"year": 2026, "month": 8, "start": 2000, "end": 2999},
            ],
            parsed_pages=[],
            source_reported_total=3000,
        )
    with pytest.raises(OcdsParseError, match="overlap"):
        build_ocds_month_snapshot(
            planned_ranges=[
                {"year": 2026, "month": 8, "start": 0, "end": 999},
                {"year": 2026, "month": 8, "start": 999, "end": 1000},
            ],
            parsed_pages=[],
            source_reported_total=1001,
        )
    with pytest.raises(OcdsParseError, match="start at offset 0"):
        build_ocds_month_snapshot(
            planned_ranges=[{"year": 2026, "month": 8, "start": 1, "end": 1}],
            parsed_pages=[],
            source_reported_total=1,
        )
    with pytest.raises(OcdsParseError, match="incomplete"):
        build_ocds_month_snapshot(
            planned_ranges=[{"year": 2026, "month": 8, "start": 0, "end": 999}],
            parsed_pages=[],
            source_reported_total=2000,
        )

    ok = plan_ocds_ranges(year=2026, month=8, source_reported_total=1001, page_size=1000)
    assert ok == [
        {"year": 2026, "month": 8, "start": 0, "end": 999},
        {"year": 2026, "month": 8, "start": 1000, "end": 1000},
    ]
    # Valid terminal empty probe plan accepted:
    term_plan = [
        {"year": 2026, "month": 8, "start": 0, "end": 0},
        {"year": 2026, "month": 8, "start": 1, "end": 1},
    ]
    term = build_ocds_month_snapshot(
        planned_ranges=term_plan,
        parsed_pages=[
            parse_ocds_package(
                _page_payload("r1"), year=2026, month=8, range_start=0, range_end=0
            ),
            parse_ocds_package(
                {"releases": []}, year=2026, month=8, range_start=1, range_end=1
            ),
        ],
        source_reported_total=1,
    )
    assert term.completeness_status == "terminal_empty_page"


def test_child_source_metadata_validation() -> None:
    from origenlab_email_pipeline.commercial_procurement_acquisition.models import (
        AcquisitionPage,
        AcquisitionQuery,
    )

    planned = plan_ocds_ranges(year=2026, month=8, source_reported_total=1, page_size=1)
    good = parse_ocds_package(
        _page_payload("r1"), year=2026, month=8, range_start=0, range_end=0
    )
    q, page, srcs, tends, lines, diag = good

    wrong_q_kind = AcquisitionQuery(**{**q.to_dict(), "source_kind": "other_source"})
    with pytest.raises(OcdsParseError, match="query source_kind"):
        build_ocds_month_snapshot(
            planned_ranges=planned,
            parsed_pages=[(wrong_q_kind, page, srcs, tends, lines, diag)],
            source_reported_total=1,
        )

    wrong_page_kind = AcquisitionPage(**{**page.to_dict(), "source_kind": "other_source"})
    with pytest.raises(OcdsParseError, match="page source_kind"):
        build_ocds_month_snapshot(
            planned_ranges=planned,
            parsed_pages=[(q, wrong_page_kind, srcs, tends, lines, diag)],
            source_reported_total=1,
        )

    wrong_page_ep = AcquisitionPage(
        **{**page.to_dict(), "endpoint_kind": "ocds_lista_agno_mes_month"}
    )
    with pytest.raises(OcdsParseError, match="page endpoint_kind"):
        build_ocds_month_snapshot(
            planned_ranges=planned,
            parsed_pages=[(q, wrong_page_ep, srcs, tends, lines, diag)],
            source_reported_total=1,
        )


def test_nested_ocds_record_field_validation() -> None:
    bad_releases_str = parse_ocds_package({"records": [{"id": "r", "releases": "invalid"}]})
    assert bad_releases_str[1].completeness_status == "partial_page_failure"
    reasons = {e["reason_code"] for e in bad_releases_str[5]["rejected_entries"]}
    assert "record_releases_invalid_collection_type" in reasons
    assert "record_without_valid_release_content" in reasons
    for entry in bad_releases_str[5]["rejected_entries"]:
        assert set(entry.keys()) <= {"ordinal", "reason_code", "entry_digest"}
        assert entry["entry_digest"]
        assert "invalid" not in entry["entry_digest"]
        assert entry.get("raw") is None
        assert "releases" not in entry

    bad_releases_obj = parse_ocds_package({"records": [{"id": "r", "releases": {}}]})
    assert bad_releases_obj[1].completeness_status == "partial_page_failure"
    assert any(
        e["reason_code"] == "record_releases_invalid_collection_type"
        for e in bad_releases_obj[5]["rejected_entries"]
    )

    bad_compiled = parse_ocds_package(
        {"records": [{"id": "r", "compiledRelease": "invalid"}]}
    )
    assert bad_compiled[1].completeness_status == "partial_page_failure"
    assert any(
        e["reason_code"] == "compiled_release_not_object"
        for e in bad_compiled[5]["rejected_entries"]
    )

    mixed = {
        "records": [
            {
                "id": "rec1",
                "releases": [
                    {
                        "ocid": "o1",
                        "id": "rel1",
                        "tender": {"id": "9999-1-LE26", "status": "active"},
                    }
                ],
                "compiledRelease": "bad",
            }
        ]
    }
    _q, page, sources, _t, _l, diag = parse_ocds_package(mixed)
    assert page.completeness_status == "partial_page_failure"
    assert len(sources) == 1
    assert any(e["reason_code"] == "compiled_release_not_object" for e in diag["rejected_entries"])

    compiled_ok_releases_bad = {
        "records": [
            {
                "id": "rec1",
                "releases": "invalid",
                "compiledRelease": {
                    "ocid": "o1",
                    "id": "c1",
                    "tender": {"id": "9999-1-LE26", "status": "active"},
                },
            }
        ]
    }
    _q2, page2, sources2, _t2, _l2, diag2 = parse_ocds_package(compiled_ok_releases_bad)
    assert page2.completeness_status == "partial_page_failure"
    assert len(sources2) == 1
    assert any(
        e["reason_code"] == "record_releases_invalid_collection_type"
        for e in diag2["rejected_entries"]
    )

    all_bad = parse_ocds_package(
        {"records": [{"releases": {}}, {"compiledRelease": 3}]}
    )
    assert all_bad[1].completeness_status == "partial_page_failure"
    assert all_bad[2] == []

    assert parse_ocds_package({"records": []})[1].completeness_status == "empty_page"
    empty_rec = parse_ocds_package(
        {"records": [{"id": "r", "releases": [], "compiledRelease": None}]}
    )
    assert empty_rec[1].completeness_status == "empty_page"
    assert empty_rec[5]["rejected_entry_count"] == 0

    # Nested malformations must not become terminal_empty_page in monthly assembly.
    planned = [{"year": 2026, "month": 8, "start": 0, "end": 0}]
    nested_mal = parse_ocds_package(
        {"records": [{"releases": "bad"}]},
        year=2026,
        month=8,
        range_start=0,
        range_end=0,
    )
    snap = build_ocds_month_snapshot(
        planned_ranges=planned, parsed_pages=[nested_mal], source_reported_total=1
    )
    assert snap.completeness_status == "partial_page_failure"
    assert snap.completeness_status != "terminal_empty_page"


def test_source_reported_total_on_snapshots_and_manifest() -> None:
    summary = _load("ticket_summary_list.json")
    detail = _load("ticket_detail_items.json")
    ocds = _load("ocds_single_release.json")

    snap_a = build_acquisition_snapshot(
        source_kind="ticket_summary",
        payload=summary,
        fixture_origin="synthetic_official_shape",
        materialized_at_utc="2026-08-01T00:00:00Z",
    )
    assert snap_a.source_reported_total == summary["Cantidad"]
    man_a = snapshot_manifest(snap_a, ticket_configured=False)
    assert man_a["source_reported_total"] == summary["Cantidad"]
    assert snap_a.to_dict()["source_reported_total"] == summary["Cantidad"]

    snap_b = build_acquisition_snapshot(
        source_kind="ticket_detail",
        payload=detail,
        fixture_origin="synthetic_official_shape",
        tender_code="9999-1-LE26",
        materialized_at_utc="2026-08-01T00:00:00Z",
    )
    assert snap_b.source_reported_total == (
        detail["Cantidad"] if isinstance(detail.get("Cantidad"), int) else 1
    )
    assert snapshot_manifest(snap_b, ticket_configured=False)["source_reported_total"] == (
        snap_b.source_reported_total
    )

    snap_ocds = build_acquisition_snapshot(
        source_kind="ocds",
        payload=ocds,
        fixture_origin="synthetic_official_shape",
        year=2026,
        month=8,
        range_start=1,
        range_end=1,
        materialized_at_utc="2026-08-01T00:00:00Z",
    )
    assert snap_ocds.source_reported_total is None
    assert snapshot_manifest(snap_ocds, ticket_configured=False)["source_reported_total"] is None

    zero = build_ocds_month_snapshot(
        planned_ranges=[],
        parsed_pages=[],
        source_reported_total=0,
        year=2026,
        month=8,
        materialized_at_utc="2026-08-01T00:00:00Z",
    )
    assert zero.source_reported_total == 0
    assert snapshot_manifest(zero, ticket_configured=False)["source_reported_total"] == 0

    for total in (1001, 2000):
        planned = plan_ocds_ranges(
            year=2026, month=8, source_reported_total=total, page_size=1000
        )
        snap = build_ocds_month_snapshot(
            planned_ranges=planned,
            parsed_pages=[],
            source_reported_total=total,
            materialized_at_utc="2026-08-01T00:00:00Z",
        )
        assert snap.source_reported_total == total
        assert snapshot_manifest(snap, ticket_configured=False)["source_reported_total"] == total

    # Materialization timestamp does not change identity.
    z2 = build_ocds_month_snapshot(
        planned_ranges=[],
        parsed_pages=[],
        source_reported_total=0,
        year=2026,
        month=8,
        materialized_at_utc="2099-01-01T00:00:00Z",
    )
    assert zero.source_fingerprint == z2.source_fingerprint
    assert zero.snapshot_id == z2.snapshot_id


def test_source_fingerprint_includes_authoritative_total() -> None:
    """Snapshot-level source_reported_total is fingerprint evidence (v2)."""
    page = parse_ocds_package(
        _page_payload("r1"), year=2026, month=8, range_start=0, range_end=0
    )[1]
    q = build_ocds_month_query(year=2026, month=8)
    identity = query_identity_from_model(q)
    fp_a = acquisition_source_fingerprint(
        source_kind=q.source_kind,
        query_identity=identity,
        pages=[page],
        completeness_status="complete",
        source_reported_total=2,
    )
    fp_b = acquisition_source_fingerprint(
        source_kind=q.source_kind,
        query_identity=identity,
        pages=[page],
        completeness_status="complete",
        source_reported_total=3,
    )
    fp_null = acquisition_source_fingerprint(
        source_kind=q.source_kind,
        query_identity=identity,
        pages=[page],
        completeness_status="complete",
        source_reported_total=None,
    )
    assert fp_a != fp_b
    assert fp_a != fp_null
    # Observation counts must not be a substitute: same total → same fingerprint.
    fp_a2 = acquisition_source_fingerprint(
        source_kind=q.source_kind,
        query_identity=identity,
        pages=[page],
        completeness_status="complete",
        source_reported_total=2,
    )
    assert fp_a == fp_a2

    month_a = build_ocds_month_snapshot(
        planned_ranges=plan_ocds_ranges(
            year=2026, month=8, source_reported_total=1, page_size=1
        ),
        parsed_pages=[
            parse_ocds_package(
                _page_payload("r1"), year=2026, month=8, range_start=0, range_end=0
            )
        ],
        source_reported_total=1,
        materialized_at_utc="2026-08-01T00:00:00Z",
    )
    fp_month_alt = acquisition_source_fingerprint(
        source_kind=month_a.query.source_kind,
        query_identity=query_identity_from_model(month_a.query),
        pages=list(month_a.pages),
        completeness_status=month_a.completeness_status,
        source_reported_total=99,
    )
    assert month_a.source_fingerprint != fp_month_alt


def test_snapshot_manifest_regression_matrix() -> None:
    summary = _load("ticket_summary_list.json")
    detail = _load("ticket_detail_items.json")
    ocds = _load("ocds_single_release.json")

    def _assert_safe(man: dict) -> None:
        blob = json.dumps(man).lower()
        assert "api_key" not in blob
        assert "password" not in blob
        assert "/home/" not in blob
        assert man.get("ticket_used_for_request") is False
        assert man.get("ticket_persisted") is False
        assert "ticket_value" not in man
        assert man["source_fingerprint"]
        assert man["normalized_semantic_digest"]
        for page in man.get("page_diagnostics", []):
            envelope_blob = json.dumps(page.get("envelope_meta", {})).casefold()
            assert "ticket" not in envelope_blob
            assert "ticket=" not in envelope_blob
            err = page.get("error_message")
            if err is not None:
                err_l = str(err).casefold()
                assert "ticket=" not in err_l
                assert "?ticket" not in err_l
                assert "authorization" not in err_l

    snap_a = build_acquisition_snapshot(
        source_kind="ticket_summary",
        payload=summary,
        fixture_origin="synthetic_official_shape",
        materialized_at_utc="2026-08-01T00:00:00Z",
    )
    man_a = snapshot_manifest(snap_a, ticket_configured=False)
    assert man_a["source_reported_total"] == summary["Cantidad"]
    assert man_a["completeness_status"] == "complete"
    _assert_safe(man_a)

    snap_b = build_acquisition_snapshot(
        source_kind="ticket_detail",
        payload=detail,
        fixture_origin="synthetic_official_shape",
        tender_code="9999-1-LE26",
        materialized_at_utc="2026-08-01T00:00:00Z",
    )
    man_b = snapshot_manifest(snap_b, ticket_configured=False)
    assert man_b["source_reported_total"] == snap_b.source_reported_total
    assert man_b["completeness_status"] == "complete"
    _assert_safe(man_b)

    snap_page = build_acquisition_snapshot(
        source_kind="ocds",
        payload=ocds,
        fixture_origin="synthetic_official_shape",
        year=2026,
        month=8,
        range_start=1,
        range_end=1,
        materialized_at_utc="2026-08-01T00:00:00Z",
    )
    man_page = snapshot_manifest(snap_page, ticket_configured=False)
    assert man_page["source_reported_total"] is None
    assert man_page["completeness_status"] == "complete"
    _assert_safe(man_page)

    p1 = parse_ocds_package(
        _page_payload("r1"), year=2026, month=8, range_start=0, range_end=0
    )
    p2 = parse_ocds_package(
        _page_payload("r2", "9999-2-LE26"), year=2026, month=8, range_start=1, range_end=1
    )
    multi = build_ocds_month_snapshot(
        planned_ranges=plan_ocds_ranges(
            year=2026, month=8, source_reported_total=2, page_size=1
        ),
        parsed_pages=[p1, p2],
        source_reported_total=2,
        materialized_at_utc="2026-08-01T00:00:00Z",
    )
    man_multi = snapshot_manifest(multi, ticket_configured=False)
    assert man_multi["source_reported_total"] == 2
    assert man_multi["completeness_status"] == "complete"
    assert man_multi["page_count"] == 2
    _assert_safe(man_multi)

    zero = build_ocds_month_snapshot(
        planned_ranges=[],
        parsed_pages=[],
        source_reported_total=0,
        year=2026,
        month=8,
        materialized_at_utc="2026-08-01T00:00:00Z",
    )
    man_zero = snapshot_manifest(zero, ticket_configured=False)
    assert man_zero["source_reported_total"] == 0
    assert man_zero["completeness_status"] == "complete"
    assert man_zero["page_count"] == 0
    _assert_safe(man_zero)

    partial_page = parse_ocds_package(
        {
            "releases": [
                {
                    "ocid": "o1",
                    "id": "r1",
                    "tender": {"id": "9999-1-LE26", "status": "active"},
                },
                "bad",
            ]
        },
        year=2026,
        month=8,
        range_start=0,
        range_end=0,
    )
    partial_month = build_ocds_month_snapshot(
        planned_ranges=[{"year": 2026, "month": 8, "start": 0, "end": 0}],
        parsed_pages=[partial_page],
        source_reported_total=1,
        materialized_at_utc="2026-08-01T00:00:00Z",
    )
    man_partial = snapshot_manifest(partial_month, ticket_configured=False)
    assert man_partial["source_reported_total"] == 1
    assert man_partial["completeness_status"] == "partial_page_failure"
    _assert_safe(man_partial)

    term = build_ocds_month_snapshot(
        planned_ranges=[
            {"year": 2026, "month": 8, "start": 0, "end": 0},
            {"year": 2026, "month": 8, "start": 1, "end": 1},
        ],
        parsed_pages=[
            parse_ocds_package(
                _page_payload("r1"), year=2026, month=8, range_start=0, range_end=0
            ),
            parse_ocds_package(
                {"releases": []}, year=2026, month=8, range_start=1, range_end=1
            ),
        ],
        source_reported_total=1,
        materialized_at_utc="2026-08-01T00:00:00Z",
    )
    man_term = snapshot_manifest(term, ticket_configured=False)
    assert man_term["source_reported_total"] == 1
    assert man_term["completeness_status"] == "terminal_empty_page"
    _assert_safe(man_term)

    term2 = build_ocds_month_snapshot(
        planned_ranges=[
            {"year": 2026, "month": 8, "start": 0, "end": 0},
            {"year": 2026, "month": 8, "start": 1, "end": 1},
        ],
        parsed_pages=[
            parse_ocds_package(
                _page_payload("r1"), year=2026, month=8, range_start=0, range_end=0
            ),
            parse_ocds_package(
                {"releases": []}, year=2026, month=8, range_start=1, range_end=1
            ),
        ],
        source_reported_total=1,
        materialized_at_utc="2099-01-01T00:00:00Z",
    )
    assert term.source_fingerprint == term2.source_fingerprint
    assert (
        snapshot_manifest(term, ticket_configured=False)["source_fingerprint"]
        == snapshot_manifest(term2, ticket_configured=False)["source_fingerprint"]
    )


# --- PR3: procurement_method / procurement_method_details from Tipo/TipoConvocatoria ---


def _detail_with_tipo(*, tipo: str | None, tipo_convocatoria: str | None) -> dict:
    base = _load("ticket_detail_items.json")
    payload = copy.deepcopy(base)
    lic = payload["Listado"][0]
    if tipo is None:
        lic.pop("Tipo", None)
    else:
        lic["Tipo"] = tipo
    if tipo_convocatoria is None:
        lic.pop("TipoConvocatoria", None)
    else:
        lic["TipoConvocatoria"] = tipo_convocatoria
    return payload


def test_ticket_parser_preserves_tipo_and_tipo_convocatoria() -> None:
    """Required regression 1: exact CO/0, LP/1, LE/1 preservation + provenance."""
    for tipo, convocatoria in (("CO", "0"), ("LP", "1"), ("LE", "1")):
        payload = _detail_with_tipo(tipo=tipo, tipo_convocatoria=convocatoria)
        _q, _p, _s, tenders, _lines, _diag = parse_ticket_detail_payload(
            payload, tender_code="9999-1-LE26"
        )
        assert len(tenders) == 1
        tender = tenders[0]
        assert tender.procurement_method == tipo
        assert tender.procurement_method_details == convocatoria
        assert tender.field_provenance["procurement_method"] == "Tipo"
        assert tender.field_provenance["procurement_method_details"] == "TipoConvocatoria"


def test_ticket_parser_missing_tipo_stays_none() -> None:
    """Required regression 2: missing Tipo/TipoConvocatoria remains None, not ''."""
    payload = _detail_with_tipo(tipo=None, tipo_convocatoria=None)
    _q, _p, _s, tenders, _lines, _diag = parse_ticket_detail_payload(
        payload, tender_code="9999-1-LE26"
    )
    assert len(tenders) == 1
    tender = tenders[0]
    assert tender.procurement_method is None
    assert tender.procurement_method_details is None


def test_procurement_method_participates_deterministically_in_semantic_digest() -> None:
    """Required regression 3: procurement fields participate in the digest, and
    the digest is a pure function of content (order-independent), not of
    incidental construction order.
    """
    payload_co = _detail_with_tipo(tipo="CO", tipo_convocatoria="0")
    payload_lp = _detail_with_tipo(tipo="LP", tipo_convocatoria="1")

    def _digest(payload: dict) -> str:
        _q, _p, sources, tenders, lines, _diag = parse_ticket_detail_payload(
            payload, tender_code="9999-1-LE26"
        )
        return acquisition_normalized_semantic_digest(
            source_observations=sources,
            tender_observations=tenders,
            line_observations=lines,
            parser_version="test-parser",
            contract_version="test-contract",
        )

    digest_co = _digest(payload_co)
    digest_lp = _digest(payload_lp)
    assert digest_co != digest_lp, (
        "changing procurement_method must change the semantic digest"
    )

    # Reversing/reordering source+tender+line collections must not change the
    # digest: acquisition_normalized_semantic_digest sorts internally.
    _q, _p, sources, tenders, lines, _diag = parse_ticket_detail_payload(
        payload_co, tender_code="9999-1-LE26"
    )
    forward = acquisition_normalized_semantic_digest(
        source_observations=sources,
        tender_observations=tenders,
        line_observations=lines,
        parser_version="test-parser",
        contract_version="test-contract",
    )
    reversed_digest = acquisition_normalized_semantic_digest(
        source_observations=list(reversed(sources)),
        tender_observations=list(reversed(tenders)),
        line_observations=list(reversed(lines)),
        parser_version="test-parser",
        contract_version="test-contract",
    )
    assert forward == reversed_digest
