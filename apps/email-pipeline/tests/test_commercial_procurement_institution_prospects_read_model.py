"""Read-only loader for a published institution-prospect bundle (read_model.py)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from origenlab_email_pipeline.commercial_procurement_institution_prospects.constants import (
    CONTRACT_VERSION,
    OPERATOR_QUEUE_NAMES,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.queues import (
    EMPTY_QUEUE_HEADERS,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.read_model import (
    PublishedReadModelMalformedError,
    PublishedReadModelMissingError,
    UnsupportedContractVersionError,
    load_institution_prospect_packet,
    load_operator_queue,
    load_published_read_model,
)

_SAG_TENDER_CODE = "745712-19-LP26"
_ISP_TENDER_CODE = "1093303-5-CO26"


def _write_csv(path: Path, header: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore", restval="")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _base_profile(institution_id: str, display_name: str) -> dict[str, object]:
    return {
        "institution_id": institution_id,
        "identity": {"display_name": display_name, "normalized_name": display_name.lower()},
        "account_contact_overlay": {"contact_authorization": False, "outreach_authorization": False},
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


def _write_bundle(
    dest: Path,
    *,
    contract_version: str = CONTRACT_VERSION,
    current_opportunity_rows: list[dict[str, object]] | None = None,
) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    profiles = [
        _base_profile("inst-sag", "SAG"),
        _base_profile("inst-isp", "ISP"),
    ]
    packet = {
        "ok": True,
        "as_of_utc": "2026-08-14T17:01:11Z",
        "run_context": "production_dry_run",
        "planner_version": "procurement_institution_prospect_planner_v4",
        "recognition_layer_version": "procurement_prospect_recognition_pr5e2_v1",
        "contract_version": contract_version,
        "not_persisted": True,
        "contact_authorization": False,
        "outreach_authorization": False,
        "profiles": profiles,
        "counts": {"institution_count": 2},
        "fingerprints": {"build_fingerprint": "abc123"},
    }
    (dest / "institution_prospect_packet.json").write_text(json.dumps(packet), encoding="utf-8")
    summary = {
        "ok": True,
        "contract_version": contract_version,
        "operator_queue_sizes": {name: 0 for name in OPERATOR_QUEUE_NAMES},
    }
    (dest / "summary.json").write_text(json.dumps(summary), encoding="utf-8")

    default_current_opportunity_rows = current_opportunity_rows or [
        {
            "queue_row_id": "row1",
            "queue": "current_opportunity_queue",
            "institution_id": "inst-sag",
            "display_name": "SAG",
            "tender_code": _SAG_TENDER_CODE,
            "coalesced_tender_id": "t-sag",
            "equipment_category": "balance",
            "lifecycle_class": "active_open",
            "review_disposition": "catalog_fit",
            "commercial_signal_type": "equipment_purchase_signal",
            "catalog_fit_status": "catalog_fit_candidate",
            "catalog_match_status": "exact_product",
            "opportunity_urgency_band": "high",
            "prospect_strength_band": "high",
            "line_evidence_unit_count": "2",
            "reason_codes": json.dumps(["procurement_method_open_public"]),
            "eligibility_reason_codes": json.dumps([]),
            "contact_authorization": "False",
            "outreach_authorization": "False",
            "closing_soon_bucket": "this_week",
            "publication_timestamp": "2026-08-01T00:00:00Z",
            "close_timestamp": "2026-08-20T00:00:00Z",
        }
    ]
    for queue_name in OPERATOR_QUEUE_NAMES:
        header = EMPTY_QUEUE_HEADERS[queue_name]
        rows = default_current_opportunity_rows if queue_name == "current_opportunity_queue" else []
        _write_csv(dest / f"{queue_name}.csv", header, rows)


def test_load_published_read_model_happy_path(tmp_path: Path) -> None:
    dest = tmp_path / "bundle"
    _write_bundle(dest)
    model = load_published_read_model(dest)
    assert model["contract_version"] == CONTRACT_VERSION
    assert model["not_persisted"] is True
    assert model["contact_authorization"] is False
    assert model["outreach_authorization"] is False
    assert model["source_digest"] == "abc123"
    assert len(model["profiles"]) == 2
    assert set(model["queues"].keys()) == set(OPERATOR_QUEUE_NAMES)

    current = model["queues"]["current_opportunity_queue"]
    assert len(current) == 1
    row = current[0]
    # Nested list cell decoded back from JSON, not left as a raw string.
    assert row["reason_codes"] == ["procurement_method_open_public"]
    assert row["eligibility_reason_codes"] == []
    # Boolean cell decoded back from "False"/"True" text, not left as a string.
    assert row["contact_authorization"] is False
    assert row["outreach_authorization"] is False

    for other_queue in OPERATOR_QUEUE_NAMES:
        if other_queue == "current_opportunity_queue":
            continue
        assert model["queues"][other_queue] == []


def test_load_published_read_model_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(PublishedReadModelMissingError):
        load_published_read_model(tmp_path / "does_not_exist")


def test_load_published_read_model_missing_queue_csv_is_missing_not_empty(tmp_path: Path) -> None:
    """A bundle with the packet but a deleted queue CSV is an incomplete bundle
    (missing), not a valid empty queue — those are different failure classes."""
    dest = tmp_path / "bundle"
    _write_bundle(dest)
    (dest / "retender_review_queue.csv").unlink()
    with pytest.raises(PublishedReadModelMissingError):
        load_published_read_model(dest)


def test_empty_queue_csv_with_header_only_is_a_valid_empty_queue(tmp_path: Path) -> None:
    dest = tmp_path / "bundle"
    _write_bundle(dest)
    rows = load_operator_queue(dest, "retender_review_queue")
    assert rows == []


def test_load_published_read_model_malformed_packet_json(tmp_path: Path) -> None:
    dest = tmp_path / "bundle"
    _write_bundle(dest)
    (dest / "institution_prospect_packet.json").write_text("{not valid json", encoding="utf-8")
    with pytest.raises(PublishedReadModelMalformedError):
        load_published_read_model(dest)


def test_load_published_read_model_unsupported_contract_version(tmp_path: Path) -> None:
    dest = tmp_path / "bundle"
    _write_bundle(dest, contract_version="institution_prospect_contract_v999")
    with pytest.raises(UnsupportedContractVersionError) as excinfo:
        load_published_read_model(dest)
    assert excinfo.value.contract_version == "institution_prospect_contract_v999"


def test_load_institution_prospect_packet_requires_profiles_list(tmp_path: Path) -> None:
    dest = tmp_path / "bundle"
    dest.mkdir()
    (dest / "institution_prospect_packet.json").write_text(
        json.dumps({"contract_version": CONTRACT_VERSION, "profiles": "not-a-list"}),
        encoding="utf-8",
    )
    with pytest.raises(PublishedReadModelMalformedError):
        load_institution_prospect_packet(dest)


def test_isp_restricted_row_shape_survives_round_trip(tmp_path: Path) -> None:
    """Regression guard for the exact ISP CO/restricted case validated in the PR3
    lifecycle hotfix: a restricted-eligibility row's reason codes must decode
    back to a real list, not a JSON-encoded string."""
    dest = tmp_path / "bundle"
    isp_row = {
        "queue_row_id": "row-isp",
        "queue": "current_opportunity_queue",
        "institution_id": "inst-isp",
        "display_name": "ISP",
        "tender_code": _ISP_TENDER_CODE,
        "coalesced_tender_id": "t-isp",
        "equipment_category": "centrifuge",
        "lifecycle_class": "active_open",
        "review_disposition": "catalog_fit",
        "commercial_signal_type": "equipment_purchase_signal",
        "catalog_fit_status": "catalog_fit_candidate",
        "catalog_match_status": "exact_product",
        "opportunity_urgency_band": "high",
        "prospect_strength_band": "high",
        "line_evidence_unit_count": "1",
        "reason_codes": json.dumps(["restricted_procurement_invitation_unconfirmed"]),
        "eligibility_reason_codes": json.dumps(
            ["restricted_procurement_invitation_unconfirmed"]
        ),
        "contact_authorization": "False",
        "outreach_authorization": "False",
        "closing_soon_bucket": "",
        "publication_timestamp": "",
        "close_timestamp": "",
    }
    _write_bundle(dest, current_opportunity_rows=[isp_row])
    model = load_published_read_model(dest)
    row = model["queues"]["current_opportunity_queue"][0]
    assert row["tender_code"] == _ISP_TENDER_CODE
    assert row["reason_codes"] == ["restricted_procurement_invitation_unconfirmed"]
    assert row["eligibility_reason_codes"] == [
        "restricted_procurement_invitation_unconfirmed"
    ]
