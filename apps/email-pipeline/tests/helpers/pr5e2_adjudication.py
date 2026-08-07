"""Score PR5E.2 dispositions against an analyst-reviewed fixture.

This harness is test-only on purpose: production classification must never read
reviewed labels, so the fixture path lives outside ``src/``.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.commercial_procurement_institution_prospects.adjudication import (
    adjudicate_public_evidence,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "commercial_procurement_pr5e2_reviewed_adjudication.json"
)


def load_reviewed_adjudication_fixture(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or FIXTURE_PATH).read_text(encoding="utf-8"))


def evaluate_reviewed_adjudication_fixture(
    fixture: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare deterministic dispositions to analyst_reviewed_provisional labels."""
    fixture = fixture or load_reviewed_adjudication_fixture()
    results: list[dict[str, Any]] = []
    for case in fixture["cases"]:
        lines = list(case.get("public_evidence", {}).get("line_texts") or [])
        for ref in case.get("relevant_line_references") or []:
            if ref.get("field_path") == "line" and ref.get("text"):
                if ref["text"] not in lines:
                    lines.append(ref["text"])
        predicted = adjudicate_public_evidence(
            title=case["public_evidence"]["tender_title"],
            line_texts=lines,
        )
        expected = case["review_disposition"]
        results.append(
            {
                "tender_code": case["tender_code"],
                "expected_disposition": expected,
                "predicted_disposition": predicted["review_disposition"],
                "match": predicted["review_disposition"] == expected,
                "predicted": predicted,
                "review_status": case.get("review_status"),
                "provenance": fixture.get("provenance"),
            }
        )
    counts = Counter(r["predicted_disposition"] for r in results)
    return {
        "provenance": fixture.get("provenance"),
        "not_gold_truth": True,
        "fixture_id": fixture.get("fixture_id"),
        "total": len(results),
        "matches": sum(1 for r in results if r["match"]),
        "predicted_disposition_counts": dict(sorted(counts.items())),
        "expected_disposition_counts": fixture.get("expected_disposition_counts"),
        "results": results,
    }


__all__ = [
    "FIXTURE_PATH",
    "evaluate_reviewed_adjudication_fixture",
    "load_reviewed_adjudication_fixture",
]
