"""Deterministic, content-addressed output for a migration evidence bundle.

Policy enforced here, not by the caller:

* the operator names the output directory explicitly — there is no default, and
  in particular no silent fall back to a production or repository path;
* a directory inside a Git working tree is refused outright, so extracted rows
  can never be staged or committed by accident;
* an existing bundle directory or archive is a collision and fails closed. The
  repository has no established safe-replacement mechanism for evidence
  bundles, so nothing is ever overwritten;
* the artifact carries real contact addresses, so **every** path this module
  creates is owner-only: directories ``0700``, files ``0600``, tar members
  ``0600``. No file is ever visible at a wider mode, not even briefly: content
  is written to an owner-only temporary file created with ``O_EXCL`` in the
  destination directory and then atomically renamed into place;
* every data file is written with fixed separators, sorted object keys and
  ``\\n`` line endings, so two runs over the same snapshot with the same
  arguments produce byte-identical files, a byte-identical ``SHA256SUMS`` and a
  byte-identical archive.

**Existing output root policy — tighten, never widen, never recurse.** When
the operator names a directory that already exists it is refused if it is a
symlink, if it is not owned by the current user, or if it is group- or
world-writable (anyone else could have planted content inside it). Otherwise
the root itself — and only the root — is tightened to ``0700``. This module
never chmods anything it did not create apart from that single directory, and
never walks into existing content.

Wall-clock values are confined to the manifest's ``run`` block, which
:func:`manifest_content_digest` deliberately excludes — the digest that
identifies *what was extracted* does not move because the clock did.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import stat
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Owner-only. The bundle holds real contact addresses; nothing in it is ever
#: readable by the group or by other users, at any point in its construction.
PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


class OutputPathError(Exception):
    """An output location was refused. Messages never carry an absolute path."""


@dataclass(frozen=True)
class BundleFile:
    """One written file and the facts ``SHA256SUMS`` and the manifest record."""

    relpath: str
    sha256: str
    bytes: int
    kind: str
    rows: int | None = None

    def to_manifest_entry(self) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "bytes": self.bytes,
            "kind": self.kind,
            "path": self.relpath,
            "sha256": self.sha256,
        }
        if self.rows is not None:
            entry["rows"] = self.rows
        return entry


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def assert_output_dir_allowed(output_dir: Path) -> Path:
    """Refuse an output directory that sits inside a Git working tree.

    ``.git`` is checked as both a directory (an ordinary clone) and a file (a
    worktree or submodule), from the target upwards to the filesystem root.

    Permission safety for an *existing* root is :func:`assert_output_root_private`;
    this function is the Git check alone, so callers that only need to validate a
    location keep their current behaviour.
    """
    candidate = Path(output_dir).expanduser()
    if candidate.is_symlink():
        raise OutputPathError(
            "output directory is a symbolic link; name the real directory instead"
        )
    resolved = candidate.resolve()
    for node in (resolved, *resolved.parents):
        marker = node / ".git"
        if marker.is_dir() or marker.is_file():
            raise OutputPathError(
                "output directory is inside a Git working tree; "
                "choose a location outside every repository"
            )
    return resolved


def assert_output_root_private(output_dir: Path) -> Path:
    """Validate the Git rule, then make the output root owner-only.

    An existing root is refused when it is a symlink, is not owned by the
    current user, or is group- or world-writable. An existing root that passes
    is tightened to ``0700`` — the root directory itself and nothing inside it.
    A missing root is created ``0700``, along with any parent this call has to
    create.
    """
    resolved = assert_output_dir_allowed(output_dir)

    if resolved.exists():
        if not resolved.is_dir():
            raise OutputPathError("the output root exists and is not a directory")
        info = resolved.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise OutputPathError(
                "output directory is a symbolic link; name the real directory instead"
            )
        if info.st_uid != os.getuid():
            raise OutputPathError(
                "the output root is not owned by the current user; "
                "an evidence bundle is written only into the operator's own directory"
            )
        if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise OutputPathError(
                "the output root is group- or world-writable; refusing rather than "
                "tightening a directory other users could already have written into"
            )
        os.chmod(resolved, PRIVATE_DIR_MODE)
        return resolved

    resolved.mkdir(parents=True, mode=PRIVATE_DIR_MODE)
    # mkdir's mode is masked by the umask; set it explicitly.
    os.chmod(resolved, PRIVATE_DIR_MODE)
    return resolved


def create_private_dir(target: Path) -> Path:
    """Create ``target`` (and its parents) owner-only, never following a symlink."""
    path = Path(target)
    if path.is_symlink():
        raise OutputPathError("refusing to write through a symbolic link")
    path.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    os.chmod(path, PRIVATE_DIR_MODE)
    return path


def write_private_bytes(target: Path, payload: bytes) -> Path:
    """Write ``payload`` to ``target`` at ``0600``, atomically and never permissive.

    The temporary file is created in the destination directory with ``O_EXCL``
    and mode ``0600``, so the content never exists at a wider mode and a partial
    file never appears under the final name. ``os.replace`` is atomic within one
    filesystem.
    """
    path = Path(target)
    if path.is_symlink():
        raise OutputPathError("refusing to write through a symbolic link")
    create_private_dir(path.parent)
    tmp = path.with_name(f".{path.name}.tmp")
    if tmp.exists():
        raise OutputPathError(f"a stale temporary file blocks {path.name}")
    fd = os.open(
        tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, PRIVATE_FILE_MODE
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        os.chmod(tmp, PRIVATE_FILE_MODE)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return path


def assert_no_collision(target: Path) -> None:
    """Refuse to write where something already exists."""
    if Path(target).exists():
        raise OutputPathError(
            f"output path already exists: {Path(target).name}; "
            "bundles are immutable and are never replaced in place"
        )


def _write_bytes(bundle_dir: Path, relpath: str, payload: bytes) -> None:
    target = Path(bundle_dir) / relpath
    create_private_dir(target.parent)
    assert_no_collision(target)
    write_private_bytes(target, payload)


def _canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")


def write_jsonl(
    bundle_dir: Path,
    relpath: str,
    rows: list[dict[str, Any]],
    *,
    kind: str,
) -> BundleFile:
    """Write one JSON object per line, deterministically, and hash the result."""
    payload = b"".join(_canonical_json_bytes(row) + b"\n" for row in rows)
    _write_bytes(bundle_dir, relpath, payload)
    return BundleFile(
        relpath=relpath,
        sha256=_sha256_bytes(payload),
        bytes=len(payload),
        kind=kind,
        rows=len(rows),
    )


def write_json(bundle_dir: Path, relpath: str, payload: Any, *, kind: str) -> BundleFile:
    """Write one canonical JSON document and hash the result."""
    body = _canonical_json_bytes(payload) + b"\n"
    _write_bytes(bundle_dir, relpath, body)
    return BundleFile(relpath=relpath, sha256=_sha256_bytes(body), bytes=len(body), kind=kind)


def write_text(bundle_dir: Path, relpath: str, text: str, *, kind: str) -> BundleFile:
    """Write a fixed text file (the bundle README) and hash the result."""
    payload = text.encode("utf-8")
    _write_bytes(bundle_dir, relpath, payload)
    return BundleFile(
        relpath=relpath, sha256=_sha256_bytes(payload), bytes=len(payload), kind=kind
    )


def write_sha256sums(bundle_dir: Path, entries: list[BundleFile]) -> BundleFile:
    """Write ``SHA256SUMS`` over every data file, sorted by path.

    ``manifest.json`` is deliberately **not** listed: it carries the run's
    wall-clock block, so listing it would make the checksum file itself
    non-deterministic. The manifest is hashed by its own ``content_digest``
    and by the archive sidecar.
    """
    lines = sorted(f"{entry.sha256}  {entry.relpath}\n" for entry in entries)
    payload = "".join(lines).encode("utf-8")
    _write_bytes(bundle_dir, "SHA256SUMS", payload)
    return BundleFile(
        relpath="SHA256SUMS",
        sha256=_sha256_bytes(payload),
        bytes=len(payload),
        kind="integrity",
        rows=len(entries),
    )


def manifest_content_digest(manifest: dict[str, Any]) -> str:
    """SHA-256 over the manifest with its ``run`` block removed.

    This is the digest that identifies the extracted content. Two runs over the
    same snapshot with the same arguments share it even though their ``run``
    timestamps differ.
    """
    content = {k: v for k, v in manifest.items() if k not in {"run", "content_digest_sha256"}}
    return _sha256_bytes(_canonical_json_bytes(content))


def write_manifest(bundle_dir: Path, manifest: dict[str, Any]) -> Path:
    """Write ``manifest.json`` with its content digest embedded."""
    payload = dict(manifest)
    payload["content_digest_sha256"] = manifest_content_digest(manifest)
    body = json.dumps(payload, sort_keys=True, ensure_ascii=True, indent=2).encode("utf-8") + b"\n"
    _write_bytes(bundle_dir, "manifest.json", body)
    return Path(bundle_dir) / "manifest.json"


def write_tarball(bundle_dir: Path, *, mtime: int) -> tuple[Path, Path, str]:
    """Pack the bundle directory into a reproducible ``.tar.gz`` plus a sidecar.

    Members are sorted, ownership and timestamps are fixed, member modes are
    ``0600``, and the gzip header carries no modification time, so the archive
    bytes depend only on the bundle's contents and an extraction reproduces the
    owner-only modes. The archive and its sidecar are themselves written
    ``0600`` through :func:`write_private_bytes`.

    Returns:
        ``(archive_path, sidecar_path, archive_sha256)``.
    """
    bundle = Path(bundle_dir).resolve()
    archive = bundle.parent / f"{bundle.name}.tar.gz"
    sidecar = bundle.parent / f"{bundle.name}.tar.gz.sha256"
    assert_no_collision(archive)
    assert_no_collision(sidecar)

    members = sorted(p for p in bundle.rglob("*") if p.is_file())
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for member in members:
            info = tarfile.TarInfo(f"{bundle.name}/{member.relative_to(bundle).as_posix()}")
            data = member.read_bytes()
            info.size = len(data)
            info.mtime = mtime
            info.mode = PRIVATE_FILE_MODE
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.type = tarfile.REGTYPE
            tar.addfile(info, io.BytesIO(data))

    compressed = io.BytesIO()
    with gzip.GzipFile(fileobj=compressed, mode="wb", compresslevel=9, mtime=0) as gz:
        gz.write(raw.getvalue())
    payload = compressed.getvalue()

    write_private_bytes(archive, payload)
    digest = _sha256_bytes(payload)
    write_private_bytes(sidecar, f"{digest}  {archive.name}\n".encode("utf-8"))
    return archive, sidecar, digest
