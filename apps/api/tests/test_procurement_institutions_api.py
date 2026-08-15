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
    base_fields = list(header)
    extra_fields = sorted(
        {
            key
            for row in rows
            for key in row
            if key not in base_fields
        }
    )
    fieldnames = [*base_fields, *extra_fields]

    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=fieldnames,
            extrasaction="ignore",
            restval="",
        )
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


def _historical_prospect_row(
    *, institution_id: str, display_name: str, equipment_category: str,
    commercial_signal_type: str, tender_count: int, tender_codes: list[str],
    review_dispositions: list[str],
) -> dict[str, object]:
    """Producer: queues.build_operator_queues's historical_acc entries."""
    return {
        "queue_row_id": f"hist-{institution_id}-{equipment_category}-{commercial_signal_type}",
        "queue": "historical_prospect_queue",
        "institution_id": institution_id,
        "display_name": display_name,
        "equipment_category": equipment_category,
        "commercial_signal_type": commercial_signal_type,
        "tender_count": str(tender_count),
        "tender_codes": json.dumps(tender_codes),
        "first_observed_date": "2025-11-01",
        "most_recent_observed_date": "2026-01-19",
        "review_dispositions": json.dumps(review_dispositions),
        "prospect_strength_band": "medium",
        "contact_authorization": "False",
        "outreach_authorization": "False",
    }


def _institution_match_review_cluster_row(
    *, institution_review_cluster_id: str, cluster_resolution_status: str,
    member_profile_ids: list[str], cluster_reason_codes: list[str],
) -> dict[str, object]:
    """Producer: queues.build_operator_queues's cluster branch — institution_id
    is `cluster.get("institution_id") or ""` and display_name is never set at
    all, so both are blank/absent for a genuinely unresolved cluster."""
    return {
        "queue_row_id": f"cluster-{institution_review_cluster_id}",
        "queue": "institution_match_review_queue",
        "institution_id": "",
        "display_name": "",
        "institution_review_cluster_id": institution_review_cluster_id,
        "identity_subject_kind": "review_cluster",
        "cluster_resolution_status": cluster_resolution_status,
        "member_profile_ids": json.dumps(member_profile_ids),
        "identifier_conflicts": json.dumps([]),
        "cluster_reason_codes": json.dumps(cluster_reason_codes),
        "account_resolution_status": "",
        "account_resolution_reason": "",
        "identity_kind": "",
        "identity_review_required": "False",
        "operator_next_action": "",
        "confirmed_account": "False",
        "contact_authorization": "False",
        "outreach_authorization": "False",
    }


def _institution_match_review_profile_row(
    *, institution_id: str, display_name: str, institution_review_cluster_id: str,
    account_resolution_status: str, identity_kind: str, identity_review_required: bool,
    operator_next_action: str,
) -> dict[str, object]:
    """Producer: queues.build_operator_queues's profile branch (match_review list)."""
    return {
        "queue_row_id": f"match-{institution_id}",
        "queue": "institution_match_review_queue",
        "institution_id": institution_id,
        "display_name": display_name,
        "institution_review_cluster_id": institution_review_cluster_id,
        "identity_subject_kind": "profile",
        "cluster_resolution_status": "",
        "member_profile_ids": json.dumps([]),
        "identifier_conflicts": json.dumps([]),
        "cluster_reason_codes": json.dumps([]),
        "account_resolution_status": account_resolution_status,
        "account_resolution_reason": "pr4_constituents_present_but_unresolved",
        "identity_kind": identity_kind,
        "identity_review_required": "True" if identity_review_required else "False",
        "operator_next_action": operator_next_action,
        "confirmed_account": "False",
        "contact_authorization": "False",
        "outreach_authorization": "False",
    }


def _contact_gap_row(
    *, institution_id: str, display_name: str, contact_gap_status: str,
    contact_resolution_status: str, prospect_strength_band: str, prospect_strength_score: int,
    known_contact_count: int, suitable_contact_count: int, verified_contact_count: int,
    operator_next_action: str,
) -> dict[str, object]:
    """Producer: queues.build_operator_queues's contact_gap_rows (institution-grain)."""
    return {
        "queue_row_id": f"gap-{institution_id}",
        "queue": "contact_gap_queue",
        "institution_id": institution_id,
        "display_name": display_name,
        "contact_gap_status": contact_gap_status,
        "contact_resolution_status": contact_resolution_status,
        "account_resolution_reason": "pr4_linked_account_carried_forward",
        "prospect_strength_band": prospect_strength_band,
        "prospect_strength_score": str(prospect_strength_score),
        "opportunity_urgency_band": "high",
        "known_contact_count": str(known_contact_count),
        "suitable_contact_count": str(suitable_contact_count),
        "verified_contact_count": str(verified_contact_count),
        "equipment_purchase_tender_count": "1",
        "queue_entry_reason": "current_catalog_opportunity",
        "operator_next_action": operator_next_action,
        "contact_authorization": "False",
        "outreach_authorization": "False",
    }


