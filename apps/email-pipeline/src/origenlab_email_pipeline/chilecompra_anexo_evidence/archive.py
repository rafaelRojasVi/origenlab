"""Bounded, in-memory ZIP preflight and member iteration.

Nothing is written to disk and nothing is executed; members are read through a
capped stream so a lying uncompressed-size header cannot bypass the budget.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field

from origenlab_email_pipeline.chilecompra_anexo_evidence.constants import (
    DEFAULT_MAX_ARCHIVE_COMPRESSION_RATIO,
    DEFAULT_MAX_ARCHIVE_DEPTH,
    DEFAULT_MAX_ARCHIVE_MEMBER_UNCOMPRESSED_BYTES,
    DEFAULT_MAX_ARCHIVE_MEMBERS,
    DEFAULT_MAX_ARCHIVE_TOTAL_UNCOMPRESSED_BYTES,
    REASON_ARCHIVE_BYTES_LIMIT,
    REASON_ARCHIVE_MEMBER_LIMIT,
    REASON_ARCHIVE_RATIO_LIMIT,
)


class ArchiveSafetyError(ValueError):
    """The archive violated a hard safety bound and must not be expanded."""


@dataclass(frozen=True)
class ArchiveLimits:
    max_members: int = DEFAULT_MAX_ARCHIVE_MEMBERS
    max_total_uncompressed_bytes: int = DEFAULT_MAX_ARCHIVE_TOTAL_UNCOMPRESSED_BYTES
    max_member_uncompressed_bytes: int = DEFAULT_MAX_ARCHIVE_MEMBER_UNCOMPRESSED_BYTES
    max_compression_ratio: float = DEFAULT_MAX_ARCHIVE_COMPRESSION_RATIO
    max_depth: int = DEFAULT_MAX_ARCHIVE_DEPTH


@dataclass
class ArchiveMemberPayload:
    """One safely extracted member plus whatever we had to note about it."""

    member_path: str
    payload: bytes
    warnings: tuple[str, ...] = ()
    encrypted: bool = False
    truncated: bool = False


@dataclass
class ArchivePreflight:
    """Declared-structure review performed before any member is decompressed."""

    member_count: int
    declared_uncompressed_bytes: int
    compressed_bytes: int
    encrypted_member_count: int
    rejected: bool = False
    reason_codes: tuple[str, ...] = ()
    warnings: list[str] = field(default_factory=list)


def is_unsafe_member_path(name: str) -> bool:
    """Reject traversal, absolute paths, and Windows drive-qualified members."""
    candidate = (name or "").replace("\\", "/")
    if not candidate or candidate.endswith("/"):
        return False
    if candidate.startswith("/"):
        return True
    if len(candidate) > 1 and candidate[1] == ":":
        return True
    return any(part == ".." for part in candidate.split("/"))


def preflight_archive(payload: bytes, *, limits: ArchiveLimits) -> ArchivePreflight:
    """Inspect the central directory only; never decompress during preflight."""
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            infos = [i for i in archive.infolist() if not i.is_dir()]
    except zipfile.BadZipFile as exc:
        raise ArchiveSafetyError(f"unreadable zip container: {exc}") from exc

    declared = sum(int(i.file_size) for i in infos)
    compressed = sum(int(i.compress_size) for i in infos)
    encrypted = sum(1 for i in infos if i.flag_bits & 0x1)

    reasons: list[str] = []
    warnings: list[str] = []
    if len(infos) > limits.max_members:
        reasons.append(REASON_ARCHIVE_MEMBER_LIMIT)
    if declared > limits.max_total_uncompressed_bytes:
        reasons.append(REASON_ARCHIVE_BYTES_LIMIT)
    if any(int(i.file_size) > limits.max_member_uncompressed_bytes for i in infos):
        reasons.append(REASON_ARCHIVE_BYTES_LIMIT)
    if compressed > 0 and declared / compressed > limits.max_compression_ratio:
        reasons.append(REASON_ARCHIVE_RATIO_LIMIT)
    if encrypted:
        warnings.append(f"encrypted_members:{encrypted}")
    unsafe = [i.filename for i in infos if is_unsafe_member_path(i.filename)]
    if unsafe:
        warnings.append(f"unsafe_member_paths:{len(unsafe)}")

    return ArchivePreflight(
        member_count=len(infos),
        declared_uncompressed_bytes=declared,
        compressed_bytes=compressed,
        encrypted_member_count=encrypted,
        rejected=bool(reasons),
        reason_codes=tuple(sorted(set(reasons))),
        warnings=warnings,
    )


def _read_member_bounded(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    *,
    max_bytes: int,
) -> tuple[bytes, bool]:
    """Read at most ``max_bytes`` + 1 so an understated header still gets caught."""
    with archive.open(info) as handle:
        data = handle.read(max_bytes + 1)
    if len(data) > max_bytes:
        return data[:max_bytes], True
    return data, False


def iter_archive_members(
    payload: bytes,
    *,
    limits: ArchiveLimits,
    remaining_budget_bytes: int | None = None,
) -> list[ArchiveMemberPayload]:
    """Return safe member payloads in deterministic (sorted-path) order.

    Directory entries, traversal paths, and encrypted members never yield bytes;
    each still produces a record so the caller can account for them.
    """
    results: list[ArchiveMemberPayload] = []
    budget = remaining_budget_bytes
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            infos = [i for i in archive.infolist() if not i.is_dir()]
            infos.sort(key=lambda i: (i.filename, i.header_offset))
            seen_paths: dict[str, int] = {}
            for info in infos[: limits.max_members]:
                name = info.filename
                if is_unsafe_member_path(name):
                    results.append(
                        ArchiveMemberPayload(
                            member_path=name,
                            payload=b"",
                            warnings=("unsafe_member_path",),
                        )
                    )
                    continue
                occurrence = seen_paths.get(name, 0)
                seen_paths[name] = occurrence + 1
                display_path = name if occurrence == 0 else f"{name}#{occurrence + 1}"
                member_warnings: list[str] = []
                if occurrence:
                    member_warnings.append("duplicate_member_path")
                if info.flag_bits & 0x1:
                    results.append(
                        ArchiveMemberPayload(
                            member_path=display_path,
                            payload=b"",
                            warnings=tuple([*member_warnings, "encrypted_member"]),
                            encrypted=True,
                        )
                    )
                    continue
                cap = limits.max_member_uncompressed_bytes
                if budget is not None:
                    cap = min(cap, max(budget, 0))
                if cap <= 0:
                    results.append(
                        ArchiveMemberPayload(
                            member_path=display_path,
                            payload=b"",
                            warnings=tuple([*member_warnings, REASON_ARCHIVE_BYTES_LIMIT]),
                            truncated=True,
                        )
                    )
                    continue
                try:
                    data, truncated = _read_member_bounded(archive, info, max_bytes=cap)
                except (zipfile.BadZipFile, RuntimeError, OSError, EOFError) as exc:
                    results.append(
                        ArchiveMemberPayload(
                            member_path=display_path,
                            payload=b"",
                            warnings=tuple(
                                [*member_warnings, f"member_unreadable:{type(exc).__name__}"]
                            ),
                        )
                    )
                    continue
                if truncated:
                    member_warnings.append(REASON_ARCHIVE_BYTES_LIMIT)
                if budget is not None:
                    budget -= len(data)
                results.append(
                    ArchiveMemberPayload(
                        member_path=display_path,
                        payload=data,
                        warnings=tuple(member_warnings),
                        truncated=truncated,
                    )
                )
    except zipfile.BadZipFile as exc:
        raise ArchiveSafetyError(f"unreadable zip container: {exc}") from exc
    return results


def archive_depth_exceeded(depth: int, *, limits: ArchiveLimits) -> bool:
    return depth > limits.max_depth


__all__ = [
    "ArchiveLimits",
    "ArchiveMemberPayload",
    "ArchivePreflight",
    "ArchiveSafetyError",
    "DEFAULT_MAX_ARCHIVE_DEPTH",
    "archive_depth_exceeded",
    "is_unsafe_member_path",
    "iter_archive_members",
    "preflight_archive",
]
