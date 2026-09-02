"""Route tests for GET /operations/customer-quotes/drive-pending."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from origenlab_api.drive.errors import DriveProvisioningError
from origenlab_api.errors import register_exception_handlers
from origenlab_api.routes import operations
from origenlab_api.services.drive_pending_quote_service import (
    DrivePendingQuoteWorkspace,
)
from origenlab_api.settings import Settings, get_settings


class FakeDrivePendingService:
    def __init__(
        self,
        workspaces: list[DrivePendingQuoteWorkspace] | None = None,
        *,
        error: DriveProvisioningError | None = None,
    ) -> None:
        self.workspaces = workspaces or []
        self.error = error

    def list_drive_pending_workspaces(self) -> list[DrivePendingQuoteWorkspace]:
        if self.error is not None:
            raise self.error
        return self.workspaces


def _client(service: FakeDrivePendingService) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(operations.router)

    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None,
        commercial_operations_writes_enabled=True,
    )
    app.dependency_overrides[operations.get_drive_pending_quote_service] = (
        lambda: service
    )

    return TestClient(app, raise_server_exceptions=False)


def test_returns_empty_list_with_meta() -> None:
    response = _client(FakeDrivePendingService()).get(
        "/operations/customer-quotes/drive-pending"
    )

    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    assert body["items"] == []
    assert body["meta"]["count"] == 0


def test_returns_drive_only_workspaces() -> None:
    workspace = DrivePendingQuoteWorkspace(
        folder_id="f1",
        folder_name="CN01191-ICN Chile",
        folder_web_url="https://drive.google.com/drive/folders/f1",
        document_identifier="CN01191",
        created_time=datetime(2026, 8, 1, tzinfo=timezone.utc),
        modified_time=datetime(2026, 8, 15, tzinfo=timezone.utc),
    )

    response = _client(FakeDrivePendingService([workspace])).get(
        "/operations/customer-quotes/drive-pending"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["count"] == 1
    item = body["items"][0]
    assert item["folder_id"] == "f1"
    assert item["folder_name"] == "CN01191-ICN Chile"
    assert item["document_identifier"] == "CN01191"
    assert item["folder_web_url"] == "https://drive.google.com/drive/folders/f1"
    # Never a durable customer-quote field.
    assert "quote_id" not in item
    assert "provisioning_status" not in item


def test_drive_provisioning_error_maps_to_redacted_503() -> None:
    service = FakeDrivePendingService(
        error=DriveProvisioningError("drive_unavailable")
    )

    response = _client(service).get("/operations/customer-quotes/drive-pending")

    assert response.status_code == 503
    body = response.json()
    message = body["error"]["message"]
    assert "drive_unavailable" in message
    # Only the redacted category slug reaches the response -- never a raw
    # provider/transport exception message.
    assert "boom" not in message
    assert "Traceback" not in message


def test_route_is_registered_before_the_quote_id_route() -> None:
    """A request for the static path must never be swallowed as a quote_id
    path parameter -- registration order is what Starlette matches on."""

    response = _client(FakeDrivePendingService()).get(
        "/operations/customer-quotes/drive-pending"
    )

    assert response.status_code == 200
