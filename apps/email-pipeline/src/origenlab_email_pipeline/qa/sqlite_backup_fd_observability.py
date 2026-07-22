"""PR-E: trusted backup-source FD taxonomy and sanitized observation aggregates.

Identity is bound via non-following open + fstat (same capture used by the
artifact-permission layer). Own PID alone is never sufficient — acceptance
requires an active in-process backup observation capability that exists only
while ``run_online_backup()`` owns its source connection.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from origenlab_email_pipeline.qa.sqlite_cutover_artifact_permissions import (
    ROLE_MAIN,
    ROLE_SHM,
    ROLE_WAL,
    filesystem_capture_member_identity,
    member_path,
)

FD_OBS_SCHEMA_VERSION = 1
BACKUP_FD_ROLES: frozenset[str] = frozenset({ROLE_MAIN, ROLE_WAL, ROLE_SHM})

CONFIDENCE_TRUSTED = "trusted_backup"
CONFIDENCE_BLOCKER = "blocker"
CONFIDENCE_AMBIGUOUS = "ambiguous"

OWNER_ACTIVE_BACKUP = "active_backup_process"
OWNER_FOREIGN = "foreign_process"

ACCESS_READ_ONLY = "read_only"
ACCESS_WRITABLE = "writable"
ACCESS_UNKNOWN = "unknown"

VERDICT_OK = "ok"
VERDICT_BLOCKED = "blocked"
VERDICT_AMBIGUOUS = "ambiguous"

PHASE_PRE_COPY = "pre_copy"
PHASE_DURING_COPY = "during_copy"
PHASE_POST_COPY = "post_copy"
OBS_PHASES: frozenset[str] = frozenset(
    {PHASE_PRE_COPY, PHASE_DURING_COPY, PHASE_POST_COPY}
)


class BackupFdObservationError(RuntimeError):
    """Fail-closed backup FD observation failure (already sanitized)."""

    def __init__(
        self,
        message: str,
        *,
        verdict: str = VERDICT_AMBIGUOUS,
        aggregate: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.verdict = verdict
        self.aggregate = aggregate or {}


@dataclass(frozen=True)
class SourceMemberIdentity:
    """Exact device/inode identity for one source artifact member."""

    role: str
    present: bool
    device: int | None = None
    inode: int | None = None
    size: int | None = None

    def key(self) -> tuple[int, int] | None:
        if (
            self.present
            and isinstance(self.device, int)
            and isinstance(self.inode, int)
        ):
            return (int(self.device), int(self.inode))
        return None


@dataclass
class ActiveBackupObservationCapability:
    """In-process capability that exists only while the backup owns the source.

    Deactivate (or leave scope) after the source connection closes so the same
    SHM FD cannot be trusted outside the active backup window.
    """

    owner_pid: int
    source_basename: str
    members: dict[str, SourceMemberIdentity]
    active: bool = True
    _token: object = field(default_factory=object, repr=False)

    def deactivate(self) -> None:
        self.active = False

    def is_active_for(self, pid: int) -> bool:
        return self.active is True and int(pid) == int(self.owner_pid)


CaptureFn = Callable[[Path], dict[str, Any]]


def capture_backup_source_members(
    source: Path,
    *,
    capture_fn: CaptureFn | None = None,
) -> dict[str, SourceMemberIdentity]:
    """Authoritatively capture main / exact -wal / exact -shm identities.

    Never uses globs or basename-only trust. Missing companions are recorded as
    ``present=False`` without inventing identities.
    """
    capture = capture_fn or filesystem_capture_member_identity
    members: dict[str, SourceMemberIdentity] = {}
    # Main is required.
    main_meta = capture(Path(source))
    members[ROLE_MAIN] = SourceMemberIdentity(
        role=ROLE_MAIN,
        present=True,
        device=int(main_meta["device"]),
        inode=int(main_meta["inode"]),
        size=int(main_meta.get("size") or 0),
    )
    for role in (ROLE_WAL, ROLE_SHM):
        path = member_path(Path(source), role)
        try:
            if path.is_symlink():
                raise BackupFdObservationError(
                    f"source {role} member is a symlink",
                    verdict=VERDICT_AMBIGUOUS,
                )
            if not path.is_file():
                members[role] = SourceMemberIdentity(role=role, present=False)
                continue
            meta = capture(path)
            members[role] = SourceMemberIdentity(
                role=role,
                present=True,
                device=int(meta["device"]),
                inode=int(meta["inode"]),
                size=int(meta.get("size") or 0),
            )
        except BackupFdObservationError:
            raise
        except OSError:
            raise BackupFdObservationError(
                f"unable to capture source {role} member identity",
                verdict=VERDICT_AMBIGUOUS,
            ) from None
        except Exception as exc:  # noqa: BLE001
            # ArtifactPermissionError and similar — sanitize to fixed message.
            raise BackupFdObservationError(
                f"source {role} member identity capture refused "
                f"({type(exc).__name__})",
                verdict=VERDICT_AMBIGUOUS,
            ) from None
    return members


def compare_source_member_sets(
    before: dict[str, SourceMemberIdentity],
    after: dict[str, SourceMemberIdentity],
) -> str | None:
    """Return a short problem code on appearance/disappearance/replacement."""
    for role in BACKUP_FD_ROLES:
        b = before.get(role)
        a = after.get(role)
        if b is None or a is None:
            return f"member_set_incomplete_{role}"
        if b.present != a.present:
            return f"member_presence_changed_{role}"
        if b.present and a.present:
            if b.key() != a.key():
                return f"member_identity_drift_{role}"
    return None


def _role_for_key(
    key: tuple[int, int],
    members: dict[str, SourceMemberIdentity],
) -> str | None:
    for role, member in members.items():
        mk = member.key()
        if mk is not None and mk == key:
            return role
    return None


def classify_backup_source_fd(
    *,
    role: str | None,
    owner_pid: int,
    access: str,
    capability: ActiveBackupObservationCapability | None,
    path_label: str | None = None,
) -> dict[str, Any]:
    """Classify one FD against the active backup capability and exact role.

    Safety rules:
    - own PID alone is never sufficient;
    - acceptance requires an active capability for this PID;
    - own main must be read-only (writable main is always a blocker);
    - own WAL may be RDWR under an active capability (SQLite opens -wal RDWR
      even for URI mode=ro so Online Backup can read frames);
    - own SHM may be writable (SQLite WAL locking) only with active capability;
    - foreign main/WAL/SHM (including read-only) is always a blocker;
    - unknown access, unmatched role, deleted/unreadable labels → ambiguous.
    """
    access_norm = access if access in {ACCESS_READ_ONLY, ACCESS_WRITABLE} else ACCESS_UNKNOWN
    label = (path_label or "").strip().lower() or None

    if role == "journal" or label == "journal":
        return {
            "role": "journal",
            "owner_role": OWNER_FOREIGN,
            "access": access_norm,
            "confidence": CONFIDENCE_AMBIGUOUS,
            "reason": "unexpected_journal_fd",
        }
    if role not in BACKUP_FD_ROLES:
        return {
            "role": role or "unknown",
            "owner_role": OWNER_FOREIGN,
            "access": access_norm,
            "confidence": CONFIDENCE_AMBIGUOUS,
            "reason": "unmatched_or_unknown_role",
        }
    if label in {"deleted", "unreadable_link"}:
        return {
            "role": role,
            "owner_role": OWNER_FOREIGN,
            "access": access_norm,
            "confidence": CONFIDENCE_AMBIGUOUS,
            "reason": f"path_label_{label}",
        }
    if access_norm == ACCESS_UNKNOWN:
        return {
            "role": role,
            "owner_role": OWNER_FOREIGN,
            "access": access_norm,
            "confidence": CONFIDENCE_AMBIGUOUS,
            "reason": "unknown_fdinfo_access",
        }

    active = (
        capability is not None and capability.is_active_for(int(owner_pid))
    )
    if not active:
        # Foreign (or own-without-capability): always blocker for artifact members.
        return {
            "role": role,
            "owner_role": OWNER_FOREIGN,
            "access": access_norm,
            "confidence": CONFIDENCE_BLOCKER,
            "reason": "foreign_or_inactive_capability",
        }

    # Active backup process + matching capability.
    if role == ROLE_MAIN:
        if access_norm == ACCESS_WRITABLE:
            return {
                "role": role,
                "owner_role": OWNER_ACTIVE_BACKUP,
                "access": access_norm,
                "confidence": CONFIDENCE_BLOCKER,
                "reason": "writable_main_blocker",
            }
        return {
            "role": role,
            "owner_role": OWNER_ACTIVE_BACKUP,
            "access": access_norm,
            "confidence": CONFIDENCE_TRUSTED,
            "reason": "trusted_readonly_main",
        }

    if role == ROLE_WAL:
        # SQLite opens the -wal file RDWR even for URI mode=ro connections so
        # the Online Backup API can read WAL frames. Writable own WAL is therefore
        # trusted only while the active backup capability is live — never as a
        # general own-PID bypass, and never for foreign PIDs.
        return {
            "role": role,
            "owner_role": OWNER_ACTIVE_BACKUP,
            "access": access_norm,
            "confidence": CONFIDENCE_TRUSTED,
            "reason": (
                "trusted_readonly_wal"
                if access_norm == ACCESS_READ_ONLY
                else "trusted_wal_frame_access"
            ),
        }

    # SHM: trusted WAL locking FD even when writable, only with active capability.
    assert role == ROLE_SHM
    return {
        "role": role,
        "owner_role": OWNER_ACTIVE_BACKUP,
        "access": access_norm,
        "confidence": CONFIDENCE_TRUSTED,
        "reason": "trusted_shm_wal_locking",
    }


def _scan_proc_for_members(
    members: dict[str, SourceMemberIdentity],
    *,
    uid: int | None = None,
    read_fdinfo: Callable[[int, str], str] | None = None,
) -> list[dict[str, Any]]:
    """Scan same-user /proc for FDs matching captured member identities."""
    from origenlab_email_pipeline.qa.sqlite_production_cutover import (
        read_fdinfo_flags,
    )

    fdinfo = read_fdinfo or read_fdinfo_flags
    keys: dict[tuple[int, int], str] = {}
    for role, member in members.items():
        k = member.key()
        if k is not None:
            keys[k] = role

    hits: list[dict[str, Any]] = []
    if not keys:
        return hits
    want_uid = os.getuid() if uid is None else uid
    proc = Path("/proc")
    if not proc.is_dir():
        raise BackupFdObservationError(
            "unable to list /proc for backup FD inventory",
            verdict=VERDICT_AMBIGUOUS,
        )
    try:
        entries = list(proc.iterdir())
    except OSError:
        raise BackupFdObservationError(
            "unable to list /proc for backup FD inventory",
            verdict=VERDICT_AMBIGUOUS,
        ) from None

    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            pid = int(entry.name)
            st = entry.stat()
            if st.st_uid != want_uid:
                continue
            fd_dir = entry / "fd"
            for fd_path in fd_dir.iterdir():
                try:
                    target = os.stat(fd_path)
                except OSError:
                    continue
                key = (int(target.st_dev), int(target.st_ino))
                role = keys.get(key)
                if role is None:
                    continue
                access = fdinfo(pid, fd_path.name)
                path_label = "unknown"
                try:
                    link = os.readlink(fd_path)
                    if link.endswith(" (deleted)"):
                        path_label = "deleted"
                    elif link.endswith("-wal"):
                        path_label = "wal"
                    elif link.endswith("-shm"):
                        path_label = "shm"
                    elif link.endswith("-journal"):
                        path_label = "journal"
                    else:
                        path_label = "db_or_alias"
                except OSError:
                    path_label = "unreadable_link"
                hits.append(
                    {
                        "pid": pid,
                        "fd": fd_path.name,
                        "device": key[0],
                        "inode": key[1],
                        "role": role,
                        "access": access,
                        "path_label": path_label,
                    }
                )
        except OSError:
            continue
    return hits


def classify_observation_hits(
    hits: list[dict[str, Any]],
    *,
    capability: ActiveBackupObservationCapability | None,
) -> list[dict[str, Any]]:
    """Classify raw FD hits into sanitized taxonomy rows (no PID/FD/path)."""
    classified: list[dict[str, Any]] = []
    for hit in hits:
        row = classify_backup_source_fd(
            role=hit.get("role") if isinstance(hit.get("role"), str) else None,
            owner_pid=int(hit.get("pid") or -1),
            access=str(hit.get("access") or ACCESS_UNKNOWN),
            capability=capability,
            path_label=hit.get("path_label") if isinstance(hit.get("path_label"), str) else None,
        )
        classified.append(row)
    return classified


def summarize_fd_observation(
    *,
    phase: str,
    classified: list[dict[str, Any]],
    members: dict[str, SourceMemberIdentity],
) -> dict[str, Any]:
    """Build a sanitized aggregate — no PID/FD/path/device/inode."""
    if phase not in OBS_PHASES:
        phase = "unknown"
    accepted = sum(1 for c in classified if c.get("confidence") == CONFIDENCE_TRUSTED)
    blockers = sum(1 for c in classified if c.get("confidence") == CONFIDENCE_BLOCKER)
    ambiguous = sum(
        1 for c in classified if c.get("confidence") == CONFIDENCE_AMBIGUOUS
    )
    roles_observed = sorted(
        {
            str(c.get("role"))
            for c in classified
            if isinstance(c.get("role"), str) and c.get("role") in BACKUP_FD_ROLES
        }
    )
    members_present = sorted(
        role for role, m in members.items() if m.present
    )
    if ambiguous:
        verdict = VERDICT_AMBIGUOUS
    elif blockers:
        verdict = VERDICT_BLOCKED
    else:
        verdict = VERDICT_OK
    return {
        "schema_version": FD_OBS_SCHEMA_VERSION,
        "phase": phase,
        "member_roles_present": members_present,
        "member_roles_observed": roles_observed,
        "accepted_locking_count": int(accepted),
        "blocker_count": int(blockers),
        "ambiguous_count": int(ambiguous),
        "verdict": verdict,
    }


def observe_backup_source_fds(
    capability: ActiveBackupObservationCapability,
    *,
    phase: str,
    scan_fn: Callable[..., list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Observe and classify FDs for the active backup capability."""
    if capability is None or not capability.active:
        raise BackupFdObservationError(
            "backup FD observation requires an active capability",
            verdict=VERDICT_AMBIGUOUS,
        )
    scanner = scan_fn or (
        lambda members: _scan_proc_for_members(members)
    )
    try:
        hits = scanner(capability.members)
    except BackupFdObservationError:
        raise
    except Exception:  # noqa: BLE001
        raise BackupFdObservationError(
            "backup FD scan failed",
            verdict=VERDICT_AMBIGUOUS,
        ) from None
    classified = classify_observation_hits(hits, capability=capability)
    aggregate = summarize_fd_observation(
        phase=phase, classified=classified, members=capability.members
    )
    if aggregate["verdict"] != VERDICT_OK:
        raise BackupFdObservationError(
            f"backup FD observation {aggregate['verdict']}",
            verdict=str(aggregate["verdict"]),
            aggregate=aggregate,
        )
    return aggregate


