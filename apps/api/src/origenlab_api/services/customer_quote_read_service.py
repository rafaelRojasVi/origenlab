"""Read service for durable customer quotes (CRM-Q1)."""

from __future__ import annotations

from origenlab_api.repositories.postgres.customer_quotes import (
    CustomerQuoteBundle,
    CustomerQuoteEvent,
)
from origenlab_api.repositories.postgres.customer_quotes_read import (
    CustomerQuoteGlobalEntry,
    PostgresCustomerQuoteReadRepository,
)
from origenlab_api.settings import Settings


def _identifier(value: str, *, field: str) -> str:
    normalized = value.strip()

    if not normalized:
        raise ValueError(f"{field} must not be blank")

    if len(normalized) > 128:
        raise ValueError(f"{field} exceeds maximum length 128")

    return normalized


class CustomerQuoteReadService:
    def __init__(
        self,
        settings: Settings,
        *,
        repository: PostgresCustomerQuoteReadRepository | None = None,
    ) -> None:
        self._repository = repository or PostgresCustomerQuoteReadRepository(
            settings
        )

    def list_quotes_for_sales_opportunity(
        self,
        sales_opportunity_id: str,
        *,
        limit: int = 100,
    ) -> list[CustomerQuoteBundle]:
        if not (1 <= limit <= 200):
            raise ValueError("limit must be between 1 and 200")

        return self._repository.list_for_sales_opportunity(
            _identifier(sales_opportunity_id, field="sales_opportunity_id"),
            limit=limit,
        )

    def get_quote(self, quote_id: str) -> CustomerQuoteBundle | None:
        return self._repository.get(
            _identifier(quote_id, field="quote_id"),
        )

    def list_all_quotes(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        drive_status: list[str] | None = None,
        stage: list[str] | None = None,
    ) -> tuple[list[CustomerQuoteGlobalEntry], int]:
        if not (1 <= limit <= 200):
            raise ValueError("limit must be between 1 and 200")
        if offset < 0:
            raise ValueError("offset must be >= 0")

        return self._repository.list_all(
            limit=limit,
            offset=offset,
            drive_status=drive_status,
            stage=stage,
        )

    def list_events(self, quote_id: str) -> list[CustomerQuoteEvent]:
        return self._repository.list_events(
            _identifier(quote_id, field="quote_id"),
        )
