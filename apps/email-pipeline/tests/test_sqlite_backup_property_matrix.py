"""Backup property / OperationalError / progress-callback adversarial matrix."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from origenlab_email_pipeline.qa.sqlite_online_backup import (
    OPERR_BUSY_OR_LOCKED,
    OPERR_CAPACITY,
    OPERR_CANNOT_OPEN,
    OPERR_INTERRUPTED,
    OPERR_IO_ERROR,
    OPERR_OTHER,
    OPERR_READONLY_WAL_LOCKING,
    BackupError,
    BackupOptions,
    classify_sqlite_operational_error,
    parse_sqlite_header_meta,
    run_online_backup,
)
from sqlite_adversarial_support import create_sqlite_corpus


def _exc(*, code: int, name: str) -> BaseException:
    return SimpleNamespace(sqlite_errorcode=code, sqlite_errorname=name)


@pytest.mark.parametrize(
    "code,name,expect",
    [
        (5, "SQLITE_BUSY", OPERR_BUSY_OR_LOCKED),
        (6, "SQLITE_LOCKED", OPERR_BUSY_OR_LOCKED),
        (8, "SQLITE_READONLY", OPERR_READONLY_WAL_LOCKING),
        (14, "SQLITE_CANTOPEN", OPERR_CANNOT_OPEN),
        (10, "SQLITE_IOERR", OPERR_IO_ERROR),
        (13, "SQLITE_FULL", OPERR_CAPACITY),
        (9, "SQLITE_INTERRUPT", OPERR_INTERRUPTED),
        (9999, "SQLITE_WEIRD", OPERR_OTHER),
    ],
)
def test_operational_error_classification_matrix(
    code: int, name: str, expect: str
) -> None:
    cat = classify_sqlite_operational_error(_exc(code=code, name=name), phase="copy")
    assert cat["category"] == expect
    assert "str(" not in str(cat)
    assert "/home/" not in str(cat)
    assert cat["sqlite_errorcode"] == code or cat["category"] == OPERR_OTHER


def _backup(src: Path, dest: Path, *, apply: bool, **kw: object) -> dict:
    lock = dest.parent / "locks"
    lock.mkdir(exist_ok=True)
    return run_online_backup(
        BackupOptions(
            source=src,
            destination=dest,
            apply=apply,
            allow_same_filesystem=True,
            pages_per_batch=int(kw.get("pages_per_batch", 5)),  # type: ignore[arg-type]
            busy_timeout_ms=1000,
            progress_interval_seconds=0.0,
            lock_dir=lock,
            progress_sink=kw.get("progress_sink"),  # type: ignore[arg-type]
            sleep=lambda _s: None,
        )
    )


def test_backup_no_wal_small_db(tmp_path: Path) -> None:
    src = create_sqlite_corpus(tmp_path, "delete_journal_mode")
    dest = tmp_path / "out.sqlite"
    result = _backup(src, dest, apply=True)
    assert result.get("completed") is True
    assert dest.exists()
    parse_sqlite_header_meta(dest)


def test_backup_wal_mode_small_db(tmp_path: Path) -> None:
    src = create_sqlite_corpus(tmp_path, "wal_mode")
    dest = tmp_path / "out.sqlite"
    result = _backup(src, dest, apply=True)
    assert result.get("completed") is True
    assert dest.exists()


def test_progress_sink_exception_fails_closed(tmp_path: Path) -> None:
    src = create_sqlite_corpus(tmp_path, "many_tables")
    dest = tmp_path / "out.sqlite"

    def boom(_msg: str) -> None:
        raise RuntimeError("progress boom")

    with pytest.raises((BackupError, RuntimeError)):
        _backup(src, dest, apply=True, pages_per_batch=1, progress_sink=boom)


def test_destination_collision_refuses(tmp_path: Path) -> None:
    src = create_sqlite_corpus(tmp_path, "one_table")
    dest = tmp_path / "out.sqlite"
    dest.write_bytes(b"EXISTING")
    with pytest.raises(BackupError):
        _backup(src, dest, apply=True)


def test_preflight_without_apply_does_not_create_dest(tmp_path: Path) -> None:
    src = create_sqlite_corpus(tmp_path, "one_table")
    dest = tmp_path / "out.sqlite"
    report = _backup(src, dest, apply=False)
    assert dest.exists() is False
    assert report.get("apply") is False
    assert report.get("source_opened_with_sqlite3") is False


def test_zero_batch_rejected(tmp_path: Path) -> None:
    src = create_sqlite_corpus(tmp_path, "one_table")
    dest = tmp_path / "out.sqlite"
    with pytest.raises(BackupError):
        _backup(src, dest, apply=True, pages_per_batch=0)


def test_message_text_never_leaked_in_classification() -> None:
    exc = sqlite3.OperationalError(
        "disk I/O error at /home/rafael/secret.db token=origenlab_test_token_ABCDEF123456"
    )
    # No sqlite_errorcode → other, but must not embed message
    cat = classify_sqlite_operational_error(exc, phase="copy")
    blob = str(cat)
    assert "/home/" not in blob
    assert "origenlab_test_token" not in blob
    assert "secret.db" not in blob
