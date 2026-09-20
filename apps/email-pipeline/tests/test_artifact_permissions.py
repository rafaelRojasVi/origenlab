"""Permission hardening of one named evidence artifact boundary.

This is the only code in the repository that changes the mode of a file it did
not create, so these tests are about what it must *not* touch as much as what
it must. The bundles here are synthetic; addresses are on the reserved
``example.invalid`` domain.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

import pytest

from origenlab_email_pipeline.migration.artifact_permissions import (
    HardeningRefused,
    harden_artifact_permissions,
)

_BUNDLE_NAME = "20260101T000000Z_wave1a_v1_safety_bundle"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_digests(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): _digest(p) for p in sorted(root.rglob("*")) if p.is_file()
    }


def _modes(root: Path) -> dict[str, int]:
    return {
        str(p.relative_to(root)): stat.S_IMODE(p.lstat().st_mode)
        for p in sorted(root.rglob("*"))
    }


def _build(
    root: Path,
    *,
    mode_dir: int = 0o755,
    mode_file: int = 0o644,
    break_checksum: bool = False,
    with_archive: bool = True,
) -> tuple[Path, Path | None]:
    """A world-readable bundle, the shape Wave 1A actually has on disk."""
    bundle = root / _BUNDLE_NAME
    (bundle / "exact").mkdir(parents=True)
    (bundle / "reports").mkdir(parents=True)

    payloads = {
        "exact/contact_email_suppression.jsonl": json.dumps(
            {"email": "supp-1@example.invalid"}, sort_keys=True
        )
        + "\n",
        "reports/reconciliation_summary.json": json.dumps({"rows": 1}, sort_keys=True) + "\n",
    }
    for rel, text in payloads.items():
        (bundle / rel).write_text(text, encoding="utf-8")

    lines = []
    for rel in sorted(payloads):
        digest = "0" * 64 if break_checksum else _digest(bundle / rel)
        lines.append(f"{digest}  {rel}\n")
    (bundle / "SHA256SUMS").write_text("".join(lines), encoding="utf-8")
    (bundle / "manifest.json").write_text(
        json.dumps({"bundle_name": bundle.name}, sort_keys=True) + "\n", encoding="utf-8"
    )

    archive: Path | None = None
    if with_archive:
        archive = root / f"{_BUNDLE_NAME}.tar.gz"
        archive.write_bytes(b"stand-in-archive")
        Path(f"{archive}.sha256").write_text(
            f"{_digest(archive)}  {archive.name}\n", encoding="utf-8"
        )

    for node in [bundle, *bundle.rglob("*")]:
        node.chmod(mode_dir if node.is_dir() else mode_file)
    if archive is not None:
        archive.chmod(mode_file)
        Path(f"{archive}.sha256").chmod(mode_file)
    return bundle, archive


def _harden(bundle: Path, archive: Path | None, **kwargs):
    return harden_artifact_permissions(bundle_dir=bundle, archive=archive, **kwargs)


# --------------------------------------------------------------------------- #
# The change itself
# --------------------------------------------------------------------------- #


def test_the_boundary_is_tightened_to_owner_only(tmp_path: Path) -> None:
    bundle, archive = _build(tmp_path)
    assert stat.S_IMODE(bundle.stat().st_mode) == 0o755

    result = _harden(bundle, archive)

    assert stat.S_IMODE(bundle.stat().st_mode) == 0o700
    for node in bundle.rglob("*"):
        wanted = 0o700 if node.is_dir() else 0o600
        assert stat.S_IMODE(node.lstat().st_mode) == wanted
    assert archive is not None
    assert stat.S_IMODE(archive.stat().st_mode) == 0o600
    assert stat.S_IMODE(Path(f"{archive}.sha256").stat().st_mode) == 0o600
    assert result.after["private_mode_ok"] is True
    assert result.changed > 0


def test_nothing_readable_or_writable_by_others_remains(tmp_path: Path) -> None:
    bundle, archive = _build(tmp_path)
    _harden(bundle, archive)
    for node in [bundle, *bundle.rglob("*"), archive, Path(f"{archive}.sha256")]:
        assert stat.S_IMODE(node.lstat().st_mode) & 0o077 == 0


def test_only_the_mode_moves(tmp_path: Path) -> None:
    bundle, archive = _build(tmp_path)
    digests_before = _tree_digests(bundle)
    archive_before = _digest(archive)
    mtimes_before = {
        str(p): p.lstat().st_mtime_ns for p in sorted(bundle.rglob("*"))
    }

    result = _harden(bundle, archive)

    assert _tree_digests(bundle) == digests_before
    assert _digest(archive) == archive_before
    assert {
        str(p): p.lstat().st_mtime_ns for p in sorted(bundle.rglob("*"))
    } == mtimes_before
    assert result.mtimes_unchanged is True
    assert result.digests_before == result.digests_after


def test_a_second_run_is_a_no_op(tmp_path: Path) -> None:
    bundle, archive = _build(tmp_path)
    _harden(bundle, archive)
    again = _harden(bundle, archive)
    assert again.changed == 0
    assert again.after["private_mode_ok"] is True


def test_a_dry_run_changes_nothing(tmp_path: Path) -> None:
    bundle, archive = _build(tmp_path)
    modes_before = _modes(bundle)

    result = _harden(bundle, archive, dry_run=True)

    assert _modes(bundle) == modes_before
    assert result.changed > 0
    assert "dry run" in result.console_lines[0]


# --------------------------------------------------------------------------- #
# The boundary
# --------------------------------------------------------------------------- #


def test_the_enclosing_directory_is_never_touched(tmp_path: Path) -> None:
    """The migration root holds other operators' artifacts. It is not ours."""
    root = tmp_path / "migration_root"
    root.mkdir(mode=0o755)
    bundle, archive = _build(root)
    sibling = root / "someone_elses_bundle"
    sibling.mkdir(mode=0o755)
    (sibling / "data.jsonl").write_text("{}\n", encoding="utf-8")
    (sibling / "data.jsonl").chmod(0o644)

    _harden(bundle, archive)

    assert stat.S_IMODE(root.stat().st_mode) == 0o755
    assert stat.S_IMODE(sibling.stat().st_mode) == 0o755
    assert stat.S_IMODE((sibling / "data.jsonl").stat().st_mode) == 0o644


