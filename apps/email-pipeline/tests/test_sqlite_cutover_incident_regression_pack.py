"""PR-F: end-to-end SQLite cutover incident-chain regression pack.

Cross-component synthetic regressions for safety contracts from PRs A–E.
Uses temporary dirs + SyntheticWorld only — no production/runtime operations.

Merging PR-F does not authorize a cutover. The July 19 MID remains permanently
non-resumable. Waves 3B/3C remain blocked. Roadmap is A–F only (no PR-G).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from origenlab_email_pipeline.qa.sqlite_backup_fd_observability import (
    ACCESS_READ_ONLY,
    CONFIDENCE_AMBIGUOUS,
    CONFIDENCE_BLOCKER,
    CONFIDENCE_TRUSTED,
    ActiveBackupObservationCapability,
    SourceMemberIdentity,
    classify_backup_source_fd,
    observe_backup_source_fds,
    summarize_fd_observation,
)
from origenlab_email_pipeline.qa.sqlite_cutover_artifact_permissions import (
    ROLE_MAIN,
    ROLE_SHM,
    ROLE_WAL,
)
from origenlab_email_pipeline.qa.sqlite_online_backup import (
    BackupError,
    build_safe_cli_json_report,
)
from origenlab_email_pipeline.qa.sqlite_production_cutover import (
    PERMANENTLY_ABANDONED_MIDS,
    CutoverError,
    CutoverFailureCategory,
    CutoverJournal,
    CutoverOptions,
    CutoverStage,
    SyntheticWorld,
    abort_before_swap,
    apply_stage,
    attempt_rollback_before_writers,
    plan_preflight,
    reconcile_atomic_swap_state,
    rollback_finalize,
    tree_snapshot,
)

MID = "cutover20260722T120000Z"
MAIN_SHA = "25cd4100e226427b3a4d027f1ee3b3af056884d4"
JULY19_MID = "cutover20260719T163633Z"

HOSTILE = (
    "disk I/O error at /home/rafael/secret/db.sqlite "
    "file:///tmp/x.db?mode=rw "
    "operator@origenlab.example "
    "SELECT * FROM secrets WHERE token='origenlab_test_token_ABCDEF123456' "
    "pid=12345 fd=42 device=99 inode=77"
)

FORBIDDEN_EVIDENCE = (
    "/home/",
    "/proc/",
    "file://",
    "SELECT ",
    "@origenlab.example",
    "origenlab_test_token",
    "pid=",
    "inode=",
    "device=",
)


# ---------------------------------------------------------------------------
# Shared synthetic helpers (thin wrappers; do not reimplement SyntheticWorld)
# ---------------------------------------------------------------------------


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
    fail_after: str | None = None,
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
            fail_after=fail_after if stage == stop_before else None,
        )
        # fail_after applies to the stage about to run when stop_before is None
        # and fail_after is set for COMPLETED path injections — handled below.
        if fail_after is not None and stop_before is None and stage == CutoverStage.COMPLETED:
            opts = _opts(
                world,
                prod,
                reports,
                live_fp,
                stage=stage,
                apply=True,
                backup=backup,
                staging=staging,
                fail_after=fail_after,
            )
        report = apply_stage(opts)
        live_fp = report["journal"]["production_fingerprint"] or live_fp
        if stage == CutoverStage.QUIESCE_WAL:
            live_fp = world.fingerprint(prod)
    return live_fp


def _journal(world: SyntheticWorld, prod: Path, *, maintenance_id: str = MID) -> dict:
    jp = prod.parent / ".origenlab_cutover_journals" / f"{maintenance_id}.journal.json"
    return json.loads(world.files[str(jp)])


def _journal_exists(world: SyntheticWorld, prod: Path, *, maintenance_id: str = MID) -> bool:
    jp = prod.parent / ".origenlab_cutover_journals" / f"{maintenance_id}.journal.json"
    return str(jp) in world.files


def _mutation_snapshot(world: SyntheticWorld, prod: Path) -> dict[str, Any]:
    return {
        "api_active": world.services.api_active,
        "timer_active": world.services.health_timer_active,
        "api_enabled": world.api_enabled,
        "timer_enabled": world.health_timer_enabled,
        "mail_pause": world.mail_pause,
        "mirror_pause": world.mirror_pause,
        "systemctl": list(world.systemctl_calls),
        "prod_mode": world.modes.get(str(prod)),
        "prod_bytes": world.files.get(str(prod)),
        "file_keys": sorted(world.files.keys()),
    }


def _assert_sanitized(blob: str) -> None:
    for bad in FORBIDDEN_EVIDENCE:
        assert bad not in blob, f"leaked {bad!r} in evidence"


def _assert_sanitized_obj(obj: Any, *, path: str = "$") -> None:
    """Recursively reject forbidden keys and hostile string values."""
    forbidden_keys = {
        "pid",
        "fd",
        "device",
        "inode",
        "path",
        "message",
        "sql",
        "token",
        "email",
        "uri",
        "dsn",
    }
    if isinstance(obj, dict):
        for key, value in obj.items():
            key_l = str(key).lower()
            assert key_l not in forbidden_keys, f"forbidden key {key!r} at {path}"
            assert "token" not in key_l or key_l in {
                "accepted_locking_count",
            }, f"suspicious key {key!r} at {path}"
            _assert_sanitized_obj(value, path=f"{path}.{key}")
        return
    if isinstance(obj, list):
        for i, value in enumerate(obj):
            _assert_sanitized_obj(value, path=f"{path}[{i}]")
        return
    if isinstance(obj, str):
        _assert_sanitized(obj)


def _member(
    role: str,
    *,
    present: bool = True,
    device: int = 1,
    inode: int = 100,
) -> SourceMemberIdentity:
    if not present:
        return SourceMemberIdentity(role=role, present=False)
    return SourceMemberIdentity(
        role=role, present=True, device=device, inode=inode, size=32
    )


def _capability(
    *,
    members: dict[str, SourceMemberIdentity] | None = None,
) -> ActiveBackupObservationCapability:
    import os

    return ActiveBackupObservationCapability(
        owner_pid=os.getpid(),
        source_basename="emails.sqlite",
        members=members
        or {
            ROLE_MAIN: _member(ROLE_MAIN, inode=10),
            ROLE_WAL: _member(ROLE_WAL, present=False),
            ROLE_SHM: _member(ROLE_SHM, present=False),
        },
        active=True,
    )


def _rollback(
    world: SyntheticWorld, prod: Path, reports: Path, root: Path
) -> tuple[Path, str, str]:
    backup, staging = _paths(root)
    _run_through(
        world, prod, reports, world.fingerprint(prod), root,
        stop_before=CutoverStage.READONLY_SMOKE,
    )
    pre_path = prod.with_name(f"{prod.name}.pre_cutover.{MID}")
    new_fp = world.fingerprint(prod)
    old_fp = world.fingerprint(pre_path)
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
    assert report["rollback_verified"] is True
    return pre_path, old_fp, new_fp


def _finalize_opts(
    world: SyntheticWorld,
    prod: Path,
    reports: Path,
    *,
    fail_after: str | None = None,
    maintenance_id: str = MID,
    apply: bool = True,
    approve_swap: bool = True,
) -> CutoverOptions:
    fp = world.fingerprint(prod)
    jp = prod.parent / ".origenlab_cutover_journals" / f"{maintenance_id}.journal.json"
    if str(jp) in world.files:
        data = json.loads(world.files[str(jp)])
        proof = data.get("rollback_proof")
        if isinstance(proof, dict) and proof.get("old_fingerprint"):
            fp = str(proof["old_fingerprint"])
    return _opts(
        world,
        prod,
        reports,
        fp,
        stage=CutoverStage.PLAN_PREFLIGHT,
        apply=apply,
        approve_swap=approve_swap,
        fail_after=fail_after,
        maintenance_id=maintenance_id,
    )


# ---------------------------------------------------------------------------
# 13. No cutover authorization (contract)
# ---------------------------------------------------------------------------


def test_roadmap_a_to_f_only_no_pr_g_and_no_cutover_authorization() -> None:
    assert JULY19_MID in PERMANENTLY_ABANDONED_MIDS
    doc = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "SQLITE_PRODUCTION_CUTOVER_ORCHESTRATOR.md"
    ).read_text(encoding="utf-8")
    assert re.search(r"\bA[–-]F\b", doc) or "A–F" in doc or "A-F" in doc
    assert "does not authorize a cutover" in doc.lower() or "does not authorize" in doc
    assert JULY19_MID in doc
    assert "Waves 3B" in doc and "3C" in doc
    # No PR-G roadmap row / section (mentioning "no PR-G" is fine).
    assert "| **G" not in doc
    assert "### PR-G" not in doc
    assert "PR-G —" not in doc and "PR-G -" not in doc
    prod_root = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "origenlab_email_pipeline"
        / "qa"
    )
    for name in (
        "sqlite_production_cutover.py",
        "sqlite_cutover_artifact_permissions.py",
        "sqlite_cutover_maintenance_boot_policy.py",
        "sqlite_backup_fd_observability.py",
        "sqlite_online_backup.py",
    ):
        text = (prod_root / name).read_text(encoding="utf-8")
        assert "authorize_cutover" not in text
        assert "CUTOVER_AUTHORIZED = True" not in text


# ---------------------------------------------------------------------------
# 1. Permanent July 19 MID lockout
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "op",
    [
        "apply_pause",
        "abort_before_swap",
        "rollback_before_writers",
        "rollback_finalize",
    ],
)
def test_july19_mid_rejected_before_any_mutation(tmp_path: Path, op: str) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    before = _mutation_snapshot(world, prod)
    tree_before = tree_snapshot(tmp_path)

    def run() -> None:
        if op == "apply_pause":
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
                    maintenance_id=JULY19_MID,
                )
            )
        elif op == "abort_before_swap":
            abort_before_swap(
                _opts(
                    world,
                    prod,
                    reports,
                    fp,
                    stage=CutoverStage.PLAN_PREFLIGHT,
                    apply=True,
                    backup=backup,
                    staging=staging,
                    maintenance_id=JULY19_MID,
                )
            )
        elif op == "rollback_before_writers":
            attempt_rollback_before_writers(
                _opts(
                    world,
                    prod,
                    reports,
                    fp,
                    stage=CutoverStage.ATOMIC_SWAP,
                    apply=True,
                    approve_swap=True,
                    backup=backup,
                    staging=staging,
                    maintenance_id=JULY19_MID,
                ),
                pre_cutover_path=prod.with_name(f"{prod.name}.pre_cutover.{JULY19_MID}"),
                expected_old_fingerprint=fp,
                expected_new_fingerprint=fp,
            )
        else:
            rollback_finalize(
                _finalize_opts(
                    world, prod, reports, maintenance_id=JULY19_MID
                )
            )

    with pytest.raises(CutoverError) as exc:
        run()
    assert exc.value.category == CutoverFailureCategory.SAFETY
    assert "abandoned" in str(exc.value).lower()
    assert _mutation_snapshot(world, prod) == before
    assert tree_snapshot(tmp_path) == tree_before
    assert not _journal_exists(world, prod, maintenance_id=JULY19_MID)
    assert world.systemctl_calls == before["systemctl"]
    # No backup / staging / journal artifacts for the abandoned MID.
    mid_artifacts = [
        k for k in world.files if JULY19_MID in k or "online_backup" in k
    ]
    assert mid_artifacts == []
    assert world.mail_pause is False
    assert world.mirror_pause is False


# ---------------------------------------------------------------------------
# 2. Preflight remains non-mutating
# ---------------------------------------------------------------------------


def test_preflight_is_strictly_non_mutating(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    before = _mutation_snapshot(world, prod)
    tree_before = tree_snapshot(tmp_path)
    report = plan_preflight(
        _opts(world, prod, reports, fp, stage=CutoverStage.PLAN_PREFLIGHT)
    )
    assert report["zero_write_preflight"] is True
    assert type(report["zero_write_preflight"]) is bool
    assert world.lock_held is False
    assert _mutation_snapshot(world, prod) == before
    assert tree_snapshot(tmp_path) == tree_before
    assert not _journal_exists(world, prod)
    assert world.systemctl_calls == []


# ---------------------------------------------------------------------------
# 3. STOP_READERS + durable boot suppression
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fail_after",
    [
        "boot_policy_intent",
        "boot_policy_timer_disabled",
        "boot_policy_api_disabled",
    ],
)
def test_stop_readers_crash_boundaries_reconciliation(
    tmp_path: Path, fail_after: str
) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    fp = _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.STOP_READERS
    )
    with pytest.raises(CutoverError):
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
                fail_after=fail_after,
            )
        )
    j = _journal(world, prod)
    assert j["maintenance_boot_policy_active"] is False
    assert type(j["maintenance_boot_policy_active"]) is bool
    assert j["maintenance_boot_policy_intent"]["action"] == "suppress"
    # Retry completes idempotently.
    report = apply_stage(
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
    j2 = report["journal"]
    assert j2["maintenance_boot_policy_active"] is True
    assert type(j2["maintenance_boot_policy_active"]) is bool
    assert world.api_enabled is False
    assert world.health_timer_enabled is False
    assert world.services.api_active is False
    assert world.services.health_timer_active is False
    # Timer disable must precede API disable (durable ordering).
    disables = [c for c in world.systemctl_calls if len(c) >= 3 and c[1] == "disable"]
    timer_idxs = [
        i for i, c in enumerate(disables) if c[2].endswith("api-health.timer")
    ]
    api_idxs = [
        i for i, c in enumerate(disables) if c[2].endswith("api.service")
    ]
    assert timer_idxs and api_idxs
    assert timer_idxs[0] < api_idxs[0]


def test_wsl_restart_simulation_keeps_units_stopped_during_maintenance(
    tmp_path: Path,
) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _run_through(world, prod, reports, fp, root, stop_before=CutoverStage.QUIESCE_WAL)
    assert world.api_enabled is False
    world.simulate_user_manager_restart()
    assert world.services.api_active is False
    assert world.services.health_timer_active is False
    assert world.api_enabled is False
    assert world.health_timer_enabled is False
    j = _journal(world, prod)
    assert j["maintenance_boot_policy_active"] is True


# ---------------------------------------------------------------------------
# 4. Reader inactivity / FD quiescence / backup FD taxonomy
# ---------------------------------------------------------------------------


def test_foreign_readonly_main_fd_is_blocker_not_trusted() -> None:
    cap = _capability()
    row = classify_backup_source_fd(
        role=ROLE_MAIN,
        owner_pid=cap.owner_pid + 999,
        access=ACCESS_READ_ONLY,
        capability=cap,
    )
    assert row["confidence"] == CONFIDENCE_BLOCKER


def test_own_pid_alone_without_role_is_ambiguous() -> None:
    cap = _capability()
    row = classify_backup_source_fd(
        role=None,
        owner_pid=cap.owner_pid,
        access=ACCESS_READ_ONLY,
        capability=cap,
    )
    assert row["confidence"] == CONFIDENCE_AMBIGUOUS


def test_empty_fd_scan_fail_closed_not_ok() -> None:
    from origenlab_email_pipeline.qa.sqlite_backup_fd_observability import (
        BackupFdObservationError,
    )

    cap = _capability(
        members={
            ROLE_MAIN: _member(ROLE_MAIN),
            ROLE_WAL: _member(ROLE_WAL, present=False),
            ROLE_SHM: _member(ROLE_SHM, present=False),
        }
    )
    with pytest.raises(BackupFdObservationError) as exc:
        observe_backup_source_fds(cap, phase="pre_copy", scan_fn=lambda _m: [])
    assert exc.value.verdict == "ambiguous"
    assert exc.value.aggregate is not None
    assert exc.value.aggregate["verdict"] == "ambiguous"
    assert exc.value.aggregate["reason"] == "missing_trusted_main"


def test_missing_positive_proof_for_present_wal_is_ambiguous() -> None:
    classified = [
        {
            "role": ROLE_MAIN,
            "owner_role": "active_backup_process",
            "access": ACCESS_READ_ONLY,
            "confidence": CONFIDENCE_TRUSTED,
            "reason": "trusted_readonly_main",
        }
    ]
    members = {
        ROLE_MAIN: _member(ROLE_MAIN),
        ROLE_WAL: _member(ROLE_WAL, present=True),
        ROLE_SHM: _member(ROLE_SHM, present=False),
    }
    agg = summarize_fd_observation(
        phase="pre_copy", classified=classified, members=members
    )
    assert agg["verdict"] == "ambiguous"
    assert agg["reason"] == "missing_trusted_wal"


def test_missing_positive_proof_for_present_shm_is_ambiguous() -> None:
    classified = [
        {
            "role": ROLE_MAIN,
            "owner_role": "active_backup_process",
            "access": ACCESS_READ_ONLY,
            "confidence": CONFIDENCE_TRUSTED,
            "reason": "trusted_readonly_main",
        }
    ]
    members = {
        ROLE_MAIN: _member(ROLE_MAIN),
        ROLE_WAL: _member(ROLE_WAL, present=False),
        ROLE_SHM: _member(ROLE_SHM, present=True),
    }
    agg = summarize_fd_observation(
        phase="pre_copy", classified=classified, members=members
    )
    assert agg["verdict"] == "ambiguous"
    assert agg["reason"] == "missing_trusted_shm"


def test_foreign_writable_wal_fd_is_blocker() -> None:
    from origenlab_email_pipeline.qa.sqlite_backup_fd_observability import (
        ACCESS_WRITABLE,
    )

    cap = _capability(
        members={
            ROLE_MAIN: _member(ROLE_MAIN),
            ROLE_WAL: _member(ROLE_WAL, present=True),
            ROLE_SHM: _member(ROLE_SHM, present=False),
        }
    )
    row = classify_backup_source_fd(
        role=ROLE_WAL,
        owner_pid=cap.owner_pid + 7,
        access=ACCESS_WRITABLE,
        capability=cap,
    )
    assert row["confidence"] == CONFIDENCE_BLOCKER


def test_quiesce_rejects_foreign_main_fd(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    fp = _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.QUIESCE_WAL
    )
    world.lock_records = []
    world.fd_hits = [
        {
            "pid": 99999,
            "fd": "7",
            "access": "r--",
            "path_label": "db_or_alias",
            "role": ROLE_MAIN,
        }
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
    assert exc.value.category in {
        CutoverFailureCategory.SAFETY,
        CutoverFailureCategory.AMBIGUOUS,
    }
    j = _journal(world, prod)
    assert j["stage"] == CutoverStage.STOP_READERS.value


# ---------------------------------------------------------------------------
# 5. Backup observation chain (CREATE_CURRENT_BACKUP)
# ---------------------------------------------------------------------------


def test_create_current_backup_requires_sanitized_ok_observation(
    tmp_path: Path,
) -> None:
    """SyntheticWorld path: stage advances only with completed backup + sanitized fd_obs."""
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    fp = _run_through(
        world,
        prod,
        reports,
        fp,
        root,
        stop_before=CutoverStage.CREATE_CURRENT_BACKUP,
    )
    report = apply_stage(
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
    fd = report["backup"]["fd_observation"]
    assert fd["verdict"] == "ok"
    assert type(fd["verdict"]) is str
    assert "pre_copy" in fd["observation_phases"]
    assert "post_copy" in fd["observation_phases"]
    _assert_sanitized_obj(fd)
    assert "pid" not in fd
    j = _journal(world, prod)
    assert j["stage"] == CutoverStage.CREATE_CURRENT_BACKUP.value
    assert j["backup_verified"] is True
    assert type(j["backup_verified"]) is bool


def test_filesystem_adapter_rejects_incomplete_fd_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real FilesystemAdapters gate: missing post_copy fails closed (PR-E)."""
    from origenlab_email_pipeline.qa import sqlite_production_cutover as cutover
    from origenlab_email_pipeline.qa.sqlite_online_backup import manifest_path_for

    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    dst_dir.mkdir()
    source = src_dir / "src.sqlite"
    dest = dst_dir / "dst.sqlite"
    source.write_bytes(b"SYNTHETIC")
    dest.write_bytes(source.read_bytes())
    man = manifest_path_for(dest)
    man.write_text(json.dumps({"completed": True, "method": "test"}), encoding="utf-8")

    def fake_backup(_options: Any) -> dict[str, Any]:
        return {
            "completed": True,
            "method": "test",
            "fd_observation": {
                "schema_version": 1,
                "observation_phases": ["pre_copy"],  # missing post_copy
                "member_roles_present": ["main"],
                "member_roles_observed": ["main"],
                "accepted_locking_count": 1,
                "blocker_count": 0,
                "ambiguous_count": 0,
                "verdict": "ok",
            },
        }

    monkeypatch.setattr(cutover, "run_online_backup", fake_backup)
    monkeypatch.setattr(cutover, "backup_is_completed", lambda _p: True)
    monkeypatch.setattr(cutover, "fingerprint_token", lambda _p: "fp")
    monkeypatch.setattr(cutover, "companion_paths", lambda _p: [])
    adapters = cutover.FilesystemAdapters()
    with pytest.raises(CutoverError) as exc:
        adapters.create_online_backup(source, dest)
    assert exc.value.category == CutoverFailureCategory.SAFETY
    assert "pre_copy/post_copy" in str(exc.value)


