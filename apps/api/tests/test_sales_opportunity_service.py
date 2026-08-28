"""CRM-1 service tests for durable human-owned sales opportunities."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from origenlab_api.repositories.postgres.commercial_operations import (
    SalesOpportunity,
)
from origenlab_api.services.commercial_operations_service import (
    CommercialOperationsService,
)
from origenlab_api.settings import Settings


NOW = datetime(2026, 8, 25, 20, 0, tzinfo=timezone.utc)


class FakeRepository:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def promote_sales_opportunity(
        self,
        **kwargs: Any,
    ) -> SalesOpportunity:
        self.calls.append(kwargs)

        return SalesOpportunity(
            sales_opportunity_id=kwargs["sales_opportunity_id"],
            source_kind="pr3",
            source_opportunity_id=kwargs["source_opportunity_id"],
            account_id="a_1",
            primary_contact_id="c_1",
            title=kwargs["title"],
            stage="new",
            owner_key=kwargs["owner_key"],
            version=1,
            created_by=kwargs["operator"],
            updated_by=kwargs["operator"],
            created_at=NOW,
            updated_at=NOW,
        )


def _service(
    repository: FakeRepository,
) -> CommercialOperationsService:
    return CommercialOperationsService(
        Settings(),
        repository=repository,  # type: ignore[arg-type]
    )


def test_promotion_normalizes_input_and_forces_server_identity() -> None:
    repository = FakeRepository()
    service = _service(repository)

    result = service.promote_sales_opportunity(
        source_opportunity_id="  o_123  ",
        title="  Centrífuga refrigerada  ",
        owner_key="tatiana@origenlab.cl",
        operator="Tatiana@OrigenLab.CL",
        idempotency_key="promote-123",
    )

    assert result.source_kind == "pr3"
    assert result.stage == "new"
    assert result.source_opportunity_id == "o_123"
    assert result.title == "Centrífuga refrigerada"
    assert result.created_by == "tatiana@origenlab.cl"

    assert len(repository.calls) == 1

    call = repository.calls[0]

    assert call["source_opportunity_id"] == "o_123"
    assert call["title"] == "Centrífuga refrigerada"
    assert call["owner_key"] == "tatiana@origenlab.cl"
    assert call["operator"] == "tatiana@origenlab.cl"
    assert call["idempotency_key"] == "promote-123"

    assert call["sales_opportunity_id"].startswith("sales_")
    assert len(call["request_fingerprint"]) == 64


def test_promotion_defaults_owner_to_operator_when_omitted() -> None:
    repository = FakeRepository()
    service = _service(repository)

    result = service.promote_sales_opportunity(
        source_opportunity_id="o_123",
        title="Centrífuga refrigerada",
        operator="Tatiana@OrigenLab.CL",
        idempotency_key="promote-123",
    )

    assert result.owner_key == "tatiana@origenlab.cl"
    assert repository.calls[0]["owner_key"] == "tatiana@origenlab.cl"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_opportunity_id", " "),
        ("title", " "),
        ("owner_key", " "),
        ("operator", " "),
        ("idempotency_key", " "),
    ],
)
def test_promotion_rejects_blank_required_values(
    field: str,
    value: str,
) -> None:
    repository = FakeRepository()
    service = _service(repository)

    kwargs = {
        "source_opportunity_id": "o_123",
        "title": "Centrífuga",
        "owner_key": "tatiana@origenlab.cl",
        "operator": "tatiana@origenlab.cl",
        "idempotency_key": "promote-123",
    }

    kwargs[field] = value

    with pytest.raises(ValueError):
        service.promote_sales_opportunity(**kwargs)

    assert repository.calls == []


def test_promotion_fingerprint_is_stable_after_normalization() -> None:
    repository = FakeRepository()
    service = _service(repository)

    service.promote_sales_opportunity(
        source_opportunity_id="o_123",
        title="Centrífuga",
        owner_key="tatiana@origenlab.cl",
        operator="tatiana@origenlab.cl",
        idempotency_key="first-key",
    )

    service.promote_sales_opportunity(
        source_opportunity_id="  o_123  ",
        title="  Centrífuga  ",
        owner_key="  tatiana@origenlab.cl  ",
        operator="Tatiana@OrigenLab.CL",
        idempotency_key="second-key",
    )

    first = repository.calls[0]["request_fingerprint"]
    second = repository.calls[1]["request_fingerprint"]

    assert first == second


def test_promotion_fingerprint_changes_when_business_request_changes() -> None:
    repository = FakeRepository()
    service = _service(repository)

    service.promote_sales_opportunity(
        source_opportunity_id="o_123",
        title="Centrífuga A",
        owner_key="tatiana@origenlab.cl",
        operator="tatiana@origenlab.cl",
        idempotency_key="same-key",
    )

    service.promote_sales_opportunity(
        source_opportunity_id="o_123",
        title="Centrífuga B",
        owner_key="tatiana@origenlab.cl",
        operator="tatiana@origenlab.cl",
        idempotency_key="same-key",
    )

    first = repository.calls[0]["request_fingerprint"]
    second = repository.calls[1]["request_fingerprint"]

    assert first != second
