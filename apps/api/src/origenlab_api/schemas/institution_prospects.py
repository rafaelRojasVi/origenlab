"""Institution-prospect procurement intelligence (read-only read model)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

QueueName = Literal[
    "current_opportunity",
    "historical_prospect",
    "contact_gap",
    "institution_match_review",
    "line_evidence_review",
    "retender_review",
]

# Route-facing short names map 1:1 onto the planner's on-disk queue file
# stems; the API never accepts or exposes the raw "_queue" filenames.
QUEUE_NAME_TO_FILE_STEM: dict[str, str] = {
    "current_opportunity": "current_opportunity_queue",
    "historical_prospect": "historical_prospect_queue",
    "contact_gap": "contact_gap_queue",
    "institution_match_review": "institution_match_review_queue",
    "line_evidence_review": "line_evidence_review_queue",
    "retender_review": "retender_review_queue",
}


class InstitutionProspectMeta(BaseModel):
    data_source: Literal["institution_prospect_read_model"] = "institution_prospect_read_model"
    read_only: bool = True
    contract_version: str = ""
    supported_contract_version: bool = True
    planner_version: str = ""
    recognition_layer_version: str = ""
    as_of_utc: str = ""
    run_context: str = ""
    generated_at_utc: str = ""
    source_digest: str = ""
    source_path: str = ""
    source_path_info: dict[str, Any] | None = None
    source_kind: str = ""
    artifact_basename: str = ""
    canonical_reason: str = ""
    reduced_mode: bool = False
    stale: bool = False
    note: str = ""
    not_persisted: bool = True
    contact_authorization: bool = False
    outreach_authorization: bool = False


class InstitutionProspectStatusResponse(BaseModel):
    meta: InstitutionProspectMeta
    counts: dict[str, Any] = Field(default_factory=dict)
    operator_queue_sizes: dict[str, int] = Field(default_factory=dict)
    summary_ok: bool | None = None


class InstitutionProfileItem(BaseModel):
    """Projection of one planner profile dict. Nested sections are passed through
    verbatim (not re-typed field-by-field) so no provenance/reason-code data the
    planner already emits is ever silently dropped by the API layer."""

    institution_id: str = ""
    identity: dict[str, Any] = Field(default_factory=dict)
    account_contact_overlay: dict[str, Any] = Field(default_factory=dict)
    axes: dict[str, Any] = Field(default_factory=dict)
    equipment_history: list[dict[str, Any]] = Field(default_factory=list)
    current_opportunities: list[dict[str, Any]] = Field(default_factory=list)
    historical_signals: list[dict[str, Any]] = Field(default_factory=list)
    counts: dict[str, Any] = Field(default_factory=dict)
    operator_next_action: str | None = None
    contact_authorization: bool = False
    outreach_authorization: bool = False
    not_persisted: bool = True


class InstitutionProspectsResponse(BaseModel):
    meta: InstitutionProspectMeta
    limit: int = 50
    offset: int = 0
    total: int = 0
    count: int = 0
    items: list[InstitutionProfileItem] = Field(default_factory=list)


class InstitutionProspectDetailResponse(BaseModel):
    meta: InstitutionProspectMeta
    item: InstitutionProfileItem | None = None


class OperatorQueueRowsResponse(BaseModel):
    """One page of a single operator queue.

    Row shape varies by queue (see docs/INSTITUTION_PROSPECT_API_CONTRACT.md
    for the exact field list per queue, taken verbatim from
    commercial_procurement_institution_prospects.queues.EMPTY_QUEUE_HEADERS);
    rows are passed through as decoded dicts rather than re-declared as six
    parallel Pydantic models, so a field the planner adds is never dropped
    here before the contract doc and planner package are updated together.
    """

    meta: InstitutionProspectMeta
    queue: str = ""
    limit: int = 50
    offset: int = 0
    total: int = 0
    count: int = 0
    items: list[dict[str, Any]] = Field(default_factory=list)


__all__ = [
    "QUEUE_NAME_TO_FILE_STEM",
    "InstitutionProfileItem",
    "InstitutionProspectDetailResponse",
    "InstitutionProspectMeta",
    "InstitutionProspectStatusResponse",
    "InstitutionProspectsResponse",
    "OperatorQueueRowsResponse",
    "QueueName",
]
