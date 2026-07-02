"""Postgres read-model dependency failures return safe 503 errors."""

from __future__ import annotations

import json

import pytest

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


def test_postgres_connection_wraps_psycopg_errors_without_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pg_common, "psycopg", _FakePsycopg)
    settings = Settings(postgres_url="postgresql://user:password@127.0.0.1:5432/origenlab")

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
