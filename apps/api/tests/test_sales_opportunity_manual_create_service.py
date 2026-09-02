"""Unit tests for CommercialOperationsService.create_manual_sales_opportunity."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from origenlab_api.repositories.postgres.commercial_operations import SalesOpportunity
from origenlab_api.services.commercial_operations_service import (
    CommercialOperationsService,
)


class _FakeRepository:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def create_manual_sales_opportunity(self, **kwargs: object) -> SalesOpportunity:
        self.calls.append(kwargs)
        now = datetime.now(timezone.utc)
        return SalesOpportunity(
            sales_opportunity_id=kwargs["sales_opportunity_id"],  # type: ignore[arg-type]
            source_kind="manual",
            source_opportunity_id=kwargs["sales_opportunity_id"],  # type: ignore[arg-type]
            account_id=None,
            primary_contact_id=None,
            title=kwargs["title"],  # type: ignore[arg-type]
            stage="new",
            owner_key=kwargs["owner_key"],  # type: ignore[arg-type]
            version=1,
            created_by=kwargs["operator"],  # type: ignore[arg-type]
            updated_by=kwargs["operator"],  # type: ignore[arg-type]
            created_at=now,
            updated_at=now,
            organization_id=None,
            primary_crm_contact_id=None,
        )


def _service() -> tuple[CommercialOperationsService, _FakeRepository]:
    service = CommercialOperationsService.__new__(CommercialOperationsService)
    fake = _FakeRepository()
    service._repository = fake  # type: ignore[attr-defined]
    return service, fake


def test_normalizes_title_and_defaults_owner_key_to_operator() -> None:
    service, fake = _service()

    service.create_manual_sales_opportunity(
        title="  Centrífuga refrigerada  ",
        operator="Tatiana@OrigenLab.cl",
        idempotency_key="manual:1",
    )

    assert fake.calls[0]["title"] == "Centrífuga refrigerada"
    assert fake.calls[0]["operator"] == "tatiana@origenlab.cl"
    assert fake.calls[0]["owner_key"] == "tatiana@origenlab.cl"
    assert fake.calls[0]["sales_opportunity_id"].startswith("sales_")  # type: ignore[union-attr]


def test_rejects_blank_title() -> None:
    service, _ = _service()

    with pytest.raises(ValueError):
        service.create_manual_sales_opportunity(
            title="   ",
            operator="tatiana@origenlab.cl",
            idempotency_key="manual:2",
        )


def test_rejects_both_organization_id_and_organization_display_name() -> None:
    service, _ = _service()

    with pytest.raises(ValueError):
        service.create_manual_sales_opportunity(
            title="Autoclave",
            operator="tatiana@origenlab.cl",
            idempotency_key="manual:3",
            organization_id="org_abc",
            organization_display_name="Hospital X",
        )


def test_rejects_contact_without_any_organization() -> None:
    service, _ = _service()

    with pytest.raises(ValueError):
        service.create_manual_sales_opportunity(
            title="Autoclave",
            operator="tatiana@origenlab.cl",
            idempotency_key="manual:4",
            contact_display_name="Marcela Soto",
        )


def test_two_calls_with_the_same_inputs_produce_the_same_fingerprint() -> None:
    service, fake = _service()

    service.create_manual_sales_opportunity(
        title="Balanza analítica",
        operator="tatiana@origenlab.cl",
        idempotency_key="manual:5a",
    )
    service.create_manual_sales_opportunity(
        title="Balanza analítica",
        operator="tatiana@origenlab.cl",
        idempotency_key="manual:5b",
    )

    assert fake.calls[0]["request_fingerprint"] == fake.calls[1]["request_fingerprint"]
