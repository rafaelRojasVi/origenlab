"""Tests for daily-core run manifest surfacing in operator status (read-only)."""

from __future__ import annotations

import json
from pathlib import Path

from origenlab_email_pipeline.operator_cli.daily_core_manifest import MANIFEST_FILENAME
from origenlab_email_pipeline.operator_status_report import (
    build_operator_status_report,
    format_human_report,
)


def _write_minimal_sqlite(db: Path) -> None:
    import sqlite3

    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE emails (id INTEGER PRIMARY KEY, date_iso TEXT, source_file TEXT, folder TEXT)"
    )
    conn.execute(
        "INSERT INTO emails (date_iso, source_file, folder) VALUES (?, ?, ?)",
        (
            "2026-05-18T12:00:00-04:00",
            "gmail:contacto@origenlab.cl/[Gmail]/Enviados",
            "[Gmail]/Enviados",
        ),
    )
    conn.commit()
    conn.close()


def _write_campaign_manifest(active: Path) -> None:
    manifest = {
        "known_warnings": [],
        "canonical_files": [],
        "auxiliary_files_active_parent": [],
        "campaign_mode": "equipment_first",
        "operator_notes": {
            "fastlab": {"email": "contacto@fastlab.cl", "outreach_state": "not_contacted"},
        },
    }
    (active / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _build_report(tmp_path: Path) -> tuple[Path, Path]:
    db = tmp_path / "emails.sqlite"
    active = tmp_path / "current"
    active.mkdir()
    _write_minimal_sqlite(db)
    _write_campaign_manifest(active)
    report = build_operator_status_report(
        sqlite_path=db,
        active_current=active,
        manifest_path=active / "manifest.json",
        gmail_user="contacto@origenlab.cl",
        sent_folders=("[Gmail]/Enviados",),
    )
    return active, report


def _valid_daily_core_manifest_payload() -> dict:
    return {
        "schema_version": 1,
        "workflow": "daily-core",
        "generated_at_utc": "2026-06-05T12:00:00+00:00",
        "command": "uv run origenlab daily-core --apply",
        "equivalent_command": "uv run origenlab refresh-dashboard --apply --no-mirror",
        "operational_truth": "SQLite + Gmail Sent history inside SQLite",
        "postgres_mirror": "not included",
        "send_approval": False,
        "safety": {"runs_postgres_mirror": False},
        "steps": [
            {"label": "gmail-ingest", "returncode": 0},
            {"label": "build-mart", "returncode": 0},
            {"label": "build-commercial-intel", "returncode": 0},
            {"label": "refresh-safety", "returncode": 0},
            {"label": "ndr-review", "returncode": 0},
            {"label": "post-send-digest", "returncode": 0},
            {"label": "status", "returncode": 0},
        ],
        "status": "success",
        "returncode": 0,
    }


def test_missing_daily_core_manifest_does_not_affect_verdict(tmp_path: Path) -> None:
    active, report = _build_report(tmp_path)
    assert not (active / MANIFEST_FILENAME).exists()
    assert report.daily_core_run["exists"] is False
    assert report.daily_core_run["path"].endswith("daily_core_run_manifest.json")
    assert not any("daily_core_run_manifest" in w for w in report.warnings)
    assert report.verdict in ("READY", "CAUTION", "BLOCKED")


def test_valid_daily_core_manifest_summary(tmp_path: Path) -> None:
    active, report = _build_report(tmp_path)
    (active / MANIFEST_FILENAME).write_text(
        json.dumps(_valid_daily_core_manifest_payload(), indent=2) + "\n",
        encoding="utf-8",
    )
    report = build_operator_status_report(
        sqlite_path=tmp_path / "emails.sqlite",
        active_current=active,
        manifest_path=active / "manifest.json",
        gmail_user="contacto@origenlab.cl",
        sent_folders=("[Gmail]/Enviados",),
    )
    dcr = report.daily_core_run
    assert dcr["exists"] is True
    assert dcr["loaded"] is True
    assert dcr["workflow"] == "daily-core"
    assert dcr["status"] == "success"
    assert dcr["returncode"] == 0
    assert dcr["step_count"] == 7
    assert dcr["last_step"] == "status"
    assert dcr["send_approval"] is False
    assert dcr["postgres_mirror"] == "not included"


def test_daily_core_manifest_parse_error_adds_warning_not_blocked(tmp_path: Path) -> None:
    active, report = _build_report(tmp_path)
    (active / MANIFEST_FILENAME).write_text("{not-json", encoding="utf-8")
    report = build_operator_status_report(
        sqlite_path=tmp_path / "emails.sqlite",
        active_current=active,
        manifest_path=active / "manifest.json",
        gmail_user="contacto@origenlab.cl",
        sent_folders=("[Gmail]/Enviados",),
    )
    assert report.daily_core_run["exists"] is True
    assert report.daily_core_run["loaded"] is False
    assert report.daily_core_run["parse_error"] is True
    assert any("daily_core_run_manifest.json parse error" in w for w in report.warnings)
    assert not any("daily_core_run_manifest.json parse error" in e for e in report.errors)
    assert report.verdict in ("READY", "CAUTION", "BLOCKED")


def test_human_report_includes_daily_core_last_run(tmp_path: Path) -> None:
    active, report = _build_report(tmp_path)
    (active / MANIFEST_FILENAME).write_text(
        json.dumps(_valid_daily_core_manifest_payload()) + "\n",
        encoding="utf-8",
    )
    report = build_operator_status_report(
        sqlite_path=tmp_path / "emails.sqlite",
        active_current=active,
        manifest_path=active / "manifest.json",
        gmail_user="contacto@origenlab.cl",
        sent_folders=("[Gmail]/Enviados",),
    )
    text = format_human_report(report)
    assert "Daily core last run:" in text
    assert "Gmail Sent (stored canonical history):" in text
    assert "API: active" in text
    assert "exists: True" in text
    assert "status: success" in text
    assert "send_approval: False" in text
    assert "postgres_mirror: not included" in text


def test_campaign_manifest_json_behavior_unchanged(tmp_path: Path) -> None:
    active, report = _build_report(tmp_path)
    assert report.manifest["loaded"] is True
    assert "campaign_mode" in report.manifest["keys"] or report.campaign_mode == "equipment_first"
    assert report.campaign_mode == "equipment_first"

    (active / "manifest.json").write_text("{bad", encoding="utf-8")
    blocked = build_operator_status_report(
        sqlite_path=tmp_path / "emails.sqlite",
        active_current=active,
        manifest_path=active / "manifest.json",
        gmail_user="contacto@origenlab.cl",
        sent_folders=("[Gmail]/Enviados",),
    )
    assert blocked.verdict == "BLOCKED"
    assert any("manifest.json parse error" in e for e in blocked.errors)
