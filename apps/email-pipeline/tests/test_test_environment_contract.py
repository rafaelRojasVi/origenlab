"""Fail loudly when the venv is missing groups the suite needs to collect.

`uv sync` prunes the environment to exactly the groups it is given. Syncing a
narrower set drops pandas/openai, and the suite then *under-collects*: pytest
reports collection errors that are easy to mistake for an unrelated regression
and a smaller "passed" count that looks authoritative. This module turns that
silent shrinkage into one obvious failure naming the fix.
"""

from __future__ import annotations

import importlib
import subprocess
from pathlib import Path

import pytest


APP_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_COMMAND = "./scripts/test.sh"

# Modules whose absence makes test modules fail at import (collection) time.
# Restricted to groups CI installs, so this contract holds in CI too.
COLLECTION_CRITICAL_MODULES = (
    ("pandas", "data-tools"),
    ("xlrd", "data-tools"),
    ("openai", "lab"),
)


@pytest.mark.parametrize(("module_name", "group"), COLLECTION_CRITICAL_MODULES)
def test_collection_critical_dependency_is_installed(module_name: str, group: str) -> None:
    try:
        importlib.import_module(module_name)
    except ImportError as exc:  # pragma: no cover - only on a mis-synced venv
        pytest.fail(
            f"{module_name!r} (dependency group {group!r}) is missing, so parts of the "
            f"suite cannot be collected and any pass count from this run is NOT "
            f"authoritative.\nBootstrap the canonical environment with "
            f"`{CANONICAL_COMMAND}` from apps/email-pipeline.\nImport error: {exc}"
        )


def test_env_bootstrap_scripts_share_one_group_list() -> None:
    """validate.sh must not re-sync a narrower group set than the suite needs."""
    validate = (APP_ROOT / "scripts" / "validate.sh").read_text(encoding="utf-8")
    assert "sync_test_env.sh" in validate, (
        "scripts/validate.sh must bootstrap through scripts/sync_test_env.sh; a "
        "separate `uv sync` line there prunes data-tools/lab out of the venv."
    )
    assert "uv sync" not in validate, (
        "scripts/validate.sh must not call `uv sync` directly - that reintroduces "
        "the pruning footgun sync_test_env.sh exists to prevent."
    )


def test_canonical_bootstrap_covers_every_collection_critical_group() -> None:
    sync_script = (APP_ROOT / "scripts" / "sync_test_env.sh").read_text(encoding="utf-8")
    for _module_name, group in COLLECTION_CRITICAL_MODULES:
        assert f"--group {group}" in sync_script, (
            f"scripts/sync_test_env.sh must install the {group!r} group"
        )


def test_bootstrap_scripts_are_executable() -> None:
    for name in ("sync_test_env.sh", "test.sh"):
        path = APP_ROOT / "scripts" / name
        mode = subprocess.run(
            ["git", "ls-files", "-s", str(path.relative_to(APP_ROOT.parents[1]))],
            cwd=APP_ROOT.parents[1],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.split()
        if mode:
            assert mode[0] == "100755", f"scripts/{name} must be tracked as executable"