def _line_evidence_review_row(
    *, institution_id: str, display_name: str, tender_code: str, coalesced_tender_id: str,
    unit_id: str, unit_decision_id: str, relevance_class: str, equipment_scopes: list[str],
    canonical_equipment_classes: list[str], line_disposition: str,
    line_reason_codes: list[str], ambiguity_reason_codes: list[str],
) -> dict[str, object]:
    """Producer: queues.build_operator_queues's line_review list (unit-grain).
    No clause_text field exists in the producer's dict literal — never invent one."""
    return {
        "queue_row_id": f"line-{coalesced_tender_id}-{unit_id}",
        "queue": "line_evidence_review_queue",
        "institution_id": institution_id,
        "display_name": display_name,
        "tender_code": tender_code,
        "coalesced_tender_id": coalesced_tender_id,
        "unit_id": unit_id,
        "unit_decision_id": unit_decision_id,
        "relevance_class": relevance_class,
        "equipment_scopes": json.dumps(equipment_scopes),
        "canonical_equipment_classes": json.dumps(canonical_equipment_classes),
        "line_disposition": line_disposition,
        "line_reason_codes": json.dumps(line_reason_codes),
        "ambiguity_reason_codes": json.dumps(ambiguity_reason_codes),
        "contact_authorization": "False",
        "outreach_authorization": "False",
    }


def _retender_review_row(
    *, family_id: str, buyer_key: str, member_tender_codes: list[str], raw_tender_count: int,
    recurrence_status: str, family_resolution_status: str, family_reason_codes: list[str],
    relationship_edges: list[dict[str, object]],
) -> dict[str, object]:
    """Producer: queues.build_operator_queues's retender_review list (family-grain).
    No institution_id/display_name field exists in the producer's dict literal
    at all — never require or fabricate one."""
    return {
        "queue_row_id": f"retender-{family_id}",
        "queue": "retender_review_queue",
        "family_id": family_id,
        "buyer_key": buyer_key,
        "member_tender_codes": json.dumps(member_tender_codes),
        "raw_tender_count": str(raw_tender_count),
        "confirmed_same_event_family_count": "0",
        "independent_event_count_lower_bound": "1",
        "independent_event_count_upper_bound": str(raw_tender_count),
        "unresolved_relationship_count": str(max(0, len(member_tender_codes) - 1)),
        "recurrence_status": recurrence_status,
        "family_resolution_status": family_resolution_status,
        "family_reason_codes": json.dumps(family_reason_codes),
        "relationship_edges": json.dumps(relationship_edges),
        "contact_authorization": "False",
        "outreach_authorization": "False",
    }


def _write_bundle(
    dest: Path,
    *,
    contract_version: str = _CONTRACT_VERSION,
    as_of_utc: str = "2026-08-14T17:01:11Z",
    queue_rows: dict[str, list[dict[str, object]]] | None = None,
) -> None:
    """``queue_rows`` maps route-internal queue name (e.g. "current_opportunity_queue")
    to the rows to publish for it; queues not present in the mapping stay empty
    (still written with a valid header, matching a genuinely empty producer queue).
    """
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

    rows_by_queue: dict[str, list[dict[str, object]]] = {name: [] for name in _OPERATOR_QUEUE_NAMES}
    if queue_rows:
        rows_by_queue.update(queue_rows)
    else:
        rows_by_queue["current_opportunity_queue"] = [
            _current_opportunity_row(
                institution_id="inst-sag",
                display_name="SAG",
                tender_code=_SAG_TENDER_CODE,
                equipment_category="balance",
                eligibility_reason_codes=[],
            ),
        ]

    sizes = {name: len(rows_by_queue[name]) for name in _OPERATOR_QUEUE_NAMES}
    summary = {"ok": True, "contract_version": contract_version, "operator_queue_sizes": sizes}
    (dest / "summary.json").write_text(json.dumps(summary), encoding="utf-8")

    for queue_name in _OPERATOR_QUEUE_NAMES:
        header = _EMPTY_QUEUE_HEADERS[queue_name]
        _write_csv(dest / f"{queue_name}.csv", header, rows_by_queue[queue_name])


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
    client = _client(tmp_path, queue_rows={"current_opportunity_queue": rows})
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
    client = _client(tmp_path, queue_rows={"current_opportunity_queue": rows})
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
    client = _client(tmp_path, queue_rows={"current_opportunity_queue": rows})
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


