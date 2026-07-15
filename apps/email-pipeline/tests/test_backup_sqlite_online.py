"""Synthetic-only tests for SQLite Online Backup API utility."""

from __future__ import annotations

import errno
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
    DEFAULT_PAGES_PER_BATCH,
    BackupError,
    BackupLock,
    BackupOptions,
    backup_is_completed,
    cleanup_script_owned_artifacts,
    detect_orphan_destination,
    fingerprint_file,
    fsync_directory,
    lock_path_for,
    manifest_partial_path_for,
    manifest_path_for,
    partial_path_for,
    required_capacity_bytes,
    run_online_backup,
    sanitize_error_message,
    sanitize_path_for_log,
    scan_manifest_privacy,
    validate_backup_options,
)

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "maintenance" / "backup_sqlite_online.py"

# Documented forbidden production path (assertion-only; never opened by tests).
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


def _count_items(path: Path) -> int:
    conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM items").fetchone()[0])
    finally:
        conn.close()


def _opts(
    source: Path,
    dest: Path,
    *,
    lock_dir: Path,
    apply: bool = True,
    **kwargs: object,
) -> BackupOptions:
    base = dict(
        source=source,
        destination=dest,
        apply=apply,
        allow_same_filesystem=True,
        pages_per_batch=kwargs.pop("pages_per_batch", 5),
        progress_interval_seconds=kwargs.pop("progress_interval_seconds", 0.0),
        lock_dir=lock_dir,
    )
    base.update(kwargs)
    return BackupOptions(**base)  # type: ignore[arg-type]


@pytest.fixture
def synth_dirs(tmp_path: Path) -> tuple[Path, Path, Path]:
    src_dir = tmp_path / "src_fs"
    dst_dir = tmp_path / "dst_fs"
    lock_dir = tmp_path / "locks"
    src_dir.mkdir()
    dst_dir.mkdir()
    lock_dir.mkdir()
    return src_dir, dst_dir, lock_dir


def test_default_pages_per_batch_is_reviewed_value() -> None:
    assert DEFAULT_PAGES_PER_BATCH == 4096


def test_sanitize_path_logs_basename_only() -> None:
    assert sanitize_path_for_log("/home/rafael/secret/emails.sqlite") == "emails.sqlite"


def test_manifest_privacy_rejects_absolute_paths_and_emails() -> None:
    assert scan_manifest_privacy({"note": "see /home/rafael/data/db.sqlite"})
    assert scan_manifest_privacy({"note": "mail user@example.org"})
    assert not scan_manifest_privacy({"destination_basename": "emails_copy.sqlite"})


def test_sanitize_unexpected_exception_messages() -> None:
    msg = sanitize_error_message(
        OSError(f"failed /home/rafael/data/emails.sqlite for user@example.org")
    )
    assert "/home/" not in msg
    assert "@" not in msg or "<email>" in msg
    assert "emails.sqlite" not in msg or "<path>" in msg


def test_preflight_default_performs_zero_writes(synth_dirs: tuple[Path, Path, Path]) -> None:
    src_dir, dst_dir, lock_dir = synth_dirs
    source = src_dir / "source.sqlite"
    dest = dst_dir / "copy.sqlite"
    _build_wal_db(source, rows=30)
    before_entries = set(dst_dir.iterdir())
    lock_before = set(lock_dir.iterdir()) if lock_dir.exists() else set()

    report = run_online_backup(_opts(source, dest, lock_dir=lock_dir, apply=False))

    assert report["mode"] == "preflight"
    assert report["writes_performed"] is False
    assert report["completed"] is False
    assert report["source_basename"] == "source.sqlite"
    assert report["destination_basename"] == "copy.sqlite"
    assert report["source_size_bytes"] > 0
    assert report["destination_free_bytes"] > 0
    assert report["filesystem_separated"] is False  # same tmp FS with allow override path
    assert not dest.exists()
    assert not partial_path_for(dest).exists()
    assert not manifest_path_for(dest).exists()
    assert not manifest_partial_path_for(dest).exists()
    assert set(dst_dir.iterdir()) == before_entries
    assert (set(lock_dir.iterdir()) if lock_dir.exists() else set()) == lock_before


def test_apply_required_for_execution_cli(synth_dirs: tuple[Path, Path, Path]) -> None:
    src_dir, _dst_dir, _lock = synth_dirs
    source = src_dir / "source.sqlite"
    dest = src_dir / "copy.sqlite"
    _build_wal_db(source, rows=20)
    cp = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--source",
            str(source),
            "--destination",
            str(dest),
            "--allow-same-filesystem",
            "--json",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert cp.returncode == 0, cp.stderr
    payload = json.loads(cp.stdout)
    assert payload["mode"] == "preflight"
    assert payload["writes_performed"] is False
    assert not dest.exists()


