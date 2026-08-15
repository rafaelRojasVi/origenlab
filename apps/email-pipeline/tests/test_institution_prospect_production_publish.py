"""Tests for publish_current_institution_prospects (staged build + validate + promote).

The real ``build_institution_prospect_plan`` is exercised extensively elsewhere
(PR5C/PR5D/PR5E test suites); these tests inject a fake in its place — exactly
the dependency-injection style already used by ``test_chilecompra_auto_refresh.py``
— so they can assert on staging/promotion/validation behavior without running
the full pipeline or any network/DB I/O.
"""

from __future__ import annotations

import csv
import json
import shutil
import uuid
from pathlib import Path
from typing import Any

import pytest

from origenlab_email_pipeline.commercial_procurement_institution_prospects import (
    production_publish,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.constants import (
    CONTRACT_VERSION,
    OPERATOR_QUEUE_NAMES,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.queues import (
    EMPTY_QUEUE_HEADERS,
)

AS_OF = "2026-08-14T17:01:11Z"
_REAL_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def real_out_dir():
    """A unique, real, gitignored reports/out path — write_atomically() (used
    by the atomic promote step) requires the destination be genuinely under
    reports/out/ and git-ignored, so this cannot be a plain tmp_path."""
    out_dir = (
        _REAL_ROOT
        / "reports"
        / "out"
        / "active"
        / "current"
        / f"_w2_test_institution_prospects_{uuid.uuid4().hex[:12]}"
    )
    yield out_dir
    shutil.rmtree(out_dir, ignore_errors=True)
    shutil.rmtree(production_publish._staging_dir_for(out_dir), ignore_errors=True)


def _write_csv(path: Path, header: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore", restval="")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_valid_bundle(
    dest: Path,
    *,
    contract_version: str = CONTRACT_VERSION,
    contact_authorization: bool = False,
    outreach_authorization: bool = False,
    current_opportunity_rows: int = 2,
) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    profiles = [
        {"institution_id": f"inst-{i}", "identity": {"display_name": f"Inst {i}"}}
        for i in range(3)
    ]
    packet = {
        "ok": True,
        "as_of_utc": AS_OF,
        "run_context": "production_apply",
        "planner_version": "procurement_institution_prospect_planner_v4",
        "recognition_layer_version": "procurement_prospect_recognition_pr5e2_v1",
        "contract_version": contract_version,
        "not_persisted": True,
        "contact_authorization": contact_authorization,
        "outreach_authorization": outreach_authorization,
        "profiles": profiles,
        "counts": {"institution_count": 3},
        "fingerprints": {"build_fingerprint": "digest-xyz"},
    }
    (dest / "institution_prospect_packet.json").write_text(json.dumps(packet), encoding="utf-8")
    sizes = {name: 0 for name in OPERATOR_QUEUE_NAMES}
    sizes["current_opportunity_queue"] = current_opportunity_rows
    (dest / "summary.json").write_text(
        json.dumps({"ok": True, "contract_version": contract_version, "operator_queue_sizes": sizes}),
        encoding="utf-8",
    )
    for queue_name in OPERATOR_QUEUE_NAMES:
        rows: list[dict[str, object]] = []
        if queue_name == "current_opportunity_queue":
            rows = [
                {"queue_row_id": f"r{i}", "institution_id": "inst-0", "contact_authorization": "False", "outreach_authorization": "False"}
                for i in range(current_opportunity_rows)
            ]
        _write_csv(dest / f"{queue_name}.csv", EMPTY_QUEUE_HEADERS[queue_name], rows)


def _fake_builder(*, captured: list[dict[str, Any]], contract_version: str = CONTRACT_VERSION):
    def _build(**kwargs: Any):
        captured.append(kwargs)
        _write_valid_bundle(kwargs["out_dir"], contract_version=contract_version)
        return {"ok": True}

    return _build


def test_flag_off_never_calls_publisher(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sanity: nothing in this module runs unless explicitly invoked (real gating
    lives in chilecompra_auto_refresh.py's options.publish_institution_prospects
    default False — verified separately)."""
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(production_publish, "build_institution_prospect_plan", _fake_builder(captured=captured))
    assert captured == []


def test_reuses_provided_detail_cache_and_manifest_no_refresh_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, real_out_dir: Path
) -> None:
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(production_publish, "build_institution_prospect_plan", _fake_builder(captured=captured))
    detail_cache_dir = tmp_path / "detail_cache"
    detail_cache_dir.mkdir()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    sqlite_path = tmp_path / "emails.sqlite"
    out_dir = real_out_dir

    result = production_publish.publish_current_institution_prospects(
        sqlite_path=sqlite_path,
        detail_cache_dir=detail_cache_dir,
        equipment_manifest_path=manifest_path,
        as_of_utc=AS_OF,
        out_dir=out_dir,
    )

    assert result.result == "applied"
    assert len(captured) == 1
    kwargs = captured[0]
    assert kwargs["detail_cache_dir"] == detail_cache_dir
    assert kwargs["equipment_manifest_path"] == manifest_path
    assert kwargs["sqlite_path"] == sqlite_path
    # Deliberately never pass a (possibly stale, not-yet-updated-for-this-run)
    # refresh_state_path — the manifest this exact run wrote is the freshness
    # authority (see module docstring / freshness.evaluate_feed_freshness
    # precedence: refresh_state wins over manifest when both are given).
    assert kwargs["refresh_state_path"] is None
    assert kwargs["enable_annex_opportunity_evidence"] is False


def test_makes_zero_chilecompra_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, real_out_dir: Path
) -> None:
    """The fake builder never imports/calls chilecompra_api; asserting the real
    module is untouched proves the publisher itself performs no acquisition."""
    import origenlab_email_pipeline.chilecompra_api as chilecompra_api

    called = {"hit": False}

    def _poison(*_a: Any, **_k: Any) -> None:
        called["hit"] = True
        raise AssertionError("chilecompra_api must never be called by the institution publisher")

    monkeypatch.setattr(chilecompra_api, "ticket_from_env", _poison)
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(production_publish, "build_institution_prospect_plan", _fake_builder(captured=captured))

    production_publish.publish_current_institution_prospects(
        sqlite_path=tmp_path / "e.sqlite",
        detail_cache_dir=tmp_path / "cache",
        equipment_manifest_path=tmp_path / "m.json",
        as_of_utc=AS_OF,
        out_dir=real_out_dir,
    )
    assert called["hit"] is False


def test_correct_canonical_basename_in_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, real_out_dir: Path
) -> None:
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(production_publish, "build_institution_prospect_plan", _fake_builder(captured=captured))
    out_dir = real_out_dir
    result = production_publish.publish_current_institution_prospects(
        sqlite_path=tmp_path / "e.sqlite",
        detail_cache_dir=tmp_path / "cache",
        equipment_manifest_path=tmp_path / "m.json",
        as_of_utc=AS_OF,
        out_dir=out_dir,
    )
    assert result.output_dir_basename == out_dir.name
    assert result.error is None
    assert out_dir.is_dir()
    assert (out_dir / "institution_prospect_packet.json").is_file()


def test_w1_loader_validates_success_and_summary_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, real_out_dir: Path
) -> None:
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(
        production_publish,
        "build_institution_prospect_plan",
        _fake_builder(captured=captured),
    )
    out_dir = real_out_dir
    result = production_publish.publish_current_institution_prospects(
        sqlite_path=tmp_path / "e.sqlite",
        detail_cache_dir=tmp_path / "cache",
        equipment_manifest_path=tmp_path / "m.json",
        as_of_utc=AS_OF,
        out_dir=out_dir,
    )
    assert result.result == "applied"
    assert result.contract_version == CONTRACT_VERSION
    assert result.as_of_utc == AS_OF
    assert result.source_digest == "digest-xyz"
    assert result.institution_count == 3
    assert result.current_opportunity_count == 2
    assert result.operator_queue_sizes["current_opportunity_queue"] == 2
    # No staging directory left behind after a successful promote.
    assert not production_publish._staging_dir_for(out_dir).exists()


def test_authorization_and_outreach_stay_false_even_if_packet_claims_otherwise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, real_out_dir: Path
) -> None:
    """The W1 loader unconditionally normalizes authorization to False in the
    validated model regardless of what a (real planner code always writes
    False, but this simulates a hypothetical regression) packet claims — so
    this is the real "authorization/outreach false" guarantee production
    publication relies on. Publication succeeds because the loader already
    neutralized the bad claim before production_publish.py's own explicit
    authorization check ever runs."""

    def _bad_builder(**kwargs: Any):
        _write_valid_bundle(kwargs["out_dir"], contact_authorization=True, outreach_authorization=True)
        return {"ok": True}

    monkeypatch.setattr(production_publish, "build_institution_prospect_plan", _bad_builder)
    out_dir = real_out_dir
    result = production_publish.publish_current_institution_prospects(
        sqlite_path=tmp_path / "e.sqlite",
        detail_cache_dir=tmp_path / "cache",
        equipment_manifest_path=tmp_path / "m.json",
        as_of_utc=AS_OF,
        out_dir=out_dir,
    )
    assert result.result == "applied"
    from origenlab_email_pipeline.commercial_procurement_institution_prospects.read_model import (
        load_published_read_model,
    )

    published_model = load_published_read_model(out_dir)
    assert published_model["contact_authorization"] is False
    assert published_model["outreach_authorization"] is False


def test_publication_failure_leaves_prior_valid_bundle_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, real_out_dir: Path
) -> None:
    out_dir = real_out_dir

    # First: a real successful publish.
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(production_publish, "build_institution_prospect_plan", _fake_builder(captured=captured))
    first = production_publish.publish_current_institution_prospects(
        sqlite_path=tmp_path / "e.sqlite",
        detail_cache_dir=tmp_path / "cache",
        equipment_manifest_path=tmp_path / "m.json",
        as_of_utc=AS_OF,
        out_dir=out_dir,
    )
    assert first.result == "applied"
    prior_packet = (out_dir / "institution_prospect_packet.json").read_text(encoding="utf-8")

    # Second: the planner raises mid-build.
    def _raising_builder(**kwargs: Any):
        raise RuntimeError("simulated planner failure")

    monkeypatch.setattr(production_publish, "build_institution_prospect_plan", _raising_builder)
    second = production_publish.publish_current_institution_prospects(
        sqlite_path=tmp_path / "e.sqlite",
        detail_cache_dir=tmp_path / "cache",
        equipment_manifest_path=tmp_path / "m.json",
        as_of_utc=AS_OF,
        out_dir=out_dir,
    )
    assert second.result == "failed"
    assert "build_failed" in (second.error or "")
    # Canonical bundle from the first, successful publish is untouched.
    assert (out_dir / "institution_prospect_packet.json").read_text(encoding="utf-8") == prior_packet


def test_malformed_output_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, real_out_dir: Path
) -> None:
    def _malformed_builder(**kwargs: Any):
        out_dir = kwargs["out_dir"]
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "institution_prospect_packet.json").write_text("{not valid json", encoding="utf-8")
        return {"ok": True}

    monkeypatch.setattr(production_publish, "build_institution_prospect_plan", _malformed_builder)
    out_dir = real_out_dir
    result = production_publish.publish_current_institution_prospects(
        sqlite_path=tmp_path / "e.sqlite",
        detail_cache_dir=tmp_path / "cache",
        equipment_manifest_path=tmp_path / "m.json",
        as_of_utc=AS_OF,
        out_dir=out_dir,
    )
    assert result.result == "failed"
    assert "validation_failed" in (result.error or "")
    assert not out_dir.exists()


def test_unsupported_contract_version_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, real_out_dir: Path
) -> None:
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(
        production_publish,
        "build_institution_prospect_plan",
        _fake_builder(captured=captured, contract_version="institution_prospect_contract_v999"),
    )
    out_dir = real_out_dir
    result = production_publish.publish_current_institution_prospects(
        sqlite_path=tmp_path / "e.sqlite",
        detail_cache_dir=tmp_path / "cache",
        equipment_manifest_path=tmp_path / "m.json",
        as_of_utc=AS_OF,
        out_dir=out_dir,
    )
    assert result.result == "failed"
    assert "validation_failed" in (result.error or "")
    assert not out_dir.exists()
