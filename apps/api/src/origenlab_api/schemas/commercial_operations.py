"""Schemas for durable commercial operations commands (ARCH-3B4)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ConfirmationStatus = Literal[
    "confirmed",
    "rejected",
    "needs_review",
]

ActivityType = Literal[
    "call",
    "whatsapp",
    "meeting",
    "email",
    "note",
    "quote",
    "follow_up",
    "other",
]

TaskPriority = Literal[
    "low",
    "normal",
    "high",
    "urgent",
]

SalesOpportunityStage = Literal[
    "new",
    "qualifying",
    "qualified",
    "quoting",
    "negotiating",
    "won",
    "lost",
    "dormant",
]


class CommercialCommandModel(BaseModel):
    """Command bodies fail closed on unknown/browser-invented fields."""

    model_config = ConfigDict(extra="forbid")


class OpportunityStateCommand(CommercialCommandModel):
    confirmation_status: ConfirmationStatus
    manual_stage: str | None = Field(
        default=None,
        max_length=128,
    )
    owner_key: str | None = Field(
        default=None,
        max_length=320,
    )
    expected_version: int = Field(ge=0)


class ActivityCreateCommand(CommercialCommandModel):
    sales_opportunity_id: str | None = Field(
        default=None,
        max_length=128,
    )
    opportunity_id: str | None = Field(
        default=None,
        max_length=128,
    )
    account_id: str | None = Field(
        default=None,
        max_length=128,
    )
    contact_id: str | None = Field(
        default=None,
        max_length=128,
    )

    activity_type: ActivityType
    occurred_at: datetime

    summary: str = Field(
        min_length=1,
        max_length=500,
    )
    detail: str | None = Field(
        default=None,
        max_length=10_000,
    )


class TaskCreateCommand(CommercialCommandModel):
    sales_opportunity_id: str | None = Field(
        default=None,
        max_length=128,
    )
    opportunity_id: str | None = Field(
        default=None,
        max_length=128,
    )
    account_id: str | None = Field(
        default=None,
        max_length=128,
    )
    contact_id: str | None = Field(
        default=None,
        max_length=128,
    )

    title: str = Field(
        min_length=1,
        max_length=500,
    )

    priority: TaskPriority = "normal"

    due_at: datetime | None = None

    owner_key: str | None = Field(
        default=None,
        max_length=320,
    )


class TaskTransitionCommand(CommercialCommandModel):
    expected_version: int = Field(ge=1)


class SalesOpportunityPromoteCommand(CommercialCommandModel):
    source_opportunity_id: str = Field(
        min_length=1,
        max_length=128,
    )
    title: str = Field(
        min_length=1,
        max_length=500,
    )
    owner_key: str | None = Field(
        default=None,
        max_length=320,
    )


class SalesOpportunityManualCreateCommand(CommercialCommandModel):
    title: str = Field(
        min_length=1,
        max_length=500,
    )
    owner_key: str | None = Field(
        default=None,
        max_length=320,
    )
    organization_id: str | None = Field(
        default=None,
        max_length=128,
    )
    organization_display_name: str | None = Field(
        default=None,
        max_length=500,
    )
    contact_id: str | None = Field(
        default=None,
        max_length=128,
    )
    contact_display_name: str | None = Field(
        default=None,
        max_length=500,
    )
    contact_email: str | None = Field(
        default=None,
        max_length=320,
    )


class SalesOpportunityStageCommand(CommercialCommandModel):
    stage: SalesOpportunityStage
    expected_version: int = Field(ge=1)


class SalesOpportunityResponse(BaseModel):
    sales_opportunity_id: str

    source_kind: Literal["pr3", "manual"]
    source_opportunity_id: str

    account_id: str | None = None
    primary_contact_id: str | None = None

    title: str
    stage: SalesOpportunityStage

    owner_key: str

    version: int

    created_by: str
    updated_by: str

    created_at: datetime
    updated_at: datetime

    # CRM-4A durable canonical CRM links. Optional and appended; promotion
    # semantics and request schemas are unchanged.
    organization_id: str | None = None
    primary_crm_contact_id: str | None = None


class SalesOpportunityReadMeta(BaseModel):
    data_source: Literal["postgres"] = "postgres"
    read_only: Literal[True] = True


class SalesOpportunityReadResponse(BaseModel):
    meta: SalesOpportunityReadMeta = Field(default_factory=SalesOpportunityReadMeta)
    item: SalesOpportunityResponse


class SalesOpportunityListItem(BaseModel):
    sales_opportunity_id: str

    source_kind: Literal["pr3", "manual"]
    source_opportunity_id: str

    account_id: str | None = None
    primary_contact_id: str | None = None
    organization_id: str | None = None
    primary_crm_contact_id: str | None = None

    title: str
    stage: SalesOpportunityStage

    owner_key: str

    version: int

    created_by: str
    updated_by: str

    created_at: datetime
    updated_at: datetime
    stage_updated_at: datetime

    contact_display_email: str | None = None
    account_display_domain: str | None = None

    organization_display_name: str | None = None
    contact_display_name: str | None = None
    contact_primary_email: str | None = None

    open_task_count: int = 0
    next_task_id: str | None = None
    next_task_title: str | None = None
    next_task_due_at: datetime | None = None


class SalesOpportunitiesMeta(BaseModel):
    data_source: Literal["postgres"] = "postgres"
    read_only: Literal[True] = True
    count: int = 0
    total_count: int = 0
    limit: int = 100
    offset: int = 0


class SalesOpportunitiesResponse(BaseModel):
    meta: SalesOpportunitiesMeta = Field(default_factory=SalesOpportunitiesMeta)
    items: list[SalesOpportunityListItem] = Field(default_factory=list)


class OpportunityStateResponse(BaseModel):
    opportunity_id: str
    confirmation_status: ConfirmationStatus
    manual_stage: str | None = None
    owner_key: str | None = None

    version: int

    created_by: str
    updated_by: str

    created_at: datetime
    updated_at: datetime


class ActivityResponse(BaseModel):
    activity_id: str

    opportunity_id: str | None = None
    sales_opportunity_id: str | None = None
    account_id: str | None = None
    contact_id: str | None = None

    activity_type: ActivityType
    occurred_at: datetime

    summary: str
    detail: str | None = None

    created_by: str
    created_at: datetime


class TaskResponse(BaseModel):
    task_id: str

    opportunity_id: str | None = None
    sales_opportunity_id: str | None = None
    account_id: str | None = None
    contact_id: str | None = None

    title: str
    status: Literal["open", "done", "cancelled"]
    priority: TaskPriority

    due_at: datetime | None = None
    owner_key: str | None = None

    version: int

    created_by: str
    updated_by: str

    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class OpportunityStateReadResponse(BaseModel):
    state: OpportunityStateResponse | None = None


class ActivityListResponse(BaseModel):
    items: list[ActivityResponse] = Field(default_factory=list)


class TaskListResponse(BaseModel):
    items: list[TaskResponse] = Field(default_factory=list)


class CommercialWorkQueueTask(BaseModel):
    task: TaskResponse

    contact_display_email: str | None = None
    account_display_domain: str | None = None

    canonical_stage: str | None = None
    machine_review_status: str | None = None


class CommercialWorkQueueOpportunity(BaseModel):
    opportunity_id: str

    contact_display_email: str | None = None
    account_display_domain: str | None = None

    canonical_stage: str
    machine_review_status: str

    confirmation_status: ConfirmationStatus | None = None
    manual_stage: str | None = None
    owner_key: str | None = None
    operator_state_version: int | None = None


class CommercialWorkQueueResponse(BaseModel):
    open_tasks: list[CommercialWorkQueueTask] = Field(default_factory=list)

    review_opportunities: list[CommercialWorkQueueOpportunity] = Field(
        default_factory=list
    )

    quote_followups: list[CommercialWorkQueueOpportunity] = Field(default_factory=list)
