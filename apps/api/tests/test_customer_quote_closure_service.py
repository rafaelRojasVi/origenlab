"""Service-layer normalization tests for CustomerQuoteService.close_quote
(CRM-Q2B)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from origenlab_api.repositories.postgres.customer_quotes import (
    CustomerQuoteBundle,
    CustomerQuoteDriveWorkspace,
    CustomerQuoteRevision,
)
from origenlab_api.repositories.postgres.customer_quotes import CustomerQuote
from origenlab_api.services.customer_quote_service import CustomerQuoteService
from origenlab_api.settings import Settings


NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
QUOTE_ID = "quote_" + "a" * 32
SALES_ID = "sales_" + "b" * 32


def _bundle(status: str = "closed_won") -> CustomerQuoteBundle:
    return CustomerQuoteBundle(
        quote=CustomerQuote(
            quote_id=QUOTE_ID,
            sales_opportunity_id=SALES_ID,
            quote_number="01183-26",
            serial=1183,
            issue_year=2026,
            document_number="CN01183",
            quote_origin="generated",
            status="draft",
            version=5,
            created_by="tatiana@origenlab.cl",
            updated_by="tatiana@origenlab.cl",
            created_at=NOW,
            updated_at=NOW,
        ),
        revision=CustomerQuoteRevision(
            quote_id=QUOTE_ID,
            revision_number=1,
            template_reference=None,
            status=status,
            created_by="tatiana@origenlab.cl",
            created_at=NOW,
            updated_by="tatiana@origenlab.cl",
            updated_at=NOW,
        ),
        workspace=CustomerQuoteDriveWorkspace(
            quote_id=QUOTE_ID,
            provider="google_drive",
            provisioning_status="ready",
            folder_id="folder-1",
            folder_web_url="https://drive.google.com/drive/folders/folder-1",
            sheet_file_id="sheet-1",
            sheet_web_url="https://docs.google.com/spreadsheets/d/sheet-1",
            failure_category=None,
            attempt_count=1,
            version=1,
            lease_expires_at=None,
            requested_at=NOW,
            completed_at=NOW,
            created_by="tatiana@origenlab.cl",
            updated_by="tatiana@origenlab.cl",
            created_at=NOW,
            updated_at=NOW,
        ),
        sales_opportunity_title="Centrífuga CEAF",
    )


@dataclass
class _FakeRepository:
    close_calls: list[dict[str, object]]

    def close_quote(self, **kwargs: object) -> CustomerQuoteBundle:
        self.close_calls.append(kwargs)
        return _bundle()


def _service() -> tuple[CustomerQuoteService, _FakeRepository]:
    fake = _FakeRepository(close_calls=[])
    settings = Settings(commercial_operations_writes_enabled=True)
    service = CustomerQuoteService(settings, repository=fake)  # type: ignore[arg-type]
    return service, fake


def test_close_quote_normalizes_operator_email_case() -> None:
    service, fake = _service()

    service.close_quote(
        quote_id=QUOTE_ID,
        operator="Tatiana@OrigenLab.CL",
        expected_version=4,
        outcome="won",
        idempotency_key="close-1",
    )

    assert fake.close_calls[0]["operator"] == "tatiana@origenlab.cl"
    assert fake.close_calls[0]["outcome"] == "won"
    assert fake.close_calls[0]["expected_version"] == 4
    assert fake.close_calls[0]["idempotency_key"] == "close-1"


def test_close_quote_rejects_version_below_one() -> None:
    service, _fake = _service()

    with pytest.raises(ValueError):
        service.close_quote(
            quote_id=QUOTE_ID,
            operator="op@origenlab.cl",
            expected_version=0,
            outcome="won",
            idempotency_key="close-1",
        )


def test_close_quote_rejects_unsupported_outcome() -> None:
    service, _fake = _service()

    with pytest.raises(ValueError):
        service.close_quote(
            quote_id=QUOTE_ID,
            operator="op@origenlab.cl",
            expected_version=4,
            outcome="lost",  # type: ignore[arg-type]
            idempotency_key="close-1",
        )


def test_close_quote_generates_a_fingerprint_scoped_to_quote_and_outcome() -> None:
    service, fake = _service()

    service.close_quote(
        quote_id=QUOTE_ID,
        operator="op@origenlab.cl",
        expected_version=4,
        outcome="won",
        idempotency_key="close-1",
    )
    service.close_quote(
        quote_id=QUOTE_ID,
        operator="op@origenlab.cl",
        expected_version=4,
        outcome="null",
        idempotency_key="close-2",
    )

    won_fingerprint = fake.close_calls[0]["request_fingerprint"]
    null_fingerprint = fake.close_calls[1]["request_fingerprint"]
    assert won_fingerprint != null_fingerprint
