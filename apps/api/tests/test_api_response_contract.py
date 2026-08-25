"""Contract smoke tests for apps/api JSON response shapes (read-only, SQLite-safe)."""

# ruff: noqa: E402

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from origenlab_api.main import create_app
from origenlab_api.settings import Settings, get_settings
from test_response_model_coverage import EXPECTED_PUBLIC_GET_PATHS

_CONTACTO_INBOX = "gmail:contacto@origenlab.cl/INBOX"

# Route inventory canonical list: tests/test_response_model_coverage.py (mirrors API_RESPONSE_CONTRACT.md).

SECRET_FORBIDDEN_SUBSTRINGS: tuple[str, ...] = (
    "postgres://",
    "ORIGENLAB_POSTGRES_URL",
    "password",
    "traceback",
    "Traceback",
)


def _recent_iso(days_ago: int = 1, hour: int = 10) -> str:
    day = date.today() - timedelta(days=days_ago)
    return f"{day.isoformat()}T{hour:02d}:00:00-04:00"


def _sqlite_client(tmp_path: Path) -> TestClient:
    active = tmp_path / "current"
    active.mkdir(parents=True)
    (active / "manifest.json").write_text(
        json.dumps({"canonical_files": [], "campaign_mode": "equipment_first"}),
        encoding="utf-8",
    )
    db = tmp_path / "contract.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE emails (
            id INTEGER PRIMARY KEY,
            date_iso TEXT,
            source_file TEXT,
            folder TEXT,
            sender TEXT,
            subject TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO emails (date_iso, source_file, folder, sender, subject) VALUES (?, ?, ?, ?, ?)",
        (
            _recent_iso(),
            _CONTACTO_INBOX,
            "INBOX",
            "Kelly Liu <kelly@supplier.com>",
            "Re: centrifuga laboratorio",
        ),
    )
    conn.commit()
    conn.close()

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        sqlite_path=db,
        active_current=active,
    )
    return TestClient(app)


def _openapi_get_paths(client: TestClient) -> set[str]:
    schema = client.get("/openapi.json").json()
    return {
        path
        for path, methods in schema.get("paths", {}).items()
        if isinstance(methods, dict) and "get" in methods
    }


def _assert_json_object(payload: Any) -> dict[str, Any]:
    assert isinstance(payload, dict), f"expected JSON object, got {type(payload).__name__}"
    assert not isinstance(payload, list)
    return payload


def _assert_safe_error_payload(payload: Any) -> None:
    text = json.dumps(payload) if not isinstance(payload, str) else payload
    for needle in SECRET_FORBIDDEN_SUBSTRINGS:
        assert needle not in text, f"error body leaked forbidden substring: {needle!r}"


def _assert_error_envelope(
    body: dict[str, Any],
    *,
    code: str,
    response: Any | None = None,
) -> str:
    assert "error" in body
    assert "detail" not in body
    error = body["error"]
    assert error["code"] == code
    assert isinstance(error["message"], str) and error["message"].strip()
    assert isinstance(error["details"], dict)
    request_id = error["request_id"]
    assert isinstance(request_id, str) and request_id
    if response is not None:
        assert response.headers.get("X-Request-ID") == request_id
    _assert_safe_error_payload(body)
    return request_id


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return _sqlite_client(tmp_path)


