"""API response timing middleware tests."""

from __future__ import annotations

import re

import pytest

from origenlab_api.settings import get_settings


def test_health_includes_timing_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from origenlab_api.main import create_app

    monkeypatch.delenv("ORIGENLAB_ENV", raising=False)
    monkeypatch.setenv("ORIGENLAB_API_BACKEND", "sqlite")
    get_settings.cache_clear()

    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers.get("X-Request-ID")
    process_ms = response.headers.get("X-Process-Time-Ms")
    server_timing = response.headers.get("Server-Timing")
    assert process_ms is not None
    assert re.fullmatch(r"\d+\.\d{2}", process_ms)
    assert server_timing is not None
    assert re.fullmatch(r"app;dur=\d+\.\d{2}", server_timing)


def test_automation_status_includes_timing_and_sqlite_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    import origenlab_api.routes.operator as operator_routes
    from origenlab_api.main import create_app
    from origenlab_api.schemas.operator_automation import OperatorAutomationStatusResponse

    monkeypatch.delenv("ORIGENLAB_ENV", raising=False)
    monkeypatch.setenv("ORIGENLAB_API_BACKEND", "sqlite")
    get_settings.cache_clear()

    def fake_status(*args, **kwargs) -> OperatorAutomationStatusResponse:
        return OperatorAutomationStatusResponse(
            generated_at_utc="2026-07-15T17:00:00+00:00",
            active_current_dir="current",
            verdict="healthy",
            sqlite_storage={
                "state_exists": True,
                "status": "healthy",
                "allocated_gib": 127.494,
                "freelist_gib": 66.534,
                "mutation": False,
                "read_only": True,
            },
            cron={"note": "not inspected by API"},
            recommended_action="none",
        )

    monkeypatch.setattr(
        operator_routes,
        "build_operator_automation_status_response",
        fake_status,
    )

    client = TestClient(create_app())
    response = client.get("/operator/automation-status")
    assert response.status_code == 200
    assert re.fullmatch(r"\d+\.\d{2}", response.headers.get("X-Process-Time-Ms") or "")
    assert re.fullmatch(r"app;dur=\d+\.\d{2}", response.headers.get("Server-Timing") or "")
    body = response.json()
    assert body["sqlite_storage"]["status"] == "healthy"
    assert body["sqlite_storage"]["mutation"] is False
