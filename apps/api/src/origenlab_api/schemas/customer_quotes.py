"""Schemas for durable customer-quote commands and reads (CRM-Q1).

Command bodies fail closed on unknown fields: the browser never supplies
server-controlled values (quote numbers, Drive references, statuses,
operator identity).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from origenlab_api.repositories.postgres.customer_quotes import (
    CustomerQuoteBundle,
)
from origenlab_api.schemas.commercial_operations import CommercialCommandModel


QuoteStatus = Literal["draft"]

QuoteProvisioningStatus = Literal[
    "pending",
    "ready",
    "failed",
]


class CustomerQuoteCreateCommand(CommercialCommandModel):
    """Deliberately empty: every field of a new quote is server-controlled.

    The body must still be sent (``{}``) so ``extra="forbid"`` rejects any
    browser-invented field such as a quote number.
    """


class CustomerQuoteDriveWorkspaceRetryCommand(CommercialCommandModel):
    expected_version: int = Field(ge=1)


class CustomerQuoteDriveWorkspaceResponse(BaseModel):
    provider: Literal["google_drive"]
    provisioning_status: QuoteProvisioningStatus

    folder_id: str | None = None
    folder_web_url: str | None = None
    sheet_file_id: str | None = None
    sheet_web_url: str | None = None

    failure_category: str | None = None

    attempt_count: int
    version: int

    requested_at: datetime | None = None
    completed_at: datetime | None = None


class CustomerQuoteResponse(BaseModel):
    quote_id: str
    sales_opportunity_id: str
    quote_number: str
    status: QuoteStatus

    version: int
    latest_revision_number: int

    created_by: str
    updated_by: str
    created_at: datetime
    updated_at: datetime

    drive_workspace: CustomerQuoteDriveWorkspaceResponse

    @classmethod
    def from_bundle(cls, bundle: CustomerQuoteBundle) -> "CustomerQuoteResponse":
        return cls(
            quote_id=bundle.quote.quote_id,
            sales_opportunity_id=bundle.quote.sales_opportunity_id,
            quote_number=bundle.quote.quote_number,
            status=bundle.quote.status,  # type: ignore[arg-type]
            version=bundle.quote.version,
            latest_revision_number=bundle.revision.revision_number,
            created_by=bundle.quote.created_by,
            updated_by=bundle.quote.updated_by,
            created_at=bundle.quote.created_at,
            updated_at=bundle.quote.updated_at,
            drive_workspace=CustomerQuoteDriveWorkspaceResponse.model_validate(
                bundle.workspace,
                from_attributes=True,
            ),
        )


class CustomerQuoteReadMeta(BaseModel):
    data_source: Literal["postgres"] = "postgres"
    read_only: Literal[True] = True


class CustomerQuoteReadResponse(BaseModel):
    meta: CustomerQuoteReadMeta = Field(default_factory=CustomerQuoteReadMeta)
    item: CustomerQuoteResponse


class CustomerQuoteListMeta(BaseModel):
    count: int


class CustomerQuoteListResponse(BaseModel):
    meta: CustomerQuoteListMeta
    items: list[CustomerQuoteResponse]
