"""Postgres read-model dependency failures return safe 503 errors."""

from __future__ import annotations

import json

import pytest

from origenlab_email_pipeline.postgres_dashboard_api import db as mirror_db

from origenlab_api.repositories.postgres import common as pg_common
from origenlab_api.repositories.postgres.common import (
    PostgresBackendUnavailableError,
    postgres_connection,
)
from origenlab_api.settings import Settings, get_settings


class _FakePsycopg:
    class Error(Exception):
        pass

    @staticmethod
    def connect(*args, **kwargs):
        raise _FakePsycopg.Error(
            "could not connect to postgresql://user:password@127.0.0.1:5432/origenlab"
        )


def test_is_psycopg_error_true_for_real_pg_error_instance() -> None:
    assert pg_common.is_psycopg_error(_FakePsycopg, _FakePsycopg.Error("boom")) is True


def test_is_psycopg_error_false_for_unrelated_exception() -> None:
    assert pg_common.is_psycopg_error(_FakePsycopg, ValueError("not a pg error")) is False


def test_is_psycopg_error_false_for_pg_without_usable_error_class() -> None:
    class _NoErrorAttr:
        pass

    assert pg_common.is_psycopg_error(_NoErrorAttr, ValueError("boom")) is False

    class _NonTypeError:
        Error = "not-a-class"

    assert pg_common.is_psycopg_error(_NonTypeError, ValueError("boom")) is False


def test_postgres_connection_wraps_psycopg_errors_without_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pg_common, "psycopg", _FakePsycopg)
    settings = Settings(
        postgres_url="postgresql://user:password@127.0.0.1:5432/origenlab"
    )

    with pytest.raises(PostgresBackendUnavailableError) as excinfo:
        with postgres_connection(settings):
            raise AssertionError("connection should fail before yielding")

    message = str(excinfo.value)
    assert "Postgres read model unavailable" in message
    assert "postgresql://" not in message
    assert "password" not in message.lower()


def test_postgres_backend_unavailable_returns_safe_503_json(
    tmp_path,
) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from origenlab_api.main import create_app

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        sqlite_path=tmp_path / "missing.sqlite",
        active_current=tmp_path / "current",
    )

    @app.get("/__contract_test_postgres_backend_unavailable")
    def _boom() -> None:
        raise PostgresBackendUnavailableError(
            "Postgres read model unavailable: postgresql://user:password@127.0.0.1:5432/origenlab"
        )

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/__contract_test_postgres_backend_unavailable")

    assert response.status_code == 503
    assert response.headers.get("X-Request-ID")
    body = response.json()
    assert body["error"]["code"] == "backend_unavailable"
    assert body["error"]["details"] == {"backend": "postgres"}
    assert body["error"]["request_id"] == response.headers.get("X-Request-ID")

    text = json.dumps(body)
    assert "postgresql://" not in text
    assert "password" not in text.lower()
    assert "traceback" not in text.lower()


def _postgres_route_client(monkeypatch: pytest.MonkeyPatch, tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from origenlab_api.main import create_app

    monkeypatch.delenv("ORIGENLAB_ENV", raising=False)
    monkeypatch.setenv("ORIGENLAB_DISABLE_DOTENV", "1")
    monkeypatch.setenv("ORIGENLAB_API_BACKEND", "postgres")
    monkeypatch.setenv(
        "ORIGENLAB_POSTGRES_URL",
        "postgresql://user:password@127.0.0.1:5432/origenlab",
    )
    monkeypatch.delenv(
        "ORIGENLAB_COMMERCIAL_OPERATIONS_WRITES_ENABLED",
        raising=False,
    )
    monkeypatch.delenv(
        "ORIGENLAB_POSTGRES_WRITE_URL",
        raising=False,
    )
    get_settings.cache_clear()

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        api_backend="postgres",
        postgres_url="postgresql://user:password@127.0.0.1:5432/origenlab",
        sqlite_path=tmp_path / "unused.sqlite",
        active_current=tmp_path / "current",
    )
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    "path",
    [
        "/cases/warm",
        "/operator/status",
        "/operator/automation-status",
    ],
)
def test_postgres_backed_routes_return_safe_503_when_read_model_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    path: str,
) -> None:
    monkeypatch.setattr(pg_common, "psycopg", _FakePsycopg)
    client = _postgres_route_client(monkeypatch, tmp_path)

    response = client.get(path, headers={"X-Request-ID": "test-pg-down"})

    assert response.status_code == 503
    assert response.headers.get("X-Request-ID") == "test-pg-down"
    body = response.json()
    assert body["error"]["code"] == "backend_unavailable"
    assert body["error"]["message"] == "Postgres read model unavailable."
    assert body["error"]["details"] == {"backend": "postgres"}
    assert body["error"]["request_id"] == "test-pg-down"

    text = json.dumps(body)
    assert "postgresql://" not in text
    assert "password" not in text.lower()
    assert "127.0.0.1" not in text
    assert "traceback" not in text.lower()


@pytest.mark.parametrize(
    "path",
    [
        "/mirror/catalog/products",
        "/mirror/leads/summary",
        "/mirror/leads/prospects",
        "/mirror/audits/gmail-interactions",
        "/mirror/commercial/deals",
    ],
)
def test_mirror_routes_return_safe_503_when_postgres_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    path: str,
) -> None:
    monkeypatch.setattr(mirror_db, "psycopg", _FakePsycopg)
    client = _postgres_route_client(monkeypatch, tmp_path)

    response = client.get(path, headers={"X-Request-ID": "test-mirror-pg-down"})

    assert response.status_code == 503
    assert response.headers.get("X-Request-ID") == "test-mirror-pg-down"
    body = response.json()
    assert body["error"]["code"] == "backend_unavailable"
    assert body["error"]["request_id"] == "test-mirror-pg-down"

    text = json.dumps(body)
    assert "postgresql://" not in text
    assert "password" not in text.lower()
    assert "127.0.0.1" not in text
    assert "traceback" not in text.lower()
