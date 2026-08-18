"""ANEXO-T1 structured Resolution/Bases tender intelligence."""

from .constants import (
    COVERAGE_COMPLETE,
    COVERAGE_INCOMPLETE,
    FACT_STATE_CONFLICTING,
    FACT_STATE_DERIVED,
    FACT_STATE_EXPLICIT,
    FACT_STATE_NOT_EXPLICITLY_FOUND,
    FACT_STATE_UNKNOWN,
    TENDER_TERMS_VERSION,
)
from .extract import extract_tender_terms
from .fingerprint import finalize_bundle, tender_terms_semantic_digest
from .production_publish import (
    TENDER_TERMS_DIRNAME,
    TENDER_TERMS_PUBLICATION_CONTRACT_VERSION,
    TenderTermsPublicationError,
    TenderTermsPublicationResult,
    load_published_tender_terms,
    publish_tender_terms,
    validate_tender_terms_row,
)
from .models import (
    FactCandidate,
    TenderItemTerms,
    TenderTermFact,
    TenderTermsBundle,
    TenderTermsCoverage,
    TermEvidence,
)
from .source import (
    ResolvedEvidenceChunk,
    coverage_from_bundle,
    resolve_evidence_chunk,
    resolve_evidence_chunks,
    term_evidence_from_span,
)

__all__ = [
    "COVERAGE_COMPLETE",
    "COVERAGE_INCOMPLETE",
    "FACT_STATE_CONFLICTING",
    "FACT_STATE_DERIVED",
    "FACT_STATE_EXPLICIT",
    "FACT_STATE_NOT_EXPLICITLY_FOUND",
    "FACT_STATE_UNKNOWN",
    "TENDER_TERMS_VERSION",
    "TENDER_TERMS_DIRNAME",
    "TENDER_TERMS_PUBLICATION_CONTRACT_VERSION",
    "TenderTermsPublicationError",
    "TenderTermsPublicationResult",
    "FactCandidate",
    "ResolvedEvidenceChunk",
    "TenderItemTerms",
    "TenderTermFact",
    "TenderTermsBundle",
    "TenderTermsCoverage",
    "TermEvidence",
    "coverage_from_bundle",
    "extract_tender_terms",
    "finalize_bundle",
    "load_published_tender_terms",
    "publish_tender_terms",
    "validate_tender_terms_row",
    "resolve_evidence_chunk",
    "resolve_evidence_chunks",
    "tender_terms_semantic_digest",
    "term_evidence_from_span",
]
