"""Post-closure fault-injection matrix at durable cutover boundaries."""

from __future__ import annotations

from pathlib import Path

import pytest

from origenlab_email_pipeline.qa.sqlite_production_cutover import (
    CutoverError,
    CutoverFailureCategory,
    CutoverStage,
    apply_stage,
    load_journal,
)
from sqlite_adversarial_support import (
    MID,
    assert_no_forbidden,
    backup_staging,
    make_opts,
    make_world,
)

# (fail_after phase, stage that triggers it)
FAULT_CASES: list[tuple[str, CutoverStage]] = [
    ("pause_writers", CutoverStage.PAUSE_WRITERS),
    ("stop_readers", CutoverStage.STOP_READERS),
    ("quiesce_wal", CutoverStage.QUIESCE_WAL),
    ("permission_intent_barrier", CutoverStage.APPLY_OS_WRITE_BARRIER),
    ("apply_os_write_barrier", CutoverStage.APPLY_OS_WRITE_BARRIER),
    ("create_current_backup", CutoverStage.CREATE_CURRENT_BACKUP),
    ("compact_to_production_fs_staging", CutoverStage.COMPACT_TO_PRODUCTION_FS_STAGING),
    ("verify_candidate", CutoverStage.VERIFY_CANDIDATE),
    ("approve_swap", CutoverStage.APPROVE_SWAP),
    ("readonly_smoke_before_ownership_write", CutoverStage.READONLY_SMOKE),
    ("readonly_smoke_ownership_written", CutoverStage.READONLY_SMOKE),
    ("readonly_smoke_after_api_start", CutoverStage.READONLY_SMOKE),
    ("readonly_smoke", CutoverStage.READONLY_SMOKE),
]


def _advance_before(
    world,
    prod,
    reports,
    root,
    target: CutoverStage,
) -> None:
    backup, staging = backup_staging(root)
    for stage in [
        CutoverStage.PAUSE_WRITERS,
        CutoverStage.STOP_READERS,
        CutoverStage.QUIESCE_WAL,
        CutoverStage.APPLY_OS_WRITE_BARRIER,
        CutoverStage.CREATE_CURRENT_BACKUP,
        CutoverStage.COMPACT_TO_PRODUCTION_FS_STAGING,
        CutoverStage.VERIFY_CANDIDATE,
        CutoverStage.APPROVE_SWAP,
        CutoverStage.ATOMIC_SWAP,
        CutoverStage.READONLY_SMOKE,
    ]:
        if stage is target:
            return
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
                approve_swap=stage
                in {CutoverStage.APPROVE_SWAP, CutoverStage.ATOMIC_SWAP},
                backup=backup,
                staging=staging,
            )
        )


@pytest.mark.parametrize("phase,stage", FAULT_CASES)
def test_fail_after_never_claims_completed(
    tmp_path: Path, phase: str, stage: CutoverStage
) -> None:
    world, prod, reports, root, fp = make_world(tmp_path)
    backup, staging = backup_staging(root)
    _advance_before(world, prod, reports, root, stage)
    if stage in {
        CutoverStage.QUIESCE_WAL,
        CutoverStage.APPLY_OS_WRITE_BARRIER,
        CutoverStage.CREATE_CURRENT_BACKUP,
        CutoverStage.COMPACT_TO_PRODUCTION_FS_STAGING,
        CutoverStage.VERIFY_CANDIDATE,
        CutoverStage.APPROVE_SWAP,
        CutoverStage.ATOMIC_SWAP,
        CutoverStage.READONLY_SMOKE,
    }:
        world.lock_records = []
        world.fd_hits = []

    with pytest.raises(CutoverError) as ei:
        apply_stage(
            make_opts(
                world,
                prod,
                reports,
                world.fingerprint(prod),
                stage=stage,
                apply=True,
                approve_swap=stage
                in {CutoverStage.APPROVE_SWAP, CutoverStage.ATOMIC_SWAP},
                backup=backup,
                staging=staging,
                fail_after=phase,
            )
        )
    assert_no_forbidden(str(ei.value))
    assert ei.value.category in set(CutoverFailureCategory)
    journal = load_journal(
        world, prod.parent / ".origenlab_cutover_journals" / f"{MID}.journal.json"
    )
    if journal is not None:
        assert journal.stage != CutoverStage.COMPLETED.value
        assert journal.abandoned is not True


def test_journal_write_fault_keeps_non_completed(tmp_path: Path) -> None:
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
    world.journal_write_fault = ("stop_readers", "before_temp_write")
    raised = False
    try:
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
    except (CutoverError, OSError):
        raised = True
    assert raised
    journal = load_journal(world, jpath)
    assert journal is not None
    assert journal.stage != CutoverStage.COMPLETED.value
    assert journal.stage == CutoverStage.PAUSE_WRITERS.value


def test_backup_incomplete_does_not_advance_stage(tmp_path: Path) -> None:
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
    world.backup_incomplete = True
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
    journal = load_journal(
        world, prod.parent / ".origenlab_cutover_journals" / f"{MID}.journal.json"
    )
    assert journal is not None
    assert journal.stage != CutoverStage.CREATE_CURRENT_BACKUP.value
