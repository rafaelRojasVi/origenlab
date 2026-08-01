"""PR5B acquisition parsers, fingerprints, redaction, range planner."""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from origenlab_email_pipeline.chilecompra_api import normalize_licitacion_detail_items
from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.normalize import (
    tender_observation_to_normalized_row,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.ocds import (
    detect_range_anomalies,
    parse_ocds_package,
    plan_ocds_ranges,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.redaction import (
    assert_no_ticket_leak,
    sanitize_mapping,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.snapshot import (
    build_acquisition_snapshot,
    build_partial_detail_snapshot,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.ticket_api import (
    parse_ticket_detail_payload,
    parse_ticket_summary_payload,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.walkthrough import (
    build_walkthrough_cases,
)

FIXTURES = Path(__file__).parent / "fixtures" / "commercial_procurement_acquisition"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_ticket_summary_list_envelope() -> None:
    payload = _load("ticket_summary_list.json")
    _q, page, sources, tenders, lines, diag = parse_ticket_summary_payload(payload)
    assert page.parser_status == "ok"
    assert len(sources) == 2
    assert len(tenders) == 2
    assert lines == []
    assert diag["lines_emitted"] == 0


def test_ticket_summary_nested_single() -> None:
    _q, page, sources, tenders, _lines, _ = parse_ticket_summary_payload(
        _load("ticket_summary_single.json")
    )
    assert page.response_item_count == 1
    assert sources[0].source_native_key == "9999-3-LE26"


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


def test_sanitize_mapping_strips_ticket_url() -> None:
    clean = sanitize_mapping(
        {
            "estado": "activas",
            "query": (
                "https://api.mercadopublico.cl/servicios/v1/publico/"
                "licitaciones.json?ticket=SECRET&estado=activas"
            ),
            "nested": {"Ticket": "SECRET", "token": "x"},
        }
    )
    blob = json.dumps(clean)
    assert "SECRET" not in blob
    assert clean.get("estado") == "activas"
    assert clean.get("endpoint_path") == "/servicios/v1/publico/licitaciones.json"


def test_ocds_single_and_multiple_releases() -> None:
    single = parse_ocds_package(_load("ocds_single_release.json"))
    assert len(single[2]) == 1
    assert single[2][0].source_status_system == "ocds"
    assert len(single[4]) == 1
    multi = parse_ocds_package(_load("ocds_multiple_releases.json"))
    assert len(multi[2]) == 2
    assert len(multi[4]) == 3


def test_ocds_empty_and_malformed() -> None:
    empty = parse_ocds_package(_load("ocds_empty_page.json"))
    assert empty[1].completeness_status == "terminal_empty_page"
    mal = parse_ocds_package(_load("ocds_malformed_shape.json"))
    assert mal[1].response_item_count == 0


def test_ocds_range_planner() -> None:
    assert plan_ocds_ranges(year=2026, month=1, source_reported_total=0) == []
    assert plan_ocds_ranges(year=2026, month=1, source_reported_total=1) == [
        {"year": 2026, "month": 1, "start": 1, "end": 1}
    ]
    assert plan_ocds_ranges(year=2026, month=1, source_reported_total=999) == [
        {"year": 2026, "month": 1, "start": 1, "end": 999}
    ]
    assert plan_ocds_ranges(year=2026, month=1, source_reported_total=1000) == [
        {"year": 2026, "month": 1, "start": 1, "end": 1000}
    ]
    assert plan_ocds_ranges(year=2026, month=1, source_reported_total=1001) == [
        {"year": 2026, "month": 1, "start": 1, "end": 1000},
        {"year": 2026, "month": 1, "start": 1001, "end": 1001},
    ]
    assert len(plan_ocds_ranges(year=2026, month=1, source_reported_total=2000)) == 2
    with pytest.raises(Exception):
        plan_ocds_ranges(year=2026, month=1, source_reported_total=-1)


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


def test_partial_vs_complete_source_fingerprint_differs() -> None:
    summary = _load("ticket_summary_list.json")
    detail = _load("ticket_detail_items.json")
    complete = build_acquisition_snapshot(
        source_kind="ticket_summary",
        payload=summary,
        fixture_origin="synthetic_official_shape",
    )
    partial = build_partial_detail_snapshot(
        summary_payload=summary,
        detail_success_payload=detail,
        detail_failure_code="9999-2-LE26",
        detail_failure_error="fail",
        fixture_origin="synthetic_official_shape",
    )
    assert partial.completeness_status == "partial_detail_failure"
    assert partial.source_fingerprint != complete.source_fingerprint
    assert partial.diagnostics["false_complete"] is False


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


def test_walkthrough_bundle_no_ticket() -> None:
    bundle = build_walkthrough_cases(FIXTURES)
    blob = json.dumps(bundle)
    assert_no_ticket_leak(blob)
    assert bundle["authenticated_request_performed"] is False
    assert bundle["cases"]["B"]["compatibility"]["codigo_match"] is True
    assert bundle["cases"]["E"]["coalesced"] is False


def test_canonical_digest_length() -> None:
    assert len(canonical_json_digest({"b": 1, "a": 2})) == 64
