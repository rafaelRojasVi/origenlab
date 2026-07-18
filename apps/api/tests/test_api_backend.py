"""DB-3A: API backend factory and Postgres operator status mapping."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from origenlab_api.backends.factory import get_repository_bundle, validate_api_settings
from origenlab_api.main import create_app
from origenlab_api.repositories.postgres.operator import (
    PostgresOperatorStatusRepository,
    map_operator_status_row,
)
from origenlab_api.repositories.sqlite.contact import SqliteContactRepository
from origenlab_api.repositories.sqlite.email import SqliteEmailRecentRepository
from origenlab_api.repositories.sqlite.equipment import SqliteEquipmentOpportunityRepository
from origenlab_api.repositories.sqlite.operator import SqliteOperatorStatusRepository
from origenlab_api.repositories.sqlite.warm_cases import SqliteWarmCaseRepository
from origenlab_api.settings import Settings, get_settings


def _clear_settings_cache() -> None:
    get_settings.cache_clear()


def test_settings_default_backend_is_sqlite() -> None:
    _clear_settings_cache()
    settings = Settings()
    assert settings.resolved_api_backend() == "sqlite"
    assert settings.postgres_configured() is False


def test_invalid_backend_raises_clear_error() -> None:
    settings = Settings(api_backend="mysql")
    with pytest.raises(ValueError, match="ORIGENLAB_API_BACKEND"):
        settings.resolved_api_backend()


def test_postgres_backend_without_url_fails_clearly() -> None:
    settings = Settings(api_backend="postgres", postgres_url=None)
    with pytest.raises(ValueError, match="ORIGENLAB_POSTGRES_URL"):
        validate_api_settings(settings)


def test_create_app_postgres_without_url_fails_at_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORIGENLAB_API_BACKEND", "postgres")
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "")
    _clear_settings_cache()
    with pytest.raises(ValueError, match="ORIGENLAB_POSTGRES_URL"):
        create_app()


def test_health_shows_backend_sqlite_default() -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    _clear_settings_cache()
    client = TestClient(create_app())
    data = client.get("/health").json()
    assert data["backend"] == "sqlite"
    assert data["postgres_configured"] is False
    assert data["mode"] == "operator-sqlite-readonly"
    assert set(data.keys()) == {
        "ok",
        "service",
        "mode",
        "backend",
        "postgres_configured",
        "sqlite_immutable",
        "sqlite_query_only",
        "recovery_mode",
        "dotenv_disabled",
    }


def test_health_shows_postgres_backend_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    monkeypatch.setenv("ORIGENLAB_API_BACKEND", "postgres")
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://user:pass@127.0.0.1:5432/testdb")
    _clear_settings_cache()
    client = TestClient(create_app())
    data = client.get("/health").json()
    assert data["backend"] == "postgres"
    assert data["postgres_configured"] is True
    assert data["mode"] == "operator-postgres-mirror-readonly"


def test_map_operator_status_row_fixture() -> None:
    row = {
        "verdict": "READY",
        "sqlite_path_redacted": "/data/emails.sqlite",
        "campaign_mode": "equipment_first",
        "warnings_json": ["Postgres mirror last sync older than 24h"],
        "outbound_readiness_json": {
            "verdict": "mirror_ok",
            "sync_run_id": 42,
            "note": "SQLite remains authoritative",
        },
    }
    mapped = map_operator_status_row(row)
    assert mapped == {
        "verdict": "READY",
        "sqlite_path": "/data/emails.sqlite",
        "campaign_mode": "equipment_first",
        "operator_focus": None,
        "outbound_readiness": "mirror_ok",
        "warnings": ["Postgres mirror last sync older than 24h"],
    }


def test_map_operator_status_row_parses_json_strings() -> None:
    row = {
        "verdict": "CAUTION",
        "sqlite_path_redacted": "",
        "campaign_mode": None,
        "warnings_json": json.dumps(["classification mirror empty"]),
        "outbound_readiness_json": json.dumps({"verdict": "mirror_stale"}),
    }
    mapped = map_operator_status_row(row)
    assert mapped["warnings"] == ["classification mirror empty"]
    assert mapped["outbound_readiness"] == "mirror_stale"
    assert mapped["sqlite_path"] == ""


def test_repository_bundle_sqlite_uses_sqlite_operator(tmp_path: Path) -> None:
    settings = Settings(
        api_backend="sqlite",
        sqlite_path=tmp_path / "x.sqlite",
        active_current=tmp_path / "current",
    )
    bundle = get_repository_bundle(settings)
    assert isinstance(bundle.operator, SqliteOperatorStatusRepository)
    assert isinstance(bundle.equipment, SqliteEquipmentOpportunityRepository)
    assert isinstance(bundle.warm_cases, SqliteWarmCaseRepository)
    assert isinstance(bundle.email_recent, SqliteEmailRecentRepository)
    assert isinstance(bundle.contact, SqliteContactRepository)


def test_repository_bundle_postgres_uses_postgres_operator() -> None:
    from origenlab_api.repositories.postgres.equipment import PostgresEquipmentOpportunityRepository
    from origenlab_api.repositories.postgres.contact import PostgresContactRepository
    from origenlab_api.repositories.postgres.email import PostgresEmailRecentRepository
    from origenlab_api.repositories.postgres.warm_cases import PostgresWarmCaseRepository

    settings = Settings(
        api_backend="postgres",
        postgres_url="postgresql://127.0.0.1:5432/scratch",
    )
    bundle = get_repository_bundle(settings)
    assert isinstance(bundle.operator, PostgresOperatorStatusRepository)
    assert isinstance(bundle.equipment, PostgresEquipmentOpportunityRepository)
    assert isinstance(bundle.warm_cases, PostgresWarmCaseRepository)
    assert isinstance(bundle.email_recent, PostgresEmailRecentRepository)
    assert isinstance(bundle.contact, PostgresContactRepository)


def test_hybrid_postgres_operator_keeps_sqlite_paths_for_other_services(
    tmp_path: Path,
) -> None:
    """DB-3A hybrid: operator repo is Postgres; SQLite paths remain for other routes."""
    db = tmp_path / "emails.sqlite"
    settings = Settings(
        api_backend="postgres",
        postgres_url="postgresql://127.0.0.1:5432/scratch",
        sqlite_path=db,
        active_current=tmp_path / "current",
    )
    bundle = get_repository_bundle(settings)
    assert isinstance(bundle.operator, PostgresOperatorStatusRepository)
    assert settings.resolved_sqlite_path() == db.resolve()
