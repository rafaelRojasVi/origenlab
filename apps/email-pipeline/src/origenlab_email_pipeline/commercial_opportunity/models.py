"""Typed records for the commercial opportunity stage resolver (PR3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SourceDealRow:
    deal_id: int
    deal_key: str
    deal_status: str
    client_org_name: str
    client_domain: str | None
    client_contact_email: str | None
    supplier_org_name: str | None
    supplier_domain: str | None
    confidence: str
    created_at: str | None
    updated_at: str | None


@dataclass(frozen=True)
class SourceDealEventRow:
    event_id: int
    deal_id: int
    deal_key: str
    event_type: str
    event_at: str | None
    confidence: str
    operator_confirmed: bool
    source_email_id: int | None
    source_attachment_id: int | None
    summary: str | None = None


@dataclass(frozen=True)
class SourceDealDocumentRow:
    document_id: int
    deal_id: int
    deal_key: str
    document_type: str
    issued_at: str | None
    confidence: str
    source_email_id: int | None
    source_attachment_id: int | None


@dataclass(frozen=True)
class SourceDealPaymentRow:
    payment_id: int
    deal_id: int
    deal_key: str
    direction: str
    paid_at: str | None
    confidence: str


@dataclass(frozen=True)
class SourceSignalRow:
    """Production opportunity_signals row (entity_kind/entity_key contract)."""

    signal_id: str
    signal_type: str | None
    entity_kind: str  # contact | organization
    entity_key: str
    created_at: str | None = None  # mart build stamp — never stage evidence
    email_id: int | None = None
    attachment_id: int | None = None
    email_date: str | None = None  # recovered business event time when available
    score: float | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def contact_email(self) -> str | None:
        if (self.entity_kind or "").strip().lower() != "contact":
            return None
        return (self.entity_key or "").strip().lower() or None

    @property
    def organization_key(self) -> str | None:
        if (self.entity_kind or "").strip().lower() != "organization":
            return None
        return (self.entity_key or "").strip().lower() or None


@dataclass(frozen=True)
class SourceContactMasterRow:
    """Production contact_master commercial-history fields."""

    contact_id: str
    email: str | None
    organization_name: str | None
    quote_email_count: int = 0
    invoice_email_count: int = 0
    purchase_email_count: int = 0
    business_doc_email_count: int = 0
    quote_doc_count: int = 0
    invoice_doc_count: int = 0
    total_emails: int = 0
    inbound_emails: int = 0
    outbound_emails: int = 0
    first_seen_at: str | None = None
    last_seen_at: str | None = None

    @property
    def has_typed_commercial_evidence(self) -> bool:
        return (
            self.quote_email_count
            + self.invoice_email_count
            + self.purchase_email_count
            + self.business_doc_email_count
            + self.quote_doc_count
            + self.invoice_doc_count
        ) > 0


@dataclass
class StageCandidate:
    canonical_stage: str
    source_stage: str
    event_at: str | None
    confidence: str
    operator_confirmed: bool
    is_terminal: bool
    is_hard_terminal: bool
    client_side: bool
    source_table: str
    source_record_id: str
    evidence_id: str
    precedence_tier: int
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class OpportunityRecord:
    opportunity_id: str
    record_kind: str
    account_id: str | None
    primary_contact_id: str | None
    source_kind: str
    source_key: str
    commercial_deal_id: int | None
    deal_key: str | None
    canonical_stage: str
    source_stage: str
    stage_reason_code: str
    stage_confidence: str
    stage_is_current: bool
    stage_is_terminal: bool
    stage_evidence_at: str | None
    stage_evidence_id: str | None
    first_activity_at: str | None
    last_activity_at: str | None
    identity_link_status: str
    review_status: str


@dataclass
class OpportunityEventRecord:
    event_id: str
    opportunity_id: str
    canonical_event_type: str
    source_event_type: str
    event_at: str | None
    source_table: str
    source_record_id: str
    source_email_id: int | None
    source_attachment_id: int | None
    confidence: str
    operator_confirmed: bool
    detail_json: str | None = None


@dataclass
class OpportunityEvidenceRecord:
    evidence_id: str
    opportunity_id: str
    subject_kind: str
    source_table: str
    source_record_id: str
    evidence_type: str
    evidence_at: str | None
    confidence: str
    reason_code: str
    source_email_id: int | None = None
    source_attachment_id: int | None = None
    detail_json: str | None = None


@dataclass
class OpportunityConflictRecord:
    conflict_id: str
    opportunity_id: str | None
    conflict_type: str
    reason_code: str
    subject_keys_json: str
    evidence_pointers_json: str
    review_status: str
    detail_json: str | None = None


@dataclass
class OpportunityResolution:
    opportunities: list[OpportunityRecord]
    events: list[OpportunityEventRecord]
    evidence: list[OpportunityEvidenceRecord]
    conflicts: list[OpportunityConflictRecord]
    metrics: dict[str, Any]
