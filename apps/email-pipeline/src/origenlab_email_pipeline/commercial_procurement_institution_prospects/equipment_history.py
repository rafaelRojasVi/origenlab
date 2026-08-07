"""Aggregate equipment history per institution across live + PR4 planes."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    CoalescedProcurementTender,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.overlay import (
    is_historical_lifecycle,
    is_open_lifecycle,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.signals import (
    classify_commercial_evidence_signal,
    is_equipment_purchase_signal,
    recurrence_label,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
    TenderRelevanceDecision,
)
from origenlab_email_pipeline.equipment_first_chilecompra_publish import (
    build_mercado_publico_search_url,
)


def _tender_code(tender: CoalescedProcurementTender) -> str:
    key = tender.canonical_tender_key or tender.coalesced_tender_id
    # Prefer ChileCompra-looking keys without namespace prefixes when present.
    if ":" in key:
        return key.split(":", 1)[-1]
    return key


def _observation_date(tender: CoalescedProcurementTender) -> str | None:
    return (
        tender.publication_timestamp_selected
        or tender.close_timestamp_selected
        or None
    )


@dataclass
class _CategoryAcc:
    category: str
    tender_ids: set[str] = field(default_factory=set)
    line_evidence_ids: set[str] = field(default_factory=set)
    dates: list[str] = field(default_factory=list)
    open_tender_ids: set[str] = field(default_factory=set)
    historical_tender_ids: set[str] = field(default_factory=set)
    tender_codes: set[str] = field(default_factory=set)
    urls: set[str] = field(default_factory=set)
    line_snippets: list[str] = field(default_factory=list)
    relevance_classes: set[str] = field(default_factory=set)
    evidence_tiers: set[str] = field(default_factory=set)
    positive_reason_codes: set[str] = field(default_factory=set)
    negative_reason_codes: set[str] = field(default_factory=set)
    ambiguity_reason_codes: set[str] = field(default_factory=set)
    commercial_signals: set[str] = field(default_factory=set)
    catalog_fit_statuses: set[str] = field(default_factory=set)


def aggregate_equipment_history(
    *,
    tenders: Iterable[CoalescedProcurementTender],
    decisions_by_tender: dict[str, TenderRelevanceDecision],
    institution_id_by_tender: dict[str, str],
) -> dict[str, list[dict[str, Any]]]:
    """
    Return institution_id → list of category history dicts.
    Consumables / maintenance / rental are recorded but do not inflate purchase counts
    at the profile level (caller uses commercial_signal).
    """
    acc: dict[str, dict[str, _CategoryAcc]] = defaultdict(dict)

    for tender in sorted(tenders, key=lambda t: t.coalesced_tender_id):
        iid = institution_id_by_tender.get(tender.coalesced_tender_id)
        if not iid:
            continue
        decision = decisions_by_tender.get(tender.coalesced_tender_id)
        if decision is None:
            continue

        signal = classify_commercial_evidence_signal(decision.relevance_class)
        classes = list(decision.canonical_equipment_classes) or [
            f"relevance:{decision.relevance_class}"
        ]
        code = _tender_code(tender)
        url = build_mercado_publico_search_url(code) if code else None
        obs = _observation_date(tender)
        line_ids = set(decision.contributing_evidence_ref_ids)
        snippets: list[str] = []
        for span in decision.matched_spans[:5]:
            text = getattr(span, "matched_text", None) or getattr(span, "span_text", None)
            if text:
                snippets.append(str(text)[:240])

        for category in classes:
            bucket = acc[iid].setdefault(category, _CategoryAcc(category=category))
            bucket.tender_ids.add(tender.coalesced_tender_id)
            bucket.line_evidence_ids |= line_ids
            if obs:
                bucket.dates.append(obs)
            if is_open_lifecycle(tender.lifecycle_class):
                bucket.open_tender_ids.add(tender.coalesced_tender_id)
            if is_historical_lifecycle(tender.lifecycle_class):
                bucket.historical_tender_ids.add(tender.coalesced_tender_id)
            bucket.tender_codes.add(code)
            if url:
                bucket.urls.add(url)
            bucket.line_snippets.extend(snippets)
            bucket.relevance_classes.add(decision.relevance_class)
            bucket.evidence_tiers.add(decision.evidence_tier)
            bucket.positive_reason_codes.update(decision.positive_reason_codes)
            bucket.negative_reason_codes.update(decision.negative_reason_codes)
            bucket.ambiguity_reason_codes.update(decision.ambiguity_reason_codes)
            bucket.commercial_signals.add(signal)
            bucket.catalog_fit_statuses.add(decision.product_resolution_status)

    out: dict[str, list[dict[str, Any]]] = {}
    for iid, cats in acc.items():
        rows: list[dict[str, Any]] = []
        for category in sorted(cats):
            a = cats[category]
            distinct = len(a.tender_ids)
            dates_sorted = sorted(d for d in a.dates if d)
            purchase_signals = {
                s for s in a.commercial_signals if is_equipment_purchase_signal(s)
            }
            rows.append(
                {
                    "canonical_equipment_category": category,
                    "distinct_tender_count": distinct,
                    "distinct_line_evidence_count": len(a.line_evidence_ids),
                    "first_observed_date": dates_sorted[0] if dates_sorted else None,
                    "most_recent_observed_date": dates_sorted[-1]
                    if dates_sorted
                    else None,
                    "open_current_tender_count": len(a.open_tender_ids),
                    "historical_tender_count": len(a.historical_tender_ids),
                    "tender_codes": sorted(a.tender_codes),
                    "mercado_publico_urls": sorted(a.urls),
                    "line_description_snippets": a.line_snippets[:20],
                    "relevance_classes": sorted(a.relevance_classes),
                    "evidence_tiers": sorted(a.evidence_tiers),
                    "positive_reason_codes": sorted(a.positive_reason_codes),
                    "negative_reason_codes": sorted(a.negative_reason_codes),
                    "ambiguity_reason_codes": sorted(a.ambiguity_reason_codes),
                    "catalog_fit_statuses": sorted(a.catalog_fit_statuses),
                    "commercial_evidence_signals": sorted(a.commercial_signals),
                    "has_equipment_purchase_signal": bool(purchase_signals),
                    "demand_recurrence": recurrence_label(distinct),
                }
            )
        out[iid] = rows
    return out


__all__ = ["aggregate_equipment_history"]
