"""Read service for durable customer quotes (CRM-Q1)."""

from __future__ import annotations

from origenlab_api.repositories.postgres.customer_quotes import (
    CustomerQuoteBundle,
)
from origenlab_api.repositories.postgres.customer_quotes_read import (
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
