"""ANEXO-E2 orchestration: same-corpus baseline vs augmented comparison.

Read-only measurement lane. It reads local cached artifacts, runs the existing
PR5D recognizer twice over the same tenders, and publishes a review packet. It
never mutates commercial state, never authorizes contact or outreach, and never
writes to Gmail, SQLite, or Postgres.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..chilecompra_anexo_evidence.redaction import assert_no_portal_tokens
from ..commercial_procurement_candidate_planner.output_safety import write_atomically
from ..commercial_procurement_product_relevance.fingerprint import (
    relevance_rules_fingerprint,
    relevance_taxonomy_fingerprint,
)
from .compare import build_claims, build_coverage_debt, classify_change, is_conflict
from .constants import (
    CHANGE_CLASSES,
    COMPARISON_VERSION,
    COVERAGE_DEBT_OUTCOMES,
)
from .corpus import AnexoEvidenceBundleSet
from .fingerprint import comparison_semantic_digest, corpus_digest
from .models import AnexoClaim, ComparisonResult, TenderComparison
from .recognize import (
    aggregate_for_tender,
    build_annex_units,
    build_baseline_units,
    classify_units,
)
from .segment import segment_chunks
from .walkthrough import render_walkthrough


class AnexoComparisonError(RuntimeError):
    """Raised when a read-only invariant of the comparison lane is violated."""


def run_comparison(
    tenders: Sequence[dict[str, Any]],
    *,
    anexo: AnexoEvidenceBundleSet,
) -> ComparisonResult:
    """Run every tender through baseline and augmented recognition."""
    taxonomy_version = relevance_taxonomy_fingerprint()
    rules_version = relevance_rules_fingerprint()
    input_fingerprint = corpus_digest(tenders)

    comparisons: list[TenderComparison] = []
    all_claims: list[AnexoClaim] = []
    all_segments = []

    for tender in sorted(tenders, key=lambda t: t["tender_id"]):
        tender_id = str(tender["tender_id"])
        lifecycle = str(tender.get("lifecycle_class") or "unknown")

        baseline_units = build_baseline_units(tender)
        baseline_decisions = classify_units(baseline_units)
        baseline_refs = [u.evidence_ref_id or "" for u in baseline_units]
        baseline_decision = aggregate_for_tender(
            tender_id,
            baseline_decisions,
            lifecycle_class=lifecycle,
            evidence_ref_ids=baseline_refs,
            input_fingerprint=input_fingerprint,
        )

        chunks = anexo.chunks(tender_id)
        attachments = anexo.attachments(tender_id)
        segments = segment_chunks(chunks, attachments=attachments)
        annex_pairs = build_annex_units(segments)
        annex_units = [unit for unit, _ in annex_pairs]
        annex_decisions = classify_units(annex_units)
        decision_by_segment = list(
            zip(annex_decisions, [seg for _, seg in annex_pairs], strict=True)
        )

        augmented_decision = aggregate_for_tender(
            tender_id,
            tuple(baseline_decisions) + tuple(annex_decisions),
            lifecycle_class=lifecycle,
            evidence_ref_ids=baseline_refs + [s.chunk_id for s in segments],
            input_fingerprint=input_fingerprint,
        )

        baseline_categories = frozenset(baseline_decision.canonical_equipment_classes)
        augmented_categories = frozenset(augmented_decision.canonical_equipment_classes)
        annex_categories = frozenset(
            category
            for decision, _ in decision_by_segment
            for category in decision.canonical_equipment_classes
        )

        coverage = build_coverage_debt(
            tender_id,
            bundle=anexo.bundle(tender_id),
            attachments=attachments,
        )
        conflict = is_conflict(
            baseline_decision=baseline_decision, augmented_decision=augmented_decision
        )
        change_class, interpretation = classify_change(
            baseline_categories=baseline_categories,
            augmented_categories=augmented_categories,
            annex_categories=annex_categories,
            coverage=coverage,
            conflict=conflict,
        )

        claims = build_claims(
            decision_by_segment,
            baseline_categories=baseline_categories,
            augmented_categories=augmented_categories,
            conflict=conflict,
            taxonomy_version=taxonomy_version,
            rules_version=rules_version,
        )
        all_claims.extend(claims)
        all_segments.extend(segments)

        comparisons.append(
            TenderComparison(
                tender_id=tender_id,
                buyer_organization=str(tender.get("buyer_organization") or ""),
                tender_title=str(tender.get("title") or ""),
                lifecycle_class=lifecycle,
                baseline_unit_count=len(baseline_units),
                baseline_relevance_class=baseline_decision.relevance_class,
                baseline_categories=tuple(sorted(baseline_categories)),
                baseline_confidence_band=baseline_decision.confidence_band,
                baseline_resolution_status=baseline_decision.product_resolution_status,
                baseline_decision_id=baseline_decision.decision_id,
                annex_segment_count=len(segments),
                annex_chunk_count=len({s.chunk_id for s in segments}),
                annex_chunk_total=len(chunks),
                annex_chunks_without_segments=len(chunks)
                - len({s.chunk_id for s in segments}),
                annex_attachment_count=len(attachments),
                augmented_relevance_class=augmented_decision.relevance_class,
                augmented_categories=tuple(sorted(augmented_categories)),
                augmented_confidence_band=augmented_decision.confidence_band,
                augmented_resolution_status=augmented_decision.product_resolution_status,
                augmented_decision_id=augmented_decision.decision_id,
                new_categories=tuple(sorted(augmented_categories - baseline_categories)),
                lost_categories=tuple(sorted(baseline_categories - augmented_categories)),
                change_class=change_class,
                interpretation=interpretation,
                coverage=coverage,
                claim_ids=tuple(c.claim_id for c in claims),
            )
        )

    result = ComparisonResult(
        comparisons=tuple(comparisons),
        claims=tuple(all_claims),
        segments=tuple(all_segments),
        comparison_version=COMPARISON_VERSION,
        taxonomy_version=taxonomy_version,
        rules_version=rules_version,
        semantic_digest=comparison_semantic_digest(comparisons, all_claims),
        corpus_digest=input_fingerprint,
    )
    assert_read_only_invariants(result)
    return result


def assert_read_only_invariants(result: ComparisonResult) -> None:
    """Re-prove on every run that this lane authorizes and persists nothing."""
    if result.contact_authorization is not False:
        raise AnexoComparisonError("contact_authorization must remain false")
    if result.outreach_authorization is not False:
        raise AnexoComparisonError("outreach_authorization must remain false")
    if result.production_queue_mutated is not False:
        raise AnexoComparisonError("comparison lane must not mutate production queues")
    if result.persisted is not False:
        raise AnexoComparisonError("comparison lane must not persist commercial state")

    known_chunks = {segment.chunk_id for segment in result.segments}
    known_segments = {segment.segment_id for segment in result.segments}
    for claim in result.claims:
        if claim.chunk_id not in known_chunks:
            raise AnexoComparisonError(
                f"claim {claim.claim_id} references unknown chunk {claim.chunk_id}"
            )
        if claim.segment_id not in known_segments:
            raise AnexoComparisonError(
                f"claim {claim.claim_id} references unknown segment {claim.segment_id}"
            )
        if not claim.locator_display or not claim.safe_filename:
            raise AnexoComparisonError(f"claim {claim.claim_id} lacks source provenance")

    claim_ids = {c.claim_id for c in result.claims}
    for comparison in result.comparisons:
        if (
            comparison.annex_chunk_count + comparison.annex_chunks_without_segments
            != comparison.annex_chunk_total
        ):
            raise AnexoComparisonError(
                f"chunk accounting broken for {comparison.tender_id}: every chunk must "
                f"be either segmented or explicitly counted as unsegmented"
            )
        if comparison.change_class not in CHANGE_CLASSES:
            raise AnexoComparisonError(
                f"unknown change class {comparison.change_class!r}"
            )
        for claim_id in comparison.claim_ids:
            if claim_id not in claim_ids:
                raise AnexoComparisonError(f"orphan claim reference {claim_id}")


def build_summary(result: ComparisonResult) -> dict[str, Any]:
    """Aggregate-only run summary; carries no per-tender rows."""
    comparisons = result.comparisons
    claims = result.claims

    with_bundle = [c for c in comparisons if c.coverage.has_anexo_bundle]
    complete = [c for c in with_bundle if c.coverage.is_complete]

    debt_counter: Counter[str] = Counter()
    for comparison in with_bundle:
        for code, count in comparison.coverage.debt_outcome_counts.items():
            debt_counter[code] += count

    new_positive = [c for c in claims if c.annex_effect == "created_new_claim"]

    return {
        "algorithm": "anexo_recognition_comparison_v1",
        "comparison_version": result.comparison_version,
        "taxonomy_version": result.taxonomy_version,
        "rules_version": result.rules_version,
        "semantic_digest": result.semantic_digest,
        "corpus_digest": result.corpus_digest,
        "tenders_evaluated": len(comparisons),
        "tenders_with_anexos": len(with_bundle),
        "bundles_complete": len(complete),
        "bundles_incomplete": len(with_bundle) - len(complete),
        "attachments": sum(c.annex_attachment_count for c in with_bundle),
        "annex_chunks_total": sum(c.annex_chunk_total for c in comparisons),
        "annex_chunks_segmented": sum(c.annex_chunk_count for c in comparisons),
        "annex_chunks_without_segments": sum(
            c.annex_chunks_without_segments for c in comparisons
        ),
        "annex_segments": sum(c.annex_segment_count for c in comparisons),
        "baseline_claims": sum(len(c.baseline_categories) for c in comparisons),
        "augmented_claims": sum(len(c.augmented_categories) for c in comparisons),
        "annex_claims": len(claims),
        "new_annex_positive_claims": len(new_positive),
        "strengthened_claims": sum(
            1 for c in claims if c.annex_effect == "strengthened_existing"
        ),
        "contradicting_claims": sum(
            1 for c in claims if c.annex_effect == "contradicted_existing"
        ),
        "supporting_context_claims": sum(
            1 for c in claims if c.annex_effect == "supporting_context_only"
        ),
        "claims_with_accessory_risk": sum(1 for c in claims if c.risk_reason_codes),
        "change_class_counts": {
            change: sum(1 for c in comparisons if c.change_class == change)
            for change in CHANGE_CLASSES
        },
        "interpretation_counts": dict(
            sorted(Counter(c.interpretation for c in comparisons).items())
        ),
        "claims_by_category": dict(
            sorted(Counter(c.matched_category for c in claims).items())
        ),
        "coverage_debt": {
            code: debt_counter.get(code, 0) for code in COVERAGE_DEBT_OUTCOMES
        },
        "new_positive_by_format": dict(
            sorted(Counter(c.detected_format for c in new_positive).items())
        ),
        "new_positive_by_locator_type": dict(
            sorted(Counter(c.locator_type for c in new_positive).items())
        ),
        "new_positive_by_document_role": dict(
            sorted(Counter(c.document_role for c in new_positive).items())
        ),
        "new_positive_with_provenance": sum(
            1 for c in new_positive if c.locator_display and c.chunk_id
        ),
        "contact_authorization": False,
        "outreach_authorization": False,
        "production_queue_mutated": False,
        "persisted": False,
    }


def _dump_json(payload: Any, path: Path, *, where: str) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    assert_no_portal_tokens(text, where=where)
    path.write_text(text, encoding="utf-8")


def _dump_jsonl(rows: Sequence[dict[str, Any]], path: Path, *, where: str) -> None:
    lines = [json.dumps(row, sort_keys=True, ensure_ascii=True) for row in rows]
    text = "\n".join(lines) + ("\n" if lines else "")
    assert_no_portal_tokens(text, where=where)
    path.write_text(text, encoding="utf-8")


def write_comparison_outputs(
    result: ComparisonResult,
    out_dir: Path,
    *,
    repo_email_pipeline_root: Path,
    require_git_ignored: bool = True,
) -> dict[str, str]:
    """Atomically publish the gitignored review packet."""
    assert_read_only_invariants(result)

    def writer(staged: Path) -> dict[str, str]:
        summary_path = staged / "summary.json"
        rows_path = staged / "comparison_rows.jsonl"
        claims_path = staged / "annex_claims.jsonl"
        coverage_path = staged / "coverage_summary.json"
        walkthrough_path = staged / "walkthrough.md"

        _dump_json(build_summary(result), summary_path, where="summary.json")
        _dump_jsonl(
            [c.to_dict() for c in result.comparisons],
            rows_path,
            where="comparison_rows.jsonl",
        )
        _dump_jsonl(
            [c.to_dict() for c in result.claims],
            claims_path,
            where="annex_claims.jsonl",
        )
        _dump_json(
            {
                "coverage": [
                    c.coverage.to_dict()
                    for c in sorted(result.comparisons, key=lambda r: r.tender_id)
                ],
                "debt_outcomes_tracked": list(COVERAGE_DEBT_OUTCOMES),
            },
            coverage_path,
            where="coverage_summary.json",
        )

        markdown = render_walkthrough(result)
        assert_no_portal_tokens(markdown, where="walkthrough.md")
        walkthrough_path.write_text(markdown, encoding="utf-8")

        return {
            "summary": str(summary_path),
            "comparison_rows": str(rows_path),
            "annex_claims": str(claims_path),
            "coverage_summary": str(coverage_path),
            "walkthrough": str(walkthrough_path),
        }

    return write_atomically(
        out_dir,
        repo_email_pipeline_root=repo_email_pipeline_root,
        writer=writer,
        require_git_ignored=require_git_ignored,
    )
