"""CLI wiring for the Wave 1B extractor and the Wave 1A addendum derivation.

Both scripts are read-only. These tests run them as subprocesses against
synthetic fixtures; nothing here names a production path.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from origenlab_email_pipeline.contact_domain_suppression import (
    CONTACT_DOMAIN_SUPPRESSION_SCHEMA_SQL,
)
from origenlab_email_pipeline.contact_email_suppression import (
    CONTACT_EMAIL_SUPPRESSION_SCHEMA_SQL,
)
from origenlab_email_pipeline.migration.wave1b_extract import SEPTEMBER_CAMPAIGN_IDS
from origenlab_email_pipeline.outbound_campaign_schema import OUTBOUND_CAMPAIGN_SCHEMA_SQL
from origenlab_email_pipeline.outreach_contact_state import OUTREACH_CONTACT_STATE_SCHEMA_SQL

APP_ROOT = Path(__file__).resolve().parents[1]
EXTRACTOR = APP_ROOT / "scripts" / "migration" / "extract_wave1b_v1_safety_bundle.py"
ADDENDUM = APP_ROOT / "scripts" / "migration" / "derive_wave1a_rfc2047_addendum.py"


def _run(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(APP_ROOT / "src")}
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=str(APP_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


MAILBOX = "operator@example.invalid"
SENT_FOLDER = "[Gmail]/Enviados"


def _minimal_db(path: Path, *, with_sent_history: bool = True) -> Path:
    conn = sqlite3.connect(path)
    for script in (
        OUTBOUND_CAMPAIGN_SCHEMA_SQL,
        CONTACT_EMAIL_SUPPRESSION_SCHEMA_SQL,
        CONTACT_DOMAIN_SUPPRESSION_SCHEMA_SQL,
        OUTREACH_CONTACT_STATE_SCHEMA_SQL,
    ):
        conn.executescript(script)
    for campaign_id in SEPTEMBER_CAMPAIGN_IDS:
        conn.execute(
            "INSERT INTO outbound_campaign (campaign_id, name, sender_email, sender_name,"
            " subject, target_attempt_count, baseline_attempt_count, status, created_at,"
            " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (campaign_id, "n", "s@example.invalid", "S", "subj", 1, 0, "paused",
             "2026-09-01T00:00:00Z", "2026-09-12T00:00:00Z"),
        )
    if with_sent_history:
        conn.execute(
            "CREATE TABLE emails (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " source_file TEXT NOT NULL, folder TEXT, subject TEXT, sender TEXT,"
            " recipients TEXT, date_iso TEXT, body TEXT)"
        )
        conn.execute(
            "INSERT INTO emails (source_file, folder, subject, sender, recipients,"
            " date_iso, body) VALUES (?,?,?,?,?,?,?)",
            (
                f"gmail:{MAILBOX}/1",
                SENT_FOLDER,
                "subj",
                MAILBOX,
                "manualsent@example.invalid",
                "2026-09-12T09:15:00+00:00",
                "body",
            ),
        )
    conn.commit()
    conn.close()
    return path


def test_both_scripts_exist_and_are_executable() -> None:
    assert EXTRACTOR.is_file()
    assert ADDENDUM.is_file()
    assert os.access(EXTRACTOR, os.X_OK)
    assert os.access(ADDENDUM, os.X_OK)


def test_extractor_help_documents_the_read_only_posture() -> None:
    proc = _run(EXTRACTOR, "--help")
    assert proc.returncode == 0, proc.stderr
    assert "mode=ro" in proc.stdout
    assert "query_only" in proc.stdout


def test_extractor_requires_an_explicit_output_directory() -> None:
    proc = _run(EXTRACTOR, "--sqlite-path", "/nonexistent/synthetic.sqlite")
    assert proc.returncode != 0
    assert "--output-dir" in (proc.stderr + proc.stdout)


def test_extractor_requires_an_explicit_sqlite_path(tmp_path: Path) -> None:
    proc = _run(EXTRACTOR, "--output-dir", str(tmp_path / "out"))
    assert proc.returncode != 0
    assert "--sqlite-path" in (proc.stderr + proc.stdout)


def test_extractor_refuses_an_output_directory_inside_the_repository(tmp_path: Path) -> None:
    db = _minimal_db(tmp_path / "src.sqlite")
    inside = APP_ROOT / "tests" / "wave1b_cli_forbidden_output"
    proc = _run(
        EXTRACTOR,
        "--sqlite-path", str(db),
        "--output-dir", str(inside),
        "--wave1a-snapshot-at", "2026-09-05T04:24:25Z",
        "--run-timestamp", "2026-09-20T12:00:00Z",
    )
    assert proc.returncode != 0
    assert "Git working tree" in (proc.stderr + proc.stdout)
    assert not inside.exists()


def test_extractor_runs_end_to_end_and_prints_no_address(tmp_path: Path) -> None:
    db = _minimal_db(tmp_path / "src.sqlite")
    out = tmp_path / "out"
    proc = _run(
        EXTRACTOR,
        "--sqlite-path", str(db),
        "--output-dir", str(out),
        "--wave1a-snapshot-at", "2026-09-05T04:24:25Z",
        "--run-timestamp", "2026-09-20T12:00:00Z",
        "--gmail-user", MAILBOX,
        "--sent-folder", SENT_FOLDER,
    )
    assert proc.returncode == 0, proc.stderr
    assert "s@example.invalid" not in proc.stdout
    assert "manualsent@example.invalid" not in proc.stdout
    assert "archive_sha256" in proc.stdout
    archives = list(out.glob("*.tar.gz"))
    assert len(archives) == 1
    assert Path(f"{archives[0]}.sha256").is_file()
    assert out.stat().st_mode & 0o777 == 0o700
    assert archives[0].stat().st_mode & 0o777 == 0o600


def test_extractor_refuses_a_source_without_canonical_sent_history(tmp_path: Path) -> None:
    db = _minimal_db(tmp_path / "src.sqlite", with_sent_history=False)
    proc = _run(
        EXTRACTOR,
        "--sqlite-path", str(db),
        "--output-dir", str(tmp_path / "out"),
        "--wave1a-snapshot-at", "2026-09-05T04:24:25Z",
        "--run-timestamp", "2026-09-20T12:00:00Z",
        "--gmail-user", MAILBOX,
        "--sent-folder", SENT_FOLDER,
    )
    assert proc.returncode == 2
    assert "REFUSED" in proc.stderr
    assert not (tmp_path / "out").exists() or not list((tmp_path / "out").glob("*wave1b*"))


def test_extractor_help_documents_the_sent_history_and_free_text_posture() -> None:
    proc = _run(EXTRACTOR, "--help")
    assert proc.returncode == 0, proc.stderr
    assert "--gmail-user" in proc.stdout
    assert "--sent-folder" in proc.stdout
    assert "No Gmail call is made" in proc.stdout


def test_addendum_help_states_it_never_edits_the_bundle() -> None:
    proc = _run(ADDENDUM, "--help")
    assert proc.returncode == 0, proc.stderr
    assert "never" in proc.stdout.lower()
    assert "--bundle-dir" in proc.stdout
