"""CRM-1 HTTP tests for explicit sales-opportunity promotion."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from origenlab_api.errors import register_exception_handlers
from origenlab_api.repositories.postgres.commercial_operations import (
    CommercialOperationConflictError,
    CommercialOperationNotFoundError,
    SalesOpportunity,
)
from origenlab_api.routes import operations
from origenlab_api.settings import Settings, get_settings


NOW = datetime(2026, 8, 25, 20, 0, tzinfo=timezone.utc)

HEADERS = {
    "X-OriginLab-Operator-Email": "Tatiana@OrigenLab.CL",
    "Idempotency-Key": "promote-1",
}


def _sales(
    *,
    sales_opportunity_id: str = "sales_1",
) -> SalesOpportunity:
    return SalesOpportunity(
        sales_opportunity_id=sales_opportunity_id,
        source_kind="pr3",
        source_opportunity_id="o_1",
        account_id="a_1",
        primary_contact_id="c_1",
        title="Centrífuga refrigerada",
        stage="new",
        owner_key="tatiana@origenlab.cl",
        version=1,
        created_by="tatiana@origenlab.cl",
        updated_by="tatiana@origenlab.cl",
        created_at=NOW,
        updated_at=NOW,
    )


class FakeWriteService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.error: Exception | None = None

    def promote_sales_opportunity(
        self,
        **kwargs: Any,
    ) -> SalesOpportunity:
        self.calls.append(kwargs)

        if self.error is not None:
            raise self.error

        return _sales()


class FakeReadService:
    def __init__(
        self,
        result: SalesOpportunity | None,
    ) -> None:
        self.result = result

    def get_sales_opportunity(
        self,
        sales_opportunity_id: str,
    ) -> SalesOpportunity | None:
        if self.result is None:
            return None

        return SalesOpportunity(
            **{
                **self.result.__dict__,
                "sales_opportunity_id": (sales_opportunity_id),
            }
        )


def _client(
    write: FakeWriteService | None = None,
    read: FakeReadService | None = None,
    *,
    writes_enabled: bool = True,
) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(operations.router)

    app.dependency_overrides[get_settings] = lambda: Settings(
        commercial_operations_writes_enabled=writes_enabled,
    )

    if write is not None:
        app.dependency_overrides[operations.get_commercial_operations_service] = (
            lambda: write
        )

    if read is not None:
        app.dependency_overrides[operations.get_commercial_operations_read_service] = (
            lambda: read
        )

    return TestClient(
        app,
        raise_server_exceptions=False,
    )


def test_promotion_uses_trusted_operator_and_returns_201() -> None:
    service = FakeWriteService()

    response = _client(write=service).post(
        "/operations/sales-opportunities/promote",
        headers=HEADERS,
        json={
            "source_opportunity_id": "o_1",
            "title": "Centrífuga refrigerada",
            "owner_key": "tatiana@origenlab.cl",
        },
    )

    assert response.status_code == 201
    assert response.json()["stage"] == "new"
    assert response.json()["source_kind"] == "pr3"

    call = service.calls[-1]

    assert call["operator"] == "tatiana@origenlab.cl"
    assert call["idempotency_key"] == "promote-1"


def test_promotion_without_owner_key_defaults_to_operator() -> None:
    service = FakeWriteService()

    response = _client(write=service).post(
        "/operations/sales-opportunities/promote",
        headers=HEADERS,
        json={
            "source_opportunity_id": "o_1",
            "title": "Centrífuga refrigerada",
        },
    )

    assert response.status_code == 201

    call = service.calls[-1]
    assert call["owner_key"] is None
    assert call["operator"] == "tatiana@origenlab.cl"


def test_browser_cannot_spoof_server_controlled_fields() -> None:
    service = FakeWriteService()

    response = _client(write=service).post(
        "/operations/sales-opportunities/promote",
        headers=HEADERS,
        json={
            "source_opportunity_id": "o_1",
            "title": "Centrífuga",
            "owner_key": "tatiana@origenlab.cl",
            "stage": "won",
            "account_id": "spoofed",
            "created_by": "attacker@example.com",
        },
    )

    assert response.status_code == 422
    assert service.calls == []


def test_missing_idempotency_key_is_422() -> None:
    service = FakeWriteService()

    response = _client(write=service).post(
        "/operations/sales-opportunities/promote",
        headers={
            "X-OriginLab-Operator-Email": "Tatiana@OrigenLab.CL",
        },
        json={
            "source_opportunity_id": "o_1",
            "title": "Centrífuga",
            "owner_key": "tatiana@origenlab.cl",
        },
    )

    assert response.status_code == 422
    assert service.calls == []


def test_missing_operator_identity_is_401() -> None:
    service = FakeWriteService()

    response = _client(write=service).post(
        "/operations/sales-opportunities/promote",
        headers={
            "Idempotency-Key": "promote-1",
        },
        json={
            "source_opportunity_id": "o_1",
            "title": "Centrífuga",
            "owner_key": "tatiana@origenlab.cl",
        },
    )

    assert response.status_code == 401
    assert service.calls == []


def test_disabled_writes_are_503() -> None:
    service = FakeWriteService()

    response = _client(
        write=service,
        writes_enabled=False,
    ).post(
        "/operations/sales-opportunities/promote",
        headers=HEADERS,
        json={
            "source_opportunity_id": "o_1",
            "title": "Centrífuga",
            "owner_key": "tatiana@origenlab.cl",
        },
    )

    assert response.status_code == 503
    assert service.calls == []


def test_missing_source_maps_to_404() -> None:
    service = FakeWriteService()
    service.error = CommercialOperationNotFoundError(
        "Commercial opportunity not found: missing"
    )

    response = _client(write=service).post(
        "/operations/sales-opportunities/promote",
        headers=HEADERS,
        json={
            "source_opportunity_id": "missing",
            "title": "Centrífuga",
            "owner_key": "tatiana@origenlab.cl",
        },
    )

    assert response.status_code == 404


def test_duplicate_promotion_maps_to_409() -> None:
    service = FakeWriteService()
    service.error = CommercialOperationConflictError(
        "Commercial opportunity already promoted"
    )

    response = _client(write=service).post(
        "/operations/sales-opportunities/promote",
        headers=HEADERS,
        json={
            "source_opportunity_id": "o_1",
            "title": "Centrífuga",
            "owner_key": "tatiana@origenlab.cl",
        },
    )

    assert response.status_code == 409


def test_sales_opportunity_read_returns_200() -> None:
    response = _client(
        read=FakeReadService(_sales()),
    ).get("/operations/sales-opportunities/sales_1")

    assert response.status_code == 200

    body = response.json()

    assert body["meta"] == {
        "data_source": "postgres",
        "read_only": True,
    }

    assert body["item"]["sales_opportunity_id"] == "sales_1"
    assert body["item"]["stage"] == "new"


def test_missing_sales_opportunity_read_returns_404() -> None:
    response = _client(
        read=FakeReadService(None),
    ).get("/operations/sales-opportunities/missing")

    assert response.status_code == 404
