"""Concurrency / race simulation for cutover (bounded, synthetic only)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from origenlab_email_pipeline.qa.sqlite_production_cutover import (
    CutoverError,
    CutoverStage,
    apply_stage,
    load_journal,
)
from sqlite_adversarial_support import MID, backup_staging, make_opts, make_world


def test_two_orchestrators_same_mid_exclusive_lock(tmp_path: Path) -> None:
    world, prod, reports, root, fp = make_world(tmp_path)
    backup, staging = backup_staging(root)
    errors: list[BaseException] = []
    started = threading.Event()
    release = threading.Event()

    def hold_lock() -> None:
        try:
            with world.acquire_exclusive_lock(prod, MID):
                started.set()
                release.wait(timeout=5)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=hold_lock, daemon=True)
    t.start()
    assert started.wait(timeout=5)
    with pytest.raises(CutoverError):
        apply_stage(
            make_opts(
                world,
                prod,
                reports,
                fp,
                stage=CutoverStage.PAUSE_WRITERS,
                apply=True,
                backup=backup,
                staging=staging,
            )
        )
    release.set()
    t.join(timeout=5)
    assert not t.is_alive()
    assert errors == []


def test_two_mids_serialized_same_source(tmp_path: Path) -> None:
    """Two MIDs may each create journals, but exclusive flock serializes apply."""
    world, prod, reports, root, fp = make_world(tmp_path)
    backup, staging = backup_staging(root)
    mid_a = "cutover20260722T180001Z"
    mid_b = "cutover20260722T180002Z"
    apply_stage(
        make_opts(
            world,
            prod,
            reports,
            fp,
            stage=CutoverStage.PAUSE_WRITERS,
            apply=True,
            backup=backup,
            staging=staging,
            maintenance_id=mid_a,
        )
    )
    apply_stage(
        make_opts(
            world,
            prod,
            reports,
            world.fingerprint(prod),
            stage=CutoverStage.PAUSE_WRITERS,
            apply=True,
            backup=backup,
            staging=staging,
            maintenance_id=mid_b,
        )
    )
    ja = load_journal(
        world, prod.parent / ".origenlab_cutover_journals" / f"{mid_a}.journal.json"
    )
    jb = load_journal(
        world, prod.parent / ".origenlab_cutover_journals" / f"{mid_b}.journal.json"
    )
    assert ja is not None and jb is not None
    assert ja.maintenance_id != jb.maintenance_id
    # Concurrent ownership is refused while one lock is held.
    errors: list[BaseException] = []
    started = threading.Event()
    release = threading.Event()

    def hold() -> None:
        try:
            with world.acquire_exclusive_lock(prod, mid_a):
                started.set()
                release.wait(timeout=5)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=hold, daemon=True)
    t.start()
    assert started.wait(timeout=5)
    with pytest.raises(CutoverError):
        apply_stage(
            make_opts(
                world,
                prod,
                reports,
                world.fingerprint(prod),
                stage=CutoverStage.STOP_READERS,
                apply=True,
                backup=backup,
                staging=staging,
                maintenance_id=mid_b,
            )
        )
    release.set()
    t.join(timeout=5)
    assert not t.is_alive()
    assert errors == []


def test_foreign_fd_after_quiescence_blocks_backup(tmp_path: Path) -> None:
    world, prod, reports, root, fp = make_world(tmp_path)
    backup, staging = backup_staging(root)
    for stage in (
        CutoverStage.PAUSE_WRITERS,
        CutoverStage.STOP_READERS,
        CutoverStage.QUIESCE_WAL,
        CutoverStage.APPLY_OS_WRITE_BARRIER,
    ):
        if stage is CutoverStage.QUIESCE_WAL:
            world.lock_records = []
            world.fd_hits = []
        apply_stage(
            make_opts(
                world,
                prod,
                reports,
                world.fingerprint(prod),
                stage=stage,
                apply=True,
                backup=backup,
                staging=staging,
            )
        )
    # Foreign reader appears after quiescence.
    world.fd_hits = [
        {
            "classification": "live",
            "access": "read_only",
            "role": "main",
            "pid": 99999,
        }
    ]
    with pytest.raises(CutoverError):
        apply_stage(
            make_opts(
                world,
                prod,
                reports,
                world.fingerprint(prod),
                stage=CutoverStage.CREATE_CURRENT_BACKUP,
                apply=True,
                backup=backup,
                staging=staging,
            )
        )


def test_external_reenable_during_maintenance_fails_closed(tmp_path: Path) -> None:
    world, prod, reports, root, fp = make_world(tmp_path)
    backup, staging = backup_staging(root)
    apply_stage(
        make_opts(
            world,
            prod,
            reports,
            fp,
            stage=CutoverStage.PAUSE_WRITERS,
            apply=True,
            backup=backup,
            staging=staging,
        )
    )
    apply_stage(
        make_opts(
            world,
            prod,
            reports,
            world.fingerprint(prod),
            stage=CutoverStage.STOP_READERS,
            apply=True,
            backup=backup,
            staging=staging,
        )
    )
    # External actor re-enables API during maintenance.
    world.api_enabled = True
    world.services.api_active = True
    with pytest.raises(CutoverError):
        apply_stage(
            make_opts(
                world,
                prod,
                reports,
                world.fingerprint(prod),
                stage=CutoverStage.QUIESCE_WAL,
                apply=True,
                backup=backup,
                staging=staging,
            )
        )


def test_pause_disappearing_unexpectedly_fails_closed(tmp_path: Path) -> None:
    world, prod, reports, root, fp = make_world(tmp_path)
    backup, staging = backup_staging(root)
    for stage in (
        CutoverStage.PAUSE_WRITERS,
        CutoverStage.STOP_READERS,
        CutoverStage.QUIESCE_WAL,
        CutoverStage.APPLY_OS_WRITE_BARRIER,
    ):
        if stage is CutoverStage.QUIESCE_WAL:
            world.lock_records = []
            world.fd_hits = []
        apply_stage(
            make_opts(
                world,
                prod,
                reports,
                world.fingerprint(prod),
                stage=stage,
                apply=True,
                backup=backup,
                staging=staging,
            )
        )
    world.mail_pause = False
    world.mirror_pause = False
    world.lock_records = []
    world.fd_hits = []
    with pytest.raises(CutoverError):
        apply_stage(
            make_opts(
                world,
                prod,
                reports,
                world.fingerprint(prod),
                stage=CutoverStage.CREATE_CURRENT_BACKUP,
                apply=True,
                backup=backup,
                staging=staging,
            )
        )


def test_journal_tampered_by_other_actor(tmp_path: Path) -> None:
    world, prod, reports, root, fp = make_world(tmp_path)
    backup, staging = backup_staging(root)
    apply_stage(
        make_opts(
            world,
            prod,
            reports,
            fp,
            stage=CutoverStage.PAUSE_WRITERS,
            apply=True,
            backup=backup,
            staging=staging,
        )
    )
    jpath = prod.parent / ".origenlab_cutover_journals" / f"{MID}.journal.json"
    world.write_text_atomic(jpath, "{corrupt")
    with pytest.raises(CutoverError):
        apply_stage(
            make_opts(
                world,
                prod,
                reports,
                world.fingerprint(prod),
                stage=CutoverStage.STOP_READERS,
                apply=True,
                backup=backup,
                staging=staging,
            )
        )
