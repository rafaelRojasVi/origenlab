"""Operator automation status API response shape."""

from __future__ import annotations

import pytest

from origenlab_api.schemas.operator_automation import OperatorAutomationStatusResponse
from origenlab_api.settings import get_settings


def test_operator_automation_status_preserves_ndr_pending_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    import origenlab_api.routes.operator as operator_routes
    from origenlab_api.main import create_app

    monkeypatch.delenv("ORIGENLAB_ENV", raising=False)
    monkeypatch.setenv("ORIGENLAB_API_BACKEND", "sqlite")
    get_settings.cache_clear()

    def fake_status(*args, **kwargs) -> OperatorAutomationStatusResponse:
        return OperatorAutomationStatusResponse(
            generated_at_utc="2026-07-02T19:30:00+00:00",
            active_current_dir="current",
            verdict="attention",
            ndr_pending_review={
                "pending_count": 2,
                "latest_seen_at": "2026-07-02T18:00:00+00:00",
                "recommended_action": "review_ndr_pending",
            },
            cron={"note": "not inspected by API"},
            recommended_action="review_ndr_pending",
        )

    monkeypatch.setattr(
        operator_routes,
        "build_operator_automation_status_response",
        fake_status,
    )

    client = TestClient(create_app())
    response = client.get("/operator/automation-status")

    assert response.status_code == 200
    body = response.json()
    assert body["ndr_pending_review"] == {
        "pending_count": 2,
        "latest_seen_at": "2026-07-02T18:00:00+00:00",
        "recommended_action": "review_ndr_pending",
    }
    assert body["recommended_action"] == "review_ndr_pending"
