"""CRM-Q2 HTTP tests: revision-workflow commands, Drive-folder adoption,
and the event-history read endpoint."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from origenlab_api.errors import register_exception_handlers
from origenlab_api.repositories.postgres.commercial_operations import (
    CommercialOperationConflictError,
    CommercialOperationNotFoundError,
)
from origenlab_api.repositories.postgres.customer_quotes import (
    CustomerQuote,
    CustomerQuoteBundle,
    CustomerQuoteDriveWorkspace,
    CustomerQuoteEvent,
    CustomerQuoteRevision,
)
from origenlab_api.routes import operations
from origenlab_api.settings import Settings, get_settings


NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)

QUOTE_ID = "quote_" + "a" * 32
SALES_ID = "sales_" + "b" * 32

OPERATOR_HEADER = {
    "X-OriginLab-Operator-Email": "Tatiana@OrigenLab.CL",
}

ADOPT_HEADER = {
    **OPERATOR_HEADER,
    "Idempotency-Key": "adopt-key-1",
}


def _quote(**overrides: Any) -> CustomerQuote:
    values: dict[str, Any] = {
        "quote_id": QUOTE_ID,
        "sales_opportunity_id": SALES_ID,
        "quote_number": "01183-26",
        "serial": 1183,
        "issue_year": 2026,
        "document_number": "CN01183",
        "quote_origin": "generated",
        "status": "draft",
        "version": 2,
        "created_by": "tatiana@origenlab.cl",
        "updated_by": "tatiana@origenlab.cl",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return CustomerQuote(**values)


def _revision(**overrides: Any) -> CustomerQuoteRevision:
    values: dict[str, Any] = {
        "quote_id": QUOTE_ID,
        "revision_number": 1,
        "template_reference": None,
        "status": "pending_approval",
        "created_by": "tatiana@origenlab.cl",
        "created_at": NOW,
        "updated_by": "tatiana@origenlab.cl",
        "updated_at": NOW,
    }
    values.update(overrides)
    return CustomerQuoteRevision(**values)


def _workspace(**overrides: Any) -> CustomerQuoteDriveWorkspace:
    values: dict[str, Any] = {
        "quote_id": QUOTE_ID,
        "provider": "google_drive",
        "provisioning_status": "ready",
        "folder_id": "folder-1",
        "folder_web_url": "https://drive.google.com/drive/folders/folder-1",
        "sheet_file_id": None,
        "sheet_web_url": None,
        "failure_category": None,
        "attempt_count": 0,
        "version": 1,
        "lease_expires_at": None,
        "requested_at": None,
        "completed_at": NOW,
        "created_by": "tatiana@origenlab.cl",
        "updated_by": "tatiana@origenlab.cl",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return CustomerQuoteDriveWorkspace(**values)


def _bundle(**overrides: Any) -> CustomerQuoteBundle:
    return CustomerQuoteBundle(
        quote=overrides.get("quote") or _quote(),
        revision=overrides.get("revision") or _revision(),
        workspace=overrides.get("workspace") or _workspace(),
        sales_opportunity_title="Centrífuga CEAF",
    )


class FakeWorkflowService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.errors: dict[str, Exception] = {}
        self.result = _bundle()

    def _respond(self, name: str, kwargs: dict[str, Any]) -> CustomerQuoteBundle:
        self.calls.append((name, kwargs))
        exc = self.errors.get(name)
        if exc is not None:
            raise exc
        return self.result

    def submit_for_review(self, **kwargs: Any) -> CustomerQuoteBundle:
        return self._respond("submit_for_review", kwargs)

    def request_adjustments(self, **kwargs: Any) -> CustomerQuoteBundle:
        return self._respond("request_adjustments", kwargs)

    def approve(self, **kwargs: Any) -> CustomerQuoteBundle:
        return self._respond("approve", kwargs)

    def confirm_send(self, **kwargs: Any) -> CustomerQuoteBundle:
        return self._respond("confirm_send", kwargs)

    def adopt_drive_folder(self, **kwargs: Any) -> CustomerQuoteBundle:
        return self._respond("adopt_drive_folder", kwargs)


class FakeWorkflowReadService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.events: list[CustomerQuoteEvent] = [
            CustomerQuoteEvent(
                event_id="event_1",
                quote_id=QUOTE_ID,
                event_type="quote_submitted_for_review",
                actor_key="tatiana@origenlab.cl",
                payload={"revision_number": 1, "from_status": "draft", "to_status": "pending_approval"},
                created_at=NOW,
            )
        ]

    def list_events(self, quote_id: str) -> list[CustomerQuoteEvent]:
        self.calls.append(("list_events", {"quote_id": quote_id}))
        return self.events


def _client(
    service: FakeWorkflowService | None = None,
    read_service: FakeWorkflowReadService | None = None,
    *,
    writes_enabled: bool = True,
) -> tuple[TestClient, FakeWorkflowService, FakeWorkflowReadService]:
    service = service or FakeWorkflowService()
    read_service = read_service or FakeWorkflowReadService()

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(operations.router)

    settings = Settings(commercial_operations_writes_enabled=writes_enabled)

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[operations.get_customer_quote_service] = lambda: service
    app.dependency_overrides[operations.get_customer_quote_read_service] = (
        lambda: read_service
    )

    client = TestClient(app, raise_server_exceptions=False)

    return client, service, read_service


# --- transition routes -----------------------------------------------


TRANSITION_ROUTES = [
    ("submit-for-review", "submit_for_review"),
    ("request-adjustments", "request_adjustments"),
    ("approve", "approve"),
    ("confirm-send", "confirm_send"),
]


def test_transition_routes_return_updated_quote_and_call_service() -> None:
    for path_segment, method_name in TRANSITION_ROUTES:
        client, service, _ = _client()

        response = client.post(
            f"/operations/customer-quotes/{QUOTE_ID}/{path_segment}",
            json={"expected_version": 2},
            headers=OPERATOR_HEADER,
        )

        assert response.status_code == 200, (path_segment, response.text)
        body = response.json()
        assert body["quote_id"] == QUOTE_ID

        name, kwargs = service.calls[0]
        assert name == method_name
        assert kwargs["quote_id"] == QUOTE_ID
        assert kwargs["operator"] == "tatiana@origenlab.cl"
        assert kwargs["expected_version"] == 2


def test_transition_routes_require_operator_identity() -> None:
    for path_segment, _ in TRANSITION_ROUTES:
        client, _, _ = _client()

        response = client.post(
            f"/operations/customer-quotes/{QUOTE_ID}/{path_segment}",
            json={"expected_version": 1},
        )

        assert response.status_code in (400, 401, 422), path_segment


def test_transition_routes_reject_extra_body_fields() -> None:
    for path_segment, _ in TRANSITION_ROUTES:
        client, _, _ = _client()

        response = client.post(
            f"/operations/customer-quotes/{QUOTE_ID}/{path_segment}",
            json={"expected_version": 1, "status": "approved"},
            headers=OPERATOR_HEADER,
        )

        assert response.status_code == 422, path_segment


def test_transition_routes_map_not_found_to_404() -> None:
    for path_segment, method_name in TRANSITION_ROUTES:
        service = FakeWorkflowService()
        service.errors[method_name] = CommercialOperationNotFoundError(
            "Customer quote not found"
        )
        client, _, _ = _client(service=service)

        response = client.post(
            f"/operations/customer-quotes/{QUOTE_ID}/{path_segment}",
            json={"expected_version": 1},
            headers=OPERATOR_HEADER,
        )

        assert response.status_code == 404, path_segment


def test_transition_routes_map_conflict_to_409() -> None:
    for path_segment, method_name in TRANSITION_ROUTES:
        service = FakeWorkflowService()
        service.errors[method_name] = CommercialOperationConflictError(
            "Customer quote revision cannot transition from 'draft' to 'sent'"
        )
        client, _, _ = _client(service=service)

        response = client.post(
            f"/operations/customer-quotes/{QUOTE_ID}/{path_segment}",
            json={"expected_version": 1},
            headers=OPERATOR_HEADER,
        )

        assert response.status_code == 409, path_segment


def test_transition_routes_map_value_error_to_422() -> None:
    for path_segment, method_name in TRANSITION_ROUTES:
        service = FakeWorkflowService()
        service.errors[method_name] = ValueError("expected_version must be >= 1")
        client, _, _ = _client(service=service)

        response = client.post(
            f"/operations/customer-quotes/{QUOTE_ID}/{path_segment}",
            json={"expected_version": 1},
            headers=OPERATOR_HEADER,
        )

        assert response.status_code == 422, path_segment


def test_transition_routes_respect_write_kill_switch() -> None:
    for path_segment, _ in TRANSITION_ROUTES:
        client, _, _ = _client(writes_enabled=False)

        response = client.post(
            f"/operations/customer-quotes/{QUOTE_ID}/{path_segment}",
            json={"expected_version": 1},
            headers=OPERATOR_HEADER,
        )

        assert response.status_code in (403, 503), path_segment


# --- adopt-drive-folder route -----------------------------------------


def test_adopt_drive_folder_returns_created_quote() -> None:
    client, service, _ = _client()

    response = client.post(
        f"/operations/sales-opportunities/{SALES_ID}/quotes/adopt-drive-folder",
        json={
            "document_number": "CN01191",
            "quote_number": "01191-24",
            "folder_id": "drive-folder-1191",
            "folder_web_url": "https://drive.google.com/drive/folders/drive-folder-1191",
        },
        headers=ADOPT_HEADER,
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["quote_id"] == QUOTE_ID

    name, kwargs = service.calls[0]
    assert name == "adopt_drive_folder"
    assert kwargs["sales_opportunity_id"] == SALES_ID
    assert kwargs["document_number"] == "CN01191"
    assert kwargs["quote_number"] == "01191-24"
    assert kwargs["folder_id"] == "drive-folder-1191"
    assert kwargs["operator"] == "tatiana@origenlab.cl"
    assert kwargs["idempotency_key"] == "adopt-key-1"


def test_adopt_drive_folder_requires_idempotency_key() -> None:
    client, _, _ = _client()

    response = client.post(
        f"/operations/sales-opportunities/{SALES_ID}/quotes/adopt-drive-folder",
        json={
            "document_number": "CN01191",
            "quote_number": "01191-24",
            "folder_id": "drive-folder-1191",
            "folder_web_url": "https://drive.google.com/drive/folders/drive-folder-1191",
        },
        headers=OPERATOR_HEADER,
    )

    assert response.status_code == 422


def test_adopt_drive_folder_rejects_browser_invented_fields() -> None:
    client, _, _ = _client()

    response = client.post(
        f"/operations/sales-opportunities/{SALES_ID}/quotes/adopt-drive-folder",
        json={
            "document_number": "CN01191",
            "quote_number": "01191-24",
            "folder_id": "drive-folder-1191",
            "folder_web_url": "https://drive.google.com/drive/folders/drive-folder-1191",
            "serial": 1191,
        },
        headers=ADOPT_HEADER,
    )

    assert response.status_code == 422


def test_adopt_drive_folder_maps_conflict_to_409() -> None:
    service = FakeWorkflowService()
    service.errors["adopt_drive_folder"] = CommercialOperationConflictError(
        "quote_number or document_number already in use"
    )
    client, _, _ = _client(service=service)

    response = client.post(
        f"/operations/sales-opportunities/{SALES_ID}/quotes/adopt-drive-folder",
        json={
            "document_number": "CN01191",
            "quote_number": "01191-24",
            "folder_id": "drive-folder-1191",
            "folder_web_url": "https://drive.google.com/drive/folders/drive-folder-1191",
        },
        headers=ADOPT_HEADER,
    )

    assert response.status_code == 409


def test_adopt_drive_folder_maps_missing_opportunity_to_404() -> None:
    service = FakeWorkflowService()
    service.errors["adopt_drive_folder"] = CommercialOperationNotFoundError(
        "Sales opportunity not found"
    )
    client, _, _ = _client(service=service)

    response = client.post(
        f"/operations/sales-opportunities/{SALES_ID}/quotes/adopt-drive-folder",
        json={
            "document_number": "CN01191",
            "quote_number": "01191-24",
            "folder_id": "drive-folder-1191",
            "folder_web_url": "https://drive.google.com/drive/folders/drive-folder-1191",
        },
        headers=ADOPT_HEADER,
    )

    assert response.status_code == 404


# --- events route ------------------------------------------------------


def test_list_customer_quote_events_returns_history() -> None:
    client, _, read_service = _client()

    response = client.get(f"/operations/customer-quotes/{QUOTE_ID}/events")

    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["count"] == 1
    assert body["items"][0]["event_type"] == "quote_submitted_for_review"
    assert body["items"][0]["payload"]["from_status"] == "draft"

    name, kwargs = read_service.calls[0]
    assert name == "list_events"
    assert kwargs["quote_id"] == QUOTE_ID


def test_list_customer_quote_events_empty_for_unknown_quote() -> None:
    read_service = FakeWorkflowReadService()
    read_service.events = []
    client, _, _ = _client(read_service=read_service)

    response = client.get(f"/operations/customer-quotes/{QUOTE_ID}/events")

    assert response.status_code == 200
    assert response.json()["items"] == []
