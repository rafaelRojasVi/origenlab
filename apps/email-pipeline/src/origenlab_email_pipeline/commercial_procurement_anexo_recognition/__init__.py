"""ANEXO-E2 — annex-backed equipment recognition comparison (read-only).

Measures whether the hardened #450/#451 annex evidence improves equipment
recognition, before any of it is allowed to affect production opportunity
semantics. This package does not classify differently from production: it runs
the existing PR5D recognizer twice over the same tenders and reports the
difference.
"""

from .compare import build_claims, build_coverage_debt, classify_change
from .constants import CHANGE_CLASSES, COMPARISON_VERSION, INTERPRETATIONS
from .corpus import load_anexo_evidence, load_detail_cache, select_corpus
from .models import AnexoClaim, AnexoEvidenceSegment, ComparisonResult, TenderComparison
from .planner import (
    AnexoComparisonError,
    build_summary,
    run_comparison,
    write_comparison_outputs,
)
from .segment import segment_chunks

__all__ = [
    "AnexoClaim",
    "AnexoComparisonError",
    "AnexoEvidenceSegment",
    "CHANGE_CLASSES",
    "COMPARISON_VERSION",
    "ComparisonResult",
    "INTERPRETATIONS",
    "TenderComparison",
    "build_claims",
    "build_coverage_debt",
    "build_summary",
    "classify_change",
    "load_anexo_evidence",
    "load_detail_cache",
    "run_comparison",
    "segment_chunks",
    "select_corpus",
    "write_comparison_outputs",
]
