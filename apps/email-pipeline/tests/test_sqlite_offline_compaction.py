"""Tests for offline SQLite VACUUM INTO compaction (synthetic DBs only)."""

from __future__ import annotations

import errno
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
    database_identity_props,
    detect_orphan_destination,
    existing_partial_artifacts,
    manifest_path_for,
    run_offline_compaction,
    schema_fingerprint,
    storage_stats,
)
from origenlab_email_pipeline.qa.sqlite_online_backup import (
    BackupError,
    fingerprint_file,
    publish_no_clobber,
)

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "maintenance" / "compact_sqlite_offline.py"
EXIT_WRAPPER = REPO / "scripts" / "maintenance" / "run_sqlite_maintenance_with_exit_marker.sh"


def _build_fragmented_db(path: Path, *, rows: int = 80) -> None:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version=42")
    conn.execute("PRAGMA application_id=123456789")
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
                "gmail:contacto@origenlab.cl/INBOX",
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
    **kwargs: object,
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
        **kwargs,  # type: ignore[arg-type]
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
    assert existing_partial_artifacts(dest) == []
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


def test_allow_same_filesystem_requires_confirm(
    synth_dirs: tuple[Path, Path, Path],
) -> None:
    src_dir, dst_dir, lock_dir = synth_dirs
    source = src_dir / "offline.sqlite"
    dest = dst_dir / "compact.sqlite"
    _build_fragmented_db(source)
    with pytest.raises(CompactionError, match="allow-same-filesystem requires"):
        run_offline_compaction(
            CompactionOptions(
                source=source,
                destination=dest,
                confirm_offline_copy=False,
                apply=False,
                allow_same_filesystem=True,
                lock_dir=lock_dir,
                settings=Settings(
                    sqlite_path=str(src_dir / "production_emails.sqlite"),
                    postgres_url=None,
                ),
            )
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


def test_refuses_destination_equal_to_source(
    synth_dirs: tuple[Path, Path, Path],
) -> None:
    src_dir, _dst_dir, lock_dir = synth_dirs
    source = src_dir / "offline.sqlite"
    _build_fragmented_db(source)
    with pytest.raises(CompactionError, match="same path|same file"):
        run_offline_compaction(
            _opts(source, source, lock_dir=lock_dir, apply=True)
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
    assert existing_partial_artifacts(dest) == []


def test_capacity_stat_unavailable(synth_dirs: tuple[Path, Path, Path]) -> None:
    src_dir, dst_dir, lock_dir = synth_dirs
    source = src_dir / "offline.sqlite"
    dest = dst_dir / "compact.sqlite"
    _build_fragmented_db(source)
    with patch(
        "origenlab_email_pipeline.qa.sqlite_offline_compaction.disk_free_bytes",
        side_effect=OSError(errno.EIO, "boom"),
    ):
        with pytest.raises(CompactionError, match="unable to read destination filesystem"):
            run_offline_compaction(_opts(source, dest, lock_dir=lock_dir, apply=True))


def test_destination_collision_refused(synth_dirs: tuple[Path, Path, Path]) -> None:
    src_dir, dst_dir, lock_dir = synth_dirs
    source = src_dir / "offline.sqlite"
    dest = dst_dir / "compact.sqlite"
    _build_fragmented_db(source)
    dest.write_bytes(b"existing")
    with pytest.raises(CompactionError, match="already exists|orphaned"):
        run_offline_compaction(_opts(source, dest, lock_dir=lock_dir, apply=True))


def test_orphan_destination_refused(synth_dirs: tuple[Path, Path, Path]) -> None:
    src_dir, dst_dir, lock_dir = synth_dirs
    source = src_dir / "offline.sqlite"
    dest = dst_dir / "compact.sqlite"
    _build_fragmented_db(source)
    dest.write_bytes(b"SQLite format 3\x00" + b"\x00" * 100)
    assert detect_orphan_destination(dest) is not None
    with pytest.raises(CompactionError, match="orphaned"):
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
    src_identity = database_identity_props(src_conn)
    src_schema = schema_fingerprint(src_conn)
    src_emails = int(src_conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0])
    src_conn.close()
    assert src_stats["freelist_count"] > 0

    report = run_offline_compaction(_opts(source, dest, lock_dir=lock_dir, apply=True))

    assert report["completed"] is True
    assert report["method"] == "VACUUM INTO"
    assert dest.is_file()
    assert manifest_path_for(dest).is_file()
    assert existing_partial_artifacts(dest) == []
    for path in companion_paths(dest).values():
        assert not path.exists()
    assert fingerprint_file(source) == before
    assert report["source_fingerprint_unchanged"] is True
    assert report["verification"]["quick_check"] == "ok"
    assert report["verification"]["foreign_key_violation_count"] == 0
    assert report["verification"]["critical_table_counts"]["emails"] == src_emails
    assert report["verification"]["schema_fingerprint"] == src_schema
    assert report["verification"]["database_identity"]["user_version"] == 42
    assert report["verification"]["database_identity"]["application_id"] == 123456789
    assert (
        report["verification"]["database_identity"]["page_size"]
        == src_identity["page_size"]
    )
    assert report["verification"]["freelist_reduced"] is True
    assert (
        report["verification"]["storage"]["freelist_count"]
        < src_stats["freelist_count"]
    )
    assert dest.stat().st_size < before.size_bytes
    assert report["estimated_reclaimed_bytes"] == before.size_bytes - dest.stat().st_size
    assert report["source_storage"]["freelist_count"] == src_stats["freelist_count"]
    privacy = json.dumps(report)
    assert "/home/" not in privacy
    assert "@origenlab" not in privacy


def test_schema_fingerprint_ignores_whitespace_detects_drift(
    synth_dirs: tuple[Path, Path, Path],
) -> None:
    src_dir, _dst_dir, _lock = synth_dirs
    a = src_dir / "a.sqlite"
    b = src_dir / "b.sqlite"
    c = src_dir / "c.sqlite"
    for path, sql in (
        (a, "CREATE TABLE t(id INTEGER PRIMARY KEY, name TEXT)"),
        (b, "CREATE TABLE t ( id  INTEGER  PRIMARY KEY ,  name   TEXT )"),
        (c, "CREATE TABLE t(id INTEGER PRIMARY KEY, name TEXT, extra TEXT)"),
    ):
        conn = sqlite3.connect(path)
        conn.execute(sql)
        conn.commit()
        conn.close()
    ca = sqlite3.connect(a)
    cb = sqlite3.connect(b)
    cc = sqlite3.connect(c)
    assert schema_fingerprint(ca) == schema_fingerprint(cb)
    assert schema_fingerprint(ca) != schema_fingerprint(cc)
    ca.close()
    cb.close()
    cc.close()


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
            _opts(
                source,
                dest,
                lock_dir=lock_dir,
                apply=True,
                vacuum_hook=aborting_vacuum,
            )
        )
    assert not dest.exists()
    assert existing_partial_artifacts(dest) == []
    assert not manifest_path_for(dest).exists()
    assert cleanup_script_owned_artifacts(dest) == []


