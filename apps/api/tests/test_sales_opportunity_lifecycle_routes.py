"""CRM-2 HTTP tests for sales-opportunity lifecycle transitions."""

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


NOW = datetime(2026, 8, 26, 16, 0, tzinfo=timezone.utc)

OPERATOR_HEADER = {
    "X-OriginLab-Operator-Email": "Tatiana@OrigenLab.CL",
}


def _sales(
    *,
    stage: str = "qualifying",
    version: int = 2,
) -> SalesOpportunity:
    return SalesOpportunity(
        sales_opportunity_id="sales_1",
        source_kind="pr3",
        source_opportunity_id="o_1",
        account_id="a_1",
        primary_contact_id="c_1",
        title="Centrífuga refrigerada",
        stage=stage,
        owner_key="tatiana@origenlab.cl",
        version=version,
        created_by="tatiana@origenlab.cl",
        updated_by="tatiana@origenlab.cl",
        created_at=NOW,
        updated_at=NOW,
    )


class FakeWriteService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.error: Exception | None = None

    def transition_sales_opportunity_stage(
        self,
        **kwargs: Any,
    ) -> SalesOpportunity:
        self.calls.append(kwargs)

        if self.error is not None:
            raise self.error

        return _sales(
            stage=kwargs["stage"],
            version=kwargs["expected_version"] + 1,
        )


def _client(
    service: FakeWriteService,
    *,
    writes_enabled: bool = True,
) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(operations.router)

    app.dependency_overrides[get_settings] = lambda: Settings(
        commercial_operations_writes_enabled=writes_enabled,
    )

    app.dependency_overrides[operations.get_commercial_operations_service] = lambda: (
        service
    )

    return TestClient(
        app,
        raise_server_exceptions=False,
    )


def test_stage_transition_uses_trusted_operator_and_needs_no_idempotency_key() -> None:
    service = FakeWriteService()

    response = _client(service).post(
        "/operations/sales-opportunities/sales_1/stage",
        headers=OPERATOR_HEADER,
        json={
            "stage": "qualifying",
            "expected_version": 1,
        },
    )

    assert response.status_code == 200

    body = response.json()

    assert body["stage"] == "qualifying"
    assert body["version"] == 2
    assert body["updated_by"] == "tatiana@origenlab.cl"

    assert service.calls == [
        {
            "sales_opportunity_id": "sales_1",
            "stage": "qualifying",
            "operator": "tatiana@origenlab.cl",
            "expected_version": 1,
        }
    ]


def test_browser_cannot_spoof_lifecycle_provenance() -> None:
    service = FakeWriteService()

    response = _client(service).post(
        "/operations/sales-opportunities/sales_1/stage",
        headers=OPERATOR_HEADER,
        json={
            "stage": "qualifying",
            "expected_version": 1,
            "updated_by": "attacker@example.com",
        },
    )

    assert response.status_code == 422
    assert service.calls == []


def test_stage_transition_missing_operator_is_401() -> None:
    service = FakeWriteService()

    response = _client(service).post(
        "/operations/sales-opportunities/sales_1/stage",
        json={
            "stage": "qualifying",
            "expected_version": 1,
        },
    )

    assert response.status_code == 401
    assert service.calls == []


def test_stage_transition_disabled_writes_is_503() -> None:
    service = FakeWriteService()

    response = _client(
        service,
        writes_enabled=False,
    ).post(
        "/operations/sales-opportunities/sales_1/stage",
        headers=OPERATOR_HEADER,
        json={
            "stage": "qualifying",
            "expected_version": 1,
        },
    )

    assert response.status_code == 503
    assert service.calls == []


def test_stage_transition_missing_sales_opportunity_maps_to_404() -> None:
    service = FakeWriteService()
    service.error = CommercialOperationNotFoundError(
        "Sales opportunity not found: sales_missing"
    )

    response = _client(service).post(
        "/operations/sales-opportunities/sales_missing/stage",
        headers=OPERATOR_HEADER,
        json={
            "stage": "qualifying",
            "expected_version": 1,
        },
    )

    assert response.status_code == 404


def test_stage_transition_conflict_maps_to_409() -> None:
    service = FakeWriteService()
    service.error = CommercialOperationConflictError(
        "Sales opportunity version conflict"
    )

    response = _client(service).post(
        "/operations/sales-opportunities/sales_1/stage",
        headers=OPERATOR_HEADER,
        json={
            "stage": "qualified",
            "expected_version": 1,
        },
    )

    assert response.status_code == 409
