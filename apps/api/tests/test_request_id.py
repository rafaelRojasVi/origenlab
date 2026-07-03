"""Request ID middleware and header behavior."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from origenlab_api.main import create_app
from origenlab_api.request_id import REQUEST_ID_HEADER, is_safe_request_id, resolve_request_id
from origenlab_api.settings import get_settings

_UNSAFE_REQUEST_ID = "postgresql://u:p@host/db password=secret"


def test_is_safe_request_id_accepts_simple_values() -> None:
    assert is_safe_request_id("local-test-123")
    assert is_safe_request_id("abc_123-456:789.0")


def test_is_safe_request_id_rejects_unsafe_values() -> None:
    assert not is_safe_request_id(_UNSAFE_REQUEST_ID)
    assert not is_safe_request_id("has space")
    assert not is_safe_request_id("a" * 129)


def test_health_returns_generated_request_id_header() -> None:
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    request_id = response.headers.get(REQUEST_ID_HEADER)
    assert request_id
    assert is_safe_request_id(request_id)


def test_health_reuses_safe_incoming_request_id() -> None:
    client = TestClient(create_app())
    response = client.get("/health", headers={REQUEST_ID_HEADER: "local-test-123"})
    assert response.status_code == 200
    assert response.headers.get(REQUEST_ID_HEADER) == "local-test-123"


def test_health_replaces_unsafe_incoming_request_id() -> None:
    client = TestClient(create_app())
    response = client.get("/health", headers={REQUEST_ID_HEADER: _UNSAFE_REQUEST_ID})
    assert response.status_code == 200
    request_id = response.headers.get(REQUEST_ID_HEADER)
    assert request_id
    assert request_id != _UNSAFE_REQUEST_ID
    assert is_safe_request_id(request_id)
    assert "postgres" not in request_id.lower()
    assert "password" not in request_id.lower()


def test_validation_error_body_request_id_matches_header() -> None:
    client = TestClient(create_app())
    response = client.get(
        "/cases/warm?limit=0",
        headers={REQUEST_ID_HEADER: "local-test-123"},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["request_id"] == "local-test-123"
    assert response.headers.get(REQUEST_ID_HEADER) == "local-test-123"


def test_unknown_route_not_found_request_id_matches_header() -> None:
    client = TestClient(create_app())
    response = client.get(
        "/this-route-does-not-exist",
        headers={REQUEST_ID_HEADER: "trace-404"},
    )
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"
    assert body["error"]["request_id"] == "trace-404"
    assert response.headers.get(REQUEST_ID_HEADER) == "trace-404"


def test_host_allowlist_forbidden_request_id_matches_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORIGENLAB_ENV", "production")
    monkeypatch.setenv("ORIGENLAB_API_BACKEND", "postgres")
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@127.0.0.1:5432/db")
    monkeypatch.setenv("ORIGENLAB_API_CORS_ORIGINS", "https://dashboard.origenlab.cl")
    monkeypatch.setenv("ORIGENLAB_API_ALLOWED_HOSTS", "api.origenlab.cl")
    monkeypatch.setenv("ORIGENLAB_API_AUTH_TOKEN", "test-token")
    get_settings.cache_clear()
    client = TestClient(create_app())
    response = client.get(
        "/health",
        headers={"Host": "evil.example", REQUEST_ID_HEADER: "host-block-1"},
    )
    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "forbidden"
    assert body["error"]["request_id"] == "host-block-1"
    assert response.headers.get(REQUEST_ID_HEADER) == "host-block-1"


def test_resolve_request_id_is_idempotent() -> None:
    pytest.importorskip("starlette.requests")
    from starlette.requests import Request

    scope = {
        "type": "http",
        "headers": [(REQUEST_ID_HEADER.lower().encode(), b"abc-123")],
        "method": "GET",
        "path": "/health",
        "query_string": b"",
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    request = Request(scope)
    first = resolve_request_id(request)
    second = resolve_request_id(request)
    assert first == second == "abc-123"
