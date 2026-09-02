"""Route tests for GET /operations/customer-quotes (global list)."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from origenlab_api.errors import register_exception_handlers
from origenlab_api.routes import operations
from origenlab_api.settings import Settings, get_settings


class FakeReadService:
    def __init__(self) -> None:
        self.calls: dict[str, Any] = {}

    def list_all_quotes(self, **kwargs: Any) -> tuple[list[object], int]:
        self.calls = kwargs
        return [], 0


def _client(read: FakeReadService) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(operations.router)

    app.dependency_overrides[get_settings] = lambda: Settings(
        commercial_operations_writes_enabled=True,
    )
    app.dependency_overrides[operations.get_customer_quote_read_service] = (
        lambda: read
    )

    return TestClient(app, raise_server_exceptions=False)


def test_returns_empty_list_with_meta() -> None:
    response = _client(FakeReadService()).get("/operations/customer-quotes")

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["meta"]["count"] == 0
    assert body["meta"]["total_count"] == 0


def test_passes_query_params_through() -> None:
    service = FakeReadService()

    response = _client(service).get(
        "/operations/customer-quotes",
        params={
            "stage": ["quoting", "negotiating"],
            "drive_status": "failed",
            "limit": 25,
            "offset": 5,
        },
    )

    assert response.status_code == 200
    assert service.calls["stage"] == ["quoting", "negotiating"]
    assert service.calls["drive_status"] == ["failed"]
    assert service.calls["limit"] == 25
    assert service.calls["offset"] == 5


def test_rejects_limit_above_200() -> None:
    response = _client(FakeReadService()).get(
        "/operations/customer-quotes",
        params={"limit": 500},
    )

    assert response.status_code == 422
