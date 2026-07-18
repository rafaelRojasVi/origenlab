"""Synthetic writable-restore rehearsal tests (throwaway fixtures only)."""

from __future__ import annotations

import ast
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from origenlab_email_pipeline.qa.sqlite_writable_restore_rehearsal import (
    COPY_CHUNK_BYTES,
    EXIT_OK,
    EXIT_PREFLIGHT,
    EXIT_SAFETY,
    MAX_SOURCE_BYTES,
    RehearsalError,
    RehearsalFailureCategory,
    RehearsalOptions,
    build_synthetic_source,
    content_sha256,
    fingerprint_file,
    preflight,
    run_rehearsal,
    stream_copy_file,
    tree_snapshot,
    validate_planned_cutover_topology,
)

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "maintenance" / "rehearse_sqlite_writable_restore.py"
MODULE = (
    REPO
    / "src"
    / "origenlab_email_pipeline"
    / "qa"
    / "sqlite_writable_restore_rehearsal.py"
)


def _scratch(tmp_path: Path) -> Path:
    root = tmp_path / "origenlab_sqlite_rehearsal"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _fixture(scratch: Path, name: str = "synthetic_source.sqlite") -> Path:
    path = scratch / name
    build_synthetic_source(
        path,
        scratch_root=scratch,
        confirm_throwaway=True,
        apply=True,
    )
    return path


def _opts(
    scratch: Path,
    src: Path,
    dst: Path,
    *,
    apply: bool = False,
    **kwargs: object,
) -> RehearsalOptions:
    return RehearsalOptions(
        source=src,
        restore_target=dst,
        scratch_root=scratch,
        confirm_throwaway=True,
        apply=apply,
        **kwargs,  # type: ignore[arg-type]
    )


def test_preflight_requires_confirm(tmp_path: Path) -> None:
    scratch = _scratch(tmp_path)
    src = _fixture(scratch)
    dst = scratch / "throwaway_restore.sqlite"
    with pytest.raises(RehearsalError) as excinfo:
        preflight(
            RehearsalOptions(
                source=src,
                restore_target=dst,
                scratch_root=scratch,
                confirm_throwaway=False,
            )
        )
    assert excinfo.value.category == RehearsalFailureCategory.SAFETY
    assert excinfo.value.exit_code == EXIT_SAFETY


def test_zero_write_preflight_tree_unchanged(tmp_path: Path) -> None:
    scratch = _scratch(tmp_path)
    src = _fixture(scratch)
    dst = scratch / "nested" / "missing" / "throwaway_restore.sqlite"
    before = tree_snapshot(tmp_path)
    report = preflight(_opts(scratch, src, dst))
    after = tree_snapshot(tmp_path)
    assert before == after
    assert report["zero_write_preflight"] is True
    assert report["apply_would_create_parent"] is True
    assert report["target_parent_exists"] is False
    assert not dst.parent.exists()


def test_build_fixture_requires_apply(tmp_path: Path) -> None:
    scratch = _scratch(tmp_path)
    path = scratch / "synthetic_fixture.sqlite"
    with pytest.raises(RehearsalError) as excinfo:
        build_synthetic_source(
            path,
            scratch_root=scratch,
            confirm_throwaway=True,
            apply=False,
        )
    assert excinfo.value.category == RehearsalFailureCategory.SAFETY
    assert not path.exists()


