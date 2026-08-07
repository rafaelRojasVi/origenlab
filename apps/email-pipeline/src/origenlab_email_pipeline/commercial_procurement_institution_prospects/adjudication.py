"""Apply provisional dispositions to public procurement evidence (no tender-code rules).

Production classification never reads analyst-reviewed labels. The comparison
harness that scores this module against a reviewed fixture lives in
``tests/helpers/pr5e2_adjudication.py``.
"""

from __future__ import annotations

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
    disposition["canonical_equipment_classes"] = list(classes)
    disposition["catalog_equipment_classes"] = [
        c for c in classes if c in VERIFIED_CATALOG_CLASSES
    ]
    return disposition


__all__ = ["adjudicate_public_evidence"]
