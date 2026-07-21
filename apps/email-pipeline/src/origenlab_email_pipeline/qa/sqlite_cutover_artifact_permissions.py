"""SQLite cutover artifact permission capture / barrier / restore (PR-B).

Treats the production database as one artifact with three exact members:
main, ``-wal``, and ``-shm``. No globs. Modes are bound to device+inode via
open + ``fstat`` / ``fchmod``, never to a replacement file that merely reuses
a pathname.

Legacy journals without ``artifact_permissions`` keep loading; companion modes
are never silently invented for an in-progress legacy barrier.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

ARTIFACT_PERMISSIONS_SCHEMA = 1
ROLE_MAIN = "main"
ROLE_WAL = "wal"
ROLE_SHM = "shm"
ARTIFACT_ROLES: tuple[str, ...] = (ROLE_MAIN, ROLE_WAL, ROLE_SHM)


class ArtifactPermissionError(Exception):
    """Raised by helpers; callers map to CutoverError."""

    def __init__(
        self,
        message: str,
        *,
        category: str = "safety",
        recovery: str | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.recovery = recovery
        self.evidence = evidence or {}


class ArtifactFs(Protocol):
    def path_exists(self, path: Path) -> bool: ...
    def is_symlink(self, path: Path) -> bool: ...
    def is_file(self, path: Path) -> bool: ...
    def capture_member_identity(self, path: Path) -> dict[str, Any]: ...
    def chmod_verified_inode(
        self,
        path: Path,
        mode: int,
        *,
        expected_device: int,
        expected_inode: int,
    ) -> None: ...
    def fsync_file(self, path: Path) -> None: ...
    def fsync_dir(self, path: Path) -> None: ...
    def get_file_mode_owner(self, path: Path) -> dict[str, int]: ...
    def path_identity(self, path: Path) -> dict[str, Any]: ...


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def wal_path(main: Path) -> Path:
    return Path(str(main) + "-wal")


def shm_path(main: Path) -> Path:
    return Path(str(main) + "-shm")


def journal_path_sidecar(main: Path) -> Path:
    return Path(str(main) + "-journal")


def member_path(main: Path, role: str) -> Path:
    if role == ROLE_MAIN:
        return main
    if role == ROLE_WAL:
        return wal_path(main)
    if role == ROLE_SHM:
        return shm_path(main)
    raise ArtifactPermissionError(
        f"unknown artifact role {role!r}",
        category="preflight",
    )


@dataclass
class ArtifactMember:
    role: str
    present: bool
    basename: str | None = None
    device: int | None = None
    inode: int | None = None
    mode: int | None = None
    uid: int | None = None
    gid: int | None = None
    nlink: int | None = None
    size: int | None = None
    barrier_applied: bool = False
    barrier_mode: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None, *, role: str) -> ArtifactMember:
        if not data:
            return ArtifactMember(role=role, present=False)
        known = {f.name for f in cls.__dataclass_fields__.values()}
        payload = {k: data[k] for k in data if k in known}
        payload.setdefault("role", role)
        payload.setdefault("present", False)
        return cls(**payload)  # type: ignore[arg-type]

    def identity_tuple(self) -> tuple[int, int] | None:
        if not self.present or self.device is None or self.inode is None:
            return None
        return (int(self.device), int(self.inode))


@dataclass
class ArtifactPermissions:
    """Canonical journal representation for main/WAL/SHM permissions."""

    schema: int = ARTIFACT_PERMISSIONS_SCHEMA
    captured_at_utc: str = field(default_factory=_iso_now)
    phase: str = "pre_swap"  # pre_swap | post_swap
    main: ArtifactMember = field(
        default_factory=lambda: ArtifactMember(role=ROLE_MAIN, present=False)
    )
    wal: ArtifactMember = field(
        default_factory=lambda: ArtifactMember(role=ROLE_WAL, present=False)
    )
    shm: ArtifactMember = field(
        default_factory=lambda: ArtifactMember(role=ROLE_SHM, present=False)
    )
    barrier_partial: bool = False
    members_changed: list[str] = field(default_factory=list)
    # After RENAME_EXCHANGE: new production main identity (restore target).
    post_swap_main: dict[str, Any] | None = None
    # Companions observed at production paths after swap/smoke (never copy
    # pre-swap companion inode metadata onto a different inode).
    post_swap_companions: dict[str, Any] | None = None
    legacy_without_companions: bool = False

    def member(self, role: str) -> ArtifactMember:
        if role == ROLE_MAIN:
            return self.main
        if role == ROLE_WAL:
            return self.wal
        if role == ROLE_SHM:
            return self.shm
        raise ArtifactPermissionError(
            f"unknown artifact role {role!r}",
            category="preflight",
        )

    def set_member(self, member: ArtifactMember) -> None:
        if member.role == ROLE_MAIN:
            self.main = member
        elif member.role == ROLE_WAL:
            self.wal = member
        elif member.role == ROLE_SHM:
            self.shm = member
        else:
            raise ArtifactPermissionError(
                f"unknown artifact role {member.role!r}",
                category="preflight",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "captured_at_utc": self.captured_at_utc,
            "phase": self.phase,
            "main": self.main.to_dict(),
            "wal": self.wal.to_dict(),
            "shm": self.shm.to_dict(),
            "barrier_partial": self.barrier_partial,
            "members_changed": list(self.members_changed),
            "post_swap_main": self.post_swap_main,
            "post_swap_companions": self.post_swap_companions,
            "legacy_without_companions": self.legacy_without_companions,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> ArtifactPermissions | None:
        if not data:
            return None
        schema = int(data.get("schema") or ARTIFACT_PERMISSIONS_SCHEMA)
        if schema != ARTIFACT_PERMISSIONS_SCHEMA:
            raise ArtifactPermissionError(
                "artifact_permissions schema mismatch",
                category="ambiguous",
            )
        return cls(
            schema=schema,
            captured_at_utc=str(data.get("captured_at_utc") or _iso_now()),
            phase=str(data.get("phase") or "pre_swap"),
            main=ArtifactMember.from_dict(data.get("main"), role=ROLE_MAIN),
            wal=ArtifactMember.from_dict(data.get("wal"), role=ROLE_WAL),
            shm=ArtifactMember.from_dict(data.get("shm"), role=ROLE_SHM),
            barrier_partial=bool(data.get("barrier_partial")),
            members_changed=[str(x) for x in (data.get("members_changed") or [])],
            post_swap_main=(
                dict(data["post_swap_main"])
                if isinstance(data.get("post_swap_main"), dict)
                else None
            ),
            post_swap_companions=(
                dict(data["post_swap_companions"])
                if isinstance(data.get("post_swap_companions"), dict)
                else None
            ),
            legacy_without_companions=bool(data.get("legacy_without_companions")),
        )


def empty_absent_member(role: str, *, basename: str | None = None) -> ArtifactMember:
    return ArtifactMember(role=role, present=False, basename=basename)


def capture_member(adapters: ArtifactFs, path: Path, *, role: str) -> ArtifactMember:
    """Capture one member. Absent path → present=False. Symlink/non-regular fail."""
    basename = path.name
    if not adapters.path_exists(path):
        return empty_absent_member(role, basename=basename)
    if adapters.is_symlink(path):
        raise ArtifactPermissionError(
            f"refusing symlink artifact member ({role})",
            category="safety",
        )
    if not adapters.is_file(path):
        raise ArtifactPermissionError(
            f"refusing non-regular artifact member ({role})",
            category="safety",
        )
    ident = adapters.capture_member_identity(path)
    return ArtifactMember(
        role=role,
        present=True,
        basename=basename,
        device=int(ident["device"]),
        inode=int(ident["inode"]),
        mode=int(ident["mode"]),
        uid=int(ident["uid"]),
        gid=int(ident["gid"]),
        nlink=int(ident["nlink"]),
        size=int(ident["size"]),
    )


def capture_artifact(
    adapters: ArtifactFs,
    production: Path,
    *,
    phase: str = "pre_swap",
    refuse_journal_sidecar: bool = True,
) -> ArtifactPermissions:
    """Capture main + exact WAL/SHM. Unexpected ``-journal`` fails closed."""
    if refuse_journal_sidecar and adapters.path_exists(journal_path_sidecar(production)):
        if adapters.is_symlink(journal_path_sidecar(production)) or adapters.is_file(
            journal_path_sidecar(production)
        ):
            raise ArtifactPermissionError(
                "unexpected -journal sidecar during artifact capture",
                category="verify",
            )
    main = capture_member(adapters, production, role=ROLE_MAIN)
    if not main.present:
        raise ArtifactPermissionError(
            "production main database missing during artifact capture",
            category="safety",
        )
    wal = capture_member(adapters, wal_path(production), role=ROLE_WAL)
    shm = capture_member(adapters, shm_path(production), role=ROLE_SHM)
    return ArtifactPermissions(
        captured_at_utc=_iso_now(),
        phase=phase,
        main=main,
        wal=wal,
        shm=shm,
    )


def barrier_mode_for(original_mode: int) -> int:
    return int(original_mode) & ~0o222


def apply_barrier_to_member(
    adapters: ArtifactFs,
    production: Path,
    member: ArtifactMember,
    *,
    persist: Callable[[ArtifactPermissions], None] | None = None,
    artifact: ArtifactPermissions | None = None,
) -> ArtifactMember:
    """fchmod one present member to clear write bits; bind to captured identity.

    Journal truthfulness: once ``fchmod`` succeeds the mutation is recorded and
    persisted *before* the verification read, so a crash between the syscall and
    the durable journal update can still be recovered conservatively (the member
    is already marked ``barrier_applied`` / ``members_changed`` / partial).
    """
    if not member.present:
        return member
    if member.device is None or member.inode is None or member.mode is None:
        raise ArtifactPermissionError(
            f"incomplete captured identity for {member.role}",
            category="ambiguous",
        )
    path = member_path(production, member.role)
    target = barrier_mode_for(int(member.mode))

    def _persist() -> None:
        if artifact is not None and persist is not None:
            persist(artifact)

    try:
        adapters.chmod_verified_inode(
            path,
            target,
            expected_device=int(member.device),
            expected_inode=int(member.inode),
        )
        adapters.fsync_file(path)
    except Exception:
        # chmod_verified_inode fstat-matches device+inode before fchmod, so a
        # raise here means no mutation occurred on the captured identity.
        if artifact is not None:
            artifact.barrier_partial = True
        _persist()
        raise
    # fchmod succeeded on the captured device+inode. Record truthfully now.
    member.barrier_applied = True
    member.barrier_mode = target
    if artifact is not None:
        artifact.set_member(member)
        if member.role not in artifact.members_changed:
            artifact.members_changed.append(member.role)
        artifact.barrier_partial = True
    _persist()
    verified = adapters.capture_member_identity(path)
    if int(verified["mode"]) != target:
        if artifact is not None:
            artifact.barrier_partial = True
        _persist()
        raise ArtifactPermissionError(
            f"write barrier mode verification failed for {member.role}",
            category="verify",
            recovery=(
                "Partial barrier may be active. Run abort_before_swap or "
                "reconcile_permission_barrier before any writer resume."
            ),
        )
    if (
        int(verified["device"]) != int(member.device)
        or int(verified["inode"]) != int(member.inode)
    ):
        # The mode change already landed on the captured identity (recorded
        # above); surface the drift without discarding that record.
        raise ArtifactPermissionError(
            f"artifact member identity changed during barrier ({member.role})",
            category="safety",
        )
    return member


def apply_write_barrier(
    adapters: ArtifactFs,
    production: Path,
    artifact: ArtifactPermissions,
    *,
    persist: Callable[[ArtifactPermissions], None],
    inject: Callable[[str], None] | None = None,
) -> ArtifactPermissions:
    """Apply RO barrier to every present captured member; journal partial state."""
    adapters.fsync_dir(production.parent)
    for role in ARTIFACT_ROLES:
        member = artifact.member(role)
        if not member.present:
            continue
        if inject:
            inject(f"before_barrier_chmod_{role}")
        apply_barrier_to_member(
            adapters,
            production,
            member,
            artifact=artifact,
            persist=persist,
        )
        if role not in artifact.members_changed:
            artifact.members_changed.append(role)
        artifact.set_member(member)
        persist(artifact)
        if inject:
            inject(f"after_barrier_chmod_{role}")
            if role == ROLE_MAIN:
                # Compatibility alias used by the fail_after matrix.
                inject("after_production_chmod_barrier")
    # Detect companions created after capture (race).
    for role, path in (
        (ROLE_WAL, wal_path(production)),
        (ROLE_SHM, shm_path(production)),
    ):
        captured = artifact.member(role)
        exists_now = adapters.path_exists(path)
        if not captured.present and exists_now:
            artifact.barrier_partial = True
            persist(artifact)
            raise ArtifactPermissionError(
                f"companion appeared after capture ({role})",
                category="safety",
                recovery=(
                    "Abort or re-capture after quiesce. Do not chmod an "
                    "unjournaled companion identity."
                ),
            )
        if captured.present and not exists_now:
            artifact.barrier_partial = True
            persist(artifact)
            raise ArtifactPermissionError(
                f"companion disappeared after capture ({role})",
                category="safety",
            )
        if captured.present and exists_now:
            live = adapters.capture_member_identity(path)
            if (
                int(live["device"]) != int(captured.device or -1)
                or int(live["inode"]) != int(captured.inode or -1)
            ):
                artifact.barrier_partial = True
                persist(artifact)
                raise ArtifactPermissionError(
                    f"companion identity drift after capture ({role})",
                    category="safety",
                )
    artifact.barrier_partial = False
    persist(artifact)
    return artifact


def restore_member_writable(
    adapters: ArtifactFs,
    production: Path,
    member: ArtifactMember,
    *,
    target_mode: int | None = None,
) -> None:
    """Restore one member that this MID actually changed."""
    if not member.present or not member.barrier_applied:
        return
    if member.device is None or member.inode is None:
        raise ArtifactPermissionError(
            f"cannot restore {member.role}: missing identity",
            category="ambiguous",
        )
    mode = int(target_mode if target_mode is not None else (member.mode or 0))
    if mode & 0o222 == 0:
        raise ArtifactPermissionError(
            f"refusing restore of {member.role} to non-writable mode",
            category="safety",
        )
    path = member_path(production, member.role)
    if not adapters.path_exists(path):
        raise ArtifactPermissionError(
            f"cannot restore {member.role}: path absent",
            category="ambiguous",
        )
    adapters.chmod_verified_inode(
        path,
        mode,
        expected_device=int(member.device),
        expected_inode=int(member.inode),
    )
    adapters.fsync_file(path)
    verified = adapters.capture_member_identity(path)
    if int(verified["mode"]) != mode:
        raise ArtifactPermissionError(
            f"restore mode verification failed for {member.role}",
            category="verify",
            recovery=(
                "Barrier ownership remains. Do not clear barrier flags or "
                "resume writers until modes are verified."
            ),
        )


def record_post_swap_main(
    artifact: ArtifactPermissions,
    adapters: ArtifactFs,
    production: Path,
) -> ArtifactPermissions:
    """After RENAME_EXCHANGE: bind restore of main to the new production inode."""
    live = capture_member(adapters, production, role=ROLE_MAIN)
    if not live.present:
        raise ArtifactPermissionError(
            "production missing after swap",
            category="ambiguous",
        )
    artifact.phase = "post_swap"
    artifact.post_swap_main = {
        "basename": live.basename,
        "device": live.device,
        "inode": live.inode,
        "mode": live.mode,
        "uid": live.uid,
        "gid": live.gid,
        "nlink": live.nlink,
        "size": live.size,
        # Target writable mode remains the pre-swap main original_mode.
        "target_mode": artifact.main.mode,
    }
    return artifact


def recapture_post_swap_companions(
    artifact: ArtifactPermissions,
    adapters: ArtifactFs,
    production: Path,
    *,
    source: str,
) -> ArtifactPermissions:
    """Record companions currently at production paths after swap/smoke.

    Never copies pre-swap companion inode metadata onto a different inode.
    """
    companions: dict[str, Any] = {
        "captured_at": source,
        "captured_at_utc": _iso_now(),
        "wal": None,
        "shm": None,
    }
    for role, path in ((ROLE_WAL, wal_path(production)), (ROLE_SHM, shm_path(production))):
        member = capture_member(adapters, path, role=role)
        companions[role] = member.to_dict() if member.present else {"role": role, "present": False}
    artifact.post_swap_companions = companions
    return artifact


def assert_no_unexpected_journal(adapters: ArtifactFs, production: Path) -> None:
    jp = journal_path_sidecar(production)
    if adapters.path_exists(jp):
        raise ArtifactPermissionError(
            "unexpected -journal sidecar",
            category="verify",
        )


def verify_artifact_writable_for_resume(
    adapters: ArtifactFs,
    production: Path,
    artifact: ArtifactPermissions | None,
    *,
    legacy_original_mode: int | None,
) -> dict[str, Any]:
    """Fail closed if main or any present sidecar lacks write bits."""
    assert_no_unexpected_journal(adapters, production)
    evidence: dict[str, Any] = {"main_ok": False, "wal_ok": False, "shm_ok": False}

    # Authoritative, symlink/FIFO-safe read of the live main via fstat: a symlink
    # or non-regular file swapped at the production path fails closed here rather
    # than letting a followed path spoof writable bits before the mail pause lifts.
    main_mode = int(adapters.capture_member_identity(production)["mode"])
    if main_mode & 0o222 == 0:
        raise ArtifactPermissionError(
            "production main is not writable before mail resume",
            category="verify",
            evidence={"main_mode": main_mode},
        )
    if legacy_original_mode is not None and main_mode != int(legacy_original_mode):
        # Allow only exact intended mode when known.
        raise ArtifactPermissionError(
            "production main mode unexpected before mail resume",
            category="verify",
            evidence={"main_mode": main_mode},
        )
    evidence["main_ok"] = True

    for role in (ROLE_WAL, ROLE_SHM):
        path = member_path(production, role)
        if not adapters.path_exists(path):
            evidence[f"{role}_ok"] = True
            evidence[f"{role}_absent"] = True
            continue
        # Present companion: fstat-authoritative + symlink/non-regular fail-closed.
        mode = int(adapters.capture_member_identity(path)["mode"])
        if mode & 0o222 == 0:
            raise ArtifactPermissionError(
                f"production {role} is not writable before mail resume",
                category="verify",
                evidence={f"{role}_mode": mode},
                recovery=(
                    "Restore companion write bits before removing the mail pause. "
                    "Mirror must remain paused."
                ),
            )
        # Legacy journals: never invent companion ownership; if a companion is
        # present and RO we already failed. If present and writable, accept.
        if artifact is None or artifact.legacy_without_companions:
            evidence[f"{role}_ok"] = True
            evidence[f"{role}_legacy_accepted_writable"] = True
            continue
        evidence[f"{role}_ok"] = True
    return evidence


def restore_pre_swap_changed_members(
    adapters: ArtifactFs,
    production: Path,
    artifact: ArtifactPermissions,
    *,
    persist: Callable[[ArtifactPermissions], None] | None = None,
) -> None:
    """Restore every member this MID barrier-changed (abort_before_swap)."""
    for role in list(artifact.members_changed):
        member = artifact.member(role)
        try:
            restore_member_writable(adapters, production, member)
        except ArtifactPermissionError:
            artifact.barrier_partial = True
            if persist is not None:
                persist(artifact)
            raise
    adapters.fsync_dir(production.parent)


def restore_post_swap_for_writer_resume(
    adapters: ArtifactFs,
    production: Path,
    artifact: ArtifactPermissions,
    *,
    original_mode: int,
    inject: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Restore writable modes on the live production artifact after PoNR.

    Main is restored using ``post_swap_main`` identity when available.
    Companions:
    - if present and matching a barrier-applied pre-swap identity at the same
      device+inode, restore that original mode;
    - if present with a post_swap capture, require writable (or restore when
      the capture recorded an intended target_mode / barrier);
    - if present, RO, and no usable metadata → fail closed (do not invent).
    """
    post = artifact.post_swap_main
    if not post:
        raise ArtifactPermissionError(
            "post_swap_main identity missing; refuse writer mode restore",
            category="ambiguous",
            recovery=(
                "Re-run atomic_swap reconciliation / readonly_smoke before "
                "resume_writers_restore_mode."
            ),
        )
    target_mode = int(post.get("target_mode") or original_mode)
    if inject:
        inject("before_restore_main")
    adapters.chmod_verified_inode(
        production,
        target_mode,
        expected_device=int(post["device"]),
        expected_inode=int(post["inode"]),
    )
    adapters.fsync_file(production)
    if inject:
        inject("after_restore_chmod")
    verified = adapters.capture_member_identity(production)
    if int(verified["mode"]) != target_mode:
        raise ArtifactPermissionError(
            "writable mode restore verification failed for main",
            category="verify",
            recovery=(
                "Barrier ownership remains. Do not clear barrier flags or "
                "remove pauses until restore succeeds."
            ),
        )
    if (
        int(verified["device"]) != int(post["device"])
        or int(verified["inode"]) != int(post["inode"])
    ):
        raise ArtifactPermissionError(
            "production identity changed during writable restore",
            category="safety",
        )
    if inject:
        inject("after_restore_main")

    # Companions currently at production paths.
    for role in (ROLE_WAL, ROLE_SHM):
        path = member_path(production, role)
        if not adapters.path_exists(path):
            continue
        live = adapters.capture_member_identity(path)
        live_id = (int(live["device"]), int(live["inode"]))
        pre = artifact.member(role)
        if (
            pre.present
            and pre.barrier_applied
            and pre.identity_tuple() == live_id
            and pre.mode is not None
        ):
            if inject:
                inject(f"before_restore_{role}")
            restore_member_writable(adapters, production, pre)
            if inject:
                inject(f"after_restore_{role}")
            continue
        # New or different inode: must already be writable (SQLite-created).
        if int(live["mode"]) & 0o222 == 0:
            raise ArtifactPermissionError(
                f"production {role} is read-only and is not the barrier-captured "
                "identity; refusing to invent companion ownership",
                category="verify",
                evidence={
                    "role": role,
                    "live_mode": int(live["mode"]),
                    "legacy_without_companions": artifact.legacy_without_companions,
                },
                recovery=(
                    "Do not remove the mail pause. Inspect companion modes. "
                    "Mirror must remain paused."
                ),
            )

    adapters.fsync_dir(production.parent)
    return verify_artifact_writable_for_resume(
        adapters,
        production,
        artifact,
        legacy_original_mode=target_mode,
    )


