"""Health endpoint tests."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from origenlab_api.main import create_app


def test_health_returns_stable_contract() -> None:
    client = TestClient(create_app())
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["service"] == "origenlab-api"
    assert data["mode"] == "operator-sqlite-readonly"
    assert data["backend"] == "sqlite"
    assert data["postgres_configured"] is False
    assert data["sqlite_immutable"] is False
    assert data["sqlite_query_only"] is True
    assert data["recovery_mode"] is False
    assert set(data.keys()) == {
        "ok",
        "service",
        "mode",
        "backend",
        "postgres_configured",
        "sqlite_immutable",
        "sqlite_query_only",
        "recovery_mode",
    }


def test_health_openapi_lists_route() -> None:
    client = TestClient(create_app())
    paths = client.get("/openapi.json").json()["paths"]
    assert "/health" in paths
    assert "get" in paths["/health"]
