"""GET /operator/procurement/* — institution-prospect read model (file-backed)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from origenlab_api.main import create_app
from origenlab_api.settings import Settings, get_settings

_CONTRACT_VERSION = "institution_prospect_contract_v4"
_SAG_TENDER_CODE = "745712-19-LP26"
_ISP_TENDER_CODE = "1093303-5-CO26"

_OPERATOR_QUEUE_NAMES = (
    "current_opportunity_queue",
    "historical_prospect_queue",
    "institution_match_review_queue",
    "contact_gap_queue",
    "line_evidence_review_queue",
    "retender_review_queue",
)

_EMPTY_QUEUE_HEADERS = {
    "current_opportunity_queue": [
        "queue_row_id", "queue", "institution_id", "display_name", "tender_code",
        "coalesced_tender_id", "equipment_category", "lifecycle_class",
        "review_disposition", "commercial_signal_type", "catalog_fit_status",
        "catalog_match_status", "opportunity_urgency_band", "prospect_strength_band",
        "line_evidence_unit_count", "reason_codes", "eligibility_reason_codes",
        "contact_authorization", "outreach_authorization", "closing_soon_bucket",
        "publication_timestamp", "close_timestamp",
    ],
    "historical_prospect_queue": [
        "queue_row_id", "queue", "institution_id", "display_name", "equipment_category",
        "commercial_signal_type", "tender_count", "tender_codes", "first_observed_date",
        "most_recent_observed_date", "review_dispositions", "prospect_strength_band",
        "contact_authorization", "outreach_authorization",
    ],
    "institution_match_review_queue": [
        "queue_row_id", "queue", "institution_id", "display_name",
        "institution_review_cluster_id", "cluster_resolution_status",
        "member_profile_ids", "identifier_conflicts", "cluster_reason_codes",
        "account_resolution_status", "account_resolution_reason", "identity_kind",
        "identity_review_required", "operator_next_action", "confirmed_account",
        "contact_authorization", "outreach_authorization",
    ],
    "contact_gap_queue": [
        "queue_row_id", "queue", "institution_id", "display_name", "contact_gap_status",
        "contact_resolution_status", "account_resolution_reason", "prospect_strength_band",
        "prospect_strength_score", "opportunity_urgency_band", "known_contact_count",
        "suitable_contact_count", "verified_contact_count",
        "equipment_purchase_tender_count", "queue_entry_reason", "operator_next_action",
        "contact_authorization", "outreach_authorization",
    ],
    "line_evidence_review_queue": [
        "queue_row_id", "queue", "institution_id", "display_name", "tender_code",
        "coalesced_tender_id", "unit_id", "unit_decision_id", "relevance_class",
        "equipment_scopes", "canonical_equipment_classes", "line_disposition",
        "line_reason_codes", "ambiguity_reason_codes", "contact_authorization",
        "outreach_authorization",
    ],
    "retender_review_queue": [
        "queue_row_id", "queue", "family_id", "buyer_key", "member_tender_codes",
        "raw_tender_count", "confirmed_same_event_family_count",
        "independent_event_count_lower_bound", "independent_event_count_upper_bound",
        "unresolved_relationship_count", "recurrence_status", "family_resolution_status",
        "family_reason_codes", "relationship_edges", "contact_authorization",
        "outreach_authorization",
    ],
}


def _write_csv(path: Path, header: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore", restval="")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _profile(institution_id: str, display_name: str) -> dict[str, object]:
    return {
        "institution_id": institution_id,
        "identity": {"display_name": display_name, "normalized_name": display_name.lower()},
        "account_contact_overlay": {
            "contact_authorization": False,
            "outreach_authorization": False,
        },
        "axes": {"prospect_strength": {"band": "high"}},
        "equipment_history": [],
        "current_opportunities": [],
        "historical_signals": [],
        "counts": {"tender_count": 1},
        "operator_next_action": "quote_now",
        "contact_authorization": False,
        "outreach_authorization": False,
        "not_persisted": True,
    }


def _current_opportunity_row(
    *, institution_id: str, display_name: str, tender_code: str, equipment_category: str,
    eligibility_reason_codes: list[str],
) -> dict[str, object]:
    return {
        "queue_row_id": f"row-{tender_code}-{equipment_category}",
        "queue": "current_opportunity_queue",
        "institution_id": institution_id,
        "display_name": display_name,
        "tender_code": tender_code,
        "coalesced_tender_id": f"t-{tender_code}",
        "equipment_category": equipment_category,
        "lifecycle_class": "active_open",
        "review_disposition": "catalog_fit",
        "commercial_signal_type": "equipment_purchase_signal",
        "catalog_fit_status": "catalog_fit_candidate",
        "catalog_match_status": "exact_product",
        "opportunity_urgency_band": "high",
        "prospect_strength_band": "high",
        "line_evidence_unit_count": "1",
        "reason_codes": json.dumps([]),
        "eligibility_reason_codes": json.dumps(eligibility_reason_codes),
        "contact_authorization": "False",
        "outreach_authorization": "False",
        "closing_soon_bucket": "this_week",
        "publication_timestamp": "2026-08-01T00:00:00Z",
        "close_timestamp": "2026-08-20T00:00:00Z",
    }


def _write_bundle(
    dest: Path,
    *,
    contract_version: str = _CONTRACT_VERSION,
    as_of_utc: str = "2026-08-14T17:01:11Z",
    current_opportunity_rows: list[dict[str, object]] | None = None,
) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    profiles = [_profile("inst-sag", "SAG"), _profile("inst-isp", "ISP")]
    packet = {
        "ok": True,
        "as_of_utc": as_of_utc,
        "run_context": "production_dry_run",
        "planner_version": "procurement_institution_prospect_planner_v4",
        "recognition_layer_version": "procurement_prospect_recognition_pr5e2_v1",
        "contract_version": contract_version,
        "not_persisted": True,
        "contact_authorization": False,
        "outreach_authorization": False,
        "profiles": profiles,
        "counts": {"institution_count": 2},
        "fingerprints": {"build_fingerprint": "digest-abc123"},
    }
    (dest / "institution_prospect_packet.json").write_text(json.dumps(packet), encoding="utf-8")

    rows = current_opportunity_rows
    if rows is None:
        rows = [
            _current_opportunity_row(
                institution_id="inst-sag",
                display_name="SAG",
                tender_code=_SAG_TENDER_CODE,
                equipment_category="balance",
                eligibility_reason_codes=[],
            ),
        ]
    sizes = {name: 0 for name in _OPERATOR_QUEUE_NAMES}
    sizes["current_opportunity_queue"] = len(rows)
    summary = {"ok": True, "contract_version": contract_version, "operator_queue_sizes": sizes}
    (dest / "summary.json").write_text(json.dumps(summary), encoding="utf-8")

    for queue_name in _OPERATOR_QUEUE_NAMES:
        header = _EMPTY_QUEUE_HEADERS[queue_name]
        queue_rows = rows if queue_name == "current_opportunity_queue" else []
        _write_csv(dest / f"{queue_name}.csv", header, queue_rows)


def _client(tmp_path: Path, *, with_bundle: bool = True, **bundle_kwargs) -> TestClient:
    dest = tmp_path / "institution_prospects"
    if with_bundle:
        _write_bundle(dest, **bundle_kwargs)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(institution_prospect_dir=dest)
    return TestClient(app)


# --- status ---


def test_status_returns_200_and_healthy_meta(tmp_path: Path) -> None:
    client = _client(tmp_path)
    r = client.get("/operator/procurement/status")
    assert r.status_code == 200
    data = r.json()
    assert data["meta"]["data_source"] == "institution_prospect_read_model"
    assert data["meta"]["reduced_mode"] is False
    assert data["meta"]["contract_version"] == _CONTRACT_VERSION
    assert data["meta"]["supported_contract_version"] is True
    assert data["meta"]["contact_authorization"] is False
    assert data["meta"]["outreach_authorization"] is False
    assert data["meta"]["source_digest"] == "digest-abc123"
    assert "/home/" not in data["meta"]["source_path"]
    assert data["operator_queue_sizes"]["current_opportunity_queue"] == 1
    assert data["summary_ok"] is True


def test_status_missing_bundle_is_reduced_mode_not_500(tmp_path: Path) -> None:
    client = _client(tmp_path, with_bundle=False)
    r = client.get("/operator/procurement/status")
    assert r.status_code == 200
    data = r.json()
    assert data["meta"]["reduced_mode"] is True
    assert data["meta"]["canonical_reason"] == "missing_institution_prospect_packet"
    assert "no published" in data["meta"]["note"].lower()
    assert data["operator_queue_sizes"] == {}


def test_status_malformed_packet_is_reduced_mode(tmp_path: Path) -> None:
    dest = tmp_path / "institution_prospects"
    _write_bundle(dest)
    (dest / "institution_prospect_packet.json").write_text("{broken", encoding="utf-8")
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(institution_prospect_dir=dest)
    client = TestClient(app)
    r = client.get("/operator/procurement/status")
    assert r.status_code == 200
    assert r.json()["meta"]["canonical_reason"] == "malformed_institution_prospect_packet"


def test_status_unsupported_contract_version_is_reduced_mode(tmp_path: Path) -> None:
    client = _client(tmp_path, contract_version="institution_prospect_contract_v999")
    r = client.get("/operator/procurement/status")
    data = r.json()
    assert data["meta"]["reduced_mode"] is True
    assert data["meta"]["canonical_reason"] == "unsupported_contract_version"
    assert data["meta"]["contract_version"] == "institution_prospect_contract_v999"


def test_status_stale_as_of_is_flagged(tmp_path: Path) -> None:
    client = _client(tmp_path, as_of_utc="2000-01-01T00:00:00Z")
    r = client.get("/operator/procurement/status")
    data = r.json()
    assert data["meta"]["reduced_mode"] is False
    assert data["meta"]["stale"] is True


# --- institutions list ---


def test_institutions_list_returns_both_profiles(tmp_path: Path) -> None:
    client = _client(tmp_path)
    r = client.get("/operator/procurement/institutions")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 2
    assert data["count"] == 2
    assert {i["institution_id"] for i in data["items"]} == {"inst-sag", "inst-isp"}
    for item in data["items"]:
        assert item["contact_authorization"] is False
        assert item["outreach_authorization"] is False


def test_institutions_list_pagination(tmp_path: Path) -> None:
    client = _client(tmp_path)
    first = client.get("/operator/procurement/institutions?limit=1&offset=0").json()
    second = client.get("/operator/procurement/institutions?limit=1&offset=1").json()
    assert first["total"] == 2
    assert first["count"] == 1
    assert second["count"] == 1
    assert first["items"][0]["institution_id"] != second["items"][0]["institution_id"]


def test_institutions_list_search_filter(tmp_path: Path) -> None:
    client = _client(tmp_path)
    data = client.get("/operator/procurement/institutions?q=SAG").json()
    assert data["total"] == 1
    assert data["items"][0]["institution_id"] == "inst-sag"


def test_institutions_list_validates_limit(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.get("/operator/procurement/institutions?limit=0").status_code == 422
    assert client.get("/operator/procurement/institutions?limit=100000").status_code == 422


def test_institutions_list_missing_bundle_returns_empty_not_error(tmp_path: Path) -> None:
    client = _client(tmp_path, with_bundle=False)
    r = client.get("/operator/procurement/institutions")
    assert r.status_code == 200
    data = r.json()
    assert data["items"] == []
    assert data["total"] == 0
    assert data["meta"]["reduced_mode"] is True


# --- institution detail ---


def test_institution_detail_found(tmp_path: Path) -> None:
    client = _client(tmp_path)
    r = client.get("/operator/procurement/institutions/inst-sag")
    assert r.status_code == 200
    assert r.json()["item"]["institution_id"] == "inst-sag"


def test_institution_detail_not_found_on_healthy_feed_is_404(tmp_path: Path) -> None:
    client = _client(tmp_path)
    r = client.get("/operator/procurement/institutions/does-not-exist")
    assert r.status_code == 404


def test_institution_detail_missing_feed_is_degraded_not_404(tmp_path: Path) -> None:
    client = _client(tmp_path, with_bundle=False)
    r = client.get("/operator/procurement/institutions/inst-sag")
    assert r.status_code == 200
    data = r.json()
    assert data["item"] is None
    assert data["meta"]["reduced_mode"] is True


# --- queues ---


def test_queue_current_opportunity_contains_sag(tmp_path: Path) -> None:
    client = _client(tmp_path)
    r = client.get("/operator/procurement/queues/current_opportunity")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    row = data["items"][0]
    assert row["tender_code"] == _SAG_TENDER_CODE
    assert row["eligibility_reason_codes"] == []
    assert row["contact_authorization"] is False
    assert row["outreach_authorization"] is False


def test_queue_tender_code_filter_is_case_insensitive(tmp_path: Path) -> None:
    """Published queue CSVs store canonical tender codes lowercase (e.g.
    '745712-19-lp26'); a caller filtering with the human-displayed uppercase
    form must still match."""
    rows = [
        _current_opportunity_row(
            institution_id="inst-sag",
            display_name="SAG",
            tender_code=_SAG_TENDER_CODE.lower(),
            equipment_category="balance",
            eligibility_reason_codes=[],
        ),
    ]
    client = _client(tmp_path, current_opportunity_rows=rows)
    data = client.get(
        f"/operator/procurement/queues/current_opportunity?tender_code={_SAG_TENDER_CODE}"
    ).json()
    assert data["total"] == 1
    assert data["items"][0]["tender_code"] == _SAG_TENDER_CODE.lower()


def test_queue_isp_restricted_row_excluded_from_current_opportunity(tmp_path: Path) -> None:
    """Mirrors the real cached-live case: ISP (CO / restricted_invitation_unconfirmed)
    must never appear in current_opportunity_queue even though it is visible
    elsewhere (institution profile / historical context)."""
    rows = [
        _current_opportunity_row(
            institution_id="inst-sag",
            display_name="SAG",
            tender_code=_SAG_TENDER_CODE,
            equipment_category="balance",
            eligibility_reason_codes=[],
        ),
    ]
    client = _client(tmp_path, current_opportunity_rows=rows)
    data = client.get("/operator/procurement/queues/current_opportunity").json()
    tender_codes = {row["tender_code"] for row in data["items"]}
    assert _ISP_TENDER_CODE not in tender_codes
    assert _SAG_TENDER_CODE in tender_codes


def test_queue_rejects_unknown_queue_name(tmp_path: Path) -> None:
    client = _client(tmp_path)
    r = client.get("/operator/procurement/queues/not_a_real_queue")
    assert r.status_code == 422


def test_queue_filters_by_institution_id(tmp_path: Path) -> None:
    rows = [
        _current_opportunity_row(
            institution_id="inst-sag", display_name="SAG", tender_code=_SAG_TENDER_CODE,
            equipment_category="balance", eligibility_reason_codes=[],
        ),
        _current_opportunity_row(
            institution_id="inst-other", display_name="Other", tender_code="9999-1-LE26",
            equipment_category="centrifuge", eligibility_reason_codes=[],
        ),
    ]
    client = _client(tmp_path, current_opportunity_rows=rows)
    data = client.get(
        "/operator/procurement/queues/current_opportunity?institution_id=inst-sag"
    ).json()
    assert data["total"] == 1
    assert data["items"][0]["institution_id"] == "inst-sag"


def test_queue_pagination_bounded_page_size(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.get("/operator/procurement/queues/current_opportunity?limit=0").status_code == 422
    assert (
        client.get("/operator/procurement/queues/current_opportunity?limit=100000").status_code
        == 422
    )


def test_queue_missing_bundle_returns_reduced_mode_empty(tmp_path: Path) -> None:
    client = _client(tmp_path, with_bundle=False)
    r = client.get("/operator/procurement/queues/current_opportunity")
    assert r.status_code == 200
    data = r.json()
    assert data["items"] == []
    assert data["total"] == 0
    assert data["meta"]["reduced_mode"] is True


def test_all_six_queue_routes_are_reachable(tmp_path: Path) -> None:
    client = _client(tmp_path)
    for route_name in (
        "current_opportunity",
        "historical_prospect",
        "contact_gap",
        "institution_match_review",
        "line_evidence_review",
        "retender_review",
    ):
        r = client.get(f"/operator/procurement/queues/{route_name}")
        assert r.status_code == 200, route_name
        assert r.json()["queue"] == route_name
