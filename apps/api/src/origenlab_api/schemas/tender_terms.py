"""ANEXO-T1 tender-term intelligence (read-only, W1-gated actionability).

W1's ``current_opportunity_queue`` read model remains the sole actionability
authority: a tender is only "actionable" if it is present in that queue.
T1 (this module) can only add commercial-term detail (facts/items/coverage)
on top of an already-actionable W1 row — it can never make a tender
actionable on its own. See ``services/tender_terms_service.py``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TenderTermsMeta(BaseModel):
    """Fail-closed status of the T1 published-terms lookup for one tender."""

    data_source: str = "tender_terms_read_model"
    read_only: bool = True
    contract_version: str = ""
    supported_contract_version: bool = False
    terms_version: str = ""
    as_of_utc: str = ""
    source_path: str = ""
    source_kind: str = "tender_terms_bundle"
    artifact_basename: str = ""
    canonical_reason: str = ""
    reduced_mode: bool = False
    note: str = ""
    published: bool = False
    contact_authorization: bool = False
    outreach_authorization: bool = False


class TermEvidence(BaseModel):
    """One human-checkable source pointer supporting a structured term."""

    evidence_id: str = ""
    attachment_id: str = ""
    evidence_attachment_id: str = ""
    safe_filename: str = ""
    attachment_sha256: str = ""
    detected_format: str = ""
    document_role: str = ""
    chunk_id: str = ""
    locator_type: str = ""
    locator: dict[str, Any] = Field(default_factory=dict)
    locator_display: str = ""
    evidence_excerpt: str = ""
    evidence_text_sha256: str = ""
    container_attachment_id: str | None = None
    member_path: str | None = None


class FactCandidate(BaseModel):
    """One candidate value when source documents disagree."""

    candidate_id: str = ""
    value: Any = None
    evidence: list[TermEvidence] = Field(default_factory=list)


class TenderTermFact(BaseModel):
    """One structured commercial/technical tender fact."""

    fact_id: str = ""
    field_name: str = ""
    state: str = "unknown"
    coverage_status: str = "incomplete"

    value: Any = None
    value_type: str | None = None
    unit: str | None = None
    currency: str | None = None
    tax_basis: str | None = None

    evidence: list[TermEvidence] = Field(default_factory=list)
    candidates: list[FactCandidate] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)


class TenderItemTerms(BaseModel):
    """Structured terms belonging to one procurement item."""

    item_id: str = ""
    item_number: str | None = None
    item_label: str | None = None
    facts: list[TenderTermFact] = Field(default_factory=list)


class TenderTermsCoverage(BaseModel):
    """How completely ANEXO-T1 could inspect the tender's source corpus."""

    has_source_bundle: bool = False
    extraction_complete: bool = False
    attachments_discovered: int = 0
    attachments_downloaded: int = 0
    incomplete_reason_codes: list[str] = Field(default_factory=list)
    unread_attachment_ids: list[str] = Field(default_factory=list)
    outcome_counts: dict[str, int] = Field(default_factory=dict)
    debt_outcome_counts: dict[str, int] = Field(default_factory=dict)
    is_complete: bool = False
    fact_coverage_status: str = "incomplete"


class TenderQueueRow(BaseModel):
    """One W1 ``current_opportunity_queue`` row (institution+tender+category grain).

    Deliberately permissive/pass-through: this mirrors whatever the W1 queue
    read model actually publishes for a row, redacted for path fields. Kept
    as a typed-but-open model (rather than a bare dict) so the contract test
    can assert its presence without hardcoding every W1 queue column here.
    """

    institution_id: str | None = None
    display_name: str | None = None
    tender_code: str | None = None
    equipment_category: str | None = None
    commercial_signal_type: str | None = None
    close_timestamp: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "TenderQueueRow":
        known = {
            "institution_id",
            "display_name",
            "tender_code",
            "equipment_category",
            "commercial_signal_type",
            "close_timestamp",
        }
        extra = {k: v for k, v in row.items() if k not in known}
        return cls(
            institution_id=row.get("institution_id"),
            display_name=row.get("display_name"),
            tender_code=row.get("tender_code"),
            equipment_category=row.get("equipment_category"),
            commercial_signal_type=row.get("commercial_signal_type"),
            close_timestamp=row.get("close_timestamp"),
            extra=extra,
        )


class TenderDetailResponse(BaseModel):
    """Merged W1 (actionability) + T1 (term intelligence) view of one tender.

    ``found_in_queue`` / ``queue_meta`` / ``queue_rows`` come exclusively from
    the W1 ``current_opportunity_queue`` read model. T1 fields
    (``tender_facts`` / ``items`` / ``coverage``) are additive detail only and
    are always empty/absent when T1 has no publication for this tender_id,
    regardless of what W1 says — never fabricated to fill the gap.

    T1 detail is only ever attached when ``found_in_queue`` is True AND the
    W1 lookup itself was healthy (``queue_meta.reduced_mode`` is False) — see
    ``services/tender_terms_service.py`` for the single enforcement point.
    """

    tender_code: str = ""
    queue_meta: dict[str, Any] = Field(default_factory=dict)
    found_in_queue: bool = False
    queue_row: dict[str, Any] | None = None
    queue_rows: list[TenderQueueRow] = Field(default_factory=list)

    t1_meta: TenderTermsMeta = Field(default_factory=TenderTermsMeta)
    t1_published: bool = False
    tender_facts: list[TenderTermFact] = Field(default_factory=list)
    items: list[TenderItemTerms] = Field(default_factory=list)
    coverage: TenderTermsCoverage | None = None


__all__ = [
    "FactCandidate",
    "TenderDetailResponse",
    "TenderItemTerms",
    "TenderQueueRow",
    "TenderTermFact",
    "TenderTermsCoverage",
    "TenderTermsMeta",
    "TermEvidence",
]