def test_a_sidecar_without_its_archive_is_refused(tmp_path: Path) -> None:
    bundle, archive = _build(tmp_path)
    with pytest.raises(HardeningRefused, match="without its archive"):
        harden_artifact_permissions(
            bundle_dir=bundle, archive=None, sidecar=Path(f"{archive}.sha256")
        )


def test_a_missing_bundle_is_refused(tmp_path: Path) -> None:
    with pytest.raises(HardeningRefused, match="does not exist"):
        harden_artifact_permissions(bundle_dir=tmp_path / "nothing_here")


# --------------------------------------------------------------------------- #
# Verify first, refuse rather than change
# --------------------------------------------------------------------------- #


def test_a_checksum_mismatch_refuses_before_changing_anything(tmp_path: Path) -> None:
    bundle, archive = _build(tmp_path, break_checksum=True)
    modes_before = _modes(bundle)

    with pytest.raises(HardeningRefused, match="SHA256SUMS mismatch"):
        _harden(bundle, archive)

    assert _modes(bundle) == modes_before


def test_a_wrong_expected_manifest_hash_refuses(tmp_path: Path) -> None:
    bundle, archive = _build(tmp_path)
    modes_before = _modes(bundle)

    with pytest.raises(HardeningRefused, match="immutable"):
        _harden(bundle, archive, expected_manifest_sha256="f" * 64)

    assert _modes(bundle) == modes_before


def test_a_wrong_expected_archive_hash_refuses(tmp_path: Path) -> None:
    bundle, archive = _build(tmp_path)
    modes_before = _modes(bundle)

    with pytest.raises(HardeningRefused, match="immutable"):
        _harden(bundle, archive, expected_archive_sha256="f" * 64)

    assert _modes(bundle) == modes_before


def test_the_recorded_hashes_are_accepted_when_they_match(tmp_path: Path) -> None:
    bundle, archive = _build(tmp_path)
    result = _harden(
        bundle,
        archive,
        expected_manifest_sha256=_digest(bundle / "manifest.json"),
        expected_archive_sha256=_digest(archive),
    )
    assert result.after["private_mode_ok"] is True


def test_an_archive_that_does_not_match_its_sidecar_refuses(tmp_path: Path) -> None:
    bundle, archive = _build(tmp_path)
    Path(f"{archive}.sha256").write_text(
        f"{'0' * 64}  {archive.name}\n", encoding="utf-8"
    )
    modes_before = _modes(bundle)

    with pytest.raises(HardeningRefused, match="sidecar"):
        _harden(bundle, archive)

    assert _modes(bundle) == modes_before


def test_a_symlink_inside_the_boundary_refuses(tmp_path: Path) -> None:
    bundle, archive = _build(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("do not chmod me\n", encoding="utf-8")
    outside.chmod(0o644)
    (bundle / "exact" / "link.jsonl").symlink_to(outside)
    modes_before = _modes(bundle)

    with pytest.raises(HardeningRefused, match="symbolic link"):
        _harden(bundle, archive)

    assert _modes(bundle) == modes_before
    assert stat.S_IMODE(outside.stat().st_mode) == 0o644


def test_a_symlinked_bundle_directory_refuses(tmp_path: Path) -> None:
    bundle, archive = _build(tmp_path)
    link = tmp_path / "linked"
    link.symlink_to(bundle, target_is_directory=True)
    with pytest.raises(HardeningRefused, match="symbolic link"):
        harden_artifact_permissions(bundle_dir=link, archive=archive)


def test_a_foreign_owner_refuses(tmp_path: Path, monkeypatch) -> None:
    bundle, archive = _build(tmp_path)
    modes_before = _modes(bundle)
    monkeypatch.setattr(os, "getuid", lambda: 999_999)

    with pytest.raises(HardeningRefused, match="not owned by the current user"):
        _harden(bundle, archive)

    assert _modes(bundle) == modes_before


def test_a_group_writable_path_refuses(tmp_path: Path) -> None:
    bundle, archive = _build(tmp_path)
    (bundle / "manifest.json").chmod(0o664)
    with pytest.raises(HardeningRefused, match="group- or world-writable"):
        _harden(bundle, archive)


def test_a_device_or_fifo_in_the_boundary_refuses(tmp_path: Path) -> None:
    bundle, archive = _build(tmp_path)
    os.mkfifo(bundle / "exact" / "pipe")
    with pytest.raises(HardeningRefused, match="neither a directory nor a regular file"):
        _harden(bundle, archive)


# --------------------------------------------------------------------------- #
# Privacy
# --------------------------------------------------------------------------- #


def test_no_address_or_absolute_path_is_printed(tmp_path: Path) -> None:
    bundle, archive = _build(tmp_path)
    result = _harden(bundle, archive)
    text = "\n".join(result.console_lines)
    assert "supp-1@example.invalid" not in text
    assert "example.invalid" not in text
    assert "@" not in text
    assert str(tmp_path) not in text


def test_a_refusal_message_carries_no_absolute_path(tmp_path: Path) -> None:
    bundle, archive = _build(tmp_path, break_checksum=True)
    with pytest.raises(HardeningRefused) as excinfo:
        _harden(bundle, archive)
    assert str(tmp_path) not in str(excinfo.value)
