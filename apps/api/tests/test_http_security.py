"""Production HTTP security: CORS, docs disabled, no wildcard origins."""

from __future__ import annotations

import json
import sqlite3

import pytest

from origenlab_api.backends.factory import validate_api_settings
from origenlab_api.http_security import (
    _PUBLIC_PATHS,
    api_auth_enabled,
    host_allowlist_enabled,
    is_public_route,
    normalize_host_header,
    openapi_docs_enabled,
)
from origenlab_api.main import create_app
from origenlab_api.settings import Settings, get_settings

_PRODUCTION_AUTH_TOKEN = "test-token"


def _clear_settings_cache() -> None:
    get_settings.cache_clear()


def test_production_mode_disables_openapi_docs() -> None:
    settings = Settings(env="production", api_disable_docs=False)
    assert openapi_docs_enabled(settings) is False


def test_api_disable_docs_flag_disables_openapi() -> None:
    settings = Settings(api_disable_docs=True)
    assert openapi_docs_enabled(settings) is False


def test_production_requires_postgres_backend_and_cors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORIGENLAB_ENV", "production")
    monkeypatch.setenv("ORIGENLAB_API_BACKEND", "sqlite")
    monkeypatch.delenv("ORIGENLAB_API_CORS_ORIGINS", raising=False)
    _clear_settings_cache()
    with pytest.raises(ValueError, match="ORIGENLAB_ENV=production requires ORIGENLAB_API_BACKEND=postgres"):
        create_app()


