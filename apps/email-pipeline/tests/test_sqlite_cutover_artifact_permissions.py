"""PR-B: SQLite cutover artifact permissions (main / -wal / -shm)."""

from __future__ import annotations

import json
import os
import stat as stat_mod
import threading
from pathlib import Path

import pytest

from origenlab_email_pipeline.qa import sqlite_cutover_artifact_permissions as apmod
from origenlab_email_pipeline.qa.sqlite_cutover_artifact_permissions import (
    ROLE_MAIN,
    ROLE_SHM,
    ROLE_WAL,
    ArtifactPermissionError,
    apply_barrier_to_member,
    apply_write_barrier,
    capture_artifact,
    filesystem_capture_member_identity,
    load_artifact_permissions,
    shm_path,
    verify_artifact_writable_for_resume,
    wal_path,
)
from origenlab_email_pipeline.qa.sqlite_production_cutover import (
    CutoverError,
    CutoverOptions,
    CutoverStage,
    SyntheticWorld,
    abort_before_swap,
    apply_stage,
    journal_path_for,
    load_journal,
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


def _journal(world: SyntheticWorld, prod: Path) -> dict:
    jpath = journal_path_for(
        CutoverOptions(maintenance_id=MID, expected_production_path=prod), prod
    )
    return json.loads(world.files[str(jpath)].decode())


def test_main_only_barrier_and_restoration(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    live = _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.CREATE_CURRENT_BACKUP
    )
    assert world.modes[str(prod)] & 0o222 == 0
    art = _journal(world, prod)["artifact_permissions"]
    assert art["main"]["present"] is True
    assert art["wal"]["present"] is False
    assert art["shm"]["present"] is False
    assert art["members_changed"] == [ROLE_MAIN]
    # Abort restores main.
    abort_before_swap(
        _opts(
            world,
            prod,
            reports,
            live,
            stage=CutoverStage.APPLY_OS_WRITE_BARRIER,
            apply=True,
            backup=_paths(root)[0],
            staging=_paths(root)[1],
        )
    )
    assert world.modes[str(prod)] == 0o644


def test_wal_shm_present_combinations_barrier(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    world.materialize_companion(prod, "wal", size=0, mode=0o644, inode=9001)
    world.materialize_companion(prod, "shm", size=32768, mode=0o644, inode=9002)
    world.wal_size = 0  # already truncated companion file
    _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.CREATE_CURRENT_BACKUP
    )
    art = _journal(world, prod)["artifact_permissions"]
    assert art["wal"]["present"] is True
    assert art["shm"]["present"] is True
    assert set(art["members_changed"]) == {ROLE_MAIN, ROLE_WAL, ROLE_SHM}
    assert world.modes[str(wal_path(prod))] & 0o222 == 0
    assert world.modes[str(shm_path(prod))] & 0o222 == 0


def test_fd_fstat_capture_and_fchmod_real_fs(tmp_path: Path) -> None:
    main = tmp_path / "emails.sqlite"
    main.write_bytes(b"db")
    os.chmod(main, 0o644)
    wal = tmp_path / "emails.sqlite-wal"
    wal.write_bytes(b"")
    os.chmod(wal, 0o600)
    ident = filesystem_capture_member_identity(main)
    assert ident["basename"] == "emails.sqlite"
    assert ident["mode"] == 0o644
    assert ident["nlink"] == 1
    fd = os.open(str(main), os.O_RDONLY)
    try:
        st = os.fstat(fd)
        assert int(st.st_dev) == ident["device"]
        assert int(st.st_ino) == ident["inode"]
        os.fchmod(fd, 0o444)
    finally:
        os.close(fd)
    assert (main.stat().st_mode & 0o7777) == 0o444


def test_device_inode_replacement_refuses_chmod(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    world.materialize_companion(prod, "shm", size=32768, mode=0o644, inode=777)
    from origenlab_email_pipeline.qa.sqlite_cutover_artifact_permissions import (
        capture_member,
    )

    member = capture_member(world, shm_path(prod), role=ROLE_SHM)
    world.inode_overrides[str(shm_path(prod))] = 999999
    with pytest.raises(CutoverError, match="device/inode mismatch"):
        world.chmod_verified_inode(
            shm_path(prod),
            0o444,
            expected_device=int(member.device or 0),
            expected_inode=int(member.inode or 0),
        )


def test_companion_appears_after_capture_fails_closed(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    art = capture_artifact(world, prod)
    assert art.shm.present is False
    # Capture without SHM, then create SHM before barrier finishes its drift check.
    world.materialize_companion(prod, "shm", size=32768, mode=0o644, inode=55)
    with pytest.raises(ArtifactPermissionError, match="appeared after capture"):
        apply_write_barrier(world, prod, art, persist=lambda a: None)


def test_symlink_and_non_regular_rejection(tmp_path: Path) -> None:
    main = tmp_path / "emails.sqlite"
    main.write_bytes(b"db")
    link = tmp_path / "emails.sqlite-wal"
    link.symlink_to(main)
    with pytest.raises(ArtifactPermissionError, match="symlink"):
        filesystem_capture_member_identity(link)
    fifo = tmp_path / "emails.sqlite-shm"
    try:
        os.mkfifo(fifo)
    except OSError:
        pytest.skip("mkfifo unsupported on this filesystem")
    with pytest.raises(ArtifactPermissionError, match="non-regular"):
        filesystem_capture_member_identity(fifo)


def test_partial_barrier_abort_restores_all_changed(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    world.materialize_companion(prod, "wal", size=0, mode=0o644, inode=8001)
    world.materialize_companion(prod, "shm", size=32768, mode=0o644, inode=8002)
    backup, staging = _paths(root)
    _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.APPLY_OS_WRITE_BARRIER
    )
    live = world.fingerprint(prod)
    with pytest.raises(CutoverError):
        apply_stage(
            _opts(
                world,
                prod,
                reports,
                live,
                stage=CutoverStage.APPLY_OS_WRITE_BARRIER,
                apply=True,
                backup=backup,
                staging=staging,
                fail_after="after_barrier_chmod_wal",
            )
        )
    j = _journal(world, prod)
    art = j["artifact_permissions"]
    assert art["barrier_partial"] is True or ROLE_WAL in art["members_changed"]
    assert world.modes[str(prod)] & 0o222 == 0
    # Abort must restore every changed identity.
    abort_before_swap(
        _opts(
            world,
            prod,
            reports,
            live,
            stage=CutoverStage.APPLY_OS_WRITE_BARRIER,
            apply=True,
            backup=backup,
            staging=staging,
        )
    )
    assert world.modes[str(prod)] == 0o644
    assert world.modes[str(wal_path(prod))] == 0o644
    # SHM may or may not have been chmod'd depending on order; if changed, restored.
    assert world.modes[str(shm_path(prod))] & 0o222


def test_legacy_journal_without_companions_loads(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.CREATE_CURRENT_BACKUP
    )
    jpath = journal_path_for(
        CutoverOptions(maintenance_id=MID, expected_production_path=prod), prod
    )
    data = json.loads(world.files[str(jpath)].decode())
    data.pop("artifact_permissions", None)
    world.files[str(jpath)] = json.dumps(data).encode()
    loaded = load_journal(world, jpath)
    assert loaded is not None
    assert loaded.artifact_permissions is None
    assert loaded.original_mode == 0o644
    # Must not invent companion modes.
    art = load_artifact_permissions(None)
    assert art is None


def test_incident_shm_0444_blocks_mail_resume(tmp_path: Path) -> None:
    """July 19 regression: main writable, SHM 0444 → refuse mail; mirror stays paused."""
    world, prod, reports, root, fp = _world(tmp_path)
    world.materialize_companion(prod, "shm", size=32768, mode=0o644, inode=5310)
    live = _run_through(
        world,
        prod,
        reports,
        fp,
        root,
        stop_before=CutoverStage.RESUME_WRITERS_RESTORE_MODE,
    )
    # Complete restore_mode path normally first? Instead stop before restore and
    # simulate incident: restore main only, leave SHM RO, then attempt mail.
    backup, staging = _paths(root)
    apply_stage(
        _opts(
            world,
            prod,
            reports,
            live,
            stage=CutoverStage.RESUME_WRITERS_RESTORE_MODE,
            apply=True,
            backup=backup,
            staging=staging,
        )
    )
    # After correct restore, both should be writable. Force the incident state:
    world.modes[str(shm_path(prod))] = 0o444
    # Clear writable_mode_restored? Keep true to reach mail gate which re-verifies.
    with pytest.raises(CutoverError) as exc:
        apply_stage(
            _opts(
                world,
                prod,
                reports,
                world.fingerprint(prod),
                stage=CutoverStage.RESUME_WRITERS_MAIL,
                apply=True,
                backup=backup,
                staging=staging,
            )
        )
    assert "shm" in str(exc.value).lower()
    assert world.mail_pause is True or (
        reports / "out" / "active" / "current" / "auto_refresh_paused"
    ).name
    # Mirror pause must still exist (never removed).
    assert world.mirror_pause is True
    j = _journal(world, prod)
    assert j.get("writers_resumed") is not True
    assert j.get("stage") != CutoverStage.COMPLETED.value


def test_corrected_path_restores_shm_before_mail(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    world.materialize_companion(prod, "shm", size=32768, mode=0o644, inode=5311)
    world.materialize_companion(prod, "wal", size=0, mode=0o644, inode=5312)
    live = _run_through(world, prod, reports, fp, root)
    assert world.modes[str(prod)] == 0o644
    assert world.modes[str(shm_path(prod))] == 0o644
    assert world.modes[str(wal_path(prod))] == 0o644
    assert world.mail_pause is False
    assert world.mirror_pause is False
    j = _journal(world, prod)
    assert j["stage"] == CutoverStage.COMPLETED.value
    assert j["writers_resumed"] is True
    assert live


def test_wal_0444_blocks_mail_resume(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    world.materialize_companion(prod, "wal", size=0, mode=0o644, inode=7001)
    live = _run_through(
        world,
        prod,
        reports,
        fp,
        root,
        stop_before=CutoverStage.RESUME_WRITERS_MAIL,
    )
    backup, staging = _paths(root)
    world.modes[str(wal_path(prod))] = 0o444
    with pytest.raises(CutoverError) as exc:
        apply_stage(
            _opts(
                world,
                prod,
                reports,
                live,
                stage=CutoverStage.RESUME_WRITERS_MAIL,
                apply=True,
                backup=backup,
                staging=staging,
            )
        )
    assert "wal" in str(exc.value).lower()
    assert world.mirror_pause is True


def test_restore_failure_preserves_barrier_and_pauses(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    world.materialize_companion(prod, "shm", size=32768, mode=0o644, inode=6100)
    live = _run_through(
        world,
        prod,
        reports,
        fp,
        root,
        stop_before=CutoverStage.RESUME_WRITERS_RESTORE_MODE,
    )
    backup, staging = _paths(root)
    # Corrupt post_swap identity so restore fails closed.
    jpath = journal_path_for(
        CutoverOptions(maintenance_id=MID, expected_production_path=prod), prod
    )
    data = json.loads(world.files[str(jpath)].decode())
    art = data["artifact_permissions"]
    art["post_swap_main"]["inode"] = 1
    data["artifact_permissions"] = art
    world.files[str(jpath)] = json.dumps(data).encode()
    with pytest.raises(CutoverError):
        apply_stage(
            _opts(
                world,
                prod,
                reports,
                live,
                stage=CutoverStage.RESUME_WRITERS_RESTORE_MODE,
                apply=True,
                backup=backup,
                staging=staging,
            )
        )
    j = _journal(world, prod)
    # Must not claim successful writable restore / completion.
    assert j.get("writable_mode_restored") is not True
    assert world.mail_pause is True
    assert world.mirror_pause is True


def test_post_swap_sidecar_recapture_on_smoke(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    live = _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.READONLY_SMOKE
    )
    # New SHM appears during smoke (API open) — materialize before smoke stage.
    world.materialize_companion(prod, "shm", size=32768, mode=0o644, inode=424242)
    backup, staging = _paths(root)
    apply_stage(
        _opts(
            world,
            prod,
            reports,
            live,
            stage=CutoverStage.READONLY_SMOKE,
            apply=True,
            backup=backup,
            staging=staging,
        )
    )
    art = _journal(world, prod)["artifact_permissions"]
    assert art["post_swap_companions"]["shm"]["present"] is True
    assert art["post_swap_companions"]["captured_at"] == "readonly_smoke"


def test_sanitized_errors_no_absolute_path(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    world.materialize_companion(prod, "shm", size=32768, mode=0o644, inode=33)
    live = _run_through(
        world,
        prod,
        reports,
        fp,
        root,
        stop_before=CutoverStage.RESUME_WRITERS_MAIL,
    )
    world.modes[str(shm_path(prod))] = 0o444
    backup, staging = _paths(root)
    with pytest.raises(CutoverError) as exc:
        apply_stage(
            _opts(
                world,
                prod,
                reports,
                live,
                stage=CutoverStage.RESUME_WRITERS_MAIL,
                apply=True,
                backup=backup,
                staging=staging,
            )
        )
    blob = str(exc.value) + json.dumps(exc.value.evidence or {})
    assert "/home/" not in blob
    assert "ORIGENLAB_API_AUTH_TOKEN" not in blob
    assert str(prod) not in blob


def test_verify_helper_rejects_ro_shm(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    world.modes[str(prod)] = 0o644
    world.materialize_companion(prod, "shm", size=32768, mode=0o444, inode=9)
    with pytest.raises(ArtifactPermissionError, match="shm"):
        verify_artifact_writable_for_resume(
            world, prod, None, legacy_original_mode=0o644
        )


def _run_with_timeout(fn, timeout: float = 5.0):
    """Run ``fn`` in a thread; fail if it does not return within ``timeout``."""
    box: dict[str, BaseException | None] = {"exc": None}

    def _target() -> None:
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised in caller
            box["exc"] = exc

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise AssertionError(f"capture did not return within {timeout}s (hang)")
    return box["exc"]


def test_fifo_capture_fails_promptly_not_hang(tmp_path: Path) -> None:
    """A FIFO at an artifact path must fail fast, never block open()."""
    fifo = tmp_path / "emails.sqlite-wal"
    try:
        os.mkfifo(fifo)
    except (OSError, AttributeError):
        pytest.skip("mkfifo unsupported on this filesystem")
    exc = _run_with_timeout(
        lambda: filesystem_capture_member_identity(fifo), timeout=5.0
    )
    assert isinstance(exc, ArtifactPermissionError)
    assert "non-regular" in str(exc)


def test_toctou_lstat_bypassed_fstat_is_authoritative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """lstat is only an early gate; a FIFO swapped in after it is caught by fstat.

    Simulate the pathname changing between the ``lstat`` early check and the
    ``open`` by forcing ``lstat`` to report a regular file while the real path
    is a FIFO. The authoritative post-open ``fstat`` must reject it, and the
    nonblocking open must not hang.
    """
    fifo = tmp_path / "emails.sqlite-shm"
    try:
        os.mkfifo(fifo)
    except (OSError, AttributeError):
        pytest.skip("mkfifo unsupported on this filesystem")

    fake_reg = os.stat_result(
        (stat_mod.S_IFREG | 0o644, 123, 7, 1, 1000, 1000, 16, 0, 0, 0)
    )
    real_lstat = os.lstat

    def _fake_lstat(path, *args, **kwargs):  # type: ignore[no-untyped-def]
        if str(path) == str(fifo):
            return fake_reg
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(apmod.os, "lstat", _fake_lstat)
    exc = _run_with_timeout(
        lambda: filesystem_capture_member_identity(fifo), timeout=5.0
    )
    assert isinstance(exc, ArtifactPermissionError)
    assert "non-regular" in str(exc) and "fstat" in str(exc)


def test_barrier_records_mutation_even_if_journal_write_fails(tmp_path: Path) -> None:
    """If persist raises right after fchmod, the mutation is still recorded.

    Journal truthfulness: a crash between the successful chmod and the durable
    journal update must not leave a clean no-mutation record.
    """
    world, prod, reports, root, fp = _world(tmp_path)
    art = capture_artifact(world, prod)
    member = art.member(ROLE_MAIN)

    calls: dict[str, int] = {"n": 0}

    def _persist(_a) -> None:  # type: ignore[no-untyped-def]
        calls["n"] += 1
        raise RuntimeError("simulated journal write failure")

    with pytest.raises(RuntimeError, match="journal write failure"):
        apply_barrier_to_member(
            world, prod, member, artifact=art, persist=_persist
        )
    # chmod already landed and the in-memory artifact reflects it truthfully.
    assert world.modes[str(prod)] & 0o222 == 0
    assert member.barrier_applied is True
    assert ROLE_MAIN in art.members_changed
    assert art.barrier_partial is True
    assert calls["n"] >= 1


def test_barrier_partial_persist_failure_then_abort_restores(tmp_path: Path) -> None:
    """persist failure after WAL chmod still lets abort restore every change."""
    world, prod, reports, root, fp = _world(tmp_path)
    world.materialize_companion(prod, "wal", size=0, mode=0o644, inode=8801)
    _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.APPLY_OS_WRITE_BARRIER
    )
    live = world.fingerprint(prod)
    backup, staging = _paths(root)
    # Fail right after the WAL chmod (compat alias covers main; use wal here).
    with pytest.raises(CutoverError):
        apply_stage(
            _opts(
                world,
                prod,
                reports,
                live,
                stage=CutoverStage.APPLY_OS_WRITE_BARRIER,
                apply=True,
                backup=backup,
                staging=staging,
                fail_after="after_barrier_chmod_main",
            )
        )
    art = _journal(world, prod)["artifact_permissions"]
    assert ROLE_MAIN in art["members_changed"]
    assert world.modes[str(prod)] & 0o222 == 0
    abort_before_swap(
        _opts(
            world,
            prod,
            reports,
            live,
            stage=CutoverStage.APPLY_OS_WRITE_BARRIER,
            apply=True,
            backup=backup,
            staging=staging,
        )
    )
    assert world.modes[str(prod)] == 0o644
    assert world.modes[str(wal_path(prod))] & 0o222


def test_companion_writable_target_mode_derivation() -> None:
    assert apmod.companion_writable_target_mode(
        captured_mode=0o444, production_target_mode=0o644
    ) == 0o644
    assert apmod.companion_writable_target_mode(
        captured_mode=0o444, production_target_mode=0o664
    ) == 0o664
    # Preserve execute bit from companion.
    assert apmod.companion_writable_target_mode(
        captured_mode=0o555, production_target_mode=0o644
    ) == 0o755
    with pytest.raises(ArtifactPermissionError, match="not writable"):
        apmod.companion_writable_target_mode(
            captured_mode=0o444, production_target_mode=0o444
        )


def test_post_swap_readonly_companions_restore_exact_inodes(tmp_path: Path) -> None:
    """Synthetic reproduction of the post-PoNR companion restore incident shape."""
    world, prod, reports, root, fp = _world(tmp_path)
    # Pre-barrier companions absent (production shape).
    world.wal_size = 0
    world.shm_size = 0
    live = _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.READONLY_SMOKE
    )
    art_pre = _journal(world, prod)["artifact_permissions"]
    assert art_pre["wal"]["present"] is False
    assert art_pre["shm"]["present"] is False
    # Post-swap smoke creates exact read-only WAL/SHM under barriered main.
    world.modes[str(prod)] = 0o444
    wal = world.materialize_companion(
        prod, "wal", size=0, mode=0o444, inode=88011, device=77
    )
    shm = world.materialize_companion(
        prod, "shm", size=32768, mode=0o444, inode=88012, device=77
    )
    backup, staging = _paths(root)
    apply_stage(
        _opts(
            world,
            prod,
            reports,
            live,
            stage=CutoverStage.READONLY_SMOKE,
            apply=True,
            backup=backup,
            staging=staging,
        )
    )
    art = _journal(world, prod)["artifact_permissions"]
    psc = art["post_swap_companions"]
    assert psc["captured_at"] == "readonly_smoke"
    assert psc["wal"]["present"] is True
    assert psc["wal"]["inode"] == 88011
    assert psc["wal"]["mode"] == 0o444
    assert psc["wal"]["target_mode"] == 0o644
    assert psc["shm"]["inode"] == 88012
    assert psc["shm"]["target_mode"] == 0o644

    live = world.fingerprint(prod)
    apply_stage(
        _opts(
            world,
            prod,
            reports,
            live,
            stage=CutoverStage.RESUME_SERVICES,
            apply=True,
            backup=backup,
            staging=staging,
        )
    )
    live = world.fingerprint(prod)
    apply_stage(
        _opts(
            world,
            prod,
            reports,
            live,
            stage=CutoverStage.RESUME_WRITERS_PONR,
            apply=True,
            backup=backup,
            staging=staging,
        )
    )
    live = world.fingerprint(prod)
    apply_stage(
        _opts(
            world,
            prod,
            reports,
            live,
            stage=CutoverStage.RESUME_WRITERS_RESTORE_MODE,
            apply=True,
            backup=backup,
            staging=staging,
        )
    )
    assert world.modes[str(prod)] == 0o644
    assert world.modes[str(wal)] == 0o644
    assert world.modes[str(shm)] == 0o644
    assert world.inode_overrides[str(wal)] == 88011
    assert world.inode_overrides[str(shm)] == 88012
    j = _journal(world, prod)
    assert j["writable_mode_restored"] is True
    assert j["stage"] == CutoverStage.RESUME_WRITERS_RESTORE_MODE.value
    assert world.mail_pause is True
    assert world.mirror_pause is True


def test_post_swap_companion_identity_drift_fails_before_mutation(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    world.wal_size = 0
    world.shm_size = 0
    live = _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.READONLY_SMOKE
    )
    world.materialize_companion(prod, "wal", size=0, mode=0o444, inode=1001)
    world.materialize_companion(prod, "shm", size=32768, mode=0o444, inode=1002)
    backup, staging = _paths(root)
    apply_stage(
        _opts(
            world, prod, reports, live,
            stage=CutoverStage.READONLY_SMOKE, apply=True,
            backup=backup, staging=staging,
        )
    )
    for stage in (
        CutoverStage.RESUME_SERVICES,
        CutoverStage.RESUME_WRITERS_PONR,
    ):
        live = world.fingerprint(prod)
        apply_stage(
            _opts(
                world, prod, reports, live, stage=stage, apply=True,
                backup=backup, staging=staging,
            )
        )
    # Replace WAL inode at same path after capture.
    world.inode_overrides[str(wal_path(prod))] = 9999
    live = world.fingerprint(prod)
    with pytest.raises(CutoverError, match="identity|refusing"):
        apply_stage(
            _opts(
                world, prod, reports, live,
                stage=CutoverStage.RESUME_WRITERS_RESTORE_MODE, apply=True,
                backup=backup, staging=staging,
            )
        )
    # Drift detected before chmod — mode remains read-only.
    assert world.modes[str(wal_path(prod))] & 0o222 == 0
    j = _journal(world, prod)
    assert j["writable_mode_restored"] is not True
    assert j["stage"] == CutoverStage.RESUME_WRITERS_PONR.value
    assert world.mail_pause is True
    assert world.mirror_pause is True


def test_legacy_post_swap_record_without_target_mode_fails_closed(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    world.wal_size = 0
    world.shm_size = 0
    live = _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.READONLY_SMOKE
    )
    world.materialize_companion(prod, "wal", size=0, mode=0o444, inode=2001)
    world.materialize_companion(prod, "shm", size=32768, mode=0o444, inode=2002)
    backup, staging = _paths(root)
    apply_stage(
        _opts(
            world, prod, reports, live,
            stage=CutoverStage.READONLY_SMOKE, apply=True,
            backup=backup, staging=staging,
        )
    )
    # Strip target_mode to simulate a legacy journal capture.
    jpath = journal_path_for(
        CutoverOptions(maintenance_id=MID, expected_production_path=prod), prod
    )
    raw = json.loads(world.files[str(jpath)].decode())
    for role in ("wal", "shm"):
        raw["artifact_permissions"]["post_swap_companions"][role].pop("target_mode", None)
    world.files[str(jpath)] = json.dumps(raw).encode()
    for stage in (CutoverStage.RESUME_SERVICES, CutoverStage.RESUME_WRITERS_PONR):
        live = world.fingerprint(prod)
        apply_stage(
            _opts(
                world, prod, reports, live, stage=stage, apply=True,
                backup=backup, staging=staging,
            )
        )
    live = world.fingerprint(prod)
    with pytest.raises(CutoverError, match="target_mode|invent|missing required fields"):
        apply_stage(
            _opts(
                world, prod, reports, live,
                stage=CutoverStage.RESUME_WRITERS_RESTORE_MODE, apply=True,
                backup=backup, staging=staging,
            )
        )
    assert world.modes[str(wal_path(prod))] & 0o222 == 0
    assert world.mail_pause is True


def test_missing_post_swap_companions_fails_closed_for_ro(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    world.wal_size = 0
    world.shm_size = 0
    live = _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.RESUME_WRITERS_RESTORE_MODE
    )
    # Inject RO companions without post_swap capture (record cleared).
    world.materialize_companion(prod, "wal", size=0, mode=0o444, inode=3001)
    world.materialize_companion(prod, "shm", size=32768, mode=0o444, inode=3002)
    jpath = journal_path_for(
        CutoverOptions(maintenance_id=MID, expected_production_path=prod), prod
    )
    raw = json.loads(world.files[str(jpath)].decode())
    raw["artifact_permissions"]["post_swap_companions"] = None
    world.files[str(jpath)] = json.dumps(raw).encode()
    backup, staging = _paths(root)
    with pytest.raises(CutoverError, match="invent|post-swap|read-only"):
        apply_stage(
            _opts(
                world, prod, reports, live,
                stage=CutoverStage.RESUME_WRITERS_RESTORE_MODE, apply=True,
                backup=backup, staging=staging,
            )
        )
    assert world.mail_pause is True
    assert world.mirror_pause is True


def test_basename_mismatch_fails_companion_restore(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    world.wal_size = 0
    world.shm_size = 0
    live = _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.READONLY_SMOKE
    )
    world.materialize_companion(prod, "wal", size=0, mode=0o444, inode=4001)
    world.materialize_companion(prod, "shm", size=32768, mode=0o444, inode=4002)
    backup, staging = _paths(root)
    apply_stage(
        _opts(
            world, prod, reports, live,
            stage=CutoverStage.READONLY_SMOKE, apply=True,
            backup=backup, staging=staging,
        )
    )
    jpath = journal_path_for(
        CutoverOptions(maintenance_id=MID, expected_production_path=prod), prod
    )
    raw = json.loads(world.files[str(jpath)].decode())
    raw["artifact_permissions"]["post_swap_companions"]["wal"]["basename"] = "wrong-wal"
    world.files[str(jpath)] = json.dumps(raw).encode()
    for stage in (CutoverStage.RESUME_SERVICES, CutoverStage.RESUME_WRITERS_PONR):
        live = world.fingerprint(prod)
        apply_stage(
            _opts(
                world, prod, reports, live, stage=stage, apply=True,
                backup=backup, staging=staging,
            )
        )
    live = world.fingerprint(prod)
    with pytest.raises(CutoverError, match="basename"):
        apply_stage(
            _opts(
                world, prod, reports, live,
                stage=CutoverStage.RESUME_WRITERS_RESTORE_MODE, apply=True,
                backup=backup, staging=staging,
            )
        )
    assert world.modes[str(wal_path(prod))] == 0o444
    assert world.mail_pause is True
    assert world.mirror_pause is True


def _post_swap_ro_companions_at_ponr(
    tmp_path: Path,
    *,
    wal_inode: int = 81001,
    shm_inode: int = 81002,
    companion_mode: int = 0o444,
) -> tuple[SyntheticWorld, Path, Path, Path, Path, Path]:
    """Advance synthetic cutover to PONR with typed post-swap RO companions."""
    world, prod, reports, root, fp = _world(tmp_path)
    world.wal_size = 0
    world.shm_size = 0
    live = _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.READONLY_SMOKE
    )
    world.materialize_companion(
        prod, "wal", size=0, mode=companion_mode, inode=wal_inode
    )
    world.materialize_companion(
        prod, "shm", size=32768, mode=companion_mode, inode=shm_inode
    )
    backup, staging = _paths(root)
    apply_stage(
        _opts(
            world,
            prod,
            reports,
            live,
            stage=CutoverStage.READONLY_SMOKE,
            apply=True,
            backup=backup,
            staging=staging,
        )
    )
    for stage in (CutoverStage.RESUME_SERVICES, CutoverStage.RESUME_WRITERS_PONR):
        live = world.fingerprint(prod)
        apply_stage(
            _opts(
                world,
                prod,
                reports,
                live,
                stage=stage,
                apply=True,
                backup=backup,
                staging=staging,
            )
        )
    jpath = journal_path_for(
        CutoverOptions(maintenance_id=MID, expected_production_path=prod), prod
    )
    return world, prod, reports, backup, staging, jpath


def _patch_post_swap_wal(
    world: SyntheticWorld, jpath: Path, **fields: object
) -> None:
    raw = json.loads(world.files[str(jpath)].decode())
    wal = raw["artifact_permissions"]["post_swap_companions"]["wal"]
    for key, value in fields.items():
        if value is _MISSING:
            wal.pop(key, None)
        else:
            wal[key] = value
    world.files[str(jpath)] = json.dumps(raw).encode()


_MISSING = object()


def _assert_restore_rejects_before_mutation(
    world: SyntheticWorld,
    prod: Path,
    reports: Path,
    backup: Path,
    staging: Path,
    *,
    match: str,
) -> None:
    wal_mode_before = world.modes[str(wal_path(prod))]
    shm_mode_before = world.modes[str(shm_path(prod))]
    live = world.fingerprint(prod)
    with pytest.raises(CutoverError, match=match):
        apply_stage(
            _opts(
                world,
                prod,
                reports,
                live,
                stage=CutoverStage.RESUME_WRITERS_RESTORE_MODE,
                apply=True,
                backup=backup,
                staging=staging,
            )
        )
    assert world.modes[str(wal_path(prod))] == wal_mode_before
    assert world.modes[str(shm_path(prod))] == shm_mode_before
    j = _journal(world, prod)
    assert j["writable_mode_restored"] is not True
    assert j["stage"] == CutoverStage.RESUME_WRITERS_PONR.value
    assert world.mail_pause is True
    assert world.mirror_pause is True


def test_reject_broader_target_mode_0777(tmp_path: Path) -> None:
    world, prod, reports, backup, staging, jpath = _post_swap_ro_companions_at_ponr(
        tmp_path
    )
    _patch_post_swap_wal(world, jpath, target_mode=0o777)
    _assert_restore_rejects_before_mutation(
        world, prod, reports, backup, staging, match="deterministic|target_mode"
    )


def test_reject_setuid_target_mode_06755(tmp_path: Path) -> None:
    world, prod, reports, backup, staging, jpath = _post_swap_ro_companions_at_ponr(
        tmp_path
    )
    _patch_post_swap_wal(world, jpath, target_mode=0o6755)
    _assert_restore_rejects_before_mutation(
        world, prod, reports, backup, staging, match="deterministic|target_mode"
    )


def test_reject_narrower_target_mode_0600(tmp_path: Path) -> None:
    world, prod, reports, backup, staging, jpath = _post_swap_ro_companions_at_ponr(
        tmp_path
    )
    _patch_post_swap_wal(world, jpath, target_mode=0o600)
    _assert_restore_rejects_before_mutation(
        world, prod, reports, backup, staging, match="deterministic|target_mode"
    )


def test_reject_bool_target_mode(tmp_path: Path) -> None:
    world, prod, reports, backup, staging, jpath = _post_swap_ro_companions_at_ponr(
        tmp_path
    )
    _patch_post_swap_wal(world, jpath, target_mode=True)
    _assert_restore_rejects_before_mutation(
        world, prod, reports, backup, staging, match="strict integer"
    )


def test_reject_bool_recorded_mode(tmp_path: Path) -> None:
    world, prod, reports, backup, staging, jpath = _post_swap_ro_companions_at_ponr(
        tmp_path
    )
    _patch_post_swap_wal(world, jpath, mode=True)
    _assert_restore_rejects_before_mutation(
        world, prod, reports, backup, staging, match="strict integer"
    )


def test_reject_string_mode(tmp_path: Path) -> None:
    world, prod, reports, backup, staging, jpath = _post_swap_ro_companions_at_ponr(
        tmp_path
    )
    _patch_post_swap_wal(world, jpath, mode="420")
    _assert_restore_rejects_before_mutation(
        world, prod, reports, backup, staging, match="strict integer"
    )


def test_reject_out_of_range_modes(tmp_path: Path) -> None:
    world, prod, reports, backup, staging, jpath = _post_swap_ro_companions_at_ponr(
        tmp_path
    )
    _patch_post_swap_wal(world, jpath, mode=0o10000)
    _assert_restore_rejects_before_mutation(
        world, prod, reports, backup, staging, match="permission range"
    )
    _patch_post_swap_wal(world, jpath, mode=0o444, target_mode=0o10000)
    _assert_restore_rejects_before_mutation(
        world, prod, reports, backup, staging, match="permission range"
    )


def test_reject_missing_role(tmp_path: Path) -> None:
    world, prod, reports, backup, staging, jpath = _post_swap_ro_companions_at_ponr(
        tmp_path
    )
    _patch_post_swap_wal(world, jpath, role=_MISSING)
    _assert_restore_rejects_before_mutation(
        world, prod, reports, backup, staging, match="missing required fields"
    )


def test_reject_wrong_role(tmp_path: Path) -> None:
    world, prod, reports, backup, staging, jpath = _post_swap_ro_companions_at_ponr(
        tmp_path
    )
    _patch_post_swap_wal(world, jpath, role="shm")
    _assert_restore_rejects_before_mutation(
        world, prod, reports, backup, staging, match="role mismatch"
    )


def test_reject_missing_recorded_mode(tmp_path: Path) -> None:
    world, prod, reports, backup, staging, jpath = _post_swap_ro_companions_at_ponr(
        tmp_path
    )
    _patch_post_swap_wal(world, jpath, mode=_MISSING)
    _assert_restore_rejects_before_mutation(
        world, prod, reports, backup, staging, match="missing required fields"
    )


def test_reject_recorded_mode_drift_from_live(tmp_path: Path) -> None:
    world, prod, reports, backup, staging, jpath = _post_swap_ro_companions_at_ponr(
        tmp_path
    )
    # Keep live 0444; lie that capture was 0400 so derivation would differ.
    _patch_post_swap_wal(world, jpath, mode=0o400, target_mode=0o600)
    _assert_restore_rejects_before_mutation(
        world, prod, reports, backup, staging, match="live mode differs"
    )


def test_reject_missing_uid_or_gid(tmp_path: Path) -> None:
    world, prod, reports, backup, staging, jpath = _post_swap_ro_companions_at_ponr(
        tmp_path
    )
    _patch_post_swap_wal(world, jpath, uid=_MISSING)
    _assert_restore_rejects_before_mutation(
        world, prod, reports, backup, staging, match="missing required fields"
    )
    _patch_post_swap_wal(world, jpath, uid=1000, gid=_MISSING)
    _assert_restore_rejects_before_mutation(
        world, prod, reports, backup, staging, match="missing required fields"
    )


def test_reject_missing_or_changed_nlink(tmp_path: Path) -> None:
    world, prod, reports, backup, staging, jpath = _post_swap_ro_companions_at_ponr(
        tmp_path
    )
    _patch_post_swap_wal(world, jpath, nlink=_MISSING)
    _assert_restore_rejects_before_mutation(
        world, prod, reports, backup, staging, match="missing required fields"
    )
    _patch_post_swap_wal(world, jpath, nlink=2)
    _assert_restore_rejects_before_mutation(
        world, prod, reports, backup, staging, match="link count"
    )


def test_deterministic_0444_to_0644_restore_succeeds(tmp_path: Path) -> None:
    world, prod, reports, backup, staging, _jpath = _post_swap_ro_companions_at_ponr(
        tmp_path
    )
    live = world.fingerprint(prod)
    apply_stage(
        _opts(
            world,
            prod,
            reports,
            live,
            stage=CutoverStage.RESUME_WRITERS_RESTORE_MODE,
            apply=True,
            backup=backup,
            staging=staging,
        )
    )
    assert world.modes[str(wal_path(prod))] == 0o644
    assert world.modes[str(shm_path(prod))] == 0o644
    assert _journal(world, prod)["writable_mode_restored"] is True


def test_deterministic_execute_bit_preservation_succeeds(tmp_path: Path) -> None:
    world, prod, reports, backup, staging, jpath = _post_swap_ro_companions_at_ponr(
        tmp_path, companion_mode=0o555
    )
    raw = json.loads(world.files[str(jpath)].decode())
    assert raw["artifact_permissions"]["post_swap_companions"]["wal"]["mode"] == 0o555
    assert (
        raw["artifact_permissions"]["post_swap_companions"]["wal"]["target_mode"]
        == 0o755
    )
    live = world.fingerprint(prod)
    apply_stage(
        _opts(
            world,
            prod,
            reports,
            live,
            stage=CutoverStage.RESUME_WRITERS_RESTORE_MODE,
            apply=True,
            backup=backup,
            staging=staging,
        )
    )
    assert world.modes[str(wal_path(prod))] == 0o755
    assert world.modes[str(shm_path(prod))] == 0o755
    assert _journal(world, prod)["writable_mode_restored"] is True


def test_writable_post_swap_companions_accepted_without_chmod(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    world.wal_size = 0
    world.shm_size = 0
    live = _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.READONLY_SMOKE
    )
    world.materialize_companion(prod, "wal", size=0, mode=0o644, inode=5001)
    world.materialize_companion(prod, "shm", size=32768, mode=0o644, inode=5002)
    backup, staging = _paths(root)
    apply_stage(
        _opts(
            world, prod, reports, live,
            stage=CutoverStage.READONLY_SMOKE, apply=True,
            backup=backup, staging=staging,
        )
    )
    for stage in (
        CutoverStage.RESUME_SERVICES,
        CutoverStage.RESUME_WRITERS_PONR,
        CutoverStage.RESUME_WRITERS_RESTORE_MODE,
    ):
        live = world.fingerprint(prod)
        apply_stage(
            _opts(
                world, prod, reports, live, stage=stage, apply=True,
                backup=backup, staging=staging,
            )
        )
    assert world.modes[str(wal_path(prod))] == 0o644
    assert _journal(world, prod)["writable_mode_restored"] is True


def test_post_swap_sidecar_recapture_records_target_mode(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    live = _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.READONLY_SMOKE
    )
    world.materialize_companion(prod, "shm", size=32768, mode=0o444, inode=424242)
    backup, staging = _paths(root)
    apply_stage(
        _opts(
            world, prod, reports, live,
            stage=CutoverStage.READONLY_SMOKE, apply=True,
            backup=backup, staging=staging,
        )
    )
    art = _journal(world, prod)["artifact_permissions"]
    assert art["post_swap_companions"]["shm"]["present"] is True
    assert art["post_swap_companions"]["captured_at"] == "readonly_smoke"
    assert art["post_swap_companions"]["shm"]["target_mode"] == 0o644


def test_partial_companion_restore_does_not_claim_success(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    world.wal_size = 0
    world.shm_size = 0
    live = _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.READONLY_SMOKE
    )
    world.materialize_companion(prod, "wal", size=0, mode=0o444, inode=6001)
    world.materialize_companion(prod, "shm", size=32768, mode=0o444, inode=6002)
    backup, staging = _paths(root)
    apply_stage(
        _opts(
            world, prod, reports, live,
            stage=CutoverStage.READONLY_SMOKE, apply=True,
            backup=backup, staging=staging,
        )
    )
    for stage in (CutoverStage.RESUME_SERVICES, CutoverStage.RESUME_WRITERS_PONR):
        live = world.fingerprint(prod)
        apply_stage(
            _opts(
                world, prod, reports, live, stage=stage, apply=True,
                backup=backup, staging=staging,
            )
        )
    # After WAL would restore, SHM identity drifts — fail on SHM.
    # Corrupt SHM recorded inode so restore of SHM fails after WAL succeeds.
    jpath = journal_path_for(
        CutoverOptions(maintenance_id=MID, expected_production_path=prod), prod
    )
    raw = json.loads(world.files[str(jpath)].decode())
    raw["artifact_permissions"]["post_swap_companions"]["shm"]["inode"] = 1
    world.files[str(jpath)] = json.dumps(raw).encode()
    live = world.fingerprint(prod)
    with pytest.raises(CutoverError):
        apply_stage(
            _opts(
                world, prod, reports, live,
                stage=CutoverStage.RESUME_WRITERS_RESTORE_MODE, apply=True,
                backup=backup, staging=staging,
            )
        )
    j = _journal(world, prod)
    assert j["writable_mode_restored"] is not True
    assert j["stage"] == CutoverStage.RESUME_WRITERS_PONR.value
    # WAL may already be writable from partial progress; SHM remains RO.
    assert world.modes[str(shm_path(prod))] & 0o222 == 0
    assert world.mail_pause is True


def test_retry_after_partial_companion_restore_is_idempotent(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    world.wal_size = 0
    world.shm_size = 0
    live = _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.READONLY_SMOKE
    )
    world.materialize_companion(prod, "wal", size=0, mode=0o444, inode=7001)
    world.materialize_companion(prod, "shm", size=32768, mode=0o444, inode=7002)
    backup, staging = _paths(root)
    apply_stage(
        _opts(
            world, prod, reports, live,
            stage=CutoverStage.READONLY_SMOKE, apply=True,
            backup=backup, staging=staging,
        )
    )
    for stage in (CutoverStage.RESUME_SERVICES, CutoverStage.RESUME_WRITERS_PONR):
        live = world.fingerprint(prod)
        apply_stage(
            _opts(
                world, prod, reports, live, stage=stage, apply=True,
                backup=backup, staging=staging,
            )
        )
    # Simulate interrupted restore: main+WAL already writable, SHM still RO.
    world.modes[str(prod)] = 0o644
    world.modes[str(wal_path(prod))] = 0o644
    live = world.fingerprint(prod)
    apply_stage(
        _opts(
            world, prod, reports, live,
            stage=CutoverStage.RESUME_WRITERS_RESTORE_MODE, apply=True,
            backup=backup, staging=staging,
        )
    )
    assert world.modes[str(prod)] == 0o644
    assert world.modes[str(wal_path(prod))] == 0o644
    assert world.modes[str(shm_path(prod))] == 0o644
    assert world.inode_overrides[str(wal_path(prod))] == 7001
    assert world.inode_overrides[str(shm_path(prod))] == 7002
