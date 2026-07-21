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
    expected_fp: str | None = None,
) -> CutoverOptions:
    # T1 approval contract: the operator's --expected-production-fingerprint is
    # bound to the rollback proof's ORIGINAL/restored fingerprint (stable), NOT
    # the live/mutable content. Derive it from the journal proof when present so
    # a post-mail-mutation retry still approves against the original fingerprint.
    fp = expected_fp
    if fp is None:
        jp = (prod.parent / ".origenlab_cutover_journals"
              / f"{maintenance_id}.journal.json")
        try:
            data = json.loads(world.files[str(jp)])
            proof = data.get("rollback_proof")
            fp = proof.get("old_fingerprint") if isinstance(proof, dict) else None
        except (KeyError, json.JSONDecodeError):
            fp = None
    if fp is None:
        fp = world.fingerprint(prod)
    return _opts(world, prod, reports, fp,
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
    # Proven ownership requires exact-boolean flag AND live MainPID+InvocationID.
    world.services.api_active = True
    _edit_journal(
        world, prod,
        rollback_finalize_started_api=True,
        rollback_finalize_api_owner={"main_pid": 4321, "invocation_id": "inv-0"},
    )
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
        rollback_finalize(_finalize_opts(
            world, prod, reports,
            fail_after="rollback_finalize_timer_stop_intent_pre_api"))
    j = _journal(world, prod)
    assert j["rollback_finalize_service_intent"] == {
        "action": "stop_timer", "phase": "pre_api"}
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


# =========================================================================== #
# THIRD source-truth correction
# =========================================================================== #

# --------------------------------------------------------------------------- #
# T1: canonical production approval binding
# --------------------------------------------------------------------------- #

def test_t1_wrong_historical_fingerprint_refused(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    # Operator supplies an arbitrary non-empty fingerprint that is NOT the
    # rollback proof's original/restored fingerprint -> fail closed.
    err = _expect_locked(lambda: rollback_finalize(
        _finalize_opts(world, prod, reports, expected_fp="123:0:1:deadbeefdeadbeef")))
    assert "manual" in (str(err) + str(err.recovery or "")).lower()
    assert _journal(world, prod)["abandoned"] is False


def test_t1_path_binding_rejects_production_drift(tmp_path: Path) -> None:
    from origenlab_email_pipeline.qa.sqlite_production_cutover import (
        _validate_production_path_binding,
    )
    world, prod, reports, root, fp = _world(tmp_path)
    _validate_production_path_binding(
        _finalize_opts(world, prod, reports), world, prod)
    drift = prod.parent / "sub" / "emails.sqlite"
    world.files[str(drift)] = b"x"
    with pytest.raises(CutoverError):
        _validate_production_path_binding(
            _finalize_opts(world, prod, reports), world, drift)


def test_t1_wrong_journal_path_refused(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    opts = _finalize_opts(world, prod, reports)
    # Point the journal path OUTSIDE the approved cutover journals directory.
    opts.journal_path = prod.parent / "elsewhere" / f"{MID}.journal.json"
    err = _expect_locked(lambda: rollback_finalize(opts))
    assert "journal" in str(err).lower()
    assert _journal(world, prod)["abandoned"] is False


def test_t1_fresh_finalize_and_idempotency_use_original_fingerprint(
    tmp_path: Path,
) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    pre_path, old_fp, new_fp = _rollback(world, prod, reports, root)
    r1 = rollback_finalize(_finalize_opts(world, prod, reports, expected_fp=old_fp))
    assert r1["stage"] == CutoverStage.ABANDONED.value
    r2 = rollback_finalize(_finalize_opts(world, prod, reports, expected_fp=old_fp))
    assert r2["already_finalized"] is True


# --------------------------------------------------------------------------- #
# T2: typed + bound rollback / sidecar proof parsing (adversarial values)
# --------------------------------------------------------------------------- #

_DELETE = object()


def _bad_proof_is_manual(world, prod, reports, **proof_changes) -> CutoverError:
    j = _journal(world, prod)
    proof = dict(j["rollback_proof"])
    for key, value in proof_changes.items():
        if value is _DELETE:
            proof.pop(key, None)
        else:
            proof[key] = value
    _edit_journal(world, prod, rollback_proof=proof)
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual" in (str(err) + str(err.recovery or "")).lower()
    assert _journal(world, prod)["abandoned"] is False
    return err


@pytest.mark.parametrize("bad_proof", [["not", "a", "dict"], "a-string-proof", 42])
def test_t2_non_dict_rollback_proof_is_manual(tmp_path: Path, bad_proof) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    _edit_journal(world, prod, rollback_proof=bad_proof)
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual" in (str(err) + str(err.recovery or "")).lower()
    assert _journal(world, prod)["abandoned"] is False


@pytest.mark.parametrize("schema", ["1", True, 1.0, 2, None])
def test_t2_schema_must_be_exact_int_one(tmp_path: Path, schema) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    _bad_proof_is_manual(world, prod, reports, schema=schema)


def test_t2_missing_timestamp_is_manual(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    _bad_proof_is_manual(world, prod, reports, verified_at_utc=_DELETE)


def test_t2_wrong_approved_plan_identity_is_manual(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    _bad_proof_is_manual(world, prod, reports, plan_production_inode=999999)


@pytest.mark.parametrize("basename", ["../evil", "a/b", "/abs", "", ".."])
def test_t2_unsafe_candidate_basename_is_manual(tmp_path: Path, basename) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    _bad_proof_is_manual(world, prod, reports, candidate_basename=basename)


def test_t2_old_eq_new_is_manual(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    j = _journal(world, prod)
    _bad_proof_is_manual(world, prod, reports,
                         new_fingerprint=j["rollback_proof"]["old_fingerprint"])


def test_t2_before_writer_resume_must_be_exact_bool(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    _bad_proof_is_manual(world, prod, reports, before_writer_resume="yes")


@pytest.mark.parametrize("bad", [
    ["x"],
    {"schema": "1"},
    {"schema": 1},
    {"schema": 1, "captured_at_utc": "", "wal": {"present": False},
     "shm": {"present": False}},
    {"schema": 1, "captured_at_utc": "t", "wal": {"present": "yes"},
     "shm": {"present": False}},
])
def test_t2_sidecar_proof_adversarial_values_are_manual(tmp_path: Path, bad) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    _edit_journal(world, prod, rollback_sidecar_proof=bad)
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual" in (str(err) + str(err.recovery or "")).lower()
    assert _journal(world, prod)["abandoned"] is False


def test_t2_terminal_resume_success_without_intent_is_manual(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _finalize_to_abandoned(world, prod, reports, root)
    _edit_journal(world, prod, rollback_finalize_mail_resume_intent=False)
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual" in (str(err) + str(err.recovery or "")).lower()


def test_t2_terminal_unresolved_permission_intent_is_manual(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _finalize_to_abandoned(world, prod, reports, root)
    _edit_journal(world, prod, permission_intent={"action": "leftover"})
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual" in (str(err) + str(err.recovery or "")).lower()


# --------------------------------------------------------------------------- #
# T3: authoritative main-identity policy (capture_member_identity / fstat)
# --------------------------------------------------------------------------- #

def test_t3_capture_failure_after_exchange_locks_and_manual(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    _run_through(world, prod, reports, world.fingerprint(prod), root,
                 stop_before=CutoverStage.READONLY_SMOKE)
    pre_path = prod.with_name(f"{prod.name}.pre_cutover.{MID}")
    new_fp = world.fingerprint(prod)
    old_fp = world.fingerprint(pre_path)
    world.capture_raise_paths = {str(prod)}
    with pytest.raises(CutoverError):
        attempt_rollback_before_writers(
            _opts(world, prod, reports, new_fp, stage=CutoverStage.ATOMIC_SWAP,
                  apply=True, approve_swap=True, backup=backup, staging=staging),
            pre_cutover_path=pre_path,
            expected_old_fingerprint=old_fp, expected_new_fingerprint=new_fp)
    j = _journal(world, prod)
    assert j["rollback_identity_capture_failed"] is True
    assert j["rollback_verified"] is False
    err = _expect_locked(lambda: apply_stage(
        _opts(world, prod, reports, world.fingerprint(prod),
              stage=CutoverStage.READONLY_SMOKE, apply=True,
              backup=backup, staging=staging)))
    assert "capture failed" in str(err).lower() or "manual" in str(err).lower()
    world.capture_raise_paths = set()
    err2 = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual" in (str(err2) + str(err2.recovery or "")).lower()
    assert _journal(world, prod)["abandoned"] is False


def test_t3_real_fs_main_fifo_is_uncapturable(tmp_path: Path) -> None:
    import os
    import threading

    from origenlab_email_pipeline.qa.sqlite_production_cutover import (
        _authoritative_identity,
    )
    fifo = tmp_path / "emails.sqlite"
    os.mkfifo(fifo)
    result: dict = {}

    def _run():
        result["id"] = _authoritative_identity(_fs_adapters(), fifo)

    t = threading.Thread(target=_run)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive()
    assert result["id"] is None


def test_t3_real_fs_main_symlink_is_uncapturable(tmp_path: Path) -> None:
    from origenlab_email_pipeline.qa.sqlite_production_cutover import (
        _authoritative_identity,
    )
    target = tmp_path / "target"
    target.write_bytes(b"x")
    link = tmp_path / "emails.sqlite"
    link.symlink_to(target)
    assert _authoritative_identity(_fs_adapters(), link) is None


def test_t3_real_fs_main_replacement_changes_identity(tmp_path: Path) -> None:
    import os

    from origenlab_email_pipeline.qa.sqlite_production_cutover import (
        _authoritative_identity,
    )
    p = tmp_path / "emails.sqlite"
    p.write_bytes(b"orig")
    ident = _authoritative_identity(_fs_adapters(), p)
    assert ident is not None
    other = tmp_path / "swapped"
    other.write_bytes(b"swapped")
    assert os.stat(other).st_ino != ident["inode"]
    os.replace(other, p)
    ident2 = _authoritative_identity(_fs_adapters(), p)
    assert ident2 is not None
    assert ident2["inode"] != ident["inode"]


# --------------------------------------------------------------------------- #
# T4: service races
# --------------------------------------------------------------------------- #

_AMBIGUOUS_API = {"is_active_text": "reloading", "main_pid": 0,
                  "listener_present": False, "sub_state": ""}
_RUNNING_API = {"is_active_text": "active", "main_pid": 4321,
                "listener_present": True, "sub_state": "running"}


def test_t4_owned_but_ambiguous_api_is_manual_not_stopped(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    _edit_journal(world, prod, smoke_started_api=True)
    world.api_activity_override = dict(_AMBIGUOUS_API)
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual" in (str(err) + str(err.recovery or "")).lower()
    assert _journal(world, prod)["abandoned"] is False


def test_t4_unowned_active_api_is_manual_not_stopped(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    world.api_activity_override = dict(_RUNNING_API)
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual" in (str(err) + str(err.recovery or "")).lower()
    assert world.api_activity()["running"] is True
    assert _journal(world, prod)["abandoned"] is False


def test_t4_active_timer_stopped_before_api_with_durable_intent(
    tmp_path: Path,
) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    world.services.health_timer_active = True
    with pytest.raises(CutoverError):
        rollback_finalize(_finalize_opts(
            world, prod, reports,
            fail_after="rollback_finalize_timer_stopped_pre_api"))
    assert world.services.health_timer_active is False
    j = _journal(world, prod)
    assert j["rollback_finalize_service_intent"] == {
        "action": "stop_timer", "phase": "pre_api"}
    report = rollback_finalize(_finalize_opts(world, prod, reports))
    assert report["stage"] == CutoverStage.ABANDONED.value


@pytest.mark.parametrize("phase", [
    "rollback_finalize_api_start_intent",
    "rollback_finalize_timer_start_intent",
])
def test_t4_service_transition_has_durable_intent(tmp_path: Path, phase) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    with pytest.raises(CutoverError):
        rollback_finalize(_finalize_opts(world, prod, reports, fail_after=phase))
    j = _journal(world, prod)
    assert j["rollback_finalize_service_intent"] is not None
    report = rollback_finalize(_finalize_opts(world, prod, reports))
    assert report["stage"] == CutoverStage.ABANDONED.value
    assert world.services.api_active is True
    assert world.services.health_timer_active is True


def test_t4_final_service_policy_verifies_both_services(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    world.services.api_active = False
    world.services.health_timer_active = False
    _rollback(world, prod, reports, root)
    report = rollback_finalize(_finalize_opts(world, prod, reports))
    assert report["stage"] == CutoverStage.ABANDONED.value
    assert world.services.api_active is False
    assert world.services.health_timer_active is False


# --------------------------------------------------------------------------- #
# T5: partial manual-recovery truth
# --------------------------------------------------------------------------- #

def _enter_manual_via_capture_lock(world, prod) -> None:
    _edit_journal(world, prod, rollback_identity_capture_failed=True)


def test_t5_mail_touch_only_failure_records_partial(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    _enter_manual_via_capture_lock(world, prod)
    # Mail was resumed (pause removed); the re-pause touch then fails, so the
    # mail pause stays ABSENT while the mirror pause is successfully reasserted.
    # Sealed-directory observation requires the pause FILE to be absent too.
    from origenlab_email_pipeline.qa.sqlite_production_cutover import mail_pause_path
    world.mail_pause = False
    world.files.pop(str(mail_pause_path(reports)), None)
    world.refuse_touch_mail = True
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    ev = err.evidence or {}
    assert ev.get("mail_touch_failed") is True
    assert ev.get("mirror_touch_failed") is False
    assert ev.get("mail_pause_present") is False
    assert ev.get("mirror_pause_present") is True
    j = _journal(world, prod)
    assert j["rollback_finalize_mail_pause_absent"] is True
    assert j["rollback_finalize_mirror_pause_absent"] is False


def test_t5_mirror_touch_only_failure_records_partial(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    _enter_manual_via_capture_lock(world, prod)
    from origenlab_email_pipeline.qa.sqlite_production_cutover import mirror_pause_path
    world.mirror_pause = False
    world.files.pop(str(mirror_pause_path(reports)), None)
    world.refuse_touch_mirror = True
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    ev = err.evidence or {}
    assert ev.get("mirror_touch_failed") is True
    assert ev.get("mail_touch_failed") is False
    assert ev.get("mail_pause_present") is True
    assert ev.get("mirror_pause_present") is False


def test_t5_list_writers_failure_is_preserved_sanitized(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    _enter_manual_via_capture_lock(world, prod)
    world.raise_list_writers = True
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    ev = err.evidence or {}
    assert any("list_writers" in e for e in ev.get("cleanup_errors", []))
    assert ev.get("mail_pause_present") is None
    assert ev.get("writers_quiesced") is False


def test_t5_owned_temp_api_stopped_before_quiescence(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    world.services.api_active = True
    world.api_fd_hits = [{"pid": 4321, "path": str(prod), "mode": "r",
                          "cmdline": "uvicorn", "device": 7, "inode": 4242}]
    # T3: manual recovery only stops a PROVEN-owned temp API. The synthetic live
    # identity while active (no start yet, generation 0) is MainPID 4321 /
    # invocation "inv-0"; record it as the durable owner evidence.
    _edit_journal(
        world, prod,
        rollback_finalize_owned_temp_api=True,
        rollback_finalize_api_owner={"main_pid": 4321, "invocation_id": "inv-0"},
    )
    _enter_manual_via_capture_lock(world, prod)
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    ev = err.evidence or {}
    assert ev.get("temp_api_stopped") is True
    assert ev.get("writers_quiesced") is True
    assert world.services.api_active is False


# --------------------------------------------------------------------------- #
# T6: real atomic-journal writer fault matrix (real tmpdir)
# --------------------------------------------------------------------------- #

def test_t6_success_preserves_prev_and_installs_new(tmp_path: Path) -> None:
    adapters = _fs_adapters()
    p = tmp_path / "x.journal.json"
    adapters.write_text_atomic(p, "OLD")
    adapters.write_text_atomic(p, "NEW")
    assert p.read_text() == "NEW"
    assert p.with_name(p.name + ".prev").read_text() == "OLD"


def test_t6_temp_open_fault_keeps_old(tmp_path: Path, monkeypatch) -> None:
    import os as _os

    from origenlab_email_pipeline.qa import sqlite_production_cutover as mod
    adapters = _fs_adapters()
    p = tmp_path / "x.journal.json"
    adapters.write_text_atomic(p, "OLD")
    real_open = _os.open

    def fake_open(path, flags, *a, **k):
        if ".partial." in str(path):
            raise OSError(5, "injected temp open fault")
        return real_open(path, flags, *a, **k)

    monkeypatch.setattr(mod.os, "open", fake_open)
    with pytest.raises(OSError):
        adapters.write_text_atomic(p, "NEW")
    assert p.read_text() == "OLD"


def test_t6_temp_fsync_fault_keeps_old(tmp_path: Path, monkeypatch) -> None:
    from origenlab_email_pipeline.qa import sqlite_production_cutover as mod
    adapters = _fs_adapters()
    p = tmp_path / "x.journal.json"
    adapters.write_text_atomic(p, "OLD")

    def fake_fsync(fd):
        raise OSError(5, "injected temp fsync fault")

    monkeypatch.setattr(mod.os, "fsync", fake_fsync)
    with pytest.raises(OSError):
        adapters.write_text_atomic(p, "NEW")
    assert p.read_text() == "OLD"


def test_t6_prev_preservation_fault_is_best_effort(tmp_path: Path, monkeypatch) -> None:
    from origenlab_email_pipeline.qa import sqlite_production_cutover as mod
    adapters = _fs_adapters()
    p = tmp_path / "x.journal.json"
    adapters.write_text_atomic(p, "OLD")

    def fake_link(src, dst):
        raise OSError(5, "injected link fault")

    monkeypatch.setattr(mod.os, "link", fake_link)
    adapters.write_text_atomic(p, "NEW")
    assert p.read_text() == "NEW"


def test_t6_final_replace_fault_keeps_old(tmp_path: Path, monkeypatch) -> None:
    from origenlab_email_pipeline.qa import sqlite_production_cutover as mod
    adapters = _fs_adapters()
    p = tmp_path / "x.journal.json"
    adapters.write_text_atomic(p, "OLD")

    def fake_replace(src, dst):
        raise OSError(5, "injected replace fault")

    monkeypatch.setattr(mod.os, "replace", fake_replace)
    with pytest.raises(OSError):
        adapters.write_text_atomic(p, "NEW")
    assert p.read_text() == "OLD"
    prev = p.with_name(p.name + ".prev")
    if prev.exists():
        assert prev.read_text() == "OLD"


def test_t6_new_file_fsync_fault_installs_new_durability_indeterminate(
    tmp_path: Path, monkeypatch
) -> None:
    from origenlab_email_pipeline.qa import sqlite_production_cutover as mod
    adapters = _fs_adapters()
    p = tmp_path / "x.journal.json"
    adapters.write_text_atomic(p, "OLD")

    def fake_fsync_file(path):
        raise OSError(5, "injected new-file fsync fault")

    monkeypatch.setattr(mod, "fsync_file", fake_fsync_file)
    with pytest.raises(OSError):
        adapters.write_text_atomic(p, "NEW")
    assert p.read_text() == "NEW"
    assert p.with_name(p.name + ".prev").read_text() == "OLD"


def test_t6_dir_fsync_fault_installs_new_durability_indeterminate(
    tmp_path: Path, monkeypatch
) -> None:
    from origenlab_email_pipeline.qa import sqlite_production_cutover as mod
    adapters = _fs_adapters()
    p = tmp_path / "x.journal.json"
    adapters.write_text_atomic(p, "OLD")

    def fake_fsync_dir(path):
        raise OSError(5, "injected dir fsync fault")

    monkeypatch.setattr(mod, "fsync_directory", fake_fsync_dir)
    with pytest.raises(OSError):
        adapters.write_text_atomic(p, "NEW")
    assert p.read_text() == "NEW"
    assert p.with_name(p.name + ".prev").read_text() == "OLD"


# --------------------------------------------------------------------------- #
# P4-1: bind every sealed path before mutation
# --------------------------------------------------------------------------- #

def _private_path(prod: Path) -> Path:
    jp = _journal_path(prod)
    return jp.with_name(jp.name + ".private.json")


def _assert_no_mutation(world: SyntheticWorld, prod: Path, *, api: bool, timer: bool,
                        mail: bool, mirror: bool) -> None:
    assert world.services.api_active is api
    assert world.services.health_timer_active is timer
    assert world.mail_pause is mail
    assert world.mirror_pause is mirror
    assert _journal(world, prod)["abandoned"] is False


def test_p4_wrong_reports_dir_no_file_or_service_mutation(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    # After the rollback the API/timer are stopped and both pauses reasserted.
    world.services.api_active = True
    world.services.health_timer_active = True
    world.mail_pause = True
    world.mirror_pause = True
    wrong = root / "reports" / "OTHER"
    err = _expect_locked(lambda: rollback_finalize(
        _finalize_opts(world, prod, wrong)
    ))
    assert "manual_recovery_required" in str(err).lower()
    ev = err.evidence or {}
    assert ev.get("sealed_plan_problem") == "reports_dir_mismatch"
    # No file or service mutation may have occurred.
    _assert_no_mutation(world, prod, api=True, timer=True, mail=True, mirror=True)


def test_p4_missing_private_plan_is_manual(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    world.files.pop(str(_private_path(prod)), None)
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert (err.evidence or {}).get("sealed_plan_problem") == "private_plan_missing"
    assert _journal(world, prod)["abandoned"] is False


def test_p4_malformed_private_plan_is_manual(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    world.files[str(_private_path(prod))] = b"[1, 2, 3]"
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert (err.evidence or {}).get("sealed_plan_problem") == "private_plan_not_object"
    assert _journal(world, prod)["abandoned"] is False


def test_p4_private_plan_journal_drift_is_manual(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    priv = json.loads(world.files[str(_private_path(prod))])
    priv["journal_path"] = "/somewhere/else.json"
    world.files[str(_private_path(prod))] = json.dumps(priv).encode()
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert (err.evidence or {}).get("sealed_plan_problem") == (
        "private_plan_journal_path_drift"
    )


# --------------------------------------------------------------------------- #
# P4-2: remove circular/optional proof binding
# --------------------------------------------------------------------------- #

def test_p4_joint_old_restored_and_operator_fp_still_manual(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    j = _journal(world, prod)
    proof = dict(j["rollback_proof"])
    proof["old_fingerprint"] = "FORGED"
    proof["restored_fingerprint"] = "FORGED"
    _edit_journal(world, prod, rollback_proof=proof)
    # Even if the operator ALSO supplies the forged fingerprint, the proof is
    # still bound to the approved plan + rollback_intent, so it fails closed.
    err = _expect_locked(lambda: rollback_finalize(
        _finalize_opts(world, prod, reports, expected_fp="FORGED")
    ))
    assert "manual" in (str(err) + str(err.recovery or "")).lower()
    assert _journal(world, prod)["abandoned"] is False


@pytest.mark.parametrize("field", [
    "staging_basename", "pre_cutover_basename", "candidate_fingerprint",
])
def test_p4_missing_journal_binding_field_is_manual(tmp_path: Path, field: str) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    _edit_journal(world, prod, **{field: None})
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual" in (str(err) + str(err.recovery or "")).lower()
    assert _journal(world, prod)["abandoned"] is False


@pytest.mark.parametrize("value", [["a"], "a-string", 5, None])
def test_p4_approved_plan_wrong_type_is_manual(tmp_path: Path, value) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    _edit_journal(world, prod, approved_plan=value)
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual" in (str(err) + str(err.recovery or "")).lower()
    assert _journal(world, prod)["abandoned"] is False


@pytest.mark.parametrize("value", ["x", ["x"], 3, None])
def test_p4_rollback_intent_wrong_type_is_manual(tmp_path: Path, value) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    _edit_journal(world, prod, rollback_intent=value)
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual" in (str(err) + str(err.recovery or "")).lower()
    assert _journal(world, prod)["abandoned"] is False


@pytest.mark.parametrize("changes", [
    {"pre_maintenance_api_active": "false"},
    {"pre_maintenance_health_timer_active": 0},
    {"pre_maintenance_api_active": 1},
])
def test_p4_non_bool_service_policy_is_manual(tmp_path: Path, changes) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    _edit_journal(world, prod, **changes)
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual" in (str(err) + str(err.recovery or "")).lower()
    assert _journal(world, prod)["abandoned"] is False


@pytest.mark.parametrize("changes", [
    {"rollback_finalized_at_utc": ""},
    {"rollback_finalize_post_mail_fingerprint": ""},
])
def test_p4_malformed_terminal_fields_are_manual(tmp_path: Path, changes) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    rollback_finalize(_finalize_opts(world, prod, reports))
    _edit_journal(world, prod, **changes)
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual" in (str(err) + str(err.recovery or "")).lower()


# --------------------------------------------------------------------------- #
# P4-3: API ownership verified-success-after
# --------------------------------------------------------------------------- #

def test_p4_crash_after_start_intent_then_external_api_is_manual(
    tmp_path: Path,
) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    # Crash exactly at the durable start-intent (before start executes): no
    # ownership flag and no owner evidence are persisted.
    with pytest.raises(CutoverError):
        rollback_finalize(_finalize_opts(
            world, prod, reports,
            fail_after="rollback_finalize_api_start_intent",
        ))
    j = _journal(world, prod)
    assert j["rollback_finalize_started_api"] is False
    assert j.get("rollback_finalize_api_owner") is None
    # A DIFFERENT API now appears externally (different MainPID/generation).
    world.services.api_active = True
    world.api_process_identity_override = {"main_pid": 9999, "invocation_id": "ext"}
    world.api_activity_override = {
        "is_active_text": "active", "main_pid": 9999,
        "listener_present": True, "sub_state": "running",
    }
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual" in (str(err) + str(err.recovery or "")).lower()
    # The unowned external API was NOT stopped.
    assert world.services.api_active is True
    assert _journal(world, prod)["abandoned"] is False


def test_p4_owned_flag_with_ambiguous_activity_is_manual(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    world.services.api_active = True
    world.api_activity_override = {
        "is_active_text": "unknown", "main_pid": 0,
        "listener_present": False, "sub_state": None,
    }
    _edit_journal(world, prod, rollback_finalize_started_api=True)
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual" in (str(err) + str(err.recovery or "")).lower()
    # Ambiguous activity is never stopped.
    assert world.services.api_active is True
    assert _journal(world, prod)["abandoned"] is False


def test_p4_happy_path_records_verified_api_owner(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    rollback_finalize(_finalize_opts(world, prod, reports))
    j = _journal(world, prod)
    # want_api True (pre-maintenance active) so the API is reused/kept, owner
    # evidence captured after verified start.
    assert j["rollback_finalize_started_api"] is True
    owner = j.get("rollback_finalize_api_owner")
    assert isinstance(owner, dict)
    assert owner["main_pid"] == 4321
    assert owner["invocation_id"] is not None


# --------------------------------------------------------------------------- #
# P4-4: timer + final service verification fail closed
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("rc,text,expect", [
    (0, "active", "active"),
    (3, "inactive", "inactive"),
    (3, "dead", "inactive"),
    (3, "failed", "ambiguous"),
    (0, "activating", "ambiguous"),
    (0, "deactivating", "ambiguous"),
    (0, "reloading", "ambiguous"),
    (0, "", "ambiguous"),
    (127, "", "ambiguous"),
    (0, "totally-unknown", "ambiguous"),
])
def test_p4_classify_health_timer_buckets(rc, text, expect) -> None:
    from origenlab_email_pipeline.qa.sqlite_production_cutover import (
        classify_health_timer_activity,
    )
    out = classify_health_timer_activity(returncode=rc, is_active_text=text)
    assert out["state"] == expect
    assert out[expect] is True


def test_p4_systemctl_failure_127_timer_is_manual(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    world.health_timer_activity_override = {"returncode": 127, "is_active_text": ""}
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual" in (str(err) + str(err.recovery or "")).lower()
    assert _journal(world, prod)["abandoned"] is False


def test_p4_unknown_timer_state_is_manual(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    world.health_timer_activity_override = {
        "returncode": 0, "is_active_text": "reloading",
    }
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual" in (str(err) + str(err.recovery or "")).lower()
    assert _journal(world, prod)["abandoned"] is False


def test_p4_timer_ambiguous_final_state_is_manual(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    # Scripted transitions: every timer read is a clean known state until the
    # FINAL convergence read, which turns ambiguous (models a late reappearance
    # / unknown state at the end). Fail closed to manual recovery.
    world.health_timer_activity_override = [
        {"returncode": 0, "is_active_text": "inactive"},   # stop_timer(pre_api)
        {"returncode": 0, "is_active_text": "inactive"},   # stop_timer(pre_health)
        {"returncode": 0, "is_active_text": "inactive"},   # start-path classify
        {"returncode": 0, "is_active_text": "active"},     # start-path verify
        {"returncode": 3, "is_active_text": "failed"},     # final convergence
    ]
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual" in (str(err) + str(err.recovery or "")).lower()
    assert _journal(world, prod)["abandoned"] is False


# --------------------------------------------------------------------------- #
# P4-5: validate rollback proof inputs BEFORE the physical exchange
# --------------------------------------------------------------------------- #

def _drive_to_atomic_swap(world, prod, reports, root):
    _run_through(world, prod, reports, world.fingerprint(prod), root,
                 stop_before=CutoverStage.READONLY_SMOKE)
    pre_path = prod.with_name(f"{prod.name}.pre_cutover.{MID}")
    new_fp = world.fingerprint(prod)
    old_fp = world.fingerprint(pre_path)
    return pre_path, old_fp, new_fp


def test_p4_rollback_unsafe_pre_cutover_basename_refused(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    pre_path, old_fp, new_fp = _drive_to_atomic_swap(world, prod, reports, root)
    _edit_journal(world, prod, pre_cutover_basename="../evil")
    backup, staging = _paths(root)
    err = _expect_locked(lambda: attempt_rollback_before_writers(
        _opts(world, prod, reports, new_fp, stage=CutoverStage.ATOMIC_SWAP,
              apply=True, approve_swap=True, backup=backup, staging=staging),
        expected_old_fingerprint=old_fp,
        expected_new_fingerprint=new_fp,
    ))
    assert "safe single component" in str(err).lower()
    # No physical exchange occurred.
    assert world.fingerprint(prod) == new_fp


def test_p4_rollback_uncapturable_retained_original_refused(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    pre_path, old_fp, new_fp = _drive_to_atomic_swap(world, prod, reports, root)
    world.capture_raise_paths = {str(pre_path)}
    backup, staging = _paths(root)
    err = _expect_locked(lambda: attempt_rollback_before_writers(
        _opts(world, prod, reports, new_fp, stage=CutoverStage.ATOMIC_SWAP,
              apply=True, approve_swap=True, backup=backup, staging=staging),
        pre_cutover_path=pre_path,
        expected_old_fingerprint=old_fp,
        expected_new_fingerprint=new_fp,
    ))
    assert "regular capturable file" in str(err).lower()
    assert world.fingerprint(prod) == new_fp


def test_p4_rollback_operator_fp_must_bind_new_side(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    pre_path, old_fp, new_fp = _drive_to_atomic_swap(world, prod, reports, root)
    backup, staging = _paths(root)
    # Operator supplies the OLD fingerprint (wrong side) as the approval fp.
    err = _expect_locked(lambda: attempt_rollback_before_writers(
        _opts(world, prod, reports, old_fp, stage=CutoverStage.ATOMIC_SWAP,
              apply=True, approve_swap=True, backup=backup, staging=staging),
        pre_cutover_path=pre_path,
        expected_old_fingerprint=old_fp,
        expected_new_fingerprint=new_fp,
    ))
    assert "current/new production" in str(err).lower()
    assert world.fingerprint(prod) == new_fp


# --------------------------------------------------------------------------- #
# P4-6: preserve unknown manual-recovery truth
# --------------------------------------------------------------------------- #

def test_p4_list_writers_failure_marks_unknown(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    _enter_manual_via_capture_lock(world, prod)
    world.raise_list_writers = True
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    ev = err.evidence or {}
    assert ev.get("mail_pause_state") == "unknown"
    assert ev.get("mirror_pause_state") == "unknown"
    j = _journal(world, prod)
    assert j["rollback_finalize_mail_pause_known"] is False
    assert j["rollback_finalize_mirror_pause_known"] is False


def test_p4_terminal_coherence_requires_known_pause_state(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    rollback_finalize(_finalize_opts(world, prod, reports))
    # A stale/unknown pause observation can never qualify as coherent terminal.
    _edit_journal(world, prod, rollback_finalize_mail_pause_known=False)
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual" in (str(err) + str(err.recovery or "")).lower()
    assert (err.evidence or {}).get("terminal") == "mixed"


# --------------------------------------------------------------------------- #
# P5-1: proven API ownership at every stop
# --------------------------------------------------------------------------- #

def test_p5_stale_owned_flag_foreign_pid_not_stopped(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    world.services.api_active = True
    world.api_process_identity_override = {"main_pid": 7777, "invocation_id": "foreign"}
    world.api_activity_override = {
        "is_active_text": "active", "main_pid": 7777,
        "listener_present": True, "sub_state": "running",
    }
    _edit_journal(
        world, prod,
        rollback_finalize_started_api=True,
        rollback_finalize_api_owner={"main_pid": 4321, "invocation_id": "inv-0"},
    )
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual" in (str(err) + str(err.recovery or "")).lower()
    assert world.services.api_active is True  # foreign API never stopped


def test_p5_matching_pid_different_invocation_not_stopped(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    world.services.api_active = True
    world.api_process_identity_override = {"main_pid": 4321, "invocation_id": "other-gen"}
    _edit_journal(
        world, prod,
        rollback_finalize_started_api=True,
        rollback_finalize_api_owner={"main_pid": 4321, "invocation_id": "inv-0"},
    )
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual" in (str(err) + str(err.recovery or "")).lower()
    assert world.services.api_active is True


@pytest.mark.parametrize("owner", [
    {"main_pid": 4321},  # missing InvocationID
    {"main_pid": 4321, "invocation_id": ""},
    {"main_pid": 4321, "invocation_id": None},
    {"main_pid": "4321", "invocation_id": "inv-0"},  # malformed PID
    {"main_pid": 0, "invocation_id": "inv-0"},
    {"main_pid": True, "invocation_id": "inv-0"},
])
def test_p5_malformed_owner_fails_closed(tmp_path: Path, owner: dict) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    world.services.api_active = True
    _edit_journal(
        world, prod,
        rollback_finalize_started_api=True,
        rollback_finalize_api_owner=owner,
    )
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual" in (str(err) + str(err.recovery or "")).lower()
    assert world.services.api_active is True


def test_p5_late_ambiguous_before_health_does_not_start(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    # Crash after permissions restored, then inject ambiguous activity before
    # the health-check API start path.
    with pytest.raises(CutoverError):
        rollback_finalize(_finalize_opts(
            world, prod, reports,
            fail_after="rollback_finalize_permissions_restored",
        ))
    world.api_activity_override = {
        "is_active_text": "unknown", "main_pid": 0,
        "listener_present": False, "sub_state": None,
    }
    starts_before = world._api_invocation
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual" in (str(err) + str(err.recovery or "")).lower()
    assert world._api_invocation == starts_before  # no start_api


def test_p5_reconcile_stop_requires_proven_ownership(tmp_path: Path) -> None:
    """Owned flag alone (no owner evidence) must never authorize a stop."""
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    world.services.api_active = True
    _edit_journal(world, prod, rollback_finalize_started_api=True)
    # No rollback_finalize_api_owner → not proven.
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual" in (str(err) + str(err.recovery or "")).lower()
    assert world.services.api_active is True


# --------------------------------------------------------------------------- #
# P5-2: sealed reports directory authoritative for observation
# --------------------------------------------------------------------------- #

def test_p5_settings_reports_drift_sealed_controls_truth(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    # Settings reports dir differs from sealed; only sealed has pause files.
    settings_reports = root / "reports" / "SETTINGS_OTHER"
    (settings_reports / "active" / "current").mkdir(parents=True)
    world.settings_reports_dir = settings_reports
    # Without sealed binding, observation of settings would see NO pauses.
    assert world.list_writers(prod).mail_pause_present is False
    # Finalize observes the sealed directory and still reaches ABANDONED.
    report = rollback_finalize(_finalize_opts(world, prod, reports))
    assert report["stage"] == CutoverStage.ABANDONED.value


def test_p5_list_writers_fail_after_mail_unlink_is_manual(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    # Fail observation immediately after mail unlink (pause absent). Historical
    # resume intent is preserved; sealed pauses are reasserted; never ABANDONED.
    world.raise_list_writers_when_mail_absent = True
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual" in (str(err) + str(err.recovery or "")).lower()
    j = _journal(world, prod)
    assert j["abandoned"] is False
    assert j["rollback_finalize_mail_resume_intent"] is True
    assert j["rollback_finalize_mail_resumed"] is False


def test_p5_list_writers_fail_after_mirror_unlink_is_manual(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    world.raise_list_writers_when_mirror_absent = True
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual" in (str(err) + str(err.recovery or "")).lower()
    j = _journal(world, prod)
    assert j["abandoned"] is False
    assert j["rollback_finalize_mirror_resume_intent"] is True
    assert j["rollback_finalize_mirror_resumed"] is False
    # Mail already succeeded historically before the mirror observation failure.
    assert j["rollback_finalize_mail_resumed"] is True
    assert j["rollback_finalize_mail_resume_intent"] is True


# --------------------------------------------------------------------------- #
# P5-3: finish pre-exchange validation
# --------------------------------------------------------------------------- #

def test_p5_wrong_retained_inode_refuses_before_exchange(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    pre_path, old_fp, new_fp = _drive_to_atomic_swap(world, prod, reports, root)
    # Drift the retained original's inode away from the approved-plan identity.
    world.inode_overrides[str(pre_path)] = 999999
    backup, staging = _paths(root)
    err = _expect_locked(lambda: attempt_rollback_before_writers(
        _opts(world, prod, reports, new_fp, stage=CutoverStage.ATOMIC_SWAP,
              apply=True, approve_swap=True, backup=backup, staging=staging),
        pre_cutover_path=pre_path,
        expected_old_fingerprint=old_fp,
        expected_new_fingerprint=new_fp,
    ))
    assert "approved-plan" in str(err).lower() or "identity" in str(err).lower()
    assert world.fingerprint(prod) == new_fp  # no exchange
    assert _journal(world, prod).get("rollback_intent") is None


def test_p5_identity_replacement_before_exchange_refuses(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    pre_path, old_fp, new_fp = _drive_to_atomic_swap(world, prod, reports, root)
    world.capture_raise_paths = {str(pre_path)}
    backup, staging = _paths(root)
    err = _expect_locked(lambda: attempt_rollback_before_writers(
        _opts(world, prod, reports, new_fp, stage=CutoverStage.ATOMIC_SWAP,
              apply=True, approve_swap=True, backup=backup, staging=staging),
        pre_cutover_path=pre_path,
        expected_old_fingerprint=old_fp,
        expected_new_fingerprint=new_fp,
    ))
    assert "capturable" in str(err).lower()
    assert world.fingerprint(prod) == new_fp
    assert _journal(world, prod).get("rollback_intent") is None


def test_p5_api_timer_probe_failure_refuses_without_intent(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    pre_path, old_fp, new_fp = _drive_to_atomic_swap(world, prod, reports, root)
    # After atomic swap the API/timer are stopped; inject ambiguous timer.
    world.health_timer_activity_override = {
        "returncode": 127, "is_active_text": "",
    }
    backup, staging = _paths(root)
    err = _expect_locked(lambda: attempt_rollback_before_writers(
        _opts(world, prod, reports, new_fp, stage=CutoverStage.ATOMIC_SWAP,
              apply=True, approve_swap=True, backup=backup, staging=staging),
        pre_cutover_path=pre_path,
        expected_old_fingerprint=old_fp,
        expected_new_fingerprint=new_fp,
    ))
    assert "timer" in str(err).lower() or "health" in str(err).lower()
    assert world.fingerprint(prod) == new_fp
    assert _journal(world, prod).get("rollback_intent") is None


def test_p5_crash_after_rollback_intent_locks_progression(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    pre_path, old_fp, new_fp = _drive_to_atomic_swap(world, prod, reports, root)
    # Simulate durable intent written but exchange never completed.
    _edit_journal(
        world, prod,
        rollback_intent={
            "old_fingerprint": old_fp,
            "new_fingerprint": new_fp,
            "pre_cutover_basename": pre_path.name,
        },
    )
    backup, staging = _paths(root)
    # Second rollback refused.
    err = _expect_locked(lambda: attempt_rollback_before_writers(
        _opts(world, prod, reports, new_fp, stage=CutoverStage.ATOMIC_SWAP,
              apply=True, approve_swap=True, backup=backup, staging=staging),
        pre_cutover_path=pre_path,
        expected_old_fingerprint=old_fp,
        expected_new_fingerprint=new_fp,
    ))
    assert "rollback_intent" in str(err).lower() or "refused" in str(err).lower()
    # Ordinary stage refused.
    err2 = _expect_locked(lambda: apply_stage(
        _opts(world, prod, reports, new_fp, stage=CutoverStage.READONLY_SMOKE,
              apply=True, backup=backup, staging=staging)
    ))
    assert "refused" in str(err2).lower()
    # Abort refused.
    err3 = _expect_locked(lambda: abort_before_swap(
        _opts(world, prod, reports, new_fp, stage=CutoverStage.PLAN_PREFLIGHT,
              apply=True, backup=backup, staging=staging)
    ))
    assert "refused" in str(err3).lower()
    assert world.fingerprint(prod) == new_fp  # never exchanged


# --------------------------------------------------------------------------- #
# P5-4: complete proof and terminal typing
# --------------------------------------------------------------------------- #

def test_p5_wrong_fresh_stage_is_manual(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    _edit_journal(world, prod, stage=CutoverStage.READONLY_SMOKE.value)
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual" in (str(err) + str(err.recovery or "")).lower()
    assert _journal(world, prod)["abandoned"] is False


def test_p5_truncated_approved_plan_is_manual(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    j = _journal(world, prod)
    plan = j["approved_plan"]
    truncated = {
        "production_device": plan["production_device"],
        "production_inode": plan["production_inode"],
        "production_fingerprint": plan["production_fingerprint"],
    }
    _edit_journal(world, prod, approved_plan=truncated)
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual" in (str(err) + str(err.recovery or "")).lower()


@pytest.mark.parametrize("changes", [
    {"maintenance_id": "cutover20260718T999999Z"},
    {"expected_main_sha": "0" * 40},
    {"production_basename": "other.sqlite"},
])
def test_p5_wrong_plan_mid_sha_basename_is_manual(tmp_path: Path, changes) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    plan = dict(_journal(world, prod)["approved_plan"])
    plan.update(changes)
    _edit_journal(world, prod, approved_plan=plan)
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual" in (str(err) + str(err.recovery or "")).lower()


def test_p5_mismatched_intent_pre_cutover_basename_is_manual(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    intent = dict(_journal(world, prod)["rollback_intent"])
    intent["pre_cutover_basename"] = "emails.sqlite.pre_cutover.OTHER"
    _edit_journal(world, prod, rollback_intent=intent)
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual" in (str(err) + str(err.recovery or "")).lower()


@pytest.mark.parametrize("changes", [
    {"rollback_finalized_at_utc": 12345},
    {"rollback_finalized_at_utc": ["x"]},
    {"rollback_finalize_post_mail_fingerprint": 99},
    {"rollback_finalize_post_mail_fingerprint": ["fp"]},
])
def test_p5_non_string_terminal_fields_are_manual(tmp_path: Path, changes) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    rollback_finalize(_finalize_opts(world, prod, reports))
    _edit_journal(world, prod, **changes)
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual" in (str(err) + str(err.recovery or "")).lower()


@pytest.mark.parametrize("changes", [
    {"exchange_completed": "true"},
    {"old_production_retained": 1},
    {"rollback_verified": "yes"},
])
def test_p5_string_integer_journal_booleans_are_manual(tmp_path: Path, changes) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    _edit_journal(world, prod, **changes)
    err = _expect_locked(lambda: rollback_finalize(_finalize_opts(world, prod, reports)))
    assert "manual" in (str(err) + str(err.recovery or "")).lower()
    assert _journal(world, prod)["abandoned"] is False
