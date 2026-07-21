"""Synthetic tests for PR-C rollback finalize (abandoned != completed).

Covers the terminal ABANDONED state, structured rollback proof, normal-path +
alternate-entrypoint lockout, crash-safe finalization ordering, retry after a
real post-resume fingerprint mutation, durable mail/mirror resume truth, sidecar
lifecycle across the physical rollback, pre-maintenance service ownership,
manual-recovery truthfulness, the approval/CLI contract, failure injection at
every durable boundary, and the invariant that ABANDONED is never a successful
cutover / soak / Waves-unblock signal.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from origenlab_email_pipeline.qa.sqlite_production_cutover import (
    CutoverError,
    CutoverOptions,
    CutoverStage,
    SyntheticWorld,
    abort_before_swap,
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
    main_sha: str = MAIN_SHA,
) -> CutoverOptions:
    return CutoverOptions(
        stage=stage,
        apply=apply,
        confirm_production_cutover=True,
        maintenance_id=maintenance_id,
        expected_main_sha=main_sha,
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


def _journal_path(prod: Path) -> Path:
    return prod.parent / ".origenlab_cutover_journals" / f"{MID}.journal.json"


def _journal(world: SyntheticWorld, prod: Path) -> dict:
    return json.loads(world.files[str(_journal_path(prod))])


def _edit_journal(world: SyntheticWorld, prod: Path, **changes) -> None:
    jp = _journal_path(prod)
    data = json.loads(world.files[str(jp)])
    data.update(changes)
    world.files[str(jp)] = json.dumps(data).encode()


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
    apply: bool = True,
    approve_swap: bool = True,
    maintenance_id: str = MID,
    main_sha: str = MAIN_SHA,
    stage: CutoverStage = CutoverStage.PLAN_PREFLIGHT,
) -> CutoverOptions:
    return _opts(world, prod, reports, world.fingerprint(prod),
                 stage=stage, apply=apply, approve_swap=approve_swap,
                 fail_after=fail_after, maintenance_id=maintenance_id,
                 main_sha=main_sha)


# --------------------------------------------------------------------------- #
# Happy path + terminal invariants
# --------------------------------------------------------------------------- #

def test_rollback_finalize_reaches_abandoned(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    pre_path, old_fp, new_fp = _rollback(world, prod, reports, root)

    report = rollback_finalize(_finalize_opts(world, prod, reports))

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
    assert j["writer_resume_started"] is False
    assert j["writers_resumed"] is False
    assert j["rollback_original_writers_resumed"] is True
    # Durable mail/mirror resume truth.
    assert j["rollback_finalize_mail_resumed"] is True
    assert j["rollback_finalize_mirror_resumed"] is True
    assert j["rollback_finalize_mail_observed_ok"] is True
    assert j["rollback_finalize_mirror_observed_ok"] is True
    assert j["rollback_finalize_post_mail_fingerprint"] is not None
    # Pre-maintenance service policy captured and honored.
    assert j["pre_maintenance_api_active"] is True
    assert j["pre_maintenance_health_timer_active"] is True
    # Restored original identity + retained candidate.
    assert world.files[str(prod)] == b"SYNTHETIC-PROD-DB-v1"
    assert world.fingerprint(prod) == old_fp
    assert world.path_identity(prod)["device"] == 7
    assert world.path_identity(prod)["inode"] == 4242
    assert world.fingerprint(prod) != new_fp
    assert world.path_exists(pre_path)
    assert world.fingerprint(pre_path) == new_fp
    assert world.modes[str(prod)] & 0o222
    assert world.services.api_active is True
    assert world.services.health_timer_active is True
    assert world.mail_pause is False
    assert world.mirror_pause is False


def test_abandoned_and_completed_mutually_exclusive(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    rollback_finalize(_finalize_opts(world, prod, reports))
    j = _journal(world, prod)
    assert j["abandoned"] is True
    assert j["stage"] != CutoverStage.COMPLETED.value


def test_finalize_idempotent_after_abandoned(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    rollback_finalize(_finalize_opts(world, prod, reports))
    report = rollback_finalize(_finalize_opts(world, prod, reports))
    assert report["already_finalized"] is True
    assert report["abandoned"] is True
    assert report["cutover_succeeded"] is False
    assert report["soak_eligible"] is False


def test_abandoned_never_soak_or_waves(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    report = rollback_finalize(_finalize_opts(world, prod, reports))
    assert report["soak_eligible"] is False
    assert report["waves_unblocked"] is False
    assert report["cutover_succeeded"] is False
    again = rollback_finalize(_finalize_opts(world, prod, reports))
    assert again["soak_eligible"] is False
    assert again["waves_unblocked"] is False


# --------------------------------------------------------------------------- #
# F1: retry ordering after pause removal + real fingerprint mutation
# --------------------------------------------------------------------------- #

def test_retry_after_real_mail_fingerprint_mutation_finishes_abandoned(
    tmp_path: Path,
) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    pre_path, old_fp, new_fp = _rollback(world, prod, reports, root)
    # Crash right after mail resumed (pause removed, post-mail fp recorded).
    with pytest.raises(CutoverError):
        rollback_finalize(
            _finalize_opts(world, prod, reports,
                           fail_after="rollback_finalize_mail_resumed")
        )
    j = _journal(world, prod)
    assert j["rollback_finalize_mail_resumed"] is True
    assert j["rollback_finalize_mail_pause_absent"] is True
    assert j["rollback_finalize_post_mail_fingerprint"] is not None
    # A real mail run legitimately mutates size/content (identity unchanged).
    world.files[str(prod)] = b"SYNTHETIC-PROD-DB-v1-with-new-mail-rows"
    assert world.fingerprint(prod) != old_fp
    # Retry must reassert pauses first and NOT reject on the pristine fingerprint.
    report = rollback_finalize(_finalize_opts(world, prod, reports))
    assert report["stage"] == CutoverStage.ABANDONED.value
    assert report["abandoned"] is True
    # Immutable identity preserved; mutated content retained (no data loss).
    assert world.path_identity(prod)["device"] == 7
    assert world.path_identity(prod)["inode"] == 4242
    assert world.files[str(prod)] == b"SYNTHETIC-PROD-DB-v1-with-new-mail-rows"
    j2 = _journal(world, prod)
    assert j2["rollback_finalize_writers_repaused"] is True


def test_reassert_pauses_before_mutable_gate_on_plain_retry(tmp_path: Path) -> None:
    # Even without a content mutation, a crash after pause removal must be
    # re-paused on retry (pauses present again before the mutable gate).
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    with pytest.raises(CutoverError):
        rollback_finalize(
            _finalize_opts(world, prod, reports,
                           fail_after="rollback_finalize_mirror_resumed")
        )
    assert world.mail_pause is False  # mail removed pre-crash
    report = rollback_finalize(_finalize_opts(world, prod, reports))
    assert report["stage"] == CutoverStage.ABANDONED.value
    assert world.mail_pause is False and world.mirror_pause is False


# --------------------------------------------------------------------------- #
# F2: durable mail/mirror resume truth
# --------------------------------------------------------------------------- #

def test_manual_recovery_preserves_historical_resume_truth(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    # Fail mail observation after the mail pause was removed.
    world.quick_check_fail = True
    with pytest.raises(CutoverError):
        rollback_finalize(_finalize_opts(world, prod, reports))
    j = _journal(world, prod)
    # Historical truth kept; current state shows re-paused.
    assert j["rollback_finalize_mail_resumed"] is True
    assert j["rollback_finalize_mail_resume_intent"] is True
    assert j["rollback_finalize_mail_pause_absent"] is False
    assert j["rollback_finalize_writers_repaused"] is True
    assert j["abandoned"] is False
    assert j["rollback_finalize_intent"] is not None
    assert world.mail_pause is True and world.mirror_pause is True


# --------------------------------------------------------------------------- #
# F3: lock every alternate entrypoint
# --------------------------------------------------------------------------- #

def _expect_locked(callable_fn) -> CutoverError:
    with pytest.raises(CutoverError) as ei:
        callable_fn()
    return ei.value


def test_alternate_entrypoints_locked_after_verified_rollback(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    pre_path, old_fp, new_fp = _rollback(world, prod, reports, root)
    backup, staging = _paths(root)

    # apply_stage (any normal stage) refused.
    _expect_locked(lambda: apply_stage(
        _opts(world, prod, reports, world.fingerprint(prod),
              stage=CutoverStage.READONLY_SMOKE, apply=True,
              backup=backup, staging=staging)))
    # A SECOND physical rollback must never exchange again.
    err = _expect_locked(lambda: attempt_rollback_before_writers(
        _opts(world, prod, reports, world.fingerprint(prod),
              stage=CutoverStage.ATOMIC_SWAP, apply=True, approve_swap=True,
              backup=backup, staging=staging),
        pre_cutover_path=pre_path,
        expected_old_fingerprint=old_fp, expected_new_fingerprint=new_fp))
    assert "second rollback refused" in str(err).lower()
    # Original still restored; candidate still retained (no re-exchange).
    assert world.files[str(prod)] == b"SYNTHETIC-PROD-DB-v1"
    assert world.fingerprint(pre_path) == new_fp
    # abort_before_swap refused.
    _expect_locked(lambda: abort_before_swap(
        _opts(world, prod, reports, world.fingerprint(prod),
              stage=CutoverStage.ATOMIC_SWAP, apply=True)))


def test_alternate_entrypoints_locked_after_abandoned(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    pre_path, old_fp, new_fp = _rollback(world, prod, reports, root)
    rollback_finalize(_finalize_opts(world, prod, reports))
    backup, staging = _paths(root)
    err = _expect_locked(lambda: apply_stage(
        _opts(world, prod, reports, world.fingerprint(prod),
              stage=CutoverStage.READONLY_SMOKE, apply=True,
              backup=backup, staging=staging)))
    assert "abandoned" in str(err).lower()
    _expect_locked(lambda: attempt_rollback_before_writers(
        _opts(world, prod, reports, world.fingerprint(prod),
              stage=CutoverStage.ATOMIC_SWAP, apply=True, approve_swap=True,
              backup=backup, staging=staging),
        pre_cutover_path=pre_path,
        expected_old_fingerprint=old_fp, expected_new_fingerprint=new_fp))


# --------------------------------------------------------------------------- #
# F4: terminal consistency + proof schema
# --------------------------------------------------------------------------- #

def test_mixed_terminal_abandoned_with_completed_is_manual_recovery(
    tmp_path: Path,
) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    # Corrupt into a contradictory terminal record.
    _edit_journal(world, prod, abandoned=True,
                  stage=CutoverStage.COMPLETED.value)
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual_recovery_required" in str(err).lower()
    j = _journal(world, prod)
    assert j["stage"] != CutoverStage.ABANDONED.value


def test_finalize_refuses_proof_missing_plan_identity(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    j = _journal(world, prod)
    proof = dict(j["rollback_proof"])
    proof["plan_production_device"] = None
    proof["plan_production_inode"] = None
    _edit_journal(world, prod, rollback_proof=proof)
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual reconciliation" in (str(err) + str(err.recovery or "")).lower()


def test_finalize_refuses_without_verified_rollback(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _run_through(world, prod, reports, fp, root,
                 stop_before=CutoverStage.READONLY_SMOKE)
    world.services.api_active = False
    world.services.health_timer_active = False
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "rollback" in str(err).lower()


def test_finalize_refuses_on_completed(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _run_through(world, prod, reports, fp, root)
    j = _journal(world, prod)
    assert j["stage"] == CutoverStage.COMPLETED.value
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "completed" in str(err).lower()


# --------------------------------------------------------------------------- #
# F5: sidecar lifecycle across physical rollback
# --------------------------------------------------------------------------- #

def test_candidate_sidecar_appeared_before_api_is_manual_recovery(
    tmp_path: Path,
) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    # A candidate-created WAL appears at production after rollback (proof said
    # absent). Must fail closed, never be silently paired with the original.
    world.materialize_companion(prod, "wal", size=0, mode=0o644,
                                inode=99991, device=7)
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual_recovery_required" in str(err).lower()
    # Never deleted/chmod'd to force a pass.
    assert world.path_exists(Path(str(prod) + "-wal"))
    j = _journal(world, prod)
    assert j["abandoned"] is False


def test_sidecar_disappeared_is_manual_recovery(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    # Proof recorded a present WAL; reality has none -> unexplained disappearance.
    j = _journal(world, prod)
    proof = dict(j["rollback_sidecar_proof"])
    proof["wal"] = {"present": True, "device": 7, "inode": 5555, "mode": 0o644}
    _edit_journal(world, prod, rollback_sidecar_proof=proof)
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual_recovery_required" in str(err).lower()


def test_sidecar_identity_drift_is_manual_recovery(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    # Proof present WAL identity X; live WAL identity Y.
    world.materialize_companion(prod, "wal", size=0, mode=0o644,
                                inode=1234, device=7)
    j = _journal(world, prod)
    proof = dict(j["rollback_sidecar_proof"])
    proof["wal"] = {"present": True, "device": 7, "inode": 9999, "mode": 0o644}
    _edit_journal(world, prod, rollback_sidecar_proof=proof)
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual_recovery_required" in str(err).lower()


def test_legit_original_companion_survives_and_finalizes(tmp_path: Path) -> None:
    # An original WAL companion present through barrier/rollback is a legit
    # original companion and rollback-finalize completes.
    world, prod, reports, root, fp = _world(tmp_path)
    # Materialize an original WAL before the cutover so the barrier captures it.
    world.materialize_companion(prod, "wal", size=0, mode=0o644,
                                inode=4243, device=7)
    _rollback(world, prod, reports, root)
    report = rollback_finalize(_finalize_opts(world, prod, reports))
    assert report["stage"] == CutoverStage.ABANDONED.value
    assert report["abandoned"] is True


# --------------------------------------------------------------------------- #
# F6: API and health-timer ownership + pre-maintenance policy
# --------------------------------------------------------------------------- #

def test_pre_maintenance_inactive_api_ends_inactive(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    # Pretend the API + timer were disabled before maintenance.
    _edit_journal(world, prod, pre_maintenance_api_active=False,
                  pre_maintenance_health_timer_active=False)
    report = rollback_finalize(_finalize_opts(world, prod, reports))
    assert report["stage"] == CutoverStage.ABANDONED.value
    # Health was verified with an owned temporary API, then stopped again.
    assert world.services.api_active is False
    assert world.services.health_timer_active is False


def test_unowned_active_api_is_manual_recovery(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    # An API is running that this MID does not own.
    world.services.api_active = True
    _edit_journal(world, prod, smoke_started_api=False,
                  rollback_finalize_started_api=False,
                  rollback_finalize_owned_temp_api=False)
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual_recovery_required" in str(err).lower()
    j = _journal(world, prod)
    assert j["abandoned"] is False


def test_missing_pre_maintenance_capture_is_manual_recovery(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    _edit_journal(world, prod, pre_maintenance_api_active=None,
                  pre_maintenance_health_timer_active=None)
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual_recovery_required" in str(err).lower()


# --------------------------------------------------------------------------- #
# F7: manual-recovery truthfulness
# --------------------------------------------------------------------------- #

def test_manual_recovery_evidence_is_truthful_and_sanitized(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    world.quick_check_fail = True
    with pytest.raises(CutoverError) as ei:
        rollback_finalize(_finalize_opts(world, prod, reports))
    err = ei.value
    ev = err.evidence or {}
    assert ev.get("repaused") is True
    assert ev.get("writers_quiesced") is True
    assert "reason" in ev
    blob = str(err) + str(err.recovery or "") + str(ev)
    assert str(prod) not in blob
    assert str(root) not in blob


def test_smoke_api_stop_failure_manual_recovery(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    _edit_journal(world, prod, smoke_started_api=True)
    world.services.api_active = True
    world.refuse_stop_api = True
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual_recovery_required" in str(err).lower()
    j = _journal(world, prod)
    assert j["abandoned"] is False
    # Ownership retained (not cleared on an unverified stop).
    assert j["smoke_started_api"] is True


# --------------------------------------------------------------------------- #
# F8: approval and CLI contract
# --------------------------------------------------------------------------- #

def test_finalize_requires_apply(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    err = _expect_locked(lambda: rollback_finalize(
        _finalize_opts(world, prod, reports, apply=False)))
    assert "--apply" in str(err)


def test_finalize_requires_head_sha_match(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    wrong = "0" * 40
    err = _expect_locked(lambda: rollback_finalize(
        _finalize_opts(world, prod, reports, main_sha=wrong)))
    assert "sha" in str(err).lower()


def test_finalize_requires_approve_swap(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    err = _expect_locked(lambda: rollback_finalize(
        _finalize_opts(world, prod, reports, approve_swap=False)))
    assert "approve-swap" in str(err).lower() or "approve_swap" in str(err).lower()


def test_july19_mid_rejected_for_finalize(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    err = _expect_locked(lambda: rollback_finalize(
        _finalize_opts(world, prod, reports, maintenance_id=ABANDONED_INCIDENT_MID)))
    assert "abandoned" in str(err).lower()


def test_july19_mid_rejected_for_apply(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    with pytest.raises(CutoverError):
        apply_stage(_opts(world, prod, reports, fp,
                          stage=CutoverStage.PAUSE_WRITERS, apply=True,
                          maintenance_id=ABANDONED_INCIDENT_MID))


def test_cli_help_text_and_mutual_exclusion() -> None:
    import importlib.util

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "orchestrate_cli",
        root / "scripts" / "maintenance" / "orchestrate_sqlite_production_cutover.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    help_text = mod.build_parser().format_help()
    assert "--execute" not in help_text
    assert "--resume" not in help_text
    # Conflicting operations -> arg-level rejection (exit 2), no side effects.
    rc = mod.main([
        "--rollback-finalize", "--abort-before-swap", "--apply",
        "--maintenance-id", MID, "--expected-main-sha", MAIN_SHA,
    ])
    assert rc == 2
    # Non-default --stage combined with rollback-finalize -> exit 2.
    rc2 = mod.main([
        "--rollback-finalize", "--stage", "atomic_swap", "--apply",
        "--maintenance-id", MID, "--expected-main-sha", MAIN_SHA,
    ])
    assert rc2 == 2


# --------------------------------------------------------------------------- #
# F9: crash / journal-failure injection matrix
# --------------------------------------------------------------------------- #

# Boundaries that fire on the default (API+timer active pre-maintenance) path.
FINALIZE_BOUNDARIES = [
    "rollback_finalize_intent",
    "rollback_finalize_before_pause_touch",
    "rollback_finalize_after_pause_touch",
    "rollback_finalize_pauses_reasserted",
    "rollback_finalize_sidecars_verified",
    "rollback_finalize_restore_intent",
    "rollback_finalize_permissions_restored",
    "rollback_finalize_services_reconciled",
    "rollback_finalize_api_start_intent",
    "rollback_finalize_api_started",
    "rollback_finalize_health_verified",
    "rollback_finalize_sidecars_recaptured",
    "rollback_finalize_timer_start_intent",
    "rollback_finalize_timer_started",
    "rollback_finalize_service_policy_applied",
    "rollback_finalize_mail_intent",
    "rollback_finalize_mail_resumed",
    "rollback_finalize_mail_observed",
    "rollback_finalize_mirror_intent",
    "rollback_finalize_mirror_resumed",
    "rollback_finalize_mirror_observed",
    "rollback_finalize_before_terminal",
    "rollback_finalize_after_terminal_replace",
]


@pytest.mark.parametrize("phase", FINALIZE_BOUNDARIES)
def test_failure_injection_then_retry_reconciles(tmp_path: Path, phase: str) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    pre_path, old_fp, new_fp = _rollback(world, prod, reports, root)
    with pytest.raises(CutoverError):
        rollback_finalize(_finalize_opts(world, prod, reports, fail_after=phase))
    j = _journal(world, prod)
    # Invariants at every boundary before the terminal record.
    assert j["stage"] != CutoverStage.COMPLETED.value
    if phase != "rollback_finalize_after_terminal_replace":
        assert j["abandoned"] is False
    assert j["writer_resume_started"] is False
    assert j["writers_resumed"] is False
    # Original device/inode remains production; candidate retained, not production.
    assert world.path_identity(prod)["device"] == 7
    assert world.path_identity(prod)["inode"] == 4242
    assert world.fingerprint(prod) != new_fp
    assert world.path_exists(pre_path)
    assert world.fingerprint(pre_path) == new_fp
    # Retry reaches ABANDONED (or a sanitized manual recovery), never the normal path.
    try:
        report = rollback_finalize(_finalize_opts(world, prod, reports))
        assert report["stage"] == CutoverStage.ABANDONED.value
        assert report["abandoned"] is True
        assert world.mail_pause is False and world.mirror_pause is False
    except CutoverError as exc:
        assert "manual_recovery_required" in str(exc).lower()
        assert _journal(world, prod)["stage"] != CutoverStage.ABANDONED.value


def test_terminal_write_injection_blocks_normal_resume(tmp_path: Path) -> None:
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
    backup, staging = _paths(root)
    with pytest.raises(CutoverError):
        apply_stage(_opts(world, prod, reports, world.fingerprint(prod),
                          stage=CutoverStage.READONLY_SMOKE, apply=True,
                          backup=backup, staging=staging))
    report = rollback_finalize(_finalize_opts(world, prod, reports))
    assert report["stage"] == CutoverStage.ABANDONED.value


# --------------------------------------------------------------------------- #
# R1: idempotent ABANDONED must pass full proof + identity + coherence
# --------------------------------------------------------------------------- #

def _finalize_to_abandoned(world, prod, reports, root):
    _rollback(world, prod, reports, root)
    rollback_finalize(_finalize_opts(world, prod, reports))
    assert _journal(world, prod)["stage"] == CutoverStage.ABANDONED.value


def test_idempotent_abandoned_with_junk_proof_is_manual_recovery(
    tmp_path: Path,
) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _finalize_to_abandoned(world, prod, reports, root)
    # Terminal record looks coherent but the proof is junk: idempotent success
    # must NOT be returned; full typed validation runs first.
    _edit_journal(world, prod, rollback_proof={"junk": True})
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual_recovery_required" in str(err).lower()
    assert "already_finalized" not in str(err.evidence or {})


def test_idempotent_abandoned_missing_typed_proof_field_is_manual_recovery(
    tmp_path: Path,
) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _finalize_to_abandoned(world, prod, reports, root)
    j = _journal(world, prod)
    proof = dict(j["rollback_proof"])
    proof["restored_device"] = None  # wrong type
    _edit_journal(world, prod, rollback_proof=proof)
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual_recovery_required" in str(err).lower()


def test_idempotent_abandoned_with_unresolved_service_intent_is_manual_recovery(
    tmp_path: Path,
) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _finalize_to_abandoned(world, prod, reports, root)
    _edit_journal(world, prod,
                  rollback_finalize_service_intent={"action": "start_api"})
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual_recovery_required" in str(err).lower()


def test_idempotent_abandoned_missing_mirror_observation_is_manual_recovery(
    tmp_path: Path,
) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _finalize_to_abandoned(world, prod, reports, root)
    _edit_journal(world, prod, rollback_finalize_mirror_observed_ok=False)
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual_recovery_required" in str(err).lower()


def test_idempotent_abandoned_with_normal_writer_fields_is_manual_recovery(
    tmp_path: Path,
) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _finalize_to_abandoned(world, prod, reports, root)
    _edit_journal(world, prod, writers_resumed=True)
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual_recovery_required" in str(err).lower()


def test_fully_coherent_abandoned_remains_idempotent(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _finalize_to_abandoned(world, prod, reports, root)
    report = rollback_finalize(_finalize_opts(world, prod, reports))
    assert report["already_finalized"] is True
    assert report["stage"] == CutoverStage.ABANDONED.value
    assert report["cutover_succeeded"] is False


# --------------------------------------------------------------------------- #
# R2: authoritative fstat capture (real filesystem) — no pathname TOCTOU
# --------------------------------------------------------------------------- #

def _fs_adapters():
    from origenlab_email_pipeline.qa.sqlite_production_cutover import FilesystemAdapters
    return FilesystemAdapters()


def test_real_fs_snapshot_fifo_is_ambiguous_and_never_blocks(tmp_path: Path) -> None:
    import os
    import threading

    from origenlab_email_pipeline.qa.sqlite_production_cutover import (
        _sidecar_identity_snapshot,
    )

    fifo = tmp_path / "emails.sqlite-wal"
    os.mkfifo(fifo)
    result: dict = {}

    def _run():
        result["snap"] = _sidecar_identity_snapshot(_fs_adapters(), fifo)

    t = threading.Thread(target=_run)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive(), "capture blocked on a FIFO (O_NONBLOCK missing)"
    assert result["snap"] == {"present": True, "ambiguous": True}


def test_real_fs_snapshot_symlink_is_refused(tmp_path: Path) -> None:
    from origenlab_email_pipeline.qa.sqlite_production_cutover import (
        _sidecar_identity_snapshot,
    )

    target = tmp_path / "target"
    target.write_bytes(b"x")
    link = tmp_path / "emails.sqlite-shm"
    link.symlink_to(target)
    snap = _sidecar_identity_snapshot(_fs_adapters(), link)
    assert snap == {"present": True, "ambiguous": True}


def test_real_fs_snapshot_regular_uses_fstat_identity(tmp_path: Path) -> None:
    import os

    from origenlab_email_pipeline.qa.sqlite_production_cutover import (
        _sidecar_identity_snapshot,
    )

    p = tmp_path / "emails.sqlite-wal"
    p.write_bytes(b"payload")
    st = os.stat(p)
    snap = _sidecar_identity_snapshot(_fs_adapters(), p)
    assert snap["present"] is True
    assert snap["device"] == int(st.st_dev)
    assert snap["inode"] == int(st.st_ino)


def test_real_fs_authoritative_fstat_rejects_identity_replacement(
    tmp_path: Path,
) -> None:
    import os

    adapters = _fs_adapters()
    p = tmp_path / "emails.sqlite"
    p.write_bytes(b"orig")
    ident = adapters.capture_member_identity(p)
    # Correct identity: fchmod succeeds.
    adapters.chmod_verified_inode(
        p, 0o600, expected_device=ident["device"], expected_inode=ident["inode"]
    )
    # Create a DISTINCT file (guaranteed different inode while p still exists),
    # then atomically replace p with it — modeling an identity swap at the path.
    other = tmp_path / "swapped"
    other.write_bytes(b"swapped")
    assert os.stat(other).st_ino != ident["inode"]
    os.replace(other, p)
    with pytest.raises(CutoverError):
        adapters.chmod_verified_inode(
            p, 0o600, expected_device=ident["device"], expected_inode=ident["inode"]
        )


def test_real_fs_authoritative_fstat_rejects_symlink_at_path(tmp_path: Path) -> None:
    adapters = _fs_adapters()
    target = tmp_path / "target"
    target.write_bytes(b"x")
    link = tmp_path / "emails.sqlite"
    link.symlink_to(target)
    with pytest.raises(CutoverError):
        adapters.capture_member_identity(link)


# --------------------------------------------------------------------------- #
# R3: typed rollback-sidecar proof + barrier_applied membership
# --------------------------------------------------------------------------- #

def test_missing_sidecar_proof_fails_closed_even_when_absent(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    _edit_journal(world, prod, rollback_sidecar_proof=None)
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual_recovery_required" in str(err).lower()
    assert _journal(world, prod)["abandoned"] is False


def test_malformed_sidecar_proof_is_manual_recovery(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    _edit_journal(world, prod, rollback_sidecar_proof={
        "schema": 1, "captured_at_utc": "z",
        "wal": {"present": True},  # present but no device/inode
        "shm": {"present": False},
    })
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual_recovery_required" in str(err).lower()


def test_present_sidecar_without_barrier_applied_is_manual_recovery(
    tmp_path: Path,
) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    world.materialize_companion(prod, "wal", size=0, mode=0o644,
                                inode=4243, device=7)
    _rollback(world, prod, reports, root)
    j = _journal(world, prod)
    art = dict(j["artifact_permissions"])
    wal = dict(art["wal"])
    wal["barrier_applied"] = False
    art["wal"] = wal
    _edit_journal(world, prod, artifact_permissions=art)
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual_recovery_required" in str(err).lower()
    # The unexplained/not-barriered sidecar is never deleted or chmod'd.
    assert world.path_exists(Path(str(prod) + "-wal"))


def test_non_regular_sidecar_capture_is_ambiguous_manual_recovery(
    tmp_path: Path,
) -> None:
    # Synthetic analog of a FIFO/symlink sidecar: capture fails -> ambiguous.
    world, prod, reports, root, fp = _world(tmp_path)
    world.materialize_companion(prod, "wal", size=0, mode=0o644,
                                inode=4243, device=7)
    _rollback(world, prod, reports, root)
    world.capture_raise_paths = {str(Path(str(prod) + "-wal"))}
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual_recovery_required" in str(err).lower()


# --------------------------------------------------------------------------- #
# R4: resume intent-before / verified-success-after
# --------------------------------------------------------------------------- #

def test_mail_unlink_failure_before_mutation_never_claims_resume(
    tmp_path: Path,
) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    world.refuse_unlink_mail = True
    with pytest.raises(OSError):
        rollback_finalize(_finalize_opts(world, prod, reports))
    j = _journal(world, prod)
    assert j["rollback_finalize_mail_resume_intent"] is True
    assert j["rollback_finalize_mail_resumed"] is False
    assert world.mail_pause is True
    # Recover: allow the unlink and retry -> ABANDONED.
    world.refuse_unlink_mail = False
    report = rollback_finalize(_finalize_opts(world, prod, reports))
    assert report["stage"] == CutoverStage.ABANDONED.value


def test_crash_after_mail_unlink_before_success_reconciles_on_retry(
    tmp_path: Path,
) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    with pytest.raises(CutoverError):
        rollback_finalize(_finalize_opts(world, prod, reports,
                          fail_after="rollback_finalize_mail_unlinked"))
    j = _journal(world, prod)
    assert j["rollback_finalize_mail_resume_intent"] is True
    assert j["rollback_finalize_mail_resumed"] is False
    assert world.mail_pause is False  # unlink already happened
    report = rollback_finalize(_finalize_opts(world, prod, reports))
    assert report["stage"] == CutoverStage.ABANDONED.value


def test_mail_pause_recreated_before_verification_is_manual_recovery(
    tmp_path: Path,
) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    world.sticky_mail_pause = True  # unlink is ineffective / pause reappears
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual_recovery_required" in str(err).lower()
    j = _journal(world, prod)
    assert j["rollback_finalize_mail_resumed"] is False
    assert j["rollback_finalize_mail_resume_intent"] is True
    assert j["abandoned"] is False


def test_repause_preserves_historical_mail_resume_truth(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    # Mail resumes, then observation fails -> manual recovery re-pauses but must
    # keep historical mail_resumed True.
    world.quick_check_fail = True
    with pytest.raises(CutoverError):
        rollback_finalize(_finalize_opts(world, prod, reports))
    j = _journal(world, prod)
    assert j["rollback_finalize_mail_resumed"] is True
    assert j["rollback_finalize_mail_pause_absent"] is False
    assert j["rollback_finalize_writers_repaused"] is True


# --------------------------------------------------------------------------- #
# R5: service ownership ordering + durable timer truth
# --------------------------------------------------------------------------- #

def test_owned_active_api_with_readonly_fd_reconciled_before_quiescence(
    tmp_path: Path,
) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    # An OWNED API is active on retry and holds a real read-only FD on the DB.
    world.services.api_active = True
    _edit_journal(world, prod, rollback_finalize_started_api=True)
    world.api_fd_hits = [{
        "pid": 999999, "access": "read_only", "basename": prod.name,
    }]
    # New ordering stops the owned API before the FD-quiescence gate, so the
    # read-only FD disappears and finalize still reaches ABANDONED.
    report = rollback_finalize(_finalize_opts(world, prod, reports))
    assert report["stage"] == CutoverStage.ABANDONED.value
    assert world.services.api_active is True  # want_api True (restarted)


def test_durable_timer_stop_intent_recorded_then_retry(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    # Force the timer active at finalize so the durable stop-intent path runs.
    world.services.health_timer_active = True
    with pytest.raises(CutoverError):
        rollback_finalize(_finalize_opts(world, prod, reports,
                          fail_after="rollback_finalize_timer_stop_intent"))
    j = _journal(world, prod)
    assert j["rollback_finalize_service_intent"] == {"action": "stop_timer"}
    assert j["abandoned"] is False
    report = rollback_finalize(_finalize_opts(world, prod, reports))
    assert report["stage"] == CutoverStage.ABANDONED.value


def test_manual_recovery_stops_owned_temp_api(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    # Pre-maintenance said API should end inactive (owned temp start), then a
    # later failure triggers manual recovery which must stop the owned temp API.
    _edit_journal(world, prod, pre_maintenance_api_active=False)
    # Fail after recapture so the owned temp API is live at manual recovery.
    world.quick_check_fail = True  # forces mail-observe failure post temp start
    with pytest.raises(CutoverError):
        rollback_finalize(_finalize_opts(world, prod, reports))
    j = _journal(world, prod)
    # The owned temp API was stopped and ownership cleared by manual recovery.
    assert j["rollback_finalize_owned_temp_api"] is False
    assert world.services.api_active is False


# --------------------------------------------------------------------------- #
# R6: real atomic-journal write fault injection
# --------------------------------------------------------------------------- #

ATOMIC_PHASES = [
    "before_temp_write",
    "after_temp_fsync",
    "before_replace",
    "after_replace_before_dir_fsync",
    "dir_fsync_failure",
]


@pytest.mark.parametrize("phase", ATOMIC_PHASES)
def test_atomic_journal_write_fault_at_finalize_intent(
    tmp_path: Path, phase: str
) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    pre_path, old_fp, new_fp = _rollback(world, prod, reports, root)
    # Fail the atomic write that persists the finalize intent, at each phase.
    world.journal_write_fault = ('"action": "finalize_abandoned"', phase)
    with pytest.raises(OSError):
        rollback_finalize(_finalize_opts(world, prod, reports))
    j = _journal(world, prod)
    applied = phase in ("after_replace_before_dir_fsync", "dir_fsync_failure")
    # Applied phases persisted the intent; pre-replace phases did not.
    assert (j.get("rollback_finalize_intent") is not None) == applied
    # Every boundary keeps the safety invariants.
    assert j["abandoned"] is False
    assert j["stage"] != CutoverStage.COMPLETED.value
    assert world.path_identity(prod)["device"] == 7
    assert world.path_identity(prod)["inode"] == 4242
    assert world.fingerprint(prod) != new_fp
    assert world.path_exists(pre_path)
    assert world.fingerprint(pre_path) == new_fp
    # One-shot fault cleared: retry reconciles to ABANDONED.
    report = rollback_finalize(_finalize_opts(world, prod, reports))
    assert report["stage"] == CutoverStage.ABANDONED.value


@pytest.mark.parametrize("phase", ATOMIC_PHASES)
def test_atomic_journal_write_fault_at_terminal(tmp_path: Path, phase: str) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    pre_path, old_fp, new_fp = _rollback(world, prod, reports, root)
    # Fail the terminal write (the one that sets stage=abandoned).
    world.journal_write_fault = ('"stage": "abandoned"', phase)
    with pytest.raises(OSError):
        rollback_finalize(_finalize_opts(world, prod, reports))
    applied = phase in ("after_replace_before_dir_fsync", "dir_fsync_failure")
    j = _journal(world, prod)
    # If the replace happened, the terminal record is durable already.
    assert (j["stage"] == CutoverStage.ABANDONED.value) == applied
    # A normal cutover stage can never resume from here.
    backup, staging = _paths(root)
    with pytest.raises(CutoverError):
        apply_stage(_opts(world, prod, reports, world.fingerprint(prod),
                          stage=CutoverStage.READONLY_SMOKE, apply=True,
                          backup=backup, staging=staging))
    # Original identity + candidate retained regardless.
    assert world.path_identity(prod)["inode"] == 4242
    assert world.fingerprint(pre_path) == new_fp
    report = rollback_finalize(_finalize_opts(world, prod, reports))
    assert report["stage"] == CutoverStage.ABANDONED.value
    assert report["abandoned"] is True


def test_crash_after_api_open_before_recapture_fails_closed_on_retry(
    tmp_path: Path,
) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    # Crash right after health verification (API open) but before recapture.
    with pytest.raises(CutoverError):
        rollback_finalize(_finalize_opts(world, prod, reports,
                          fail_after="rollback_finalize_health_verified"))
    # The API created a WAL that was never folded into the artifact.
    world.materialize_companion(prod, "wal", size=8, mode=0o644,
                                inode=787878, device=7)
    # Retry: the pre-API sidecar check sees an unexplained sidecar -> fail closed.
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual_recovery_required" in str(err).lower()
    assert _journal(world, prod)["abandoned"] is False
    assert world.path_exists(Path(str(prod) + "-wal"))
