"""Pydantic response contracts for the operator cockpit API (`/v2/cockpit/*`).

All field names are English snake_case.  Docstrings name the source table(s)
so the dashboard team can trace every field without grepping migrations.

This module is the contract.  The repository and routes must not diverge from
it; field renames are migrations here first.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ──────────────────────────────────────────── KPIs ──────────────────────────


class QuotationKpis(BaseModel):
    """Counters derived from ``crm.quote`` and ``crm.quote_revision``."""

    sent_historical_revisions: int = Field(
        description=(
            "crm.quote_revision rows where origin='historical_import', "
            "status='sent', superseded_by_revision_no IS NULL."
        )
    )
    void_historical_revisions: int = Field(
        description=(
            "crm.quote_revision rows where origin='historical_import', "
            "status='void'."
        )
    )
    quotes_with_undetermined_canonical: int = Field(
        description=(
            "crm.quote rows that have more than one revision where "
            "status NOT IN ('void') AND superseded_by_revision_no IS NULL — "
            "the canonical revision cannot be determined."
        )
    )
    shared_printed_numbers: int = Field(
        description=(
            "Distinct quote_number values (number_origin='printed_historical') "
            "that appear on more than one opportunity_id."
        )
    )


class EvidenceKpis(BaseModel):
    """Counters derived from ``evidence.source_record`` and ``evidence.assertion``."""

    pending_source_records: int = Field(
        description=(
            "evidence.source_record rows where review_status='pending' "
            "and is_quarantined=false."
        )
    )
    unresolved_document_references: int = Field(
        description=(
            "evidence.assertion rows where kind='document_reference' "
            "and resolution='unresolved'."
        )
    )
    revisions_missing_thread_id: int = Field(
        description=(
            "crm.quote_revision rows (origin='historical_import') "
            "whose linked evidence.source_record payload lacks "
            "a non-null gmail_thread_id."
        )
    )
    revisions_missing_document_entry: int = Field(
        description=(
            "crm.quote_revision rows (origin='historical_import') "
            "whose linked evidence.source_record payload has an empty "
            "or absent 'documents' array."
        )
    )


class OpportunityKpis(BaseModel):
    """Counters derived from ``crm.opportunity`` and ``crm.opportunity_organization``."""

    by_stage: dict[str, int] = Field(
        description=(
            "Count of crm.opportunity rows grouped by stage.  "
            "Stages with zero rows are still present (value 0)."
        )
    )
    leads_without_institution: int = Field(
        description=(
            "crm.opportunity rows at stage='lead' where organization_id IS NULL "
            "and no current crm.opportunity_organization row with "
            "role='requesting_institution' exists."
        )
    )


class KpiResponse(BaseModel):
    """All cockpit KPIs in one round-trip.  Source: multiple ``crm.*`` and ``evidence.*`` tables."""

    quotations: QuotationKpis
    evidence: EvidenceKpis
    opportunities: OpportunityKpis


# ─────────────────────────────────────── Work queue ─────────────────────────

WORK_ITEM_KINDS = frozenset(
    {
        "pending_evidence",
        "unresolved_document",
        "canonical_undetermined",
        "shared_printed_number",
        "case_without_institution",
        "case_without_quote",
    }
)


class WorkQueueItem(BaseModel):
    """One actionable item the operator needs to decide.

    Derived entirely from DB state; never synthetic.

    Source tables: ``evidence.source_record``, ``evidence.assertion``,
    ``crm.quote``, ``crm.quote_revision``, ``crm.opportunity``,
    ``crm.opportunity_organization``.
    """

    kind: str = Field(
        description=(
            "One of: pending_evidence, unresolved_document, "
            "canonical_undetermined, shared_printed_number, "
            "case_without_institution, case_without_quote."
        )
    )
    reason: str = Field(description="Human-readable explanation of what needs attention.")
    next_action: str = Field(description="Short imperative describing the operator's next step.")
    subject_ids: dict[str, str] = Field(
        description=(
            "Named UUID references for this item — keys depend on kind.  "
            "E.g. {'source_record_id': '<uuid>'} for pending_evidence, "
            "{'quote_id': '<uuid>', 'opportunity_id': '<uuid>'} for canonical_undetermined."
        )
    )
    age_days: int | None = Field(
        default=None,
        description=(
            "How many whole days have passed since the item was created "
            "(based on created_at of the primary subject row).  "
            "NULL when the creation timestamp is unavailable."
        ),
    )
    label: str | None = Field(
        default=None,
        description=(
            "Short human label for the subject — quote number, opportunity title, "
            "assertion value_norm — to display without a separate lookup."
        ),
    )


class WorkQueueResponse(BaseModel):
    """Paginated work queue.  Items are ordered: oldest first within each kind."""

    items: list[WorkQueueItem]
    total: int
    limit: int
    offset: int


# ────────────────────────────────────── Opportunities ───────────────────────


class OpportunitySummary(BaseModel):
    """One row in ``GET /v2/cockpit/opportunities``.

    Source: ``crm.opportunity`` joined to ``crm.organization`` and aggregate
    counts from ``crm.quote``, ``crm.quote_revision``,
    ``crm.opportunity_organization``, ``crm.opportunity_interest``.
    """

    opportunity_id: str
    title: str
    stage: str
    organization_id: str | None = Field(
        description="crm.opportunity.organization_id — the confirmed requesting institution, or null."
    )
    organization_name: str | None = Field(
        description="crm.organization.name for the requesting institution."
    )
    quote_count: int = Field(description="Number of crm.quote rows for this opportunity.")
    sent_revision_count: int = Field(
        description="Number of crm.quote_revision rows with status='sent' and not superseded."
    )
    interest_count: int = Field(
        description="Number of non-withdrawn crm.opportunity_interest rows."
    )
    has_requesting_institution: bool = Field(
        description=(
            "True when organization_id IS NOT NULL or a current "
            "opportunity_organization row with role='requesting_institution' exists."
        )
    )
    closed_at: str | None = Field(
        description="ISO-8601 timestamp from crm.opportunity.closed_at, or null."
    )
    close_reason: str | None = Field(description="crm.opportunity.close_reason, or null.")
    created_at: str
    updated_at: str


class OpportunityListResponse(BaseModel):
    """Paginated opportunity list."""

    items: list[OpportunitySummary]
    total: int
    limit: int
    offset: int


class RevisionSummary(BaseModel):
    """One revision row inside an opportunity or quotation card.

    Source: ``crm.quote_revision``.
    """

    revision_id: str
    revision_no: int
    status: str
    origin: str = Field(description="'authored' or 'historical_import'.")
    pdf_sha256: str | None
    sent_at: str | None
    grand_total: str | None = Field(
        description="crm.quote_revision.grand_total cast to text, or null."
    )
    quote_currency: str | None
    superseded_by_revision_no: int | None
    is_canonical: bool = Field(
        description=(
            "True when this is the sole non-void, non-superseded revision "
            "on the quote.  False if multiple such revisions exist "
            "(canonical_undetermined) or if it is superseded/void."
        )
    )
    origin_source_record_id: str | None = Field(
        description="evidence.source_record.id for historical_import revisions, else null."
    )


class QuoteSummary(BaseModel):
    """One quote with its revisions inside an opportunity card.

    Source: ``crm.quote`` + ``crm.quote_revision``.
    """

    quote_id: str
    quote_number: str
    number_origin: str = Field(description="'minted' or 'printed_historical'.")
    revisions: list[RevisionSummary]
    has_conflict: bool = Field(
        description="True when more than one active (non-void, non-superseded) revision exists."
    )


class EvidenceLinkSummary(BaseModel):
    """One ``crm.opportunity_evidence`` row.

    Source: ``crm.opportunity_evidence``.
    """

    evidence_id: str
    relation: str
    source_record_id: str | None
    assertion_id: str | None
    linked_at: str
    unlinked_at: str | None


class OpportunityDetail(BaseModel):
    """Full card for one opportunity.

    Source: ``crm.opportunity``, ``crm.organization``,
    ``crm.opportunity_organization``, ``crm.quote``,
    ``crm.quote_revision``, ``crm.opportunity_evidence``.
    """

    opportunity_id: str
    title: str
    stage: str
    organization_id: str | None
    organization_name: str | None
    close_reason: str | None
    closed_at: str | None
    origin_source_record_id: str | None = Field(
        description="evidence.source_record.id from which this opportunity was opened."
    )
    created_at: str
    updated_at: str
    quotes: list[QuoteSummary]
    case_organizations: list[dict[str, Any]] = Field(
        description=(
            "Current crm.opportunity_organization rows (valid_to IS NULL), "
            "each with: id, organization_id, organization_name, role, confirmation."
        )
    )
    evidence_links: list[EvidenceLinkSummary]
    evidence_completeness: dict[str, bool] = Field(
        description=(
            "Flags: has_gmail_source (any linked source_record of kind='gmail_message'), "
            "all_revisions_have_pdf (every sent revision has a non-null pdf_sha256)."
        )
    )


# ────────────────────────────────────────── Quotations ──────────────────────


class QuotationSummary(BaseModel):
    """One row in ``GET /v2/cockpit/quotations``.

    Source: ``crm.quote``, ``crm.quote_revision``, ``crm.opportunity``,
    ``crm.organization``, ``evidence.source_record``.
    """

    quote_id: str
    quote_number: str
    number_origin: str
    opportunity_id: str
    opportunity_title: str
    organization_id: str | None
    organization_name: str | None
    sent_revision_count: int = Field(
        description="Non-superseded, non-void revisions with status='sent'."
    )
    latest_sent_at: str | None
    latest_pdf_sha256: str | None
    has_conflict: bool
    created_at: str


class QuotationListResponse(BaseModel):
    """Paginated quotation list."""

    items: list[QuotationSummary]
    total: int
    limit: int
    offset: int


class ConflictDetail(BaseModel):
    """What kind of conflict this quote has.

    Source: ``crm.quote``, ``crm.quote_revision``.
    """

    kind: str = Field(description="'shared_printed_number' or 'canonical_undetermined'.")
    description: str
    related_opportunity_ids: list[str] = Field(
        default_factory=list,
        description="Other opportunity_ids that share the same printed number.",
    )


class GmailSourceSummary(BaseModel):
    """Gmail identifiers extracted from ``evidence.source_record.payload``.

    Source: ``evidence.source_record`` (kind='gmail_message').
    """

    source_record_id: str
    gmail_message_id: str | None
    gmail_thread_id: str | None
    sender: str | None
    subject_raw: str | None
    sent_at: str | None


class QuotationDetail(BaseModel):
    """Full card for one quote.

    Source: ``crm.quote``, ``crm.quote_revision``, ``crm.opportunity``,
    ``crm.organization``, ``evidence.source_record``, ``evidence.assertion``.
    """

    quote_id: str
    quote_number: str
    number_origin: str
    opportunity_id: str
    opportunity_title: str
    organization_id: str | None
    organization_name: str | None
    revisions: list[RevisionSummary]
    canonical_revision: RevisionSummary | None = Field(
        description="The single canonical revision, or null if undetermined or none."
    )
    gmail_sources: list[GmailSourceSummary] = Field(
        description=(
            "Gmail source records linked to any revision via "
            "crm.quote_revision.origin_source_record_id."
        )
    )
    conflicts: list[ConflictDetail]
    created_at: str


# ──────────────────────────────────────────── Timeline ──────────────────────

TIMELINE_ENTRY_KINDS = frozenset(
    {"domain_event", "quotation_sent"}
)


class TimelineEntry(BaseModel):
    """One ordered entry in an opportunity's timeline.

    Source: ``crm.domain_event``, plus ``crm.quote_revision.sent_at``
    for 'quotation_sent' entries.
    """

    entry_kind: str = Field(
        description="'domain_event' or 'quotation_sent'."
    )
    occurred_at: str = Field(
        description=(
            "ISO-8601 timestamp.  For domain_event: recorded_at.  "
            "For quotation_sent: crm.quote_revision.sent_at."
        )
    )
    event_type: str | None = Field(
        default=None,
        description="crm.domain_event.event_type, or null for synthetic entries.",
    )
    aggregate_kind: str | None = Field(
        default=None,
        description="crm.domain_event.aggregate_kind.",
    )
    aggregate_id: str | None = Field(
        default=None,
        description="crm.domain_event.aggregate_id as text.",
    )
    actor_operator_id: str | None = None
    payload: dict[str, Any] | None = Field(
        default=None,
        description="crm.domain_event.payload, or null for synthetic entries.",
    )
    # Quotation-sent extras (when entry_kind = 'quotation_sent')
    quote_id: str | None = None
    quote_number: str | None = None
    revision_no: int | None = None
    pdf_sha256: str | None = None


class TimelineResponse(BaseModel):
    """Ordered timeline for one opportunity."""

    opportunity_id: str
    entries: list[TimelineEntry]


# ──────────────────────────────────────── Evidence drawer ───────────────────


class DocumentEntry(BaseModel):
    """One document from ``evidence.source_record.payload['documents']``."""

    sha256: str | None
    filename: str | None
    bytes: int | None = None


class AssertionEntry(BaseModel):
    """One ``evidence.assertion`` row with its resolution state."""

    assertion_id: str
    kind: str
    value_norm: str
    resolution: str
    resolved_kind: str | None
    resolved_id: str | None
    resolved_at: str | None
    ambiguity_note: str | None


class EvidenceDrawer(BaseModel):
    """Full evidence drawer for one source record.

    Never includes the raw email body.

    Source: ``evidence.source_record``, ``evidence.assertion``,
    ``crm.domain_event`` (for audit events on the assertions/record).
    """

    source_record_id: str
    kind: str = Field(description="evidence.source_record.kind, e.g. 'gmail_message'.")
    review_status: str
    is_quarantined: bool
    acquired_at: str
    # Gmail-specific fields extracted from payload (kind='gmail_message'):
    gmail_message_id: str | None = Field(
        description="payload['gmail_message_id'], or null for non-Gmail records."
    )
    gmail_thread_id: str | None = Field(
        description="payload['gmail_thread_id'], or null."
    )
    sender: str | None = Field(
        description="payload['sender'] display string, or null."
    )
    recipients: str | None = Field(
        description="payload['recipients'] display string, or null.  Never the body."
    )
    subject_raw: str | None = Field(
        description="payload['subject_raw'], or null."
    )
    sent_at: str | None = Field(
        description="payload['sent_at'], or null."
    )
    documents: list[DocumentEntry] = Field(
        description="payload['documents'] entries with sha256, filename and bytes."
    )
    assertions: list[AssertionEntry]
    decision_events: list[dict[str, Any]] = Field(
        description=(
            "crm.domain_event rows where aggregate_kind='source_record' "
            "and aggregate_id=this record's id, ordered by seq."
        )
    )


# ──────────────────────────────────────────── Search ────────────────────────


class SearchHit(BaseModel):
    """One match from ``GET /v2/cockpit/search``.

    Source tables vary by match_kind.
    """

    match_kind: str = Field(
        description=(
            "One of: quote_number, gmail_message_id, gmail_thread_id, "
            "sha256_prefix, organization_name, opportunity_title."
        )
    )
    subject_kind: str = Field(
        description="The CRM entity kind: 'quote', 'source_record', 'organization', 'opportunity'."
    )
    subject_id: str
    label: str = Field(description="Short display label for the matched row.")
    opportunity_id: str | None = Field(
        default=None,
        description="Set when the match is a quote or revision linked to one opportunity.",
    )


class SearchResponse(BaseModel):
    """Results from the global cockpit search."""

    query: str
    hits: list[SearchHit]
    truncated: bool = Field(
        description="True when the result set was capped (>= MAX_SEARCH_HITS)."
    )