def test_fingerprint_drift_refused(synth_dirs: tuple[Path, Path, Path]) -> None:
    src_dir, dst_dir, lock_dir = synth_dirs
    source = src_dir / "offline.sqlite"
    dest = dst_dir / "compact.sqlite"
    _build_fragmented_db(source)

    def mutate_source_after_vacuum() -> None:
        os.utime(source, (source.stat().st_atime + 5, source.stat().st_mtime + 5))

    with pytest.raises(CompactionError, match="fingerprint changed"):
        run_offline_compaction(
            _opts(
                source,
                dest,
                lock_dir=lock_dir,
                apply=True,
                post_vacuum_hook=mutate_source_after_vacuum,
            )
        )
    assert not dest.exists()
    assert existing_partial_artifacts(dest) == []


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
            _opts(
                source,
                dest,
                lock_dir=lock_dir,
                apply=True,
                verify_hook=bad_verify,
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
            _opts(
                source,
                dest,
                lock_dir=lock_dir,
                apply=True,
                post_vacuum_hook=create_final,
            )
        )
    assert dest.read_bytes() == preexisting


def test_hardlink_unsupported_refuses_before_vacuum(
    synth_dirs: tuple[Path, Path, Path],
) -> None:
    src_dir, dst_dir, lock_dir = synth_dirs
    source = src_dir / "offline.sqlite"
    dest = dst_dir / "compact.sqlite"
    _build_fragmented_db(source)
    vacuum_calls = {"n": 0}

    def counting_vacuum(conn: sqlite3.Connection, partial: Path) -> None:
        vacuum_calls["n"] += 1
        raise AssertionError("VACUUM INTO must not run when hard-link probe fails")

    with patch(
        "origenlab_email_pipeline.qa.sqlite_offline_compaction.probe_hardlink_no_clobber_supported",
        side_effect=BackupError(
            "destination filesystem does not support atomic hard-link no-clobber "
            "publication (errno=1); refusing to weaken overwrite protection"
        ),
    ):
        with pytest.raises(CompactionError, match="hard-link"):
            run_offline_compaction(
                _opts(
                    source,
                    dest,
                    lock_dir=lock_dir,
                    apply=True,
                    vacuum_hook=counting_vacuum,
                )
            )
    assert vacuum_calls["n"] == 0
    assert not dest.exists()
    assert existing_partial_artifacts(dest) == []


