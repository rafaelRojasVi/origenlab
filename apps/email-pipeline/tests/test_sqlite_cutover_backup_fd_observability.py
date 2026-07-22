"""PR-E: backup FD taxonomy, observation, and sanitized OperationalError detail."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from origenlab_email_pipeline.qa.sqlite_backup_fd_observability import (
    ACCESS_READ_ONLY,
    ACCESS_UNKNOWN,
    ACCESS_WRITABLE,
    CONFIDENCE_AMBIGUOUS,
    CONFIDENCE_BLOCKER,
    CONFIDENCE_TRUSTED,
    PHASE_DURING_COPY,
    PHASE_POST_COPY,
    PHASE_PRE_COPY,
    ActiveBackupObservationCapability,
    SourceMemberIdentity,
    classify_backup_source_fd,
    merge_observation_aggregates,
    summarize_fd_observation,
)
from origenlab_email_pipeline.qa.sqlite_cutover_artifact_permissions import (
    ROLE_MAIN,
    ROLE_SHM,
    ROLE_WAL,
)
from origenlab_email_pipeline.qa.sqlite_online_backup import (
    BACKUP_PHASES,
    BACKUP_PHASE_COPY,
    BACKUP_PHASE_DESTINATION_CONNECT,
    BACKUP_PHASE_SOURCE_CONNECT,
    OPERR_BUSY_OR_LOCKED,
    OPERR_CANNOT_OPEN,
    OPERR_CAPACITY,
    OPERR_INTERRUPTED,
    OPERR_IO_ERROR,
    OPERR_OTHER,
    OPERR_READONLY_WAL_LOCKING,
    OPERR_RECOVERY_WAL_SHM,
    OPERR_UNKNOWN_NAME,
    BackupError,
    BackupOptions,
    backup_is_completed,
    classify_sqlite_operational_error,
    operational_error_to_backup_error,
    partial_path_for,
    run_online_backup,
)
from origenlab_email_pipeline.qa.sqlite_production_cutover import (
    CutoverError,
    CutoverFailureCategory,
    CutoverOptions,
    CutoverStage,
    SyntheticWorld,
    _cutover_category_for_backup_error,
    _raise_cutover_from_backup_error,
    _sanitized_backup_failure_evidence,
    apply_stage,
)

HOSTILE = (
    "disk I/O error at /home/rafael/secret/db.sqlite "
    "file:///tmp/x.db?mode=rw "
    "C:\\Users\\rafael\\db.sqlite "
    "operator@origenlab.example "
    "SELECT * FROM secrets WHERE token='origenlab_test_token_ABCDEF123456' "
    "newline\ninjected\x00control"
)

MAIN_SHA = "b" * 40
MID = "cutover20260722T120000Z"


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
    pid: int | None = None,
    active: bool = True,
    members: dict[str, SourceMemberIdentity] | None = None,
) -> ActiveBackupObservationCapability:
    return ActiveBackupObservationCapability(
        owner_pid=os.getpid() if pid is None else pid,
        source_basename="emails.sqlite",
        members=members
        or {
            ROLE_MAIN: _member(ROLE_MAIN, inode=10),
            ROLE_WAL: _member(ROLE_WAL, inode=11),
            ROLE_SHM: _member(ROLE_SHM, inode=12),
        },
        active=active,
    )


def _operr(
    *,
    code: int | None,
    name: str | None,
    message: str = HOSTILE,
) -> sqlite3.OperationalError:
    exc = sqlite3.OperationalError(message)
    if code is not None:
        exc.sqlite_errorcode = code  # type: ignore[attr-defined]
    if name is not None:
        exc.sqlite_errorname = name  # type: ignore[attr-defined]
    return exc


def _assert_no_hostile(text: str) -> None:
    lowered = text.lower()
    for needle in (
        "/home/rafael",
        "/tmp/x.db",
        "c:\\users",
        "file://",
        "operator@",
        "select * from",
        "origenlab_test_token",
        "injected",
    ):
        assert needle not in lowered, f"hostile fragment leaked: {needle!r} in {text!r}"
    assert "\x00" not in text


# ---------------------------------------------------------------------------
# FD taxonomy
# ---------------------------------------------------------------------------


def test_own_main_readonly_trusted_with_active_capability() -> None:
    cap = _capability()
    row = classify_backup_source_fd(
        role=ROLE_MAIN,
        owner_pid=cap.owner_pid,
        access=ACCESS_READ_ONLY,
        capability=cap,
    )
    assert row["confidence"] == CONFIDENCE_TRUSTED
    assert row["owner_role"] == "active_backup_process"


def test_own_wal_readonly_trusted_with_active_capability() -> None:
    cap = _capability()
    row = classify_backup_source_fd(
        role=ROLE_WAL,
        owner_pid=cap.owner_pid,
        access=ACCESS_READ_ONLY,
        capability=cap,
    )
    assert row["confidence"] == CONFIDENCE_TRUSTED


def test_own_wal_writable_trusted_only_with_active_capability() -> None:
    """SQLite opens -wal RDWR under mode=ro; trust only with active capability."""
    cap = _capability()
    row = classify_backup_source_fd(
        role=ROLE_WAL,
        owner_pid=cap.owner_pid,
        access=ACCESS_WRITABLE,
        capability=cap,
    )
    assert row["confidence"] == CONFIDENCE_TRUSTED
    assert row["reason"] == "trusted_wal_frame_access"
    inactive = _capability(active=False)
    row2 = classify_backup_source_fd(
        role=ROLE_WAL,
        owner_pid=inactive.owner_pid,
        access=ACCESS_WRITABLE,
        capability=inactive,
    )
    assert row2["confidence"] == CONFIDENCE_BLOCKER


def test_own_writable_main_blocker() -> None:
    cap = _capability()
    row = classify_backup_source_fd(
        role=ROLE_MAIN,
        owner_pid=cap.owner_pid,
        access=ACCESS_WRITABLE,
        capability=cap,
    )
    assert row["confidence"] == CONFIDENCE_BLOCKER
    assert "writable_main" in row["reason"]


def test_own_shm_writable_trusted_only_with_active_capability() -> None:
    cap = _capability()
    row = classify_backup_source_fd(
        role=ROLE_SHM,
        owner_pid=cap.owner_pid,
        access=ACCESS_WRITABLE,
        capability=cap,
    )
    assert row["confidence"] == CONFIDENCE_TRUSTED
    assert row["reason"] == "trusted_shm_wal_locking"


def test_own_shm_rejected_without_active_capability() -> None:
    cap = _capability(active=False)
    row = classify_backup_source_fd(
        role=ROLE_SHM,
        owner_pid=cap.owner_pid,
        access=ACCESS_WRITABLE,
        capability=cap,
    )
    assert row["confidence"] == CONFIDENCE_BLOCKER
    assert row["reason"] == "foreign_or_inactive_capability"


def test_own_shm_rejected_when_capability_none() -> None:
    row = classify_backup_source_fd(
        role=ROLE_SHM,
        owner_pid=os.getpid(),
        access=ACCESS_WRITABLE,
        capability=None,
    )
    assert row["confidence"] == CONFIDENCE_BLOCKER


@pytest.mark.parametrize("access", [ACCESS_READ_ONLY, ACCESS_WRITABLE])
def test_foreign_member_always_blocker(access: str) -> None:
    cap = _capability()
    foreign_pid = cap.owner_pid + 99999
    for role in (ROLE_MAIN, ROLE_WAL, ROLE_SHM):
        row = classify_backup_source_fd(
            role=role,
            owner_pid=foreign_pid,
            access=access,
            capability=cap,
        )
        assert row["confidence"] == CONFIDENCE_BLOCKER


def test_unknown_fdinfo_access_ambiguous() -> None:
    cap = _capability()
    row = classify_backup_source_fd(
        role=ROLE_MAIN,
        owner_pid=cap.owner_pid,
        access=ACCESS_UNKNOWN,
        capability=cap,
    )
    assert row["confidence"] == CONFIDENCE_AMBIGUOUS


def test_deleted_and_unreadable_labels_ambiguous() -> None:
    cap = _capability()
    for label in ("deleted", "unreadable_link"):
        row = classify_backup_source_fd(
            role=ROLE_WAL,
            owner_pid=cap.owner_pid,
            access=ACCESS_READ_ONLY,
            capability=cap,
            path_label=label,
        )
        assert row["confidence"] == CONFIDENCE_AMBIGUOUS


def test_unexpected_journal_ambiguous() -> None:
    cap = _capability()
    row = classify_backup_source_fd(
        role="journal",
        owner_pid=cap.owner_pid,
        access=ACCESS_READ_ONLY,
        capability=cap,
    )
    assert row["confidence"] == CONFIDENCE_AMBIGUOUS


def test_pid_equality_without_matching_identity_rejected() -> None:
    """Own PID alone never trusts an unmatched / unknown role."""
    cap = _capability()
    row = classify_backup_source_fd(
        role=None,
        owner_pid=cap.owner_pid,
        access=ACCESS_READ_ONLY,
        capability=cap,
    )
    assert row["confidence"] == CONFIDENCE_AMBIGUOUS


def test_no_blanket_own_pid_bypass_in_assert_writers_signature() -> None:
    import inspect

    from origenlab_email_pipeline.qa import sqlite_production_cutover as mod

    sig = inspect.signature(mod._assert_writers_quiesced)
    assert "allow_own_fd" not in sig.parameters
    src = inspect.getsource(mod._assert_writers_quiesced)
    assert "allow_own_fd" not in src


# ---------------------------------------------------------------------------
# Observation aggregates + run_online_backup hooks
# ---------------------------------------------------------------------------


def test_summarize_and_merge_omit_raw_identity() -> None:
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
        ROLE_SHM: _member(ROLE_SHM, present=False),
    }
    agg = summarize_fd_observation(
        phase=PHASE_PRE_COPY, classified=classified, members=members
    )
    blob = json.dumps(agg)
    for forbidden in ("pid", "inode", "device", "/home/", "fd"):
        # "fd" may appear inside "fd_observation" schema keys elsewhere; here keys are safe.
        if forbidden == "fd":
            assert "fd_number" not in blob
            continue
        assert forbidden not in blob
    merged = merge_observation_aggregates([agg, {**agg, "phase": PHASE_POST_COPY}])
    assert merged["verdict"] == "ok"
    assert PHASE_PRE_COPY in merged["observation_phases"]


def test_empty_fd_scan_is_ambiguous_not_ok() -> None:
    """No /proc hits must not fail open as verdict=ok."""
    from origenlab_email_pipeline.qa.sqlite_backup_fd_observability import (
        BackupFdObservationError,
        observe_backup_source_fds,
    )

    cap = _capability(
        members={
            ROLE_MAIN: _member(ROLE_MAIN),
            ROLE_WAL: _member(ROLE_WAL, present=False),
            ROLE_SHM: _member(ROLE_SHM, present=False),
        }
    )
    with pytest.raises(BackupFdObservationError) as excinfo:
        observe_backup_source_fds(cap, phase=PHASE_PRE_COPY, scan_fn=lambda _m: [])
    assert excinfo.value.verdict == "ambiguous"
    assert excinfo.value.aggregate is not None
    assert excinfo.value.aggregate["verdict"] == "ambiguous"
    assert excinfo.value.aggregate.get("reason") == "missing_trusted_main"


def test_missing_trusted_wal_when_wal_present_is_ambiguous() -> None:
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
        phase=PHASE_PRE_COPY, classified=classified, members=members
    )
    assert agg["verdict"] == "ambiguous"
    assert agg["reason"] == "missing_trusted_wal"


def test_observe_propagates_incomplete_scan_error() -> None:
    from origenlab_email_pipeline.qa.sqlite_backup_fd_observability import (
        BackupFdObservationError,
        observe_backup_source_fds,
    )

    def boom(_members: dict[str, SourceMemberIdentity]) -> list[dict[str, Any]]:
        raise BackupFdObservationError(
            "unable to read active backup process FD table",
            verdict="ambiguous",
        )

    with pytest.raises(BackupFdObservationError) as excinfo:
        observe_backup_source_fds(
            _capability(),
            phase=PHASE_PRE_COPY,
            scan_fn=boom,
        )
    assert excinfo.value.verdict == "ambiguous"
    assert "active backup process" in str(excinfo.value)


def test_scan_fails_closed_when_owner_fd_table_unreadable() -> None:
    import errno as errno_mod

    from origenlab_email_pipeline.qa import sqlite_backup_fd_observability as obs

    owner_pid = 4242
    members = {ROLE_MAIN: _member(ROLE_MAIN, device=1, inode=10)}
    want_uid = os.getuid()

    class _FdDir:
        def iterdir(self) -> Any:
            raise PermissionError(errno_mod.EACCES, "denied")

    class _Entry:
        name = str(owner_pid)

        def stat(self) -> os.stat_result:
            return os.stat_result(
                (0o555, owner_pid, 1, 1, want_uid, want_uid, 0, 0, 0, 0)
            )

        def __truediv__(self, other: str) -> Any:
            assert other == "fd"
            return _FdDir()

    class _Proc:
        def is_dir(self) -> bool:
            return True

        def iterdir(self) -> Any:
            return iter([_Entry()])

    real_path = Path

    def path_factory(p: Any) -> Any:
        if str(p) == "/proc":
            return _Proc()
        return real_path(p)

    # Patch Path only inside the observability module.
    import origenlab_email_pipeline.qa.sqlite_backup_fd_observability as obs_mod

    original_path = obs_mod.Path
    try:
        obs_mod.Path = path_factory  # type: ignore[misc,assignment]
        with pytest.raises(obs.BackupFdObservationError) as excinfo:
            obs._scan_proc_for_members(
                members,
                owner_pid=owner_pid,
                read_fdinfo=lambda *_a: "r--",
            )
        assert excinfo.value.verdict == "ambiguous"
        assert "active backup process" in str(excinfo.value)
    finally:
        obs_mod.Path = original_path


def test_observation_phases_before_during_after(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    lock_dir = tmp_path / "locks"
    src_dir.mkdir()
    dst_dir.mkdir()
    lock_dir.mkdir()
    source = src_dir / "src.sqlite"
    dest = dst_dir / "dst.sqlite"
    conn = sqlite3.connect(source)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, payload TEXT)")
    conn.executemany("INSERT INTO t(payload) VALUES (?)", [(f"x{i}" * 50,) for i in range(80)])
    conn.commit()
    conn.close()

    phases: list[str] = []
    clock = {"t": 0.0}

    def fake_clock() -> float:
        return clock["t"]

    def observe(cap: ActiveBackupObservationCapability, phase: str) -> dict[str, Any]:
        assert cap.active
        phases.append(phase)
        # Advance clock so during_copy interval elapses across progress callbacks.
        clock["t"] += 10.0
        return {
            "schema_version": 1,
            "phase": phase,
            "member_roles_present": ["main"],
            "member_roles_observed": ["main"],
            "accepted_locking_count": 1,
            "blocker_count": 0,
            "ambiguous_count": 0,
            "verdict": "ok",
        }

    def capture(path: Path) -> dict[str, Any]:
        st = path.stat()
        return {
            "device": st.st_dev,
            "inode": st.st_ino,
            "size": st.st_size,
            "mode": st.st_mode,
        }

    result = run_online_backup(
        BackupOptions(
            source=source,
            destination=dest,
            apply=True,
            allow_same_filesystem=True,
            pages_per_batch=2,
            progress_interval_seconds=0.0,
            fd_observation_interval_seconds=1.0,
            lock_dir=lock_dir,
            clock=fake_clock,
            require_fd_observation=True,
            fd_observe_hook=observe,
            fd_capture_hook=capture,
        )
    )
    assert result["completed"] is True
    assert PHASE_PRE_COPY in phases
    assert PHASE_POST_COPY in phases
    assert PHASE_DURING_COPY in phases
    assert result["fd_observation"]["verdict"] == "ok"
    blob = json.dumps(result["fd_observation"])
    _assert_no_hostile(blob)
    assert "pid" not in blob
    assert str(os.getpid()) not in blob


def test_observer_failure_cleans_partials_and_publishes_nothing(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    lock_dir = tmp_path / "locks"
    for d in (src_dir, dst_dir, lock_dir):
        d.mkdir()
    source = src_dir / "src.sqlite"
    dest = dst_dir / "dst.sqlite"
    conn = sqlite3.connect(source)
    conn.execute("CREATE TABLE t (id INTEGER)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()

    def observe(_cap: Any, phase: str) -> dict[str, Any]:
        if phase == PHASE_PRE_COPY:
            return {
                "schema_version": 1,
                "phase": phase,
                "member_roles_present": ["main"],
                "member_roles_observed": ["main"],
                "accepted_locking_count": 0,
                "blocker_count": 1,
                "ambiguous_count": 0,
                "verdict": "blocked",
            }
        return {"verdict": "ok", "phase": phase}

    def capture(path: Path) -> dict[str, Any]:
        st = path.stat()
        return {"device": st.st_dev, "inode": st.st_ino, "size": st.st_size, "mode": st.st_mode}

    with pytest.raises(BackupError) as excinfo:
        run_online_backup(
            BackupOptions(
                source=source,
                destination=dest,
                apply=True,
                allow_same_filesystem=True,
                lock_dir=lock_dir,
                require_fd_observation=True,
                fd_observe_hook=observe,
                fd_capture_hook=capture,
            )
        )
    assert excinfo.value.detail is not None
    assert excinfo.value.detail["fd_observation"]["verdict"] == "blocked"
    assert not dest.exists()
    assert not backup_is_completed(dest)
    assert not partial_path_for(dest).exists()


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="Linux /proc required")
def test_real_wal_proc_observation_on_linux(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    lock_dir = tmp_path / "locks"
    for d in (src_dir, dst_dir, lock_dir):
        d.mkdir()
    source = src_dir / "wal_src.sqlite"
    dest = dst_dir / "wal_dst.sqlite"
    conn = sqlite3.connect(source)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, payload TEXT)")
    conn.executemany(
        "INSERT INTO t(payload) VALUES (?)",
        [(f"row-{i}-" + ("y" * 100),) for i in range(40)],
    )
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    # Companions are typically removed on last close after TRUNCATE; backup
    # re-baselines after the readonly source open recreates WAL locking files.

    result = run_online_backup(
        BackupOptions(
            source=source,
            destination=dest,
            apply=True,
            allow_same_filesystem=True,
            pages_per_batch=8,
            progress_interval_seconds=0.0,
            fd_observation_interval_seconds=60.0,
            lock_dir=lock_dir,
            require_fd_observation=True,
        )
    )
    assert result["completed"] is True
    fd_obs = result["fd_observation"]
    assert fd_obs["verdict"] == "ok"
    assert fd_obs["blocker_count"] == 0
    assert fd_obs["ambiguous_count"] == 0
    assert "main" in fd_obs["member_roles_observed"]
    blob = json.dumps(fd_obs)
    assert str(os.getpid()) not in blob
    assert str(source) not in blob
    assert "/proc" not in blob


# ---------------------------------------------------------------------------
# OperationalError classifier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "name", "category"),
    [
        (5, "SQLITE_BUSY", OPERR_BUSY_OR_LOCKED),
        (6, "SQLITE_LOCKED", OPERR_BUSY_OR_LOCKED),
        (520, "SQLITE_READONLY_CANTLOCK", OPERR_READONLY_WAL_LOCKING),
        (1288, "SQLITE_READONLY_CANTINIT", OPERR_READONLY_WAL_LOCKING),
        (14, "SQLITE_CANTOPEN", OPERR_CANNOT_OPEN),
        (10, "SQLITE_IOERR", OPERR_IO_ERROR),
        (13, "SQLITE_FULL", OPERR_CAPACITY),
        (9, "SQLITE_INTERRUPT", OPERR_INTERRUPTED),
        (99, "SQLITE_SOMETHING_ELSE", OPERR_OTHER),
    ],
)
def test_operational_error_categories(
    code: int, name: str, category: str
) -> None:
    detail = classify_sqlite_operational_error(
        _operr(code=code, name=name), phase=BACKUP_PHASE_COPY
    )
    assert detail["category"] == category
    assert detail["sqlite_errorcode"] == code
    assert detail["sqlite_errorname"] == name
    blob = json.dumps(detail)
    _assert_no_hostile(blob)
    if category == OPERR_READONLY_WAL_LOCKING:
        assert detail["recovery"] == OPERR_RECOVERY_WAL_SHM


@pytest.mark.parametrize("phase", sorted(BACKUP_PHASES))
def test_operational_error_every_allowlisted_phase(phase: str) -> None:
    detail = classify_sqlite_operational_error(
        _operr(code=5, name="SQLITE_BUSY"), phase=phase
    )
    assert detail["phase"] == phase


def test_operational_error_malformed_attributes() -> None:
    exc = sqlite3.OperationalError(HOSTILE)

    class Bad:
        def __int__(self) -> int:
            raise RuntimeError("boom")

    exc.sqlite_errorcode = Bad()  # type: ignore[attr-defined]
    exc.sqlite_errorname = "not a valid name!!!"  # type: ignore[attr-defined]
    detail = classify_sqlite_operational_error(exc, phase=BACKUP_PHASE_COPY)
    assert detail["sqlite_errorcode"] is None
    assert detail["sqlite_errorname"] == OPERR_UNKNOWN_NAME
    assert detail["category"] == OPERR_OTHER
    blob = json.dumps(detail) + str(
        operational_error_to_backup_error(exc, phase=BACKUP_PHASE_COPY)
    )
    _assert_no_hostile(blob)


def test_backup_error_detail_never_includes_raw_exc(tmp_path: Path) -> None:
    err = operational_error_to_backup_error(
        _operr(code=520, name="SQLITE_READONLY_CANTLOCK"),
        phase=BACKUP_PHASE_SOURCE_CONNECT,
    )
    assert isinstance(err.detail, dict)
    text = str(err) + json.dumps(err.detail)
    _assert_no_hostile(text)
    assert "READONLY_CANTLOCK" in json.dumps(err.detail)


def test_fail_phase_injection_converts_operational_error(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    lock_dir = tmp_path / "locks"
    for d in (src_dir, dst_dir, lock_dir):
        d.mkdir()
    source = src_dir / "src.sqlite"
    dest = dst_dir / "dst.sqlite"
    conn = sqlite3.connect(source)
    conn.execute("CREATE TABLE t (id INTEGER)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()
    with pytest.raises(BackupError) as excinfo:
        run_online_backup(
            BackupOptions(
                source=source,
                destination=dest,
                apply=True,
                allow_same_filesystem=True,
                lock_dir=lock_dir,
                fail_phase=BACKUP_PHASE_DESTINATION_CONNECT,
                fail_phase_exc=_operr(code=14, name="SQLITE_CANTOPEN"),
            )
        )
    detail = excinfo.value.detail or {}
    assert detail["operational_error"]["category"] == OPERR_CANNOT_OPEN
    assert detail["operational_error"]["phase"] == BACKUP_PHASE_DESTINATION_CONNECT
    _assert_no_hostile(str(excinfo.value) + json.dumps(detail))
    assert not dest.exists()


# ---------------------------------------------------------------------------
# Cutover CREATE_CURRENT_BACKUP conversion + journal
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
    backup: Path | None = None,
    staging: Path | None = None,
) -> CutoverOptions:
    return CutoverOptions(
        stage=stage,
        apply=apply,
        confirm_production_cutover=True,
        maintenance_id=MID,
        expected_main_sha=MAIN_SHA,
        expected_production_path=prod,
        expected_production_fingerprint=fp,
        journal_path=prod.parent
        / ".origenlab_cutover_journals"
        / f"{MID}.journal.json",
        backup_dest=backup,
        staging_dest=staging,
        reports_dir=reports,
        adapters=world,
        allow_synthetic_world=True,
    )


def _run_through(
    world: SyntheticWorld,
    prod: Path,
    reports: Path,
    fp: str,
    root: Path,
    *,
    stop_before: CutoverStage,
) -> str:
    backup = root / "emails_online_backup_cutover_fresh.sqlite"
    staging = root / "emails.sqlite.staged.cutover"
    sequence = [
        CutoverStage.PAUSE_WRITERS,
        CutoverStage.STOP_READERS,
        CutoverStage.QUIESCE_WAL,
        CutoverStage.APPLY_OS_WRITE_BARRIER,
        CutoverStage.CREATE_CURRENT_BACKUP,
    ]
    live = fp
    for stage in sequence:
        if stage == stop_before:
            break
        if stage == CutoverStage.QUIESCE_WAL:
            world.lock_records = []
            world.fd_hits = []
        report = apply_stage(
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
        live = report["journal"]["production_fingerprint"] or live
    return live


def test_backup_error_maps_to_non_generic_cutover_and_no_journal_advance(
    tmp_path: Path,
) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup = root / "emails_online_backup_cutover_fresh.sqlite"
    staging = root / "emails.sqlite.staged.cutover"
    live = _run_through(
        world,
        prod,
        reports,
        fp,
        root,
        stop_before=CutoverStage.CREATE_CURRENT_BACKUP,
    )
    jp = prod.parent / ".origenlab_cutover_journals" / f"{MID}.journal.json"
    before = json.loads(world.files[str(jp)])
    assert before["stage"] == CutoverStage.APPLY_OS_WRITE_BARRIER.value

    hostile_err = BackupError(
        "sqlite operational failure category=readonly_wal_locking phase=copy",
        detail={
            "operational_error": classify_sqlite_operational_error(
                _operr(code=520, name="SQLITE_READONLY_CANTLOCK", message=HOSTILE),
                phase=BACKUP_PHASE_COPY,
            )
        },
    )

    def boom(_source: Path, _dest: Path) -> dict[str, Any]:
        _raise_cutover_from_backup_error(hostile_err)
        raise AssertionError("unreachable")

    world.create_online_backup = boom  # type: ignore[method-assign]

    with pytest.raises(CutoverError) as excinfo:
        apply_stage(
            _opts(
                world,
                prod,
                reports,
                live,
                stage=CutoverStage.CREATE_CURRENT_BACKUP,
                apply=True,
                backup=backup,
                staging=staging,
            )
        )
    err = excinfo.value
    assert err.category == CutoverFailureCategory.SAFETY
    assert "unexpected" not in str(err).lower()
    assert err.evidence["operational_error"]["category"] == OPERR_READONLY_WAL_LOCKING
    assert err.recovery == OPERR_RECOVERY_WAL_SHM
    evidence_blob = json.dumps(err.evidence)
    _assert_no_hostile(evidence_blob)
    after = json.loads(world.files[str(jp)])
    assert after["stage"] == CutoverStage.APPLY_OS_WRITE_BARRIER.value
    assert after.get("backup_verified") is not True


def test_sanitized_evidence_and_category_mapping() -> None:
    detail = {
        "operational_error": classify_sqlite_operational_error(
            _operr(code=5, name="SQLITE_BUSY", message=HOSTILE),
            phase=BACKUP_PHASE_COPY,
        ),
        "fd_observation": {
            "verdict": "blocked",
            "blocker_count": 1,
            "pid": 12345,
            "path": "/home/rafael/x",
            "reason": "foreign_writable_main",
            "extra_leak": HOSTILE,
        },
    }
    evidence = _sanitized_backup_failure_evidence(detail)
    assert "pid" not in json.dumps(evidence["fd_observation"])
    assert "path" not in evidence["fd_observation"]
    assert "extra_leak" not in evidence["fd_observation"]
    assert evidence["fd_observation"].get("reason") == "foreign_writable_main"
    err = BackupError("x", detail=detail)
    assert _cutover_category_for_backup_error(err) == CutoverFailureCategory.SAFETY
    with pytest.raises(CutoverError) as raised:
        _raise_cutover_from_backup_error(err)
    assert raised.value.detail == raised.value.evidence
    assert "pid" not in json.dumps(raised.value.detail or {})


def test_sanitized_evidence_drops_hostile_recovery_and_malformed_fields() -> None:
    detail = {
        "operational_error": {
            "schema": 1,
            "phase": "copy",
            "category": OPERR_BUSY_OR_LOCKED,
            "sqlite_errorcode": 5,
            "sqlite_errorname": "SQLITE_BUSY",
            "retryable": True,
            "recovery": "/home/rafael/secret or SELECT 1",
            "message": HOSTILE,
        },
        "fd_observation": {
            "verdict": "not-a-real-verdict",
            "blocker_count": True,  # bool must not count as int
            "ambiguous_count": -1,
            "reason": "Bad Reason!",
            "observation_phases": ["pre_copy", "evil", 3],
            "member_roles_present": ["main", "journal", "/tmp/x"],
        },
    }
    evidence = _sanitized_backup_failure_evidence(detail)
    op = evidence["operational_error"]
    assert "recovery" not in op
    assert "message" not in op
    assert op["category"] == OPERR_BUSY_OR_LOCKED
    fd = evidence["fd_observation"]
    assert "verdict" not in fd
    assert "blocker_count" not in fd
    assert "ambiguous_count" not in fd
    assert "reason" not in fd
    assert fd["observation_phases"] == ["pre_copy"]
    assert fd["member_roles_present"] == ["main"]


def test_filesystem_backup_requires_ok_fd_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from origenlab_email_pipeline.qa import sqlite_production_cutover as cutover
    from origenlab_email_pipeline.qa.sqlite_online_backup import manifest_path_for

    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    dst_dir.mkdir()
    source = src_dir / "src.sqlite"
    dest = dst_dir / "dst.sqlite"
    conn = sqlite3.connect(source)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()
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
    with pytest.raises(CutoverError) as excinfo:
        adapters.create_online_backup(source, dest)
    assert excinfo.value.category == CutoverFailureCategory.SAFETY
    assert "pre_copy/post_copy" in str(excinfo.value)


def test_successful_synthetic_backup_includes_sanitized_fd_aggregate(
    tmp_path: Path,
) -> None:
    world, prod, reports, root, fp = _world(tmp_path)
    backup = root / "emails_online_backup_cutover_fresh.sqlite"
    staging = root / "emails.sqlite.staged.cutover"
    live = _run_through(
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
            live,
            stage=CutoverStage.CREATE_CURRENT_BACKUP,
            apply=True,
            backup=backup,
            staging=staging,
        )
    )
    fd_obs = report["backup"]["fd_observation"]
    assert fd_obs["verdict"] == "ok"
    blob = json.dumps(report)
    assert "/home/" not in blob
    assert "pid" not in fd_obs
