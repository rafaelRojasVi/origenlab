"""Tests for read-only operator status report."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from origenlab_email_pipeline import active_current_manifest as acm
from origenlab_email_pipeline.operator_status_report import (
    build_auxiliary_file_status,
    build_fastlab_status_warning,
    build_operator_status_report,
    compute_verdict,
    load_manifest,
    manifest_warnings_for_verdict,
    normalize_operator_warnings,
)


def test_compute_verdict_blocked_without_sqlite() -> None:
    assert (
        compute_verdict(
            sqlite_exists=False,
            readiness_verdict="ready",
            manifest_warnings=[],
            extra_errors=[],
        )
        == "BLOCKED"
    )


def test_compute_verdict_caution_with_manifest_warnings() -> None:
    assert (
        compute_verdict(
            sqlite_exists=True,
            readiness_verdict="ready",
            manifest_warnings=["FastLab pending"],
            extra_errors=[],
        )
        == "CAUTION"
    )


def test_manifest_warnings_for_verdict_excludes_stale_fastlab_when_corrected() -> None:
    manifest = {
        "operator_notes": {
            "fastlab": {"outreach_state": "not_contacted", "email": "contacto@fastlab.cl"},
        },
    }
    raw = [
        "FastLab: manual_state_only_pending_sent_verification — outreach_state contacted",
        "buyer_opportunity_crosscheck_20260518.csv is stale",
    ]
    filtered = manifest_warnings_for_verdict(raw, manifest)
    assert len(filtered) == 1
    assert "crosscheck" in filtered[0]


def test_build_fastlab_status_warning_corrected() -> None:
    manifest = {
        "operator_notes": {
            "fastlab": {
                "email": "contacto@fastlab.cl",
                "outreach_state": "not_contacted",
            },
        },
    }
    msg = build_fastlab_status_warning(manifest)
    assert msg is not None
    assert "corrected to not_contacted" in msg
    assert "contacted without" not in msg.lower()


def test_normalize_operator_warnings_replaces_stale_fastlab() -> None:
    manifest = {
        "operator_notes": {
            "fastlab": {"email": "contacto@fastlab.cl", "outreach_state": "not_contacted"},
        },
    }
    stale = [
        "FastLab (contacto@fastlab.cl): manual_state_only_pending_sent_verification — "
        "outreach_state contacted without Gmail Sent row.",
        "buyer_opportunity_crosscheck_20260518.csv is stale",
    ]
    out = normalize_operator_warnings(stale, manifest, conn=None)
    fastlab_lines = [w for w in out if "fastlab" in w.lower()]
    assert len(fastlab_lines) == 1
    assert "corrected to not_contacted" in fastlab_lines[0]
    assert "contacted without" not in fastlab_lines[0].lower()


def test_build_auxiliary_file_status_paths(tmp_path: Path) -> None:
    active_root = tmp_path / "active"
    active_current = active_root / "current"
    active_current.mkdir(parents=True)
    active_root.mkdir(parents=True, exist_ok=True)
    (active_current / "do_not_repeat_master.csv").write_text("email\na@b.cl\n", encoding="utf-8")
    (active_root / "outreach_contacted_all.csv").write_text("email\n", encoding="utf-8")
    (active_root / "all_known_marketing_contacts_dedup.csv").write_text("email\n", encoding="utf-8")
    manifest = {
        "canonical_files": ["do_not_repeat_master.csv"],
        "auxiliary_files_active_parent": [
            "outreach_contacted_all.csv",
            "all_known_marketing_contacts_dedup.csv",
        ],
    }
    aux = build_auxiliary_file_status(active_current, manifest)
    assert aux["do_not_repeat_master.csv"]["exists"] is True
    assert "current" in aux["do_not_repeat_master.csv"]["path"]
    assert aux["outreach_contacted_all.csv"]["exists"] is True
    assert "/active/outreach_contacted_all.csv" in aux["outreach_contacted_all.csv"]["path"].replace(
        "\\", "/"
    )
    assert aux["all_known_marketing_contacts_dedup.csv"]["exists"] is True


def test_build_operator_status_report_minimal_sqlite(tmp_path: Path) -> None:
    db = tmp_path / "emails.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE emails (
            id INTEGER PRIMARY KEY,
            date_iso TEXT,
            source_file TEXT,
            subject TEXT,
            folder TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO emails (date_iso, source_file, subject, folder) VALUES (?, ?, ?, ?)",
        (
            "2026-05-18T12:00:00-04:00",
            "gmail:contacto@origenlab.cl/[Gmail]/Enviados",
            "test",
            "[Gmail]/Enviados",
        ),
    )
    conn.commit()
    conn.close()

    active_root = tmp_path / "active"
    active = active_root / "current"
    active.mkdir(parents=True)
    (active / "do_not_repeat_master.csv").write_text("email\n", encoding="utf-8")
    manifest = {
        "known_warnings": ["test warning from manifest"],
        "canonical_files": ["do_not_repeat_master.csv"],
        "auxiliary_files_active_parent": [],
        "campaign_mode": "equipment_first",
        "current_operator_focus": "test focus",
        "operator_notes": {
            "fastlab": {"email": "contacto@fastlab.cl", "outreach_state": "not_contacted"},
        },
    }
    (active / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    report = build_operator_status_report(
        sqlite_path=db,
        active_current=active,
        manifest_path=active / "manifest.json",
        gmail_user="contacto@origenlab.cl",
        sent_folders=("[Gmail]/Enviados",),
    )
    assert report.sqlite_exists
    assert report.sent.get("canonical_sent_row_count") == 1
    assert "comparison_note" in report.sent
    assert "canonical_sent_distinct_message_id_count" in report.sent
    assert "test warning from manifest" in report.warnings
    assert report.auxiliary_files["do_not_repeat_master.csv"]["exists"] is True
    assert report.verdict in ("READY", "CAUTION", "BLOCKED")
    assert report.campaign_mode == "equipment_first"
    assert report.current_operator_focus == "test focus"


def test_load_manifest_missing() -> None:
    assert load_manifest(Path("/nonexistent/manifest.json")) == {}


def test_operator_status_surfaces_corrected_fastlab_not_stale_contacted(tmp_path: Path) -> None:
    db = tmp_path / "emails.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE emails (id INTEGER PRIMARY KEY, date_iso TEXT, source_file TEXT, folder TEXT)"
    )
    conn.execute(
        """
        CREATE TABLE outreach_contact_state (
            contact_email_norm TEXT PRIMARY KEY,
            state TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO outreach_contact_state (contact_email_norm, state) VALUES (?, ?)",
        ("contacto@fastlab.cl", "not_contacted"),
    )
    conn.commit()
    conn.close()

    active = tmp_path / "current"
    active.mkdir()
    manifest = {
        "known_warnings": [
            "FastLab (contacto@fastlab.cl): manual_state_only_pending_sent_verification — "
            "outreach_state contacted without Gmail Sent row.",
            "buyer_opportunity_crosscheck_20260518.csv is stale",
        ],
        "canonical_files": ["equipment_first_operator_queue_20260518.csv"],
        "postgres_status": "parked",
        "api_status": "parked",
        "campaign_mode": "equipment_first",
        "current_operator_focus": "equipment-first tenders",
        "stale_files": [{"path": "buyer_opportunity_crosscheck_20260518.csv"}],
        "operator_notes": {
            "fastlab": {
                "email": "contacto@fastlab.cl",
                "outreach_state": "not_contacted",
            },
        },
    }
    (active / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (active / "equipment_first_operator_queue_20260518.csv").write_text(
        "priority_rank,codigo_licitacion\n1,TEST-1\n",
        encoding="utf-8",
    )

    report = build_operator_status_report(
        sqlite_path=db,
        active_current=active,
        manifest_path=active / "manifest.json",
        gmail_user="contacto@origenlab.cl",
        sent_folders=("[Gmail]/Enviados",),
    )
    assert report.verdict in ("CAUTION", "BLOCKED")  # BLOCKED if minimal schema skips readiness
    fastlab_warnings = [w for w in report.warnings if "fastlab" in w.lower()]
    assert len(fastlab_warnings) == 1
    assert "corrected to not_contacted" in fastlab_warnings[0]
    assert "outreach_state contacted" not in fastlab_warnings[0].lower()
    assert any("crosscheck" in w.lower() or "stale" in w.lower() for w in report.warnings)


@pytest.mark.skipif(
    os.environ.get("ORIGENLAB_TEST_USE_REPO_ACTIVE_CURRENT") != "1"
    or not (Path(__file__).resolve().parents[1] / "reports/out/active/current/manifest.json").is_file(),
    reason="live repo active/current manifest validation is opt-in; set ORIGENLAB_TEST_USE_REPO_ACTIVE_CURRENT=1",
)
def test_operator_status_against_repo_manifest() -> None:
    repo = Path(__file__).resolve().parents[1]
    active = repo / "reports/out/active/current"
    active_root = active.parent
    manifest_path = active / "manifest.json"
    from origenlab_email_pipeline.config import load_settings

    db = load_settings().resolved_sqlite_path()
    if not db.is_file():
        pytest.skip("production SQLite not available")

    manifest = acm.load_manifest(manifest_path)
    errors = acm.validate_manifest(manifest, active_current=active, active_root=active_root)
    assert errors == [], "manifest validation errors:\n" + "\n".join(errors)

    report = build_operator_status_report(
        sqlite_path=db,
        active_current=active,
        manifest_path=manifest_path,
        gmail_user="contacto@origenlab.cl",
        sent_folders=("[Gmail]/Enviados",),
    )
    assert report.postgres.get("status") in ("parked", "available")
    assert report.api.get("status") == "active"
    fastlab_warnings = [w for w in report.warnings if "fastlab" in w.lower()]
    assert len(fastlab_warnings) == 1
    assert "corrected to not_contacted" in fastlab_warnings[0]
    assert "manual_state_only_pending" not in fastlab_warnings[0]
    assert report.auxiliary_files["do_not_repeat_master.csv"]["exists"] is True
    assert "active/current" in report.auxiliary_files["do_not_repeat_master.csv"]["path"].replace(
        "\\", "/"
    )
    assert report.verdict in ("READY", "CAUTION", "BLOCKED")
    eq = report.equipment_queue.get("row_count")
    if (active / "equipment_first_operator_queue_20260518.csv").is_file():
        assert eq is not None and eq >= 1
    assert manifest.get("campaign_mode") in (
        "equipment_first",
        "volume_marketing",
        "precision_leads",
        "none",
    )
    assert report.campaign_mode == manifest.get("campaign_mode")
