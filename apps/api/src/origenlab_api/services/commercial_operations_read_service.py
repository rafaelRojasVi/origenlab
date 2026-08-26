"""Read service for durable operator-owned CRM state (ARCH-3B6)."""

from __future__ import annotations

from origenlab_api.repositories.postgres.commercial_operations import (
    Activity,
    OperatorState,
    SalesOpportunity,
    Task,
)
from origenlab_api.repositories.postgres.commercial_operations_read import (
    CommercialWorkQueueOpportunity,
    CommercialWorkQueueTask,
    PostgresCommercialOperationsReadRepository,
)
from origenlab_api.settings import Settings


def _opportunity_id(value: str) -> str:
    normalized = value.strip()

    if not normalized:
        raise ValueError("opportunity_id must not be blank")

    if len(normalized) > 128:
        raise ValueError("opportunity_id exceeds maximum length 128")

    return normalized


def _sales_opportunity_id(value: str) -> str:
    normalized = value.strip()

    if not normalized:
        raise ValueError("sales_opportunity_id must not be blank")

    if len(normalized) > 128:
        raise ValueError("sales_opportunity_id exceeds maximum length 128")

    return normalized


class CommercialOperationsReadService:
    def __init__(
        self,
        settings: Settings,
        *,
        repository: PostgresCommercialOperationsReadRepository | None = None,
    ) -> None:
        self._repository = repository or PostgresCommercialOperationsReadRepository(
            settings
        )

    def get_sales_opportunity(
        self,
        sales_opportunity_id: str,
    ) -> SalesOpportunity | None:
        return self._repository.get_sales_opportunity(
            _sales_opportunity_id(sales_opportunity_id)
        )

    def get_operator_state(
        self,
        opportunity_id: str,
    ) -> OperatorState | None:
        return self._repository.get_operator_state(_opportunity_id(opportunity_id))

    def list_activities(
        self,
        opportunity_id: str,
        *,
        limit: int = 100,
    ) -> list[Activity]:
        return self._repository.list_activities_for_opportunity(
            _opportunity_id(opportunity_id),
            limit=limit,
        )

    def list_tasks(
        self,
        opportunity_id: str,
        *,
        limit: int = 100,
    ) -> list[Task]:
        return self._repository.list_tasks_for_opportunity(
            _opportunity_id(opportunity_id),
            limit=limit,
        )

    def get_work_queue(
        self,
        *,
        limit: int = 100,
    ) -> tuple[
        list[CommercialWorkQueueTask],
        list[CommercialWorkQueueOpportunity],
        list[CommercialWorkQueueOpportunity],
    ]:
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")

        return self._repository.get_work_queue(
            limit=limit,
        )
