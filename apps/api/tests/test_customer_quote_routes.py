"""CRM-Q1 HTTP tests for customer-quote commands and reads."""

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
    QuoteNumberingNotConfiguredError,
)
from origenlab_api.routes import operations
from origenlab_api.settings import Settings, get_settings


NOW = datetime(2026, 8, 30, 14, 0, tzinfo=timezone.utc)

QUOTE_ID = "quote_" + "a" * 32
SALES_ID = "sales_" + "b" * 32

OPERATOR_HEADER = {
    "X-OriginLab-Operator-Email": "Tatiana@OrigenLab.CL",
}

CREATE_HEADER = {
    **OPERATOR_HEADER,
    "Idempotency-Key": "quote-create-key-1",
}


def _workspace(**overrides: Any) -> CustomerQuoteDriveWorkspace:
    values: dict[str, Any] = {
        "quote_id": QUOTE_ID,
        "provider": "google_drive",
        "provisioning_status": "ready",
        "folder_id": "folder-1",
        "folder_web_url": "https://drive.google.com/drive/folders/folder-1",
        "sheet_file_id": "sheet-1",
        "sheet_web_url": "https://docs.google.com/spreadsheets/d/sheet-1",
        "failure_category": None,
        "attempt_count": 1,
        "version": 3,
        "requested_at": NOW,
        "completed_at": NOW,
        "created_by": "tatiana@origenlab.cl",
        "updated_by": "tatiana@origenlab.cl",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return CustomerQuoteDriveWorkspace(**values)


def _bundle(
    workspace: CustomerQuoteDriveWorkspace | None = None,
) -> CustomerQuoteBundle:
    return CustomerQuoteBundle(
        quote=CustomerQuote(
            quote_id=QUOTE_ID,
            sales_opportunity_id=SALES_ID,
            quote_number="CN011729",
            status="draft",
            version=1,
            created_by="tatiana@origenlab.cl",
            updated_by="tatiana@origenlab.cl",
            created_at=NOW,
            updated_at=NOW,
        ),
        revision=CustomerQuoteRevision(
            quote_id=QUOTE_ID,
            revision_number=1,
            template_reference="template-file-1",
            status="draft",
            created_by="tatiana@origenlab.cl",
            created_at=NOW,
        ),
        workspace=workspace or _workspace(),
        sales_opportunity_title="Centrífuga CEAF",
    )


class FakeQuoteService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.errors: dict[str, Exception] = {}
        self.create_result = _bundle()
        self.retry_result = _bundle()

    def create_quote(self, **kwargs: Any) -> CustomerQuoteBundle:
        self.calls.append(("create_quote", kwargs))
        exc = self.errors.get("create_quote")
        if exc is not None:
            raise exc
        return self.create_result

    def retry_drive_provisioning(self, **kwargs: Any) -> CustomerQuoteBundle:
        self.calls.append(("retry_drive_provisioning", kwargs))
        exc = self.errors.get("retry_drive_provisioning")
        if exc is not None:
            raise exc
        return self.retry_result


class FakeQuoteReadService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.list_result: list[CustomerQuoteBundle] = [_bundle()]
        self.get_result: CustomerQuoteBundle | None = _bundle()

    def list_quotes_for_sales_opportunity(
        self, sales_opportunity_id: str, *, limit: int = 100
    ) -> list[CustomerQuoteBundle]:
        self.calls.append(
            (
                "list_quotes_for_sales_opportunity",
                {"sales_opportunity_id": sales_opportunity_id, "limit": limit},
            )
        )
        return self.list_result

    def get_quote(self, quote_id: str) -> CustomerQuoteBundle | None:
        self.calls.append(("get_quote", {"quote_id": quote_id}))
        return self.get_result


def _client(
    service: FakeQuoteService | None = None,
    read_service: FakeQuoteReadService | None = None,
    *,
    writes_enabled: bool = True,
) -> tuple[TestClient, FakeQuoteService, FakeQuoteReadService]:
    service = service or FakeQuoteService()
    read_service = read_service or FakeQuoteReadService()

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(operations.router)

    settings = Settings(
        commercial_operations_writes_enabled=writes_enabled,
    )

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[operations.get_customer_quote_service] = (
        lambda: service
    )
    app.dependency_overrides[operations.get_customer_quote_read_service] = (
        lambda: read_service
    )

    client = TestClient(
        app,
        raise_server_exceptions=False,
    )

    return client, service, read_service


def test_create_quote_returns_created_quote_with_workspace() -> None:
    client, service, _ = _client()

    response = client.post(
        f"/operations/sales-opportunities/{SALES_ID}/quotes",
        json={},
        headers=CREATE_HEADER,
    )

    assert response.status_code == 201

    body = response.json()

    assert body["quote_id"] == QUOTE_ID
    assert body["quote_number"] == "CN011729"
    assert body["status"] == "draft"
    assert body["latest_revision_number"] == 1
    assert body["drive_workspace"]["provisioning_status"] == "ready"
    assert body["drive_workspace"]["folder_web_url"].startswith("https://")

    method, kwargs = service.calls[0]

    assert method == "create_quote"
    assert kwargs["sales_opportunity_id"] == SALES_ID
    assert kwargs["operator"] == "tatiana@origenlab.cl"
    assert kwargs["idempotency_key"] == "quote-create-key-1"


def test_create_quote_requires_operator_identity() -> None:
    client, service, _ = _client()

    response = client.post(
        f"/operations/sales-opportunities/{SALES_ID}/quotes",
        json={},
        headers={"Idempotency-Key": "quote-create-key-1"},
    )

    assert response.status_code == 401
    assert service.calls == []


def test_create_quote_requires_idempotency_key() -> None:
    client, service, _ = _client()

    response = client.post(
        f"/operations/sales-opportunities/{SALES_ID}/quotes",
        json={},
        headers=OPERATOR_HEADER,
    )

    assert response.status_code == 422
    assert service.calls == []


def test_create_quote_rejects_browser_invented_fields() -> None:
    client, service, _ = _client()

    response = client.post(
        f"/operations/sales-opportunities/{SALES_ID}/quotes",
        json={"quote_number": "CN999999"},
        headers=CREATE_HEADER,
    )

    assert response.status_code == 422
    assert service.calls == []


def test_create_quote_respects_write_kill_switch() -> None:
    client, service, _ = _client(writes_enabled=False)

    response = client.post(
        f"/operations/sales-opportunities/{SALES_ID}/quotes",
        json={},
        headers=CREATE_HEADER,
    )

    assert response.status_code == 503
    assert service.calls == []


def test_create_quote_maps_numbering_not_configured_to_503() -> None:
    client, service, _ = _client()
    service.errors["create_quote"] = QuoteNumberingNotConfiguredError(
        "quote_numbering_not_configured"
    )

    response = client.post(
        f"/operations/sales-opportunities/{SALES_ID}/quotes",
        json={},
        headers=CREATE_HEADER,
    )

    assert response.status_code == 503
    assert "quote_numbering_not_configured" in response.text


def test_create_quote_maps_missing_opportunity_to_404() -> None:
    client, service, _ = _client()
    service.errors["create_quote"] = CommercialOperationNotFoundError(
        f"Sales opportunity not found: {SALES_ID}"
    )

    response = client.post(
        f"/operations/sales-opportunities/{SALES_ID}/quotes",
        json={},
        headers=CREATE_HEADER,
    )

    assert response.status_code == 404


def test_create_quote_failed_workspace_still_returns_created_quote() -> None:
    client, service, _ = _client()
    service.create_result = _bundle(
        _workspace(
            provisioning_status="failed",
            failure_category="drive_not_configured",
            folder_id=None,
            folder_web_url=None,
            sheet_file_id=None,
            sheet_web_url=None,
            completed_at=None,
        )
    )

    response = client.post(
        f"/operations/sales-opportunities/{SALES_ID}/quotes",
        json={},
        headers=CREATE_HEADER,
    )

    assert response.status_code == 201

    workspace = response.json()["drive_workspace"]

    assert workspace["provisioning_status"] == "failed"
    assert workspace["failure_category"] == "drive_not_configured"


def test_create_quote_response_never_leaks_provider_internals() -> None:
    client, _, _ = _client()

    response = client.post(
        f"/operations/sales-opportunities/{SALES_ID}/quotes",
        json={},
        headers=CREATE_HEADER,
    )

    text = response.text.lower()

    for forbidden in ("token", "credential", "service_account", "authorization"):
        assert forbidden not in text


def test_retry_drive_workspace_returns_updated_state() -> None:
    client, service, _ = _client()

    response = client.post(
        f"/operations/customer-quotes/{QUOTE_ID}/drive-workspace",
        json={"expected_version": 3},
        headers=OPERATOR_HEADER,
    )

    assert response.status_code == 200
    assert response.json()["drive_workspace"]["provisioning_status"] == "ready"

    method, kwargs = service.calls[0]

    assert method == "retry_drive_provisioning"
    assert kwargs["quote_id"] == QUOTE_ID
    assert kwargs["expected_version"] == 3


def test_retry_drive_workspace_validates_expected_version() -> None:
    client, service, _ = _client()

    response = client.post(
        f"/operations/customer-quotes/{QUOTE_ID}/drive-workspace",
        json={"expected_version": 0},
        headers=OPERATOR_HEADER,
    )

    assert response.status_code == 422
    assert service.calls == []


def test_retry_drive_workspace_maps_conflict_to_409() -> None:
    client, service, _ = _client()
    service.errors["retry_drive_provisioning"] = (
        CommercialOperationConflictError("Drive workspace version conflict")
    )

    response = client.post(
        f"/operations/customer-quotes/{QUOTE_ID}/drive-workspace",
        json={"expected_version": 1},
        headers=OPERATOR_HEADER,
    )

    assert response.status_code == 409


def test_retry_drive_workspace_requires_operator_identity() -> None:
    client, service, _ = _client()

    response = client.post(
        f"/operations/customer-quotes/{QUOTE_ID}/drive-workspace",
        json={"expected_version": 1},
    )

    assert response.status_code == 401
    assert service.calls == []


def test_list_quotes_for_sales_opportunity() -> None:
    client, _, read_service = _client()

    response = client.get(
        f"/operations/sales-opportunities/{SALES_ID}/quotes",
    )

    assert response.status_code == 200

    body = response.json()

    assert body["meta"]["count"] == 1
    assert body["items"][0]["quote_number"] == "CN011729"
    assert body["items"][0]["drive_workspace"]["provisioning_status"] == "ready"

    method, kwargs = read_service.calls[0]

    assert method == "list_quotes_for_sales_opportunity"
    assert kwargs["sales_opportunity_id"] == SALES_ID


def test_get_customer_quote_detail() -> None:
    client, _, _ = _client()

    response = client.get(f"/operations/customer-quotes/{QUOTE_ID}")

    assert response.status_code == 200
    assert response.json()["item"]["quote_id"] == QUOTE_ID


def test_get_customer_quote_missing_returns_404() -> None:
    client, _, read_service = _client()
    read_service.get_result = None

    response = client.get(f"/operations/customer-quotes/{QUOTE_ID}")

    assert response.status_code == 404