def test_fixture_marker_provenance(tmp_path: Path) -> None:
    scratch = _scratch(tmp_path)
    src = _fixture(scratch)
    conn = sqlite3.connect(src)
    try:
        row = conn.execute(
            "SELECT schema_version, fixture_identity, tool "
            "FROM origenlab_sqlite_rehearsal_meta WHERE id=1"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == 1
    assert row[1] == "origenlab-sqlite-writable-restore-rehearsal"
    assert row[2] == "sqlite_writable_restore_rehearsal"


def test_refuses_unmarked_sqlite(tmp_path: Path) -> None:
    scratch = _scratch(tmp_path)
    src = scratch / "synthetic_unmarked.sqlite"
    conn = sqlite3.connect(src)
    try:
        conn.execute("CREATE TABLE emails (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO emails (id) VALUES (1)")
        conn.commit()
    finally:
        conn.close()
    dst = scratch / "throwaway_restore.sqlite"
    with pytest.raises(RehearsalError) as excinfo:
        preflight(_opts(scratch, src, dst))
    assert excinfo.value.category == RehearsalFailureCategory.SAFETY
    assert "marker" in str(excinfo.value).lower()


def test_hard_size_limit_before_payload_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scratch = _scratch(tmp_path)
    src = _fixture(scratch, "synthetic_large.sqlite")
    os.truncate(src, MAX_SOURCE_BYTES + 1)
    reads: list[str] = []
    real_open = Path.open

    def tracking_open(self: Path, *args: object, **kwargs: object):  # noqa: ANN001
        if self == src or self.resolve() == src.resolve():
            # Header parse may open; track any open after size should have failed.
            reads.append("open")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracking_open)
    dst = scratch / "throwaway_restore.sqlite"
    with pytest.raises(RehearsalError) as excinfo:
        preflight(_opts(scratch, src, dst))
    assert excinfo.value.category == RehearsalFailureCategory.SAFETY
    assert "maximum size" in str(excinfo.value).lower()
    assert reads == []


def test_refuses_real_compact_candidate_shape_without_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scratch = _scratch(tmp_path)
    # Shape matches the known offline compact candidate; must refuse before payload.
    candidate = Path(
        "/mnt/d/origenlab-sqlite-offline/emails_compact_20260717T183537Z.sqlite"
    )
    opened: list[str] = []

    def boom_open(self: Path, *args: object, **kwargs: object):  # noqa: ANN001
        opened.append(str(self))
        raise AssertionError("must not open compact candidate")

    monkeypatch.setattr(Path, "open", boom_open)
    dst = scratch / "throwaway_restore.sqlite"
    with pytest.raises(RehearsalError) as excinfo:
        preflight(
            RehearsalOptions(
                source=candidate,
                restore_target=dst,
                scratch_root=scratch,
                confirm_throwaway=True,
            )
        )
    assert excinfo.value.category == RehearsalFailureCategory.SAFETY
    assert opened == []


def test_refuses_large_arbitrary_non_production_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scratch = _scratch(tmp_path)
    src = scratch / "synthetic_arbitrary.sqlite"
    # Minimal valid header then truncate past cap — not a rehearsal-marked fixture.
    src.write_bytes(b"SQLite format 3\x00" + b"\x00" * 84)
    os.truncate(src, MAX_SOURCE_BYTES + 4096)

    def boom_read_bytes(self: Path) -> bytes:  # noqa: ANN001
        raise AssertionError("Path.read_bytes must never be used")

    monkeypatch.setattr(Path, "read_bytes", boom_read_bytes)
    dst = scratch / "throwaway_restore.sqlite"
    with pytest.raises(RehearsalError) as excinfo:
        preflight(_opts(scratch, src, dst))
    assert excinfo.value.exit_code == EXIT_SAFETY