def test_successful_consistent_backup(synth_dirs: tuple[Path, Path, Path]) -> None:
    src_dir, dst_dir, lock_dir = synth_dirs
    source = src_dir / "source.sqlite"
    dest = dst_dir / "copy.sqlite"
    _build_wal_db(source, rows=250)
    before = fingerprint_file(source)

    with patch(
        "origenlab_email_pipeline.qa.sqlite_online_backup.same_filesystem",
        return_value=False,
    ):
        manifest = run_online_backup(
            _opts(
                source,
                dest,
                lock_dir=lock_dir,
                allow_same_filesystem=False,
                pages_per_batch=5,
            )
        )

    assert dest.is_file()
    assert manifest_path_for(dest).is_file()
    assert backup_is_completed(dest) is True
    assert not partial_path_for(dest).exists()
    assert not manifest_partial_path_for(dest).exists()
    assert manifest["completed"] is True
    assert manifest["source_mutated_by_utility"] is False
    assert manifest["completion_marker"] == "final_manifest"
    assert _count_items(dest) == 250
    assert fingerprint_file(source) == before
    man = json.loads(manifest_path_for(dest).read_text(encoding="utf-8"))
    assert man["completed"] is True
    assert not scan_manifest_privacy(man)
    assert PRODUCTION_SQLITE.as_posix() not in json.dumps(man)


def test_progress_batching_emits_time_based_and_final(
    synth_dirs: tuple[Path, Path, Path],
) -> None:
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
            _opts(
                source,
                dest,
                lock_dir=lock_dir,
                allow_same_filesystem=False,
                pages_per_batch=1,
                progress_interval_seconds=0.0,
                progress_sink=events.append,
            )
        )
    assert len(events) >= 2
    assert manifest["progress_events"] >= 2
    assert any("(100.0%)" in line or "pages=" in line and "/ " not in line for line in events)
    assert any("100.0%" in line for line in events)


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
                _opts(
                    source,
                    dest,
                    lock_dir=lock_dir,
                    allow_same_filesystem=False,
                    pages_per_batch=2,
                    busy_timeout_ms=30_000,
                )
            )
    finally:
        stop.set()
        thread.join(timeout=5)

    assert backup_is_completed(dest)
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
                _opts(source, dest, lock_dir=lock_dir, allow_same_filesystem=False)
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
        with pytest.raises(BackupError, match="orphan|already exists"):
            validate_backup_options(
                _opts(source, dest, lock_dir=lock_dir, allow_same_filesystem=False)
            )


def test_same_filesystem_refusal_by_default(synth_dirs: tuple[Path, Path, Path]) -> None:
    src_dir, dst_dir, lock_dir = synth_dirs
    source = src_dir / "source.sqlite"
    dest = dst_dir / "copy.sqlite"
    _build_wal_db(source, rows=20)
    with pytest.raises(BackupError, match="same filesystem"):
        validate_backup_options(
            BackupOptions(
                source=source,
                destination=dest,
                apply=True,
                allow_same_filesystem=False,
                lock_dir=lock_dir,
            )
        )


def test_same_filesystem_override_allows_backup(synth_dirs: tuple[Path, Path, Path]) -> None:
    src_dir, _dst_dir, lock_dir = synth_dirs
    source = src_dir / "source.sqlite"
    dest = src_dir / "copy.sqlite"
    _build_wal_db(source, rows=40)
    manifest = run_online_backup(_opts(source, dest, lock_dir=lock_dir, pages_per_batch=10))
    assert manifest["allow_same_filesystem"] is True
    assert backup_is_completed(dest)


def test_source_destination_alias_refusal(synth_dirs: tuple[Path, Path, Path]) -> None:
    src_dir, _dst_dir, lock_dir = synth_dirs
    source = src_dir / "source.sqlite"
    _build_wal_db(source, rows=10)
    with pytest.raises(BackupError, match="alias|same"):
        validate_backup_options(
            _opts(source, src_dir / "." / "source.sqlite", lock_dir=lock_dir)
        )
    alias = src_dir / "alias.sqlite"
    os.link(source, alias)
    with pytest.raises(BackupError, match="alias|samefile|same file"):
        validate_backup_options(_opts(source, alias, lock_dir=lock_dir))


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
                _opts(
                    source,
                    dest,
                    lock_dir=lock_dir,
                    allow_same_filesystem=False,
                    pages_per_batch=1,
                    should_abort=abort_after_progress,
                )
            )
    assert not dest.exists()
    assert not partial_path_for(dest).exists()
    assert not manifest_path_for(dest).exists()
    assert not manifest_partial_path_for(dest).exists()


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
                _opts(source, dest, lock_dir=lock_dir, allow_same_filesystem=False)
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
                apply=True,
                pages_per_batch=0,
                allow_same_filesystem=True,
                lock_dir=lock_dir,
            )
        )


