"""Read service for durable operator-owned CRM state (ARCH-3B6)."""

from __future__ import annotations

from origenlab_api.repositories.postgres.commercial_operations import (
    SALES_OPPORTUNITY_STAGES,
    Activity,
    OperatorState,
    SalesOpportunity,
    Task,
)
from origenlab_api.repositories.postgres.commercial_operations_read import (
    CommercialWorkQueueOpportunity,
    CommercialWorkQueueTask,
    PostgresCommercialOperationsReadRepository,
    SalesOpportunityBoardItem,
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

    def list_sales_opportunity_activities(
        self,
        sales_opportunity_id: str,
        *,
        limit: int = 100,
    ) -> list[Activity]:
        return self._repository.list_activities_for_sales_opportunity(
            _sales_opportunity_id(sales_opportunity_id),
            limit=limit,
        )

    def list_sales_opportunity_tasks(
        self,
        sales_opportunity_id: str,
        *,
        limit: int = 100,
    ) -> list[Task]:
        return self._repository.list_tasks_for_sales_opportunity(
            _sales_opportunity_id(sales_opportunity_id),
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

    def list_sales_opportunities(
        self,
        *,
        stages: list[str] | None = None,
        owner_key: str | None = None,
        source_opportunity_ids: list[str] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[SalesOpportunityBoardItem], int]:
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")

        if offset < 0:
            raise ValueError("offset must be >= 0")

        normalized_stages: list[str] | None = None

        if stages is not None:
            normalized_stages = []

            for value in stages:
                stage = value.strip().lower()

                if stage not in SALES_OPPORTUNITY_STAGES:
                    raise ValueError(f"Unsupported sales opportunity stage: {value!r}")

                normalized_stages.append(stage)

        normalized_owner: str | None = None

        if owner_key is not None:
            normalized_owner = owner_key.strip()

            if not normalized_owner:
                raise ValueError("owner_key must not be blank when provided")

        normalized_source_ids: list[str] | None = None

        if source_opportunity_ids is not None:
            if len(source_opportunity_ids) > 200:
                raise ValueError(
                    "source_opportunity_id supports at most 200 values per request"
                )

            normalized_source_ids = [
                _opportunity_id(value) for value in source_opportunity_ids
            ]

        return self._repository.list_sales_opportunities(
            stages=normalized_stages,
            owner_key=normalized_owner,
            source_opportunity_ids=normalized_source_ids,
            limit=limit,
            offset=offset,
        )
