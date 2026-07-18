"""Shared pytest fixtures for apps/api."""

from __future__ import annotations

import pytest

from origenlab_api.settings import get_settings
from origenlab_api.sqlite_ro import RECOVERY_FORBIDDEN_ENV_VARS


@pytest.fixture(autouse=True)
def _isolate_origenlab_api_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default tests to SQLite backend unless they set env explicitly."""
    monkeypatch.setenv("ORIGENLAB_API_BACKEND", "sqlite")
    # Keep recovery admission deterministic on developer machines that export
    # Postgres/Gmail/token process variables for ordinary operator work.
    for name in RECOVERY_FORBIDDEN_ENV_VARS:
        monkeypatch.setenv(name, "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