def test_production_fails_fast_if_auth_token_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORIGENLAB_ENV", "production")
    monkeypatch.setenv("ORIGENLAB_API_BACKEND", "postgres")
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@127.0.0.1:5432/db")
    monkeypatch.setenv("ORIGENLAB_API_CORS_ORIGINS", "https://dashboard.origenlab.cl")
    monkeypatch.delenv("ORIGENLAB_API_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("ORIGENLAB_API_AUTH_TOKEN", "")
    _clear_settings_cache()
    with pytest.raises(ValueError, match="ORIGENLAB_ENV=production requires ORIGENLAB_API_AUTH_TOKEN"):
        create_app()


def test_production_rejects_wildcard_cors() -> None:
    settings = Settings(
        env="production",
        api_backend="postgres",
        postgres_url="postgresql://u:p@127.0.0.1:5432/db",
        api_cors_origins="*",
    )
    with pytest.raises(ValueError, match="must not include '\\*'"):
        validate_api_settings(settings)


def _dev_cors_client(monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    monkeypatch.setenv("ORIGENLAB_API_BACKEND", "postgres")
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@127.0.0.1:5432/db")
    monkeypatch.setenv("ORIGENLAB_API_CORS_ORIGINS", "https://dashboard.origenlab.cl")
    monkeypatch.delenv("ORIGENLAB_ENV", raising=False)
    _clear_settings_cache()
    return TestClient(create_app())


def test_cors_allows_configured_dashboard_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _dev_cors_client(monkeypatch)
    r = client.options(
        "/health",
        headers={
            "Origin": "https://dashboard.origenlab.cl",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "https://dashboard.origenlab.cl"
    assert r.headers.get("access-control-allow-credentials") == "true"


def test_operator_security_headers_on_json_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    monkeypatch.setenv("ORIGENLAB_API_BACKEND", "postgres")
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@127.0.0.1:5432/db")
    monkeypatch.setenv("ORIGENLAB_API_CORS_ORIGINS", "https://dashboard.origenlab.cl")
    _clear_settings_cache()
    client = TestClient(create_app())
    r = client.get("/health")
    assert r.status_code == 200
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
    assert r.headers.get("x-frame-options") == "DENY"
    assert "no-store" in (r.headers.get("cache-control") or "")


def test_cors_get_health_includes_allow_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _dev_cors_client(monkeypatch)
    r = client.get(
        "/health",
        headers={"Origin": "https://dashboard.origenlab.cl"},
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "https://dashboard.origenlab.cl"
    assert r.headers.get("access-control-allow-credentials") == "true"
    assert "x-request-id" in (r.headers.get("access-control-expose-headers") or "").lower()


def test_cors_get_operator_status_includes_allow_credentials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    db = tmp_path / "emails.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE emails (id INTEGER PRIMARY KEY, date_iso TEXT, source_file TEXT, folder TEXT)"
    )
    conn.commit()
    conn.close()
    active = tmp_path / "current"
    active.mkdir()
    (active / "manifest.json").write_text(
        json.dumps({"known_warnings": [], "canonical_files": []}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ORIGENLAB_API_BACKEND", "sqlite")
    monkeypatch.setenv("ORIGENLAB_SQLITE_PATH", str(db))
    monkeypatch.setenv("ORIGENLAB_ACTIVE_CURRENT", str(active))
    monkeypatch.setenv("ORIGENLAB_API_CORS_ORIGINS", "https://dashboard.origenlab.cl")
    monkeypatch.delenv("ORIGENLAB_ENV", raising=False)
    _clear_settings_cache()
    client = TestClient(create_app())
    r = client.get(
        "/operator/status",
        headers={"Origin": "https://dashboard.origenlab.cl"},
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "https://dashboard.origenlab.cl"
    assert r.headers.get("access-control-allow-credentials") == "true"


def test_cors_disallowed_origin_not_reflected(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _dev_cors_client(monkeypatch)
    r = client.get(
        "/health",
        headers={"Origin": "https://evil.example.com"},
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") is None


def test_normalize_host_strips_port() -> None:
    assert normalize_host_header("api.origenlab.cl:443") == "api.origenlab.cl"
    assert normalize_host_header("API.OrigenLab.CL") == "api.origenlab.cl"


def test_normalize_host_uses_first_forwarded_value() -> None:
    assert normalize_host_header("api.origenlab.cl, origenlab.onrender.com") == "api.origenlab.cl"


def _production_client(monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    monkeypatch.setenv("ORIGENLAB_ENV", "production")
    monkeypatch.setenv("ORIGENLAB_API_BACKEND", "postgres")
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@127.0.0.1:5432/db")
    monkeypatch.setenv("ORIGENLAB_API_CORS_ORIGINS", "https://dashboard.origenlab.cl")
    monkeypatch.setenv("ORIGENLAB_API_ALLOWED_HOSTS", "api.origenlab.cl")
    monkeypatch.setenv("ORIGENLAB_API_AUTH_TOKEN", _PRODUCTION_AUTH_TOKEN)
    _clear_settings_cache()
    return TestClient(create_app())


def _production_auth_headers(*, token: str = _PRODUCTION_AUTH_TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_production_allowed_host_permits_health(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _production_client(monkeypatch)
    r = client.get("/health", headers={"Host": "api.origenlab.cl"})
    assert r.status_code == 200


def test_production_render_host_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _production_client(monkeypatch)
    r = client.get("/health", headers={"Host": "origenlab.onrender.com"})
    assert r.status_code == 403
    body = r.json()
    assert body["error"]["code"] == "forbidden"
    assert body["error"]["message"] == "Forbidden"


def test_production_missing_host_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _production_client(monkeypatch)
    r = client.get("/health", headers={"Host": ""})
    assert r.status_code == 403


def test_dev_mode_allows_render_host_without_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    monkeypatch.setenv("ORIGENLAB_API_BACKEND", "postgres")
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@127.0.0.1:5432/db")
    monkeypatch.setenv("ORIGENLAB_API_CORS_ORIGINS", "https://dashboard.origenlab.cl")
    monkeypatch.delenv("ORIGENLAB_ENV", raising=False)
    monkeypatch.delenv("ORIGENLAB_API_ALLOWED_HOSTS", raising=False)
    _clear_settings_cache()
    client = TestClient(create_app())
    r = client.get("/health", headers={"Host": "origenlab.onrender.com"})
    assert r.status_code == 200


def test_host_allowlist_disabled_without_env_hosts() -> None:
    settings = Settings(env="production", api_allowed_hosts=None)
    assert host_allowlist_enabled(settings) is False


def test_production_hides_docs_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    monkeypatch.setenv("ORIGENLAB_ENV", "production")
    monkeypatch.setenv("ORIGENLAB_API_BACKEND", "postgres")
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@127.0.0.1:5432/db")
    monkeypatch.setenv("ORIGENLAB_API_CORS_ORIGINS", "https://dashboard.origenlab.cl")
    monkeypatch.setenv("ORIGENLAB_API_AUTH_TOKEN", _PRODUCTION_AUTH_TOKEN)
    monkeypatch.delenv("ORIGENLAB_API_ALLOWED_HOSTS", raising=False)
    _clear_settings_cache()
    client = TestClient(create_app())
    assert client.get("/docs", headers=_production_auth_headers()).status_code == 404
    assert client.get("/openapi.json", headers=_production_auth_headers()).status_code == 404
    assert client.get("/health").status_code == 200


def test_api_auth_enabled_only_in_production() -> None:
    assert api_auth_enabled(Settings(env="production", api_auth_token="secret")) is True
    assert api_auth_enabled(Settings(env="production", api_auth_token=None)) is False
    assert api_auth_enabled(Settings(env="development", api_auth_token="secret")) is False


def test_public_unauthenticated_paths_are_health_and_options_only() -> None:
    """Lock production auth surface: only /health and OPTIONS bypass bearer auth."""
    assert _PUBLIC_PATHS == frozenset({"/health"})

    pytest.importorskip("starlette.requests")
    from starlette.requests import Request

    def _request(method: str, path: str) -> Request:
        return Request({"type": "http", "method": method, "path": path, "headers": []})

    assert is_public_route(_request("GET", "/health")) is True
    assert is_public_route(_request("OPTIONS", "/operator/status")) is True
    assert is_public_route(_request("OPTIONS", "/mirror/dashboard/summary")) is True
    assert is_public_route(_request("GET", "/operator/status")) is False
    assert is_public_route(_request("GET", "/emails/recent")) is False
    assert is_public_route(_request("GET", "/mirror/contacts")) is False
    # Any method on /health bypasses auth (load balancers may use GET or HEAD).
    assert is_public_route(_request("HEAD", "/health")) is True


def test_production_health_works_without_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _production_client(monkeypatch)
    r = client.get("/health", headers={"Host": "api.origenlab.cl"})
    assert r.status_code == 200


def test_production_operator_status_without_auth_returns_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _production_client(monkeypatch)
    r = client.get("/operator/status", headers={"Host": "api.origenlab.cl"})
    assert r.status_code == 401
    body = r.json()
    assert body["error"]["code"] == "unauthorized"
    assert body["error"]["message"] == "Unauthorized"
    assert r.headers.get("www-authenticate") == "Bearer"
    assert r.headers.get("X-Request-ID") == body["error"]["request_id"]


def test_production_emails_recent_without_auth_returns_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _production_client(monkeypatch)
    r = client.get("/emails/recent", headers={"Host": "api.origenlab.cl"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


@pytest.mark.parametrize(
    "path",
    [
        "/mirror/catalog/products",
        "/mirror/leads/summary",
    ],
)
def test_production_mirror_routes_without_auth_return_401(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    client = _production_client(monkeypatch)
    r = client.get(path, headers={"Host": "api.origenlab.cl"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_production_invalid_bearer_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _production_client(monkeypatch)
    r = client.get(
        "/operator/status",
        headers={"Host": "api.origenlab.cl", "Authorization": "Bearer wrong-token"},
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_production_valid_bearer_not_unauthorized(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _production_client(monkeypatch)
    r = client.get(
        "/operator/status",
        headers={"Host": "api.origenlab.cl", **_production_auth_headers()},
    )
    assert r.status_code != 401


def test_production_valid_bearer_reaches_mirror_route_auth_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _production_client(monkeypatch)
    r = client.get(
        "/mirror/catalog/products",
        headers={"Host": "api.origenlab.cl", **_production_auth_headers()},
    )
    assert r.status_code != 401


def test_production_valid_api_key_header_not_unauthorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _production_client(monkeypatch)
    r = client.get(
        "/operator/status",
        headers={
            "Host": "api.origenlab.cl",
            "X-OriginLab-API-Key": _PRODUCTION_AUTH_TOKEN,
        },
    )
    assert r.status_code != 401


def test_production_invalid_api_key_header_returns_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _production_client(monkeypatch)
    r = client.get(
        "/operator/status",
        headers={
            "Host": "api.origenlab.cl",
            "X-OriginLab-API-Key": "wrong-token",
        },
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_production_options_preflight_without_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _production_client(monkeypatch)
    r = client.options(
        "/operator/status",
        headers={
            "Host": "api.origenlab.cl",
            "Origin": "https://dashboard.origenlab.cl",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.status_code != 401
