"""Bounded, in-memory ZIP preflight and member iteration.

Nothing is written to disk and nothing is executed; members are read through a
capped stream so a lying uncompressed-size header cannot bypass the budget.
"""

from __future__ import annotations

import io
import struct
import zlib
import zipfile
from dataclasses import dataclass, field

from origenlab_email_pipeline.chilecompra_anexo_evidence.constants import (
    DEFAULT_ARCHIVE_VERIFY_CHUNK_BYTES,
    DEFAULT_MAX_ARCHIVE_COMPRESSION_RATIO,
    DEFAULT_MAX_ARCHIVE_DEPTH,
    DEFAULT_MAX_ARCHIVE_MEMBER_UNCOMPRESSED_BYTES,
    DEFAULT_MAX_ARCHIVE_MEMBERS,
    DEFAULT_MAX_ARCHIVE_TOTAL_UNCOMPRESSED_BYTES,
    REASON_ARCHIVE_ACTUAL_MEMBER_BYTES_LIMIT,
    REASON_ARCHIVE_ACTUAL_RATIO_LIMIT,
    REASON_ARCHIVE_ACTUAL_SIZE_MISMATCH,
    REASON_ARCHIVE_ACTUAL_TOTAL_BYTES_LIMIT,
    REASON_ARCHIVE_BYTES_LIMIT,
    REASON_ARCHIVE_MEMBER_LIMIT,
    REASON_ARCHIVE_RATIO_LIMIT,
    REASON_ARCHIVE_SUSPICIOUS_MEMBER,
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
    worst_member_ratio: float = 0.0


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

    # A single bomb member can hide behind ordinary members that dilute the
    # whole-archive ratio, so every member is also checked on its own.
    worst_ratio = 0.0
    for info in infos:
        size = int(info.file_size)
        if size <= 0:
            continue
        member_compressed = int(info.compress_size)
        if member_compressed <= 0:
            reasons.append(REASON_ARCHIVE_SUSPICIOUS_MEMBER)
            continue
        ratio = size / member_compressed
        worst_ratio = max(worst_ratio, ratio)
        if ratio > limits.max_compression_ratio:
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
        worst_member_ratio=worst_ratio,
    )


@dataclass
class ArchiveActualExpansion:
    """What the members really decompress to, independent of their headers."""

    members_verified: int
    actual_total_bytes: int
    largest_member_bytes: int
    worst_actual_ratio: float
    rejected: bool = False
    reason_codes: tuple[str, ...] = ()
    offending_member: str | None = None


def _member_data_offset(payload: bytes, info: zipfile.ZipInfo) -> int | None:
    """Locate a member's raw compressed bytes via its local header."""
    start = int(info.header_offset)
    if start < 0 or start + 30 > len(payload):
        return None
    if bytes(payload[start : start + 4]) != b"PK\x03\x04":
        return None
    name_len, extra_len = struct.unpack_from("<HH", payload, start + 26)
    offset = start + 30 + name_len + extra_len
    return offset if offset <= len(payload) else None


def _actual_member_bytes(
    payload: bytes,
    info: zipfile.ZipInfo,
    *,
    abort_above: int,
    chunk_bytes: int,
) -> tuple[int, bool]:
    """Stream one member and count true output bytes.

    Decompresses the raw deflate stream directly rather than through
    ``ZipExtFile``, which stops at the *declared* size and would therefore never
    reveal an understated header. Returns ``(actual_bytes, aborted)`` and holds
    at most one chunk of output at a time.
    """
    compressed = int(info.compress_size)
    data_start = _member_data_offset(payload, info)
    if data_start is None or data_start + max(compressed, 0) > len(payload):
        return 0, False

    if info.compress_type == zipfile.ZIP_STORED:
        return max(compressed, 0), False

    if info.compress_type != zipfile.ZIP_DEFLATED:
        # Rare in OOXML; fall back to bounded reads through zipfile.
        actual = 0
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive, archive.open(info) as handle:
                while True:
                    block = handle.read(chunk_bytes)
                    if not block:
                        break
                    actual += len(block)
                    if actual > abort_above:
                        return actual, True
        except (zipfile.BadZipFile, RuntimeError, OSError, EOFError, NotImplementedError):
            return actual, False
        return actual, False

    view = memoryview(payload)
    decompressor = zlib.decompressobj(-zlib.MAX_WBITS)
    actual = 0
    position = data_start
    end = data_start + compressed
    try:
        while position < end:
            block = view[position : min(position + chunk_bytes, end)]
            position += len(block)
            out = decompressor.decompress(block, chunk_bytes)
            actual += len(out)
            if actual > abort_above:
                return actual, True
            while decompressor.unconsumed_tail:
                out = decompressor.decompress(decompressor.unconsumed_tail, chunk_bytes)
                actual += len(out)
                if actual > abort_above:
                    return actual, True
    except zlib.error:
        # A stream we cannot decode is handled by the parser's corrupt path.
        return actual, False
    finally:
        view.release()
    return actual, False


