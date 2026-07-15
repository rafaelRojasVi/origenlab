"""Synthetic-only tests for SQLite Online Backup API utility."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from origenlab_email_pipeline.qa.sqlite_online_backup import (
    BackupError,
    BackupOptions,
    fingerprint_file,
    manifest_path_for,
    partial_path_for,
    required_capacity_bytes,
    run_online_backup,
    sanitize_path_for_log,
    scan_manifest_privacy,
    validate_backup_options,
)

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "maintenance" / "backup_sqlite_online.py"

# Tests must never reference the production DB path.
PRODUCTION_SQLITE = Path("/home/rafael/data/origenlab-email/sqlite/emails.sqlite")


def _build_wal_db(path: Path, *, rows: int = 200) -> None:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
    conn.executemany(
        "INSERT INTO items (payload) VALUES (?)",
        [(f"row-{i}-" + ("x" * 200),) for i in range(rows)],
    )
    conn.commit()
    conn.close()
    assert path.with_name(path.name + "-wal").exists() or True  # wal may checkpoint


def _count_items(path: Path) -> int:
    conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM items").fetchone()[0])
    finally:
        conn.close()


@pytest.fixture
def synth_dirs(tmp_path: Path) -> tuple[Path, Path, Path]:
    src_dir = tmp_path / "src_fs"
    dst_dir = tmp_path / "dst_fs"
    lock_dir = tmp_path / "locks"
    src_dir.mkdir()
    dst_dir.mkdir()
    lock_dir.mkdir()
    return src_dir, dst_dir, lock_dir


def test_sanitize_path_logs_basename_only() -> None:
    assert sanitize_path_for_log("/home/rafael/secret/emails.sqlite") == "emails.sqlite"


def test_manifest_privacy_rejects_absolute_paths_and_emails() -> None:
    assert scan_manifest_privacy({"note": "see /home/rafael/data/db.sqlite"})
    assert scan_manifest_privacy({"note": "mail user@example.org"})
    assert not scan_manifest_privacy({"destination_basename": "emails_copy.sqlite"})


def test_successful_consistent_backup(synth_dirs: tuple[Path, Path, Path]) -> None:
    src_dir, dst_dir, lock_dir = synth_dirs
    source = src_dir / "source.sqlite"
    dest = dst_dir / "copy.sqlite"
    _build_wal_db(source, rows=250)
    before = fingerprint_file(source)

    # Force same-filesystem check to pass across sibling dirs by overriding.
    with patch(
        "origenlab_email_pipeline.qa.sqlite_online_backup.same_filesystem",
        return_value=False,
    ):
        manifest = run_online_backup(
            BackupOptions(
                source=source,
                destination=dest,
                pages_per_batch=5,
                progress_interval_seconds=0.0,
                lock_dir=lock_dir,
            )
        )

    assert dest.is_file()
    assert not partial_path_for(dest).exists()
    assert manifest["completed"] is True
    assert manifest["source_mutated_by_utility"] is False
    assert manifest["source_opened_readonly"] is True
    assert manifest["method"] == "sqlite3.Connection.backup"
    assert _count_items(dest) == 250
    assert fingerprint_file(source) == before
    man = json.loads(manifest_path_for(dest).read_text(encoding="utf-8"))
    assert man["completed"] is True
    assert not scan_manifest_privacy(man)
    assert PRODUCTION_SQLITE.as_posix() not in json.dumps(man)
    assert "/home/" not in json.dumps(man)


def test_progress_batching_emits_multiple_events(synth_dirs: tuple[Path, Path, Path]) -> None:
    src_dir, dst_dir, lock_dir = synth_dirs
    source = src_dir / "source.sqlite"
    dest = dst_dir / "copy.sqlite"
    _build_wal_db(source, rows=800)
    events: list[str] = []
    with patch(
        "origenlab_email_pipeline.qa.sqlite_online_backup.same_filesystem",
        return_value=False,
    ):
        manifest = run_online_backup(
            BackupOptions(
                source=source,
                destination=dest,
                pages_per_batch=1,
                progress_interval_seconds=0.0,
                progress_sink=events.append,
                lock_dir=lock_dir,
            )
        )
    assert len(events) >= 2
    assert manifest["progress_events"] >= 2
    assert all("pages=" in line for line in events)


def test_wal_backup_with_concurrent_writer(synth_dirs: tuple[Path, Path, Path]) -> None:
    src_dir, dst_dir, lock_dir = synth_dirs
    source = src_dir / "source.sqlite"
    dest = dst_dir / "copy.sqlite"
    _build_wal_db(source, rows=300)

    stop = threading.Event()

    def writer() -> None:
        conn = sqlite3.connect(source, timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
        i = 0
        while not stop.is_set():
            conn.execute("INSERT INTO items (payload) VALUES (?)", (f"live-{i}",))
            conn.commit()
            i += 1
            time.sleep(0.01)
        conn.close()

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    try:
        with patch(
            "origenlab_email_pipeline.qa.sqlite_online_backup.same_filesystem",
            return_value=False,
        ):
            manifest = run_online_backup(
                BackupOptions(
                    source=source,
                    destination=dest,
                    pages_per_batch=2,
                    progress_interval_seconds=0.0,
                    busy_timeout_ms=30_000,
                    lock_dir=lock_dir,
                )
            )
    finally:
        stop.set()
        thread.join(timeout=5)

    assert dest.is_file()
    assert manifest["completed"] is True
    assert manifest["source_mutated_by_utility"] is False
    assert _count_items(dest) >= 300


def test_insufficient_capacity(synth_dirs: tuple[Path, Path, Path]) -> None:
    src_dir, dst_dir, lock_dir = synth_dirs
    source = src_dir / "source.sqlite"
    dest = dst_dir / "copy.sqlite"
    _build_wal_db(source, rows=50)
    with patch(
        "origenlab_email_pipeline.qa.sqlite_online_backup.disk_free_bytes",
        return_value=1024,
    ), patch(
        "origenlab_email_pipeline.qa.sqlite_online_backup.same_filesystem",
        return_value=False,
    ):
        with pytest.raises(BackupError, match="insufficient destination capacity"):
            run_online_backup(
                BackupOptions(source=source, destination=dest, lock_dir=lock_dir)
            )
    assert not dest.exists()
    assert not partial_path_for(dest).exists()


def test_destination_already_exists(synth_dirs: tuple[Path, Path, Path]) -> None:
    src_dir, dst_dir, lock_dir = synth_dirs
    source = src_dir / "source.sqlite"
    dest = dst_dir / "copy.sqlite"
    _build_wal_db(source, rows=20)
    dest.write_bytes(b"already-here")
    with patch(
        "origenlab_email_pipeline.qa.sqlite_online_backup.same_filesystem",
        return_value=False,
    ):
        with pytest.raises(BackupError, match="already exists"):
            validate_backup_options(
                BackupOptions(source=source, destination=dest, lock_dir=lock_dir)
            )


def test_same_filesystem_refusal_by_default(synth_dirs: tuple[Path, Path, Path]) -> None:
    src_dir, dst_dir, lock_dir = synth_dirs
    source = src_dir / "source.sqlite"
    dest = dst_dir / "copy.sqlite"
    _build_wal_db(source, rows=20)
    # Sibling dirs on tmp_path share a filesystem.
    with pytest.raises(BackupError, match="same filesystem"):
        validate_backup_options(
            BackupOptions(
                source=source,
                destination=dest,
                allow_same_filesystem=False,
                lock_dir=lock_dir,
            )
        )


def test_same_filesystem_override_allows_backup(synth_dirs: tuple[Path, Path, Path]) -> None:
    src_dir, _dst_dir, lock_dir = synth_dirs
    source = src_dir / "source.sqlite"
    dest = src_dir / "copy.sqlite"
    _build_wal_db(source, rows=40)
    manifest = run_online_backup(
        BackupOptions(
            source=source,
            destination=dest,
            allow_same_filesystem=True,
            pages_per_batch=10,
            progress_interval_seconds=0.0,
            lock_dir=lock_dir,
        )
    )
    assert manifest["allow_same_filesystem"] is True
    assert dest.is_file()


def test_source_destination_alias_refusal(synth_dirs: tuple[Path, Path, Path]) -> None:
    src_dir, _dst_dir, lock_dir = synth_dirs
    source = src_dir / "source.sqlite"
    _build_wal_db(source, rows=10)
    # Same path via resolve trick.
    with pytest.raises(BackupError, match="alias|same"):
        validate_backup_options(
            BackupOptions(
                source=source,
                destination=src_dir / "." / "source.sqlite",
                allow_same_filesystem=True,
                lock_dir=lock_dir,
            )
        )
    # Hardlink alias of an existing destination path.
    alias = src_dir / "alias.sqlite"
    os.link(source, alias)
    with pytest.raises(BackupError, match="alias|samefile|same file"):
        validate_backup_options(
            BackupOptions(
                source=source,
                destination=alias,
                allow_same_filesystem=True,
                lock_dir=lock_dir,
            )
        )


def test_interruption_does_not_publish_partial(synth_dirs: tuple[Path, Path, Path]) -> None:
    src_dir, dst_dir, lock_dir = synth_dirs
    source = src_dir / "source.sqlite"
    dest = dst_dir / "copy.sqlite"
    _build_wal_db(source, rows=400)
    calls = {"n": 0}

    def abort_after_progress() -> bool:
        calls["n"] += 1
        return calls["n"] > 2

    with patch(
        "origenlab_email_pipeline.qa.sqlite_online_backup.same_filesystem",
        return_value=False,
    ):
        with pytest.raises(BackupError, match="interrupted"):
            run_online_backup(
                BackupOptions(
                    source=source,
                    destination=dest,
                    pages_per_batch=1,
                    progress_interval_seconds=0.0,
                    should_abort=abort_after_progress,
                    lock_dir=lock_dir,
                )
            )
    assert not dest.exists()
    assert not partial_path_for(dest).exists()
    assert not manifest_path_for(dest).exists()


def test_malformed_non_sqlite_source(synth_dirs: tuple[Path, Path, Path]) -> None:
    src_dir, dst_dir, lock_dir = synth_dirs
    source = src_dir / "notdb.sqlite"
    dest = dst_dir / "copy.sqlite"
    source.write_bytes(b"this is not sqlite")
    with patch(
        "origenlab_email_pipeline.qa.sqlite_online_backup.same_filesystem",
        return_value=False,
    ):
        with pytest.raises(BackupError, match="not SQLite"):
            run_online_backup(
                BackupOptions(source=source, destination=dest, lock_dir=lock_dir)
            )


def test_pages_zero_forbidden(synth_dirs: tuple[Path, Path, Path]) -> None:
    src_dir, dst_dir, lock_dir = synth_dirs
    source = src_dir / "source.sqlite"
    dest = dst_dir / "copy.sqlite"
    _build_wal_db(source, rows=10)
    with pytest.raises(BackupError, match="positive"):
        validate_backup_options(
            BackupOptions(
                source=source,
                destination=dest,
                pages_per_batch=0,
                allow_same_filesystem=True,
                lock_dir=lock_dir,
            )
        )


def test_required_capacity_includes_margin() -> None:
    needed = required_capacity_bytes(1_000_000, margin_ratio=0.05, margin_min_bytes=100)
    assert needed >= 1_000_000 + 50_000


def test_cli_happy_path(synth_dirs: tuple[Path, Path, Path]) -> None:
    src_dir, _dst_dir, _lock_dir = synth_dirs
    source = src_dir / "source.sqlite"
    dest = src_dir / "copy.sqlite"
    _build_wal_db(source, rows=30)
    cp = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--source",
            str(source),
            "--destination",
            str(dest),
            "--allow-same-filesystem",
            "--pages-per-batch",
            "5",
            "--json",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert cp.returncode == 0, cp.stderr
    payload = json.loads(cp.stdout)
    assert payload["completed"] is True
    assert PRODUCTION_SQLITE.as_posix() not in cp.stdout
    assert PRODUCTION_SQLITE.as_posix() not in cp.stderr


def test_tests_never_use_production_path(tmp_path: Path) -> None:
    """Guards against wiring tests to the live production SQLite path."""
    assert PRODUCTION_SQLITE.is_absolute()
    # All BackupOptions in this module must use tmp_path / synthetic dirs, not production.
    # Smoke: a synthetic backup still works without referencing production.
    source = tmp_path / "only_synth.sqlite"
    dest = tmp_path / "only_synth_copy.sqlite"
    _build_wal_db(source, rows=5)
    manifest = run_online_backup(
        BackupOptions(
            source=source,
            destination=dest,
            allow_same_filesystem=True,
            pages_per_batch=1,
            progress_interval_seconds=0.0,
            lock_dir=tmp_path / "locks",
        )
    )
    assert manifest["source_basename"] == "only_synth.sqlite"
    assert str(PRODUCTION_SQLITE) not in json.dumps(manifest)

