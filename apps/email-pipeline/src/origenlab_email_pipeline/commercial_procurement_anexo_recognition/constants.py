"""ANEXO-E2 vocabularies: change classes, coverage debt, annex effects.

Read-only measurement lane. Nothing here authorizes outreach, mutates
commercial state, or feeds the production opportunity queues.
"""

from __future__ import annotations

from typing import Final


COMPARISON_VERSION: Final = "anexo_e2_v1"
COMPARISON_ALGORITHM: Final = "anexo_recognition_comparison_v1"

# Evidence produced by this lane is tagged with its own source plane so it can
# never be mistaken for PR5C/PR5D acquisition-plane evidence.
ANEXO_SOURCE_PLANE: Final = "anexo_evidence"
BASELINE_SOURCE_PLANE: Final = "acquisition"

# --- how a tender's recognition changed once annex evidence was added --------
CHANGE_UNCHANGED_POSITIVE: Final = "unchanged_positive"
CHANGE_UNCHANGED_NEGATIVE_OR_UNRESOLVED: Final = "unchanged_negative_or_unresolved"
CHANGE_NEW_ANNEX_POSITIVE: Final = "new_annex_positive"
CHANGE_ANNEX_STRENGTHENED_EXISTING: Final = "annex_strengthened_existing"
CHANGE_ANNEX_CHANGED_CATEGORY: Final = "annex_changed_category"
CHANGE_ANNEX_CREATED_CONFLICT: Final = "annex_created_conflict"
CHANGE_BASELINE_POSITIVE_ANNEX_INCOMPLETE: Final = "baseline_positive_annex_incomplete"
CHANGE_ANNEX_INCOMPLETE_NO_CONCLUSION: Final = "annex_incomplete_no_conclusion"

CHANGE_CLASSES: Final = (
    CHANGE_UNCHANGED_POSITIVE,
    CHANGE_UNCHANGED_NEGATIVE_OR_UNRESOLVED,
    CHANGE_NEW_ANNEX_POSITIVE,
    CHANGE_ANNEX_STRENGTHENED_EXISTING,
    CHANGE_ANNEX_CHANGED_CATEGORY,
    CHANGE_ANNEX_CREATED_CONFLICT,
    CHANGE_BASELINE_POSITIVE_ANNEX_INCOMPLETE,
    CHANGE_ANNEX_INCOMPLETE_NO_CONCLUSION,
)

# --- what a *lack* of annex matches is allowed to mean -----------------------
# The load-bearing rule of this lane: annex evidence may prove PRESENCE, but an
# incomplete extraction may never prove ABSENCE.
INTERPRETATION_PRESENCE_PROVEN: Final = "presence_proven"
INTERPRETATION_ABSENCE_UNPROVEN: Final = "absence_unproven"
INTERPRETATION_NO_CLAIM_COVERAGE_COMPLETE: Final = "no_annex_claim_extraction_complete"
INTERPRETATION_NO_ANEXOS: Final = "no_anexo_evidence_available"

INTERPRETATIONS: Final = (
    INTERPRETATION_PRESENCE_PROVEN,
    INTERPRETATION_ABSENCE_UNPROVEN,
    INTERPRETATION_NO_CLAIM_COVERAGE_COMPLETE,
    INTERPRETATION_NO_ANEXOS,
)

# --- coverage debt -----------------------------------------------------------
# Any attachment outcome that leaves part of the annex corpus unread. Mirrors
# the #450 extraction outcome vocabulary; `extracted_empty` is NOT debt because
# an empty document was genuinely read.
COVERAGE_DEBT_OUTCOMES: Final = (
    "needs_ocr",
    "needs_converter",
    "unsupported_format",
    "encrypted",
    "corrupt",
    "partial_due_to_safety_limit",
    "download_failed",
    "extraction_failed",
)

# --- how one annex claim relates to the baseline ------------------------------
EFFECT_CREATED_NEW_CLAIM: Final = "created_new_claim"
EFFECT_STRENGTHENED_EXISTING: Final = "strengthened_existing"
EFFECT_CONTRADICTED_EXISTING: Final = "contradicted_existing"
EFFECT_SUPPORTING_CONTEXT_ONLY: Final = "supporting_context_only"

ANNEX_EFFECTS: Final = (
    EFFECT_CREATED_NEW_CLAIM,
    EFFECT_STRENGTHENED_EXISTING,
    EFFECT_CONTRADICTED_EXISTING,
    EFFECT_SUPPORTING_CONTEXT_ONLY,
)

# --- segmentation -------------------------------------------------------------
SEGMENT_WHOLE_CHUNK: Final = "whole_chunk"
SEGMENT_TABLE_ROW: Final = "table_row"
SEGMENT_PAGE_BLOCK: Final = "page_block"
SEGMENT_SHEET_ROW: Final = "sheet_row"

SEGMENT_KINDS: Final = (
    SEGMENT_WHOLE_CHUNK,
    SEGMENT_TABLE_ROW,
    SEGMENT_PAGE_BLOCK,
    SEGMENT_SHEET_ROW,
)

# A segment shorter than this carries no recognizable product text; keeping it
# would only add noise. Segments are still *counted*, never silently dropped.
MIN_SEGMENT_CHARS: Final = 3

# A PDF page with no blank lines (continuous prose) collapses into one huge
# block, and a claim anchored to it points at a whole page rather than a line.
# Blocks above this size are split further on line boundaries so the locator
# stays reviewable.
MAX_PAGE_BLOCK_CHARS: Final = 400

# Evidence tier assigned to annex segments, by segment kind.
#
# A row of an "Ítem | Producto" annex table is a product line in the same sense
# an API item line is, so it earns `line_product_text`. Prose paragraphs and
# PDF page blocks are descriptive text and earn `tender_description`. Tier
# drives PR5D's confidence band, so this mapping is deliberately explicit and
# auditable rather than implied.
SEGMENT_EVIDENCE_TIERS: Final = {
    SEGMENT_TABLE_ROW: "line_product_text",
    SEGMENT_SHEET_ROW: "line_product_text",
    SEGMENT_PAGE_BLOCK: "tender_description",
    SEGMENT_WHOLE_CHUNK: "tender_description",
}

# Document roles are ranking/context metadata only. They never gate whether a
# document is searched (#450 found specification tables inside administrative
# documents).
DOCUMENT_ROLES: Final = (
    "technical_spec",
    "administrative_basis",
    "economic_form",
    "template",
    "unknown",
)