def verify_actual_expansion(
    payload: bytes,
    *,
    limits: ArchiveLimits,
    chunk_bytes: int = DEFAULT_ARCHIVE_VERIFY_CHUNK_BYTES,
) -> ArchiveActualExpansion:
    """Bound what an archive *really* expands to before a parser touches it.

    ``preflight_archive`` can only trust the central directory. This walks the
    real decompressed streams in fixed-size chunks, so a member that understates
    its size is caught before any parser performs its own unbounded read.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            infos = [i for i in archive.infolist() if not i.is_dir()]
    except zipfile.BadZipFile as exc:
        raise ArchiveSafetyError(f"unreadable zip container: {exc}") from exc

    reasons: list[str] = []
    offender: str | None = None
    total = 0
    largest = 0
    worst_ratio = 0.0
    verified = 0

    def _flag(code: str, member: str) -> None:
        nonlocal offender
        reasons.append(code)
        if offender is None:
            offender = member

    for info in infos:
        if info.flag_bits & 0x1:
            continue  # encrypted members are refused elsewhere; never decoded here
        compressed = int(info.compress_size)
        remaining_total = max(limits.max_total_uncompressed_bytes - total, 0)
        abort_above = min(limits.max_member_uncompressed_bytes, remaining_total)

        actual, aborted = _actual_member_bytes(
            payload, info, abort_above=abort_above, chunk_bytes=chunk_bytes
        )
        verified += 1
        total += actual
        largest = max(largest, actual)

        if aborted:
            if actual > limits.max_member_uncompressed_bytes:
                _flag(REASON_ARCHIVE_ACTUAL_MEMBER_BYTES_LIMIT, info.filename)
            else:
                _flag(REASON_ARCHIVE_ACTUAL_TOTAL_BYTES_LIMIT, info.filename)
            break

        if actual > limits.max_member_uncompressed_bytes:
            _flag(REASON_ARCHIVE_ACTUAL_MEMBER_BYTES_LIMIT, info.filename)
        if total > limits.max_total_uncompressed_bytes:
            _flag(REASON_ARCHIVE_ACTUAL_TOTAL_BYTES_LIMIT, info.filename)
        if actual > 0:
            if compressed <= 0:
                _flag(REASON_ARCHIVE_SUSPICIOUS_MEMBER, info.filename)
            else:
                ratio = actual / compressed
                worst_ratio = max(worst_ratio, ratio)
                if ratio > limits.max_compression_ratio:
                    _flag(REASON_ARCHIVE_ACTUAL_RATIO_LIMIT, info.filename)
        # A truthful archive declares exactly what it expands to.
        if actual != int(info.file_size):
            _flag(REASON_ARCHIVE_ACTUAL_SIZE_MISMATCH, info.filename)

    return ArchiveActualExpansion(
        members_verified=verified,
        actual_total_bytes=total,
        largest_member_bytes=largest,
        worst_actual_ratio=worst_ratio,
        rejected=bool(reasons),
        reason_codes=tuple(sorted(set(reasons))),
        offending_member=offender,
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
    "ArchiveActualExpansion",
    "ArchiveLimits",
    "ArchiveMemberPayload",
    "ArchivePreflight",
    "ArchiveSafetyError",
    "DEFAULT_MAX_ARCHIVE_DEPTH",
    "archive_depth_exceeded",
    "is_unsafe_member_path",
    "iter_archive_members",
    "preflight_archive",
    "verify_actual_expansion",
]
