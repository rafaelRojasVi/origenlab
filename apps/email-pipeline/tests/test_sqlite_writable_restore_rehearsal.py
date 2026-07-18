"""Synthetic writable-restore rehearsal tests (throwaway fixtures only)."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from origenlab_email_pipeline.qa.sqlite_online_backup import fingerprint_file
from origenlab_email_pipeline.qa.sqlite_writable_restore_rehearsal import (
    EXIT_OK,
    EXIT_PREFLIGHT,
    EXIT_SAFETY,
    RehearsalError,
    RehearsalOptions,
    build_synthetic_source,
    preflight,
    run_rehearsal,
)

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "maintenance" / "rehearse_sqlite_writable_restore.py"


def test_preflight_requires_confirm(tmp_path: Path) -> None:
    src = tmp_path / "synthetic_source.sqlite"
    dst = tmp_path / "throwaway_restore.sqlite"
    build_synthetic_source(src)
    with pytest.raises(RehearsalError, match="confirm-throwaway"):
        preflight(
            RehearsalOptions(source=src, restore_target=dst, confirm_throwaway=False)
        )


def test_preflight_refuses_production_basename(tmp_path: Path) -> None:
    src = tmp_path / "synthetic_source.sqlite"
    build_synthetic_source(src)
    with pytest.raises(RehearsalError, match="basename"):
        preflight(
            RehearsalOptions(
                source=src,
                restore_target=tmp_path / "emails.sqlite",
                confirm_throwaway=True,
            )
        )


def test_apply_writable_transaction_and_rollback(tmp_path: Path) -> None:
    src = tmp_path / "synthetic_source.sqlite"
    dst = tmp_path / "throwaway_restore.sqlite"
    build_synthetic_source(src)
    fp_before = fingerprint_file(src)
    report = run_rehearsal(
        RehearsalOptions(
            source=src,
            restore_target=dst,
            confirm_throwaway=True,
            apply=True,
        )
    )
    assert report["completed"] is True
    assert report["writable_transaction_verified"] is True
    assert report["rollback_verified"] is True
    assert report["source_fingerprint_unchanged"] is True
    assert fingerprint_file(src) == fp_before
    assert dst.is_file()
    conn = sqlite3.connect(dst)
    try:
        marker = conn.execute("SELECT marker FROM emails WHERE id=1").fetchone()[0]
        assert marker == "pre-restore"
    finally:
        conn.close()
    assert not list(tmp_path.glob("*.partial.*"))
    blob = json.dumps(report)
    assert "/home/" not in blob or "<" in report.get("source_path_sanitized", "")


def test_source_sidecars_refuse(tmp_path: Path) -> None:
    src = tmp_path / "synthetic_source.sqlite"
    dst = tmp_path / "rehearsal_target.sqlite"
    build_synthetic_source(src)
    (Path(str(src) + "-wal")).write_bytes(b"")
    with pytest.raises(RehearsalError, match="companion"):
        preflight(
            RehearsalOptions(source=src, restore_target=dst, confirm_throwaway=True)
        )


def test_cli_preflight_and_apply_exit_codes(tmp_path: Path) -> None:
    src = tmp_path / "synthetic_src.sqlite"
    dst = tmp_path / "throwaway_dst.sqlite"
    env = {**os.environ, "PYTHONPATH": str(REPO / "src")}
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--build-synthetic-source",
            str(src),
            "--restore-target",
            str(dst),
            "--confirm-throwaway",
            "--json",
        ],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == EXIT_OK, proc.stderr
    report = json.loads(proc.stdout)
    assert report["apply"] is False
    assert report["completed"] is True

    proc2 = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--source",
            str(src),
            "--restore-target",
            str(dst),
            "--confirm-throwaway",
            "--apply",
            "--json",
        ],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc2.returncode == EXIT_OK, proc2.stderr
    applied = json.loads(proc2.stdout)
    assert applied["writable_transaction_verified"] is True
    assert applied["rollback_verified"] is True

    bad = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--source",
            str(src),
            "--restore-target",
            str(tmp_path / "emails.sqlite"),
            "--confirm-throwaway",
            "--json",
        ],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert bad.returncode == EXIT_SAFETY
    assert "emails.sqlite" not in bad.stdout  # no path dump required; error on stderr
    assert bad.returncode != EXIT_OK


def test_cli_missing_confirm_exit(tmp_path: Path) -> None:
    src = tmp_path / "synthetic_src.sqlite"
    dst = tmp_path / "throwaway_dst.sqlite"
    build_synthetic_source(src)
    env = {**os.environ, "PYTHONPATH": str(REPO / "src")}
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--source",
            str(src),
            "--restore-target",
            str(dst),
            "--json",
        ],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == EXIT_PREFLIGHT
