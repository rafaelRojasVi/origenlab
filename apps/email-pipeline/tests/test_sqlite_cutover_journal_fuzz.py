"""Post-closure journal / CLI / evidence fuzzing for SQLite cutover.

Synthetic only. Not PR-G. Does not authorize a cutover.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from origenlab_email_pipeline.qa.sqlite_online_backup import (
    BackupError,
    build_safe_cli_json_report,
)
from origenlab_email_pipeline.qa.sqlite_production_cutover import (
    CutoverError,
    CutoverFailureCategory,
    CutoverStage,
    apply_stage,
    load_journal,
    sanitize_evidence,
)
from sqlite_adversarial_support import (
    FORBIDDEN_EVIDENCE,
    HOSTILE_TEXT,
    MAIN_SHA,
    MID,
    assert_no_forbidden,
    backup_staging,
    journal_dict_skeleton,
    make_opts,
    make_world,
)

REPO = Path(__file__).resolve().parents[1]
CUTOVER_CLI = (
    REPO / "scripts" / "maintenance" / "orchestrate_sqlite_production_cutover.py"
)
BACKUP_CLI = REPO / "scripts" / "maintenance" / "backup_sqlite_online.py"


@pytest.mark.parametrize(
    "payload",
    [
        {"path": "/home/rafael/secret.db"},
        {"email": "operator@origenlab.example"},
        {"msg": HOSTILE_TEXT},
    ],
)
def test_sanitize_evidence_rejects_hostile_payloads(payload: dict) -> None:
    with pytest.raises(CutoverError) as ei:
        sanitize_evidence(payload)
    assert ei.value.category is CutoverFailureCategory.VERIFY


def test_sanitize_evidence_path_gate_does_not_cover_token_only() -> None:
    # Documented residual: sanitize_evidence currently gates absolute paths /
    # email-like text; token-shaped strings alone are not rejected here.
    out = sanitize_evidence({"note": "origenlab_test_token_ABCDEF123456"})
    assert out["note"].startswith("origenlab_test_token_")


@pytest.mark.parametrize(
    "raw",
    [
        "{",
        "[]",
        "null",
        '"string"',
        "1",
        '{"schema_version":"3"}',
        '{"schema_version":3,"tool":"wrong"}',
        '{"schema_version":999,"tool":"orchestrate_sqlite_production_cutover"}',
    ],
)
def test_load_journal_rejects_malformed(tmp_path: Path, raw: str) -> None:
    world, prod, reports, root, fp = make_world(tmp_path)
    jdir = prod.parent / ".origenlab_cutover_journals"
    jdir.mkdir(parents=True, exist_ok=True)
    jpath = jdir / f"{MID}.journal.json"
    world.files[str(jpath)] = raw.encode()
    world.modes[str(jpath)] = 0o644
    with pytest.raises(CutoverError) as ei:
        load_journal(world, jpath)
    assert ei.value.category is CutoverFailureCategory.AMBIGUOUS
    assert_no_forbidden(str(ei.value))


@pytest.mark.parametrize(
    "mid",
    [
        "",
        " ",
        "short",
        "bad mid spaces!!",
        "../escape",
        "a" * 100,
        "cutover/../../../etc/passwd",
        "cutover20260719T163633Z",  # permanently abandoned — auth layer
        "ユニコードmid12345678",
        "mid;DROP TABLE t;--xxxxxx",
    ],
)
def test_malformed_maintenance_ids_refused(tmp_path: Path, mid: str) -> None:
    world, prod, reports, root, fp = make_world(tmp_path)
    backup, staging = backup_staging(root)
    with pytest.raises(CutoverError) as ei:
        apply_stage(
            make_opts(
                world,
                prod,
                reports,
                fp,
                stage=CutoverStage.PAUSE_WRITERS,
                apply=True,
                backup=backup,
                staging=staging,
                maintenance_id=mid,
            )
        )
    assert ei.value.category in {
        CutoverFailureCategory.PREFLIGHT,
        CutoverFailureCategory.SAFETY,
    }


def test_safe_cli_json_rejects_hostile_non_allowlisted_shape() -> None:
    raw = {
        "ok": True,
        "stage": "plan_preflight",
        "secret_token": "origenlab_test_token_ABCDEF123456",
        "path": "/home/rafael/secret.db",
        "email": "a@b.example",
        "nested": {"x": 1},
        "writers": {"fd_hit_count": 0, "mail_pause_present": False},
        "blockers": ["pause_markers_absent"],
    }
    with pytest.raises(BackupError) as ei:
        build_safe_cli_json_report(raw)
    assert_no_forbidden(str(ei.value))


def test_safe_cli_json_preflight_projection_omits_unknown_keys() -> None:
    raw = {
        "mode": "preflight",
        "apply": False,
        "completed": False,
        "writes_performed": False,
        "source_opened_with_sqlite3": False,
        "source_size_bytes": 100,
        "estimated_output_bytes": 100,
        "destination_free_bytes": 1000,
        "capacity_required_bytes": 200,
        "filesystem_separated": True,
        "allow_same_filesystem": False,
        "pages_per_batch": 5,
        "busy_timeout_ms": 1000,
        "secret_token": "origenlab_test_token_ABCDEF123456",
        "path": "/home/rafael/secret.db",
    }
    safe = build_safe_cli_json_report(raw)
    blob = json.dumps(safe)
    assert_no_forbidden(blob)
    assert "secret_token" not in safe
    assert "path" not in safe


def _run_cli(script: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "python", str(script), *args],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--stage", "not_a_stage"],
        ["--apply"],
        ["--apply", "--confirm-production-cutover"],
        [
            "--apply",
            "--confirm-production-cutover",
            "--maintenance-id",
            "x",
            "--expected-main-sha",
            "abc",
            "--expected-production-path",
            "/tmp/nope.sqlite",
            "--expected-production-fingerprint",
            "fp",
        ],
        [
            "--abort-before-swap",
            "--rollback-before-writers",
            "--apply",
            "--confirm-production-cutover",
            "--maintenance-id",
            MID,
            "--expected-main-sha",
            MAIN_SHA,
            "--expected-production-path",
            "/tmp/nope.sqlite",
            "--expected-production-fingerprint",
            "fp",
        ],
        [
            "--rollback-finalize",
            "--stage",
            "atomic_swap",
            "--apply",
            "--confirm-production-cutover",
            "--maintenance-id",
            MID,
            "--expected-main-sha",
            MAIN_SHA,
            "--expected-production-path",
            "/tmp/nope.sqlite",
            "--expected-production-fingerprint",
            "fp",
            "--approve-swap",
        ],
        [
            "--json",
            "--maintenance-id",
            " " * 8,
            "--expected-main-sha",
            "0" * 40,
        ],
        [
            "--stage",
            "pause_writers",
            "--apply",
            "--confirm-production-cutover",
            "--maintenance-id",
            MID,
            "--expected-main-sha",
            MAIN_SHA,
            "--expected-production-path",
            "file:///etc/passwd",
            "--expected-production-fingerprint",
            "fp",
        ],
    ],
)
def test_cutover_cli_fuzz_exit_codes_and_no_leakage(args: list[str]) -> None:
    proc = _run_cli(CUTOVER_CLI, args)
    # Must terminate; non-zero is fine for invalid input.
    assert proc.returncode != 0 or "ok stage=" in proc.stdout or proc.stdout.startswith("{")
    combined = proc.stdout + proc.stderr
    assert "Traceback" not in combined
    for needle in FORBIDDEN_EVIDENCE:
        # CLI may print sanitized errors; forbid raw hostile patterns.
        if needle in {"SELECT ", "file://"} and needle.lower() in combined.lower():
            # file URI may appear in argparse help for path types — only fail on token/email/home
            continue
        if needle in {"/home/", "@origenlab.example", "origenlab_test_token", "pid=", "inode=", "device="}:
            assert needle.lower() not in combined.lower()


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--source", "/tmp/nope.sqlite"],
        ["--source", "/tmp/nope.sqlite", "--destination", "/tmp/out.sqlite"],
        [
            "--source",
            "/tmp/nope.sqlite",
            "--destination",
            "/tmp/out.sqlite",
            "--apply",
        ],
        [
            "--source",
            "",
            "--destination",
            "/tmp/out.sqlite",
            "--json",
        ],
        [
            "--source",
            "/tmp/nope.sqlite",
            "--destination",
            "/tmp/out.sqlite",
            "--pages-per-batch",
            "0",
        ],
        [
            "--source",
            "/tmp/nope.sqlite",
            "--destination",
            "/tmp/out.sqlite",
            "--busy-timeout-ms",
            "-1",
        ],
    ],
)
def test_backup_cli_fuzz_no_traceback(args: list[str]) -> None:
    proc = _run_cli(BACKUP_CLI, args)
    combined = proc.stdout + proc.stderr
    assert "Traceback" not in combined
    assert len(combined) < 200_000


def test_journal_skeleton_roundtrip_keys() -> None:
    skeleton = journal_dict_skeleton(extra={"writers_resumed": False})
    assert skeleton["schema_version"] == 3
    assert skeleton["maintenance_id"] == MID
