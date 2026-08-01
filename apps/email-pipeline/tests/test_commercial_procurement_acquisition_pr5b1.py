"""PR5B.1 bounded live source-contract validation tests (offline)."""

from __future__ import annotations

import io
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.live_contract.budget import (
    RequestBudget,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.live_contract.compare import (
    build_field_type_matrix,
    compare_contracts,
    json_type_name,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.live_contract.range_semantics import (
    conclude_range_semantics,
    summarize_probe,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.live_contract.runner import (
    select_detail_codes,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.live_contract.sanitize import (
    SANITIZER_VERSION,
    assert_no_identifier_leaks,
    sanitize_live_payload,
    strip_sanitizer_meta,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.live_contract.transport import (
    BudgetedLiveTransport,
    build_ocds_probe_path,
    plan_requests,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.ocds import (
    OcdsParseError,
    build_ocds_query,
    parse_ocds_package,
    plan_ocds_ranges,
)

FIXTURES = Path(__file__).parent / "fixtures" / "commercial_procurement_acquisition"
ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "commercial" / "validate_live_procurement_source_contracts.py"


def test_build_ocds_query_allows_zero_offset_wire_path() -> None:
    q = build_ocds_query(year=2026, month=7, range_start=0, range_end=0)
    assert q.endpoint_path.endswith("/0/1")
    with pytest.raises(OcdsParseError, match="offset"):
        build_ocds_query(year=2026, month=7, range_start=-1, range_end=0)
    # Probe path builder remains isolated and allows literal 0/0 wire probe.
    assert build_ocds_probe_path(year=2026, month=7, start=0, end=0).endswith("/0/0")


def test_plan_only_zero_network(monkeypatch: pytest.MonkeyPatch) -> None:
    import runpy
    import sys

    calls: list[str] = []

    def boom(*_a, **_k):  # pragma: no cover
        calls.append("urlopen")
        raise AssertionError("network_should_not_run")

    monkeypatch.setattr(
        "origenlab_email_pipeline.commercial_procurement_acquisition.live_contract.transport.urlopen",
        boom,
    )
    monkeypatch.setattr(sys, "argv", [str(SCRIPT)])
    # Import main and run without execute flags.
    ns = runpy.run_path(str(SCRIPT), run_name="not_main")
    rc = ns["main"]([])
    assert rc == 0
    assert calls == []


def test_cli_rejects_ticket_and_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    import runpy

    ns = runpy.run_path(str(SCRIPT), run_name="not_main")
    assert ns["main"](["--ticket", "SECRET"]) == 2
    assert ns["main"](["--apply"]) == 2
    assert ns["main"](["--persist"]) == 2
    assert ns["main"](["--authenticated-request-budget", "5"]) == 2
    assert ns["main"](["--public-request-budget", "5"]) == 2


def test_budget_includes_failed_requests_no_retry() -> None:
    budget = RequestBudget(authenticated_budget_max=2, public_budget_max=1)

    class FakeResp:
        status = 500

        def read(self) -> bytes:
            return b'{"error":true}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def getcode(self):
            return 500

    def urlopen_fn(_req, timeout=30):
        return FakeResp()

    transport = BudgetedLiveTransport(
        budget, ticket="TEST-TICKET-VALUE", urlopen_fn=urlopen_fn
    )
    r1 = transport.fetch_ticket_summary()
    assert r1.http_status == 500
    assert budget.authenticated_attempted == 1
    assert budget.authenticated_completed == 0
    assert budget.no_automatic_retry is True
    # Second attempt also fails and consumes budget; no auto-retry beyond that.
    transport.fetch_ticket_summary()
    assert budget.authenticated_attempted == 2
    with pytest.raises(RuntimeError, match="budget_exhausted"):
        transport.fetch_ticket_summary()
    ledger = json.dumps(budget.to_dict())
    assert "TEST-TICKET-VALUE" not in ledger
    assert "ticket=" not in ledger


def test_budget_public_cap() -> None:
    budget = RequestBudget(authenticated_budget_max=1, public_budget_max=1)

    class FakeResp:
        status = 200

        def read(self) -> bytes:
            return b'{"releases":[]}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def getcode(self):
            return 200

    transport = BudgetedLiveTransport(budget, ticket="T", urlopen_fn=lambda *a, **k: FakeResp())
    transport.fetch_ocds_probe(year=2026, month=7, start=1, end=1)
    with pytest.raises(RuntimeError, match="budget_exhausted"):
        transport.fetch_ocds_probe(year=2026, month=7, start=0, end=0)


def test_sanitize_deterministic_and_leak_free() -> None:
    payload = {
        "Cantidad": 1,
        "Listado": [
            {
                "CodigoExterno": "1234-5-LE26",
                "Nombre": "Real Buyer SA",
                "Email": "buyer@example.com",
                "Fono": "+56 9 1234 5678",
                "RutUnidad": "76.123.456-7",
                "Url": "https://api.mercadopublico.cl/doc?ticket=SECRET",
                "Items": {"Cantidad": 1, "Listado": [{"Descripcion": "HPLC"}]},
            }
        ],
    }
    a = sanitize_live_payload(payload, source_kind="ticket_summary", seed="s")
    b = sanitize_live_payload(payload, source_kind="ticket_summary", seed="s")
    assert strip_sanitizer_meta(a) == strip_sanitizer_meta(b)
    body = strip_sanitizer_meta(a)
    assert body["Cantidad"] == 1
    assert isinstance(body["Listado"], list)
    assert body["Listado"][0]["CodigoExterno"] != "1234-5-LE26"
    assert "Real Buyer SA" not in json.dumps(body)
    assert "buyer@example.com" not in json.dumps(body)
    assert "SECRET" not in json.dumps(body)
    assert "ticket=" not in json.dumps(body).casefold()
    assert_no_identifier_leaks(
        body,
        forbidden_substrings=["1234-5-LE26", "Real Buyer SA", "SECRET", "ticket=SECRET"],
    )
    assert a["_sanitizer_meta"]["sanitizer_version"] == SANITIZER_VERSION


def test_contract_compare_field_paths() -> None:
    live = {"Cantidad": 2, "Listado": [{"CodigoExterno": "x"}], "Extra": None}
    ref = json.loads((FIXTURES / "ticket_summary_list.json").read_text())
    live_m = build_field_type_matrix(live)
    ref_m = build_field_type_matrix(ref)
    assert json_type_name([]) == "array"
    assert json_type_name(None) == "null"
    diffs = compare_contracts(live_matrix=live_m, reference_matrix=ref_m)
    assert diffs == sorted(diffs, key=lambda d: d["path"])
    paths = {d["path"] for d in diffs}
    assert "$.Cantidad" in paths
    assert "$.Extra" in paths


def test_range_semantics_one_based_inclusive() -> None:
    probes = [
        summarize_probe(
            label="0_0",
            start=0,
            end=0,
            http_status=200,
            payload={"releases": []},
            error_classification=None,
        ),
        summarize_probe(
            label="1_1",
            start=1,
            end=1,
            http_status=200,
            payload={"releases": [{"ocid": "a", "id": "1"}]},
            error_classification=None,
        ),
        summarize_probe(
            label="0_9",
            start=0,
            end=9,
            http_status=200,
            payload={"releases": []},
            error_classification=None,
        ),
        summarize_probe(
            label="1_10",
            start=1,
            end=10,
            http_status=200,
            payload={"releases": [{"ocid": f"o{i}", "id": str(i)} for i in range(10)]},
            error_classification=None,
        ),
    ]
    result = conclude_range_semantics(probes)
    assert result["conclusion"] == "one_based_end_inclusive"
    assert result["pr5b_correction_required"] is False
    assert result["width_interpretation"] == "end - start + 1"


def test_range_semantics_zero_based_offset_limit_requires_correction() -> None:
    probes = [
        summarize_probe(
            label="0_0",
            start=0,
            end=0,
            http_status=200,
            payload={"status": 404, "detail": "No se encontraron resultados."},
            error_classification=None,
        ),
        summarize_probe(
            label="1_1",
            start=1,
            end=1,
            http_status=200,
            payload={
                "pagination": {"offset": 1, "limit": 1, "total": 100},
                "data": [{"ocid": "ocds-70d2nz-a"}],
            },
            error_classification=None,
        ),
        summarize_probe(
            label="0_9",
            start=0,
            end=9,
            http_status=200,
            payload={
                "pagination": {"offset": 0, "limit": 9, "total": 100},
                "data": [{"ocid": f"ocds-70d2nz-{i}"} for i in range(9)],
            },
            error_classification=None,
        ),
        summarize_probe(
            label="1_10",
            start=1,
            end=10,
            http_status=200,
            payload={
                "pagination": {"offset": 1, "limit": 10, "total": 100},
                "data": [{"ocid": f"ocds-70d2nz-{i}"} for i in range(10)],
            },
            error_classification=None,
        ),
    ]
    result = conclude_range_semantics(probes)
    assert result["conclusion"] == "zero_based_offset_limit"
    assert result["pr5b_correction_required"] is True
    assert "limit" in result["width_interpretation"]


def test_range_semantics_endpoint_rejected_zero() -> None:
    probes = [
        summarize_probe(
            label="0_0",
            start=0,
            end=0,
            http_status=400,
            payload=None,
            error_classification="http_400",
        ),
        summarize_probe(
            label="1_1",
            start=1,
            end=1,
            http_status=200,
            payload={"releases": [{"ocid": "a", "id": "1"}]},
            error_classification=None,
        ),
        summarize_probe(
            label="0_9",
            start=0,
            end=9,
            http_status=400,
            payload=None,
            error_classification="http_400",
        ),
        summarize_probe(
            label="1_10",
            start=1,
            end=10,
            http_status=200,
            payload={"releases": [{"ocid": f"o{i}", "id": str(i)} for i in range(10)]},
            error_classification=None,
        ),
    ]
    result = conclude_range_semantics(probes)
    assert result["conclusion"] == "endpoint_rejected_zero"
    assert result["pr5b_correction_required"] is False


def test_select_detail_codes_deterministic() -> None:
    summary = {
        "Listado": [
            {"CodigoExterno": "2222-1-LE26"},
            {"CodigoExterno": "1111-1-LE26"},
            {"CodigoExterno": "1111-1-LE26"},
            {"CodigoExterno": "bad"},
            {"CodigoExterno": None},
            {"CodigoExterno": "3333-1-LE26"},
        ]
    }
    assert select_detail_codes(summary, limit=3) == [
        "1111-1-le26",
        "2222-1-le26",
        "3333-1-le26",
    ]
    assert select_detail_codes({"Listado": []}, limit=3) == []


def test_plan_requests_shape() -> None:
    plan = plan_requests(
        ticket_summary_limit_details=3,
        authenticated_request_budget=4,
        public_request_budget=4,
    )
    assert plan["mode"] == "plan_only_no_network"
    assert len(plan["planned_authenticated"]) == 4  # 1 summary + 3 details
    assert len(plan["planned_public"]) == 4
    blob = json.dumps(plan)
    assert "ticket=" not in blob
    assert "CHILECOMPRA" not in blob


def test_http_error_consumes_budget_without_retry() -> None:
    budget = RequestBudget(authenticated_budget_max=1, public_budget_max=0)

    def urlopen_fn(req, timeout=30):
        raise HTTPError(req.full_url, 429, "rate", hdrs=None, fp=io.BytesIO(b"{}"))

    transport = BudgetedLiveTransport(
        budget, ticket="SECRET-TICKET", urlopen_fn=urlopen_fn
    )
    result = transport.fetch_ticket_summary()
    assert result.http_status == 429
    assert budget.authenticated_attempted == 1
    assert "SECRET-TICKET" not in json.dumps(budget.to_dict())
    with pytest.raises(RuntimeError):
        transport.fetch_ticket_summary()


def test_fixture_digest_stable_for_sanitizer() -> None:
    payload = json.loads((FIXTURES / "ticket_summary_list.json").read_text())
    a = strip_sanitizer_meta(sanitize_live_payload(payload, source_kind="ticket_summary"))
    b = strip_sanitizer_meta(sanitize_live_payload(payload, source_kind="ticket_summary"))
    assert canonical_json_digest(a) == canonical_json_digest(b)


LIVE_CONTRACT = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "commercial_procurement_acquisition_live_contract"
)


def test_committed_live_fixtures_origin_and_no_leaks() -> None:
    origin = json.loads((LIVE_CONTRACT / "FIXTURE_ORIGIN.json").read_text())
    assert origin["origin"] == "live_response_sanitized"
    assert origin["range_conclusion"] == "zero_based_offset_limit"
    assert origin["pr5b_correction_required"] is True
    assert origin["no_ticket_retained"] is True
    assert (
        origin["contract_versions_after_correction"]["ocds_query_contract"]
        == "acquisition_query_v2"
    )
    assert (
        origin["contract_versions_after_correction"]["ticket_query_contract"]
        == "acquisition_query_v1"
    )
    forbidden = [
        "ticket=",
        "@",
        "/home/",
        "CHILECOMPRA_API_TICKET",
        "ocds-70d2nz-",  # live ocid prefix must not remain
    ]
    for path in sorted(LIVE_CONTRACT.glob("*.json")):
        blob = path.read_text()
        payload = json.loads(blob)
        digest = canonical_json_digest(payload)
        if path.name != "FIXTURE_ORIGIN.json":
            assert origin["sanitized_fixture_digests"][path.stem] == digest
        for token in forbidden:
            assert token not in blob
        assert_no_identifier_leaks(payload, forbidden_substrings=["SECRET", "password"])


def test_live_ocds_fixture_parses_as_lista_index() -> None:
    payload = json.loads((LIVE_CONTRACT / "ocds_range_live_shape_v1.json").read_text())
    _q, page, sources, tenders, lines, diag = parse_ocds_package(
        payload, year=2026, month=7, range_start=1, range_end=1
    )
    assert diag.get("lista_index") is True
    assert page.completeness_status == "complete"
    assert page.response_item_count == 1
    assert page.source_reported_total == 8004
    assert len(sources) == 1
    assert len(tenders) == 1
    assert lines == []
    assert _q.query_contract_version == "acquisition_query_v2"
    assert "ocds_range_semantics" in _q.identity_payload()


def test_live_range_conclusion_reproduced_from_sanitized_probe_shapes() -> None:
    """Reproduce PR5B.1 indexing conclusion without network or raw captures."""
    listing = json.loads((LIVE_CONTRACT / "ocds_range_live_shape_v1.json").read_text())
    total = listing["pagination"]["total"]

    def listing_rows(offset: int, limit: int) -> dict:
        return {
            "creationDate": listing["creationDate"],
            "version": listing["version"],
            "pagination": {"offset": offset, "limit": limit, "total": total},
            "data": [
                {"ocid": f"ocds-synth-probe-{offset + i}", "urlTender": "<redacted_url>"}
                for i in range(limit)
            ],
        }

    probes = [
        summarize_probe(
            label="0_0",
            start=0,
            end=0,
            http_status=200,
            payload={"status": 404, "detail": "No se encontraron resultados."},
            error_classification=None,
        ),
        summarize_probe(
            label="1_1",
            start=1,
            end=1,
            http_status=200,
            payload=listing_rows(1, 1),
            error_classification=None,
        ),
        summarize_probe(
            label="0_9",
            start=0,
            end=9,
            http_status=200,
            payload=listing_rows(0, 9),
            error_classification=None,
        ),
        summarize_probe(
            label="1_10",
            start=1,
            end=10,
            http_status=200,
            payload=listing_rows(1, 10),
            error_classification=None,
        ),
    ]
    result = conclude_range_semantics(probes)
    assert result["conclusion"] == "zero_based_offset_limit"
    assert result["pr5b_correction_required"] is True
    assert result["observed_wire_contract"]["second_param"] == "limit"
    planned = plan_ocds_ranges(year=2026, month=7, source_reported_total=1001)
    assert planned[0] == {"year": 2026, "month": 7, "start": 0, "end": 999}
    assert planned[1] == {"year": 2026, "month": 7, "start": 1000, "end": 1000}
    q = build_ocds_query(year=2026, month=7, range_start=0, range_end=999)
    assert q.endpoint_path.endswith("/0/1000")
    assert q.query_contract_version == "acquisition_query_v2"
