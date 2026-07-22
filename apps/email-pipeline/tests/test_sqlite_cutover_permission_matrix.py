"""Permission / sidecar combinatorial adversarial matrix (synthetic)."""

from __future__ import annotations

from pathlib import Path

import pytest

from origenlab_email_pipeline.qa.sqlite_cutover_artifact_permissions import (
    capture_artifact,
)
from origenlab_email_pipeline.qa.sqlite_production_cutover import (
    CutoverError,
    CutoverStage,
    SyntheticWorld,
    abort_before_swap,
    apply_stage,
    load_journal,
)
from sqlite_adversarial_support import backup_staging, make_opts, make_world


def _synth_paths(world: SyntheticWorld, prod: Path) -> None:
    wal = Path(str(prod) + "-wal")
    shm = Path(str(prod) + "-shm")
    world.files[str(wal)] = b"\x00" * 64
    world.files[str(shm)] = b"\x00" * 32
    world.modes[str(wal)] = 0o644
    world.modes[str(shm)] = 0o644
    world.owners[str(wal)] = (1000, 1000)
    world.owners[str(shm)] = (1000, 1000)
    world.inode_overrides[str(wal)] = 4243
    world.inode_overrides[str(shm)] = 4244
    world.device_overrides[str(wal)] = 7
    world.device_overrides[str(shm)] = 7
    world.wal_size = 64
    world.shm_size = 32


@pytest.mark.parametrize("wal_present", [True, False])
@pytest.mark.parametrize("shm_present", [True, False])
def test_capture_reflects_companion_presence(
    tmp_path: Path, wal_present: bool, shm_present: bool
) -> None:
    world, prod, reports, root, fp = make_world(tmp_path)
    if wal_present or shm_present:
        _synth_paths(world, prod)
        if not wal_present:
            world.files.pop(str(Path(str(prod) + "-wal")), None)
            world.wal_size = 0
        if not shm_present:
            world.files.pop(str(Path(str(prod) + "-shm")), None)
            world.shm_size = 0
    artifact = capture_artifact(world, prod)
    assert artifact.main.present is True
    assert artifact.wal.present is wal_present
    assert artifact.shm.present is shm_present


def test_barrier_then_abort_restores_modes(tmp_path: Path) -> None:
    world, prod, reports, root, fp = make_world(tmp_path)
    _synth_paths(world, prod)
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
    assert world.modes[str(prod)] & 0o222 == 0
    abort_before_swap(
        make_opts(
            world,
            prod,
            reports,
            world.fingerprint(prod),
            stage=CutoverStage.PLAN_PREFLIGHT,
            apply=True,
            backup=backup,
            staging=staging,
        )
    )
    assert world.modes[str(prod)] & 0o200 != 0


def test_shm_readonly_blocks_mail_resume_verification(tmp_path: Path) -> None:
    """Read-only SHM after barrier must fail closed at resume verification."""
    world, prod, reports, root, fp = make_world(tmp_path)
    _synth_paths(world, prod)
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
    shm = Path(str(prod) + "-shm")
    assert world.modes[str(shm)] & 0o222 == 0
    # Restore main writable (as post-swap resume would) but leave SHM RO.
    world.modes[str(prod)] = 0o644
    artifact = capture_artifact(world, prod)
    from origenlab_email_pipeline.qa.sqlite_cutover_artifact_permissions import (
        ArtifactPermissionError,
        verify_artifact_writable_for_resume,
    )

    with pytest.raises(ArtifactPermissionError) as ei:
        verify_artifact_writable_for_resume(
            world, prod, artifact, legacy_original_mode=0o644
        )
    assert "shm" in str(ei.value).lower() or "not writable" in str(ei.value).lower()

def test_stale_sidecar_identity_drift_is_visible(tmp_path: Path) -> None:
    world, prod, reports, root, fp = make_world(tmp_path)
    _synth_paths(world, prod)
    artifact = capture_artifact(world, prod)
    before = artifact.wal.inode
    world.inode_overrides[str(Path(str(prod) + "-wal"))] = 99999
    # Re-capture must observe a different inode than the sealed artifact.
    again = capture_artifact(world, prod)
    assert before != again.wal.inode
    assert artifact.wal.inode != again.wal.inode


def test_partial_barrier_not_reported_as_full_success(tmp_path: Path) -> None:
    world, prod, reports, root, fp = make_world(tmp_path)
    _synth_paths(world, prod)
    backup, staging = backup_staging(root)
    for stage in (
        CutoverStage.PAUSE_WRITERS,
        CutoverStage.STOP_READERS,
        CutoverStage.QUIESCE_WAL,
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
    # Fail during barrier via capture refusal on SHM
    world.capture_raise_paths.add(str(Path(str(prod) + "-shm")))
    with pytest.raises(CutoverError):
        apply_stage(
            make_opts(
                world,
                prod,
                reports,
                world.fingerprint(prod),
                stage=CutoverStage.APPLY_OS_WRITE_BARRIER,
                apply=True,
                backup=backup,
                staging=staging,
            )
        )
    journal = load_journal(
        world,
        prod.parent
        / ".origenlab_cutover_journals"
        / "cutover20260722T180000Z.journal.json",
    )
    assert journal is not None
    assert journal.stage != CutoverStage.COMPLETED.value
