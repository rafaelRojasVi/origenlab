"""Local validate script matches README and safe operator checks."""

from __future__ import annotations

from pathlib import Path

_PIPELINE_ROOT = Path(__file__).resolve().parents[1]
_VALIDATE_SCRIPT = _PIPELINE_ROOT / "scripts" / "validate.sh"
_README = _PIPELINE_ROOT / "README.md"

_PYTEST_FILES = (
    "tests/test_operator_cli.py",
    "tests/test_operator_status_report.py",
    "tests/test_daily_core_manifest.py",
    "tests/test_daily_core_docs.py",
    "tests/test_script_map_docs.py",
    "tests/test_module_facade_audit.py",
)


def test_validate_script_exists_and_runs_safe_checks() -> None:
    assert _VALIDATE_SCRIPT.is_file()
    text = _VALIDATE_SCRIPT.read_text(encoding="utf-8")
    # Bootstrapping goes through the shared script so validate.sh cannot prune
    # data-tools/lab out of the venv and leave the suite under-collected.
    assert "./scripts/sync_test_env.sh" in text
    assert "uv sync" not in text
    sync_text = (_PIPELINE_ROOT / "scripts" / "sync_test_env.sh").read_text(encoding="utf-8")
    assert "--group dev" in sync_text
    assert "--group gmail" in sync_text
    for path in _PYTEST_FILES:
        assert path in text
    assert "uv run origenlab status" in text
    assert "uv run origenlab daily-core" in text
    assert "uv run origenlab refresh-dashboard" in text
    assert "uv run origenlab audit-facades -- --fail-on-manual-review" in text
    assert "--apply" not in text


def test_readme_documents_validate_script() -> None:
    readme = _README.read_text(encoding="utf-8")
    assert "./scripts/validate.sh" in readme
