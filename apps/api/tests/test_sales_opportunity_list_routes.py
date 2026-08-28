"""HTTP tests for the durable sales-opportunity board list route."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from origenlab_api.errors import register_exception_handlers
from origenlab_api.repositories.postgres.commercial_operations_read import (
    SalesOpportunityBoardItem,
)
from origenlab_api.routes import operations
from origenlab_api.settings import Settings, get_settings


NOW = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


def _item(**overrides: Any) -> SalesOpportunityBoardItem:
    base: dict[str, Any] = dict(
        sales_opportunity_id="sales_1",
        source_kind="pr3",
        source_opportunity_id="o_1",
        account_id="a_1",
        primary_contact_id="c_1",
        organization_id=None,
        primary_crm_contact_id=None,
        title="Centrífuga refrigerada",
        stage="qualifying",
        owner_key="tatiana@origenlab.cl",
        version=2,
        created_by="tatiana@origenlab.cl",
        updated_by="tatiana@origenlab.cl",
        created_at=NOW,
        updated_at=NOW,
        stage_updated_at=NOW,
        contact_display_email="buyer@example.cl",
        account_display_domain="example.cl",
        open_task_count=1,
        next_task_id="task_1",
        next_task_title="Llamar cliente",
        next_task_due_at=NOW,
    )
    base.update(overrides)
    return SalesOpportunityBoardItem(**base)


class FakeReadService:
    def __init__(self, items: list[SalesOpportunityBoardItem], total: int) -> None:
        self.items = items
        self.total = total
        self.calls: list[dict[str, Any]] = []

    def list_sales_opportunities(self, **kwargs: Any) -> tuple[list[SalesOpportunityBoardItem], int]:
        self.calls.append(kwargs)
        return self.items, self.total


def _client(read: FakeReadService) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(operations.router)

    app.dependency_overrides[get_settings] = lambda: Settings()
    app.dependency_overrides[operations.get_commercial_operations_read_service] = lambda: read

    return TestClient(app, raise_server_exceptions=False)


def test_list_returns_envelope_and_items() -> None:
    service = FakeReadService([_item()], total=1)

    response = _client(service).get("/operations/sales-opportunities")

    assert response.status_code == 200
    body = response.json()

    assert body["meta"]["data_source"] == "postgres"
    assert body["meta"]["total_count"] == 1
    assert body["meta"]["limit"] == 100
    assert body["meta"]["offset"] == 0
    assert len(body["items"]) == 1
    assert body["items"][0]["sales_opportunity_id"] == "sales_1"
    assert body["items"][0]["next_task_title"] == "Llamar cliente"


def test_list_forwards_repeatable_stage_filter() -> None:
    service = FakeReadService([], total=0)

    response = _client(service).get(
        "/operations/sales-opportunities?stage=new&stage=qualifying&limit=50&offset=10"
    )

    assert response.status_code == 200
    call = service.calls[-1]
    assert call["stages"] == ["new", "qualifying"]
    assert call["limit"] == 50
    assert call["offset"] == 10


def test_list_forwards_repeatable_source_opportunity_id_filter() -> None:
    service = FakeReadService([], total=0)

    response = _client(service).get(
        "/operations/sales-opportunities?source_opportunity_id=o_1&source_opportunity_id=o_2"
    )

    assert response.status_code == 200
    assert service.calls[-1]["source_opportunity_ids"] == ["o_1", "o_2"]


def test_list_invalid_filter_maps_to_422() -> None:
    class RejectingService(FakeReadService):
        def list_sales_opportunities(self, **kwargs: Any) -> tuple[list[SalesOpportunityBoardItem], int]:
            raise ValueError("Unsupported sales opportunity stage: 'invented'")

    response = _client(RejectingService([], total=0)).get(
        "/operations/sales-opportunities?stage=invented"
    )

    assert response.status_code == 422
