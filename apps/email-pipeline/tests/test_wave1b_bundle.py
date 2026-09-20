"""Bundle writing: output-path policy, hashing, determinism and idempotency.

Synthetic payloads only. Any address used here is on the reserved
``example.invalid`` domain.
"""

from __future__ import annotations

import hashlib
import json
import tarfile
from pathlib import Path

import pytest

from origenlab_email_pipeline.migration.bundle import (
    OutputPathError,
    assert_no_collision,
    assert_output_dir_allowed,
    manifest_content_digest,
    write_jsonl,
    write_json,
    write_manifest,
    write_sha256sums,
    write_tarball,
)

_ROWS = [
    {"id": 1, "email_norm": "one@example.invalid", "state": "candidate"},
    {"id": 2, "email_norm": "two@example.invalid", "state": "sent"},
]
_FIXED_MTIME = 1_757_046_265  # 2026-09-05T04:24:25Z, the Wave 1A snapshot instant


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- output path policy ----------------------------------------------------


def test_assert_output_dir_allowed_accepts_a_directory_outside_any_repository(
    tmp_path: Path,
) -> None:
    out = tmp_path / "bundles"
    assert assert_output_dir_allowed(out) == out.resolve()


def test_assert_output_dir_allowed_refuses_a_path_inside_this_repository() -> None:
    inside = Path(__file__).resolve().parent / "wave1b_output"
    with pytest.raises(OutputPathError):
        assert_output_dir_allowed(inside)


