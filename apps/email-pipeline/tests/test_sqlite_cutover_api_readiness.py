"""Post-closure hardening: bounded API readiness wait before readonly smoke."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from origenlab_email_pipeline.qa.sqlite_production_cutover import (
    CutoverError,
    CutoverFailureCategory,
    CutoverOptions,
    CutoverStage,
    SyntheticWorld,
    apply_stage,
    attempt_rollback_before_writers,
    journal_path_for,
    wait_for_api_health_ready,
    _classify_health_ready_payload,
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
    return world, prod, reports, root, world.fingerprint(prod)


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
    ready_timeout: float = 120.0,
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
        api_ready_timeout_seconds=ready_timeout,
        api_ready_poll_seconds=0.01,
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


def _activity_running() -> dict:
    return {
        "running": True,
        "stopped": False,
        "ambiguous": False,
        "state": "running",
    }


def _activity_stopped() -> dict:
    return {
        "running": False,
        "stopped": True,
        "ambiguous": False,
        "state": "stopped",
    }


def test_classify_health_payload() -> None:
    assert _classify_health_ready_payload({"ok": True}) == "ready"
    assert _classify_health_ready_payload({"ok": False}) == "fail_not_ok"
    assert _classify_health_ready_payload([1, 2]) == "fail_malformed"
    assert _classify_health_ready_payload("x") == "fail_malformed"


def test_readiness_initially_unavailable_then_healthy() -> None:
    clock = [0.0]
    outcomes = ["retry_not_listening", "retry_not_listening", "ready"]

    def probe() -> str:
        return outcomes.pop(0)

    result = wait_for_api_health_ready(
        api_base_url="http://127.0.0.1:8001",
        api_activity=_activity_running,
        probe=probe,
        timeout_seconds=5.0,
        poll_interval_seconds=0.5,
        monotonic=lambda: clock[0],
        sleep=lambda s: clock.__setitem__(0, clock[0] + s),
    )
    assert result["ok"] is True
    assert result["attempts"] == 3
    assert result["outcome"] == "ready"


def test_readiness_healthy_immediately() -> None:
    result = wait_for_api_health_ready(
        api_base_url="http://127.0.0.1:8001",
        api_activity=_activity_running,
        probe=lambda: "ready",
        timeout_seconds=5.0,
        poll_interval_seconds=0.5,
        sleep=lambda _s: None,
    )
    assert result["ok"] is True
    assert result["attempts"] == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timeout_seconds": 0},
        {"poll_interval_seconds": 0},
        {"attempt_timeout_seconds": 0},
        {"timeout_seconds": -1},
        {"poll_interval_seconds": -0.1},
        {"attempt_timeout_seconds": -1},
    ],
)
def test_readiness_rejects_non_positive_timeouts(kwargs: dict) -> None:
    params = {
        "api_base_url": "http://127.0.0.1:8001",
        "api_activity": _activity_running,
        "probe": lambda: "ready",
        "timeout_seconds": 5.0,
        "poll_interval_seconds": 0.5,
        "attempt_timeout_seconds": 3.0,
        "sleep": lambda _s: None,
    }
    params.update(kwargs)
    with pytest.raises(CutoverError, match="timeout/poll/attempt must be positive") as exc:
        wait_for_api_health_ready(**params)
    assert exc.value.category == CutoverFailureCategory.PREFLIGHT


def test_readiness_timeout_never_reachable() -> None:
    clock = [0.0]

    with pytest.raises(CutoverError, match="timed out") as exc:
        wait_for_api_health_ready(
            api_base_url="http://127.0.0.1:8001",
            api_activity=_activity_running,
            probe=lambda: "retry_not_listening",
            timeout_seconds=1.0,
            poll_interval_seconds=0.4,
            monotonic=lambda: clock[0],
            sleep=lambda s: clock.__setitem__(0, clock[0] + s),
        )
    assert exc.value.category == CutoverFailureCategory.VERIFY
    assert exc.value.evidence.get("readiness") == "fail_timeout"
    blob = str(exc.value) + json.dumps(exc.value.evidence or {})
    assert "Bearer" not in blob
    assert "ORIGENLAB_API_AUTH_TOKEN" not in blob
    assert "/home/" not in blob


def test_readiness_process_exits_before_ready() -> None:
    state = {"n": 0}

    def activity() -> dict:
        state["n"] += 1
        if state["n"] >= 2:
            return _activity_stopped()
        return _activity_running()

    with pytest.raises(CutoverError, match="exited before HTTP readiness") as exc:
        wait_for_api_health_ready(
            api_base_url="http://127.0.0.1:8001",
            api_activity=activity,
            probe=lambda: "retry_not_listening",
            timeout_seconds=5.0,
            poll_interval_seconds=0.1,
            sleep=lambda _s: None,
        )
    assert exc.value.evidence.get("readiness") == "fail_process_exited"


@pytest.mark.parametrize(
    "outcome,match",
    [
        ("fail_http", "non-success HTTP"),
        ("fail_malformed", "malformed JSON"),
        ("fail_not_ok", "ok=true"),
    ],
)
def test_readiness_hard_probe_failures(outcome: str, match: str) -> None:
    with pytest.raises(CutoverError, match=match) as exc:
        wait_for_api_health_ready(
            api_base_url="http://127.0.0.1:8001",
            api_activity=_activity_running,
            probe=lambda: outcome,
            timeout_seconds=5.0,
            poll_interval_seconds=0.1,
            sleep=lambda _s: None,
        )
    assert exc.value.evidence.get("readiness") == outcome


def test_smoke_succeeds_after_delayed_readiness(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    world.api_ready_script = ["retry", "retry", "ready"]
    live = _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.RESUME_SERVICES
    )
    assert live
    journal = _journal(world, prod)
    assert journal["smoke_ok"] is True
    assert journal["stage"] == CutoverStage.READONLY_SMOKE.value
    assert world.services.api_active is True
    assert world.services.health_timer_active is False


def test_smoke_readiness_timeout_stops_owned_api(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    live = _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.READONLY_SMOKE
    )
    world.api_ready_script = ["retry", "retry", "retry", "retry", "retry"]
    with pytest.raises(CutoverError, match="timed out|readiness"):
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
                ready_timeout=0.05,
            )
        )
    assert world.services.api_active is False
    assert world.services.health_timer_active is False
    journal = _journal(world, prod)
    assert journal["stage"] == CutoverStage.ATOMIC_SWAP.value
    assert journal["smoke_ok"] is False
    assert journal["smoke_started_api"] is False
    assert world.mail_pause is True
    assert world.mirror_pause is True


def test_smoke_readiness_http_fail_preserves_failsafe(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    live = _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.READONLY_SMOKE
    )
    world.api_ready_script = ["http"]
    with pytest.raises(CutoverError):
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
    journal = _journal(world, prod)
    assert journal["stage"] == CutoverStage.ATOMIC_SWAP.value
    assert journal["smoke_ok"] is False
    assert journal["smoke_started_api"] is False
    assert world.services.api_active is False


def test_smoke_ready_then_later_endpoint_fails(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    live = _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.READONLY_SMOKE
    )
    world.api_ready_script = ["ready"]
    world.smoke_payload = {"status": "degraded", "sqlite_query_only": True}
    with pytest.raises(CutoverError):
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
    assert world.services.api_active is False
    journal = _journal(world, prod)
    assert journal["smoke_ok"] is False
    assert journal["stage"] == CutoverStage.ATOMIC_SWAP.value


def test_smoke_refuses_preexisting_api_without_claim(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    live = _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.READONLY_SMOKE
    )
    world.services.api_active = True
    with pytest.raises(CutoverError, match="already active"):
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
    assert world.services.api_active is True  # not stopped
    journal = _journal(world, prod)
    assert journal.get("smoke_started_api") is not True


def test_smoke_ambiguous_stop_keeps_ownership(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    live = _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.READONLY_SMOKE
    )
    world.api_ready_script = ["http"]
    world.refuse_stop_api = True
    with pytest.raises(CutoverError) as exc:
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
    journal = _journal(world, prod)
    assert journal["smoke_started_api"] is True
    assert journal["smoke_ok"] is False
    evidence = getattr(exc.value, "evidence", {}) or {}
    assert evidence.get("smoke_cleanup", {}).get("manual_stop_required") is True
    assert world.services.api_active is True


def test_readiness_failure_keeps_rollback_available(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    live = _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.READONLY_SMOKE
    )
    world.api_ready_script = ["malformed"]
    with pytest.raises(CutoverError):
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
    pre_path = prod.with_name(f"{prod.name}.pre_cutover.{MID}")
    report = attempt_rollback_before_writers(
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
        pre_cutover_path=pre_path,
        expected_old_fingerprint=world.fingerprint(pre_path),
        expected_new_fingerprint=world.fingerprint(prod),
    )
    assert report["rolled_back"] is True