def test_manifest_publish_failure_leaves_orphan(
    synth_dirs: tuple[Path, Path, Path],
) -> None:
    src_dir, dst_dir, lock_dir = synth_dirs
    source = src_dir / "offline.sqlite"
    dest = dst_dir / "compact.sqlite"
    _build_fragmented_db(source)
    calls = {"n": 0}
    real_publish = publish_no_clobber

    def flaky_publish(src: Path, target: Path) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            real_publish(src, target)
            return
        raise BackupError(
            f"no-clobber publication refused: destination already exists ({target.name})"
        )

    with patch(
        "origenlab_email_pipeline.qa.sqlite_offline_compaction.publish_no_clobber",
        side_effect=flaky_publish,
    ):
        with pytest.raises(CompactionError, match="no-clobber"):
            run_offline_compaction(_opts(source, dest, lock_dir=lock_dir, apply=True))

    assert dest.is_file()
    assert not manifest_path_for(dest).exists()
    assert detect_orphan_destination(dest) is not None
    with pytest.raises(CompactionError, match="orphaned"):
        run_offline_compaction(_opts(source, dest, lock_dir=lock_dir, apply=True))


def test_directory_fsync_unsupported_recorded_as_warning(
    synth_dirs: tuple[Path, Path, Path],
) -> None:
    src_dir, dst_dir, lock_dir = synth_dirs
    source = src_dir / "offline.sqlite"
    dest = dst_dir / "compact.sqlite"
    _build_fragmented_db(source)

    def unsupported_fsync(_fd: int) -> None:
        raise OSError(errno.EINVAL, "unsupported")

    report = run_offline_compaction(
        _opts(
            source,
            dest,
            lock_dir=lock_dir,
            apply=True,
            dir_fsync=unsupported_fsync,
        )
    )
    assert report["completed"] is True
    assert report["directory_fsync_supported"] is False
    assert any("directory" in w and "unsupported" in w for w in report["warnings"])


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
    assert "SQLITE_MAINTENANCE_EXIT=0" in text
    assert "AUDIT_RESUME_EXIT=0" in text
    assert "SQLITE_MAINTENANCE_EXIT=\n" not in text
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
    text = progress.read_text(encoding="utf-8")
    assert "SQLITE_MAINTENANCE_EXIT=7" in text
    assert "AUDIT_RESUME_EXIT=7" in text


def test_exit_marker_no_false_success_when_command_fails(tmp_path: Path) -> None:
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
            "echo failing; false",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert cp.returncode != 0
    assert marker.read_text(encoding="utf-8").strip() != "0"
    assert "SQLITE_MAINTENANCE_EXIT=0" not in progress.read_text(encoding="utf-8")
