"""Immutable ANEXO-E2 comparison models.

Every annex-derived claim carries enough provenance to be re-found by hand in
the original attachment: tender, attachment identity and digest, archive member
path, chunk, and an in-chunk locator.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class AnexoEvidenceSegment:
    """One addressable annex evidence unit: a chunk, or a span inside a chunk.

    Chunks from #450 can be whole pages or whole tables. Recognizing over those
    directly would let one page assert five unrelated categories at once, so a
    chunk is split into addressable segments that keep their own locator.
    """

    segment_id: str
    tender_id: str
    chunk_id: str
    attachment_id: str
    evidence_attachment_id: str
    safe_filename: str
    attachment_sha256: str
    detected_format: str
    document_role: str
    container_attachment_id: str | None
    member_path: str | None
    locator_type: str
    locator: dict[str, Any]
    segment_kind: str
    segment_locator: dict[str, Any]
    segment_ordinal: int
    text_raw: str
    evidence_tier: str

    @property
    def locator_display(self) -> str:
        """Human-readable "where in the document" string for review packets."""
        return render_locator(
            safe_filename=self.safe_filename,
            member_path=self.member_path,
            locator_type=self.locator_type,
            locator=self.locator,
            segment_kind=self.segment_kind,
            segment_locator=self.segment_locator,
        )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["locator_display"] = self.locator_display
        return d


def render_locator(
    *,
    safe_filename: str,
    member_path: str | None,
    locator_type: str,
    locator: dict[str, Any],
    segment_kind: str,
    segment_locator: dict[str, Any],
) -> str:
    """Render a stable, human-checkable source locator."""
    where = safe_filename
    if member_path:
        where = f"{safe_filename}::{member_path}"

    if locator_type == "docx_paragraph":
        section = locator.get("section", "body")
        inner = f"{section} paragraph {locator.get('paragraph')}"
    elif locator_type == "docx_table":
        section = locator.get("section", "body")
        inner = f"{section} table {locator.get('table')}"
        if "row" in segment_locator:
            inner += f" row {segment_locator['row']}"
    elif locator_type == "pdf_page":
        inner = f"page {locator.get('page')}"
        if locator.get("part") not in (None, 1):
            inner += f" part {locator['part']}"
        if "block" in segment_locator:
            inner += f" block {segment_locator['block']}"
        if "line" in segment_locator:
            inner += f" line {segment_locator['line']}"
    elif locator_type == "xlsx_rows":
        sheet = locator.get("sheet")
        inner = f"sheet {sheet!r} rows {locator.get('start_row')}-{locator.get('end_row')}"
        if "row" in segment_locator:
            inner += f" row {segment_locator['row']}"
    else:
        inner = f"{locator_type} {sorted(locator.items())}"

    return f"{where} :: {inner}"


@dataclass(frozen=True)
class AnexoClaim:
    """One equipment-category claim derived from annex evidence.

    A claim exists only with full provenance; there is no code path that emits
    a category without a resolvable chunk and locator.
    """

    claim_id: str
    tender_id: str
    matched_category: str

    # provenance
    attachment_id: str
    evidence_attachment_id: str
    safe_filename: str
    attachment_sha256: str
    detected_format: str
    document_role: str
    container_attachment_id: str | None
    member_path: str | None
    chunk_id: str
    contributing_chunk_ids: tuple[str, ...]
    segment_id: str
    locator_type: str
    locator: dict[str, Any]
    segment_kind: str
    segment_locator: dict[str, Any]
    locator_display: str
    evidence_excerpt: str
    evidence_text_sha256: str

    # recognition
    relevance_class: str
    confidence_band: str
    evidence_tier: str
    matched_rule_ids: tuple[str, ...]
    unit_decision_id: str
    taxonomy_version: str
    rules_version: str
    recognition_version: str

    # relation to baseline
    existed_in_baseline: bool
    annex_effect: str

    # Advisory only: never suppresses the claim, never changes recognition.
    risk_reason_codes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["contributing_chunk_ids"] = list(self.contributing_chunk_ids)
        d["matched_rule_ids"] = list(self.matched_rule_ids)
        d["risk_reason_codes"] = list(self.risk_reason_codes)
        return d


@dataclass(frozen=True)
class CoverageDebt:
    """What part of a tender's annex corpus could not be read."""

    tender_id: str
    has_anexo_bundle: bool
    extraction_complete: bool
    attachments_discovered: int
    attachments_downloaded: int
    incomplete_reason_codes: tuple[str, ...]
    debt_outcome_counts: dict[str, int] = field(default_factory=dict)
    unread_attachment_ids: tuple[str, ...] = ()

    @property
    def is_complete(self) -> bool:
        return (
            self.has_anexo_bundle
            and self.extraction_complete
            and not self.debt_outcome_counts
        )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["incomplete_reason_codes"] = list(self.incomplete_reason_codes)
        d["unread_attachment_ids"] = list(self.unread_attachment_ids)
        d["is_complete"] = self.is_complete
        return d


@dataclass(frozen=True)
class TenderComparison:
    """Baseline vs augmented recognition for exactly one tender."""

    tender_id: str
    buyer_organization: str
    tender_title: str
    lifecycle_class: str

    baseline_unit_count: int
    baseline_relevance_class: str
    baseline_categories: tuple[str, ...]
    baseline_confidence_band: str
    baseline_resolution_status: str
    baseline_decision_id: str

    annex_segment_count: int
    annex_chunk_count: int
    # Every chunk is accounted for: chunks that yielded no segment (whitespace
    # or sub-minimal text) are counted here rather than silently disappearing.
    annex_chunk_total: int
    annex_chunks_without_segments: int
    annex_attachment_count: int

    augmented_relevance_class: str
    augmented_categories: tuple[str, ...]
    augmented_confidence_band: str
    augmented_resolution_status: str
    augmented_decision_id: str

    new_categories: tuple[str, ...]
    lost_categories: tuple[str, ...]
    change_class: str
    interpretation: str
    coverage: CoverageDebt
    claim_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for key in (
            "baseline_categories",
            "augmented_categories",
            "new_categories",
            "lost_categories",
            "claim_ids",
        ):
            d[key] = list(getattr(self, key))
        d["coverage"] = self.coverage.to_dict()
        return d


@dataclass(frozen=True)
class ComparisonResult:
    """Whole-run result: rows, claims, and aggregate-only metrics."""

    comparisons: tuple[TenderComparison, ...]
    claims: tuple[AnexoClaim, ...]
    segments: tuple[AnexoEvidenceSegment, ...]
    comparison_version: str
    taxonomy_version: str
    rules_version: str
    semantic_digest: str
    corpus_digest: str
    # Invariants re-proved on every run; see planner.assert_read_only_invariants.
    contact_authorization: bool = False
    outreach_authorization: bool = False
    production_queue_mutated: bool = False
    persisted: bool = False
