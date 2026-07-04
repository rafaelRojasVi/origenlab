"""Tests for dashboard API production smoke (mocked HTTP)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from origenlab_email_pipeline.qa.dashboard_api_readiness import (
    API_AUTH_401_HINT,
    API_AUTH_401_INVALID_HINT,
    API_AUTH_TOKEN_HEADER,
    BLUESLICK_PRODUCT_KEY,
    CF_ACCESS_403_HINT,
    CF_ACCESS_CLIENT_ID_HEADER,
    CF_ACCESS_CLIENT_SECRET_HEADER,
    TEMED_PRODUCT_KEY,
    CloudflareAccessConfig,
    build_smoke_request_headers,
    collect_json_keys,
    default_fetch_json,
    resolve_api_auth_token,
    resolve_cf_access_credentials,
    run_dashboard_api_smoke,
    scan_payload_safety,
)


def _blueslick_history() -> list[dict[str, Any]]:
    return [
        {
            "line_side": "client",
            "line_kind": "product",
            "currency": "CLP",
            "amount_net_clp": 695000,
            "amount_decimal": None,
            "deal_label": "CEAF × SERVA",
        },
        {
            "line_side": "supplier",
            "line_kind": "product",
            "currency": "EUR",
            "amount_net_clp": None,
            "amount_decimal": "117.00",
            "deal_label": "CEAF × SERVA",
        },
    ]


def _temed_history() -> list[dict[str, Any]]:
    return [
        {
            "line_side": "client",
            "line_kind": "product",
            "currency": "CLP",
            "amount_net_clp": 545000,
            "amount_decimal": None,
        },
        {
            "line_side": "supplier",
            "line_kind": "product",
            "currency": "EUR",
            "amount_decimal": "31.00",
        },
    ]


def _good_responses() -> dict[str, dict[str, Any]]:
    return {
        "/health": {"ok": True, "service": "origenlab-api", "mode": "operator-postgres-readonly", "backend": "postgres"},
        "/operator/status": {
            "verdict": "READY",
            "campaign_mode": "equipment_first",
            "outbound_readiness": "ready",
            "warnings": [],
        },
        "/mirror/commercial/deals": {
            "table_available": True,
            "read_only": True,
            "data_source": "postgres_mirror",
            "total": 1,
            "items": [{"client_org_name": "CEAF", "supplier_org_name": "SERVA"}],
        },
        "/mirror/catalog/products": {
            "table_available": True,
            "read_only": True,
            "data_source": "postgres_mirror",
            "total": 9,
            "items": [{"product_key": "x", "display_name": "Product"}],
        },
        f"/mirror/catalog/products/{BLUESLICK_PRODUCT_KEY}": {
            "table_available": True,
            "read_only": True,
            "data_source": "postgres_mirror",
            "product": {
                "product_key": BLUESLICK_PRODUCT_KEY,
                "display_name": "BlueSlick™ 250 ml",
                "public_summary": "Vendido en electroforesis con cotización y disponibilidad.",
                "commercial_history": _blueslick_history(),
            },
        },
        f"/mirror/catalog/products/{TEMED_PRODUCT_KEY}": {
            "table_available": True,
            "read_only": True,
            "data_source": "postgres_mirror",
            "product": {
                "product_key": TEMED_PRODUCT_KEY,
                "display_name": "TEMED 25 ml",
                "commercial_history": _temed_history(),
            },
        },
        "/cases/warm": {
            "meta": {
                "data_source": "postgres_mirror",
                "read_only": True,
                "reduced_mode": False,
                "count": 3,
            },
            "items": [
                {
                    "case_id": "gmail-contacto-1",
                    "category": "client_opportunity",
                    "gmail_url": None,
                }
            ],
        },
        "/opportunities/equipment": {
            "meta": {
                "data_source": "postgres_mirror",
                "read_only": True,
                "reduced_mode": False,
                "count": 0,
                "source_path": "",
                "note": "",
            },
            "items": [],
        },
    }


def _mock_fetch(responses: dict[str, dict[str, Any]]):
    def fetch(path: str, params: dict[str, str | int]) -> tuple[int, dict[str, Any] | None, str]:
        _ = params
        body = responses.get(path)
        if body is None:
            return 404, None, "not found"
        return 200, body, ""

    return fetch


def test_scan_payload_safety_ignores_null_gmail_url() -> None:
    assert scan_payload_safety({"gmail_url": None}) == []


def test_scan_payload_safety_flags_forbidden_key_and_prose() -> None:
    errors = scan_payload_safety({"gmail_url": "https://mail.google.com/x"})
    assert any("forbidden keys" in e for e in errors)

    errors = scan_payload_safety({"snippet": "texto con montoes en resumen"})
    assert any("prose artifact" in e for e in errors)

    errors = scan_payload_safety({"note": "oportunida de s en catálogo"})
    assert any("prose artifact" in e for e in errors)


def test_smoke_passes_with_good_mock_responses() -> None:
    report = run_dashboard_api_smoke(
        "https://api.example.test",
        fetch=_mock_fetch(_good_responses()),
    )
    assert report.passed
    assert len(report.checks) == 8
    assert "PASS" in report.summary_lines()[-1]


def test_smoke_fails_when_catalog_total_below_minimum() -> None:
    responses = _good_responses()
    responses["/mirror/catalog/products"]["total"] = 3
    report = run_dashboard_api_smoke(
        "https://api.example.test",
        fetch=_mock_fetch(responses),
    )
    assert not report.passed
    catalog = next(c for c in report.checks if c.name.endswith("/products"))
    assert not catalog.ok
    assert "total=3" in catalog.detail


def test_smoke_fails_when_blueslick_history_missing_amounts() -> None:
    responses = _good_responses()
    responses[f"/mirror/catalog/products/{BLUESLICK_PRODUCT_KEY}"]["product"][
        "commercial_history"
    ] = []
    report = run_dashboard_api_smoke(
        "https://api.example.test",
        fetch=_mock_fetch(responses),
    )
    assert not report.passed
    blueslick = next(c for c in report.checks if BLUESLICK_PRODUCT_KEY in c.name)
    assert not blueslick.ok


def test_smoke_fails_when_operator_exposes_sqlite_path() -> None:
    responses = _good_responses()
    responses["/operator/status"]["sqlite_path"] = "/home/user/secret/emails.sqlite"
    report = run_dashboard_api_smoke(
        "https://api.example.test",
        fetch=_mock_fetch(responses),
    )
    assert not report.passed
    op = next(c for c in report.checks if "/operator/status" in c.name)
    assert "sqlite_path" in op.detail


def test_smoke_fails_on_forbidden_transfer_id_in_deals() -> None:
    responses = _good_responses()
    responses["/mirror/commercial/deals"]["items"][0]["transfer_id"] = "secret-transfer"
    report = run_dashboard_api_smoke(
        "https://api.example.test",
        fetch=_mock_fetch(responses),
    )
    assert not report.passed
    keys: set[str] = set()
    collect_json_keys(responses["/mirror/commercial/deals"], keys)
    assert "transfer_id" in keys


def test_smoke_fails_when_equipment_exposes_source_path() -> None:
    responses = _good_responses()
    responses["/opportunities/equipment"]["meta"]["source_path"] = "/reports/out/secret.csv"
    report = run_dashboard_api_smoke(
        "https://api.example.test",
        fetch=_mock_fetch(responses),
    )
    assert not report.passed
    eq = next(c for c in report.checks if "/opportunities/equipment" in c.name)
    assert "source_path" in eq.detail


def test_summary_does_not_embed_response_bodies() -> None:
    report = run_dashboard_api_smoke(
        "https://api.example.test",
        fetch=_mock_fetch(_good_responses()),
    )
    joined = "\n".join(report.summary_lines())
    assert "695000" not in joined
    assert "117.00" not in joined
    assert "postgresql://" not in joined


def test_resolve_cf_access_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CF_ACCESS_CLIENT_ID", "cf-id-from-env")
    monkeypatch.setenv("CF_ACCESS_CLIENT_SECRET", "cf-secret-from-env")
    cfg = resolve_cf_access_credentials()
    assert cfg is not None
    assert cfg.client_id == "cf-id-from-env"
    assert cfg.client_secret == "cf-secret-from-env"


def test_resolve_cf_access_origenlab_env_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CF_ACCESS_CLIENT_ID", raising=False)
    monkeypatch.delenv("CF_ACCESS_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("ORIGENLAB_CF_ACCESS_CLIENT_ID", "origen-id")
    monkeypatch.setenv("ORIGENLAB_CF_ACCESS_CLIENT_SECRET", "origen-secret")
    cfg = resolve_cf_access_credentials()
    assert cfg is not None
    assert cfg.client_id == "origen-id"


def test_resolve_cf_access_cli_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CF_ACCESS_CLIENT_ID", "env-id")
    monkeypatch.setenv("CF_ACCESS_CLIENT_SECRET", "env-secret")
    cfg = resolve_cf_access_credentials(cli_client_id="cli-id", cli_client_secret="cli-secret")
    assert cfg is not None
    assert cfg.client_id == "cli-id"
    assert cfg.client_secret == "cli-secret"


def test_default_fetch_json_attaches_cf_access_headers() -> None:
    captured: dict[str, str] = {}

    class FakeResponse:
        status = 200

        def read(self) -> bytes:
            return b'{"ok": true}'

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def fake_urlopen(req: object, timeout: float = 30.0) -> FakeResponse:
        _ = timeout
        request = req  # type: ignore[assignment]
        for key, value in request.header_items():  # type: ignore[attr-defined]
            captured[key] = value
        return FakeResponse()

    cfg = CloudflareAccessConfig(client_id="test-client-id", client_secret="test-client-secret")
    with patch("origenlab_email_pipeline.qa.dashboard_api_readiness.urlopen", fake_urlopen):
        status, body, err = default_fetch_json(
            "http://127.0.0.1:8001",
            "/health",
            extra_headers=cfg.request_headers(),
        )
    assert status == 200
    assert body == {"ok": True}
    assert err == ""
    normalized = {k.lower(): v for k, v in captured.items()}
    assert normalized[CF_ACCESS_CLIENT_ID_HEADER.lower()] == "test-client-id"
    assert normalized[CF_ACCESS_CLIENT_SECRET_HEADER.lower()] == "test-client-secret"


def test_smoke_403_without_cf_token_shows_helpful_message() -> None:
    def fetch_403(path: str, params: dict[str, str | int]) -> tuple[int, dict[str, Any] | None, str]:
        _ = path, params
        return 403, None, "Forbidden"

    report = run_dashboard_api_smoke("https://api.origenlab.cl", fetch=fetch_403)
    assert not report.passed
    health = report.checks[0]
    assert health.detail == CF_ACCESS_403_HINT


def test_smoke_403_with_cf_configured_does_not_show_cf_hint() -> None:
    def fetch_403(path: str, params: dict[str, str | int]) -> tuple[int, dict[str, Any] | None, str]:
        _ = path, params
        return 403, None, "Forbidden"

    cf = CloudflareAccessConfig(client_id="id", client_secret="secret")
    report = run_dashboard_api_smoke("https://api.origenlab.cl", fetch=fetch_403, cf_access=cf)
    health = report.checks[0]
    assert CF_ACCESS_403_HINT not in health.detail
    assert "HTTP 403" in health.detail


def test_smoke_summary_never_prints_cf_secrets() -> None:
    secret = "super-secret-cf-token-value"
    cf = CloudflareAccessConfig(client_id="public-client-id", client_secret=secret)

    report = run_dashboard_api_smoke(
        "https://api.example.test",
        fetch=_mock_fetch(_good_responses()),
        cf_access=cf,
    )
    joined = "\n".join(report.summary_lines())
    assert secret not in joined
    assert "public-client-id" not in joined


def test_local_unauthenticated_mode_without_cf_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CF_ACCESS_CLIENT_ID", raising=False)
    monkeypatch.delenv("CF_ACCESS_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("ORIGENLAB_CF_ACCESS_CLIENT_ID", raising=False)
    monkeypatch.delenv("ORIGENLAB_CF_ACCESS_CLIENT_SECRET", raising=False)
    assert resolve_cf_access_credentials() is None
    report = run_dashboard_api_smoke(
        "http://127.0.0.1:8001",
        fetch=_mock_fetch(_good_responses()),
    )
    assert report.passed


def test_resolve_api_auth_token_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORIGENLAB_API_AUTH_TOKEN", "  smoke-token-value  ")
    assert resolve_api_auth_token() == "smoke-token-value"


def test_resolve_api_auth_token_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORIGENLAB_API_AUTH_TOKEN", raising=False)
    assert resolve_api_auth_token() is None


def test_build_smoke_request_headers_merges_cf_and_api_auth() -> None:
    cf = CloudflareAccessConfig(client_id="cf-id", client_secret="cf-secret")
    headers = build_smoke_request_headers(cf_access=cf, api_auth_token="api-token")
    assert headers[CF_ACCESS_CLIENT_ID_HEADER] == "cf-id"
    assert headers[CF_ACCESS_CLIENT_SECRET_HEADER] == "cf-secret"
    assert headers[API_AUTH_TOKEN_HEADER] == "api-token"


def test_default_fetch_json_attaches_api_auth_header() -> None:
    captured: dict[str, str] = {}

    class FakeResponse:
        status = 200

        def read(self) -> bytes:
            return b'{"ok": true}'

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def fake_urlopen(req: object, timeout: float = 30.0) -> FakeResponse:
        _ = timeout
        request = req  # type: ignore[assignment]
        for key, value in request.header_items():  # type: ignore[attr-defined]
            captured[key] = value
        return FakeResponse()

    with patch("origenlab_email_pipeline.qa.dashboard_api_readiness.urlopen", fake_urlopen):
        status, body, err = default_fetch_json(
            "http://127.0.0.1:8001",
            "/operator/status",
            extra_headers=build_smoke_request_headers(api_auth_token="secret-api-token"),
        )
    assert status == 200
    assert body == {"ok": True}
    assert err == ""
    normalized = {k.lower(): v for k, v in captured.items()}
    assert normalized[API_AUTH_TOKEN_HEADER.lower()] == "secret-api-token"


def test_smoke_401_without_api_token_shows_helpful_message() -> None:
    def fetch_401(path: str, params: dict[str, str | int]) -> tuple[int, dict[str, Any] | None, str]:
        _ = path, params
        return 401, None, '{"error":{"code":"unauthorized"}}'

    report = run_dashboard_api_smoke("https://api.origenlab.cl", fetch=fetch_401)
    assert not report.passed
    health = report.checks[0]
    assert health.detail == API_AUTH_401_HINT


def test_smoke_401_with_api_token_configured_shows_invalid_hint() -> None:
    def fetch_401(path: str, params: dict[str, str | int]) -> tuple[int, dict[str, Any] | None, str]:
        _ = path, params
        return 401, None, '{"error":{"code":"unauthorized"}}'

    report = run_dashboard_api_smoke(
        "https://api.origenlab.cl",
        fetch=fetch_401,
        api_auth_token="configured-but-wrong",
    )
    assert not report.passed
    health = report.checks[0]
    assert health.detail == API_AUTH_401_INVALID_HINT
    assert API_AUTH_401_HINT not in health.detail


def test_smoke_summary_never_prints_api_auth_token() -> None:
    secret = "super-secret-api-token-value"
    report = run_dashboard_api_smoke(
        "https://api.example.test",
        fetch=_mock_fetch(_good_responses()),
        api_auth_token=secret,
    )
    joined = "\n".join(report.summary_lines())
    assert secret not in joined
