"""Versions, safety budgets, and status vocabularies for the anexo evidence lane."""

from __future__ import annotations

from typing import Final

ANEXO_EVIDENCE_BUILDER_VERSION: Final = "chilecompra_anexo_evidence_builder_v1"
ANEXO_EVIDENCE_EXTRACTOR_VERSION: Final = "chilecompra_anexo_evidence_extractor_v1"
ANEXO_EVIDENCE_SEMANTIC_DIGEST_ALGORITHM: Final = "chilecompra_anexo_evidence_semantic_digest_v1"
ANEXO_EVIDENCE_CHUNK_ID_ALGORITHM: Final = "chilecompra_anexo_evidence_chunk_id_v1"
ANEXO_EVIDENCE_ATTACHMENT_ID_ALGORITHM: Final = "chilecompra_anexo_evidence_attachment_id_v1"

# --- Attachment acquisition sources (provenance layer) -----------------------
# How the attachment bytes for a TenderAttachmentBundle legitimately arrived.
# This is deliberately a closed, named vocabulary rather than a free-form
# string: every acquisition path this codebase supports (or could someday
# support) must be named here first.
ACQUISITION_SOURCE_CHILECOMPRA_LEGACY_PARTIAL_HTTP: Final = "chilecompra_legacy_partial_http"
ACQUISITION_SOURCE_OPERATOR_COMPLETE_BUNDLE: Final = "operator_complete_bundle"
# Interface-only seam (see acquisition_provenance.FutureChileCompraDocumentApiSource):
# no implementation exists or may be added speculatively.
ACQUISITION_SOURCE_FUTURE_CHILECOMPRA_DOCUMENT_API: Final = "future_chilecompra_document_api"

ACQUISITION_SOURCES: Final = frozenset(
    {
        ACQUISITION_SOURCE_CHILECOMPRA_LEGACY_PARTIAL_HTTP,
        ACQUISITION_SOURCE_OPERATOR_COMPLETE_BUNDLE,
        ACQUISITION_SOURCE_FUTURE_CHILECOMPRA_DOCUMENT_API,
    }
)

# --- Acquisition-level completeness states (provenance layer) ----------------
# Distinct from the 2-state (complete/incomplete) TenderTermsCoverage vocabulary
# in commercial_procurement_anexo_tender_terms.constants: this 3-state
# vocabulary exists because an un-declared operator bundle is genuinely
# unknown (it might be the full inventory, might not be) rather than known to
# be incomplete. Both "incomplete" and "unknown" still fail closed identically
# downstream, via TenderAttachmentBundle.incomplete_reason_codes.
COMPLETENESS_STATE_COMPLETE: Final = "complete"
COMPLETENESS_STATE_INCOMPLETE: Final = "incomplete"
COMPLETENESS_STATE_UNKNOWN: Final = "unknown"

COMPLETENESS_STATES: Final = frozenset(
    {
        COMPLETENESS_STATE_COMPLETE,
        COMPLETENESS_STATE_INCOMPLETE,
        COMPLETENESS_STATE_UNKNOWN,
    }
)

# --- Per-attachment processing outcomes (exactly one per discovered row) -----
OUTCOME_EXTRACTION_SUCCESS: Final = "extraction_success"
OUTCOME_EXTRACTED_EMPTY: Final = "extracted_empty"
OUTCOME_NEEDS_OCR: Final = "needs_ocr"
OUTCOME_NEEDS_CONVERTER: Final = "needs_converter"
OUTCOME_UNSUPPORTED_FORMAT: Final = "unsupported_format"
OUTCOME_ENCRYPTED: Final = "encrypted"
OUTCOME_CORRUPT: Final = "corrupt"
OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT: Final = "partial_due_to_safety_limit"
OUTCOME_DOWNLOAD_FAILED: Final = "download_failed"
OUTCOME_EXTRACTION_FAILED: Final = "extraction_failed"

ALL_OUTCOMES: Final = (
    OUTCOME_EXTRACTION_SUCCESS,
    OUTCOME_EXTRACTED_EMPTY,
    OUTCOME_NEEDS_OCR,
    OUTCOME_NEEDS_CONVERTER,
    OUTCOME_UNSUPPORTED_FORMAT,
    OUTCOME_ENCRYPTED,
    OUTCOME_CORRUPT,
    OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT,
    OUTCOME_DOWNLOAD_FAILED,
    OUTCOME_EXTRACTION_FAILED,
)

# Outcomes that mean "this document was fully read and accounted for".
COMPLETE_OUTCOMES: Final = frozenset({OUTCOME_EXTRACTION_SUCCESS, OUTCOME_EXTRACTED_EMPTY})

