"""Human-readable ANEXO-E2 walkthrough.

Shows what annex evidence changed and what it failed to read. Coverage-debt and
unchanged cases are rendered alongside the wins on purpose: a review packet that
only shows improvements cannot be used to judge whether the lane is safe.
"""

from __future__ import annotations

from collections.abc import Sequence

from .constants import (
    CHANGE_ANNEX_CHANGED_CATEGORY,
    CHANGE_ANNEX_CREATED_CONFLICT,
    CHANGE_ANNEX_INCOMPLETE_NO_CONCLUSION,
    CHANGE_ANNEX_STRENGTHENED_EXISTING,
    CHANGE_BASELINE_POSITIVE_ANNEX_INCOMPLETE,
    CHANGE_NEW_ANNEX_POSITIVE,
    CHANGE_UNCHANGED_NEGATIVE_OR_UNRESOLVED,
    CHANGE_UNCHANGED_POSITIVE,
    INTERPRETATION_ABSENCE_UNPROVEN,
)
from .models import AnexoClaim, ComparisonResult, TenderComparison


MAX_CASES_PER_SECTION = 8
MAX_CLAIMS_PER_CASE = 12


def _fmt(values: Sequence[str]) -> str:
    return ", ".join(values) if values else "unresolved"


def _coverage_line(comparison: TenderComparison) -> str:
    coverage = comparison.coverage
    if not coverage.has_anexo_bundle:
        return "no anexo bundle available"
    if coverage.is_complete:
        return "complete"
    parts = []
    if coverage.debt_outcome_counts:
        parts.append(
            ", ".join(
                f"{code}={count}"
                for code, count in sorted(coverage.debt_outcome_counts.items())
            )
        )
    if coverage.incomplete_reason_codes:
        parts.append("reasons: " + ", ".join(coverage.incomplete_reason_codes))
    return "INCOMPLETE (" + "; ".join(parts) + ")" if parts else "INCOMPLETE"


def _render_case(
    comparison: TenderComparison, claims: Sequence[AnexoClaim], lines: list[str]
) -> None:
    lines.append("-" * 66)
    lines.append(f"Tender: {comparison.tender_id}")
    lines.append(f"Buyer: {comparison.buyer_organization or 'unknown'}")
    if comparison.tender_title:
        lines.append(f"Title: {comparison.tender_title[:160]}")
    lines.append("")
    lines.append("BASELINE:")
    lines.append(f"  category: {_fmt(comparison.baseline_categories)}")
    lines.append(
        f"  relevance: {comparison.baseline_relevance_class} "
        f"({comparison.baseline_confidence_band})"
    )
    lines.append(f"  evidence units: {comparison.baseline_unit_count}")
    lines.append("")

    if claims:
        lines.append("ANNEX:")
        for claim in claims[:MAX_CLAIMS_PER_CASE]:
            lines.append(f"  attachment: {claim.safe_filename} [{claim.document_role}]")
            lines.append(f"  locator: {claim.locator_display}")
            lines.append(f"  category: {claim.matched_category} ({claim.annex_effect})")
            excerpt = " ".join(claim.evidence_excerpt.split())[:180]
            lines.append(f"  evidence: {excerpt!r}")
            lines.append("")
        if len(claims) > MAX_CLAIMS_PER_CASE:
            lines.append(f"  ... {len(claims) - MAX_CLAIMS_PER_CASE} further claims")
            lines.append("")
    else:
        lines.append("ANNEX:")
        lines.append(f"  attachments: {comparison.annex_attachment_count}")
        lines.append(f"  segments searched: {comparison.annex_segment_count}")
        lines.append("  no equipment category recognized")
        lines.append("")

    lines.append("AUGMENTED:")
    lines.append(f"  category: {_fmt(comparison.augmented_categories)}")
    lines.append(
        f"  relevance: {comparison.augmented_relevance_class} "
        f"({comparison.augmented_confidence_band})"
    )
    lines.append("")
    lines.append(f"CHANGE: {comparison.change_class}")
    lines.append(f"COVERAGE: {_coverage_line(comparison)}")
    lines.append(f"INTERPRETATION: {comparison.interpretation}")
    lines.append("")


def render_walkthrough(result: ComparisonResult) -> str:
    claims_by_tender: dict[str, list[AnexoClaim]] = {}
    for claim in result.claims:
        claims_by_tender.setdefault(claim.tender_id, []).append(claim)

    lines: list[str] = [
        "# ANEXO-E2 — annex-backed equipment recognition comparison",
        "",
        "Read-only measurement lane. Baseline and augmented recognition run the",
        "same PR5D rules over the same tenders; only the evidence set differs.",
        "Nothing here changes production queues, ranking, or authorization.",
        "",
        "**Annex evidence may prove presence. Incomplete extraction never proves absence.**",
        "",
        f"- comparison version: `{result.comparison_version}`",
        f"- rules version: `{result.rules_version}`",
        f"- semantic digest: `{result.semantic_digest}`",
        f"- tenders evaluated: {len(result.comparisons)}",
        f"- annex claims: {len(result.claims)}",
        "",
    ]

    sections = (
        ("New annex positives", CHANGE_NEW_ANNEX_POSITIVE),
        ("Annex changed the recognized category set", CHANGE_ANNEX_CHANGED_CATEGORY),
        ("Annex strengthened an existing claim", CHANGE_ANNEX_STRENGTHENED_EXISTING),
        ("Annex created a conflict", CHANGE_ANNEX_CREATED_CONFLICT),
        (
            "Baseline positive, annex coverage incomplete",
            CHANGE_BASELINE_POSITIVE_ANNEX_INCOMPLETE,
        ),
        (
            "No conclusion possible (incomplete extraction)",
            CHANGE_ANNEX_INCOMPLETE_NO_CONCLUSION,
        ),
        ("Unchanged positive", CHANGE_UNCHANGED_POSITIVE),
        ("Unchanged negative or unresolved", CHANGE_UNCHANGED_NEGATIVE_OR_UNRESOLVED),
    )

    for title, change_class in sections:
        matching = [c for c in result.comparisons if c.change_class == change_class]
        lines.append(f"## {title} ({len(matching)})")
        lines.append("")
        if not matching:
            lines.append("_none in this corpus._")
            lines.append("")
            continue
        for comparison in matching[:MAX_CASES_PER_SECTION]:
            _render_case(comparison, claims_by_tender.get(comparison.tender_id, []), lines)
        if len(matching) > MAX_CASES_PER_SECTION:
            lines.append(
                f"_... {len(matching) - MAX_CASES_PER_SECTION} further tenders in "
                f"`comparison_rows.jsonl`._"
            )
            lines.append("")

    unproven = [
        c for c in result.comparisons if c.interpretation == INTERPRETATION_ABSENCE_UNPROVEN
    ]
    lines.append(f"## Absence unproven ({len(unproven)})")
    lines.append("")
    lines.append(
        "These tenders produced no annex equipment claim, but part of the annex "
        "corpus could not be read. They must not be treated as evidence that the "
        "annexes contain no matching equipment."
    )
    lines.append("")
    for comparison in unproven[:MAX_CASES_PER_SECTION]:
        lines.append(
            f"- `{comparison.tender_id}` — {_coverage_line(comparison)}"
        )
    lines.append("")
    return "\n".join(lines) + "\n"
