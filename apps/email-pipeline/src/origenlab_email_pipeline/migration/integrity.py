"""Verification shared by every read-only consumer of a migration evidence bundle.

One implementation of "is this artifact the one it claims to be, and could
anyone else have altered it", used by both the cross-wave reconciliation
(:mod:`cross_wave_safety`) and the permission hardening
(:mod:`artifact_permissions`). Duplicating these rules would let the two drift,
and a drifted integrity check is worse than none.

Every message here carries a relative file name and a count — never an address,
a domain, an absolute path or any other datum from the artifact. Callers
re-raise :class:`ArtifactIntegrityError` as their own refusal type so their
public exception contract stays theirs.
"""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, TypeVar

from origenlab_email_pipeline.migration.bundle import PRIVATE_DIR_MODE, PRIVATE_FILE_MODE

#: Files a bundle's own ``SHA256SUMS`` is allowed not to list. ``bundle.write_sha256sums``
#: deliberately leaves ``manifest.json`` out, because it carries the run's wall clock.
SHA256SUMS_EXEMPT = frozenset({"SHA256SUMS", "manifest.json"})

_T = TypeVar("_T")


class ArtifactIntegrityError(Exception):
    """An artifact failed verification. Messages never carry artifact data."""


def as_refusal(refuse: type[Exception], fn: Callable[..., _T], *args: Any, **kwargs: Any) -> _T:
    """Run an integrity check, re-raising its failure as the caller's refusal type."""
    try:
        return fn(*args, **kwargs)
    except ArtifactIntegrityError as exc:
        raise refuse(str(exc)) from None


def sha256_file(path: Path) -> str:
    """SHA-256 of a file, streamed so a large archive never lands in memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_paths(root: Path) -> Iterator[Path]:
    """Every path under ``root``, sorted, so results are deterministic."""
    yield from sorted(Path(root).rglob("*"))


def assert_not_symlink(path: Path, label: str) -> None:
    if Path(path).is_symlink():
        raise ArtifactIntegrityError(
            f"{label} is a symbolic link; an evidence artifact is used only at its real path"
        )


def assert_source_safe(path: Path, label: str) -> None:
    """Refuse a path another account could have tampered with.

    Group- or world-*writable* is the tampering risk and refuses. Being merely
    readable by others is a privacy weakness, reported separately by
    :func:`observe_modes` rather than refused — the Wave 1A bundle predates the
    owner-only construction policy, and refusing it outright would make the
    historical evidence unusable rather than safer.
    """
    assert_not_symlink(path, label)
    if not path.exists():
        raise ArtifactIntegrityError(f"{label} does not exist")
    info = path.lstat()
    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
        raise ArtifactIntegrityError(
            f"{label} is neither a directory nor a regular file"
        )
    if info.st_uid != os.getuid():
        raise ArtifactIntegrityError(
            f"{label} is not owned by the current user; refusing to act on evidence "
            "another account controls"
        )
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ArtifactIntegrityError(
            f"{label} is group- or world-writable; the evidence could have been altered"
        )


def observe_modes(root: Path, label: str) -> dict[str, Any]:
    """Tamper-check every path under ``root`` and record whether it is owner-only."""
    assert_source_safe(root, label)
    wider_dirs = 0
    wider_files = 0
    for node in iter_paths(root):
        assert_not_symlink(node, f"{label} contains a symbolic link")
        assert_source_safe(node, f"a path inside {label}")
        mode = stat.S_IMODE(node.lstat().st_mode)
        if node.is_dir():
            if mode != PRIVATE_DIR_MODE:
                wider_dirs += 1
        elif mode != PRIVATE_FILE_MODE:
            wider_files += 1

    root_mode = stat.S_IMODE(root.lstat().st_mode)
    return {
        "root_mode": f"0o{root_mode:03o}",
        "directories_wider_than_0700": wider_dirs,
        "files_wider_than_0600": wider_files,
        "private_mode_ok": root_mode == PRIVATE_DIR_MODE
        and wider_dirs == 0
        and wider_files == 0,
        "tamper_checks": "not a symlink, owned by the running user, not group- or world-writable",
    }


def parse_sha256sums(text: str, label: str) -> dict[str, str]:
    """Parse a ``sha256sum`` listing, refusing a malformed or repeating one."""
    listed: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        digest, _, name = line.partition("  ")
        digest = digest.strip()
        name = name.strip()
        if len(digest) != 64 or not name:
            raise ArtifactIntegrityError(f"{label}/SHA256SUMS has a malformed line")
        if name in listed:
            raise ArtifactIntegrityError(f"{label}/SHA256SUMS lists {name} twice")
        listed[name] = digest
    if not listed:
        raise ArtifactIntegrityError(f"{label}/SHA256SUMS is empty")
    return listed


def verify_bundle_checksums(bundle: Path, label: str) -> dict[str, str]:
    """Verify every file of a bundle against its own ``SHA256SUMS``.

    A bundle file the checksum list does not mention refuses: an artifact that
    grew a file nobody hashed is not the artifact it claims to be.
    """
    if not bundle.is_dir():
        raise ArtifactIntegrityError(f"{label} is not a directory")
    sums = bundle / "SHA256SUMS"
    if not sums.is_file():
        raise ArtifactIntegrityError(
            f"{label} has no SHA256SUMS; integrity cannot be verified"
        )

    listed = parse_sha256sums(sums.read_text(encoding="utf-8"), label)
    present = {
        node.relative_to(bundle).as_posix() for node in iter_paths(bundle) if node.is_file()
    }
    unlisted = sorted(present - set(listed) - SHA256SUMS_EXEMPT)
    if unlisted:
        raise ArtifactIntegrityError(
            f"{label} holds {len(unlisted)} file(s) its SHA256SUMS does not list; "
            "the bundle is not the one it claims to be"
        )

    verified: dict[str, str] = {}
    for rel, expected in sorted(listed.items()):
        target = bundle / rel
        if not target.is_file():
            raise ArtifactIntegrityError(f"{label} is missing {rel}")
        actual = sha256_file(target)
        if actual != expected:
            raise ArtifactIntegrityError(
                f"SHA256SUMS mismatch for {label}/{rel}; the bundle is not the one "
                "it claims to be"
            )
        verified[rel] = actual
    return verified


def verify_sidecar(target: Path, sidecar: Path, label: str) -> str:
    """Verify a ``<file>.sha256`` sidecar and return the confirmed digest."""
    assert_source_safe(target, label)
    assert_source_safe(sidecar, f"{label} sidecar")
    if not target.is_file():
        raise ArtifactIntegrityError(f"{label} is not a regular file")
    if not sidecar.is_file():
        raise ArtifactIntegrityError(f"{label} has no .sha256 sidecar")
    listed = parse_sha256sums(sidecar.read_text(encoding="utf-8"), label)
    if target.name not in listed:
        raise ArtifactIntegrityError(f"the {label} sidecar does not name {target.name}")
    actual = sha256_file(target)
    if actual != listed[target.name]:
        raise ArtifactIntegrityError(f"{label} does not match its .sha256 sidecar")
    return actual


def assert_recorded(actual: str, expected: str, label: str) -> None:
    """Compare a measured hash with the value ``docs/DATA.md`` pins for it."""
    if actual != expected:
        raise ArtifactIntegrityError(
            f"{label} is not the value docs/DATA.md records; Wave 1A is immutable "
            "and its hashes never change"
        )
