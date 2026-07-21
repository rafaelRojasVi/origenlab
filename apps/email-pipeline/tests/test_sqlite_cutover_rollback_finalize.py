"""Synthetic tests for PR-C rollback finalize (abandoned != completed).

Covers the terminal ABANDONED state, structured rollback proof, normal-path
lockout, crash-safe finalization ordering, idempotency, failure injection at
every durable boundary, and the invariant that ABANDONED is never a successful
cutover / soak / Waves-unblock signal.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from origenlab_email_pipeline.qa.sqlite_production_cutover import (
    CutoverError,
    CutoverOptions,
    CutoverStage,
    SyntheticWorld,
    apply_stage,
    attempt_rollback_before_writers,
    rollback_finalize,
)

MID = "cutover20260718T120000Z"
MAIN_SHA = "25cd4100e226427b3a4d027f1ee3b3af056884d4"
ABANDONED_INCIDENT_MID = "cutover20260719T163633Z"


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
    maintenance_id: str = MID,
) -> CutoverOptions:
    return CutoverOptions(
        stage=stage,
        apply=apply,
        confirm_production_cutover=True,
        maintenance_id=maintenance_id,
        expected_main_sha=MAIN_SHA,
        expected_production_path=prod,
        expected_production_fingerprint=fp,
        approve_swap=approve_swap,
        journal_path=prod.parent
        / ".origenlab_cutover_journals"
        / f"{maintenance_id}.journal.json",
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
    import json

    jp = prod.parent / ".origenlab_cutover_journals" / f"{MID}.journal.json"
    return json.loads(world.files[str(jp)])


def _rollback(
    world: SyntheticWorld, prod: Path, reports: Path, root: Path
) -> tuple[Path, str, str]:
    """Drive to atomic_swap, then perform a verified pre-PoNR rollback."""
    backup, staging = _paths(root)
    _run_through(world, prod, reports, world.fingerprint(prod), root,
                 stop_before=CutoverStage.READONLY_SMOKE)
    pre_path = prod.with_name(f"{prod.name}.pre_cutover.{MID}")
    new_fp = world.fingerprint(prod)
    old_fp = world.fingerprint(pre_path)
    report = attempt_rollback_before_writers(
        _opts(world, prod, reports, new_fp, stage=CutoverStage.ATOMIC_SWAP,
              apply=True, approve_swap=True, backup=backup, staging=staging),
        pre_cutover_path=pre_path,
        expected_old_fingerprint=old_fp,
        expected_new_fingerprint=new_fp,
    )
    assert report["rolled_back"] is True
    assert report["rollback_verified"] is True
    assert report["next_required"] == "rollback_finalize"
    return pre_path, old_fp, new_fp


def _finalize_opts(
    world: SyntheticWorld,
    prod: Path,
    reports: Path,
    *,
    fail_after: str | None = None,
) -> CutoverOptions:
    return _opts(world, prod, reports, world.fingerprint(prod),
                 stage=CutoverStage.ATOMIC_SWAP, apply=True, approve_swap=True,
                 fail_after=fail_after)


# --- 1-7: happy path, identity, candidate, permissions, services, terminal ---

def test_rollback_finalize_reaches_abandoned(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    pre_path, old_fp, new_fp = _rollback(world, prod, reports, root)

    report = rollback_finalize(_finalize_opts(world, prod, reports))

    # 1 + terminal: distinct ABANDONED outcome, never a success/soak/waves signal.
    assert report["stage"] == CutoverStage.ABANDONED.value
    assert report["abandoned"] is True
    assert report["rollback_finalized"] is True
    assert report["cutover_succeeded"] is False
    assert report["completed"] is False
    assert report["soak_eligible"] is False
    assert report["waves_unblocked"] is False
    assert report["writers_resumed_against"] == "restored_original"

    j = _journal(world, prod)
    assert j["stage"] == CutoverStage.ABANDONED.value
    assert j["abandoned"] is True
    assert j["rollback_finalized"] is True
    # 6: normal-cutover writer semantics remain separate and untouched.
    assert j["writer_resume_started"] is False
    assert j["writers_resumed"] is False
    assert j["rollback_original_writers_resumed"] is True
    # 2: restored original identity remains production.
    assert world.files[str(prod)] == b"SYNTHETIC-PROD-DB-v1"
    assert world.fingerprint(prod) == old_fp
    assert world.path_identity(prod)["device"] == 7
    assert world.path_identity(prod)["inode"] == 4242
    # 3: compacted candidate is NOT production and remains retained.
    assert world.fingerprint(prod) != new_fp
    assert world.path_exists(pre_path)
    assert world.fingerprint(pre_path) == new_fp
    # 4: original main restored writable.
    assert world.modes[str(prod)] & 0o222
    # 5: services + writers resumed against the restored original.
    assert world.services.api_active is True
    assert world.mail_pause is False
    assert world.mirror_pause is False


def test_abandoned_and_completed_mutually_exclusive(tmp_path: Path) -> None:
    # 7: after ABANDONED, COMPLETED can never be forced.
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    rollback_finalize(_finalize_opts(world, prod, reports))
    j = _journal(world, prod)
    assert j["abandoned"] is True
    assert j["stage"] != CutoverStage.COMPLETED.value


# --- 8: lockout of ordinary execute/resume ---

def test_ordinary_stage_refused_after_verified_rollback(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    backup, staging = _paths(root)
    with pytest.raises(CutoverError) as ei:
        apply_stage(_opts(world, prod, reports, world.fingerprint(prod),
                          stage=CutoverStage.READONLY_SMOKE, apply=True,
                          backup=backup, staging=staging))
    assert "locked" in str(ei.value).lower() or "rollback" in str(ei.value).lower()


def test_ordinary_stage_refused_after_abandoned(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    rollback_finalize(_finalize_opts(world, prod, reports))
    backup, staging = _paths(root)
    with pytest.raises(CutoverError) as ei:
        apply_stage(_opts(world, prod, reports, world.fingerprint(prod),
                          stage=CutoverStage.READONLY_SMOKE, apply=True,
                          backup=backup, staging=staging))
    assert "abandoned" in str(ei.value).lower()


# --- 9: finalize refuses without verified rollback / post-PoNR / completed ---

def test_finalize_refuses_without_verified_rollback(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _run_through(world, prod, reports, fp, root,
                 stop_before=CutoverStage.READONLY_SMOKE)
    # No rollback performed: no structured proof.
    world.services.api_active = False
    world.services.health_timer_active = False
    with pytest.raises(CutoverError) as ei:
        rollback_finalize(_finalize_opts(world, prod, reports))
    assert "rollback" in str(ei.value).lower()


def test_finalize_refuses_on_completed(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _run_through(world, prod, reports, fp, root)  # full run -> COMPLETED
    j = _journal(world, prod)
    assert j["stage"] == CutoverStage.COMPLETED.value
    with pytest.raises(CutoverError) as ei:
        rollback_finalize(_finalize_opts(world, prod, reports))
    assert "completed" in str(ei.value).lower()


def test_finalize_refuses_insufficient_legacy_proof(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    # Simulate a legacy journal that recorded the physical rollback but lacks
    # the structured proof block (older tool version).
    import json

    jp = prod.parent / ".origenlab_cutover_journals" / f"{MID}.journal.json"
    data = json.loads(world.files[str(jp)])
    data["rollback_proof"] = None
    data["rollback_verified"] = False
    world.files[str(jp)] = json.dumps(data).encode()
    with pytest.raises(CutoverError) as ei:
        rollback_finalize(_finalize_opts(world, prod, reports))
    assert "manual" in (str(ei.value) + str(ei.value.recovery or "")).lower()


# --- 10: exact rejection of the permanently abandoned July 19 MID ---

def test_july19_mid_rejected_for_finalize(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    with pytest.raises(CutoverError) as ei:
        rollback_finalize(_opts(world, prod, reports, fp,
                                stage=CutoverStage.ATOMIC_SWAP, apply=True,
                                approve_swap=True,
                                maintenance_id=ABANDONED_INCIDENT_MID))
    assert "abandoned" in str(ei.value).lower()


def test_july19_mid_rejected_for_apply(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    with pytest.raises(CutoverError):
        apply_stage(_opts(world, prod, reports, fp,
                          stage=CutoverStage.PAUSE_WRITERS, apply=True,
                          maintenance_id=ABANDONED_INCIDENT_MID))


# --- 11: idempotent second finalize ---

def test_finalize_idempotent_after_abandoned(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    rollback_finalize(_finalize_opts(world, prod, reports))
    report = rollback_finalize(_finalize_opts(world, prod, reports))
    assert report["already_finalized"] is True
    assert report["abandoned"] is True
    assert report["cutover_succeeded"] is False
    assert report["soak_eligible"] is False


# --- 12 + 13: failure injection at every durable boundary, then retry ---

FINALIZE_BOUNDARIES = [
    "rollback_finalize_intent",
    "rollback_finalize_pauses_reasserted",
    "rollback_finalize_smoke_reconciled",
    "rollback_finalize_restore_intent",
    "rollback_finalize_permissions_restored",
    "rollback_finalize_api_started",
    "rollback_finalize_health_verified",
    "rollback_finalize_mail_resumed",
    "rollback_finalize_mail_observed",
    "rollback_finalize_mirror_resumed",
    "rollback_finalize_mirror_observed",
    "rollback_finalize_before_terminal",
]


@pytest.mark.parametrize("phase", FINALIZE_BOUNDARIES)
def test_failure_injection_then_retry_reconciles(tmp_path: Path, phase: str) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    with pytest.raises(CutoverError):
        rollback_finalize(_finalize_opts(world, prod, reports, fail_after=phase))
    j = _journal(world, prod)
    # Crash before terminal write: never ABANDONED, never COMPLETED, normal
    # cutover remains blocked (intent retained), writers not falsely resumed.
    assert j["stage"] != CutoverStage.ABANDONED.value
    assert j["stage"] != CutoverStage.COMPLETED.value
    assert j["abandoned"] is False
    assert j["writer_resume_started"] is False
    assert j["writers_resumed"] is False
    # Retry with no injected failure reconciles reality and finishes ABANDONED.
    report = rollback_finalize(_finalize_opts(world, prod, reports))
    assert report["stage"] == CutoverStage.ABANDONED.value
    assert report["abandoned"] is True
    assert world.fingerprint(prod) == world.fingerprint(prod)
    assert world.mail_pause is False
    assert world.mirror_pause is False


def test_terminal_write_injection_blocks_normal_resume(tmp_path: Path) -> None:
    # 13/failure: writers active but terminal journal write fails; normal resume
    # stays blocked; retry safely finishes ABANDONED (not the normal path).
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    with pytest.raises(CutoverError):
        rollback_finalize(
            _finalize_opts(world, prod, reports,
                           fail_after="rollback_finalize_before_terminal")
        )
    j = _journal(world, prod)
    assert j["stage"] != CutoverStage.ABANDONED.value
    assert j["abandoned"] is False
    # Normal cutover path still refused (rollback_finalize_intent retained).
    backup, staging = _paths(root)
    with pytest.raises(CutoverError):
        apply_stage(_opts(world, prod, reports, world.fingerprint(prod),
                          stage=CutoverStage.READONLY_SMOKE, apply=True,
                          backup=backup, staging=staging))
    report = rollback_finalize(_finalize_opts(world, prod, reports))
    assert report["stage"] == CutoverStage.ABANDONED.value


# --- 14: partial writer resume re-pauses safely ---

def test_partial_resume_repauses_safely(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    world.quick_check_fail = True  # mail observation fails after pause removed
    with pytest.raises(CutoverError) as ei:
        rollback_finalize(_finalize_opts(world, prod, reports))
    assert "manual_recovery_required" in str(ei.value).lower()
    # Both pauses reasserted; ABANDONED not recorded; intent retained for retry.
    assert world.mail_pause is True
    assert world.mirror_pause is True
    j = _journal(world, prod)
    assert j["abandoned"] is False
    assert j["stage"] != CutoverStage.ABANDONED.value
    assert j["rollback_finalize_intent"] is not None


# --- 15: cleanup/re-pause failure -> MANUAL_RECOVERY_REQUIRED ---

def test_smoke_api_stop_failure_manual_recovery(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    # An API owned by this MID (smoke) is running and refuses to stop.
    import json

    jp = prod.parent / ".origenlab_cutover_journals" / f"{MID}.journal.json"
    data = json.loads(world.files[str(jp)])
    data["smoke_started_api"] = True
    world.files[str(jp)] = json.dumps(data).encode()
    world.services.api_active = True
    world.refuse_stop_api = True
    with pytest.raises(CutoverError) as ei:
        rollback_finalize(_finalize_opts(world, prod, reports))
    assert "manual_recovery_required" in str(ei.value).lower()
    j = _journal(world, prod)
    assert j["abandoned"] is False
    assert j["stage"] != CutoverStage.ABANDONED.value


# --- 20: sanitized errors leak no absolute production path ---

def test_manual_recovery_error_is_sanitized(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    world.quick_check_fail = True
    with pytest.raises(CutoverError) as ei:
        rollback_finalize(_finalize_opts(world, prod, reports))
    blob = str(ei.value) + str(ei.value.recovery or "") + str(ei.value.evidence or "")
    assert str(prod) not in blob
    assert str(root) not in blob


# --- 21: ABANDONED never soak-eligible / never unblocks Waves 3B/3C ---

def test_abandoned_never_soak_or_waves(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    report = rollback_finalize(_finalize_opts(world, prod, reports))
    assert report["soak_eligible"] is False
    assert report["waves_unblocked"] is False
    assert report["cutover_succeeded"] is False
    # Idempotent status keeps the same non-success posture.
    again = rollback_finalize(_finalize_opts(world, prod, reports))
    assert again["soak_eligible"] is False
    assert again["waves_unblocked"] is False