def filesystem_capture_member_identity(path: Path) -> dict[str, Any]:
    """Real FS: open O_RDONLY|O_NOFOLLOW, fstat, refuse non-regular."""
    import stat as stat_mod

    try:
        if path.is_symlink():
            raise ArtifactPermissionError(
                "refusing symlink artifact member",
                category="safety",
            )
        st_link = os.lstat(path)
    except OSError as exc:
        raise ArtifactPermissionError(
            f"unable to inspect artifact member ({type(exc).__name__})",
            category="safety",
        ) from exc
    if not stat_mod.S_ISREG(st_link.st_mode):
        raise ArtifactPermissionError(
            "refusing non-regular artifact member",
            category="safety",
        )
    # lstat above is only an early rejection: the pathname can change between
    # lstat and open. O_NOFOLLOW refuses a symlink swapped in; O_NONBLOCK keeps
    # a FIFO/device swapped in from blocking open(); O_CLOEXEC avoids FD leaks.
    # The authoritative identity/type is taken from fstat on the opened FD.
    flags = os.O_RDONLY
    for _flag_name in ("O_NOFOLLOW", "O_CLOEXEC", "O_NONBLOCK"):
        flags |= getattr(os, _flag_name, 0)
    try:
        fd = os.open(str(path), flags)
    except OSError as exc:
        raise ArtifactPermissionError(
            f"unable to open artifact member ({type(exc).__name__})",
            category="safety",
        ) from exc
    try:
        st = os.fstat(fd)
        if not stat_mod.S_ISREG(st.st_mode):
            raise ArtifactPermissionError(
                "refusing non-regular artifact member (fstat)",
                category="safety",
            )
        return {
            "basename": path.name,
            "device": int(st.st_dev),
            "inode": int(st.st_ino),
            "mode": int(st.st_mode) & 0o7777,
            "uid": int(st.st_uid),
            "gid": int(st.st_gid),
            "nlink": int(st.st_nlink),
            "size": int(st.st_size),
        }
    finally:
        os.close(fd)