def test_create_app_openapi_is_available(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema.get("openapi", "").startswith("3.")
    assert schema.get("info", {}).get("title") == "OrigenLab API"
    assert "/health" in schema.get("paths", {})


def test_public_get_route_paths_are_stable(client: TestClient) -> None:
    actual = _openapi_get_paths(client)
    assert actual == set(EXPECTED_PUBLIC_GET_PATHS)


@pytest.mark.parametrize(
    "path",
    [
        "/health",
        "/cases/warm?positive_signal_only=false&limit=5",
        "/emails/recent?limit=5",
        "/opportunities/equipment?limit=5",
    ],
)
def test_selected_success_endpoints_return_json_objects(
    client: TestClient,
    path: str,
) -> None:
    response = client.get(path)
    assert response.status_code == 200
    data = _assert_json_object(response.json())
    assert not isinstance(data, list)


def test_list_endpoints_use_meta_and_items_not_bare_array(client: TestClient) -> None:
    for path in ("/cases/warm?limit=5", "/emails/recent?limit=5", "/opportunities/equipment"):
        data = _assert_json_object(client.get(path).json())
        assert "meta" in data
        assert "items" in data
        assert isinstance(data["items"], list)
        assert isinstance(data["meta"], dict)


def test_contact_detail_success_is_object_with_meta_and_contact(tmp_path: Path) -> None:
    client = _sqlite_client(tmp_path)
    data = _assert_json_object(client.get("/contacts/unknown@example.cl").json())
    assert "meta" in data
    assert "contact" in data
    assert isinstance(data["meta"], dict)
    assert isinstance(data["contact"], dict)


def test_cases_warm_invalid_category_returns_unified_error_envelope(client: TestClient) -> None:
    response = client.get("/cases/warm?category=not_a_real_category")
    assert response.status_code == 422
    body = _assert_json_object(response.json())
    _assert_error_envelope(body, code="invalid_query_param", response=response)
    assert "Invalid category" in body["error"]["message"]
    assert "not_a_real_category" in body["error"]["message"]


def test_query_validation_error_returns_unified_error_envelope(client: TestClient) -> None:
    response = client.get("/cases/warm?limit=0")
    assert response.status_code == 422
    body = _assert_json_object(response.json())
    _assert_error_envelope(body, code="validation_error", response=response)
    errors = body["error"]["details"]["validation_errors"]
    assert isinstance(errors, list)
    assert errors
    assert any("limit" in ".".join(err.get("loc", [])) for err in errors)


def test_unknown_route_404_returns_not_found_envelope(client: TestClient) -> None:
    response = client.get("/this-route-does-not-exist")
    assert response.status_code == 404
    body = _assert_json_object(response.json())
    _assert_error_envelope(body, code="not_found", response=response)


def test_contact_invalid_email_422_returns_validation_error(client: TestClient) -> None:
    response = client.get("/contacts/not-an-email")
    assert response.status_code == 422
    body = _assert_json_object(response.json())
    _assert_error_envelope(body, code="validation_error", response=response)


def test_unhandled_exception_returns_internal_error_without_secrets(tmp_path: Path) -> None:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        sqlite_path=tmp_path / "missing.sqlite",
        active_current=tmp_path / "current",
    )

    @app.get("/__contract_test_internal_error")
    def _boom() -> None:
        raise RuntimeError("postgres://user:password@127.0.0.1:5432/db traceback in message")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/__contract_test_internal_error")
    assert response.status_code == 500
    body = _assert_json_object(response.json())
    _assert_error_envelope(body, code="internal_error", response=response)
    assert body["error"]["message"] == "An unexpected error occurred"


def test_http_exception_nested_details_are_sanitized(tmp_path: Path) -> None:
    from fastapi import HTTPException

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        sqlite_path=tmp_path / "missing.sqlite",
        active_current=tmp_path / "current",
    )

    @app.get("/__contract_test_nested_error")
    def _nested_error() -> None:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Mirror unavailable",
                "db": {
                    "url": "postgresql://user:password@127.0.0.1:5432/db",
                    "password": "secret-value",
                },
            },
        )

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/__contract_test_nested_error")
    assert response.status_code == 503
    body = _assert_json_object(response.json())
    _assert_error_envelope(body, code="mirror_not_configured", response=response)
    text = json.dumps(body)
    assert "postgresql://" not in text
    assert "secret-value" not in text
    assert "<redacted-database-url>" in text or "<redacted>" in text


def test_host_allowlist_rejection_is_safe_json_object(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ORIGENLAB_ENV", "production")
    monkeypatch.setenv("ORIGENLAB_API_BACKEND", "postgres")
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@127.0.0.1:5432/db")
    monkeypatch.setenv("ORIGENLAB_API_CORS_ORIGINS", "https://dashboard.origenlab.cl")
    monkeypatch.setenv("ORIGENLAB_API_ALLOWED_HOSTS", "api.origenlab.cl")
    monkeypatch.setenv("ORIGENLAB_API_AUTH_TOKEN", "test-token")
    get_settings.cache_clear()
    client = TestClient(create_app())
    response = client.get("/health", headers={"Host": "evil.example"})
    assert response.status_code == 403
    body = _assert_json_object(response.json())
    _assert_error_envelope(body, code="forbidden", response=response)
