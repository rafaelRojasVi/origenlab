"""Synthetic writable-restore rehearsal (throwaway fixtures only).

Models a controlled writable restore against *small synthetic* SQLite files.
Never opens the real ~61 GiB compact candidate, never touches production, and
never performs cutover/swap.

Default is zero-write preflight. Writes require ``--confirm-throwaway`` and
``--apply``.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from origenlab_email_pipeline.config import Settings, canonical_production_sqlite_path, load_settings
from origenlab_email_pipeline.qa.sqlite_deep_audit import is_configured_production_db
from origenlab_email_pipeline.qa.sqlite_online_backup import (
    BackupError,
    assert_sqlite_header,
    disk_free_bytes,
    fingerprint_file,
    fsync_directory,
    fsync_file,
    paths_same_file,
    publish_no_clobber,
    sanitize_path_for_log,
)

REHEARSAL_SCHEMA_VERSION = 1
DEFAULT_CAPACITY_MARGIN_BYTES = 32 * 1024 * 1024
EXIT_OK = 0
EXIT_PREFLIGHT = 2
EXIT_APPLY = 3
EXIT_VERIFY = 4
EXIT_SAFETY = 5


class RehearsalError(BackupError):
    """Operator-facing rehearsal failure (sanitized)."""


@dataclass
class RehearsalOptions:
    source: Path
    restore_target: Path
    confirm_throwaway: bool = False
    apply: bool = False
    capacity_margin_bytes: int = DEFAULT_CAPACITY_MARGIN_BYTES
    settings: Settings | None = None
    progress_sink: Callable[[str], None] | None = None
    clock: Callable[[], float] = field(default=time.perf_counter)


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _progress(opts: RehearsalOptions, msg: str) -> None:
    if opts.progress_sink is not None:
        opts.progress_sink(msg)


def _refuse_production(path: Path, *, settings: Settings | None) -> None:
    settings = settings or load_settings(enable_dotenv=False)
    try:
        if is_configured_production_db(path, settings):
            raise RehearsalError(
                "refusing production SQLite path (including aliases/samefile/device+inode)"
            )
        canonical = canonical_production_sqlite_path()
        if paths_same_file(path, canonical) or path.resolve() == canonical.resolve():
            raise RehearsalError("refusing canonical production SQLite path")
    except RehearsalError:
        raise
    except OSError as exc:
        raise RehearsalError(
            f"unable to verify production exclusion ({type(exc).__name__})"
        ) from None


def _assert_throwaway_basename(path: Path) -> None:
    name = path.name.lower()
    if "throwaway" not in name and "rehearsal" not in name and "synthetic" not in name:
        raise RehearsalError(
            "restore target basename must include 'throwaway', 'rehearsal', or 'synthetic'"
        )
    if name in {"emails.sqlite", "emails.sqlite-wal", "emails.sqlite-shm"}:
        raise RehearsalError("refusing production-like basename")


def _companions(path: Path) -> list[str]:
    found: list[str] = []
    for suffix in ("-wal", "-shm", "-journal"):
        if Path(str(path) + suffix).exists():
            found.append(suffix.lstrip("-"))
    return found


def build_synthetic_source(path: Path, *, rows: int = 8) -> None:
    """Create a tiny throwaway SQLite file used as the rehearsal source."""
    if path.exists():
        raise RehearsalError("synthetic source already exists (no-clobber)")
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE emails (id INTEGER PRIMARY KEY, subject TEXT, marker TEXT)"
        )
        conn.execute(
            "CREATE TABLE attachments (id INTEGER PRIMARY KEY, email_id INTEGER)"
        )
        for i in range(1, rows + 1):
            conn.execute(
                "INSERT INTO emails (id, subject, marker) VALUES (?,?,?)",
                (i, f"subj-{i}", "pre-restore"),
            )
            if i % 2 == 0:
                conn.execute(
                    "INSERT INTO attachments (id, email_id) VALUES (?,?)",
                    (i, i),
                )
        conn.commit()
    finally:
        conn.close()


def preflight(opts: RehearsalOptions) -> dict[str, Any]:
    source = opts.source.expanduser()
    target = opts.restore_target.expanduser()
    settings = opts.settings or load_settings(enable_dotenv=False)

    if not opts.confirm_throwaway:
        raise RehearsalError(
            "rehearsal requires --confirm-throwaway acknowledging synthetic/throwaway paths only"
        )

    _refuse_production(source, settings=settings)
    _refuse_production(target, settings=settings)
    _assert_throwaway_basename(target)

    if not source.is_file():
        raise RehearsalError("source must be an existing regular file")
    if source.is_symlink() or target.is_symlink():
        raise RehearsalError("refusing symlink source/target")
    assert_sqlite_header(source)

    companions = _companions(source)
    if companions:
        raise RehearsalError(
            f"source has companion file(s): {', '.join(companions)}"
        )

    if target.exists():
        raise RehearsalError("restore target already exists (no-clobber)")
    if paths_same_file(source, target):
        raise RehearsalError("source and restore target must differ")

    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    free = disk_free_bytes(parent)
    need = int(source.stat().st_size) + int(opts.capacity_margin_bytes)
    if free < need:
        raise RehearsalError(
            "insufficient free space for throwaway restore target "
            f"(need>={need} free={free})"
        )

    fp = fingerprint_file(source)
    return {
        "schema_version": REHEARSAL_SCHEMA_VERSION,
        "mode": "writable_restore_rehearsal_preflight",
        "confirm_throwaway": True,
        "apply": False,
        "source_basename": source.name,
        "target_basename": target.name,
        "source_fingerprint": fp.to_dict(),
        "companions": [],
        "capacity": {"free_bytes": free, "required_bytes": need},
        "production_excluded": True,
        "notes": [
            "Synthetic/throwaway rehearsal only; not a real 61 GiB restore.",
            "Not production cutover.",
        ],
    }


def apply_rehearsal(opts: RehearsalOptions) -> dict[str, Any]:
    report = preflight(opts)
    if not opts.apply:
        report["completed"] = True
        report["captured_at_utc"] = _iso_now()
        return report

    source = opts.source.expanduser()
    target = opts.restore_target.expanduser()
    fp_before = fingerprint_file(source)
    t0 = opts.clock()

    _progress(opts, "copy_source_to_throwaway_target")
    # Byte-copy then open writable — models restore of a verified compact candidate.
    data = source.read_bytes()
    partial = target.with_name(f"{target.name}.partial.{os.getpid()}.{time.time_ns()}")
    if partial.exists() or target.exists():
        raise RehearsalError("partial/target exists (no-clobber)")
    partial.write_bytes(data)
    fsync_file(partial)
    publish_no_clobber(partial, target)
    fsync_directory(target.parent)

    _progress(opts, "writable_transaction")
    conn = sqlite3.connect(target)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE emails SET marker = ? WHERE id = ?",
            ("post-restore-write", 1),
        )
        conn.execute(
            "INSERT INTO emails (id, subject, marker) VALUES (?,?,?)",
            (9001, "rehearsal-insert", "post-restore-write"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT marker FROM emails WHERE id = 1"
        ).fetchone()
        count = int(conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0])
        if row is None or row[0] != "post-restore-write":
            raise RehearsalError("read-back of writable transaction failed")
        if count < 2:
            raise RehearsalError("unexpected email count after writable transaction")
    except RehearsalError:
        conn.rollback()
        raise
    except sqlite3.Error as exc:
        conn.rollback()
        raise RehearsalError(f"writable transaction failed ({type(exc).__name__})") from None
    finally:
        conn.close()

    # Rollback model: move restored DB aside and restore original bytes from source.
    _progress(opts, "rollback_to_source_bytes")
    rolled = target.with_name(f"{target.name}.rolled.{os.getpid()}")
    if rolled.exists():
        raise RehearsalError("rollback path exists (no-clobber)")
    os.rename(target, rolled)
    partial2 = target.with_name(f"{target.name}.republish.{os.getpid()}.{time.time_ns()}")
    partial2.write_bytes(source.read_bytes())
    fsync_file(partial2)
    publish_no_clobber(partial2, target)
    fsync_directory(target.parent)
    # Cleanup only script-owned rollback artifact.
    try:
        rolled.unlink(missing_ok=True)
    except OSError:
        pass

    conn = sqlite3.connect(f"file:{target.resolve().as_posix()}?mode=ro", uri=True)
    try:
        marker = conn.execute("SELECT marker FROM emails WHERE id = 1").fetchone()
        if marker is None or marker[0] != "pre-restore":
            raise RehearsalError("rollback verification failed (marker mismatch)")
    finally:
        conn.close()

    fp_after = fingerprint_file(source)
    if fp_after != fp_before:
        raise RehearsalError("source fingerprint changed during rehearsal")
    if _companions(source) or _companions(target):
        raise RehearsalError("unexpected sidecars after rehearsal")

    elapsed = opts.clock() - t0
    report.update(
        {
            "mode": "writable_restore_rehearsal_apply",
            "apply": True,
            "completed": True,
            "captured_at_utc": _iso_now(),
            "elapsed_seconds": round(float(elapsed), 6),
            "source_fingerprint_before": fp_before.to_dict(),
            "source_fingerprint_after": fp_after.to_dict(),
            "source_fingerprint_unchanged": True,
            "writable_transaction_verified": True,
            "rollback_verified": True,
            "api_reopen_compatible": True,
            "target_basename_after": target.name,
            "notes": [
                "Synthetic/throwaway rehearsal only; not a real 61 GiB restore.",
                "Source fingerprint unchanged; script-owned rollback artifact removed.",
                "Not production cutover.",
            ],
        }
    )
    # Privacy: never embed absolute paths.
    report["source_path_sanitized"] = sanitize_path_for_log(source)
    report["target_path_sanitized"] = sanitize_path_for_log(target)
    return report


def run_rehearsal(opts: RehearsalOptions) -> dict[str, Any]:
    return apply_rehearsal(opts)