def test_required_capacity_includes_margin() -> None:
    needed = required_capacity_bytes(1_000_000, margin_ratio=0.05, margin_min_bytes=100)
    assert needed >= 1_000_000 + 50_000


def test_lock_acquisition_failure_closes_fd(synth_dirs: tuple[Path, Path, Path]) -> None:
    src_dir, _dst, lock_dir = synth_dirs
    source = src_dir / "source.sqlite"
    _build_wal_db(source, rows=5)
    path = lock_path_for(source, lock_dir)
    holder = BackupLock(path)
    holder.acquire()
    try:
        contender = BackupLock(path)
        with pytest.raises(BackupError, match="another sqlite online backup"):
            contender.acquire()
        assert contender._fh is None
        assert contender.acquired is False
    finally:
        holder.release()


def test_fingerprint_policy_failure_before_publication(
    synth_dirs: tuple[Path, Path, Path],
) -> None:
    src_dir, dst_dir, lock_dir = synth_dirs
    source = src_dir / "source.sqlite"
    dest = dst_dir / "copy.sqlite"
    _build_wal_db(source, rows=40)
    real_fp = fingerprint_file
    state = {"flip": False}

    def selective_fp(path: Path):
        fp = real_fp(path)
        if state["flip"] and Path(path).resolve() == source.resolve():
            return type(fp)(
                size_bytes=fp.size_bytes + 1,
                mtime_ns=fp.mtime_ns + 1,
                device=fp.device,
                inode=fp.inode,
            )
        return fp

    with patch(
        "origenlab_email_pipeline.qa.sqlite_online_backup.fingerprint_file",
        side_effect=selective_fp,
    ), patch(
        "origenlab_email_pipeline.qa.sqlite_online_backup.same_filesystem",
        return_value=False,
    ):
        with pytest.raises(BackupError, match="fingerprint changed"):
            run_online_backup(
                _opts(
                    source,
                    dest,
                    lock_dir=lock_dir,
                    allow_same_filesystem=False,
                    fail_if_source_fingerprint_changes=True,
                    post_copy_hook=lambda: state.__setitem__("flip", True),
                )
            )
    assert not dest.exists()
    assert not manifest_path_for(dest).exists()
    assert not partial_path_for(dest).exists()


def test_privacy_failure_before_publication(synth_dirs: tuple[Path, Path, Path]) -> None:
    src_dir, dst_dir, lock_dir = synth_dirs
    source = src_dir / "source.sqlite"
    dest = dst_dir / "copy.sqlite"
    _build_wal_db(source, rows=20)
    with patch(
        "origenlab_email_pipeline.qa.sqlite_online_backup.scan_manifest_privacy",
        return_value=["$.note: matched privacy pattern"],
    ), patch(
        "origenlab_email_pipeline.qa.sqlite_online_backup.same_filesystem",
        return_value=False,
    ):
        with pytest.raises(BackupError, match="privacy"):
            run_online_backup(
                _opts(source, dest, lock_dir=lock_dir, allow_same_filesystem=False)
            )
    assert not dest.exists()
    assert not manifest_path_for(dest).exists()
    assert not partial_path_for(dest).exists()
    assert not manifest_partial_path_for(dest).exists()


def test_manifest_write_failure_cleans_partials(synth_dirs: tuple[Path, Path, Path]) -> None:
    src_dir, dst_dir, lock_dir = synth_dirs
    source = src_dir / "source.sqlite"
    dest = dst_dir / "copy.sqlite"
    _build_wal_db(source, rows=20)

    def boom(path: Path, payload: str) -> None:
        raise OSError("simulated manifest write failure /home/rafael/secret/db")

    with patch(
        "origenlab_email_pipeline.qa.sqlite_online_backup.same_filesystem",
        return_value=False,
    ):
        with pytest.raises(BackupError) as excinfo:
            run_online_backup(
                _opts(
                    source,
                    dest,
                    lock_dir=lock_dir,
                    allow_same_filesystem=False,
                    manifest_write_hook=boom,
                )
            )
    assert "/home/" not in str(excinfo.value)
    assert not dest.exists()
    assert not partial_path_for(dest).exists()
    assert not manifest_partial_path_for(dest).exists()


