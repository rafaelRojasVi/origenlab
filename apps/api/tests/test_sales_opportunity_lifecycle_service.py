"""CRM-2 service tests for sales-opportunity lifecycle commands."""

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


NOW = datetime(2026, 8, 26, 16, 0, tzinfo=timezone.utc)


class FakeRepository:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def transition_sales_opportunity_stage(
        self,
        **kwargs: Any,
    ) -> SalesOpportunity:
        self.calls.append(kwargs)

        return SalesOpportunity(
            sales_opportunity_id=kwargs["sales_opportunity_id"],
            source_kind="pr3",
            source_opportunity_id="o_1",
            account_id="a_1",
            primary_contact_id="c_1",
            title="Centrífuga",
            stage=kwargs["stage"],
            owner_key="tatiana@origenlab.cl",
            version=kwargs["expected_version"] + 1,
            created_by="tatiana@origenlab.cl",
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


def test_stage_transition_normalizes_command() -> None:
    repository = FakeRepository()
    service = _service(repository)

    result = service.transition_sales_opportunity_stage(
        sales_opportunity_id="  sales_1  ",
        stage="  QUALIFYING  ",
        operator="Tatiana@OrigenLab.CL",
        expected_version=1,
    )

    assert result.stage == "qualifying"
    assert result.version == 2

    assert repository.calls == [
        {
            "sales_opportunity_id": "sales_1",
            "stage": "qualifying",
            "operator": "tatiana@origenlab.cl",
            "expected_version": 1,
        }
    ]


@pytest.mark.parametrize(
    "stage",
    [
        "",
        " ",
        "invented",
    ],
)
def test_stage_transition_rejects_invalid_stage(
    stage: str,
) -> None:
    repository = FakeRepository()
    service = _service(repository)

    with pytest.raises(ValueError):
        service.transition_sales_opportunity_stage(
            sales_opportunity_id="sales_1",
            stage=stage,
            operator="tatiana@origenlab.cl",
            expected_version=1,
        )

    assert repository.calls == []


def test_stage_transition_requires_positive_expected_version() -> None:
    repository = FakeRepository()
    service = _service(repository)

    with pytest.raises(
        ValueError,
        match="expected_version must be >= 1",
    ):
        service.transition_sales_opportunity_stage(
            sales_opportunity_id="sales_1",
            stage="qualified",
            operator="tatiana@origenlab.cl",
            expected_version=0,
        )

    assert repository.calls == []
