"""CRM-Q2B HTTP tests: the customer_quote_close command route."""

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

CLOSE_HEADER = {
    **OPERATOR_HEADER,
    "Idempotency-Key": "close-key-1",
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
        "version": 5,
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
        "status": "closed_won",
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


class FakeCloseService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.error: Exception | None = None
        self.result = _bundle()

    def close_quote(self, **kwargs: Any) -> CustomerQuoteBundle:
        self.calls.append(("close_quote", kwargs))
        if self.error is not None:
            raise self.error
        return self.result


def _client(
    service: FakeCloseService | None = None,
    *,
    writes_enabled: bool = True,
) -> tuple[TestClient, FakeCloseService]:
    service = service or FakeCloseService()

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(operations.router)

    settings = Settings(commercial_operations_writes_enabled=writes_enabled)

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[operations.get_customer_quote_service] = lambda: service

    client = TestClient(app, raise_server_exceptions=False)

    return client, service


def test_close_route_returns_closed_board_stage_and_outcome() -> None:
    client, service = _client()

    response = client.post(
        f"/operations/customer-quotes/{QUOTE_ID}/close",
        json={"expected_version": 4, "outcome": "won"},
        headers=CLOSE_HEADER,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["quote_id"] == QUOTE_ID
    assert body["board_stage"] == "closed"
    assert body["quote_outcome"] == "won"

    name, kwargs = service.calls[0]
    assert name == "close_quote"
    assert kwargs["quote_id"] == QUOTE_ID
    assert kwargs["operator"] == "tatiana@origenlab.cl"
    assert kwargs["expected_version"] == 4
    assert kwargs["outcome"] == "won"
    assert kwargs["idempotency_key"] == "close-key-1"


def test_close_route_requires_idempotency_key_header() -> None:
    client, _service = _client()

    response = client.post(
        f"/operations/customer-quotes/{QUOTE_ID}/close",
        json={"expected_version": 4, "outcome": "won"},
        headers=OPERATOR_HEADER,
    )

    assert response.status_code == 422


def test_close_route_requires_operator_identity() -> None:
    client, _service = _client()

    response = client.post(
        f"/operations/customer-quotes/{QUOTE_ID}/close",
        json={"expected_version": 4, "outcome": "won"},
        headers={"Idempotency-Key": "close-key-1"},
    )

    assert response.status_code in (400, 401, 422)


def test_close_route_rejects_browser_invented_fields() -> None:
    client, _service = _client()

    response = client.post(
        f"/operations/customer-quotes/{QUOTE_ID}/close",
        json={"expected_version": 4, "outcome": "won", "revision_status": "closed_won"},
        headers=CLOSE_HEADER,
    )

    assert response.status_code == 422


def test_close_route_rejects_unsupported_outcome_value() -> None:
    client, _service = _client()

    response = client.post(
        f"/operations/customer-quotes/{QUOTE_ID}/close",
        json={"expected_version": 4, "outcome": "lost"},
        headers=CLOSE_HEADER,
    )

    assert response.status_code == 422


def test_close_route_maps_not_found_to_404() -> None:
    service = FakeCloseService()
    service.error = CommercialOperationNotFoundError("customer_quote_not_found: quote_x")
    client, _ = _client(service=service)

    response = client.post(
        f"/operations/customer-quotes/{QUOTE_ID}/close",
        json={"expected_version": 4, "outcome": "won"},
        headers=CLOSE_HEADER,
    )

    assert response.status_code == 404


def test_close_route_maps_illegal_transition_conflict_to_409() -> None:
    service = FakeCloseService()
    service.error = CommercialOperationConflictError(
        "customer_quote_illegal_transition: cannot close from 'approved'"
    )
    client, _ = _client(service=service)

    response = client.post(
        f"/operations/customer-quotes/{QUOTE_ID}/close",
        json={"expected_version": 4, "outcome": "won"},
        headers=CLOSE_HEADER,
    )

    assert response.status_code == 409
    assert "customer_quote_illegal_transition" in response.text


def test_close_route_maps_stale_version_conflict_to_409() -> None:
    service = FakeCloseService()
    service.error = CommercialOperationConflictError(
        "customer_quote_version_conflict: expected 4, found 5"
    )
    client, _ = _client(service=service)

    response = client.post(
        f"/operations/customer-quotes/{QUOTE_ID}/close",
        json={"expected_version": 4, "outcome": "won"},
        headers=CLOSE_HEADER,
    )

    assert response.status_code == 409


def test_close_route_respects_write_kill_switch() -> None:
    client, _service = _client(writes_enabled=False)

    response = client.post(
        f"/operations/customer-quotes/{QUOTE_ID}/close",
        json={"expected_version": 4, "outcome": "won"},
        headers=CLOSE_HEADER,
    )

    assert response.status_code in (403, 503)