def reconcile_artifact_barrier_state(
    adapters: ArtifactFs,
    production: Path,
    artifact: ArtifactPermissions | None,
    *,
    barrier_active_flag: bool,
    permission_intent: dict[str, Any] | None,
    writer_resume_started: bool,
    legacy_original_mode: int | None,
) -> dict[str, Any]:
    """Inspect main (+ companions when journaled) after chmod crash."""
    state: dict[str, Any] = {
        "barrier_active_flag": barrier_active_flag,
        "permission_intent": permission_intent,
        "artifact_present": artifact is not None,
        "barrier_partial": bool(artifact.barrier_partial) if artifact else False,
        "recognized": None,
        "safe_action": None,
        "members": {},
    }
    if writer_resume_started:
        state["recognized"] = "ponr_reached"
        state["safe_action"] = "never_auto_rollback_inspect_manually"
        return state

    main_mode = int(adapters.get_file_mode_owner(production)["mode"])
    write_bits = bool(main_mode & 0o222)
    state["current_main_mode"] = main_mode
    state["main_write_bits_present"] = write_bits

    if artifact is not None:
        for role in ARTIFACT_ROLES:
            path = member_path(production, role)
            if not adapters.path_exists(path):
                state["members"][role] = {"present": False}
                continue
            mode = int(adapters.get_file_mode_owner(path)["mode"])
            state["members"][role] = {
                "present": True,
                "mode": mode,
                "write_bits": bool(mode & 0o222),
                "barrier_applied": artifact.member(role).barrier_applied,
            }

    if not write_bits and (
        legacy_original_mode is not None
        or (artifact is not None and artifact.main.mode is not None)
    ):
        if barrier_active_flag:
            state["recognized"] = "barrier_active_ro"
            state["safe_action"] = "continue_or_abort_before_swap"
        elif permission_intent or (artifact and artifact.barrier_partial):
            state["recognized"] = "chmod_applied_journal_incomplete"
            state["safe_action"] = (
                "abort_before_swap_to_restore_or_complete_barrier_stage"
            )
        else:
            state["recognized"] = "unexpected_readonly"
            state["safe_action"] = "abort_before_swap_or_manual_chmod_restore"
        return state
    if write_bits and barrier_active_flag:
        state["recognized"] = "barrier_flag_stale_file_writable"
        state["safe_action"] = "manual_inspect_refuse_blind_retry"
        return state
    state["recognized"] = "writable_no_barrier"
    state["safe_action"] = "continue_if_pre_barrier_else_inspect"
    return state


def legacy_artifact_from_original_mode(
    *,
    original_mode: int,
    production_device: int | None,
    production_inode: int | None,
    original_uid: int | None = None,
    original_gid: int | None = None,
) -> ArtifactPermissions:
    """Build a main-only artifact view for legacy journals (no companion invent)."""
    return ArtifactPermissions(
        phase="pre_swap",
        main=ArtifactMember(
            role=ROLE_MAIN,
            present=True,
            device=production_device,
            inode=production_inode,
            mode=int(original_mode),
            uid=original_uid,
            gid=original_gid,
            barrier_applied=True,
            barrier_mode=barrier_mode_for(int(original_mode)),
        ),
        wal=empty_absent_member(ROLE_WAL),
        shm=empty_absent_member(ROLE_SHM),
        members_changed=[ROLE_MAIN],
        legacy_without_companions=True,
    )


def load_artifact_permissions(
    raw: Mapping[str, Any] | None,
) -> ArtifactPermissions | None:
    return ArtifactPermissions.from_dict(raw)


def dump_artifact_permissions(
    artifact: ArtifactPermissions | None,
) -> dict[str, Any] | None:
    if artifact is None:
        return None
    return artifact.to_dict()