def test_backup_failure_does_not_advance_journal(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    fp = _run_through(
        world,
        prod,
        reports,
        fp,
        root,
        stop_before=CutoverStage.CREATE_CURRENT_BACKUP,
    )
    world.backup_incomplete = True
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
    assert "unexpected" not in str(exc.value).lower()
    j = _journal(world, prod)
    assert j["stage"] == CutoverStage.APPLY_OS_WRITE_BARRIER.value
    assert j["backup_verified"] is False
    assert type(j["backup_verified"]) is bool
    _assert_sanitized_obj(exc.value.evidence or {})


# ---------------------------------------------------------------------------
# 6. Safe CLI / evidence boundaries
# ---------------------------------------------------------------------------


def test_safe_cli_json_omits_unknown_and_rejects_bool_ints() -> None:
    raw = {
        "schema_version": 1,
        "completed": True,
        "method": "sqlite3.Connection.backup",
        "elapsed_seconds": 1.0,
        "source_size_bytes": 10,
        "destination_size_bytes": 10,
        "pages_per_batch": 100,
        "progress_events": 1,
        "allow_same_filesystem": False,
        "publication_method": "hardlink_no_clobber",
        "destination_verification": "mode=ro&immutable=1",
        "directory_fsync_supported": True,
        "source_opened_readonly": True,
        "source_mutated_by_utility": False,
        "source_fingerprint_changed_during_backup": False,
        "verification": "header+query_only+cheap_pragmas+schema_inventory",
        "completion_marker": "final_manifest",
        "secret_path": "/home/rafael/db.sqlite",
        "fd_observation": {
            "schema_version": 1,
            "observation_phases": ["pre_copy", "post_copy"],
            "member_roles_present": ["main"],
            "member_roles_observed": ["main"],
            "accepted_locking_count": 1,
            "blocker_count": 0,
            "ambiguous_count": 0,
            "verdict": "ok",
            "pid": 1,
            "path": "/home/x",
        },
    }
    report = build_safe_cli_json_report(raw)
    assert "secret_path" not in report
    assert "pid" not in report["fd_observation"]
    assert "path" not in report["fd_observation"]
    with pytest.raises(BackupError, match="cli json report rejected"):
        build_safe_cli_json_report({**raw, "completed": 1})
    with pytest.raises(BackupError, match="cli json report rejected"):
        build_safe_cli_json_report({**raw, "directory_fsync_supported": 0})
    with pytest.raises(BackupError, match="cli json report rejected"):
        poisoned = dict(raw)
        poisoned["fd_observation"] = {
            **raw["fd_observation"],
            "verdict": "weird",
        }
        build_safe_cli_json_report(poisoned)


def test_sanitized_backup_failure_evidence_drops_hostile_recovery() -> None:
    from origenlab_email_pipeline.qa.sqlite_production_cutover import (
        _sanitized_backup_failure_evidence,
    )

    detail = {
        "operational_error": {
            "schema": 1,
            "phase": "copy",
            "category": "busy_or_locked",
            "sqlite_errorcode": 5,
            "sqlite_errorname": "SQLITE_BUSY",
            "retryable": True,
            "recovery": "/home/rafael/secret " + HOSTILE,
            "message": HOSTILE,
        },
        "fd_observation": {
            "verdict": "blocked",
            "blocker_count": 1,
            "pid": 12345,
            "path": "/home/rafael/x",
            "reason": "foreign_writable_main",
        },
    }
    evidence = _sanitized_backup_failure_evidence(detail)
    _assert_sanitized_obj(evidence)
    assert "operational_error" in evidence
    assert "recovery" not in evidence["operational_error"]
    assert "message" not in evidence["operational_error"]
    assert "pid" not in evidence["fd_observation"]
    assert evidence["fd_observation"]["verdict"] == "blocked"
    assert evidence["fd_observation"]["blocker_count"] == 1
    assert evidence["fd_observation"]["reason"] == "foreign_writable_main"


# ---------------------------------------------------------------------------
# 7. Artifact permission chain
# ---------------------------------------------------------------------------


def test_barrier_partial_then_abort_restores_without_writer_resume(
    tmp_path: Path,
) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    world.materialize_companion(prod, "wal", size=0, mode=0o644, inode=9001)
    world.materialize_companion(prod, "shm", size=32768, mode=0o644, inode=9002)
    world.wal_size = 0
    backup, staging = _paths(root)
    fp = _run_through(
        world,
        prod,
        reports,
        fp,
        root,
        stop_before=CutoverStage.APPLY_OS_WRITE_BARRIER,
    )
    with pytest.raises(CutoverError):
        apply_stage(
            _opts(
                world,
                prod,
                reports,
                fp,
                stage=CutoverStage.APPLY_OS_WRITE_BARRIER,
                apply=True,
                backup=backup,
                staging=staging,
                fail_after="after_barrier_chmod_main",
            )
        )
    j = _journal(world, prod)
    # Partial barrier must not claim full barrier success / COMPLETED.
    assert j["stage"] != CutoverStage.APPLY_OS_WRITE_BARRIER.value
    assert j["stage"] != CutoverStage.COMPLETED.value
    abort_before_swap(
        _opts(
            world,
            prod,
            reports,
            fp,
            stage=CutoverStage.PLAN_PREFLIGHT,
            apply=True,
            backup=backup,
            staging=staging,
        )
    )
    j2 = _journal(world, prod)
    assert j2["stage"] != CutoverStage.COMPLETED.value
    assert j2["writers_resumed"] is False
    assert type(j2["writers_resumed"]) is bool


def test_shm_readonly_blocks_mail_resume_after_barrier(tmp_path: Path) -> None:
    """Incident-style: SHM left 0444 must block mail resume (cross PR-B gate)."""
    world, prod, reports, root, fp = _world(tmp_path)
    world.materialize_companion(prod, "wal", size=0, mode=0o644, inode=9001)
    world.materialize_companion(prod, "shm", size=32768, mode=0o644, inode=9002)
    world.wal_size = 0
    live = _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.RESUME_WRITERS_MAIL
    )
    shm = Path(str(prod) + "-shm")
    world.modes[str(shm)] = 0o444
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
    assert exc.value.category in {
        CutoverFailureCategory.SAFETY,
        CutoverFailureCategory.VERIFY,
    }
    assert "shm" in str(exc.value).lower()
    j = _journal(world, prod)
    assert j["stage"] != CutoverStage.COMPLETED.value
    assert j["writers_resumed"] is False
    assert type(j["writers_resumed"]) is bool


