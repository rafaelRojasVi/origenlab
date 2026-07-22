"""PR-D: maintenance boot-policy (persistent systemd enablement suppression)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from origenlab_email_pipeline.qa.sqlite_cutover_maintenance_boot_policy import (
    BOOT_POLICY_API_UNIT,
    BOOT_POLICY_TIMER_UNIT,
    classify_unit_enablement,
    sanitized_boot_policy_status,
)
from origenlab_email_pipeline.qa.sqlite_production_cutover import (
    API_HEALTH_TIMER,
    API_SERVICE,
    CutoverError,
    CutoverFailureCategory,
    CutoverOptions,
    CutoverStage,
    FilesystemAdapters,
    SyntheticWorld,
    abort_before_swap,
    apply_stage,
    attempt_rollback_before_writers,
    rollback_finalize,
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
        journal_path=prod.parent
        / ".origenlab_cutover_journals"
        / f"{MID}.journal.json",
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


def _journal_path(prod: Path) -> Path:
    return prod.parent / ".origenlab_cutover_journals" / f"{MID}.journal.json"


def _journal(world: SyntheticWorld, prod: Path) -> dict[str, Any]:
    return json.loads(world.files[str(_journal_path(prod))])


def _edit_journal(world: SyntheticWorld, prod: Path, **changes: Any) -> None:
    jp = _journal_path(prod)
    data = json.loads(world.files[str(jp)])
    data.update(changes)
    world.files[str(jp)] = json.dumps(data).encode()


def _pause(world: SyntheticWorld, prod: Path, reports: Path, fp: str, root: Path) -> str:
    backup, staging = _paths(root)
    report = apply_stage(
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
    return report["journal"]["production_fingerprint"] or fp


def _stop_readers(
    world: SyntheticWorld,
    prod: Path,
    reports: Path,
    fp: str,
    root: Path,
    *,
    fail_after: str | None = None,
) -> dict[str, Any]:
    backup, staging = _paths(root)
    return apply_stage(
        _opts(
            world,
            prod,
            reports,
            fp,
            stage=CutoverStage.STOP_READERS,
            apply=True,
            backup=backup,
            staging=staging,
            fail_after=fail_after,
        )
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
        report = apply_stage(
            _opts(
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
        )
        live_fp = report["journal"]["production_fingerprint"] or live_fp
        if stage == CutoverStage.QUIESCE_WAL:
            live_fp = world.fingerprint(prod)
    return live_fp


# --- Classifier matrix --------------------------------------------------------


@pytest.mark.parametrize(
    ("returncode", "text", "enabled", "disabled", "ambiguous"),
    [
        (0, "enabled", True, False, False),
        (1, "disabled", False, True, False),
        (3, "disabled", False, True, False),
        (0, "disabled", False, False, True),  # inconsistent
        (1, "enabled", False, False, True),  # inconsistent
        (127, "enabled", False, False, True),
        (127, "disabled", False, False, True),
        (0, "static", False, False, True),
        (1, "masked", False, False, True),
        (0, "indirect", False, False, True),
        (0, "generated", False, False, True),
        (0, "enabled-runtime", False, False, True),
        (0, "", False, False, True),
        (1, None, False, False, True),
        (0, "ENABLED", True, False, False),  # case-normalized
        (2, "activating", False, False, True),
    ],
)
def test_enablement_classifier_matrix(
    returncode: int,
    text: str | None,
    enabled: bool,
    disabled: bool,
    ambiguous: bool,
) -> None:
    result = classify_unit_enablement(returncode=returncode, is_enabled_text=text)
    assert result["enabled"] is enabled
    assert result["disabled"] is disabled
    assert result["ambiguous"] is ambiguous
    # Never treat "nonzero means disabled" as a binary rule without exact text.
    if returncode != 0 and (text or "").strip().lower() != "disabled":
        assert result["disabled"] is False


def test_sanitized_boot_policy_status_has_no_paths() -> None:
    status = sanitized_boot_policy_status(
        api_enabled=True,
        timer_enabled=False,
        intent={
            "action": "suppress",
            "phase": "active",
            "maintenance_id": MID,
            "started_at_utc": "2026-07-21T00:00:00Z",
            "api_was_enabled": True,
            "timer_was_enabled": False,
        },
        active=True,
        restored=False,
    )
    blob = json.dumps(status)
    assert "/home/" not in blob
    assert "systemctl" not in blob
    assert status["pre_maintenance_api_enabled"] is True
    assert status["pre_maintenance_health_timer_enabled"] is False
    assert status["maintenance_boot_policy_active"] is True


# --- Command-vector adapter tests ---------------------------------------------


def test_filesystem_adapter_enablement_command_vectors() -> None:
    calls: list[list[str]] = []

    def systemctl(args: list[str]) -> int:
        calls.append(list(args))
        return 0

    def systemctl_text(args: list[str]) -> tuple[int, str]:
        calls.append(list(args))
        if args[-1:] == [API_SERVICE] or args[-1:] == [API_HEALTH_TIMER]:
            if "is-enabled" in args:
                return 0, "enabled"
        return 0, "active"

    adapters = FilesystemAdapters(systemctl=systemctl, systemctl_text=systemctl_text)
    assert adapters.unit_enablement(API_SERVICE)["enabled"] is True
    adapters.disable_unit(API_HEALTH_TIMER)
    adapters.enable_unit(API_SERVICE)
    assert ["--user", "is-enabled", API_SERVICE] in calls
    assert ["--user", "disable", API_HEALTH_TIMER] in calls
    assert ["--user", "enable", API_SERVICE] in calls


def test_filesystem_adapter_refuses_unrelated_unit() -> None:
    adapters = FilesystemAdapters(
        systemctl=lambda _a: 0,
        systemctl_text=lambda _a: (0, "enabled"),
    )
    with pytest.raises(CutoverError) as exc:
        adapters.disable_unit("sshd.service")
    assert exc.value.category == CutoverFailureCategory.SAFETY


# --- Capture / suppress / restore scenarios -----------------------------------


def test_both_initially_enabled(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    world.api_enabled = True
    world.health_timer_enabled = True
    fp = _pause(world, prod, reports, fp, root)
    report = _stop_readers(world, prod, reports, fp, root)
    j = report["journal"]
    assert j["pre_maintenance_api_enabled"] is True
    assert j["pre_maintenance_health_timer_enabled"] is True
    assert j["maintenance_boot_policy_active"] is True
    assert world.api_enabled is False
    assert world.health_timer_enabled is False
    assert world.services.api_active is False
    assert world.services.health_timer_active is False
    # Timer disabled before API in call order.
    disables = [c for c in world.systemctl_calls if c[1] == "disable"]
    assert disables[0][-1] == API_HEALTH_TIMER
    assert disables[1][-1] == API_SERVICE


def test_both_initially_disabled(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    world.api_enabled = False
    world.health_timer_enabled = False
    fp = _pause(world, prod, reports, fp, root)
    report = _stop_readers(world, prod, reports, fp, root)
    j = report["journal"]
    assert j["pre_maintenance_api_enabled"] is False
    assert j["pre_maintenance_health_timer_enabled"] is False
    assert j["maintenance_boot_policy_active"] is True
    assert world.api_enabled is False
    assert world.health_timer_enabled is False
    assert not any(c[1] == "disable" for c in world.systemctl_calls)


def test_mixed_policies(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    world.api_enabled = True
    world.health_timer_enabled = False
    fp = _pause(world, prod, reports, fp, root)
    report = _stop_readers(world, prod, reports, fp, root)
    j = report["journal"]
    assert j["pre_maintenance_api_enabled"] is True
    assert j["pre_maintenance_health_timer_enabled"] is False
    assert world.api_enabled is False
    assert world.health_timer_enabled is False
    disables = [c for c in world.systemctl_calls if c[1] == "disable"]
    assert disables == [["--user", "disable", API_SERVICE]]


@pytest.mark.parametrize(
    "fail_after",
    [
        "boot_policy_intent",
        "boot_policy_timer_disabled",
        "boot_policy_api_disabled",
    ],
)
def test_crash_after_suppress_phases_reconciles(tmp_path: Path, fail_after: str) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    fp = _pause(world, prod, reports, fp, root)
    with pytest.raises(CutoverError):
        _stop_readers(world, prod, reports, fp, root, fail_after=fail_after)
    j = _journal(world, prod)
    assert j["maintenance_boot_policy_intent"]["action"] == "suppress"
    assert j["maintenance_boot_policy_active"] is False
    # Retry continues without overwriting capture.
    prior_ts = j["maintenance_boot_policy_intent"]["started_at_utc"]
    prior_api = j["pre_maintenance_api_enabled"]
    report = _stop_readers(world, prod, reports, fp, root)
    j2 = report["journal"]
    assert j2["maintenance_boot_policy_active"] is True
    assert j2["maintenance_boot_policy_intent"]["started_at_utc"] == prior_ts
    assert j2["pre_maintenance_api_enabled"] is prior_api
    assert world.api_enabled is False
    assert world.health_timer_enabled is False


def test_malformed_intent_causes_zero_enablement_mutation(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    fp = _pause(world, prod, reports, fp, root)
    with pytest.raises(CutoverError):
        _stop_readers(
            world, prod, reports, fp, root, fail_after="boot_policy_intent"
        )
    _edit_journal(
        world,
        prod,
        maintenance_boot_policy_intent={
            "action": "suppress",
            "maintenance_id": "wrong-mid",
            "started_at_utc": "2026-07-21T00:00:00Z",
            "api_was_enabled": True,
            "timer_was_enabled": True,
            "phase": "capture",
        },
    )
    world.systemctl_calls.clear()
    before_api = world.api_enabled
    before_timer = world.health_timer_enabled
    with pytest.raises(CutoverError) as exc:
        _stop_readers(world, prod, reports, fp, root)
    assert "malformed" in str(exc.value).lower() or "mid" in str(exc.value).lower()
    assert world.api_enabled is before_api
    assert world.health_timer_enabled is before_timer
    assert not any(c[1] in {"disable", "enable"} for c in world.systemctl_calls)


def test_ambiguous_enablement_probe_stops_without_mutation(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    world.unit_enablement_override = {
        API_HEALTH_TIMER: {"returncode": 0, "is_enabled_text": "static"},
    }
    fp = _pause(world, prod, reports, fp, root)
    with pytest.raises(CutoverError) as exc:
        _stop_readers(world, prod, reports, fp, root)
    assert exc.value.category == CutoverFailureCategory.SAFETY
    assert world.api_enabled is True
    assert world.health_timer_enabled is True
    assert world.services.api_active is True


def test_simulated_wsl_restart_keeps_disabled_stopped(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    fp = _pause(world, prod, reports, fp, root)
    _stop_readers(world, prod, reports, fp, root)
    assert world.api_enabled is False
    world.simulate_user_manager_restart()
    assert world.services.api_active is False
    assert world.services.health_timer_active is False
    # Control: if enablement were still true, restart would bring them back.
    world.api_enabled = True
    world.health_timer_enabled = True
    world.simulate_user_manager_restart()
    assert world.services.api_active is True
    assert world.services.health_timer_active is True


@pytest.mark.parametrize(
    "stop_before",
    [
        CutoverStage.QUIESCE_WAL,
        CutoverStage.APPLY_OS_WRITE_BARRIER,
        CutoverStage.CREATE_CURRENT_BACKUP,
        CutoverStage.ATOMIC_SWAP,
        CutoverStage.READONLY_SMOKE,
        CutoverStage.RESUME_SERVICES,
        CutoverStage.RESUME_WRITERS_COMMIT,
    ],
)
def test_external_reenable_fails_closed_every_phase(
    tmp_path: Path, stop_before: CutoverStage
) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    live = _run_through(world, prod, reports, fp, root, stop_before=stop_before)
    # Externally re-enable the API unit during maintenance.
    world.api_enabled = True
    backup, staging = _paths(root)
    with pytest.raises(CutoverError) as exc:
        apply_stage(
            _opts(
                world,
                prod,
                reports,
                live,
                stage=stop_before,
                apply=True,
                approve_swap=stop_before
                in {CutoverStage.APPROVE_SWAP, CutoverStage.ATOMIC_SWAP},
                backup=backup,
                staging=staging,
            )
        )
    assert "boot" in str(exc.value).lower() or "suppression" in str(exc.value).lower()


def test_resume_services_starts_without_enabling(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    live = _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.RESUME_SERVICES
    )
    assert world.api_enabled is False
    assert world.health_timer_enabled is False
    backup, staging = _paths(root)
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
    assert world.services.api_active is True
    assert world.services.health_timer_active is True
    assert world.api_enabled is False
    assert world.health_timer_enabled is False
    assert not any(c[1] == "enable" for c in world.systemctl_calls)


def test_completed_restores_exact_enablement(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    world.api_enabled = True
    world.health_timer_enabled = False
    _run_through(world, prod, reports, fp, root)
    j = _journal(world, prod)
    assert j["stage"] == CutoverStage.COMPLETED.value
    assert j["maintenance_boot_policy_restored"] is True
    assert j["maintenance_boot_policy_active"] is False
    assert world.api_enabled is True
    assert world.health_timer_enabled is False


def test_abort_restores_enablement(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    world.api_enabled = True
    world.health_timer_enabled = True
    live = _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.ATOMIC_SWAP
    )
    assert world.api_enabled is False
    report = abort_before_swap(
        _opts(
            world,
            prod,
            reports,
            live,
            stage=CutoverStage.ABORT_BEFORE_SWAP
            if hasattr(CutoverStage, "ABORT_BEFORE_SWAP")
            else CutoverStage.PAUSE_WRITERS,
            apply=True,
        )
    )
    assert report["aborted_before_swap"] is True
    assert report["boot_policy"]["maintenance_boot_policy_restored"] is True
    assert world.api_enabled is True
    assert world.health_timer_enabled is True


def test_abort_before_stop_readers_skips_boot_restore(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    live = _pause(world, prod, reports, fp, root)
    assert world.api_enabled is True
    report = abort_before_swap(
        _opts(
            world,
            prod,
            reports,
            live,
            stage=CutoverStage.PAUSE_WRITERS,
            apply=True,
        )
    )
    assert report["aborted_before_swap"] is True
    assert report["boot_policy"]["maintenance_boot_policy_restored"] is None or (
        report["boot_policy"]["maintenance_boot_policy_restored"] is False
    )
    # Must not invent enable/disable mutations for a never-established policy.
    assert not any(c[1] in {"enable", "disable"} for c in world.systemctl_calls)


def test_rollback_finalize_restores_and_abandons(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    world.api_enabled = True
    world.health_timer_enabled = True
    backup, staging = _paths(root)
    live = _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.READONLY_SMOKE
    )
    j = _journal(world, prod)
    old_fp = j["approved_plan"]["production_fingerprint"]
    new_fp = j["production_fingerprint"]
    attempt_rollback_before_writers(
        _opts(
            world,
            prod,
            reports,
            live,
            stage=CutoverStage.ATOMIC_SWAP,
            apply=True,
            approve_swap=True,
            backup=backup,
            staging=staging,
        ),
        expected_old_fingerprint=old_fp,
        expected_new_fingerprint=new_fp,
    )
    assert world.api_enabled is False
    result = rollback_finalize(
        _opts(
            world,
            prod,
            reports,
            old_fp,
            stage=CutoverStage.ATOMIC_SWAP,
            apply=True,
            approve_swap=True,
            backup=backup,
            staging=staging,
        )
    )
    assert result["journal"]["abandoned"] is True
    assert result["journal"]["maintenance_boot_policy_restored"] is True
    assert world.api_enabled is True
    assert world.health_timer_enabled is True


def test_partial_restore_blocks_completed(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    live = _run_through(
        world,
        prod,
        reports,
        fp,
        root,
        stop_before=CutoverStage.COMPLETED,
    )
    with pytest.raises(CutoverError):
        apply_stage(
            _opts(
                world,
                prod,
                reports,
                live,
                stage=CutoverStage.COMPLETED,
                apply=True,
                backup=_paths(root)[0],
                staging=_paths(root)[1],
                fail_after="boot_policy_timer_restored",
            )
        )
    j = _journal(world, prod)
    assert j["stage"] != CutoverStage.COMPLETED.value
    assert j["maintenance_boot_policy_restored"] is not True
    assert j["maintenance_boot_policy_intent"]["action"] == "restore"
    # Retry finishes restoration.
    apply_stage(
        _opts(
            world,
            prod,
            reports,
            live,
            stage=CutoverStage.COMPLETED,
            apply=True,
            backup=_paths(root)[0],
            staging=_paths(root)[1],
        )
    )
    j2 = _journal(world, prod)
    assert j2["stage"] == CutoverStage.COMPLETED.value
    assert j2["maintenance_boot_policy_restored"] is True


def test_malformed_journal_booleans_block_quiesce(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    live = _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.QUIESCE_WAL
    )
    _edit_journal(world, prod, maintenance_boot_policy_active="true")
    world.systemctl_calls.clear()
    with pytest.raises(CutoverError):
        apply_stage(
            _opts(
                world,
                prod,
                reports,
                live,
                stage=CutoverStage.QUIESCE_WAL,
                apply=True,
                backup=_paths(root)[0],
                staging=_paths(root)[1],
            )
        )
    assert not any(c[1] in {"enable", "disable", "start", "stop"} for c in world.systemctl_calls)


def test_units_constants_match() -> None:
    assert BOOT_POLICY_API_UNIT == API_SERVICE
    assert BOOT_POLICY_TIMER_UNIT == API_HEALTH_TIMER
