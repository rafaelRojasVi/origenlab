"""ARCH-3B4 HTTP tests for commercial operations commands."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from origenlab_api.errors import register_exception_handlers
from origenlab_api.repositories.postgres.commercial_operations import (
    Activity,
    CommercialOperationConflictError,
    CommercialOperationNotFoundError,
    OperatorState,
    Task,
)
from origenlab_api.routes import operations
from origenlab_api.settings import Settings, get_settings


NOW = datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc)

OPERATOR_HEADER = {
    "X-OriginLab-Operator-Email": "Tatiana@OrigenLab.CL",
}

CREATE_HEADER = {
    **OPERATOR_HEADER,
    "Idempotency-Key": "test-create-key-1",
}


class FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.errors: dict[str, Exception] = {}

    def _raise_if_configured(self, method: str) -> None:
        exc = self.errors.get(method)
        if exc is not None:
            raise exc

    def set_opportunity_state(self, **kwargs: Any) -> OperatorState:
        self.calls.append(("set_opportunity_state", kwargs))
        self._raise_if_configured("set_opportunity_state")

        return OperatorState(
            opportunity_id=kwargs["opportunity_id"],
            confirmation_status=kwargs["confirmation_status"],
            manual_stage=kwargs["manual_stage"],
            owner_key=kwargs["owner_key"],
            version=1,
            created_by=kwargs["operator"],
            updated_by=kwargs["operator"],
            created_at=NOW,
            updated_at=NOW,
        )

    def create_activity(self, **kwargs: Any) -> Activity:
        self.calls.append(("create_activity", kwargs))
        self._raise_if_configured("create_activity")

        return Activity(
            activity_id="act_1",
            opportunity_id=kwargs["opportunity_id"],
            sales_opportunity_id=kwargs["sales_opportunity_id"],
            account_id=kwargs["account_id"],
            contact_id=kwargs["contact_id"],
            activity_type=kwargs["activity_type"],
            occurred_at=kwargs["occurred_at"],
            summary=kwargs["summary"],
            detail=kwargs["detail"],
            created_by=kwargs["operator"],
            created_at=NOW,
        )

    def create_task(self, **kwargs: Any) -> Task:
        self.calls.append(("create_task", kwargs))
        self._raise_if_configured("create_task")

        return Task(
            task_id="task_1",
            opportunity_id=kwargs["opportunity_id"],
            sales_opportunity_id=kwargs["sales_opportunity_id"],
            account_id=kwargs["account_id"],
            contact_id=kwargs["contact_id"],
            title=kwargs["title"],
            status="open",
            priority=kwargs["priority"],
            due_at=kwargs["due_at"],
            owner_key=kwargs["owner_key"],
            version=1,
            created_by=kwargs["operator"],
            updated_by=kwargs["operator"],
            completed_at=None,
            created_at=NOW,
            updated_at=NOW,
        )

    def complete_task(self, **kwargs: Any) -> Task:
        self.calls.append(("complete_task", kwargs))
        self._raise_if_configured("complete_task")

        return Task(
            task_id=kwargs["task_id"],
            opportunity_id="opp_1",
            account_id=None,
            contact_id=None,
            title="Follow up",
            status="done",
            priority="normal",
            due_at=None,
            owner_key="tatiana@origenlab.cl",
            version=kwargs["expected_version"] + 1,
            created_by="tatiana@origenlab.cl",
            updated_by=kwargs["operator"],
            completed_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        )

    def cancel_task(self, **kwargs: Any) -> Task:
        self.calls.append(("cancel_task", kwargs))
        self._raise_if_configured("cancel_task")

        return Task(
            task_id=kwargs["task_id"],
            opportunity_id="opp_1",
            account_id=None,
            contact_id=None,
            title="Follow up",
            status="cancelled",
            priority="normal",
            due_at=None,
            owner_key="tatiana@origenlab.cl",
            version=kwargs["expected_version"] + 1,
            created_by="tatiana@origenlab.cl",
            updated_by=kwargs["operator"],
            completed_at=None,
            created_at=NOW,
            updated_at=NOW,
        )


def _client(
    service: FakeService,
    *,
    writes_enabled: bool = True,
) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(operations.router)

    settings = Settings(
        commercial_operations_writes_enabled=writes_enabled,
    )

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[operations.get_commercial_operations_service] = lambda: (
        service
    )

    return TestClient(
        app,
        raise_server_exceptions=False,
    )


def test_set_opportunity_state_uses_trusted_operator_header() -> None:
    service = FakeService()
    client = _client(service)

    response = client.post(
        "/operations/opportunities/opp_1/state",
        headers=OPERATOR_HEADER,
        json={
            "confirmation_status": "confirmed",
            "manual_stage": "quote_sent",
            "expected_version": 0,
        },
    )

    assert response.status_code == 200

    method, kwargs = service.calls[-1]

    assert method == "set_opportunity_state"
    assert kwargs["operator"] == "tatiana@origenlab.cl"
    assert kwargs["opportunity_id"] == "opp_1"
    assert kwargs["expected_version"] == 0


def test_opportunity_not_found_maps_to_404() -> None:
    service = FakeService()
    service.errors["set_opportunity_state"] = CommercialOperationNotFoundError(
        "missing opportunity"
    )

    response = _client(service).post(
        "/operations/opportunities/missing/state",
        headers=OPERATOR_HEADER,
        json={
            "confirmation_status": "confirmed",
            "expected_version": 0,
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_opportunity_state_requires_expected_version() -> None:
    service = FakeService()

    response = _client(service).post(
        "/operations/opportunities/opp_1/state",
        headers=OPERATOR_HEADER,
        json={
            "confirmation_status": "confirmed",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert service.calls == []


def test_opportunity_version_conflict_maps_to_409() -> None:
    service = FakeService()
    service.errors["set_opportunity_state"] = CommercialOperationConflictError(
        "version conflict"
    )

    response = _client(service).post(
        "/operations/opportunities/opp_1/state",
        headers=OPERATOR_HEADER,
        json={
            "confirmation_status": "confirmed",
            "expected_version": 2,
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


def test_service_validation_maps_to_422() -> None:
    service = FakeService()
    service.errors["create_activity"] = ValueError(
        "At least one CRM context reference is required"
    )

    response = _client(service).post(
        "/operations/activities",
        headers=CREATE_HEADER,
        json={
            "activity_type": "call",
            "occurred_at": NOW.isoformat(),
            "summary": "Called customer",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_create_activity_returns_201() -> None:
    service = FakeService()

    response = _client(service).post(
        "/operations/activities",
        headers=CREATE_HEADER,
        json={
            "opportunity_id": "opp_1",
            "activity_type": "whatsapp",
            "occurred_at": NOW.isoformat(),
            "summary": "Cliente pidió seguimiento",
        },
    )

    assert response.status_code == 201
    assert response.json()["activity_id"] == "act_1"


def test_create_task_returns_201() -> None:
    service = FakeService()

    response = _client(service).post(
        "/operations/tasks",
        headers=CREATE_HEADER,
        json={
            "opportunity_id": "opp_1",
            "title": "Follow up",
            "priority": "high",
        },
    )

    assert response.status_code == 201
    assert response.json()["task_id"] == "task_1"
    assert response.json()["status"] == "open"


def test_create_activity_accepts_sales_opportunity_context() -> None:
    service = FakeService()

    response = _client(service).post(
        "/operations/activities",
        headers=CREATE_HEADER,
        json={
            "sales_opportunity_id": "sales_1",
            "activity_type": "call",
            "occurred_at": NOW.isoformat(),
            "summary": "Called customer",
        },
    )

    assert response.status_code == 201
    assert response.json()["sales_opportunity_id"] == "sales_1"

    method, kwargs = service.calls[-1]
    assert method == "create_activity"
    assert kwargs["sales_opportunity_id"] == "sales_1"
    assert kwargs["opportunity_id"] is None


def test_create_task_accepts_sales_opportunity_context() -> None:
    service = FakeService()

    response = _client(service).post(
        "/operations/tasks",
        headers=CREATE_HEADER,
        json={
            "sales_opportunity_id": "sales_1",
            "title": "Follow up",
        },
    )

    assert response.status_code == 201
    assert response.json()["sales_opportunity_id"] == "sales_1"

    method, kwargs = service.calls[-1]
    assert method == "create_task"
    assert kwargs["sales_opportunity_id"] == "sales_1"
    assert kwargs["opportunity_id"] is None


def test_browser_cannot_supply_created_by() -> None:
    service = FakeService()

    response = _client(service).post(
        "/operations/tasks",
        headers=CREATE_HEADER,
        json={
            "opportunity_id": "opp_1",
            "title": "Follow up",
            "created_by": "spoofed@attacker.example",
        },
    )

    assert response.status_code == 422
    assert service.calls == []


def test_complete_task_uses_expected_version() -> None:
    service = FakeService()

    response = _client(service).post(
        "/operations/tasks/task_1/complete",
        headers=OPERATOR_HEADER,
        json={
            "expected_version": 3,
        },
    )

    assert response.status_code == 200

    method, kwargs = service.calls[-1]

    assert method == "complete_task"
    assert kwargs["expected_version"] == 3
    assert kwargs["operator"] == "tatiana@origenlab.cl"


def test_missing_operator_identity_maps_to_401() -> None:
    service = FakeService()

    response = _client(service).post(
        "/operations/tasks",
        headers={
            "Idempotency-Key": "test-missing-operator",
        },
        json={
            "opportunity_id": "opp_1",
            "title": "Follow up",
        },
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert service.calls == []


def test_disabled_writes_fail_closed_before_service() -> None:
    service = FakeService()

    response = _client(
        service,
        writes_enabled=False,
    ).post(
        "/operations/tasks",
        headers=CREATE_HEADER,
        json={
            "opportunity_id": "opp_1",
            "title": "Follow up",
        },
    )

    assert response.status_code == 503
    assert service.calls == []


class FakeReadService:
    def get_operator_state(
        self,
        opportunity_id: str,
    ) -> OperatorState | None:
        del opportunity_id
        return None

    def list_activities(
        self,
        opportunity_id: str,
        *,
        limit: int,
    ) -> list[Activity]:
        del opportunity_id, limit
        return []

    def list_tasks(
        self,
        opportunity_id: str,
        *,
        limit: int,
    ) -> list[Task]:
        del opportunity_id, limit
        return []

    def list_sales_opportunity_activities(
        self,
        sales_opportunity_id: str,
        *,
        limit: int,
    ) -> list[Activity]:
        del sales_opportunity_id, limit
        return []

    def list_sales_opportunity_tasks(
        self,
        sales_opportunity_id: str,
        *,
        limit: int,
    ) -> list[Task]:
        del sales_opportunity_id, limit
        return []

    def get_work_queue(
        self,
        *,
        limit: int,
    ):
        del limit
        return ([], [], [])


def _read_client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(operations.router)

    app.dependency_overrides[operations.get_commercial_operations_read_service] = (
        lambda: FakeReadService()
    )

    return TestClient(
        app,
        raise_server_exceptions=False,
    )


def test_sales_opportunity_activity_read_returns_items() -> None:
    response = _read_client().get(
        "/operations/sales-opportunities/sales_1/activities"
    )

    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_sales_opportunity_task_read_returns_items() -> None:
    response = _read_client().get(
        "/operations/sales-opportunities/sales_1/tasks"
    )

    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_operator_state_read_returns_null_when_unreviewed() -> None:
    response = _read_client().get(
        "/operations/opportunities/o_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/state"
    )

    assert response.status_code == 200
    assert response.json() == {"state": None}


def test_activity_readback_returns_items() -> None:
    response = _read_client().get(
        "/operations/opportunities/o_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/activities"
    )

    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_task_readback_returns_items() -> None:
    response = _read_client().get(
        "/operations/opportunities/o_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/tasks"
    )

    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_work_queue_read_returns_empty_lists() -> None:
    response = _read_client().get("/operations/work-queue")

    assert response.status_code == 200
    assert response.json() == {
        "open_tasks": [],
        "review_opportunities": [],
        "quote_followups": [],
    }


def test_work_queue_read_serializes_populated_items() -> None:
    from types import SimpleNamespace

    opportunity_id = "o_" + ("a" * 32)
    task_id = "task_" + ("b" * 32)

    task = SimpleNamespace(
        task_id=task_id,
        opportunity_id=opportunity_id,
        account_id="a_1",
        contact_id="c_1",
        title="Llamar cliente",
        status="open",
        priority="urgent",
        due_at="2026-08-24T15:00:00Z",
        owner_key="tatiana@origenlab.cl",
        version=3,
        created_by="tatiana@origenlab.cl",
        updated_by="tatiana@origenlab.cl",
        completed_at=None,
        created_at="2026-08-24T14:00:00Z",
        updated_at="2026-08-24T14:00:00Z",
    )

    work_task = SimpleNamespace(
        task=task,
        contact_display_email="buyer@example.cl",
        account_display_domain="example.cl",
        canonical_stage="quote_sent",
        machine_review_status="needs_review",
    )

    opportunity = SimpleNamespace(
        opportunity_id=opportunity_id,
        contact_display_email="buyer@example.cl",
        account_display_domain="example.cl",
        canonical_stage="quote_sent",
        machine_review_status="needs_review",
        confirmation_status="needs_review",
        manual_stage="follow_up",
        owner_key="tatiana@origenlab.cl",
        operator_state_version=2,
    )

    class PopulatedReadService:
        def get_work_queue(
            self,
            *,
            limit: int,
        ):
            assert limit == 25
            return (
                [work_task],
                [opportunity],
                [opportunity],
            )

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(operations.router)

    app.dependency_overrides[operations.get_commercial_operations_read_service] = (
        lambda: PopulatedReadService()
    )

    client = TestClient(
        app,
        raise_server_exceptions=False,
    )

    response = client.get("/operations/work-queue?limit=25")

    assert response.status_code == 200

    payload = response.json()

    assert len(payload["open_tasks"]) == 1
    assert len(payload["review_opportunities"]) == 1
    assert len(payload["quote_followups"]) == 1

    assert payload["open_tasks"][0]["task"]["task_id"] == task_id
    assert payload["open_tasks"][0]["task"]["status"] == "open"
    assert payload["open_tasks"][0]["task"]["version"] == 3
    assert payload["open_tasks"][0]["account_display_domain"] == "example.cl"

    assert payload["review_opportunities"][0]["opportunity_id"] == opportunity_id
    assert payload["review_opportunities"][0]["confirmation_status"] == "needs_review"
    assert payload["review_opportunities"][0]["operator_state_version"] == 2

    assert payload["quote_followups"][0]["canonical_stage"] == "quote_sent"


def test_create_activity_missing_opportunity_maps_to_404() -> None:
    service = FakeService()
    service.errors["create_activity"] = CommercialOperationNotFoundError(
        "Commercial opportunity not found: missing"
    )

    response = _client(service).post(
        "/operations/activities",
        headers=CREATE_HEADER,
        json={
            "opportunity_id": "missing",
            "activity_type": "call",
            "occurred_at": NOW.isoformat(),
            "summary": "Called customer",
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_create_task_missing_opportunity_maps_to_404() -> None:
    service = FakeService()
    service.errors["create_task"] = CommercialOperationNotFoundError(
        "Commercial opportunity not found: missing"
    )

    response = _client(service).post(
        "/operations/tasks",
        headers=CREATE_HEADER,
        json={
            "opportunity_id": "missing",
            "title": "Follow up",
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_create_activity_requires_idempotency_key() -> None:
    service = FakeService()

    response = _client(service).post(
        "/operations/activities",
        headers=OPERATOR_HEADER,
        json={
            "opportunity_id": "opp_1",
            "activity_type": "call",
            "occurred_at": NOW.isoformat(),
            "summary": "Called customer",
        },
    )

    assert response.status_code == 422
    assert service.calls == []


def test_create_task_requires_idempotency_key() -> None:
    service = FakeService()

    response = _client(service).post(
        "/operations/tasks",
        headers=OPERATOR_HEADER,
        json={
            "opportunity_id": "opp_1",
            "title": "Follow up",
        },
    )

    assert response.status_code == 422
    assert service.calls == []


def test_create_activity_passes_idempotency_key_to_service() -> None:
    service = FakeService()

    response = _client(service).post(
        "/operations/activities",
        headers={
            **OPERATOR_HEADER,
            "Idempotency-Key": "activity.retry-123",
        },
        json={
            "opportunity_id": "opp_1",
            "activity_type": "call",
            "occurred_at": NOW.isoformat(),
            "summary": "Called customer",
        },
    )

    assert response.status_code == 201

    method, kwargs = service.calls[-1]
    assert method == "create_activity"
    assert kwargs["idempotency_key"] == "activity.retry-123"


def test_idempotency_conflict_maps_to_409() -> None:
    service = FakeService()
    service.errors["create_task"] = CommercialOperationConflictError(
        "Idempotency key reused with different request"
    )

    response = _client(service).post(
        "/operations/tasks",
        headers={
            **OPERATOR_HEADER,
            "Idempotency-Key": "task.retry-123",
        },
        json={
            "opportunity_id": "opp_1",
            "title": "Follow up",
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


def test_operator_state_read_serializes_dataclass() -> None:
    state = OperatorState(
        opportunity_id="o_" + ("a" * 32),
        confirmation_status="needs_review",
        manual_stage="fulfillment",
        owner_key="tatiana@origenlab.cl",
        version=2,
        created_by="tatiana@origenlab.cl",
        updated_by="rafael@origenlab.cl",
        created_at=NOW,
        updated_at=NOW,
    )

    class StateReadService(FakeReadService):
        def get_operator_state(
            self,
            opportunity_id: str,
        ) -> OperatorState | None:
            del opportunity_id
            return state

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(operations.router)

    app.dependency_overrides[operations.get_commercial_operations_read_service] = (
        lambda: StateReadService()
    )

    client = TestClient(
        app,
        raise_server_exceptions=False,
    )

    response = client.get(
        "/operations/opportunities/o_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/state"
    )

    assert response.status_code == 200

    body = response.json()

    assert body["state"]["opportunity_id"] == ("o_" + ("a" * 32))
    assert body["state"]["confirmation_status"] == "needs_review"
    assert body["state"]["manual_stage"] == "fulfillment"
    assert body["state"]["owner_key"] == "tatiana@origenlab.cl"
    assert body["state"]["version"] == 2
