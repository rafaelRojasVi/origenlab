"""Aggregate equipment history per institution across live + PR4 planes."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    CoalescedProcurementTender,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.event_families import (
    FAMILY_CONFIRMED,
    FAMILY_REVIEW,
    FAMILY_SINGLE,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.line_claims import (
    LineClaim,
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


FAMILY_NOT_APPLIED = "family_resolution_not_applied"


def _fam_field(family: Any, key: str, default: Any = None) -> Any:
    if isinstance(family, dict):
        return family.get(key, default)
    return getattr(family, key, default)


def _claims_for(
    claims_by_tender: dict[str, Any] | None, tender_id: str
) -> list[LineClaim] | None:
    """Claims recorded for one tender, or None when the caller supplied none.

    An empty claim list for a tender that was never claim-classified must fall
    back to the tender decision. Only an explicit empty list for a tender that
    *was* claim-classified (key present) means "lines named no category".
    """
    if claims_by_tender is None:
        return None
    if tender_id not in claims_by_tender:
        return None
    return list(claims_by_tender.get(tender_id) or ())


def _category_claim_facts(claims: Iterable[LineClaim]) -> dict[str, dict[str, Any]]:
    """Per-category facts taken only from the claims that named that category."""
    facts: dict[str, dict[str, Any]] = {}
    for claim in sorted(claims, key=lambda c: (c.unit_id, c.clause_index)):
        for category in claim.canonical_equipment_classes:
            name = str(category)
            if not name or name.startswith("relevance:"):
                continue
            entry = facts.setdefault(
                name,
                {
                    "scopes": set(),
                    "commercial_signals": set(),
                    "catalog_match_statuses": set(),
                    "positive_reason_codes": set(),
                    "negative_reason_codes": set(),
                    "ambiguity_reason_codes": set(),
                    "evidence_ref_ids": set(),
                    "clause_texts": [],
                    "claim_ids": [],
                },
            )
            entry["scopes"].add(claim.equipment_scope)
            entry["commercial_signals"].add(claim.commercial_signal_type)
            if claim.catalog_match_status:
                entry["catalog_match_statuses"].add(claim.catalog_match_status)
            entry["positive_reason_codes"].update(claim.positive_reason_codes)
            entry["negative_reason_codes"].update(claim.negative_reason_codes)
            entry["ambiguity_reason_codes"].update(claim.ambiguity_reason_codes)
            entry["evidence_ref_ids"].update(claim.contributing_evidence_ref_ids)
            if claim.evidence_ref_id:
                entry["evidence_ref_ids"].add(claim.evidence_ref_id)
            # The clause the buyer wrote is the evidence, not a matched fragment.
            if claim.clause_text and len(entry["clause_texts"]) < 20:
                entry["clause_texts"].append(str(claim.clause_text)[:240])
            entry["claim_ids"].append(claim.claim_id)
    return facts


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
    family_ids: set[str] = field(default_factory=set)
    family_independent_events: dict[str, int] = field(default_factory=dict)
    family_unresolved_relationships: dict[str, int] = field(default_factory=dict)
    family_statuses: set[str] = field(default_factory=set)
    family_reason_codes: set[str] = field(default_factory=set)
    equipment_scopes: set[str] = field(default_factory=set)


def aggregate_equipment_history(
    *,
    tenders: Iterable[CoalescedProcurementTender],
    decisions_by_tender: dict[str, TenderRelevanceDecision],
    institution_id_by_tender: dict[str, str],
    lifecycle_by_tender: dict[str, str] | None = None,
    family_by_tender: dict[str, Any] | None = None,
    claim_axes_by_tender: dict[str, dict[str, Any]] | None = None,
    claims_by_tender: dict[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """
    Return institution_id → list of category history dicts.

    Consumables / maintenance / rental are recorded but do not inflate purchase counts
    at the profile level (caller uses commercial_signal).

    ``lifecycle_by_tender`` supplies the PR5E.2 projected lifecycle class per
    coalesced tender id; when omitted the tender's own ``lifecycle_class`` is used.
    ``family_by_tender`` supplies procurement-event family metadata per tender so
    recurrence is driven by independent demand events rather than raw tender count.
    ``claims_by_tender`` supplies category-scoped line claims. When present each
    category records only the scopes and signals of the claims that named it, so a
    maintenance clause cannot lend a purchase signal to a different instrument and
    a title verdict cannot invent demand the lines never expressed.
    """
    acc: dict[str, dict[str, _CategoryAcc]] = defaultdict(dict)
    families_applied = family_by_tender is not None

    for tender in sorted(tenders, key=lambda t: t.coalesced_tender_id):
        iid = institution_id_by_tender.get(tender.coalesced_tender_id)
        if not iid:
            continue
        decision = decisions_by_tender.get(tender.coalesced_tender_id)
        if decision is None:
            continue

        claims = _claims_for(claims_by_tender, tender.coalesced_tender_id)
        claims_drive = claims is not None and len(claims) > 0
        axes = (claim_axes_by_tender or {}).get(tender.coalesced_tender_id) or {}
        if claims_drive:
            per_category = _category_claim_facts(claims or ())
            classes = sorted(per_category)
        else:
            per_category = {}
            # Line claims name what was actually requested; fall back to the tender
            # decision when no category-scoped claims exist for this tender.
            classes = [
                c
                for c in (
                    list(axes.get("canonical_equipment_classes") or ())
                    or list(decision.canonical_equipment_classes)
                )
                if c and not str(c).startswith("relevance:")
            ]
        if not classes:
            continue
        signal = classify_commercial_evidence_signal(decision.relevance_class)
        code = _tender_code(tender)
        url = build_mercado_publico_search_url(code) if code else None
        obs = _observation_date(tender)
        line_ids = set(decision.contributing_evidence_ref_ids)
        snippets: list[str] = []
        if not claims_drive:
            for span in decision.matched_spans[:5]:
                text = getattr(span, "matched_text", None) or getattr(
                    span, "span_text", None
                )
                if text:
                    snippets.append(str(text)[:240])

        lifecycle = (lifecycle_by_tender or {}).get(
            tender.coalesced_tender_id, tender.lifecycle_class
        )
        family = (family_by_tender or {}).get(tender.coalesced_tender_id)

        for category in classes:
            bucket = acc[iid].setdefault(category, _CategoryAcc(category=category))
            bucket.tender_ids.add(tender.coalesced_tender_id)
            bucket.line_evidence_ids |= line_ids
            if obs:
                bucket.dates.append(obs)
            if is_open_lifecycle(lifecycle):
                bucket.open_tender_ids.add(tender.coalesced_tender_id)
            if is_historical_lifecycle(lifecycle):
                bucket.historical_tender_ids.add(tender.coalesced_tender_id)
            bucket.tender_codes.add(code)
            if url:
                bucket.urls.add(url)
            bucket.relevance_classes.add(decision.relevance_class)
            bucket.evidence_tiers.add(decision.evidence_tier)
            if claims_drive:
                facts = per_category[category]
                bucket.line_snippets.extend(facts["clause_texts"])
                bucket.line_evidence_ids |= facts["evidence_ref_ids"]
                bucket.positive_reason_codes.update(facts["positive_reason_codes"])
                bucket.negative_reason_codes.update(facts["negative_reason_codes"])
                bucket.ambiguity_reason_codes.update(facts["ambiguity_reason_codes"])
                bucket.commercial_signals.update(facts["commercial_signals"])
                bucket.catalog_fit_statuses.update(facts["catalog_match_statuses"])
                bucket.equipment_scopes.update(facts["scopes"])
            else:
                bucket.line_snippets.extend(snippets)
                bucket.positive_reason_codes.update(decision.positive_reason_codes)
                bucket.negative_reason_codes.update(decision.negative_reason_codes)
                bucket.ambiguity_reason_codes.update(decision.ambiguity_reason_codes)
                bucket.commercial_signals.add(signal)
                bucket.catalog_fit_statuses.add(decision.product_resolution_status)
                bucket.equipment_scopes.update(
                    str(s) for s in (axes.get("equipment_scopes") or ())
                )
            if family is not None:
                fid = str(_fam_field(family, "family_id") or "")
                if fid:
                    bucket.family_ids.add(fid)
                    bucket.family_independent_events[fid] = int(
                        _fam_field(family, "independent_demand_event_count", 0) or 0
                    )
                    bucket.family_unresolved_relationships[fid] = int(
                        _fam_field(family, "unresolved_relationship_count", 0) or 0
                    )
                status = _fam_field(family, "family_resolution_status")
                if status:
                    bucket.family_statuses.add(str(status))
                bucket.family_reason_codes.update(
                    str(c) for c in (_fam_field(family, "family_reason_codes") or ())
                )

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
            if families_applied:
                family_count = len(a.family_ids)
                independent = sum(a.family_independent_events.values())
                unresolved = sum(a.family_unresolved_relationships.values())
                if FAMILY_REVIEW in a.family_statuses:
                    family_status = FAMILY_REVIEW
                elif FAMILY_CONFIRMED in a.family_statuses:
                    family_status = FAMILY_CONFIRMED
                else:
                    family_status = FAMILY_SINGLE
                recurrence = recurrence_label(
                    distinct,
                    independent_demand_event_count=independent,
                    family_resolution_status=family_status,
                    unresolved_relationship_count=unresolved,
                )
            else:
                family_count = distinct
                independent = distinct
                unresolved = 0
                family_status = FAMILY_NOT_APPLIED
                recurrence = recurrence_label(distinct)
            rows.append(
                {
                    "canonical_equipment_category": category,
                    "distinct_tender_count": distinct,
                    "raw_tender_count": distinct,
                    "procurement_event_family_count": family_count,
                    "independent_demand_event_count": independent,
                    "unresolved_relationship_count": unresolved,
                    "equipment_scopes": sorted(a.equipment_scopes),
                    "family_resolution_status": family_status,
                    "family_reason_codes": sorted(a.family_reason_codes),
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
                    "demand_recurrence": recurrence,
                }
            )
        out[iid] = rows
    return out


__all__ = ["FAMILY_NOT_APPLIED", "aggregate_equipment_history"]