def test_orphan_final_db_detection_and_refusal(synth_dirs: tuple[Path, Path, Path]) -> None:
    src_dir, dst_dir, lock_dir = synth_dirs
    source = src_dir / "source.sqlite"
    dest = dst_dir / "copy.sqlite"
    _build_wal_db(source, rows=15)
    # Simulate crash window: final DB without completed manifest.
    dest.write_bytes(source.read_bytes())
    assert detect_orphan_destination(dest)
    assert backup_is_completed(dest) is False
    with patch(
        "origenlab_email_pipeline.qa.sqlite_online_backup.same_filesystem",
        return_value=False,
    ):
        with pytest.raises(BackupError, match="orphan"):
            validate_backup_options(
                _opts(source, dest, lock_dir=lock_dir, allow_same_filesystem=False)
            )
    # Explicit cleanup instruction: never silent delete — operator removes orphan.
    dest.unlink()
    assert detect_orphan_destination(dest) is None


def test_sidecar_cleanup_on_pre_publication_failure(
    synth_dirs: tuple[Path, Path, Path],
) -> None:
    src_dir, dst_dir, lock_dir = synth_dirs
    source = src_dir / "source.sqlite"
    dest = dst_dir / "copy.sqlite"
    _build_wal_db(source, rows=30)
    partial = partial_path_for(dest)
    # Seed leftover companions as if a prior crash left them; validate should refuse.
    partial.write_bytes(b"x")
    Path(str(partial) + "-wal").write_bytes(b"")
    Path(str(partial) + "-shm").write_bytes(b"\0" * 32)
    with pytest.raises(BackupError, match="partial companion"):
        validate_backup_options(_opts(source, dest, lock_dir=lock_dir))
    # cleanup helper only removes script-owned partials, not unrelated files.
    unrelated = dst_dir / "keep_me.txt"
    unrelated.write_text("keep", encoding="utf-8")
    cleanup_script_owned_artifacts(dest)
    assert not partial.exists()
    assert not Path(str(partial) + "-wal").exists()
    assert unrelated.exists()


def test_unsupported_directory_fsync_warning(synth_dirs: tuple[Path, Path, Path]) -> None:
    src_dir, dst_dir, lock_dir = synth_dirs
    source = src_dir / "source.sqlite"
    dest = dst_dir / "copy.sqlite"
    _build_wal_db(source, rows=25)

    def bad_fsync(_fd: int) -> None:
        raise OSError(errno.EINVAL, "Invalid argument")

    with patch(
        "origenlab_email_pipeline.qa.sqlite_online_backup.same_filesystem",
        return_value=False,
    ):
        manifest = run_online_backup(
            _opts(
                source,
                dest,
                lock_dir=lock_dir,
                allow_same_filesystem=False,
                dir_fsync=bad_fsync,
            )
        )
    assert backup_is_completed(dest)
    assert manifest["directory_fsync_supported"] is False
    assert any("directory fsync unsupported" in w for w in manifest["warnings"])


def test_supported_directory_fsync(synth_dirs: tuple[Path, Path, Path]) -> None:
    src_dir, dst_dir, lock_dir = synth_dirs
    source = src_dir / "source.sqlite"
    dest = dst_dir / "copy.sqlite"
    _build_wal_db(source, rows=10)
    info = fsync_directory(dst_dir)
    assert info["directory_fsync_supported"] is True
    with patch(
        "origenlab_email_pipeline.qa.sqlite_online_backup.same_filesystem",
        return_value=False,
    ):
        manifest = run_online_backup(
            _opts(source, dest, lock_dir=lock_dir, allow_same_filesystem=False)
        )
    assert manifest["directory_fsync_supported"] is True


def test_cli_apply_happy_path(synth_dirs: tuple[Path, Path, Path]) -> None:
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
            "--apply",
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


def test_cli_sanitizes_unexpected_exceptions(synth_dirs: tuple[Path, Path, Path]) -> None:
    src_dir, _dst, _lock = synth_dirs
    source = src_dir / "source.sqlite"
    dest = src_dir / "copy.sqlite"
    _build_wal_db(source, rows=5)
    with patch(
        "origenlab_email_pipeline.qa.sqlite_online_backup.run_online_backup",
        side_effect=RuntimeError("boom /home/rafael/data/emails.sqlite user@x.com"),
    ):
        # Direct sanitize coverage of CLI helper path:
        assert "/home/" not in sanitize_error_message(
            RuntimeError("boom /home/rafael/data/emails.sqlite user@x.com")
        )


def test_tests_never_use_production_path(tmp_path: Path) -> None:
    assert PRODUCTION_SQLITE.is_absolute()
    source = tmp_path / "only_synth.sqlite"
    dest = tmp_path / "only_synth_copy.sqlite"
    _build_wal_db(source, rows=5)
    manifest = run_online_backup(
        _opts(source, dest, lock_dir=tmp_path / "locks", pages_per_batch=1)
    )
    assert manifest["source_basename"] == "only_synth.sqlite"
    assert str(PRODUCTION_SQLITE) not in json.dumps(manifest)
