"""Commercial opportunity lifecycle API schemas (ARCH-2B)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


CommercialOpportunityDataSource = Literal["sqlite_pr3", "postgres_mirror"]


class CommercialOpportunitiesMeta(BaseModel):
    data_source: CommercialOpportunityDataSource
    read_only: bool = True
    count: int = 0
    total_count: int = 0
    limit: int = 50
    offset: int = 0
    reduced_mode: bool = False
    note: str = ""


class CommercialOpportunityDetailMeta(BaseModel):
    data_source: CommercialOpportunityDataSource
    read_only: bool = True


class CommercialOpportunityItem(BaseModel):
    opportunity_id: str
    record_kind: str

    account_id: str | None = None
    primary_contact_id: str | None = None

    contact_display_email: str | None = None
    account_display_domain: str | None = None

    source_kind: str
    source_key: str
    deal_key: str | None = None

    canonical_stage: str
    source_stage: str
    stage_reason_code: str
    stage_confidence: str
    stage_is_current: bool
    stage_is_terminal: bool

    stage_evidence_at: str | None = None
    stage_evidence_id: str | None = None
    first_activity_at: str | None = None
    last_activity_at: str | None = None

    identity_link_status: str
    review_status: str
    synced_at: str | None = None


class CommercialOpportunityEvent(BaseModel):
    event_id: str
    opportunity_id: str

    canonical_event_type: str
    source_event_type: str
    event_at: str | None = None

    source_table: str
    source_record_id: str
    source_email_id: int | None = None
    source_attachment_id: int | None = None

    confidence: str
    operator_confirmed: bool
    detail_json: Any | None = None
    synced_at: str | None = None


class CommercialOpportunityEvidence(BaseModel):
    evidence_id: str
    opportunity_id: str

    subject_kind: str
    source_table: str
    source_record_id: str
    evidence_type: str
    evidence_at: str | None = None

    confidence: str
    reason_code: str

    source_email_id: int | None = None
    source_attachment_id: int | None = None

    detail_json: Any | None = None
    synced_at: str | None = None


class CommercialOpportunityConflict(BaseModel):
    conflict_id: str
    opportunity_id: str | None = None

    conflict_type: str
    reason_code: str

    subject_keys_json: Any
    evidence_pointers_json: Any

    review_status: str
    detail_json: Any | None = None
    synced_at: str | None = None


class CommercialOpportunitiesResponse(BaseModel):
    meta: CommercialOpportunitiesMeta
    items: list[CommercialOpportunityItem] = Field(default_factory=list)


class CommercialOpportunityDetailResponse(BaseModel):
    meta: CommercialOpportunityDetailMeta
    opportunity: CommercialOpportunityItem
    events: list[CommercialOpportunityEvent] = Field(default_factory=list)
    evidence: list[CommercialOpportunityEvidence] = Field(default_factory=list)
    conflicts: list[CommercialOpportunityConflict] = Field(default_factory=list)
