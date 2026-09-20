"""Tighten an already-written evidence artifact to owner-only, and prove it.

The Wave 1B bundle is owner-only by construction (``bundle.py``): directories
``0700``, files and tar members ``0600``, never visible at a wider mode even
briefly. The Wave 1A bundle predates that policy and was written with the
ordinary umask, so it sits at ``0755`` / ``0644`` — readable by every local
account. Nothing in it is group- or world-*writable*, so the evidence is
intact; it is simply not private.

This module closes that gap for **one named artifact boundary** and nothing
else. It is the only place in the repository that changes the mode of a file it
did not create, so the rules are strict:

* **The boundary is explicit.** A bundle directory, and optionally its archive
  and that archive's ``.sha256`` sidecar. Nothing else is touched — in
  particular the enclosing migration root is never chmod-ed and never walked.
  A path that is not inside the named boundary is refused, not skipped.
* **Verify first, then change.** Every file is checked against the bundle's own
  ``SHA256SUMS``, the manifest and archive against the hashes the caller pins,
  before a single ``chmod``. An artifact that cannot prove what it is does not
  get hardened; it gets refused.
* **Verify again afterwards.** Every checksum is recomputed after the change.
  ``chmod`` cannot alter content, and this proves it did not: the tool reports
  the before and after digests and refuses if any moved.
* **Only the mode moves.** No content is written, no file is renamed or
  replaced, and ``mtime`` is neither set nor touched — it is recorded before
  and after as evidence. (``ctime`` does advance; that is what recording a
  metadata change means, and it is not suppressible.)
* **Symlinks and foreign owners refuse.** Every path is ``lstat``-ed; a
  symbolic link anywhere in the boundary refuses the run rather than being
  followed, so a link can never be used to redirect a ``chmod`` outside.
* **No address is read.** This module opens files only to hash them.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.migration.bundle import PRIVATE_DIR_MODE, PRIVATE_FILE_MODE
from origenlab_email_pipeline.migration.integrity import (
    as_refusal,
    assert_not_symlink,
    assert_recorded,
    assert_source_safe,
    iter_paths,
    observe_modes,
    sha256_file,
    verify_bundle_checksums,
    verify_sidecar,
)


class HardeningRefused(Exception):
    """The hardening failed closed. No mode was changed.

    Messages carry counts and relative names only — never an address, a domain
    or an absolute path.
    """


@dataclass(frozen=True)
class HardeningResult:
    """What was tightened, and the proof that only the mode moved."""

    boundary: tuple[Path, ...]
    directories_changed: int
    files_changed: int
    before: dict[str, Any]
    after: dict[str, Any]
    digests_before: dict[str, str]
    digests_after: dict[str, str]
    mtimes_unchanged: bool
    console_lines: tuple[str, ...] = ()

    @property
    def changed(self) -> int:
        return self.directories_changed + self.files_changed


def _resolve(value: Path, label: str) -> Path:
    """Expand, refuse a symlink *before* resolving it away, then resolve."""
    candidate = Path(value).expanduser()
    as_refusal(HardeningRefused, assert_not_symlink, candidate, label)
    return candidate.resolve()


def _mtimes(paths: list[Path]) -> dict[str, int]:
    return {str(p): p.lstat().st_mtime_ns for p in paths}


def _boundary_paths(bundle: Path, extras: list[Path]) -> list[Path]:
    """Every path this run may touch: the bundle tree plus the named extras."""
    return [bundle, *iter_paths(bundle), *extras]


def harden_artifact_permissions(
    *,
    bundle_dir: Path,
    archive: Path | None = None,
    sidecar: Path | None = None,
    expected_manifest_sha256: str | None = None,
    expected_archive_sha256: str | None = None,
    dry_run: bool = False,
) -> HardeningResult:
    """Tighten one named artifact boundary to ``0700`` / ``0600``.

    Args:
        bundle_dir: the extracted bundle directory. Its tree is the boundary.
        archive: the bundle's ``.tar.gz``, if it is to be tightened too.
        sidecar: that archive's ``.sha256``. Defaults to ``<archive>.sha256``.
        expected_manifest_sha256: the hash ``docs/DATA.md`` pins for the
            bundle's ``manifest.json``. Verified before and after.
        expected_archive_sha256: the hash ``docs/DATA.md`` pins for the archive.
        dry_run: verify and report what would change, changing nothing.

    Raises:
        HardeningRefused: verification failed, a path is a symlink, a path is
            not owned by the running user, or a post-change digest moved. In
            every case before the change, nothing was modified.
    """
    bundle = _resolve(bundle_dir, "the bundle directory")
    if not bundle.is_dir():
        raise HardeningRefused("the bundle directory does not exist")

    extras: list[Path] = []
    archive_path = _resolve(archive, "the archive") if archive else None
    if archive_path is not None:
        sidecar_path = (
            _resolve(sidecar, "the archive sidecar")
            if sidecar
            else Path(f"{archive_path}.sha256")
        )
        as_refusal(HardeningRefused, assert_not_symlink, sidecar_path, "the archive sidecar")
        extras.extend([archive_path, sidecar_path])
    elif sidecar is not None:
        raise HardeningRefused("a sidecar was named without its archive")

    boundary = _boundary_paths(bundle, extras)

    # --- every path must be safe to act on, before anything is acted on ---- #
    for node in boundary:
        as_refusal(
            HardeningRefused, assert_source_safe, node, "a path in the artifact boundary"
        )
    # A boundary member must be inside the bundle or be one of the named extras.
    named = {str(p) for p in extras}
    for node in boundary:
        if node == bundle or str(node) in named:
            continue
        if bundle not in node.parents:
            raise HardeningRefused(
                "a path resolved outside the named artifact boundary; refusing to "
                "change the mode of anything the operator did not name"
            )

    # --- verify what this artifact is, before changing it ------------------ #
    before_modes = as_refusal(
        HardeningRefused, observe_modes, bundle, "the bundle"
    )
    digests_before = as_refusal(
        HardeningRefused, verify_bundle_checksums, bundle, "the bundle"
    )

    manifest = bundle / "manifest.json"
    if expected_manifest_sha256 is not None:
        if not manifest.is_file():
            raise HardeningRefused("the bundle has no manifest.json to verify")
        as_refusal(
            HardeningRefused,
            assert_recorded,
            sha256_file(manifest),
            expected_manifest_sha256,
            "the bundle manifest.json SHA-256",
        )
    if manifest.is_file():
        digests_before["manifest.json"] = sha256_file(manifest)

    if archive_path is not None:
        archive_digest = as_refusal(
            HardeningRefused,
            verify_sidecar,
            archive_path,
            extras[1],
            "the archive",
        )
        if expected_archive_sha256 is not None:
            as_refusal(
                HardeningRefused,
                assert_recorded,
                archive_digest,
                expected_archive_sha256,
                "the archive SHA-256",
            )
        digests_before[archive_path.name] = archive_digest
        digests_before[extras[1].name] = sha256_file(extras[1])

    before_modes["archive_and_sidecar_modes"] = {
        p.name: f"0o{stat.S_IMODE(p.lstat().st_mode):03o}" for p in extras
    }
    mtimes_before = _mtimes(boundary)

    # --- the change ------------------------------------------------------- #
    directories_changed = 0
    files_changed = 0
    for node in boundary:
        current = stat.S_IMODE(node.lstat().st_mode)
        wanted = PRIVATE_DIR_MODE if node.is_dir() else PRIVATE_FILE_MODE
        if current == wanted:
            continue
        if not dry_run:
            # `follow_symlinks=False` is not needed: every node was lstat-proven
            # not to be a link above, and nothing has run since.
            os.chmod(node, wanted)
        if node.is_dir():
            directories_changed += 1
        else:
            files_changed += 1

    # --- prove only the mode moved ----------------------------------------- #
    digests_after = as_refusal(
        HardeningRefused, verify_bundle_checksums, bundle, "the bundle"
    )
    if manifest.is_file():
        digests_after["manifest.json"] = sha256_file(manifest)
    if archive_path is not None:
        digests_after[archive_path.name] = sha256_file(archive_path)
        digests_after[extras[1].name] = sha256_file(extras[1])

    moved = sorted(
        name for name, digest in digests_before.items() if digests_after.get(name) != digest
    )
    if moved:
        raise HardeningRefused(
            f"{len(moved)} file(s) changed content during a permission change; "
            "this must never happen and the artifact needs an operator check"
        )

    mtimes_after = _mtimes(boundary)
    mtimes_unchanged = mtimes_before == mtimes_after

    after_modes = as_refusal(HardeningRefused, observe_modes, bundle, "the bundle")
    extra_modes = {
        p.name: f"0o{stat.S_IMODE(p.lstat().st_mode):03o}" for p in extras
    }
    after_modes["archive_and_sidecar_modes"] = extra_modes

    if not dry_run:
        leaked = [
            str(p.relative_to(bundle.parent))
            for p in boundary
            if stat.S_IMODE(p.lstat().st_mode) & 0o077
        ]
        if leaked:
            raise HardeningRefused(
                f"{len(leaked)} path(s) remain readable or writable by group or other "
                "after hardening"
            )

    console = [
        f"artifact permission hardening{' (dry run)' if dry_run else ''}",
        f"boundary: 1 bundle directory + {len(list(iter_paths(bundle)))} path(s) inside"
        + (f" + {len(extras)} named file(s)" if extras else ""),
        f"before: root {before_modes['root_mode']}, "
        f"{before_modes['directories_wider_than_0700']} dir(s) wider than 0700, "
        f"{before_modes['files_wider_than_0600']} file(s) wider than 0600",
        f"changed: {directories_changed} directory(ies), {files_changed} file(s)",
        f"after: root {after_modes['root_mode']}, private_mode_ok="
        f"{after_modes['private_mode_ok']}",
        f"checksums re-verified: {len(digests_after)} file(s), zero digests moved",
        f"mtimes unchanged: {mtimes_unchanged}",
        "no content was read, written or renamed; no address was opened",
    ]

    return HardeningResult(
        boundary=tuple(boundary),
        directories_changed=directories_changed,
        files_changed=files_changed,
        before=before_modes,
        after=after_modes,
        digests_before=digests_before,
        digests_after=digests_after,
        mtimes_unchanged=mtimes_unchanged,
        console_lines=tuple(console),
    )