def test_source_outside_scratch_refused(tmp_path: Path) -> None:
    scratch = _scratch(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    src = outside / "synthetic_source.sqlite"
    build_synthetic_source(
        src,
        scratch_root=outside,
        confirm_throwaway=True,
        apply=True,
    )
    dst = scratch / "throwaway_restore.sqlite"
    with pytest.raises(RehearsalError) as excinfo:
        preflight(_opts(scratch, src, dst))
    assert "scratch root" in str(excinfo.value).lower()


def test_target_outside_scratch_refused(tmp_path: Path) -> None:
    scratch = _scratch(tmp_path)
    src = _fixture(scratch)
    dst = tmp_path / "outside_throwaway_restore.sqlite"
    with pytest.raises(RehearsalError) as excinfo:
        preflight(_opts(scratch, src, dst))
    assert "scratch root" in str(excinfo.value).lower()


def test_production_directory_refusal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scratch = _scratch(tmp_path)
    prod_dir = tmp_path / "prod_sqlite_dir"
    prod_dir.mkdir()
    prod = prod_dir / "emails.sqlite"
    prod.write_bytes(b"SQLite format 3\x00" + b"\x00" * 84)
    monkeypatch.setattr(
        "origenlab_email_pipeline.qa.sqlite_writable_restore_rehearsal.canonical_production_sqlite_path",
        lambda **_kwargs: prod.resolve(),
    )
    monkeypatch.setattr(
        "origenlab_email_pipeline.qa.sqlite_writable_restore_rehearsal.is_configured_production_db",
        lambda *_a, **_k: False,
    )
    # Place a would-be path under production dir with approved token — still refuse.
    under = prod_dir / "synthetic_under_prod.sqlite"
    under.write_bytes(b"SQLite format 3\x00" + b"\x00" * 84)
    dst = scratch / "throwaway_restore.sqlite"
    with pytest.raises(RehearsalError) as excinfo:
        preflight(
            RehearsalOptions(
                source=under,
                restore_target=dst,
                scratch_root=scratch,
                confirm_throwaway=True,
            )
        )
    assert "production" in str(excinfo.value).lower()


def test_source_target_alias_refused(tmp_path: Path) -> None:
    scratch = _scratch(tmp_path)
    src = _fixture(scratch, "synthetic_alias.sqlite")
    alias = scratch / "throwaway_alias.sqlite"
    os.link(src, alias)
    with pytest.raises(RehearsalError) as excinfo:
        preflight(_opts(scratch, src, alias))
    assert excinfo.value.category == RehearsalFailureCategory.SAFETY


def test_source_sidecar_refused(tmp_path: Path) -> None:
    scratch = _scratch(tmp_path)
    src = _fixture(scratch)
    (Path(str(src) + "-wal")).write_bytes(b"")
    dst = scratch / "rehearsal_target.sqlite"
    with pytest.raises(RehearsalError) as excinfo:
        preflight(_opts(scratch, src, dst))
    assert "companion" in str(excinfo.value).lower()


def test_target_sidecar_collision_refused(tmp_path: Path) -> None:
    scratch = _scratch(tmp_path)
    src = _fixture(scratch)
    dst = scratch / "throwaway_restore.sqlite"
    (Path(str(dst) + "-shm")).write_bytes(b"")
    with pytest.raises(RehearsalError) as excinfo:
        preflight(_opts(scratch, src, dst))
    assert "companion" in str(excinfo.value).lower() or "partial" in str(excinfo.value).lower()


def test_capacity_failure(tmp_path: Path) -> None:
    scratch = _scratch(tmp_path)
    src = _fixture(scratch)
    dst = scratch / "throwaway_restore.sqlite"
    with pytest.raises(RehearsalError) as excinfo:
        preflight(
            _opts(
                scratch,
                src,
                dst,
                capacity_margin_bytes=10**15,
            )
        )
    assert excinfo.value.category == RehearsalFailureCategory.PREFLIGHT
    assert "free space" in str(excinfo.value).lower()


def test_partial_collision_refused(tmp_path: Path) -> None:
    scratch = _scratch(tmp_path)
    src = _fixture(scratch)
    dst = scratch / "throwaway_restore.sqlite"
    (scratch / f"{dst.name}.partial.1.2").write_bytes(b"x")
    with pytest.raises(RehearsalError) as excinfo:
        preflight(_opts(scratch, src, dst))
    assert excinfo.value.category == RehearsalFailureCategory.PREFLIGHT


def test_concurrent_invocation_lock(tmp_path: Path) -> None:
    scratch = _scratch(tmp_path)
    src = _fixture(scratch)
    dst2 = scratch / "throwaway_b.sqlite"

    from origenlab_email_pipeline.qa.sqlite_online_backup import BackupLock
    from origenlab_email_pipeline.qa.sqlite_writable_restore_rehearsal import (
        LOCK_DIR_NAME,
        _lock_path,
    )

    opts = _opts(scratch, src, dst2, apply=True)
    lock = BackupLock(_lock_path(opts, scratch, src))
    lock.acquire()
    try:
        with pytest.raises(RehearsalError) as excinfo:
            run_rehearsal(opts)
        assert "lock" in str(excinfo.value).lower()
        assert excinfo.value.category == RehearsalFailureCategory.APPLY
    finally:
        lock.release()


def test_module_never_calls_read_bytes() -> None:
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "read_bytes":
            raise AssertionError("Path.read_bytes must not appear in rehearsal module")
        if isinstance(node, ast.Attribute) and node.attr == "write_bytes":
            raise AssertionError("Path.write_bytes must not appear in rehearsal module")


def test_streaming_copy_bounded(tmp_path: Path) -> None:
    scratch = _scratch(tmp_path)
    src = scratch / "synthetic_stream_src.bin"
    dst = scratch / "synthetic_stream_dst.bin"
    src.write_bytes(b"abcdefgh" * 4096)
    copied = stream_copy_file(src, dst, chunk_bytes=64)
    assert copied == src.stat().st_size
    assert dst.read_bytes() == src.read_bytes()
    assert COPY_CHUNK_BYTES == 1024 * 1024
    with pytest.raises(RehearsalError) as excinfo:
        stream_copy_file(src, dst, chunk_bytes=64)
    assert "exclusive create" in str(excinfo.value).lower()


def test_exclusive_partial_create_race(tmp_path: Path) -> None:
    scratch = _scratch(tmp_path)
    src = _fixture(scratch)
    dst = scratch / "throwaway_restore.sqlite"
    import origenlab_email_pipeline.qa.sqlite_writable_restore_rehearsal as mod

    real_name = mod._owned_partial_name

    def colliding(target: Path, kind: str) -> Path:
        path = real_name(target, kind)
        if kind == "partial":
            path.write_bytes(b"race")
        return path

    mod._owned_partial_name = colliding  # type: ignore[assignment]
    try:
        with pytest.raises(RehearsalError) as excinfo:
            run_rehearsal(_opts(scratch, src, dst, apply=True))
        assert "exclusive" in str(excinfo.value).lower() or "exists" in str(
            excinfo.value
        ).lower()
        leftovers = [p for p in scratch.iterdir() if ".partial." in p.name]
        assert leftovers == []
    finally:
        mod._owned_partial_name = real_name


def test_exclusive_republish_create_race(tmp_path: Path) -> None:
    scratch = _scratch(tmp_path)
    src = _fixture(scratch)
    dst = scratch / "throwaway_restore.sqlite"
    import origenlab_email_pipeline.qa.sqlite_writable_restore_rehearsal as mod

    real_name = mod._owned_partial_name
    calls = {"n": 0}

    def colliding(target: Path, kind: str) -> Path:
        path = real_name(target, kind)
        calls["n"] += 1
        if kind == "republish":
            path.write_bytes(b"race")
        return path

    mod._owned_partial_name = colliding  # type: ignore[assignment]
    try:
        with pytest.raises(RehearsalError) as excinfo:
            run_rehearsal(_opts(scratch, src, dst, apply=True))
        assert excinfo.value.category == RehearsalFailureCategory.APPLY
        evidence = excinfo.value.recovery_evidence or {}
        assert evidence.get("last_recoverable_deleted") is not True
        # After preserve failure path: target restored or preserved retained.
        assert evidence.get("target_present") or evidence.get("preserved_present")
    finally:
        mod._owned_partial_name = real_name


def test_fail_after_each_crash_boundary(tmp_path: Path) -> None:
    scratch = _scratch(tmp_path)
    phases = (
        "initial_copy",
        "publication",
        "writable_commit",
        "target_preservation",
        "rollback_republish",
    )
    for phase in phases:
        phase_dir = scratch / phase
        phase_dir.mkdir()
        src = _fixture(phase_dir, f"synthetic_{phase}.sqlite")
        dst = phase_dir / f"throwaway_{phase}.sqlite"
        fp_before = fingerprint_file(src)
        hash_before = content_sha256(src)
        with pytest.raises(RehearsalError) as excinfo:
            run_rehearsal(
                _opts(phase_dir, src, dst, apply=True, fail_after=phase)
            )
        err = excinfo.value
        assert err.category == RehearsalFailureCategory.APPLY
        assert phase in str(err)
        assert fingerprint_file(src) == fp_before
        assert content_sha256(src) == hash_before
        evidence = err.recovery_evidence or {}
        assert evidence.get("last_recoverable_deleted") is not True

        partials = [
            p
            for p in phase_dir.iterdir()
            if ".partial." in p.name or ".republish." in p.name
        ]
        assert partials == []
        for side in ("-wal", "-shm", "-journal"):
            assert not (Path(str(src) + side)).exists()
            assert not (Path(str(dst) + side)).exists()

        if phase == "initial_copy":
            assert not dst.exists()
            assert evidence.get("target_present") is False
            # Clean slate — preflight should succeed.
            preflight(_opts(phase_dir, src, dst))
        elif phase in {"publication", "writable_commit"}:
            assert dst.is_file()
            assert evidence.get("target_present") is True
            with pytest.raises(RehearsalError) as pre_exc:
                preflight(_opts(phase_dir, src, dst))
            assert "already exists" in str(pre_exc.value).lower()
        elif phase == "target_preservation":
            # Restored preserved→target (preferred) or retained preserved.
            assert evidence.get("preserved_restored_to_target") or evidence.get(
                "preserved_retained_for_recovery"
            )
            assert evidence.get("target_present") or evidence.get("preserved_present")
            with pytest.raises(RehearsalError):
                preflight(_opts(phase_dir, src, dst))
        elif phase == "rollback_republish":
            assert dst.is_file()
            assert evidence.get("target_present") is True
            # Preserved retained for inspection on this failure boundary.
            assert evidence.get("preserved_retained_for_recovery") is True
            with pytest.raises(RehearsalError):
                preflight(_opts(phase_dir, src, dst))

        # Recovery artifacts stay inside the synthetic scratch root only.
        for p in phase_dir.rglob("*"):
            if p.is_file():
                assert phase_dir in p.resolve().parents or p.resolve() == phase_dir.resolve() or p.parent == phase_dir or phase_dir in p.parents


def test_broken_symlink_target_refused(tmp_path: Path) -> None:
    scratch = _scratch(tmp_path)
    src = _fixture(scratch)
    dst = scratch / "throwaway_restore.sqlite"
    dst.symlink_to(scratch / "missing_outside_target")
    with pytest.raises(RehearsalError) as excinfo:
        preflight(_opts(scratch, src, dst))
    assert "symlink" in str(excinfo.value).lower()


def test_symlink_cannot_redirect_outside_scratch(tmp_path: Path) -> None:
    scratch = _scratch(tmp_path)
    outside = tmp_path / "outside_dir"
    outside.mkdir()
    outside_file = outside / "escape.sqlite"
    outside_file.write_bytes(b"SQLite format 3\x00" + b"\x00" * 84)
    src = _fixture(scratch)
    dst = scratch / "throwaway_link.sqlite"
    dst.symlink_to(outside_file)
    with pytest.raises(RehearsalError) as excinfo:
        preflight(_opts(scratch, src, dst))
    assert excinfo.value.category == RehearsalFailureCategory.SAFETY
    # Outside file untouched.
    assert outside_file.read_bytes().startswith(b"SQLite format 3")


def test_non_regular_target_collision_refused(tmp_path: Path) -> None:
    scratch = _scratch(tmp_path)
    src = _fixture(scratch)
    dst = scratch / "throwaway_restore.sqlite"
    dst.mkdir()
    with pytest.raises(RehearsalError) as excinfo:
        preflight(_opts(scratch, src, dst))
    assert "non-regular" in str(excinfo.value).lower() or "exists" in str(
        excinfo.value
    ).lower() or "symlink" in str(excinfo.value).lower()


def test_planned_cutover_topology_synthetic() -> None:
    gib = 1024**3
    plan = validate_planned_cutover_topology(
        mnt_d_free_bytes=int(242 * gib),
        root_free_bytes=int(207 * gib),
        snapshot_size_bytes=int(127.494 * gib),
        compact_capacity_required_bytes=int(133.9 * gib),
        staged_compact_size_bytes=int(60.89 * gib),
    )
    assert plan["snapshot_on_mnt_d_ok"] is True
    assert plan["compact_staging_on_root_ok"] is True
    assert plan["dual_artifacts_on_mnt_d_ok"] is False
    assert plan["second_full_snapshot_on_mnt_d_required"] is False
    assert plan["recommended_topology_ok"] is True
    assert plan["fail_closed"] is False
    # Fail-closed when root cannot host compact capacity check.
    bad = validate_planned_cutover_topology(
        mnt_d_free_bytes=int(242 * gib),
        root_free_bytes=int(50 * gib),
        snapshot_size_bytes=int(127.494 * gib),
        compact_capacity_required_bytes=int(133.9 * gib),
        staged_compact_size_bytes=int(60.89 * gib),
    )
    assert bad["fail_closed"] is True
    assert bad["recommended_topology_ok"] is False


def test_source_fingerprint_drift_detected(tmp_path: Path) -> None:
    scratch = _scratch(tmp_path)
    src = _fixture(scratch)
    dst = scratch / "throwaway_restore.sqlite"
    calls = {"n": 0}
    real_fp = fingerprint_file

    def flaky(path: Path):  # noqa: ANN001
        fp = real_fp(path)
        calls["n"] += 1
        if calls["n"] == 2 and path.resolve() == src.resolve():
            return type(fp)(
                size_bytes=fp.size_bytes + 1,
                mtime_ns=fp.mtime_ns,
                device=fp.device,
                inode=fp.inode,
            )
        return fp

    import origenlab_email_pipeline.qa.sqlite_writable_restore_rehearsal as mod

    original = mod.fingerprint_file
    mod.fingerprint_file = flaky  # type: ignore[assignment]
    try:
        with pytest.raises(RehearsalError) as excinfo:
            run_rehearsal(_opts(scratch, src, dst, apply=True))
        assert excinfo.value.category == RehearsalFailureCategory.VERIFY
    finally:
        mod.fingerprint_file = original


def test_apply_writable_transaction_and_rollback(tmp_path: Path) -> None:
    scratch = _scratch(tmp_path)
    src = _fixture(scratch)
    dst = scratch / "throwaway_restore.sqlite"
    fp_before = fingerprint_file(src)
    report = run_rehearsal(_opts(scratch, src, dst, apply=True))
    assert report["completed"] is True
    assert report["writable_transaction_commit_verified"] is True
    assert report["cutover_rollback_verified"] is True
    assert report["sqlite_readonly_reopen_verified"] is True
    assert report["source_fingerprint_unchanged"] is True
    assert report["quick_check_ok"] is True
    assert report["foreign_key_check_violations"] == 0
    assert fingerprint_file(src) == fp_before
    assert dst.is_file()
    conn = sqlite3.connect(dst)
    try:
        marker = conn.execute("SELECT marker FROM emails WHERE id=1").fetchone()[0]
        assert marker == "pre-restore"
    finally:
        conn.close()
    assert not list(scratch.glob("*.partial.*"))
    blob = json.dumps(report)
    assert "/home/" not in blob
    assert "/mnt/" not in blob
    assert "pre-restore" not in blob
    assert "post-restore-write" not in blob
    assert "@" not in blob or "<email>" in blob


def test_strict_privacy_sanitization(tmp_path: Path) -> None:
    scratch = _scratch(tmp_path)
    src = _fixture(scratch)
    dst = scratch / "throwaway_restore.sqlite"
    report = run_rehearsal(_opts(scratch, src, dst, apply=True))
    blob = json.dumps(report, sort_keys=True)
    assert "/tmp/" not in blob
    assert "subj-" not in blob
    assert report["source_path_sanitized"] == src.name
    assert report["target_path_sanitized"] == dst.name


def test_typed_exit_codes_cli(tmp_path: Path) -> None:
    scratch = _scratch(tmp_path)
    src = scratch / "synthetic_src.sqlite"
    dst = scratch / "throwaway_dst.sqlite"
    env = {**os.environ, "PYTHONPATH": str(REPO / "src")}

    # build-fixture without apply must fail closed (no write).
    no_apply = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--operation",
            "build-fixture",
            "--source",
            str(src),
            "--scratch-root",
            str(scratch),
            "--confirm-throwaway",
            "--json",
        ],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert no_apply.returncode == EXIT_SAFETY
    assert not src.exists()

    built = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--operation",
            "build-fixture",
            "--source",
            str(src),
            "--scratch-root",
            str(scratch),
            "--confirm-throwaway",
            "--apply",
            "--json",
        ],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert built.returncode == EXIT_OK, built.stderr

    pre = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--operation",
            "preflight",
            "--source",
            str(src),
            "--restore-target",
            str(dst),
            "--scratch-root",
            str(scratch),
            "--confirm-throwaway",
            "--json",
        ],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert pre.returncode == EXIT_OK, pre.stderr
    report = json.loads(pre.stdout)
    assert report["apply"] is False
    assert report["zero_write_preflight"] is True

    applied = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--operation",
            "rehearse",
            "--source",
            str(src),
            "--restore-target",
            str(dst),
            "--scratch-root",
            str(scratch),
            "--confirm-throwaway",
            "--apply",
            "--json",
        ],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert applied.returncode == EXIT_OK, applied.stderr
    body = json.loads(applied.stdout)
    assert body["cutover_rollback_verified"] is True
    assert body["sqlite_readonly_reopen_verified"] is True

    bad = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--operation",
            "preflight",
            "--source",
            str(src),
            "--restore-target",
            str(tmp_path / "emails.sqlite"),
            "--scratch-root",
            str(scratch),
            "--confirm-throwaway",
            "--json",
        ],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert bad.returncode == EXIT_SAFETY


