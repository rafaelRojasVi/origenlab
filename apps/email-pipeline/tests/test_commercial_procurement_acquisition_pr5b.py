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
    payload_digests,
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
        build_ocds_query(year=2026, month=13, range_start=1, range_end=1)
    with pytest.raises(OcdsParseError):
        build_ocds_query(year=2026, month=1, range_start=0, range_end=1)
    with pytest.raises(OcdsParseError):
        build_ocds_query(year=2026, month=1, range_start=5, range_end=4)
    with pytest.raises(OcdsParseError):
        build_ocds_query(year=2026, month=1, range_start=1, range_end=1001)


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
    run, children = build_partial_detail_run(
        summary_payload=summary,
        detail_success_payload=detail,
        detail_failure_code="9999-2-LE26",
        detail_failure_error="fail wording A",
        fixture_origin="synthetic_official_shape",
        materialized_at_utc="2026-08-01T00:00:00Z",
    )
    assert run.run_completeness == "partial_detail_failure"
    assert run.completed_detail_count == 1
    assert run.failed_detail_count == 1
    q_a = children["detail_a"].query.acquisition_query_id
    q_b = run.detail_attempts[1].query.acquisition_query_id
    assert q_a != q_b
    assert run.detail_attempts[1].query.tender_code == "9999-2-le26"
    assert build_ticket_detail_query(tender_code="9999-2-LE26").acquisition_query_id == q_b
    run2, _ = build_partial_detail_run(
        summary_payload=summary,
        detail_success_payload=detail,
        detail_failure_code="9999-2-LE26",
        detail_failure_error="different sanitized wording",
        fixture_origin="synthetic_official_shape",
        materialized_at_utc="2099-01-01T00:00:00Z",
    )
    # Failure payload identity (codigo + status) drives fingerprint; error text separate.
    assert (
        run.detail_attempts[1].page_id is not None
        and run2.detail_attempts[1].page_id is not None
    )


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
        {"year": 2026, "month": 1, "start": 1, "end": 1000},
        {"year": 2026, "month": 1, "start": 1001, "end": 1001},
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
            {"start": 1, "end": 1000},
            {"start": 1, "end": 1000},
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
        _page_payload("r1", "9999-1-LE26"), year=2026, month=8, range_start=1, range_end=1
    )
    p2 = parse_ocds_package(
        _page_payload("r2", "9999-2-LE26"), year=2026, month=8, range_start=2, range_end=2
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
        {"year": 2026, "month": 8, "start": 1, "end": 1},
        {"year": 2026, "month": 8, "start": 2, "end": 2},
        {"year": 2026, "month": 8, "start": 3, "end": 3},
    ]
    p1 = parse_ocds_package(
        _page_payload("r1"), year=2026, month=8, range_start=1, range_end=1
    )
    empty = parse_ocds_package(
        _load("ocds_empty_page.json"), year=2026, month=8, range_start=2, range_end=2
    )
    p3 = parse_ocds_package(
        _page_payload("r3", "9999-3-LE26"), year=2026, month=8, range_start=3, range_end=3
    )
    mid = build_ocds_month_snapshot(
        planned_ranges=planned,
        parsed_pages=[p1, empty, p3],
        source_reported_total=2,
    )
    assert mid.completeness_status == "incomplete_range"

    # Valid terminal empty: filled page then empty beyond total.
    planned2 = [
        {"year": 2026, "month": 8, "start": 1, "end": 1},
        {"year": 2026, "month": 8, "start": 2, "end": 2},
    ]
    term = build_ocds_month_snapshot(
        planned_ranges=planned2,
        parsed_pages=[
            parse_ocds_package(
                _page_payload("r1"), year=2026, month=8, range_start=1, range_end=1
            ),
            parse_ocds_package(
                _load("ocds_empty_page.json"),
                year=2026,
                month=8,
                range_start=2,
                range_end=2,
            ),
        ],
        source_reported_total=1,
    )
    assert term.completeness_status == "terminal_empty_page"


def test_ocds_month_missing_duplicate_overlap() -> None:
    planned = plan_ocds_ranges(year=2026, month=8, source_reported_total=2, page_size=1)
    p1 = parse_ocds_package(
        _page_payload("r1"), year=2026, month=8, range_start=1, range_end=1
    )
    missing = build_ocds_month_snapshot(
        planned_ranges=planned, parsed_pages=[p1], source_reported_total=2
    )
    assert missing.completeness_status == "incomplete_range"

    dup = build_ocds_month_snapshot(
        planned_ranges=planned,
        parsed_pages=[p1, p1],
        source_reported_total=2,
    )
    assert dup.completeness_status == "duplicate_page"

    overlap_pages = [
        parse_ocds_package(
            _page_payload("r1"), year=2026, month=8, range_start=1, range_end=2
        ),
        parse_ocds_package(
            _page_payload("r2", "9999-2-LE26"),
            year=2026,
            month=8,
            range_start=2,
            range_end=3,
        ),
    ]
    overlap = build_ocds_month_snapshot(
        planned_ranges=[
            {"year": 2026, "month": 8, "start": 1, "end": 2},
            {"year": 2026, "month": 8, "start": 2, "end": 3},
        ],
        parsed_pages=overlap_pages,
        source_reported_total=3,
    )
    assert overlap.completeness_status == "overlapping_range"


def test_ocds_month_source_total_mismatch() -> None:
    planned = plan_ocds_ranges(year=2026, month=8, source_reported_total=2, page_size=1)
    pages = [
        parse_ocds_package(
            _page_payload("r1"), year=2026, month=8, range_start=1, range_end=1
        ),
        parse_ocds_package(
            _page_payload("r2", "9999-2-LE26"),
            year=2026,
            month=8,
            range_start=2,
            range_end=2,
        ),
    ]
    bad = build_ocds_month_snapshot(
        planned_ranges=planned, parsed_pages=pages, source_reported_total=99
    )
    assert bad.completeness_status == "source_total_mismatch"
    good = build_ocds_month_snapshot(
        planned_ranges=planned, parsed_pages=pages, source_reported_total=2
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
            lambda p: p["releases"][0]["tender"].__setitem__(
                "relatedProcesses",
                [{"id": "x", "identifier": "y", "title": "z", "scheme": "s"}],
            )
        )
        != baseline
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


def test_canonical_digest_length() -> None:
    assert len(canonical_json_digest({"b": 1, "a": 2})) == 64