def test_assert_output_dir_allowed_refuses_a_worktree_dot_git_file(tmp_path: Path) -> None:
    (tmp_path / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")
    with pytest.raises(OutputPathError):
        assert_output_dir_allowed(tmp_path / "nested" / "out")


def test_assert_no_collision_refuses_an_existing_target(tmp_path: Path) -> None:
    target = tmp_path / "bundle"
    target.mkdir()
    with pytest.raises(OutputPathError):
        assert_no_collision(target)


def test_assert_no_collision_accepts_a_free_target(tmp_path: Path) -> None:
    assert_no_collision(tmp_path / "bundle") is None


# --- deterministic data files ----------------------------------------------


def test_write_jsonl_is_deterministic_and_records_its_own_digest(tmp_path: Path) -> None:
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()

    entry_one = write_jsonl(first, "exact/rows.jsonl", _ROWS, kind="exact")
    entry_two = write_jsonl(second, "exact/rows.jsonl", _ROWS, kind="exact")

    assert entry_one.sha256 == entry_two.sha256
    assert entry_one.rows == 2
    assert entry_one.sha256 == _sha256(first / "exact/rows.jsonl")
    assert (first / "exact/rows.jsonl").read_bytes() == (second / "exact/rows.jsonl").read_bytes()
    assert (first / "exact/rows.jsonl").read_text(encoding="utf-8").endswith("\n")


def test_write_json_sorts_keys_so_bytes_do_not_depend_on_build_order(tmp_path: Path) -> None:
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()
    write_json(first, "derived/summary.json", {"b": 2, "a": 1}, kind="derived")
    write_json(second, "derived/summary.json", {"a": 1, "b": 2}, kind="derived")
    assert _sha256(first / "derived/summary.json") == _sha256(second / "derived/summary.json")


# --- SHA256SUMS ------------------------------------------------------------


def test_write_sha256sums_round_trips_every_data_file(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    entries = [
        write_jsonl(bundle, "exact/rows.jsonl", _ROWS, kind="exact"),
        write_json(bundle, "derived/summary.json", {"rows": 2}, kind="derived"),
    ]
    sums = write_sha256sums(bundle, entries)

    listed = {}
    for line in (bundle / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        listed[name] = digest

    assert listed == {e.relpath: e.sha256 for e in entries}
    for name, digest in listed.items():
        assert _sha256(bundle / name) == digest
    assert sums.relpath == "SHA256SUMS"


def test_write_sha256sums_is_byte_identical_for_the_same_inputs(tmp_path: Path) -> None:
    digests = []
    for name in ("a", "b"):
        bundle = tmp_path / name
        bundle.mkdir()
        entries = [
            write_json(bundle, "derived/summary.json", {"rows": 2}, kind="derived"),
            write_jsonl(bundle, "exact/rows.jsonl", _ROWS, kind="exact"),
        ]
        write_sha256sums(bundle, entries)
        digests.append(_sha256(bundle / "SHA256SUMS"))
    assert digests[0] == digests[1]


# --- manifest --------------------------------------------------------------


def test_manifest_content_digest_ignores_the_run_block(tmp_path: Path) -> None:
    base = {"wave": "1b", "row_counts": {"campaign": 2}}
    one = {**base, "run": {"started_at_utc": "2026-09-20T10:00:00Z"}}
    two = {**base, "run": {"started_at_utc": "2026-09-21T23:59:59Z"}}
    assert manifest_content_digest(one) == manifest_content_digest(two)


def test_manifest_content_digest_changes_when_a_count_changes() -> None:
    one = {"wave": "1b", "row_counts": {"campaign": 2}, "run": {}}
    two = {"wave": "1b", "row_counts": {"campaign": 3}, "run": {}}
    assert manifest_content_digest(one) != manifest_content_digest(two)


def test_write_manifest_embeds_its_own_content_digest(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    payload = {"wave": "1b", "row_counts": {"campaign": 1}, "run": {"x": 1}}
    path = write_manifest(bundle, payload)
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["content_digest_sha256"] == manifest_content_digest(payload)


# --- tarball ---------------------------------------------------------------


def test_write_tarball_is_deterministic_and_sidecar_matches(tmp_path: Path) -> None:
    archives = []
    for name in ("a", "b"):
        root = tmp_path / name
        bundle = root / "wave1b_bundle"
        bundle.mkdir(parents=True)
        entries = [write_jsonl(bundle, "exact/rows.jsonl", _ROWS, kind="exact")]
        write_sha256sums(bundle, entries)
        write_manifest(bundle, {"wave": "1b", "run": {}})
        archive, sidecar, digest = write_tarball(bundle, mtime=_FIXED_MTIME)
        assert sidecar.read_text(encoding="utf-8").split()[0] == digest
        assert digest == _sha256(archive)
        archives.append(_sha256(archive))
    assert archives[0] == archives[1]


def test_write_tarball_round_trips_the_bundle_contents(tmp_path: Path) -> None:
    bundle = tmp_path / "wave1b_bundle"
    bundle.mkdir()
    entries = [write_jsonl(bundle, "exact/rows.jsonl", _ROWS, kind="exact")]
    write_sha256sums(bundle, entries)
    archive, _sidecar, _digest = write_tarball(bundle, mtime=_FIXED_MTIME)

    extract_to = tmp_path / "extracted"
    with tarfile.open(archive, "r:gz") as tar:
        names = tar.getnames()
        tar.extractall(extract_to, filter="data")

    assert names == sorted(names)
    assert all(n.startswith("wave1b_bundle/") for n in names)
    restored = extract_to / "wave1b_bundle" / "exact" / "rows.jsonl"
    assert _sha256(restored) == entries[0].sha256


def test_write_tarball_refuses_to_overwrite_an_existing_archive(tmp_path: Path) -> None:
    bundle = tmp_path / "wave1b_bundle"
    bundle.mkdir()
    write_sha256sums(bundle, [write_jsonl(bundle, "exact/rows.jsonl", _ROWS, kind="exact")])
    write_tarball(bundle, mtime=_FIXED_MTIME)
    with pytest.raises(OutputPathError):
        write_tarball(bundle, mtime=_FIXED_MTIME)


# --- private filesystem permissions -----------------------------------------


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_assert_output_root_private_creates_a_0700_root(tmp_path: Path) -> None:
    from origenlab_email_pipeline.migration.bundle import assert_output_root_private

    root = assert_output_root_private(tmp_path / "fresh" / "nested")
    assert root.is_dir()
    assert _mode(root) == 0o700


def test_assert_output_root_private_tightens_an_existing_owner_owned_root(
    tmp_path: Path,
) -> None:
    from origenlab_email_pipeline.migration.bundle import assert_output_root_private

    root = tmp_path / "existing"
    root.mkdir()
    root.chmod(0o755)
    child = root / "unrelated"
    child.mkdir()
    child.chmod(0o755)

    assert_output_root_private(root)
    assert _mode(root) == 0o700
    assert _mode(child) == 0o755, "existing content must never be recursively chmodded"


def test_assert_output_root_private_refuses_a_group_writable_root(tmp_path: Path) -> None:
    from origenlab_email_pipeline.migration.bundle import (
        OutputPathError,
        assert_output_root_private,
    )

    root = tmp_path / "loose"
    root.mkdir()
    root.chmod(0o770)
    with pytest.raises(OutputPathError, match="writable"):
        assert_output_root_private(root)


def test_assert_output_root_private_refuses_a_symlink(tmp_path: Path) -> None:
    from origenlab_email_pipeline.migration.bundle import (
        OutputPathError,
        assert_output_root_private,
    )

    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(OutputPathError, match="symbolic link"):
        assert_output_root_private(link)


def test_write_private_bytes_is_0600_atomic_and_leaves_no_temporary(
    tmp_path: Path,
) -> None:
    from origenlab_email_pipeline.migration.bundle import write_private_bytes

    target = tmp_path / "nested" / "evidence.jsonl"
    write_private_bytes(target, b'{"address":"a@example.invalid"}\n')
    assert _mode(target) == 0o600
    assert _mode(target.parent) == 0o700
    assert list(tmp_path.rglob(".*.tmp")) == []


def test_write_private_bytes_refuses_to_follow_a_symlink(tmp_path: Path) -> None:
    from origenlab_email_pipeline.migration.bundle import (
        OutputPathError,
        write_private_bytes,
    )

    victim = tmp_path / "victim.txt"
    victim.write_text("keep me\n", encoding="utf-8")
    link = tmp_path / "link.jsonl"
    link.symlink_to(victim)

    with pytest.raises(OutputPathError, match="symbolic link"):
        write_private_bytes(link, b"overwritten\n")
    assert victim.read_text(encoding="utf-8") == "keep me\n"


def test_a_failed_write_removes_its_temporary_file(tmp_path: Path, monkeypatch) -> None:
    from origenlab_email_pipeline.migration import bundle as module

    target = tmp_path / "boom.jsonl"

    def _fail(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(module.os, "replace", _fail)
    with pytest.raises(OSError, match="disk full"):
        module.write_private_bytes(target, b"payload\n")

    assert not target.exists()
    assert list(tmp_path.glob(".*.tmp")) == []
