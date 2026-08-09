"""Deterministic digests for the ANEXO-E2 comparison.

Digests are computed over sorted, id-keyed payloads and never include
timestamps or output paths, so rerunning the same corpus reproduces the same
digest. Evidence text is reduced to a hash so digests stay shareable.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..commercial_procurement_acquisition.canonical_json import canonical_json_digest
from .constants import COMPARISON_ALGORITHM, COMPARISON_VERSION
from .models import AnexoClaim, TenderComparison


def corpus_digest(tenders: Sequence[dict]) -> str:
    """Digest of the evaluated tender set and its evidence shape."""
    payload = {
        "algorithm": f"{COMPARISON_ALGORITHM}.corpus",
        "version": COMPARISON_VERSION,
        "tenders": sorted(
            (
                {
                    "tender_id": t["tender_id"],
                    "line_count": len(t.get("lines") or []),
                    "has_title": bool((t.get("title") or "").strip()),
                    "has_description": bool((t.get("description") or "").strip()),
                }
                for t in tenders
            ),
            key=lambda r: r["tender_id"],
        ),
    }
    return canonical_json_digest(payload)


def comparison_semantic_digest(
    comparisons: Sequence[TenderComparison], claims: Sequence[AnexoClaim]
) -> str:
    """Digest of the comparison outcome, independent of run time and ordering."""
    payload = {
        "algorithm": COMPARISON_ALGORITHM,
        "version": COMPARISON_VERSION,
        "comparisons": sorted(
            (
                {
                    "tender_id": c.tender_id,
                    "baseline_categories": sorted(c.baseline_categories),
                    "augmented_categories": sorted(c.augmented_categories),
                    "change_class": c.change_class,
                    "interpretation": c.interpretation,
                    "coverage_complete": c.coverage.is_complete,
                }
                for c in comparisons
            ),
            key=lambda r: r["tender_id"],
        ),
        "claims": sorted(
            (
                {
                    "claim_id": c.claim_id,
                    "tender_id": c.tender_id,
                    "category": c.matched_category,
                    "chunk_id": c.chunk_id,
                    "segment_id": c.segment_id,
                    "annex_effect": c.annex_effect,
                    "evidence_text_sha256": c.evidence_text_sha256,
                }
                for c in claims
            ),
            key=lambda r: r["claim_id"],
        ),
    }
    return canonical_json_digest(payload)