def test_institution_match_review_empty_queue_uses_fallback_header_without_subject_kind(
    tmp_path: Path,
) -> None:
    """An empty queue uses EMPTY_QUEUE_HEADERS exactly.

    identity_subject_kind is a real field on non-empty produced rows, but is
    intentionally absent from the producer's current empty fallback header.
    """
    dest = tmp_path / "institution_prospects"
    _write_bundle(
        dest,
        queue_rows={"institution_match_review_queue": []},
    )

    with (dest / "institution_match_review_queue.csv").open(
        "r",
        encoding="utf-8",
        newline="",
    ) as fh:
        header = next(csv.reader(fh))

    assert header == _EMPTY_QUEUE_HEADERS["institution_match_review_queue"]
    assert "identity_subject_kind" not in header


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


# --- field-level queue-shape tests (contract hardening) ---


def test_historical_prospect_queue_field_shape_rental_signal(tmp_path: Path) -> None:
    """A: historical_prospect_queue — uses a non-purchase (rental/comodato)
    fixture deliberately, so this test fails if a future change hardcodes
    equipment_purchase_signal semantics onto this queue."""
    row = _historical_prospect_row(
        institution_id="002c706c",
        display_name="CORPORACION MUNICIPAL DE FOMENTO PRODUCTIVO DE ARICA",
        equipment_category="",
        commercial_signal_type="rental_or_comodato_signal",
        tender_count=1,
        tender_codes=["1234-5-le26"],
        review_dispositions=["installed_base_or_rental"],
    )
    client = _client(tmp_path, queue_rows={"historical_prospect_queue": [row]})
    r = client.get("/operator/procurement/queues/historical_prospect")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    item = data["items"][0]
    assert item["queue"] == "historical_prospect_queue"
    assert item["institution_id"] == "002c706c"
    assert item["equipment_category"] == ""
    assert item["commercial_signal_type"] == "rental_or_comodato_signal"
    # Type/decoding: count fields are strings, list fields decode to JSON arrays.
    assert item["tender_count"] == "1"
    assert isinstance(item["tender_count"], str)
    assert item["tender_codes"] == ["1234-5-le26"]
    assert item["review_dispositions"] == ["installed_base_or_rental"]
    assert item["contact_authorization"] is False
    assert item["outreach_authorization"] is False


def test_institution_match_review_queue_cluster_row_preserves_blank_identity(tmp_path: Path) -> None:
    """B: institution_match_review_queue — a genuinely unresolved cluster row.
    The API must preserve blank institution_id/display_name rather than
    manufacturing an identity the planner never resolved."""
    row = _institution_match_review_cluster_row(
        institution_review_cluster_id="008550af43420e2d",
        cluster_resolution_status="review_only_fragmented_identity",
        member_profile_ids=["profile-a", "profile-b"],
        cluster_reason_codes=[
            "normalized_name_multi_profile_fragmentation",
            "review_only_cluster_no_auto_merge",
        ],
    )
    client = _client(tmp_path, queue_rows={"institution_match_review_queue": [row]})
    r = client.get("/operator/procurement/queues/institution_match_review")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    item = data["items"][0]
    assert item["queue"] == "institution_match_review_queue"
    assert item["identity_subject_kind"] == "review_cluster"
    # The truth is blank — asserted explicitly, not defaulted/hidden.
    assert item["institution_id"] == ""
    assert item["display_name"] == ""
    assert item["institution_review_cluster_id"] == "008550af43420e2d"
    assert item["cluster_resolution_status"] == "review_only_fragmented_identity"
    assert item["member_profile_ids"] == ["profile-a", "profile-b"]
    assert item["identifier_conflicts"] == []
    assert item["cluster_reason_codes"] == [
        "normalized_name_multi_profile_fragmentation",
        "review_only_cluster_no_auto_merge",
    ]
    assert item["identity_review_required"] is False
    assert item["operator_next_action"] == ""
    assert item["contact_authorization"] is False
    assert item["outreach_authorization"] is False


