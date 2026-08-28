"""Service tests for durable sales-opportunity list validation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from origenlab_api.repositories.postgres.commercial_operations_read import (
    SalesOpportunityBoardItem,
)
from origenlab_api.services.commercial_operations_read_service import (
    CommercialOperationsReadService,
)
from origenlab_api.settings import Settings


NOW = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


class FakeRepository:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def list_sales_opportunities(self, **kwargs: Any) -> tuple[list[SalesOpportunityBoardItem], int]:
        self.calls.append(kwargs)
        return [], 0


def _service(repository: FakeRepository) -> CommercialOperationsReadService:
    return CommercialOperationsReadService(Settings(), repository=repository)  # type: ignore[arg-type]


def test_list_normalizes_stage_case_and_whitespace() -> None:
    repository = FakeRepository()
    service = _service(repository)

    service.list_sales_opportunities(stages=["  QUALIFYING  ", "won"])

    assert repository.calls[0]["stages"] == ["qualifying", "won"]


def test_list_rejects_unknown_stage() -> None:
    repository = FakeRepository()
    service = _service(repository)

    with pytest.raises(ValueError, match="Unsupported sales opportunity stage"):
        service.list_sales_opportunities(stages=["invented"])

    assert repository.calls == []


@pytest.mark.parametrize("limit", [0, 201])
def test_list_rejects_out_of_range_limit(limit: int) -> None:
    repository = FakeRepository()
    service = _service(repository)

    with pytest.raises(ValueError, match="limit"):
        service.list_sales_opportunities(limit=limit)

    assert repository.calls == []


def test_list_rejects_negative_offset() -> None:
    repository = FakeRepository()
    service = _service(repository)

    with pytest.raises(ValueError, match="offset"):
        service.list_sales_opportunities(offset=-1)

    assert repository.calls == []


def test_list_rejects_blank_owner_key() -> None:
    repository = FakeRepository()
    service = _service(repository)

    with pytest.raises(ValueError, match="owner_key"):
        service.list_sales_opportunities(owner_key="   ")

    assert repository.calls == []


def test_list_caps_source_opportunity_id_count() -> None:
    repository = FakeRepository()
    service = _service(repository)

    with pytest.raises(ValueError, match="source_opportunity_id"):
        service.list_sales_opportunities(source_opportunity_ids=[f"o_{i}" for i in range(201)])

    assert repository.calls == []


def test_list_passes_through_defaults() -> None:
    repository = FakeRepository()
    service = _service(repository)

    service.list_sales_opportunities()

    call = repository.calls[0]
    assert call["stages"] is None
    assert call["owner_key"] is None
    assert call["source_opportunity_ids"] is None
    assert call["limit"] == 100
    assert call["offset"] == 0