def merge_observation_aggregates(
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Merge per-phase aggregates into one sanitized report fragment."""
    phases: list[str] = []
    accepted = 0
    blockers = 0
    ambiguous = 0
    roles: set[str] = set()
    present: set[str] = set()
    verdict = VERDICT_OK
    for obs in observations:
        if not isinstance(obs, dict):
            continue
        ph = obs.get("phase")
        if isinstance(ph, str) and ph in OBS_PHASES:
            phases.append(ph)
        accepted += int(obs.get("accepted_locking_count") or 0)
        blockers += int(obs.get("blocker_count") or 0)
        ambiguous += int(obs.get("ambiguous_count") or 0)
        for r in obs.get("member_roles_observed") or []:
            if isinstance(r, str):
                roles.add(r)
        for r in obs.get("member_roles_present") or []:
            if isinstance(r, str):
                present.add(r)
        v = obs.get("verdict")
        if v == VERDICT_AMBIGUOUS:
            verdict = VERDICT_AMBIGUOUS
        elif v == VERDICT_BLOCKED and verdict == VERDICT_OK:
            verdict = VERDICT_BLOCKED
    return {
        "schema_version": FD_OBS_SCHEMA_VERSION,
        "observation_phases": phases,
        "member_roles_present": sorted(present),
        "member_roles_observed": sorted(roles),
        "accepted_locking_count": accepted,
        "blocker_count": blockers,
        "ambiguous_count": ambiguous,
        "verdict": verdict,
    }
