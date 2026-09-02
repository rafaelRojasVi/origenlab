"""CRM-Q2 service-layer tests: revision-workflow commands + Drive-folder
adoption. All Drive/DB behavior is exercised through a deterministic fake
repository; no test touches Postgres or the network."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

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
from origenlab_api.services.customer_quote_service import CustomerQuoteService
from origenlab_api.settings import Settings


NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)

QUOTE_ID = "quote_" + "a" * 32
SALES_ID = "sales_" + "b" * 32
OPERATOR = "tatiana@origenlab.cl"


def _settings() -> Settings:
    return Settings(_env_file=None)


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
        "created_by": OPERATOR,
        "updated_by": OPERATOR,
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
        "created_by": OPERATOR,
        "created_at": NOW,
        "updated_by": OPERATOR,
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
        "created_by": OPERATOR,
        "updated_by": OPERATOR,
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


class FakeWorkflowRepository:
    def __init__(self, *, result: CustomerQuoteBundle | Exception | None = None) -> None:
        self.result = result if result is not None else _bundle()
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _respond(self, name: str, kwargs: dict[str, Any]) -> CustomerQuoteBundle:
        self.calls.append((name, kwargs))
        if isinstance(self.result, Exception):
            raise self.result
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


# --- transition commands: normalization + delegation ----------------------


@pytest.mark.parametrize(
    "method_name",
    ["submit_for_review", "request_adjustments", "approve", "confirm_send"],
)
def test_transition_delegates_normalized_args_to_repository(method_name: str) -> None:
    repo = FakeWorkflowRepository()
    service = CustomerQuoteService(_settings(), repository=repo)

    method = getattr(service, method_name)
    bundle = method(
        quote_id=f"  {QUOTE_ID}  ",
        operator=f"  {OPERATOR.upper()}  ",
        expected_version=2,
    )

    assert bundle is repo.result
    assert len(repo.calls) == 1
    name, kwargs = repo.calls[0]
    assert name == method_name
    assert kwargs["quote_id"] == QUOTE_ID
    assert kwargs["operator"] == OPERATOR
    assert kwargs["expected_version"] == 2


@pytest.mark.parametrize(
    "method_name",
    ["submit_for_review", "request_adjustments", "approve", "confirm_send"],
)
def test_transition_rejects_expected_version_below_one(method_name: str) -> None:
    service = CustomerQuoteService(_settings(), repository=FakeWorkflowRepository())

    with pytest.raises(ValueError):
        getattr(service, method_name)(
            quote_id=QUOTE_ID, operator=OPERATOR, expected_version=0
        )


@pytest.mark.parametrize(
    "method_name",
    ["submit_for_review", "request_adjustments", "approve", "confirm_send"],
)
def test_transition_rejects_blank_quote_id(method_name: str) -> None:
    service = CustomerQuoteService(_settings(), repository=FakeWorkflowRepository())

    with pytest.raises(ValueError):
        getattr(service, method_name)(
            quote_id="   ", operator=OPERATOR, expected_version=1
        )


def test_transition_propagates_not_found_unchanged() -> None:
    repo = FakeWorkflowRepository(
        result=CommercialOperationNotFoundError("Customer quote not found")
    )
    service = CustomerQuoteService(_settings(), repository=repo)

    with pytest.raises(CommercialOperationNotFoundError):
        service.approve(quote_id=QUOTE_ID, operator=OPERATOR, expected_version=1)


def test_transition_propagates_conflict_unchanged() -> None:
    repo = FakeWorkflowRepository(
        result=CommercialOperationConflictError("Customer quote version conflict")
    )
    service = CustomerQuoteService(_settings(), repository=repo)

    with pytest.raises(CommercialOperationConflictError):
        service.confirm_send(quote_id=QUOTE_ID, operator=OPERATOR, expected_version=1)


# --- adopt_drive_folder -----------------------------------------------


def test_adopt_drive_folder_normalizes_and_delegates() -> None:
    repo = FakeWorkflowRepository()
    service = CustomerQuoteService(_settings(), repository=repo)

    bundle = service.adopt_drive_folder(
        sales_opportunity_id=f"  {SALES_ID}  ",
        document_number="  CN01191  ",
        quote_number="  01191-24  ",
        folder_id="  drive-folder-1191  ",
        folder_web_url="https://drive.google.com/drive/folders/drive-folder-1191",
        operator=f"  {OPERATOR.upper()}  ",
        idempotency_key="adopt-cn01191",
    )

    assert bundle is repo.result
    assert len(repo.calls) == 1
    name, kwargs = repo.calls[0]

    assert name == "adopt_drive_folder"
    assert kwargs["sales_opportunity_id"] == SALES_ID
    assert kwargs["document_number"] == "CN01191"
    assert kwargs["quote_number"] == "01191-24"
    assert kwargs["folder_id"] == "drive-folder-1191"
    assert (
        kwargs["folder_web_url"]
        == "https://drive.google.com/drive/folders/drive-folder-1191"
    )
    assert kwargs["operator"] == OPERATOR
    assert kwargs["idempotency_key"] == "adopt-cn01191"
    assert kwargs["quote_id"].startswith("quote_")
    assert len(kwargs["request_fingerprint"]) == 64


def test_adopt_drive_folder_generates_a_fresh_quote_id_each_call() -> None:
    repo = FakeWorkflowRepository()
    service = CustomerQuoteService(_settings(), repository=repo)

    for _ in range(2):
        service.adopt_drive_folder(
            sales_opportunity_id=SALES_ID,
            document_number="CN01191",
            quote_number="01191-24",
            folder_id="drive-folder-1191",
            folder_web_url="https://drive.google.com/drive/folders/drive-folder-1191",
            operator=OPERATOR,
            idempotency_key="adopt-cn01191",
        )

    first_id = repo.calls[0][1]["quote_id"]
    second_id = repo.calls[1][1]["quote_id"]
    assert first_id != second_id


def test_adopt_drive_folder_never_derives_quote_number_from_document_number() -> None:
    """A deliberately mismatched pair must reach the repository verbatim --
    proves the service applies no correlation/derivation between the two."""

    repo = FakeWorkflowRepository()
    service = CustomerQuoteService(_settings(), repository=repo)

    service.adopt_drive_folder(
        sales_opportunity_id=SALES_ID,
        document_number="CN09999",
        quote_number="00042-19",
        folder_id="drive-folder-9999",
        folder_web_url="https://drive.google.com/drive/folders/drive-folder-9999",
        operator=OPERATOR,
        idempotency_key="adopt-mismatch",
    )

    _, kwargs = repo.calls[0]
    assert kwargs["document_number"] == "CN09999"
    assert kwargs["quote_number"] == "00042-19"


@pytest.mark.parametrize(
    "field", ["sales_opportunity_id", "document_number", "quote_number", "folder_id", "folder_web_url"]
)
def test_adopt_drive_folder_rejects_blank_required_fields(field: str) -> None:
    service = CustomerQuoteService(_settings(), repository=FakeWorkflowRepository())

    kwargs: dict[str, Any] = {
        "sales_opportunity_id": SALES_ID,
        "document_number": "CN01191",
        "quote_number": "01191-24",
        "folder_id": "drive-folder-1191",
        "folder_web_url": "https://drive.google.com/drive/folders/drive-folder-1191",
        "operator": OPERATOR,
        "idempotency_key": "adopt-cn01191",
    }
    kwargs[field] = "   "

    with pytest.raises(ValueError):
        service.adopt_drive_folder(**kwargs)


def test_adopt_drive_folder_propagates_conflict_unchanged() -> None:
    repo = FakeWorkflowRepository(
        result=CommercialOperationConflictError(
            "quote_number or document_number already in use"
        )
    )
    service = CustomerQuoteService(_settings(), repository=repo)

    with pytest.raises(CommercialOperationConflictError):
        service.adopt_drive_folder(
            sales_opportunity_id=SALES_ID,
            document_number="CN01191",
            quote_number="01191-24",
            folder_id="drive-folder-1191",
            folder_web_url="https://drive.google.com/drive/folders/drive-folder-1191",
            operator=OPERATOR,
            idempotency_key="adopt-cn01191",
        )
