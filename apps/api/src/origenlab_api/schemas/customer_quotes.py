"""Schemas for durable customer-quote commands and reads (CRM-Q1).

Command bodies fail closed on unknown fields: the browser never supplies
server-controlled values (quote numbers, Drive references, statuses,
operator identity).
"""

from __future__ import annotations

from datetime import datetime, timezone
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

    # Safe retryability exposure: while an attempt actively owns the
    # server-side lease, the dashboard must not offer an immediate retry
    # (it would only conflict). lease_expires_at is the raw retry-after
    # boundary; retryable is the pre-computed convenience flag.
    lease_expires_at: datetime | None = None
    retryable: bool = True

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
        workspace = bundle.workspace
        lease_expires_at = workspace.lease_expires_at
        now = datetime.now(timezone.utc)

        # Retryable unless an attempt is actively pending under an
        # unexpired lease -- the exact condition
        # begin_drive_provision_attempt itself enforces, so the dashboard
        # never offers a retry the server would just conflict.
        retryable = not (
            workspace.provisioning_status == "pending"
            and lease_expires_at is not None
            and lease_expires_at > now
        )

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
            drive_workspace=CustomerQuoteDriveWorkspaceResponse(
                provider=workspace.provider,  # type: ignore[arg-type]
                provisioning_status=workspace.provisioning_status,  # type: ignore[arg-type]
                folder_id=workspace.folder_id,
                folder_web_url=workspace.folder_web_url,
                sheet_file_id=workspace.sheet_file_id,
                sheet_web_url=workspace.sheet_web_url,
                failure_category=workspace.failure_category,
                attempt_count=workspace.attempt_count,
                version=workspace.version,
                lease_expires_at=lease_expires_at,
                retryable=retryable,
                requested_at=workspace.requested_at,
                completed_at=workspace.completed_at,
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