# ---------------------------------------------------------------------------
# 8. Read-only smoke ownership + cleanup
# ---------------------------------------------------------------------------


def test_smoke_failure_cleans_owned_api_and_preserves_pause(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.READONLY_SMOKE
    )
    world.smoke_payload = {"status": "degraded", "sqlite_query_only": True}
    live_fp = world.fingerprint(prod)
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
    assert world.services.api_active is False
    assert world.services.health_timer_active is False
    assert world.mail_pause is True
    assert world.mirror_pause is True
    j = _journal(world, prod)
    assert j["stage"] == CutoverStage.ATOMIC_SWAP.value
    assert j["smoke_ok"] is False
    assert j["smoke_started_api"] is False
    assert type(j["smoke_ok"]) is bool
    assert type(j["smoke_started_api"]) is bool
    _assert_sanitized_obj(exc.value.evidence or {})
    _assert_sanitized(str(exc.value))


def test_smoke_cleanup_ambiguous_keeps_ownership(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.READONLY_SMOKE
    )
    world.refuse_stop_api = True
    world.smoke_payload = {"status": "degraded", "sqlite_query_only": True}
    live_fp = world.fingerprint(prod)
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
    evidence = getattr(exc.value, "evidence", {}) or {}
    cleanup = evidence["smoke_cleanup"]
    assert cleanup["manual_stop_required"] is True
    assert cleanup["api_stopped"] is False
    assert type(cleanup["manual_stop_required"]) is bool
    j = _journal(world, prod)
    assert j["stage"] == CutoverStage.ATOMIC_SWAP.value
    assert j["smoke_started_api"] is True
    assert type(j["smoke_started_api"]) is bool
    assert world.services.api_active is True
    assert world.mail_pause is True
    assert world.mirror_pause is True


