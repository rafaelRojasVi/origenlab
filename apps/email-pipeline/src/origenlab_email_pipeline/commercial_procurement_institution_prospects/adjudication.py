"""Apply provisional dispositions to public procurement evidence (no tender-code rules)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.commercial_procurement_institution_prospects.catalog_scope import (
    VERIFIED_CATALOG_CLASSES,
    classify_provisional_disposition,
    refine_commercial_signal,
)
from origenlab_email_pipeline.commercial_procurement_live_relevance.constants import (
    STRONG_RELEVANCE_CLASSES,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
    ProductTextUnit,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.normalize import (
    normalize_product_text,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.rules import (
    classify_product_text_unit,
)

_NEGATIVE = frozenset(
    {
        "consumable_or_reagent",
        "service_or_maintenance_only",
        "rental_or_comodato",
        "non_laboratory_false_positive",
        "unrelated",
    }
)


def _unit(text: str, *, tier: str, field_path: str, idx: int) -> ProductTextUnit:
    return ProductTextUnit(
        unit_id=f"adj-unit-{idx}",
        coalesced_tender_id="adjudication",
        evidence_ref_id=None,
        link_status="linked",
        unresolved_reason=None,
        field_path=field_path,
        text_raw=text,
        text_normalized=normalize_product_text(text),
        evidence_tier=tier,
        source_plane="fixture",
        snapshot_id=None,
        observation_id=None,
        tender_observation_id=None,
        line_observation_id=None,
        pr4_procurement_id=None,
        contributing_evidence_ref_ids=(),
    )


def adjudicate_public_evidence(
    *,
    title: str,
    line_texts: list[str] | None = None,
) -> dict[str, Any]:
    """Classify one tender from public title + line texts only."""
    texts: list[tuple[str, str, str]] = [("title_only", "title", title)]
    for i, line in enumerate(line_texts or []):
        texts.append(("line_product_text", f"line[{i}]", line))

    unit_decs = [
        classify_product_text_unit(_unit(text, tier=tier, field_path=path, idx=i))
        for i, (tier, path, text) in enumerate(texts)
    ]
    strong = [d for d in unit_decs if d.relevance_class in STRONG_RELEVANCE_CLASSES]
    negatives = [d for d in unit_decs if d.relevance_class in _NEGATIVE]

    if strong:
        relevance = strong[0].relevance_class
        classes = tuple(
            sorted({c for d in strong for c in d.canonical_equipment_classes})
        )
    elif negatives:
        order = [
            "rental_or_comodato",
            "service_or_maintenance_only",
            "consumable_or_reagent",
            "non_laboratory_false_positive",
            "unrelated",
        ]
        chosen = sorted(
            negatives,
            key=lambda d: order.index(d.relevance_class)
            if d.relevance_class in order
            else 99,
        )[0]
        relevance = chosen.relevance_class
        classes = ()
    else:
        relevance = unit_decs[0].relevance_class
        classes = tuple(unit_decs[0].canonical_equipment_classes)

    negs = tuple(sorted({n for d in unit_decs for n in d.negative_reason_codes}))
    ambs = tuple(sorted({a for d in unit_decs for a in d.ambiguity_reason_codes}))
    signal, _ = refine_commercial_signal(
        relevance_class=relevance, title=title, negative_reason_codes=negs
    )
    disposition = classify_provisional_disposition(
        relevance_class=relevance,
        commercial_signal=signal,
        canonical_equipment_classes=classes,
        title=title,
        negative_reason_codes=negs,
        ambiguity_reason_codes=ambs,
    )
    disposition["relevance_class"] = relevance
    disposition["canonical_equipment_classes"] = [
        c for c in classes if c in VERIFIED_CATALOG_CLASSES or True
    ]
    return disposition


def load_reviewed_adjudication_fixture(
    path: Path | None = None,
) -> dict[str, Any]:
    if path is None:
        # .../src/origenlab_email_pipeline/commercial_procurement_institution_prospects
        # parents[3] == apps/email-pipeline
        path = (
            Path(__file__).resolve().parents[3]
            / "tests"
            / "fixtures"
            / "commercial_procurement_pr5e2_reviewed_adjudication.json"
        )
    return json.loads(path.read_text(encoding="utf-8"))


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
    "adjudicate_public_evidence",
    "evaluate_reviewed_adjudication_fixture",
    "load_reviewed_adjudication_fixture",
]
