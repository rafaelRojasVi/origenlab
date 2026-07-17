"""Tests for offline SQLite VACUUM INTO compaction (synthetic DBs only)."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from origenlab_email_pipeline.config import Settings
from origenlab_email_pipeline.qa.sqlite_offline_compaction import (
    CompactionError,
    CompactionOptions,
    cleanup_script_owned_artifacts,
    companion_paths,
    manifest_partial_path_for,
    manifest_path_for,
    partial_path_for,
    run_offline_compaction,
    storage_stats,
)
from origenlab_email_pipeline.qa.sqlite_online_backup import fingerprint_file

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "maintenance" / "compact_sqlite_offline.py"
EXIT_WRAPPER = REPO / "scripts" / "maintenance" / "run_sqlite_maintenance_with_exit_marker.sh"


def _build_fragmented_db(path: Path, *, rows: int = 80) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE emails (
          id INTEGER PRIMARY KEY,
          source_file TEXT,
          message_id TEXT,
          subject TEXT,
          body TEXT,
          body_html TEXT,
          body_text_raw TEXT,
          body_text_clean TEXT,
          full_body_clean TEXT,
          top_reply_clean TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE attachments (
          id INTEGER PRIMARY KEY,
          email_id INTEGER REFERENCES emails(id),
          filename TEXT
        )
        """
    )
    for i in range(1, rows + 1):
        body = ("payload-" + str(i) + "-") * 200
        conn.execute(
            """
            INSERT INTO emails (
              id, source_file, message_id, subject, body, body_html,
              body_text_raw, body_text_clean, full_body_clean, top_reply_clean
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                i,
                f"gmail:contacto@origenlab.cl/INBOX",
                f"<m{i}@x>",
                f"subj-{i}",
                body,
                f"<p>{body}</p>",
                body,
                body,
                body,
                body,
            ),
        )
        if i % 3 == 0:
            conn.execute(
                "INSERT INTO attachments (id, email_id, filename) VALUES (?,?,?)",
                (i, i, f"f{i}.pdf"),
            )
    conn.commit()
    # Delete half the rows to create freelist pages without reducing file size much.
    conn.execute("DELETE FROM attachments WHERE id % 2 = 0")
    conn.execute("DELETE FROM emails WHERE id % 2 = 0")
    conn.commit()
    conn.close()


@pytest.fixture
def synth_dirs(tmp_path: Path) -> tuple[Path, Path, Path]:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    lock = tmp_path / "locks"
    src.mkdir()
    dst.mkdir()
    lock.mkdir()
    return src, dst, lock


def _opts(
    source: Path,
    dest: Path,
    *,
    lock_dir: Path,
    apply: bool = False,
    confirm: bool = True,
    allow_same_fs: bool = True,
    settings: Settings | None = None,
) -> CompactionOptions:
    return CompactionOptions(
        source=source,
        destination=dest,
        confirm_offline_copy=confirm,
        apply=apply,
        allow_same_filesystem=allow_same_fs,
        lock_dir=lock_dir,
        settings=settings
        or Settings(
            sqlite_path=str(source.parent / "production_emails.sqlite"),
            postgres_url=None,
        ),
    )


def test_preflight_default_is_zero_write(synth_dirs: tuple[Path, Path, Path]) -> None:
    src_dir, dst_dir, lock_dir = synth_dirs
    source = src_dir / "offline.sqlite"
    dest = dst_dir / "compact.sqlite"
    _build_fragmented_db(source)
    before = fingerprint_file(source)
    report = run_offline_compaction(_opts(source, dest, lock_dir=lock_dir, apply=False))
    after = fingerprint_file(source)
    assert report["mode"] == "preflight"
    assert report["writes_performed"] is False
    assert report["completed"] is False
    assert before == after
    assert not dest.exists()
    assert not partial_path_for(dest).exists()
    assert not manifest_path_for(dest).exists()


def test_apply_requires_confirm_offline_copy(
    synth_dirs: tuple[Path, Path, Path],
) -> None:
    src_dir, dst_dir, lock_dir = synth_dirs
    source = src_dir / "offline.sqlite"
    dest = dst_dir / "compact.sqlite"
    _build_fragmented_db(source)
    with pytest.raises(CompactionError, match="confirm-offline-copy"):
        run_offline_compaction(
            _opts(source, dest, lock_dir=lock_dir, apply=True, confirm=False)
        )


def test_refuses_configured_production_db(
    synth_dirs: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    src_dir, dst_dir, lock_dir = synth_dirs
    prod = tmp_path / "emails.sqlite"
    _build_fragmented_db(prod)
    dest = dst_dir / "compact.sqlite"
    settings = Settings(sqlite_path=str(prod), postgres_url=None)
    with pytest.raises(CompactionError, match="production"):
        run_offline_compaction(
            CompactionOptions(
                source=prod,
                destination=dest,
                confirm_offline_copy=True,
                apply=True,
                allow_same_filesystem=True,
                lock_dir=lock_dir,
                settings=settings,
            )
        )


def test_refuses_source_sidecars(synth_dirs: tuple[Path, Path, Path]) -> None:
    src_dir, dst_dir, lock_dir = synth_dirs
    source = src_dir / "offline.sqlite"
    dest = dst_dir / "compact.sqlite"
    _build_fragmented_db(source)
    Path(str(source) + "-wal").write_bytes(b"x")
    with pytest.raises(CompactionError, match="companion wal"):
        run_offline_compaction(_opts(source, dest, lock_dir=lock_dir, apply=True))


def test_insufficient_capacity(synth_dirs: tuple[Path, Path, Path]) -> None:
    src_dir, dst_dir, lock_dir = synth_dirs
    source = src_dir / "offline.sqlite"
    dest = dst_dir / "compact.sqlite"
    _build_fragmented_db(source)
    with patch(
        "origenlab_email_pipeline.qa.sqlite_offline_compaction.disk_free_bytes",
        return_value=1,
    ):
        with pytest.raises(CompactionError, match="insufficient destination capacity"):
            run_offline_compaction(_opts(source, dest, lock_dir=lock_dir, apply=True))
    assert not dest.exists()
    assert not partial_path_for(dest).exists()


def test_destination_collision_refused(synth_dirs: tuple[Path, Path, Path]) -> None:
    src_dir, dst_dir, lock_dir = synth_dirs
    source = src_dir / "offline.sqlite"
    dest = dst_dir / "compact.sqlite"
    _build_fragmented_db(source)
    dest.write_bytes(b"existing")
    with pytest.raises(CompactionError, match="already exists"):
        run_offline_compaction(_opts(source, dest, lock_dir=lock_dir, apply=True))


def test_successful_compaction_shrinks_and_verifies(
    synth_dirs: tuple[Path, Path, Path],
) -> None:
    src_dir, dst_dir, lock_dir = synth_dirs
    source = src_dir / "offline.sqlite"
    dest = dst_dir / "compact.sqlite"
    _build_fragmented_db(source, rows=120)
    before = fingerprint_file(source)
    src_conn = sqlite3.connect(source)
    src_stats = storage_stats(src_conn)
    src_emails = int(src_conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0])
    src_conn.close()

    report = run_offline_compaction(_opts(source, dest, lock_dir=lock_dir, apply=True))

    assert report["completed"] is True
    assert report["method"] == "VACUUM INTO"
    assert dest.is_file()
    assert manifest_path_for(dest).is_file()
    assert not partial_path_for(dest).exists()
    assert not manifest_partial_path_for(dest).exists()
    for path in companion_paths(dest).values():
        assert not path.exists()
    assert fingerprint_file(source) == before
    assert report["source_fingerprint_unchanged"] is True
    assert report["verification"]["quick_check"] == "ok"
    assert report["verification"]["foreign_key_violation_count"] == 0
    assert report["verification"]["critical_table_counts"]["emails"] == src_emails
    assert dest.stat().st_size < before.size_bytes
    assert report["estimated_reclaimed_bytes"] == before.size_bytes - dest.stat().st_size
    assert report["source_storage"]["freelist_count"] == src_stats["freelist_count"]
    privacy = json.dumps(report)
    assert "/home/" not in privacy
    assert "@origenlab" not in privacy


def test_interruption_cleans_partial(synth_dirs: tuple[Path, Path, Path]) -> None:
    src_dir, dst_dir, lock_dir = synth_dirs
    source = src_dir / "offline.sqlite"
    dest = dst_dir / "compact.sqlite"
    _build_fragmented_db(source)

    def aborting_vacuum(conn: sqlite3.Connection, partial: Path) -> None:
        partial.write_bytes(b"incomplete")
        raise CompactionError("compaction aborted after VACUUM INTO")

    with pytest.raises(CompactionError, match="aborted"):
        run_offline_compaction(
            CompactionOptions(
                source=source,
                destination=dest,
                confirm_offline_copy=True,
                apply=True,
                allow_same_filesystem=True,
                lock_dir=lock_dir,
                vacuum_hook=aborting_vacuum,
                settings=Settings(
                    sqlite_path=str(src_dir / "production_emails.sqlite"),
                    postgres_url=None,
                ),
            )
        )
    assert not dest.exists()
    assert not partial_path_for(dest).exists()
    assert not manifest_path_for(dest).exists()
    assert cleanup_script_owned_artifacts(dest) == []


def test_fingerprint_drift_refused(synth_dirs: tuple[Path, Path, Path]) -> None:
    src_dir, dst_dir, lock_dir = synth_dirs
    source = src_dir / "offline.sqlite"
    dest = dst_dir / "compact.sqlite"
    _build_fragmented_db(source)

    def mutate_source_after_vacuum() -> None:
        # Touch mtime after vacuum to simulate drift.
        os.utime(source, (source.stat().st_atime + 5, source.stat().st_mtime + 5))

    with pytest.raises(CompactionError, match="fingerprint changed"):
        run_offline_compaction(
            CompactionOptions(
                source=source,
                destination=dest,
                confirm_offline_copy=True,
                apply=True,
                allow_same_filesystem=True,
                lock_dir=lock_dir,
                post_vacuum_hook=mutate_source_after_vacuum,
                settings=Settings(
                    sqlite_path=str(src_dir / "production_emails.sqlite"),
                    postgres_url=None,
                ),
            )
        )
    assert not dest.exists()
    assert not partial_path_for(dest).exists()


def test_validation_failure_refuses_publish(
    synth_dirs: tuple[Path, Path, Path],
) -> None:
    src_dir, dst_dir, lock_dir = synth_dirs
    source = src_dir / "offline.sqlite"
    dest = dst_dir / "compact.sqlite"
    _build_fragmented_db(source)

    def bad_verify(_partial: Path) -> dict:
        raise CompactionError("candidate quick_check failed: not ok")

    with pytest.raises(CompactionError, match="quick_check failed"):
        run_offline_compaction(
            CompactionOptions(
                source=source,
                destination=dest,
                confirm_offline_copy=True,
                apply=True,
                allow_same_filesystem=True,
                lock_dir=lock_dir,
                verify_hook=bad_verify,
                settings=Settings(
                    sqlite_path=str(src_dir / "production_emails.sqlite"),
                    postgres_url=None,
                ),
            )
        )
    assert not dest.exists()
    assert not manifest_path_for(dest).exists()


def test_publish_race_existing_destination(
    synth_dirs: tuple[Path, Path, Path],
) -> None:
    src_dir, dst_dir, lock_dir = synth_dirs
    source = src_dir / "offline.sqlite"
    dest = dst_dir / "compact.sqlite"
    _build_fragmented_db(source)
    preexisting = b"EXISTING-MUST-NOT-CHANGE"

    def create_final() -> None:
        dest.write_bytes(preexisting)

    with pytest.raises(CompactionError, match="no-clobber|already exists"):
        run_offline_compaction(
            CompactionOptions(
                source=source,
                destination=dest,
                confirm_offline_copy=True,
                apply=True,
                allow_same_filesystem=True,
                lock_dir=lock_dir,
                post_vacuum_hook=create_final,
                settings=Settings(
                    sqlite_path=str(src_dir / "production_emails.sqlite"),
                    postgres_url=None,
                ),
            )
        )
    assert dest.read_bytes() == preexisting


def test_cli_preflight_and_apply(synth_dirs: tuple[Path, Path, Path]) -> None:
    src_dir, dst_dir, _lock = synth_dirs
    source = src_dir / "offline.sqlite"
    dest = dst_dir / "compact.sqlite"
    _build_fragmented_db(source, rows=40)
    env = os.environ.copy()
    env["ORIGENLAB_SQLITE_PATH"] = str(src_dir / "production_emails.sqlite")
    cp = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--source",
            str(source),
            "--destination",
            str(dest),
            "--confirm-offline-copy",
            "--allow-same-filesystem",
            "--json",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert cp.returncode == 0, cp.stderr
    payload = json.loads(cp.stdout)
    assert payload["writes_performed"] is False

    cp2 = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--source",
            str(source),
            "--destination",
            str(dest),
            "--confirm-offline-copy",
            "--allow-same-filesystem",
            "--apply",
            "--json",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    assert cp2.returncode == 0, cp2.stderr
    payload2 = json.loads(cp2.stdout)
    assert payload2["completed"] is True
    assert dest.is_file()


def test_exit_marker_records_zero_on_success(tmp_path: Path) -> None:
    progress = tmp_path / "progress.log"
    marker = tmp_path / "exit.marker"
    cp = subprocess.run(
        [
            "bash",
            str(EXIT_WRAPPER),
            "--progress-log",
            str(progress),
            "--exit-marker",
            str(marker),
            "--",
            "bash",
            "-lc",
            "echo ok; exit 0",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert cp.returncode == 0
    assert marker.read_text(encoding="utf-8").strip() == "0"
    text = progress.read_text(encoding="utf-8")
    assert "AUDIT_RESUME_EXIT=0" in text
    assert "AUDIT_RESUME_EXIT=\n" not in text


def test_exit_marker_records_nonzero_on_failure(tmp_path: Path) -> None:
    progress = tmp_path / "progress.log"
    marker = tmp_path / "exit.marker"
    cp = subprocess.run(
        [
            "bash",
            str(EXIT_WRAPPER),
            "--progress-log",
            str(progress),
            "--exit-marker",
            str(marker),
            "--",
            "bash",
            "-lc",
            "exit 7",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert cp.returncode == 7
    assert marker.read_text(encoding="utf-8").strip() == "7"
    assert "AUDIT_RESUME_EXIT=7" in progress.read_text(encoding="utf-8")
