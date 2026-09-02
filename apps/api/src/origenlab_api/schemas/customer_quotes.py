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
    CustomerQuoteEvent,
)
from origenlab_api.repositories.postgres.customer_quotes_read import (
    CustomerQuoteGlobalEntry,
)
from origenlab_api.schemas.commercial_operations import (
    CommercialCommandModel,
    SalesOpportunityStage,
)


QuoteStatus = Literal["draft"]

QuoteOrigin = Literal["generated", "adopted"]

RevisionStatus = Literal[
    "draft",
    "pending_approval",
    "adjustments_requested",
    "approved",
    "sent",
    "superseded",
]

# The Cotizaciones Kanban lane, derived from the current revision's status --
# never a stored column (see commercial.customer_quote_revision.status,
# CRM-Q2). "drive_intake" is not produced here: it is the dashboard's label
# for items sourced from the separate drive-pending endpoint, which never
# have a durable revision at all. 'superseded' has no lane of its own: it is
# reserved for a future multi-revision slice and is never the *current*
# (latest) revision of a quote, which is the only revision this derives
# from.
BoardStage = Literal[
    "review",
    "approved_to_send",
    "sent_follow_up",
]

_BOARD_STAGE_BY_REVISION_STATUS: dict[str, BoardStage] = {
    "draft": "review",
    "adjustments_requested": "review",
    "pending_approval": "review",
    "approved": "approved_to_send",
    "sent": "sent_follow_up",
}


def derive_board_stage(revision_status: str) -> BoardStage:
    try:
        return _BOARD_STAGE_BY_REVISION_STATUS[revision_status]
    except KeyError:
        raise ValueError(
            f"No board stage for current revision status: {revision_status!r}"
        ) from None


QuoteProvisioningStatus = Literal[
    "pending",
    "ready",
    "folder_ready",
    "failed",
]


class CustomerQuoteCreateCommand(CommercialCommandModel):
    """Deliberately empty: every field of a new quote is server-controlled.

    The body must still be sent (``{}``) so ``extra="forbid"`` rejects any
    browser-invented field such as a quote number.
    """


class CustomerQuoteDriveWorkspaceRetryCommand(CommercialCommandModel):
    expected_version: int = Field(ge=1)


class CustomerQuoteRevisionTransitionCommand(CommercialCommandModel):
    """Shared body shape for submit-for-review / request-adjustments /
    approve / confirm-send: each is its own endpoint with its own fixed
    legal-from-status set enforced by the repository -- this command only
    ever carries the optimistic-concurrency token, never a caller-chosen
    target status."""

    expected_version: int = Field(ge=1)


class CustomerQuoteAdoptDriveFolderCommand(CommercialCommandModel):
    """"Incorporar al CRM": attach an existing Drive-only folder to a new
    durable quote. document_number/quote_number are independent,
    operator-confirmed inputs -- neither is ever derived from the other,
    and no field here can allocate a customer_quote_number_series serial.
    """

    document_number: str = Field(min_length=1, max_length=32)
    quote_number: str = Field(min_length=1, max_length=32)
    folder_id: str = Field(min_length=1, max_length=256)
    folder_web_url: str = Field(min_length=1, max_length=2048)


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
    retryable: bool = False

    requested_at: datetime | None = None
    completed_at: datetime | None = None


class CustomerQuoteResponse(BaseModel):
    quote_id: str
    sales_opportunity_id: str
    quote_number: str
    document_number: str
    quote_origin: QuoteOrigin
    sales_opportunity_title: str
    status: QuoteStatus

    version: int
    latest_revision_number: int
    revision_status: RevisionStatus
    revision_updated_by: str
    revision_updated_at: datetime
    board_stage: BoardStage

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

        # Retry is meaningful only while provisioning still needs work:
        # failed workspaces can retry immediately; pending workspaces can
        # retry only when no active lease remains. A ready workspace is
        # terminal for provisioning and must never advertise retryability.
        retryable = (
            workspace.provisioning_status == "failed"
            or (
                workspace.provisioning_status == "pending"
                and (
                    lease_expires_at is None
                    or lease_expires_at <= now
                )
            )
        )

        return cls(
            quote_id=bundle.quote.quote_id,
            sales_opportunity_id=bundle.quote.sales_opportunity_id,
            quote_number=bundle.quote.quote_number,
            document_number=bundle.quote.document_number,
            quote_origin=bundle.quote.quote_origin,  # type: ignore[arg-type]
            sales_opportunity_title=bundle.sales_opportunity_title,
            status=bundle.quote.status,  # type: ignore[arg-type]
            version=bundle.quote.version,
            latest_revision_number=bundle.revision.revision_number,
            revision_status=bundle.revision.status,  # type: ignore[arg-type]
            revision_updated_by=bundle.revision.updated_by,
            revision_updated_at=bundle.revision.updated_at,
            board_stage=derive_board_stage(bundle.revision.status),
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


class CustomerQuoteGlobalItem(BaseModel):
    quote: CustomerQuoteResponse
    sales_opportunity_stage: SalesOpportunityStage
    sales_opportunity_owner_key: str
    organization_display_name: str | None = None
    contact_display_name: str | None = None
    contact_primary_email: str | None = None
    next_task_title: str | None = None
    next_task_due_at: datetime | None = None

    @classmethod
    def from_entry(cls, entry: CustomerQuoteGlobalEntry) -> "CustomerQuoteGlobalItem":
        return cls(
            quote=CustomerQuoteResponse.from_bundle(entry.bundle),
            sales_opportunity_stage=entry.sales_opportunity_stage,  # type: ignore[arg-type]
            sales_opportunity_owner_key=entry.sales_opportunity_owner_key,
            organization_display_name=entry.organization_display_name,
            contact_display_name=entry.contact_display_name,
            contact_primary_email=entry.contact_primary_email,
            next_task_title=entry.next_task_title,
            next_task_due_at=entry.next_task_due_at,
        )


class CustomerQuoteGlobalListMeta(BaseModel):
    count: int
    total_count: int
    limit: int = 100
    offset: int = 0


class CustomerQuoteGlobalListResponse(BaseModel):
    meta: CustomerQuoteGlobalListMeta
    items: list[CustomerQuoteGlobalItem]


class CustomerQuoteEventResponse(BaseModel):
    event_id: str
    event_type: str
    actor_key: str
    payload: dict[str, object]
    created_at: datetime

    @classmethod
    def from_event(cls, event: CustomerQuoteEvent) -> "CustomerQuoteEventResponse":
        return cls(
            event_id=event.event_id,
            event_type=event.event_type,
            actor_key=event.actor_key,
            payload=event.payload,
            created_at=event.created_at,
        )


class CustomerQuoteEventListMeta(BaseModel):
    count: int


class CustomerQuoteEventListResponse(BaseModel):
    meta: CustomerQuoteEventListMeta
    items: list[CustomerQuoteEventResponse]
