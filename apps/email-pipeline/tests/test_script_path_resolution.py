from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_script(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[call-arg]
    return module


def test_report_scripts_resolve_repo_root_and_canonical_paths() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    scripts_root = repo_root / "scripts"

    run_all = _load_script(scripts_root / "reports" / "run_all_reports.py", "run_all_reports")
    gen = _load_script(
        scripts_root / "reports" / "generate_client_report.py",
        "generate_client_report",
    )

    assert run_all._repo_root() == repo_root
    assert gen._repo_root() == repo_root

    # Verify known derived locations now resolve under the app root.
    assert (
        run_all._repo_root() / "scripts" / "maintenance" / "dedupe_canonical_gmail_messages.py"
    ).is_file()
    assert (run_all._repo_root() / "scripts" / "reports" / "generate_client_report.py").is_file()
    assert (gen._repo_root() / "docs" / "REPORT_SCOPE_CLIENT.md").is_file()


def test_run_all_reports_invokes_expected_child_scripts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    mod = _load_script(
        repo_root / "scripts" / "reports" / "run_all_reports.py",
        "run_all_reports_wiring",
    )

    db = tmp_path / "emails.sqlite"
    db.touch()
    out_dir = tmp_path / "run_out"

    class DummySettings:
        def resolved_sqlite_path(self) -> Path:
            return db

    recorded: list[tuple[list[str], str | None]] = []

    def fake_run(cmd, cwd=None, **_kwargs):
        recorded.append((list(cmd), cwd))
        class R:
            returncode = 0

        return R()

    monkeypatch.setattr(mod, "load_settings", lambda: DummySettings())
    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_all_reports.py", "--fast", "--out", str(out_dir)],
    )

    mod.main()

    assert len(recorded) == 2
    exp_export = str(repo_root / "scripts" / "tools" / "export_unique_emails_csv.py")
    exp_client = str(repo_root / "scripts" / "reports" / "generate_client_report.py")
    assert recorded[0][0][1] == exp_export
    assert recorded[1][0][1] == exp_client
    assert recorded[0][1] == str(repo_root)
    assert recorded[1][1] == str(repo_root)
    assert "--out" in recorded[0][0]
    assert str(out_dir / "unique_emails.csv") in recorded[0][0]
    assert "--with-business-filter" in recorded[1][0]
    assert "--fast" in recorded[1][0]


def test_run_all_reports_dedupe_uses_guarded_maintenance_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    mod = _load_script(
        repo_root / "scripts" / "reports" / "run_all_reports.py",
        "run_all_reports_dedupe_wiring",
    )

    db = tmp_path / "emails.sqlite"
    db.touch()
    out_dir = tmp_path / "run_out"

    class DummySettings:
        def resolved_sqlite_path(self) -> Path:
            return db

    recorded: list[tuple[list[str], str | None]] = []

    def fake_run(cmd, cwd=None, **_kwargs):
        recorded.append((list(cmd), cwd))

        class R:
            returncode = 0

        return R()

    monkeypatch.setattr(mod, "load_settings", lambda: DummySettings())
    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_all_reports.py", "--dedupe", "--out", str(out_dir)],
    )

    mod.main()

    assert len(recorded) == 3
    exp_dedupe = str(
        repo_root / "scripts" / "maintenance" / "dedupe_canonical_gmail_messages.py"
    )
    assert recorded[0][0][1] == exp_dedupe
    assert "--apply" not in recorded[0][0]
    assert "--ack-sqlite-backup" not in recorded[0][0]
