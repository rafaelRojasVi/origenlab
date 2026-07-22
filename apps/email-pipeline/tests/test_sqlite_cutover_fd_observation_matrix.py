"""Post-closure FD observation adversarial matrix (beyond PR-E/F packs).

Synthetic taxonomy only — no live /proc mutation of production DB.
"""

from __future__ import annotations

import pytest

from origenlab_email_pipeline.qa.sqlite_backup_fd_observability import (
    ACCESS_READ_ONLY,
    ACCESS_UNKNOWN,
    ACCESS_WRITABLE,
    CONFIDENCE_AMBIGUOUS,
    CONFIDENCE_BLOCKER,
    CONFIDENCE_TRUSTED,
    ActiveBackupObservationCapability,
    BackupFdObservationError,
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

OWNER = 1111
FOREIGN = 2222


def _member(role: str, *, present: bool = True, device: int = 1, inode: int = 10) -> SourceMemberIdentity:
    return SourceMemberIdentity(
        role=role,
        present=present,
        device=device if present else None,
        inode=inode if present else None,
    )


def _cap(
    *,
    main: bool = True,
    wal: bool = True,
    shm: bool = True,
    active: bool = True,
) -> ActiveBackupObservationCapability:
    members = {
        ROLE_MAIN: _member(ROLE_MAIN, present=main, inode=10),
        ROLE_WAL: _member(ROLE_WAL, present=wal, inode=11),
        ROLE_SHM: _member(ROLE_SHM, present=shm, inode=12),
    }
    return ActiveBackupObservationCapability(
        active=active,
        owner_pid=OWNER,
        source_basename="emails.sqlite",
        members=members,
    )


@pytest.mark.parametrize(
    "role,owner,access,expect",
    [
        (ROLE_MAIN, OWNER, ACCESS_READ_ONLY, CONFIDENCE_TRUSTED),
        (ROLE_MAIN, OWNER, ACCESS_WRITABLE, CONFIDENCE_BLOCKER),
        (ROLE_MAIN, FOREIGN, ACCESS_READ_ONLY, CONFIDENCE_BLOCKER),
        (ROLE_MAIN, FOREIGN, ACCESS_WRITABLE, CONFIDENCE_BLOCKER),
        (ROLE_WAL, OWNER, ACCESS_WRITABLE, CONFIDENCE_TRUSTED),
        (ROLE_WAL, FOREIGN, ACCESS_READ_ONLY, CONFIDENCE_BLOCKER),
        (ROLE_SHM, OWNER, ACCESS_WRITABLE, CONFIDENCE_TRUSTED),
        (ROLE_SHM, FOREIGN, ACCESS_WRITABLE, CONFIDENCE_BLOCKER),
        (ROLE_MAIN, OWNER, ACCESS_UNKNOWN, CONFIDENCE_AMBIGUOUS),
        (ROLE_WAL, OWNER, ACCESS_UNKNOWN, CONFIDENCE_AMBIGUOUS),
        ("journal", OWNER, ACCESS_READ_ONLY, CONFIDENCE_AMBIGUOUS),
        (None, OWNER, ACCESS_READ_ONLY, CONFIDENCE_AMBIGUOUS),
    ],
)
def test_classify_matrix_role_owner_access(
    role: str | None, owner: int, access: str, expect: str
) -> None:
    cap = _cap()
    row = classify_backup_source_fd(
        role=role,
        owner_pid=owner,
        access=access,
        capability=cap,
        path_label="db_or_alias" if role == ROLE_MAIN else role,
    )
    assert row["confidence"] == expect


def test_own_pid_without_active_capability_never_trusted() -> None:
    cap = _cap(active=False)
    row = classify_backup_source_fd(
        role=ROLE_MAIN,
        owner_pid=OWNER,
        access=ACCESS_READ_ONLY,
        capability=cap,
    )
    assert row["confidence"] != CONFIDENCE_TRUSTED


def test_empty_scan_is_ambiguous_not_ok() -> None:
    cap = _cap()
    agg = summarize_fd_observation(phase="pre_copy", classified=[], members=cap.members)
    assert agg["verdict"] != "ok"
    assert agg["verdict"] == "ambiguous"
    assert agg.get("reason") in {"missing_trusted_main", "main_identity_missing"} or agg[
        "ambiguous_count"
    ] >= 1


def test_blocker_dominates_when_trusted_also_present() -> None:
    cap = _cap()
    classified = [
        {
            "role": ROLE_MAIN,
            "confidence": CONFIDENCE_TRUSTED,
            "access": ACCESS_READ_ONLY,
        },
        {
            "role": ROLE_WAL,
            "confidence": CONFIDENCE_TRUSTED,
            "access": ACCESS_WRITABLE,
        },
        {
            "role": ROLE_SHM,
            "confidence": CONFIDENCE_TRUSTED,
            "access": ACCESS_WRITABLE,
        },
        {
            "role": ROLE_MAIN,
            "confidence": CONFIDENCE_BLOCKER,
            "access": ACCESS_READ_ONLY,
        },
    ]
    # summarize: ambiguity from missing? all roles trusted but also blocker
    # Actually positive proof is satisfied; blockers set verdict blocked unless ambiguous.
    # Re-check summarize logic: if ambiguous or positive_problem -> ambiguous;
    # elif blockers -> blocked; else ok
    agg = summarize_fd_observation(
        phase="during_copy", classified=classified, members=cap.members
    )
    assert agg["verdict"] == "blocked"
    assert agg["blocker_count"] >= 1


def test_missing_wal_proof_ambiguous_when_wal_present() -> None:
    cap = _cap(wal=True)
    classified = [
        {"role": ROLE_MAIN, "confidence": CONFIDENCE_TRUSTED},
        {"role": ROLE_SHM, "confidence": CONFIDENCE_TRUSTED},
    ]
    agg = summarize_fd_observation(
        phase="pre_copy", classified=classified, members=cap.members
    )
    assert agg["verdict"] == "ambiguous"
    assert agg.get("reason") == "missing_trusted_wal"


def test_missing_shm_proof_ambiguous_when_shm_present() -> None:
    cap = _cap(shm=True)
    classified = [
        {"role": ROLE_MAIN, "confidence": CONFIDENCE_TRUSTED},
        {"role": ROLE_WAL, "confidence": CONFIDENCE_TRUSTED},
    ]
    agg = summarize_fd_observation(
        phase="pre_copy", classified=classified, members=cap.members
    )
    assert agg["verdict"] == "ambiguous"
    assert agg.get("reason") == "missing_trusted_shm"


def test_wal_absent_does_not_require_wal_proof() -> None:
    cap = _cap(wal=False, shm=False)
    classified = [{"role": ROLE_MAIN, "confidence": CONFIDENCE_TRUSTED}]
    agg = summarize_fd_observation(
        phase="post_copy", classified=classified, members=cap.members
    )
    assert agg["verdict"] == "ok"


def test_observe_inactive_capability_raises() -> None:
    cap = _cap(active=False)
    with pytest.raises(BackupFdObservationError) as ei:
        observe_backup_source_fds(cap, phase="pre_copy", scan_fn=lambda _m: [])
    assert ei.value.verdict == "ambiguous"


def test_observe_scan_exception_becomes_ambiguous() -> None:
    cap = _cap()

    def boom(_members):
        raise RuntimeError("/proc gone " + "/home/rafael/secret")

    with pytest.raises(BackupFdObservationError) as ei:
        observe_backup_source_fds(cap, phase="pre_copy", scan_fn=boom)
    assert ei.value.verdict == "ambiguous"
    assert "/home/" not in str(ei.value)


def test_observe_malformed_hits_ambiguous() -> None:
    cap = _cap()
    with pytest.raises(BackupFdObservationError):
        observe_backup_source_fds(
            cap, phase="pre_copy", scan_fn=lambda _m: {"not": "a list"}  # type: ignore[arg-type,return-value]
        )


def test_duplicate_fds_same_member_still_ok_if_trusted() -> None:
    cap = _cap(wal=False, shm=False)
    hits = [
        {
            "pid": OWNER,
            "fd": "3",
            "device": 1,
            "inode": 10,
            "role": ROLE_MAIN,
            "access": ACCESS_READ_ONLY,
            "path_label": "db_or_alias",
        },
        {
            "pid": OWNER,
            "fd": "4",
            "device": 1,
            "inode": 10,
            "role": ROLE_MAIN,
            "access": ACCESS_READ_ONLY,
            "path_label": "db_or_alias",
        },
    ]
    agg = observe_backup_source_fds(
        cap, phase="pre_copy", scan_fn=lambda _m: hits
    )
    assert agg["verdict"] == "ok"


def test_foreign_and_owner_on_same_member_is_blocked() -> None:
    cap = _cap(wal=False, shm=False)
    hits = [
        {
            "pid": OWNER,
            "fd": "3",
            "device": 1,
            "inode": 10,
            "role": ROLE_MAIN,
            "access": ACCESS_READ_ONLY,
            "path_label": "db_or_alias",
        },
        {
            "pid": FOREIGN,
            "fd": "9",
            "device": 1,
            "inode": 10,
            "role": ROLE_MAIN,
            "access": ACCESS_READ_ONLY,
            "path_label": "db_or_alias",
        },
    ]
    with pytest.raises(BackupFdObservationError) as ei:
        observe_backup_source_fds(cap, phase="pre_copy", scan_fn=lambda _m: hits)
    assert ei.value.verdict == "blocked"


def test_deleted_path_label_is_not_silently_ok() -> None:
    cap = _cap(wal=False, shm=False)
    row = classify_backup_source_fd(
        role=ROLE_MAIN,
        owner_pid=OWNER,
        access=ACCESS_READ_ONLY,
        capability=cap,
        path_label="deleted",
    )
    # deleted identities should not be trusted blindly
    assert row["confidence"] in {CONFIDENCE_AMBIGUOUS, CONFIDENCE_BLOCKER}