# --- Budget / preflight reason codes ----------------------------------------
REASON_ATTACHMENT_COUNT_BUDGET_EXCEEDED: Final = "attachment_count_budget_exceeded"
REASON_TOTAL_BYTES_BUDGET_EXCEEDED: Final = "total_bytes_budget_exceeded"
REASON_EMBEDDED_CONTENT_UNPROCESSED: Final = "embedded_content_unprocessed"
# A ficha advertised a second annex-listing surface (e.g. a popup gated by a
# browser-executed challenge) that this HTTP-only acquisition path cannot
# enumerate. This never implies zero attachments exist behind it, and it is
# deliberately portal/mechanism-neutral in naming -- it means "we know there is
# more, we don't know how much", not "download failed" or "budget exceeded".
REASON_GATED_LISTING_UNREACHABLE: Final = "gated_listing_unreachable"
REASON_ARCHIVE_DEPTH_LIMIT: Final = "archive_depth_limit"
REASON_ARCHIVE_MEMBER_LIMIT: Final = "archive_member_limit"
REASON_ARCHIVE_BYTES_LIMIT: Final = "archive_bytes_limit"
REASON_ARCHIVE_RATIO_LIMIT: Final = "archive_ratio_limit"
REASON_ARCHIVE_SUSPICIOUS_MEMBER: Final = "archive_suspicious_member"
REASON_OOXML_CONTAINER_UNSAFE: Final = "ooxml_container_unsafe"

# Actual (decompressed-stream) bounds, as opposed to the declared header bounds
# above. A central directory can understate a member's true expansion.
REASON_ARCHIVE_ACTUAL_MEMBER_BYTES_LIMIT: Final = "archive_actual_member_bytes_limit"
REASON_ARCHIVE_ACTUAL_TOTAL_BYTES_LIMIT: Final = "archive_actual_total_bytes_limit"
REASON_ARCHIVE_ACTUAL_RATIO_LIMIT: Final = "archive_actual_ratio_limit"
REASON_ARCHIVE_ACTUAL_SIZE_MISMATCH: Final = "archive_actual_size_mismatch"

# An operator-supplied ZIP bundle (source_kind "operator_complete_bundle") was
# imported without the operator explicitly asserting (via --declare-complete)
# that it is Mercado Público's complete attachment inventory. This is never
# inferred from file count, filenames, or comparison against any other
# source's result count -- see acquisition_provenance.resolve_completeness_state.
REASON_OPERATOR_COMPLETENESS_NOT_DECLARED: Final = "operator_completeness_not_declared"

# Streaming verification reads this much at a time, bounding verifier memory.
DEFAULT_ARCHIVE_VERIFY_CHUNK_BYTES: Final = 64 * 1024

# --- Detected formats --------------------------------------------------------
FORMAT_PDF: Final = "pdf"
FORMAT_DOCX: Final = "docx"
FORMAT_XLSX: Final = "xlsx"
FORMAT_PPTX: Final = "pptx"
FORMAT_ZIP: Final = "zip"
FORMAT_LEGACY_OLE: Final = "legacy_ole"
FORMAT_XLS: Final = "xls"
FORMAT_DOC: Final = "doc"
FORMAT_CSV: Final = "csv"
FORMAT_TXT: Final = "txt"
FORMAT_XML: Final = "xml"
FORMAT_IMAGE: Final = "image"
FORMAT_RAR: Final = "rar"
FORMAT_SEVENZIP: Final = "7z"
FORMAT_UNKNOWN: Final = "unknown"

# --- Document role tags (non-authoritative; never used to skip a file) -------
ROLE_TECHNICAL_SPEC: Final = "technical_spec"
ROLE_ADMINISTRATIVE_BASIS: Final = "administrative_basis"
ROLE_ECONOMIC_FORM: Final = "economic_form"
ROLE_TEMPLATE: Final = "template"
ROLE_UNKNOWN: Final = "unknown"

# --- Extraction safety budgets (all overridable per run) ---------------------
DEFAULT_MAX_PDF_PAGES: Final = 2000
DEFAULT_MAX_CHUNK_CHARS: Final = 8000
DEFAULT_MAX_CHARS_PER_ATTACHMENT: Final = 4_000_000
DEFAULT_MAX_CSV_ROWS: Final = 200_000
DEFAULT_MAX_XLSX_CELLS: Final = 500_000
DEFAULT_MAX_XLSX_NONEMPTY_CELLS: Final = 200_000
DEFAULT_MAX_XML_CHARS: Final = 4_000_000
DEFAULT_CSV_ROWS_PER_CHUNK: Final = 200
DEFAULT_XLSX_ROWS_PER_CHUNK: Final = 100

# A PDF page with less than this many non-whitespace characters counts as
# image-only for OCR triage purposes.
DEFAULT_PDF_MIN_PAGE_CHARS: Final = 8

# --- Archive safety budgets --------------------------------------------------
DEFAULT_MAX_ARCHIVE_MEMBERS: Final = 512
DEFAULT_MAX_ARCHIVE_TOTAL_UNCOMPRESSED_BYTES: Final = 256 * 1024 * 1024
DEFAULT_MAX_ARCHIVE_MEMBER_UNCOMPRESSED_BYTES: Final = 64 * 1024 * 1024
DEFAULT_MAX_ARCHIVE_COMPRESSION_RATIO: Final = 200.0
DEFAULT_MAX_ARCHIVE_DEPTH: Final = 2

# --- Acquisition budgets (evidence lane; deliberately higher than #438) ------
DEFAULT_EVIDENCE_MAX_ATTACHMENT_COUNT: Final = 300
DEFAULT_EVIDENCE_MAX_TOTAL_BYTES: Final = 512 * 1024 * 1024

# Query keys that must never reach a shareable artifact.
FORBIDDEN_ARTIFACT_SUBSTRINGS: Final = ("qs=", "enc=", "ticket=")
