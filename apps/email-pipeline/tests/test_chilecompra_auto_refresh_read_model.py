"""Direct read-model publish tests for ChileCompra equipment auto-refresh."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from origenlab_email_pipeline.chilecompra_api import TICKET_ENV_VAR
from origenlab_email_pipeline.equipment_first_chilecompra_read_model import (
    preview_chilecompra_equipment_read_model,
)
from origenlab_email_pipeline.operator_cli.chilecompra_auto_refresh import (
    ChilecompraEquipmentAutoRefreshOptions,
    ChilecompraEquipmentAutoRefreshState,
    load_state,
    run_chilecompra_equipment_auto_refresh,
    save_state,
    state_path,
)

_SECRET_TICKET = "00000000-0000-0000-0000-000000000099"
_T0 = datetime(2026, 7, 2, 15, 25, 29, tzinfo=timezone.utc)


def _parse_output(out: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in out.strip().splitlines():
        if "=" in line and line != "chilecompra_equipment_auto_refresh":
            key, value = line.split("=", 1)
            result[key] = value
    return result


def _source_rows() -> list[dict[str, str]]:
    return [
        {
            "codigo_licitacion": "1051-1-LP26",
            "buyer": "Hospital Demo",
            "region": "RM",
            "close_date": "2026-07-10T16:00:00",
            "title": "Centrifuga",
            "item_description": "Centrifuga refrigerada",
            "equipment_category": "centrifuge",
            "fit_score": "85",
            "reason": "source:chilecompra_api; equipment:centrifuge",
            "next_action": "quote_now",
            "validity_status": "open",
            "chilecompra_status_code": "5",
            "chilecompra_status": "Publicada",
            "api_checked_at_utc": _T0.isoformat(),
            "source": "chilecompra_api",
        }
    ]


def _mock_build_result() -> tuple[list[dict[str, str]], dict[str, object], list[dict[str, str]]]:
    manifest = {
        "campaign_mode": "equipment_first",
        "fetched_summaries": 10,
        "candidate_summaries": 3,
        "prefilter_skipped_summaries": 7,
        "detail_requests": 2,
        "detail_cache_hits": 1,
        "detail_error_count": 0,
        "output_rows": 1,
    }
    audit_rows = [{"codigo": "1051-1-LP26", "prefilter_match": "true"}]
    return _source_rows(), manifest, audit_rows


def test_preview_chilecompra_read_model_builds_typed_summary(tmp_path: Path) -> None:
    export_path = tmp_path / "equipment_first_operator_queue_chilecompra_api_20260702.csv"

    summary = preview_chilecompra_equipment_read_model(
        _source_rows(),
        export_csv_path=export_path,
        now=_T0,
    )

    assert summary["source_input"] == "typed_rows"
    assert summary["source_kind"] == "typed_read_model"
    assert summary["artifact_basename"] == export_path.name
    assert summary["canonical_reason"] == "chilecompra_api_direct_rows"
    assert summary["source_rows"] == 1
    assert summary["output_rows"] == 1
    assert summary["sample_rows"] == ["1051-1-LP26"]


def test_auto_refresh_direct_publishes_read_model_from_builder_rows(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    reports_dir = tmp_path
    monkeypatch.setenv(TICKET_ENV_VAR, _SECRET_TICKET)
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@127.0.0.1:5432/scratch")
    publish = MagicMock(
        return_value={
            "out_csv": str(reports_dir / "active/current/equipment_first_operator_queue_chilecompra_api_20260702.csv"),
            "output_rows": 1,
            "coalesced_duplicate_rows": 0,
            "unique_codigo_count": 1,
        }
    )
    read_model_publish = MagicMock(
        return_value={
            "applied": True,
            "source_input": "typed_rows",
            "source_key": str(reports_dir / "active/current/equipment_first_operator_queue_chilecompra_api_20260702.csv"),
            "source_id": 55,
            "rows_inserted": 1,
            "row_count": 1,
            "source_kind": "typed_read_model",
            "artifact_basename": "equipment_first_operator_queue_chilecompra_api_20260702.csv",
            "canonical_reason": "chilecompra_api_direct_rows",
            "is_canonical": True,
        }
    )

    rc = run_chilecompra_equipment_auto_refresh(
        ChilecompraEquipmentAutoRefreshOptions(
            apply=True, once=True, force=True, publish_read_model=True
        ),
        reports_dir=reports_dir,
        build_fn=lambda **kwargs: _mock_build_result(),
        publish_fn=publish,
        read_model_publish_fn=read_model_publish,
        now_fn=lambda: _T0,
    )

    out = _parse_output(capsys.readouterr().out)
    assert rc == 0
    assert out["reason"] == "refreshed"
    assert out["read_model_result"] == "applied"
    assert out["read_model_rows"] == "1"
    assert out["read_model_source_kind"] == "typed_read_model"
    read_model_publish.assert_called_once()
    args, kwargs = read_model_publish.call_args
    assert args[1] == _source_rows()
    assert kwargs["export_csv_path"].name == "equipment_first_operator_queue_20260702.csv"
    assert kwargs["updated_by"] == "auto_chilecompra_equipment"
    assert kwargs["reason"] == "auto_chilecompra_equipment_refresh"
    assert kwargs["replace_source"] is True

    state = load_state(state_path(reports_dir))
    assert state.read_model_result == "applied"
    assert state.read_model_rows == 1
    assert state.read_model_source_kind == "typed_read_model"
    assert state.read_model_source_id == 55
    manifest_path = reports_dir / "active" / "current" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sync = manifest["chilecompra_api_publish"]["read_model_sync"]
    assert sync["source_input"] == "typed_rows"
    assert sync["source_kind"] == "typed_read_model"
    assert sync["source_id"] == 55


def test_auto_refresh_direct_read_model_failure_fails_closed(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    reports_dir = tmp_path
    monkeypatch.setenv(TICKET_ENV_VAR, _SECRET_TICKET)
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@127.0.0.1:5432/scratch")
    publish = MagicMock(
        return_value={
            "out_csv": str(reports_dir / "active/current/equipment_first_operator_queue_chilecompra_api_20260702.csv"),
            "output_rows": 1,
            "coalesced_duplicate_rows": 0,
            "unique_codigo_count": 1,
        }
    )
    read_model_publish = MagicMock(return_value={"applied": False, "error": "boom"})

    rc = run_chilecompra_equipment_auto_refresh(
        ChilecompraEquipmentAutoRefreshOptions(
            apply=True, once=True, force=True, publish_read_model=True
        ),
        reports_dir=reports_dir,
        build_fn=lambda **kwargs: _mock_build_result(),
        publish_fn=publish,
        read_model_publish_fn=read_model_publish,
        now_fn=lambda: _T0,
    )

    out = _parse_output(capsys.readouterr().out)
    assert rc == 1
    assert out["reason"] == "read_model_failed"
    state = load_state(state_path(reports_dir))
    assert state.last_result == "read_model_failed"
    assert state.read_model_result == "boom"
    assert state.consecutive_failures == 1


def test_disabled_read_model_clears_stale_fields_but_keeps_last_success_timestamp(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    """A previous manual/legacy publish left rows/source_kind/source_id in the
    state file. Once the writer is disabled (the new default), those fields
    must stop looking current — but last_successful_read_model_publish_at is
    historical evidence and must survive untouched."""
    reports_dir = tmp_path
    monkeypatch.setenv(TICKET_ENV_VAR, _SECRET_TICKET)
    prior_success_at = "2026-07-01T00:00:00+00:00"
    save_state(
        state_path(reports_dir),
        ChilecompraEquipmentAutoRefreshState(
            read_model_result="applied",
            read_model_rows=1,
            read_model_source_kind="typed_read_model",
            read_model_source_id=55,
            last_successful_read_model_publish_at=prior_success_at,
        ),
    )
    publish = MagicMock(
        return_value={
            "out_csv": str(reports_dir / "active/current/equipment_first_operator_queue_chilecompra_api_20260702.csv"),
            "output_rows": 1,
            "coalesced_duplicate_rows": 0,
            "unique_codigo_count": 1,
        }
    )
    read_model_publish = MagicMock()

    rc = run_chilecompra_equipment_auto_refresh(
        ChilecompraEquipmentAutoRefreshOptions(apply=True, once=True, force=True),  # publish_read_model default
        reports_dir=reports_dir,
        build_fn=lambda **kwargs: _mock_build_result(),
        publish_fn=publish,
        read_model_publish_fn=read_model_publish,
        now_fn=lambda: _T0,
    )

    assert rc == 0
    read_model_publish.assert_not_called()
    out = _parse_output(capsys.readouterr().out)
    assert out["read_model_result"] == "disabled"

    state = load_state(state_path(reports_dir))
    assert state.read_model_result == "disabled"
    assert state.read_model_rows is None
    assert state.read_model_source_kind is None
    assert state.read_model_source_id is None
    assert state.last_successful_read_model_publish_at == prior_success_at