# ---------------------------------------------------------------------------
# 9. Rollback-before-writers → terminal ABANDONED
# ---------------------------------------------------------------------------


def test_smoke_fail_rollback_finalize_abandoned_not_completed(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.READONLY_SMOKE
    )
    world.smoke_payload = {"status": "degraded", "sqlite_query_only": True}
    with pytest.raises(CutoverError):
        apply_stage(
            _opts(
                world,
                prod,
                reports,
                world.fingerprint(prod),
                stage=CutoverStage.READONLY_SMOKE,
                apply=True,
                backup=backup,
                staging=staging,
            )
        )
    # Reset smoke payload so later finalize health checks are not poisoned.
    world.smoke_payload = {
        "status": "ok",
        "sqlite_query_only": True,
        "fingerprint": world.fingerprint(prod),
    }
    pre_path = prod.with_name(f"{prod.name}.pre_cutover.{MID}")
    old_fp = world.fingerprint(pre_path)
    new_fp = world.fingerprint(prod)
    rb = attempt_rollback_before_writers(
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
    assert rb["rolled_back"] is True
    assert rb["next_required"] == "rollback_finalize"
    report = rollback_finalize(_finalize_opts(world, prod, reports))
    assert report["abandoned"] is True
    assert report["cutover_succeeded"] is False
    assert report["completed"] is False
    assert report["soak_eligible"] is False
    assert report["waves_unblocked"] is False
    assert type(report["abandoned"]) is bool
    assert type(report["completed"]) is bool
    assert report["stage"] == CutoverStage.ABANDONED.value
    j = _journal(world, prod)
    assert j["abandoned"] is True
    assert j["stage"] == CutoverStage.ABANDONED.value
    assert j["stage"] != CutoverStage.COMPLETED.value
    assert j["writers_resumed"] is False
    assert type(j["writers_resumed"]) is bool
    assert type(j["abandoned"]) is bool
    # Idempotent terminal.
    again = rollback_finalize(_finalize_opts(world, prod, reports))
    assert again["already_finalized"] is True
    assert again["abandoned"] is True
    assert again["completed"] is False


def test_incomplete_rollback_proof_rejected_without_mutation(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    _rollback(world, prod, reports, root)
    jp = prod.parent / ".origenlab_cutover_journals" / f"{MID}.journal.json"
    data = json.loads(world.files[str(jp)])
    data["rollback_proof"] = {"incomplete": True}
    world.files[str(jp)] = json.dumps(data).encode()
    before = _mutation_snapshot(world, prod)
    with pytest.raises(CutoverError) as exc:
        rollback_finalize(_finalize_opts(world, prod, reports))
    assert exc.value.category in {
        CutoverFailureCategory.SAFETY,
        CutoverFailureCategory.AMBIGUOUS,
        CutoverFailureCategory.VERIFY,
    }
    j = _journal(world, prod)
    assert j["abandoned"] is False
    assert type(j["abandoned"]) is bool
    assert j["stage"] != CutoverStage.ABANDONED.value
    assert world.mail_pause == before["mail_pause"]
    assert world.mirror_pause == before["mirror_pause"]
    # May probe enablement, but must not mutate enablement/runtime.
    mutating = [
        c
        for c in world.systemctl_calls[len(before["systemctl"]) :]
        if len(c) >= 2 and c[1] in {"enable", "disable", "start", "stop"}
    ]
    assert mutating == []


# ---------------------------------------------------------------------------
# 10. Successful terminal COMPLETED ordering
# ---------------------------------------------------------------------------


def test_successful_path_reaches_completed_only_after_gates(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    live = _run_through(world, prod, reports, fp, root)
    j = _journal(world, prod)
    assert j["stage"] == CutoverStage.COMPLETED.value
    assert j["abandoned"] is False
    assert type(j["abandoned"]) is bool
    assert j["writers_resumed"] is True
    assert type(j["writers_resumed"]) is bool
    assert j["smoke_ok"] is True
    assert j["backup_verified"] is True
    assert world.mail_pause is False
    assert world.mirror_pause is False
    # Enablement restored from pre-maintenance capture.
    assert world.api_enabled is True
    assert world.health_timer_enabled is True
    assert j["maintenance_boot_policy_restored"] is True
    assert type(j["maintenance_boot_policy_restored"]) is bool
    assert live


def test_failure_before_completed_cannot_claim_success(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
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
                backup=backup,
                staging=staging,
                fail_after="boot_policy_restore_intent",
            )
        )
    j = _journal(world, prod)
    assert j["stage"] != CutoverStage.COMPLETED.value
    assert j["abandoned"] is False
    assert type(j["abandoned"]) is bool
    assert j["maintenance_boot_policy_restored"] is False
    assert type(j["maintenance_boot_policy_restored"]) is bool


# ---------------------------------------------------------------------------
# 11. Crash / restart matrix (durable intent boundaries)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stop_before", "fail_after", "expect_active_false"),
    [
        (CutoverStage.STOP_READERS, "boot_policy_intent", True),
        (CutoverStage.STOP_READERS, "boot_policy_timer_disabled", True),
        (CutoverStage.APPLY_OS_WRITE_BARRIER, "after_barrier_chmod_main", False),
        (CutoverStage.CREATE_CURRENT_BACKUP, None, False),  # incomplete backup below
    ],
)
def test_crash_restart_matrix_refuses_or_redrives(
    tmp_path: Path,
    stop_before: CutoverStage,
    fail_after: str | None,
    expect_active_false: bool,
) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    if stop_before == CutoverStage.CREATE_CURRENT_BACKUP:
        fp = _run_through(world, prod, reports, fp, root, stop_before=stop_before)
        world.backup_incomplete = True
        with pytest.raises(CutoverError):
            apply_stage(
                _opts(
                    world,
                    prod,
                    reports,
                    fp,
                    stage=stop_before,
                    apply=True,
                    backup=backup,
                    staging=staging,
                )
            )
        j = _journal(world, prod)
        assert j["stage"] != stop_before.value
        return

    fp = _run_through(world, prod, reports, fp, root, stop_before=stop_before)
    with pytest.raises(CutoverError):
        apply_stage(
            _opts(
                world,
                prod,
                reports,
                fp,
                stage=stop_before,
                apply=True,
                backup=backup,
                staging=staging,
                fail_after=fail_after,
            )
        )
    j = _journal(world, prod)
    assert j["stage"] != CutoverStage.COMPLETED.value
    if expect_active_false:
        assert j["maintenance_boot_policy_active"] is False
    # Must not silently skip: retry either completes or still refuses.
    try:
        apply_stage(
            _opts(
                world,
                prod,
                reports,
                fp,
                stage=stop_before,
                apply=True,
                backup=backup,
                staging=staging,
            )
        )
    except CutoverError:
        # Fail-closed refuse is acceptable when state is still unsafe.
        j2 = _journal(world, prod)
        assert j2["stage"] != CutoverStage.COMPLETED.value
        return
    j2 = _journal(world, prod)
    assert j2["stage"] == stop_before.value


def test_smoke_ownership_crash_boundary_preserves_flag(tmp_path: Path) -> None:
    """Crash after ownership write / after API start never advances journal."""
    world, prod, reports, root, fp = _world(tmp_path)
    backup, staging = _paths(root)
    _run_through(
        world, prod, reports, fp, root, stop_before=CutoverStage.READONLY_SMOKE
    )
    # After ownership write, before start: cleanup confirms stopped and clears.
    with pytest.raises(CutoverError):
        apply_stage(
            _opts(
                world,
                prod,
                reports,
                world.fingerprint(prod),
                stage=CutoverStage.READONLY_SMOKE,
                apply=True,
                backup=backup,
                staging=staging,
                fail_after="readonly_smoke_ownership_written",
            )
        )
    j = _journal(world, prod)
    assert j["stage"] == CutoverStage.ATOMIC_SWAP.value
    assert j["smoke_ok"] is False
    assert j["smoke_started_api"] is False
    assert world.services.api_active is False
    # After API start: cleanup stops owned API and clears ownership.
    with pytest.raises(CutoverError):
        apply_stage(
            _opts(
                world,
                prod,
                reports,
                world.fingerprint(prod),
                stage=CutoverStage.READONLY_SMOKE,
                apply=True,
                backup=backup,
                staging=staging,
                fail_after="readonly_smoke_after_api_start",
            )
        )
    j2 = _journal(world, prod)
    assert j2["stage"] == CutoverStage.ATOMIC_SWAP.value
    assert j2["smoke_started_api"] is False
    assert world.services.api_active is False
    assert world.mail_pause is True


# ---------------------------------------------------------------------------
# 12. Deferred stale-journal swap reconciliation residual
# ---------------------------------------------------------------------------


def test_stale_journal_after_exchange_is_ambiguous_pre_exchange(
    tmp_path: Path,
) -> None:
    """Pin residual: exchange reality + stale journal ≠ safe pre-exchange."""
    world, prod, reports, root, fp = _world(tmp_path)
    staging = root / "emails.sqlite.staged.cutover"
    pre = prod.with_name(f"{prod.name}.pre_cutover.{MID}")
    old_bytes = b"OLD-PROD"
    new_bytes = b"NEW-CANDIDATE"
    world.files[str(prod)] = new_bytes  # post-exchange: production holds new
    world.files[str(staging)] = old_bytes  # staging holds old
    world.modes[str(prod)] = 0o644
    world.modes[str(staging)] = 0o644
    old_fp = world.fingerprint(staging)
    new_fp = world.fingerprint(prod)
    # Journal still claims exchange not completed (stale write).
    journal = CutoverJournal(
        maintenance_id=MID,
        stage=CutoverStage.ATOMIC_SWAP,
        exchange_completed=False,
        old_production_retained=False,
        swap_intent={
            "old_fingerprint": old_fp,
            "new_fingerprint": new_fp,
        },
    )
    state = reconcile_atomic_swap_state(
        world,
        production=prod,
        staging=staging,
        pre_cutover=pre,
        journal=journal,
    )
    assert state["recognized"] == "ambiguous_pre_exchange"
    assert state["safe_action"] == "manual_inspect"
    # Must not classify as safely pre-exchange or completed.
    assert state["recognized"] != "pre_exchange_ready"
    assert state["recognized"] != "swap_complete"
    assert state["safe_action"] != "retry_atomic_swap_from_intent"
    assert state["safe_action"] != "continue_readonly_smoke"


def test_matching_pre_exchange_fingerprints_remain_retryable(tmp_path: Path) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    staging = root / "emails.sqlite.staged.cutover"
    pre = prod.with_name(f"{prod.name}.pre_cutover.{MID}")
    world.files[str(prod)] = b"OLD-PROD"
    world.files[str(staging)] = b"NEW-CANDIDATE"
    old_fp = world.fingerprint(prod)
    new_fp = world.fingerprint(staging)
    journal = CutoverJournal(
        maintenance_id=MID,
        stage=CutoverStage.ATOMIC_SWAP,
        exchange_completed=False,
        old_production_retained=False,
        swap_intent={
            "old_fingerprint": old_fp,
            "new_fingerprint": new_fp,
        },
    )
    state = reconcile_atomic_swap_state(
        world,
        production=prod,
        staging=staging,
        pre_cutover=pre,
        journal=journal,
    )
    assert state["recognized"] == "pre_exchange_ready"
    assert state["safe_action"] == "retry_atomic_swap_from_intent"
