"""Route tests for POST /operations/sales-opportunities/manual."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from origenlab_api.errors import register_exception_handlers
from origenlab_api.repositories.postgres.commercial_operations import (
    SalesOpportunity,
)
from origenlab_api.routes import operations
from origenlab_api.settings import Settings, get_settings


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)

HEADERS = {
    "X-OriginLab-Operator-Email": "Tatiana@OrigenLab.CL",
    "Idempotency-Key": "manual-1",
}


def _sales() -> SalesOpportunity:
    return SalesOpportunity(
        sales_opportunity_id="sales_" + "0" * 32,
        source_kind="manual",
        source_opportunity_id="sales_" + "0" * 32,
        account_id=None,
        primary_contact_id=None,
        title="Centrífuga refrigerada",
        stage="new",
        owner_key="tatiana@origenlab.cl",
        version=1,
        created_by="tatiana@origenlab.cl",
        updated_by="tatiana@origenlab.cl",
        created_at=NOW,
        updated_at=NOW,
        organization_id=None,
        primary_crm_contact_id=None,
    )


class FakeWriteService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.error: Exception | None = None

    def create_manual_sales_opportunity(self, **kwargs: Any) -> SalesOpportunity:
        self.calls.append(kwargs)

        if self.error is not None:
            raise self.error

        return _sales()


def _client(
    write: FakeWriteService,
    *,
    writes_enabled: bool = True,
) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(operations.router)

    app.dependency_overrides[get_settings] = lambda: Settings(
        commercial_operations_writes_enabled=writes_enabled,
    )
    app.dependency_overrides[operations.get_commercial_operations_service] = (
        lambda: write
    )

    return TestClient(app, raise_server_exceptions=False)


def test_requires_operator_identity() -> None:
    response = _client(FakeWriteService()).post(
        "/operations/sales-opportunities/manual",
        json={"title": "Centrífuga"},
        headers={"Idempotency-Key": "manual-1"},
    )

    assert response.status_code == 401


def test_creates_a_manual_sales_opportunity() -> None:
    service = FakeWriteService()

    response = _client(service).post(
        "/operations/sales-opportunities/manual",
        json={"title": "Centrífuga refrigerada"},
        headers=HEADERS,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["source_kind"] == "manual"
    assert body["title"] == "Centrífuga refrigerada"

    call = service.calls[-1]
    assert call["operator"] == "tatiana@origenlab.cl"
    assert call["idempotency_key"] == "manual-1"


def test_requires_idempotency_key() -> None:
    response = _client(FakeWriteService()).post(
        "/operations/sales-opportunities/manual",
        json={"title": "Centrífuga"},
        headers={"X-OriginLab-Operator-Email": "tatiana@origenlab.cl"},
    )

    assert response.status_code == 422


def test_rejects_a_browser_supplied_extra_field() -> None:
    response = _client(FakeWriteService()).post(
        "/operations/sales-opportunities/manual",
        json={"title": "Centrífuga", "sales_opportunity_id": "sales_forged"},
        headers=HEADERS,
    )

    assert response.status_code == 422
