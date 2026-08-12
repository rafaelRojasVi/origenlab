"""Immutable ANEXO-T1 structured tender-term models.

The terms layer is downstream of chilecompra_anexo_evidence. It never replaces
the source evidence: every asserted fact must remain traceable to the exact
attachment/chunk/locator that supports it.

Missing evidence is not false:
- not_explicitly_found requires complete source coverage;
- incomplete coverage yields unknown instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from origenlab_email_pipeline.chilecompra_anexo_evidence.constants import (
    COMPLETE_OUTCOMES,
)

from .constants import (
    COVERAGE_COMPLETE,
    COVERAGE_INCOMPLETE,
    COVERAGE_STATES,
    FACT_STATE_CONFLICTING,
    FACT_STATE_DERIVED,
    FACT_STATE_EXPLICIT,
    FACT_STATE_NOT_EXPLICITLY_FOUND,
    FACT_STATE_UNKNOWN,
    FACT_STATES,
    TENDER_TERMS_VERSION,
)


@dataclass(frozen=True)
class TermEvidence:
    """One human-checkable source pointer supporting a structured term."""

    evidence_id: str
    attachment_id: str
    evidence_attachment_id: str
    safe_filename: str
    attachment_sha256: str
    detected_format: str
    document_role: str
    chunk_id: str
    locator_type: str
    locator: dict[str, Any]
    locator_display: str
    evidence_excerpt: str
    evidence_text_sha256: str
    container_attachment_id: str | None = None
    member_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "attachment_id": self.attachment_id,
            "evidence_attachment_id": self.evidence_attachment_id,
            "safe_filename": self.safe_filename,
            "attachment_sha256": self.attachment_sha256,
            "detected_format": self.detected_format,
            "document_role": self.document_role,
            "container_attachment_id": self.container_attachment_id,
            "member_path": self.member_path,
            "chunk_id": self.chunk_id,
            "locator_type": self.locator_type,
            "locator": dict(self.locator),
            "locator_display": self.locator_display,
            "evidence_excerpt": self.evidence_excerpt,
            "evidence_text_sha256": self.evidence_text_sha256,
        }


@dataclass(frozen=True)
class FactCandidate:
    """One candidate value when source documents disagree."""

    candidate_id: str
    value: Any
    evidence: tuple[TermEvidence, ...]

    def __post_init__(self) -> None:
        if not self.evidence:
            raise ValueError("fact candidate requires provenance evidence")

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "value": self.value,
            "evidence": [row.to_dict() for row in self.evidence],
        }


@dataclass(frozen=True)
class TenderTermFact:
    """One structured commercial/technical tender fact."""

    fact_id: str
    field_name: str
    state: str
    coverage_status: str

    value: Any = None
    value_type: str | None = None
    unit: str | None = None
    currency: str | None = None
    tax_basis: str | None = None

    evidence: tuple[TermEvidence, ...] = ()
    candidates: tuple[FactCandidate, ...] = ()
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.state not in FACT_STATES:
            raise ValueError(f"invalid fact state: {self.state}")
        if self.coverage_status not in COVERAGE_STATES:
            raise ValueError(f"invalid coverage status: {self.coverage_status}")

        if self.state in {FACT_STATE_EXPLICIT, FACT_STATE_DERIVED}:
            if self.value is None:
                raise ValueError(f"{self.state} fact requires a value")
            if not self.evidence:
                raise ValueError(f"{self.state} fact requires provenance evidence")
            if self.candidates:
                raise ValueError(f"{self.state} fact cannot carry conflict candidates")

        elif self.state == FACT_STATE_NOT_EXPLICITLY_FOUND:
            if self.coverage_status != COVERAGE_COMPLETE:
                raise ValueError(
                    "not_explicitly_found requires complete extraction coverage"
                )
            if self.value is not None or self.candidates:
                raise ValueError(
                    "not_explicitly_found cannot assert a value or candidates"
                )

        elif self.state == FACT_STATE_UNKNOWN:
            if self.value is not None or self.candidates:
                raise ValueError("unknown fact cannot assert a value or candidates")

        elif self.state == FACT_STATE_CONFLICTING:
            if self.value is not None:
                raise ValueError("conflicting fact cannot select one authoritative value")
            if len(self.candidates) < 2:
                raise ValueError("conflicting fact requires at least two candidates")

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "field_name": self.field_name,
            "state": self.state,
            "coverage_status": self.coverage_status,
            "value": self.value,
            "value_type": self.value_type,
            "unit": self.unit,
            "currency": self.currency,
            "tax_basis": self.tax_basis,
            "evidence": [row.to_dict() for row in self.evidence],
            "candidates": [row.to_dict() for row in self.candidates],
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class TenderItemTerms:
    """Structured terms belonging to one procurement item."""

    item_id: str
    item_number: str | None
    item_label: str | None
    facts: tuple[TenderTermFact, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "item_number": self.item_number,
            "item_label": self.item_label,
            "facts": [fact.to_dict() for fact in self.facts],
        }


@dataclass(frozen=True)
class TenderTermsCoverage:
    """How completely ANEXO-T1 could inspect the tender's source corpus."""

    has_source_bundle: bool
    extraction_complete: bool
    attachments_discovered: int
    attachments_downloaded: int
    incomplete_reason_codes: tuple[str, ...] = ()
    unread_attachment_ids: tuple[str, ...] = ()
    outcome_counts: dict[str, int] = field(default_factory=dict)

    @property
    def debt_outcome_counts(self) -> dict[str, int]:
        """Unread or otherwise incomplete source outcomes, fail-closed."""

        return {
            code: int(count)
            for code, count in sorted(self.outcome_counts.items())
            if int(count) > 0 and code not in COMPLETE_OUTCOMES
        }

    @property
    def is_complete(self) -> bool:
        return (
            self.has_source_bundle
            and self.extraction_complete
            and not self.incomplete_reason_codes
            and not self.unread_attachment_ids
            and not self.debt_outcome_counts
        )

    @property
    def fact_coverage_status(self) -> str:
        return COVERAGE_COMPLETE if self.is_complete else COVERAGE_INCOMPLETE

    def to_dict(self) -> dict[str, Any]:
        return {
            "has_source_bundle": self.has_source_bundle,
            "extraction_complete": self.extraction_complete,
            "attachments_discovered": self.attachments_discovered,
            "attachments_downloaded": self.attachments_downloaded,
            "incomplete_reason_codes": list(self.incomplete_reason_codes),
            "unread_attachment_ids": list(self.unread_attachment_ids),
            "outcome_counts": dict(self.outcome_counts),
            "debt_outcome_counts": dict(self.debt_outcome_counts),
            "is_complete": self.is_complete,
            "fact_coverage_status": self.fact_coverage_status,
        }


@dataclass(frozen=True)
class TenderTermsBundle:
    """Whole structured-intelligence result for one tender."""

    tender_id: str
    source_bundle_semantic_digest: str
    tender_facts: tuple[TenderTermFact, ...]
    items: tuple[TenderItemTerms, ...]
    coverage: TenderTermsCoverage

    terms_version: str = TENDER_TERMS_VERSION
    semantic_digest: str = ""

    # Hard safety invariants: T1 is read-only intelligence only.
    contact_authorization: bool = False
    outreach_authorization: bool = False
    production_queue_mutated: bool = False
    persisted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "tender_id": self.tender_id,
            "source_bundle_semantic_digest": self.source_bundle_semantic_digest,
            "tender_facts": [fact.to_dict() for fact in self.tender_facts],
            "items": [item.to_dict() for item in self.items],
            "coverage": self.coverage.to_dict(),
            "terms_version": self.terms_version,
            "semantic_digest": self.semantic_digest,
            "contact_authorization": self.contact_authorization,
            "outreach_authorization": self.outreach_authorization,
            "production_queue_mutated": self.production_queue_mutated,
            "persisted": self.persisted,
        }
