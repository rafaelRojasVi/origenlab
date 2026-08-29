"""Unit tests for hydrate_realdata_preview.py's disposable-database admission
gate (_require_local_dsn). No Postgres connection is made by these tests."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "hydrate_realdata_preview.py"
_SPEC = importlib.util.spec_from_file_location("hydrate_realdata_preview", _MODULE_PATH)
assert _SPEC and _SPEC.loader
_hydrate = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _hydrate
_SPEC.loader.exec_module(_hydrate)

_require_local_dsn = _hydrate._require_local_dsn


def _require(dsn: str, *, confirm_disposable: bool) -> None:
    _require_local_dsn(dsn, label="--postgres-write-url", confirm_disposable=confirm_disposable)


def test_remote_host_rejected() -> None:
    with pytest.raises(SystemExit):
        _require(
            "postgresql://user:pass@example.com/origenlab_realdata_preview",
            confirm_disposable=True,
        )


def test_localhost_generic_non_preview_database_rejected() -> None:
    with pytest.raises(SystemExit):
        _require("postgresql://user:pass@localhost/mydb", confirm_disposable=True)


def test_localhost_default_postgres_database_rejected() -> None:
    with pytest.raises(SystemExit):
        _require("postgresql://user:pass@localhost/postgres", confirm_disposable=True)


def test_localhost_origenlab_default_database_rejected() -> None:
    with pytest.raises(SystemExit):
        _require("postgresql://user:pass@localhost/origenlab", confirm_disposable=True)


def test_localhost_empty_database_name_rejected() -> None:
    with pytest.raises(SystemExit):
        _require("postgresql://user:pass@localhost/", confirm_disposable=True)


def test_localhost_preview_database_accepted_with_confirmation() -> None:
    # Must not raise.
    _require(
        "postgresql://user:pass@localhost/origenlab_realdata_preview",
        confirm_disposable=True,
    )


def test_localhost_shadow_database_accepted_with_confirmation() -> None:
    _require(
        "postgresql://user:pass@127.0.0.1/origenlab_realdata_shadow_v1",
        confirm_disposable=True,
    )


def test_localhost_test_database_accepted_with_confirmation() -> None:
    _require(
        "postgresql://user:pass@localhost/origenlab_test_db",
        confirm_disposable=True,
    )


def test_localhost_preview_database_rejected_without_confirmation() -> None:
    with pytest.raises(SystemExit):
        _require(
            "postgresql://user:pass@localhost/origenlab_realdata_preview",
            confirm_disposable=False,
        )