def test_institution_match_review_queue_profile_row_field_shape(tmp_path: Path) -> None:
    """B (profile-grain variant): a profile-grain review row does carry an
    institution identity, distinguishing it from the cluster-grain case above."""
    row = _institution_match_review_profile_row(
        institution_id="inst-example",
        display_name="MUNICIPALIDAD DE EJEMPLO",
        institution_review_cluster_id="cluster-example-1",
        account_resolution_status="unlinked",
        identity_kind="normalized_buyer_name",
        identity_review_required=True,
        operator_next_action="confirm_identity_match",
    )
    client = _client(tmp_path, queue_rows={"institution_match_review_queue": [row]})
    data = client.get("/operator/procurement/queues/institution_match_review").json()
    item = data["items"][0]
    assert item["identity_subject_kind"] == "profile"
    assert item["institution_id"] == "inst-example"
    assert item["display_name"] == "MUNICIPALIDAD DE EJEMPLO"
    assert item["identity_kind"] == "normalized_buyer_name"
    assert item["identity_review_required"] is True
    assert item["operator_next_action"] == "confirm_identity_match"
    assert item["contact_authorization"] is False
    assert item["outreach_authorization"] is False


def test_contact_gap_queue_field_shape(tmp_path: Path) -> None:
    """C: contact_gap_queue — institution-grain contact-readiness gap."""
    row = _contact_gap_row(
        institution_id="inst-example",
        display_name="MUNICIPALIDAD DE EJEMPLO",
        contact_gap_status="linked_account_no_contact",
        contact_resolution_status="no_contact_found",
        prospect_strength_band="high",
        prospect_strength_score=7,
        known_contact_count=0,
        suitable_contact_count=0,
        verified_contact_count=0,
        operator_next_action="research_new_contact",
    )
    client = _client(tmp_path, queue_rows={"contact_gap_queue": [row]})
    r = client.get("/operator/procurement/queues/contact_gap")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    item = data["items"][0]
    assert item["queue"] == "contact_gap_queue"
    assert item["institution_id"] == "inst-example"
    assert item["contact_gap_status"] == "linked_account_no_contact"
    assert item["contact_resolution_status"] == "no_contact_found"
    assert item["prospect_strength_band"] == "high"
    # Type/decoding: score/count fields stay strings.
    assert item["prospect_strength_score"] == "7"
    assert item["known_contact_count"] == "0"
    assert item["suitable_contact_count"] == "0"
    assert item["verified_contact_count"] == "0"
    assert item["operator_next_action"] == "research_new_contact"
    assert item["contact_authorization"] is False
    assert item["outreach_authorization"] is False


def test_line_evidence_review_queue_field_shape_and_no_fabricated_clause_text(tmp_path: Path) -> None:
    """D: line_evidence_review_queue — unit-grain evidence, and explicitly no
    clause_text field, since the published contract does not carry raw
    clause/quote text at this grain."""
    row = _line_evidence_review_row(
        institution_id="inst-example",
        display_name="MUNICIPALIDAD DE EJEMPLO",
        tender_code="1234-5-le26",
        coalesced_tender_id="coalesced_tender_example",
        unit_id="unit-1",
        unit_decision_id="decision-1",
        relevance_class="ambiguous",
        equipment_scopes=["ambiguous"],
        canonical_equipment_classes=[],
        line_disposition="review_required",
        line_reason_codes=["no_supported_explicit_pattern_match"],
        ambiguity_reason_codes=["scope_ambiguous"],
    )
    client = _client(tmp_path, queue_rows={"line_evidence_review_queue": [row]})
    r = client.get("/operator/procurement/queues/line_evidence_review")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    item = data["items"][0]
    assert item["queue"] == "line_evidence_review_queue"
    assert item["coalesced_tender_id"] == "coalesced_tender_example"
    assert item["unit_id"] == "unit-1"
    assert item["unit_decision_id"] == "decision-1"
    assert item["relevance_class"] == "ambiguous"
    assert item["equipment_scopes"] == ["ambiguous"]
    assert item["canonical_equipment_classes"] == []
    assert item["line_disposition"] == "review_required"
    assert item["line_reason_codes"] == ["no_supported_explicit_pattern_match"]
    assert item["ambiguity_reason_codes"] == ["scope_ambiguous"]
    assert item["contact_authorization"] is False
    assert item["outreach_authorization"] is False
    # No fabricated tender-level commercial-term field.
    assert "clause_text" not in item