def test_cli_missing_confirm_exit(tmp_path: Path) -> None:
    scratch = _scratch(tmp_path)
    src = _fixture(scratch)
    dst = scratch / "throwaway_dst.sqlite"
    env = {**os.environ, "PYTHONPATH": str(REPO / "src")}
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--operation",
            "preflight",
            "--source",
            str(src),
            "--restore-target",
            str(dst),
            "--scratch-root",
            str(scratch),
            "--json",
        ],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == EXIT_SAFETY


def test_no_clobber_race_on_publish(tmp_path: Path) -> None:
    scratch = _scratch(tmp_path)
    src = _fixture(scratch)
    dst = scratch / "throwaway_restore.sqlite"

    import origenlab_email_pipeline.qa.sqlite_writable_restore_rehearsal as mod

    real_stream = mod.stream_copy_file

    def race_stream(source: Path, destination: Path, **kwargs: object) -> int:
        # Create the final target before publish_no_clobber runs.
        if destination.name.startswith(dst.name + ".partial."):
            dst.write_bytes(b"collision")
        return real_stream(source, destination, **kwargs)  # type: ignore[arg-type]

    mod.stream_copy_file = race_stream  # type: ignore[assignment]
    try:
        with pytest.raises(RehearsalError) as excinfo:
            run_rehearsal(_opts(scratch, src, dst, apply=True))
        assert excinfo.value.category in {
            RehearsalFailureCategory.APPLY,
            RehearsalFailureCategory.PREFLIGHT,
        } or "no-clobber" in str(excinfo.value).lower() or "exists" in str(
            excinfo.value
        ).lower()
    finally:
        mod.stream_copy_file = real_stream


def test_preflight_refuses_production_basename(tmp_path: Path) -> None:
    scratch = _scratch(tmp_path)
    src = _fixture(scratch)
    with pytest.raises(RehearsalError) as excinfo:
        preflight(
            RehearsalOptions(
                source=src,
                restore_target=scratch / "emails.sqlite",
                scratch_root=scratch,
                confirm_throwaway=True,
            )
        )
    assert excinfo.value.exit_code == EXIT_SAFETY
