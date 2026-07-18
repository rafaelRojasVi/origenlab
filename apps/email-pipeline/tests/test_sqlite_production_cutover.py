"""Synthetic tests for the SQLite production cutover orchestrator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from origenlab_email_pipeline.qa.sqlite_production_cutover import (
    KNOWN_EVIDENCE_COMPACT,
    CutoverError,
    CutoverFailureCategory,
    CutoverOptions,
    CutoverStage,
    SyntheticWorld,
    apply_stage,
    attempt_rollback_before_writers,
    journal_path_for,
    plan_preflight,
    tree_snapshot,
)


MID = "cutover20260718T120000Z"
MAIN_SHA = "25cd4100e226427b3a4d027f1ee3b3af056884d4"


def _world(tmp_path: Path) -> tuple[SyntheticWorld, Path, Path, Path, str]:
    root = tmp_path / "cutover_world"
    root.mkdir()
    prod = root / "emails.sqlite"
    reports = root / "reports" / "out"
    (reports / "active" / "current").mkdir(parents=True)
    world = SyntheticWorld(root=root, head_sha=MAIN_SHA)
    world.files[str(prod)] = b"SYNTHETIC-PROD-DB-v1"
    world.services.api_active = True
    world.services.health_timer_active = True
    world.wal_size = 4096
    fp = world.fingerprint(prod)
    return world, prod, reports, root, fp


def _opts(
    world: SyntheticWorld,
    prod: Path,
    reports: Path,
    fp: str,
    *,
    stage: CutoverStage,
    apply: bool = False,
    approve_swap: bool = False,
    backup: Path | None = None,
    staging: Path | None = None,
    fail_after: str | None = None,
) -> CutoverOptions:
    return CutoverOptions(
        stage=stage,
        apply=apply,
        confirm_production_cutover=True,
        maintenance_id=MID,
        expected_main_sha=MAIN_SHA,
        expected_production_path=prod,
        expected_production_fingerprint=fp,
        approve_swap=approve_swap,
        journal_path=prod.parent / ".origenlab_cutover_journals" / f"{MID}.journal.json",
        backup_dest=backup,
        staging_dest=staging,
        reports_dir=reports,
        adapters=world,
        fail_after=fail_after,
        allow_synthetic_world=True,
    )


def _run_through(
    world: SyntheticWorld,
    prod: Path,
    reports: Path,
    fp: str,
    root: Path,
    *,
    stop_before: CutoverStage | None = None,
) -> str:
    backup = root / "emails_offline_cutover_fresh.sqlite"
    staging = root / "emails.sqlite.staged.cutover"
    # Avoid forbidden evidence prefixes on fresh artifacts.
    backup = root / "emails_online_backup_cutover_fresh.sqlite"
    sequence = [
        CutoverStage.PAUSE_WRITERS,
        CutoverStage.STOP_READERS,
        CutoverStage.QUIESCE_WAL,
        CutoverStage.CREATE_CURRENT_BACKUP,
        CutoverStage.COMPACT_TO_PRODUCTION_FS_STAGING,
        CutoverStage.VERIFY_CANDIDATE,
        CutoverStage.APPROVE_SWAP,
        CutoverStage.ATOMIC_SWAP,
        CutoverStage.READONLY_SMOKE,
        CutoverStage.RESUME_SERVICES,
        CutoverStage.RESUME_WRITERS,
        CutoverStage.COMPLETED,
    ]
    # Refresh fingerprint after quiesce will update — track live fp from journal via apply.
    live_fp = fp
    for stage in sequence:
        if stop_before is not None and stage == stop_before:
            break
        if stage == CutoverStage.QUIESCE_WAL:
            world.writer_pids = []
            world.locks = []
        opts = _opts(
            world,
            prod,
            reports,
            live_fp,
            stage=stage,
            apply=True,
            approve_swap=stage
            in {CutoverStage.APPROVE_SWAP, CutoverStage.ATOMIC_SWAP},
            backup=backup,
            staging=staging,
        )
        report = apply_stage(opts)
        live_fp = report["journal"]["production_fingerprint"] or live_fp
        # After quiesce, production fingerprint may be unchanged; keep token.
        if stage == CutoverStage.QUIESCE_WAL:
            live_fp = world.fingerprint(prod)
            # Update expected fingerprint for subsequent auth checks.
            fp = live_fp
    return live_fp


def test_zero_write_preflight_tree_unchanged(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    before = tree_snapshot(tmp_path)
    report = plan_preflight(
        _opts(world, prod, reports, fp, stage=CutoverStage.PLAN_PREFLIGHT)
    )
    after = tree_snapshot(tmp_path)
    assert before == after
    assert report["zero_write_preflight"] is True
    assert report["apply"] is False
    assert "pause_markers_absent" in report["blockers"]


def test_wrong_fingerprint_refused(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    with pytest.raises(CutoverError) as exc:
        apply_stage(
            _opts(
                world,
                prod,
                reports,
                "0:0:0:dead",
                stage=CutoverStage.PAUSE_WRITERS,
                apply=True,
            )
        )
    assert exc.value.category == CutoverFailureCategory.SAFETY


def test_wrong_main_sha_refused(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    opts = _opts(world, prod, reports, fp, stage=CutoverStage.PAUSE_WRITERS, apply=True)
    opts.expected_main_sha = "0000000"
    world.head_sha = MAIN_SHA
    with pytest.raises(CutoverError) as exc:
        apply_stage(opts)
    assert exc.value.category == CutoverFailureCategory.SAFETY


def test_active_writer_blocks_quiesce(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    apply_stage(
        _opts(world, prod, reports, fp, stage=CutoverStage.PAUSE_WRITERS, apply=True)
    )
    apply_stage(
        _opts(world, prod, reports, fp, stage=CutoverStage.STOP_READERS, apply=True)
    )
    world.writer_pids = [1234]
    with pytest.raises(CutoverError) as exc:
        apply_stage(
            _opts(world, prod, reports, fp, stage=CutoverStage.QUIESCE_WAL, apply=True)
        )
    assert "quiesced" in str(exc.value).lower()


def test_nonzero_wal_after_checkpoint_fails(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    apply_stage(
        _opts(world, prod, reports, fp, stage=CutoverStage.PAUSE_WRITERS, apply=True)
    )
    apply_stage(
        _opts(world, prod, reports, fp, stage=CutoverStage.STOP_READERS, apply=True)
    )
    world.writer_pids = []
    world.fail_checkpoint = True
    with pytest.raises(CutoverError):
        apply_stage(
            _opts(world, prod, reports, fp, stage=CutoverStage.QUIESCE_WAL, apply=True)
        )


def test_evidence_compact_refused_as_source(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    bad = root / KNOWN_EVIDENCE_COMPACT
    world.files[str(bad)] = b"EVIDENCE"
    with pytest.raises(CutoverError) as exc:
        world.verify_candidate(bad)
    assert exc.value.category == CutoverFailureCategory.SAFETY


def test_cross_filesystem_swap_refused(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup = root / "emails_online_backup_cutover_fresh.sqlite"
    staging = root / "emails.sqlite.staged.cutover"
    _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.ATOMIC_SWAP
    )
    live_fp = world.fingerprint(prod)
    world.same_fs = lambda a, b: False  # type: ignore[method-assign]
    with pytest.raises(CutoverError) as exc:
        apply_stage(
            _opts(
                world,
                prod,
                reports,
                live_fp,
                stage=CutoverStage.ATOMIC_SWAP,
                apply=True,
                approve_swap=True,
                backup=backup,
                staging=staging,
            )
        )
    assert "filesystem" in str(exc.value).lower()


def test_rename_exchange_unsupported_fail_closed(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup = root / "emails_online_backup_cutover_fresh.sqlite"
    staging = root / "emails.sqlite.staged.cutover"
    _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.ATOMIC_SWAP
    )
    live_fp = world.fingerprint(prod)
    world.rename_exchange_ok = False
    with pytest.raises(CutoverError) as exc:
        apply_stage(
            _opts(
                world,
                prod,
                reports,
                live_fp,
                stage=CutoverStage.ATOMIC_SWAP,
                apply=True,
                approve_swap=True,
                backup=backup,
                staging=staging,
            )
        )
    assert "renameat2" in str(exc.value).lower() or "exchange" in str(exc.value).lower()
    assert "dual-mv" in str(exc.value).lower() or "fallback" in (
        exc.value.recovery or ""
    ).lower()


def test_full_happy_path_retains_pre_cutover(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _run_through(world, prod, reports, fp, root)
    pre = [
        k
        for k in world.files
        if "pre_cutover" in Path(k).name
    ]
    assert len(pre) == 1
    assert world.files[str(prod)].startswith(b"COMPACT:")
    assert world.mail_pause is False
    assert world.writers_resumed if False else world.mail_pause is False
    jpath = journal_path_for(
        CutoverOptions(maintenance_id=MID, expected_production_path=prod),
        prod,
    )
    journal = json.loads(world.files[str(jpath)].decode())
    assert journal["stage"] == CutoverStage.COMPLETED.value
    assert journal["writers_resumed"] is True
    assert journal["pre_cutover_basename"]


def test_rollback_before_writers_ok(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup = root / "emails_online_backup_cutover_fresh.sqlite"
    staging = root / "emails.sqlite.staged.cutover"
    _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.READONLY_SMOKE
    )
    pre_name = f"{prod.name}.pre_cutover.{MID}"
    pre_path = prod.with_name(pre_name)
    old_fp = world.fingerprint(pre_path)
    new_fp = world.fingerprint(prod)
    report = attempt_rollback_before_writers(
        _opts(
            world,
            prod,
            reports,
            new_fp,
            stage=CutoverStage.ATOMIC_SWAP,
            apply=True,
            approve_swap=True,
            backup=backup,
            staging=staging,
        ),
        pre_cutover_path=pre_path,
        expected_old_fingerprint=old_fp,
        expected_new_fingerprint=new_fp,
    )
    assert report["rolled_back"] is True
    assert world.files[str(prod)] == b"SYNTHETIC-PROD-DB-v1"


def test_rollback_refused_after_writers_resume(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _run_through(world, prod, reports, fp, root)
    pre = prod.with_name(f"{prod.name}.pre_cutover.{MID}")
    with pytest.raises(CutoverError) as exc:
        attempt_rollback_before_writers(
            _opts(
                world,
                prod,
                reports,
                world.fingerprint(prod),
                stage=CutoverStage.ATOMIC_SWAP,
                apply=True,
                approve_swap=True,
            ),
            pre_cutover_path=pre,
            expected_old_fingerprint=world.fingerprint(pre),
            expected_new_fingerprint=world.fingerprint(prod),
        )
    assert "writers resumed" in str(exc.value).lower()


def test_crash_boundaries_leave_journal_resumable(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup = root / "emails_online_backup_cutover_fresh.sqlite"
    staging = root / "emails.sqlite.staged.cutover"
    live_fp = fp
    # pause_writers with inject after journal write
    with pytest.raises(CutoverError) as exc:
        apply_stage(
            _opts(
                world,
                prod,
                reports,
                live_fp,
                stage=CutoverStage.PAUSE_WRITERS,
                apply=True,
                backup=backup,
                staging=staging,
                fail_after="pause_writers",
            )
        )
    assert "pause_writers" in str(exc.value)
    jpath = prod.parent / ".origenlab_cutover_journals" / f"{MID}.journal.json"
    journal = json.loads(world.files[str(jpath)].decode())
    assert journal["stage"] == CutoverStage.PAUSE_WRITERS.value
    # Resume next stage successfully
    apply_stage(
        _opts(
            world,
            prod,
            reports,
            live_fp,
            stage=CutoverStage.STOP_READERS,
            apply=True,
            backup=backup,
            staging=staging,
        )
    )
    journal = json.loads(world.files[str(jpath)].decode())
    assert journal["stage"] == CutoverStage.STOP_READERS.value


def test_no_clobber_backup_dest(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup = root / "emails_online_backup_cutover_fresh.sqlite"
    staging = root / "emails.sqlite.staged.cutover"
    _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.CREATE_CURRENT_BACKUP
    )
    live_fp = world.fingerprint(prod)
    world.files[str(backup)] = b"EXISTING"
    with pytest.raises(CutoverError) as exc:
        apply_stage(
            _opts(
                world,
                prod,
                reports,
                live_fp,
                stage=CutoverStage.CREATE_CURRENT_BACKUP,
                apply=True,
                backup=backup,
                staging=staging,
            )
        )
    assert "no-clobber" in str(exc.value).lower() or "exists" in str(exc.value).lower()


def test_non_sequential_stage_refused(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    apply_stage(
        _opts(world, prod, reports, fp, stage=CutoverStage.PAUSE_WRITERS, apply=True)
    )
    with pytest.raises(CutoverError) as exc:
        apply_stage(
            _opts(
                world,
                prod,
                reports,
                fp,
                stage=CutoverStage.CREATE_CURRENT_BACKUP,
                apply=True,
                backup=root / "emails_online_backup_cutover_fresh.sqlite",
            )
        )
    assert exc.value.category == CutoverFailureCategory.AMBIGUOUS


def test_swap_without_approve_refused(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    with pytest.raises(CutoverError) as exc:
        apply_stage(
            _opts(
                world,
                prod,
                reports,
                fp,
                stage=CutoverStage.ATOMIC_SWAP,
                apply=True,
                approve_swap=False,
            )
        )
    assert "approve-swap" in str(exc.value).lower()


def test_privacy_no_absolute_paths_in_plan(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    report = plan_preflight(
        _opts(world, prod, reports, fp, stage=CutoverStage.PLAN_PREFLIGHT)
    )
    blob = json.dumps(report)
    assert "/home/" not in blob
    assert "/mnt/" not in blob
    assert "@" not in blob


def test_plan_preflight_rejects_apply(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    with pytest.raises(CutoverError):
        apply_stage(
            _opts(
                world,
                prod,
                reports,
                fp,
                stage=CutoverStage.PLAN_PREFLIGHT,
                apply=True,
            )
        )