def test_retender_review_queue_field_shape_no_institution_identity(tmp_path: Path) -> None:
    """E: retender_review_queue — family-grain, and explicitly no
    institution_id/display_name requirement (the producer's row dict never
    sets them at all for this queue)."""
    row = _retender_review_row(
        family_id="0009a395",
        buyer_key="buyer_name:i municipalidad de coquimbo",
        member_tender_codes=["2446-4-le26", "2446-16-le26"],
        raw_tender_count=2,
        recurrence_status="recurrence_not_established",
        family_resolution_status="retender_review_required",
        family_reason_codes=["independence_not_established", "retender_review_required"],
        relationship_edges=[],
    )
    client = _client(tmp_path, queue_rows={"retender_review_queue": [row]})
    r = client.get("/operator/procurement/queues/retender_review")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    item = data["items"][0]
    assert item["queue"] == "retender_review_queue"
    assert item["family_id"] == "0009a395"
    assert item["buyer_key"] == "buyer_name:i municipalidad de coquimbo"
    assert item["member_tender_codes"] == ["2446-4-le26", "2446-16-le26"]
    assert item["raw_tender_count"] == "2"
    assert item["recurrence_status"] == "recurrence_not_established"
    assert item["family_resolution_status"] == "retender_review_required"
    assert item["family_reason_codes"] == [
        "independence_not_established",
        "retender_review_required",
    ]
    assert item["relationship_edges"] == []
    assert item["contact_authorization"] is False
    assert item["outreach_authorization"] is False
    # No fabricated institution identity for this family-grain queue.
    assert "institution_id" not in item
    assert "display_name" not in item


def test_all_six_queues_field_level_rows_and_authorization_false(tmp_path: Path) -> None:
    """F: strengthens the existing all-six-reachable check — every queue
    simultaneously carries a real, non-empty representative row (not just an
    empty header), and authorization stays false everywhere at once."""
    queue_rows = {
        "current_opportunity_queue": [
            _current_opportunity_row(
                institution_id="inst-sag",
                display_name="SAG",
                tender_code=_SAG_TENDER_CODE,
                equipment_category="balance",
                eligibility_reason_codes=[],
            ),
        ],
        "historical_prospect_queue": [
            _historical_prospect_row(
                institution_id="002c706c",
                display_name="CORPORACION MUNICIPAL DE FOMENTO PRODUCTIVO DE ARICA",
                equipment_category="",
                commercial_signal_type="installed_base_signal",
                tender_count=1,
                tender_codes=["1234-5-le26"],
                review_dispositions=["installed_base_or_rental"],
            ),
        ],
        "institution_match_review_queue": [
            _institution_match_review_cluster_row(
                institution_review_cluster_id="008550af43420e2d",
                cluster_resolution_status="review_only_fragmented_identity",
                member_profile_ids=["profile-a"],
                cluster_reason_codes=["normalized_name_multi_profile_fragmentation"],
            ),
        ],
        "contact_gap_queue": [
            _contact_gap_row(
                institution_id="inst-example",
                display_name="MUNICIPALIDAD DE EJEMPLO",
                contact_gap_status="linked_account_no_contact",
                contact_resolution_status="no_contact_found",
                prospect_strength_band="high",
                prospect_strength_score=7,
                known_contact_count=0,
                suitable_contact_count=0,
                verified_contact_count=0,
                operator_next_action="research_new_contact",
            ),
        ],
        "line_evidence_review_queue": [
            _line_evidence_review_row(
                institution_id="inst-example",
                display_name="MUNICIPALIDAD DE EJEMPLO",
                tender_code="1234-5-le26",
                coalesced_tender_id="coalesced_tender_example",
                unit_id="unit-1",
                unit_decision_id="decision-1",
                relevance_class="ambiguous",
                equipment_scopes=["ambiguous"],
                canonical_equipment_classes=[],
                line_disposition="review_required",
                line_reason_codes=["no_supported_explicit_pattern_match"],
                ambiguity_reason_codes=["scope_ambiguous"],
            ),
        ],
        "retender_review_queue": [
            _retender_review_row(
                family_id="0009a395",
                buyer_key="buyer_name:i municipalidad de coquimbo",
                member_tender_codes=["2446-4-le26", "2446-16-le26"],
                raw_tender_count=2,
                recurrence_status="recurrence_not_established",
                family_resolution_status="retender_review_required",
                family_reason_codes=["independence_not_established", "retender_review_required"],
                relationship_edges=[],
            ),
        ],
    }
    client = _client(tmp_path, queue_rows=queue_rows)
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
        data = r.json()
        assert data["queue"] == route_name
        assert data["total"] == 1, route_name
        item = data["items"][0]
        assert item["contact_authorization"] is False, route_name
        assert item["outreach_authorization"] is False, route_name
