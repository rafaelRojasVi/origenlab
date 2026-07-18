"""Synthetic tests for the hardened SQLite production cutover orchestrator."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from origenlab_email_pipeline.qa.sqlite_production_cutover import (
    CHILECOMPRA_LOCK_FILENAME,
    CUTOVER_ACCESS_INVENTORY,
    DASHBOARD_LOCK_FILENAME,
    KNOWN_EVIDENCE_COMPACT,
    MAIL_LOCK_FILENAME,
    REAL_PRODUCTION_APPLY_BLOCKED,
    REAL_SQLITE_READER_ENTRY_POINTS,
    REAL_SQLITE_WRITER_ENTRY_POINTS,
    UNRELATED_EXTERNAL_WRITER_ENTRY_POINTS,
    CutoverError,
    CutoverExclusiveLock,
    CutoverFailureCategory,
    CutoverOptions,
    CutoverStage,
    LockRecord,
    SyntheticWorld,
    abort_before_swap,
    apply_stage,
    attempt_rollback_before_writers,
    journal_path_for,
    parse_fdinfo_access_mode,
    plan_preflight,
    reconcile_atomic_swap_state,
    tree_snapshot,
)


MID = "cutover20260718T120000Z"
MAIN_SHA = "25cd4100e226427b3a4d027f1ee3b3af056884d4"


def _world(tmp_path: Path) -> tuple[SyntheticWorld, Path, Path, Path, str]:
    root = tmp_path / "cutover_world"
    root.mkdir(parents=True, exist_ok=True)
    prod = root / "emails.sqlite"
    reports = root / "reports" / "out"
    (reports / "active" / "current").mkdir(parents=True)
    world = SyntheticWorld(root=root, head_sha=MAIN_SHA)
    world.files[str(prod)] = b"SYNTHETIC-PROD-DB-v1"
    world.modes[str(prod)] = 0o644
    world.owners[str(prod)] = (1000, 1000)
    world.inode_overrides[str(prod)] = 4242
    world.device_overrides[str(prod)] = 7
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


def _paths(root: Path) -> tuple[Path, Path]:
    return (
        root / "emails_online_backup_cutover_fresh.sqlite",
        root / "emails.sqlite.staged.cutover",
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
    backup, staging = _paths(root)
    sequence = [
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
        CutoverStage.RESUME_SERVICES,
        CutoverStage.RESUME_WRITERS_PONR,
        CutoverStage.RESUME_WRITERS_RESTORE_MODE,
        CutoverStage.RESUME_WRITERS_MAIL,
        CutoverStage.RESUME_WRITERS_OBSERVE_MAIL,
        CutoverStage.RESUME_WRITERS_MIRROR,
        CutoverStage.RESUME_WRITERS_OBSERVE_MIRROR,
        CutoverStage.RESUME_WRITERS_COMMIT,
        CutoverStage.COMPLETED,
    ]
    live_fp = fp
    for stage in sequence:
        if stop_before is not None and stage == stop_before:
            break
        if stage == CutoverStage.QUIESCE_WAL:
            world.lock_records = []
            world.fd_hits = []
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
        if stage == CutoverStage.QUIESCE_WAL:
            live_fp = world.fingerprint(prod)
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
    assert "pause_markers_absent" in report["blockers"]
    assert world.lock_held is False


def test_wrong_fingerprint_refused(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    with pytest.raises(CutoverError) as exc:
        apply_stage(
            _opts(
                world,
                prod,
                reports,
                "0:0:0:dead",
                stage=CutoverStage.PAUSE_WRITERS,
                apply=True,
                backup=backup,
                staging=staging,
            )
        )
    assert exc.value.category == CutoverFailureCategory.SAFETY


def test_exact_sha_required_not_prefix(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    opts = _opts(
        world,
        prod,
        reports,
        fp,
        stage=CutoverStage.PAUSE_WRITERS,
        apply=True,
        backup=backup,
        staging=staging,
    )
    opts.expected_main_sha = MAIN_SHA[:7]
    with pytest.raises(CutoverError) as exc:
        apply_stage(opts)
    assert "40-character" in str(exc.value).lower()


def test_wrong_main_sha_refused(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    opts = _opts(
        world,
        prod,
        reports,
        fp,
        stage=CutoverStage.PAUSE_WRITERS,
        apply=True,
        backup=backup,
        staging=staging,
    )
    opts.expected_main_sha = "0" * 40
    world.head_sha = MAIN_SHA
    with pytest.raises(CutoverError) as exc:
        apply_stage(opts)
    assert exc.value.category == CutoverFailureCategory.SAFETY


def test_plan_option_drift_refused(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    apply_stage(
        _opts(
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
    other_backup = root / "emails_online_backup_OTHER.sqlite"
    with pytest.raises(CutoverError) as exc:
        apply_stage(
            _opts(
                world,
                prod,
                reports,
                fp,
                stage=CutoverStage.STOP_READERS,
                apply=True,
                backup=other_backup,
                staging=staging,
            )
        )
    assert "drift" in str(exc.value).lower()


def test_concurrent_stage_lock_contention(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    barrier = threading.Event()
    errors: list[BaseException] = []

    def hold_lock() -> None:
        try:
            with world.acquire_exclusive_lock(prod, MID):
                barrier.set()
                threading.Event().wait(0.3)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=hold_lock)
    t.start()
    assert barrier.wait(2.0)
    with pytest.raises(CutoverError) as exc:
        apply_stage(
            _opts(
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
    assert "contention" in str(exc.value).lower()
    t.join(timeout=2.0)
    assert not errors


def test_cross_maintenance_id_lock_contention(tmp_path: Path) -> None:
    db = tmp_path / "emails.sqlite"
    db.write_bytes(b"x")
    lock_dir = tmp_path / "locks"
    a = CutoverExclusiveLock(db, "maintAAAA1111", lock_dir=lock_dir)
    b = CutoverExclusiveLock(db, "maintBBBB2222", lock_dir=lock_dir)
    assert a.key == b.key
    a.acquire()
    with pytest.raises(CutoverError):
        b.acquire()
    a.release()


def test_different_databases_independent_locks(tmp_path: Path) -> None:
    a_path = tmp_path / "a.sqlite"
    b_path = tmp_path / "b.sqlite"
    a_path.write_bytes(b"a")
    b_path.write_bytes(b"b")
    lock_dir = tmp_path / "locks"
    la = CutoverExclusiveLock(a_path, MID, lock_dir=lock_dir)
    lb = CutoverExclusiveLock(b_path, MID, lock_dir=lock_dir)
    assert la.key != lb.key
    la.acquire()
    lb.acquire()
    la.release()
    lb.release()


def test_stale_lock_file_without_flock_reusable(tmp_path: Path) -> None:
    db = tmp_path / "emails.sqlite"
    db.write_bytes(b"x")
    lock_dir = tmp_path / "locks"
    lock = CutoverExclusiveLock(db, MID, lock_dir=lock_dir)
    lock.path.parent.mkdir(parents=True, exist_ok=True)
    lock.path.write_text("pid=1 maintenance_id=stale started_at=x\n", encoding="utf-8")
    # No flock held — acquire must succeed.
    lock.acquire()
    lock.release()


def test_real_flock_contention(tmp_path: Path) -> None:
    db = tmp_path / "emails.sqlite"
    db.write_bytes(b"x")
    lock_dir = tmp_path / "locks"
    a = CutoverExclusiveLock(db, MID, lock_dir=lock_dir)
    b = CutoverExclusiveLock(db, MID, lock_dir=lock_dir)
    a.acquire()
    with pytest.raises(CutoverError):
        b.acquire()
    a.release()


def test_live_stale_malformed_locks(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    apply_stage(
        _opts(
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
        _opts(
            world,
            prod,
            reports,
            fp,
            stage=CutoverStage.STOP_READERS,
            apply=True,
            backup=backup,
            staging=staging,
        )
    )
    world.lock_records = [
        LockRecord(basename="auto_refresh.lock", classification="live", pid=4242)
    ]
    with pytest.raises(CutoverError) as exc:
        apply_stage(
            _opts(
                world,
                prod,
                reports,
                fp,
                stage=CutoverStage.QUIESCE_WAL,
                apply=True,
                backup=backup,
                staging=staging,
            )
        )
    assert "live" in str(exc.value).lower()

    world.lock_records = [
        LockRecord(basename="auto_refresh.lock", classification="malformed", detail="x")
    ]
    with pytest.raises(CutoverError):
        apply_stage(
            _opts(
                world,
                prod,
                reports,
                fp,
                stage=CutoverStage.QUIESCE_WAL,
                apply=True,
                backup=backup,
                staging=staging,
            )
        )

    world.lock_records = [
        LockRecord(basename="auto_refresh.lock", classification="stale", pid=1)
    ]
    world.fd_hits = []
    # stale alone should allow quiesce
    apply_stage(
        _opts(
            world,
            prod,
            reports,
            fp,
            stage=CutoverStage.QUIESCE_WAL,
            apply=True,
            backup=backup,
            staging=staging,
        )
    )


def test_production_inode_fd_detection(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    apply_stage(
        _opts(
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
        _opts(
            world,
            prod,
            reports,
            fp,
            stage=CutoverStage.STOP_READERS,
            apply=True,
            backup=backup,
            staging=staging,
        )
    )
    world.lock_records = []
    world.fd_hits = [{"pid": 99999, "fd": "3", "device": 1, "inode": 2, "kind": "production"}]
    with pytest.raises(CutoverError) as exc:
        apply_stage(
            _opts(
                world,
                prod,
                reports,
                fp,
                stage=CutoverStage.QUIESCE_WAL,
                apply=True,
                backup=backup,
                staging=staging,
            )
        )
    assert "fd" in str(exc.value).lower()


def test_busy_wal_checkpoint(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    apply_stage(
        _opts(
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
        _opts(
            world,
            prod,
            reports,
            fp,
            stage=CutoverStage.STOP_READERS,
            apply=True,
            backup=backup,
            staging=staging,
        )
    )
    world.lock_records = []
    world.checkpoint_busy = 1
    with pytest.raises(CutoverError) as exc:
        apply_stage(
            _opts(
                world,
                prod,
                reports,
                fp,
                stage=CutoverStage.QUIESCE_WAL,
                apply=True,
                backup=backup,
                staging=staging,
            )
        )
    assert "busy" in str(exc.value).lower()


def test_backup_incomplete_manifest_refused(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.CREATE_CURRENT_BACKUP
    )
    live_fp = world.fingerprint(prod)
    world.backup_incomplete = True
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
    assert "incomplete" in str(exc.value).lower()


def test_evidence_compact_refused_as_source(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    bad = root / KNOWN_EVIDENCE_COMPACT
    world.files[str(bad)] = b"EVIDENCE"
    with pytest.raises(CutoverError) as exc:
        world.verify_candidate(bad)
    assert exc.value.category == CutoverFailureCategory.SAFETY


def test_cross_filesystem_swap_refused(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
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
    backup, staging = _paths(root)
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
    blob = f"{exc.value} {exc.value.recovery or ''}".lower()
    assert "dual-mv" in blob or "fallback" in blob or "ordinary mv" in blob


def test_pre_cutover_destination_race(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.ATOMIC_SWAP
    )
    live_fp = world.fingerprint(prod)
    pre = prod.with_name(f"{prod.name}.pre_cutover.{MID}")
    world.files[str(pre)] = b"RACE"
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
    assert "no-clobber" in str(exc.value).lower() or "exists" in str(exc.value).lower()


def test_atomic_swap_crash_boundaries(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    phases = [
        "before_exchange",
        "after_exchange",
        "after_dir_fsync",
        "before_retain",
        "after_retain",
    ]
    for phase in phases:
        w, p, r, rt, f = _world(tmp_path / phase)
        _run_through(w, p, r, f, rt, stop_before=CutoverStage.ATOMIC_SWAP)
        live = w.fingerprint(p)
        with pytest.raises(CutoverError):
            apply_stage(
                _opts(
                    w,
                    p,
                    r,
                    live,
                    stage=CutoverStage.ATOMIC_SWAP,
                    apply=True,
                    approve_swap=True,
                    backup=_paths(rt)[0],
                    staging=_paths(rt)[1],
                    fail_after=phase,
                )
            )


def test_reconcile_every_filesystem_state(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.READONLY_SMOKE
    )
    jpath = journal_path_for(
        CutoverOptions(maintenance_id=MID, expected_production_path=prod),
        prod,
    )
    journal = json.loads(world.files[str(jpath)].decode())
    from origenlab_email_pipeline.qa.sqlite_production_cutover import CutoverJournal

    j = CutoverJournal.from_dict(journal)
    pre = prod.with_name(j.pre_cutover_basename or "")
    recon = reconcile_atomic_swap_state(
        world, production=prod, staging=staging, pre_cutover=pre, journal=j
    )
    assert recon["recognized"] == "swap_complete"


def test_api_only_smoke_keeps_health_stopped(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.RESUME_SERVICES
    )
    assert world.services.api_active is True
    assert world.services.health_timer_active is False


def test_non_empty_wal_sidecar_rejected_in_smoke(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.READONLY_SMOKE
    )
    live_fp = world.fingerprint(prod)
    wal = Path(str(prod) + "-wal")
    world.files[str(wal)] = b"x" * 100
    # path_identity will report size; also set wal_size for wal_state
    world.wal_size = 100
    with pytest.raises(CutoverError) as exc:
        apply_stage(
            _opts(
                world,
                prod,
                reports,
                live_fp,
                stage=CutoverStage.READONLY_SMOKE,
                apply=True,
                backup=backup,
                staging=staging,
            )
        )
    assert "wal" in str(exc.value).lower()


def test_full_happy_path_retains_pre_cutover(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _run_through(world, prod, reports, fp, root)
    pre = [k for k in world.files if "pre_cutover" in Path(k).name]
    assert len(pre) == 1
    assert world.files[str(prod)].startswith(b"COMPACT:")
    assert world.mail_pause is False
    assert world.mirror_pause is False
    jpath = journal_path_for(
        CutoverOptions(maintenance_id=MID, expected_production_path=prod),
        prod,
    )
    journal = json.loads(world.files[str(jpath)].decode())
    assert journal["stage"] == CutoverStage.COMPLETED.value
    assert journal["writers_resumed"] is True
    assert journal["writer_resume_started"] is True
    assert journal["pre_cutover_basename"]


def test_rollback_before_writers_ok(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.READONLY_SMOKE
    )
    pre_path = prod.with_name(f"{prod.name}.pre_cutover.{MID}")
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


def test_rollback_refused_from_writer_resume_started(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    _run_through(
        world,
        prod,
        reports,
        fp,
        root,
        stop_before=CutoverStage.RESUME_WRITERS_RESTORE_MODE,
    )
    # writer_resume_started should be true after resume_writers_ponr
    jpath = journal_path_for(
        CutoverOptions(maintenance_id=MID, expected_production_path=prod),
        prod,
    )
    journal = json.loads(world.files[str(jpath)].decode())
    assert journal["writer_resume_started"] is True
    assert journal["writers_resumed"] is False
    assert journal["writable_mode_restored"] is False
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
                backup=backup,
                staging=staging,
            ),
            pre_cutover_path=pre,
            expected_old_fingerprint=world.fingerprint(pre),
            expected_new_fingerprint=world.fingerprint(prod),
        )
    assert "writer_resume_started" in str(exc.value).lower()


def test_arbitrary_rollback_path_refused(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.READONLY_SMOKE
    )
    evil = root / "evil.sqlite"
    world.files[str(evil)] = world.files[str(prod.with_name(f"{prod.name}.pre_cutover.{MID}"))]
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
                backup=backup,
                staging=staging,
            ),
            pre_cutover_path=evil,
            expected_old_fingerprint=world.fingerprint(evil),
            expected_new_fingerprint=world.fingerprint(prod),
        )
    assert "arbitrary" in str(exc.value).lower() or "match" in str(exc.value).lower()


def test_journal_path_escape_refused(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    opts = _opts(
        world,
        prod,
        reports,
        fp,
        stage=CutoverStage.PAUSE_WRITERS,
        apply=True,
        backup=backup,
        staging=staging,
    )
    opts.journal_path = tmp_path / "escape.journal.json"
    with pytest.raises(CutoverError) as exc:
        apply_stage(opts)
    assert "journal" in str(exc.value).lower()


def test_idempotent_resume_after_swap_complete(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.READONLY_SMOKE
    )
    live_fp = world.fingerprint(prod)
    # Re-enter atomic_swap after completion — should reconcile without repeating exchange.
    report = apply_stage(
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
    assert report.get("reconciled", {}).get("recognized") == "swap_complete"
    assert world.files[str(prod)].startswith(b"COMPACT:")


def test_crash_after_first_writer_unpause(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.RESUME_WRITERS_PONR
    )
    live_fp = world.fingerprint(prod)
    with pytest.raises(CutoverError):
        apply_stage(
            _opts(
                world,
                prod,
                reports,
                live_fp,
                stage=CutoverStage.RESUME_WRITERS_PONR,
                apply=True,
                backup=backup,
                staging=staging,
                fail_after="writer_resume_started",
            )
        )
    jpath = journal_path_for(
        CutoverOptions(maintenance_id=MID, expected_production_path=prod),
        prod,
    )
    journal = json.loads(world.files[str(jpath)].decode())
    assert journal["writer_resume_started"] is True
    assert journal["writers_resumed"] is False
    assert journal["writable_mode_restored"] is False


def test_non_sequential_stage_refused(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    apply_stage(
        _opts(
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
    with pytest.raises(CutoverError) as exc:
        apply_stage(
            _opts(
                world,
                prod,
                reports,
                fp,
                stage=CutoverStage.CREATE_CURRENT_BACKUP,
                apply=True,
                backup=backup,
                staging=staging,
            )
        )
    assert exc.value.category == CutoverFailureCategory.AMBIGUOUS


def test_swap_without_approve_refused(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
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
                backup=backup,
                staging=staging,
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


def test_maintenance_id_regex(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    opts = _opts(
        world,
        prod,
        reports,
        fp,
        stage=CutoverStage.PAUSE_WRITERS,
        apply=True,
        backup=backup,
        staging=staging,
    )
    opts.maintenance_id = "bad/id"
    with pytest.raises(CutoverError):
        apply_stage(opts)


def test_real_apply_blocked_flag_documented() -> None:
    assert REAL_PRODUCTION_APPLY_BLOCKED is False
    names = {e["name"] for e in REAL_SQLITE_WRITER_ENTRY_POINTS}
    assert "chilecompra_equipment_auto_refresh" not in names
    assert "mail_auto_refresh" in names
    reader_names = {e["name"] for e in REAL_SQLITE_READER_ENTRY_POINTS}
    assert "dashboard_auto_mirror" in reader_names
    assert "origenlab-api.service" in reader_names
    unrelated = {e["name"] for e in UNRELATED_EXTERNAL_WRITER_ENTRY_POINTS}
    assert "chilecompra_equipment_auto_refresh" in unrelated
    assert "sqlite_writers" in CUTOVER_ACCESS_INVENTORY


def test_no_clobber_backup_dest(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
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


def test_parse_fdinfo_access_modes() -> None:
    assert parse_fdinfo_access_mode(0o100000) == "read_only"  # O_RDONLY
    assert parse_fdinfo_access_mode(0o100001) == "writable"  # O_WRONLY
    assert parse_fdinfo_access_mode(0o100002) == "writable"  # O_RDWR
    assert parse_fdinfo_access_mode("0100002") == "writable"
    assert parse_fdinfo_access_mode("not-a-flag") == "unknown"


def test_writable_fd_fails_quiesce(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    apply_stage(
        _opts(
            world, prod, reports, fp, stage=CutoverStage.PAUSE_WRITERS, apply=True,
            backup=backup, staging=staging,
        )
    )
    apply_stage(
        _opts(
            world, prod, reports, fp, stage=CutoverStage.STOP_READERS, apply=True,
            backup=backup, staging=staging,
        )
    )
    world.lock_records = []
    world.fd_hits = [
        {"pid": 99999, "fd": "3", "device": 7, "inode": 4242, "kind": "production", "access": "writable"}
    ]
    with pytest.raises(CutoverError) as exc:
        apply_stage(
            _opts(
                world, prod, reports, fp, stage=CutoverStage.QUIESCE_WAL, apply=True,
                backup=backup, staging=staging,
            )
        )
    assert "writable" in str(exc.value).lower() or "fd" in str(exc.value).lower()


def test_unknown_fdinfo_fails_closed(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    apply_stage(
        _opts(
            world, prod, reports, fp, stage=CutoverStage.PAUSE_WRITERS, apply=True,
            backup=backup, staging=staging,
        )
    )
    apply_stage(
        _opts(
            world, prod, reports, fp, stage=CutoverStage.STOP_READERS, apply=True,
            backup=backup, staging=staging,
        )
    )
    world.lock_records = []
    world.fd_hits = [
        {"pid": 99999, "fd": "3", "device": 7, "inode": 4242, "kind": "production", "access": "unknown"}
    ]
    with pytest.raises(CutoverError):
        apply_stage(
            _opts(
                world, prod, reports, fp, stage=CutoverStage.QUIESCE_WAL, apply=True,
                backup=backup, staging=staging,
            )
        )


def test_write_barrier_blocks_writable_open(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.CREATE_CURRENT_BACKUP
    )
    assert world.modes[str(prod)] & 0o222 == 0
    assert world.try_open_writable(prod) is False
    jpath = journal_path_for(
        CutoverOptions(maintenance_id=MID, expected_production_path=prod), prod
    )
    journal = json.loads(world.files[str(jpath)].decode())
    assert journal["production_write_barrier_active"] is True
    assert journal["original_mode"] == 0o644


def test_pre_swap_abort_restores_permissions(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.APPROVE_SWAP
    )
    live_fp = world.fingerprint(prod)
    report = abort_before_swap(
        _opts(
            world, prod, reports, live_fp, stage=CutoverStage.VERIFY_CANDIDATE,
            apply=True, backup=backup, staging=staging,
        )
    )
    assert report["aborted_before_swap"] is True
    assert world.modes[str(prod)] == 0o644
    assert world.mail_pause is False
    assert world.services.api_active is True


def test_abort_refused_after_swap_intent(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.READONLY_SMOKE
    )
    with pytest.raises(CutoverError) as exc:
        abort_before_swap(
            _opts(
                world, prod, reports, world.fingerprint(prod),
                stage=CutoverStage.ATOMIC_SWAP, apply=True, approve_swap=True,
                backup=backup, staging=staging,
            )
        )
    assert "swap" in str(exc.value).lower()


def test_post_mail_fingerprint_drift_accepted(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.RESUME_WRITERS_OBSERVE_MAIL
    )
    # Simulate legitimate mail ingest changing content/size.
    world.files[str(prod)] = world.files[str(prod)] + b"-INGESTED"
    world.modes[str(prod)] = 0o644
    live_fp = world.fingerprint(prod)
    report = apply_stage(
        _opts(
            world, prod, reports, live_fp,
            stage=CutoverStage.RESUME_WRITERS_OBSERVE_MAIL, apply=True,
            backup=backup, staging=staging,
        )
    )
    assert report["post_mail_fingerprint"] == live_fp


def test_device_inode_replacement_after_mail_refused(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.RESUME_WRITERS_OBSERVE_MAIL
    )
    world.inode_overrides[str(prod)] = 999999
    with pytest.raises(CutoverError) as exc:
        apply_stage(
            _opts(
                world, prod, reports, world.fingerprint(prod),
                stage=CutoverStage.RESUME_WRITERS_OBSERVE_MAIL, apply=True,
                backup=backup, staging=staging,
            )
        )
    assert "inode" in str(exc.value).lower()


def test_chilecompra_live_lock_does_not_block_quiesce(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    apply_stage(
        _opts(
            world, prod, reports, fp, stage=CutoverStage.PAUSE_WRITERS, apply=True,
            backup=backup, staging=staging,
        )
    )
    apply_stage(
        _opts(
            world, prod, reports, fp, stage=CutoverStage.STOP_READERS, apply=True,
            backup=backup, staging=staging,
        )
    )
    world.lock_records = [
        LockRecord(
            basename=CHILECOMPRA_LOCK_FILENAME,
            classification="live",
            pid=5555,
        )
    ]
    world.fd_hits = []
    apply_stage(
        _opts(
            world, prod, reports, fp, stage=CutoverStage.QUIESCE_WAL, apply=True,
            backup=backup, staging=staging,
        )
    )


def test_ponr_before_chmod_restore(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.RESUME_WRITERS_RESTORE_MODE
    )
    jpath = journal_path_for(
        CutoverOptions(maintenance_id=MID, expected_production_path=prod), prod
    )
    journal = json.loads(world.files[str(jpath)].decode())
    assert journal["writer_resume_started"] is True
    assert journal["writable_mode_restored"] is False
    assert world.modes[str(prod)] & 0o222 == 0


def test_chilecompra_live_lock_does_not_block_preflight(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    world.lock_records = [
        LockRecord(
            basename=CHILECOMPRA_LOCK_FILENAME,
            classification="live",
            pid=5555,
        )
    ]
    report = plan_preflight(
        _opts(world, prod, reports, fp, stage=CutoverStage.PLAN_PREFLIGHT)
    )
    assert "active_sqlite_writers_or_locks" not in report["blockers"]
    assert report["chilecompra_blocks_rpo0"] is False


def test_mid_barrier_crash_abort_restores_mode(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.APPLY_OS_WRITE_BARRIER
    )
    live_fp = world.fingerprint(prod)
    with pytest.raises(CutoverError):
        apply_stage(
            _opts(
                world, prod, reports, live_fp,
                stage=CutoverStage.APPLY_OS_WRITE_BARRIER, apply=True,
                backup=backup, staging=staging,
                fail_after="after_production_chmod_barrier",
            )
        )
    # chmod applied; barrier_active may still be false
    assert world.modes[str(prod)] & 0o222 == 0
    jpath = journal_path_for(
        CutoverOptions(maintenance_id=MID, expected_production_path=prod), prod
    )
    journal = json.loads(world.files[str(jpath)].decode())
    assert journal.get("original_mode") == 0o644
    report = abort_before_swap(
        _opts(
            world, prod, reports, world.fingerprint(prod),
            stage=CutoverStage.APPLY_OS_WRITE_BARRIER, apply=True,
            backup=backup, staging=staging,
        )
    )
    assert report["aborted_before_swap"] is True
    assert world.modes[str(prod)] == 0o644


def test_chmod_refuses_device_inode_mismatch(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.APPLY_OS_WRITE_BARRIER
    )
    # Corrupt journal device so verified chmod fails
    jpath = journal_path_for(
        CutoverOptions(maintenance_id=MID, expected_production_path=prod), prod
    )
    journal = json.loads(world.files[str(jpath)].decode())
    journal["production_device"] = 999
    world.files[str(jpath)] = json.dumps(journal).encode()
    with pytest.raises(CutoverError) as exc:
        apply_stage(
            _opts(
                world, prod, reports, world.fingerprint(prod),
                stage=CutoverStage.APPLY_OS_WRITE_BARRIER, apply=True,
                backup=backup, staging=staging,
            )
        )
    assert "device" in str(exc.value).lower() or "inode" in str(exc.value).lower()


def test_mail_observe_failure_repauses_and_blocks_mirror(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.RESUME_WRITERS_OBSERVE_MAIL
    )
    world.quick_check_fail = True
    with pytest.raises(CutoverError) as exc:
        apply_stage(
            _opts(
                world, prod, reports, world.fingerprint(prod),
                stage=CutoverStage.RESUME_WRITERS_OBSERVE_MAIL, apply=True,
                backup=backup, staging=staging,
            )
        )
    assert "mail" in str(exc.value).lower()
    assert world.mail_pause is True
    assert "mirror" in (exc.value.recovery or "").lower()
    with pytest.raises(CutoverError) as exc2:
        apply_stage(
            _opts(
                world, prod, reports, world.fingerprint(prod),
                stage=CutoverStage.RESUME_WRITERS_MIRROR, apply=True,
                backup=backup, staging=staging,
            )
        )
    msg = str(exc2.value).lower()
    assert (
        "mail observe" in msg
        or "observe" in msg
        or "non-sequential" in msg
        or "resume_writers_mail" in msg
    )


def test_fail_after_matrix_never_loses_both_dbs(tmp_path: Path) -> None:
    phases = [
        "pause_writers",
        "stop_readers",
        "quiesce_wal",
        "permission_intent_barrier",
        "after_production_chmod_barrier",
        "apply_os_write_barrier",
        "create_current_backup",
        "compact_to_production_fs_staging",
        "verify_candidate",
        "approve_swap",
        "swap_intent",
        "before_exchange",
        "after_exchange",
        "after_dir_fsync",
        "before_retain",
        "after_retain",
        "atomic_swap",
    ]
    stage_for_phase = {
        "pause_writers": CutoverStage.PAUSE_WRITERS,
        "stop_readers": CutoverStage.STOP_READERS,
        "quiesce_wal": CutoverStage.QUIESCE_WAL,
        "permission_intent_barrier": CutoverStage.APPLY_OS_WRITE_BARRIER,
        "after_production_chmod_barrier": CutoverStage.APPLY_OS_WRITE_BARRIER,
        "apply_os_write_barrier": CutoverStage.APPLY_OS_WRITE_BARRIER,
        "create_current_backup": CutoverStage.CREATE_CURRENT_BACKUP,
        "compact_to_production_fs_staging": CutoverStage.COMPACT_TO_PRODUCTION_FS_STAGING,
        "verify_candidate": CutoverStage.VERIFY_CANDIDATE,
        "approve_swap": CutoverStage.APPROVE_SWAP,
        "swap_intent": CutoverStage.ATOMIC_SWAP,
        "before_exchange": CutoverStage.ATOMIC_SWAP,
        "after_exchange": CutoverStage.ATOMIC_SWAP,
        "after_dir_fsync": CutoverStage.ATOMIC_SWAP,
        "before_retain": CutoverStage.ATOMIC_SWAP,
        "after_retain": CutoverStage.ATOMIC_SWAP,
        "atomic_swap": CutoverStage.ATOMIC_SWAP,
    }
    for phase in phases:
        w, p, r, rt, f = _world(tmp_path / phase)
        backup, staging = _paths(rt)
        stop = stage_for_phase[phase]
        _run_through(w, p, r, f, rt, stop_before=stop)
        live = w.fingerprint(p)
        with pytest.raises(CutoverError):
            apply_stage(
                _opts(
                    w, p, r, live, stage=stop, apply=True,
                    approve_swap=stop
                    in {CutoverStage.APPROVE_SWAP, CutoverStage.ATOMIC_SWAP},
                    backup=backup, staging=staging, fail_after=phase,
                )
            )
        prod_present = w.is_file(p)
        staging_present = w.is_file(staging)
        pre = p.with_name(f"{p.name}.pre_cutover.{MID}")
        pre_present = w.is_file(pre)
        # Never lose both active production and all recoverable copies.
        recoverable = prod_present or staging_present or pre_present
        # Before any backup, only production must exist.
        if phase in {"pause_writers", "stop_readers", "quiesce_wal", "permission_intent_barrier", "after_production_chmod_barrier", "apply_os_write_barrier"}:
            assert prod_present
        else:
            assert recoverable, f"phase={phase} lost all DB copies"


def test_zero_write_preflight_creates_no_lock_file(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    lock_dir = tmp_path / "locks"
    before = tree_snapshot(tmp_path)
    plan_preflight(_opts(world, prod, reports, fp, stage=CutoverStage.PLAN_PREFLIGHT))
    after = tree_snapshot(tmp_path)
    assert before == after
    assert not lock_dir.exists()
    assert world.lock_held is False
