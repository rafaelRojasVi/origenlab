"""Diff CURRENT vs SHADOW institution prospect plans (in memory only)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from origenlab_email_pipeline.commercial_procurement_anexo_recognition.models import (
    AnexoClaim,
    TenderComparison,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.constants import (
    OPERATOR_QUEUE_NAMES,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.planner import (
    InstitutionProspectPlanResult,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
    TenderRelevanceDecision,
)

from .classify import (
    claim_capabilities_for,
    classify_shadow_change,
    commercial_intent_class,
)
from .constants import (
    QUEUE_UNCHANGED,
    QUEUE_WOULD_CHANGE_PRIORITY,
    QUEUE_WOULD_ENTER_CONTACT_GAP,
    QUEUE_WOULD_ENTER_CURRENT,
    QUEUE_WOULD_ENTER_MATCH_REVIEW,
    QUEUE_WOULD_LEAVE_CURRENT,
)
from .models import InstitutionShadowDelta, QueueShadowDelta, TenderShadowDelta
from .plans import decision_summary


def _queue_row_key(queue_name: str, row: Mapping[str, Any]) -> str:
    if queue_name == "current_opportunity_queue":
        return "|".join(
            [
                str(row.get("institution_id") or ""),
                str(row.get("tender_code") or row.get("coalesced_tender_id") or ""),
                str(row.get("equipment_category") or ""),
            ]
        )
    if queue_name == "historical_prospect_queue":
        return "|".join(
            [
                str(row.get("institution_id") or ""),
                str(row.get("equipment_category") or ""),
                str(row.get("commercial_signal_type") or ""),
            ]
        )
    if queue_name == "contact_gap_queue":
        return str(row.get("institution_id") or "")
    if queue_name == "institution_match_review_queue":
        return "|".join(
            [
                str(row.get("institution_id") or ""),
                str(row.get("institution_review_cluster_id") or ""),
            ]
        )
    return str(row.get("queue_row_id") or row.get("coalesced_tender_id") or "")


def _index_queues(
    plan: InstitutionProspectPlanResult,
) -> dict[str, dict[str, dict[str, Any]]]:
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for name in OPERATOR_QUEUE_NAMES:
        indexed: dict[str, dict[str, Any]] = {}
        for row in plan.operator_queues.get(name, []):
            indexed[_queue_row_key(name, row)] = row
        out[name] = indexed
    return out


def _memberships_for_tender(
    plan: InstitutionProspectPlanResult, tender_id: str
) -> tuple[str, ...]:
    names: list[str] = []
    for queue_name, rows in plan.operator_queues.items():
        for row in rows:
            tid = str(row.get("coalesced_tender_id") or row.get("tender_code") or "")
            codes = row.get("tender_codes") or []
            if tid == tender_id or tender_id in codes:
                names.append(queue_name)
                break
    return tuple(sorted(set(names)))


def _tender_rows_by_id(
    plan: InstitutionProspectPlanResult,
) -> dict[str, list[dict[str, Any]]]:
    by: dict[str, list[dict[str, Any]]] = {}
    for row in plan.tender_rows:
        tid = str(row.get("coalesced_tender_id") or "")
        by.setdefault(tid, []).append(row)
    return by


def _primary_disposition(rows: Sequence[Mapping[str, Any]]) -> str | None:
    if not rows:
        return None
    # Prefer purchase-like rows.
    for row in rows:
        if row.get("commercial_signal_type") == "equipment_purchase_signal":
            return str(row.get("review_disposition") or "") or None
    return str(rows[0].get("review_disposition") or "") or None


def _profile_by_institution(
    plan: InstitutionProspectPlanResult,
) -> dict[str, dict[str, Any]]:
    return {
        str(p.get("institution_id")): p
        for p in plan.profiles
        if p.get("institution_id")
    }


def build_queue_deltas(
    current: InstitutionProspectPlanResult,
    shadow: InstitutionProspectPlanResult,
) -> tuple[QueueShadowDelta, ...]:
    cur_q = _index_queues(current)
    sh_q = _index_queues(shadow)
    deltas: list[QueueShadowDelta] = []

    for queue_name in OPERATOR_QUEUE_NAMES:
        keys = set(cur_q[queue_name]) | set(sh_q[queue_name])
        for key in sorted(keys):
            c_row = cur_q[queue_name].get(key)
            s_row = sh_q[queue_name].get(key)
            c_pri = (c_row or {}).get("opportunity_urgency_band") or (c_row or {}).get(
                "prospect_strength_band"
            )
            s_pri = (s_row or {}).get("opportunity_urgency_band") or (s_row or {}).get(
                "prospect_strength_band"
            )
            if c_row and not s_row:
                if queue_name == "current_opportunity_queue":
                    delta = QUEUE_WOULD_LEAVE_CURRENT
                else:
                    delta = QUEUE_UNCHANGED
            elif s_row and not c_row:
                if queue_name == "current_opportunity_queue":
                    delta = QUEUE_WOULD_ENTER_CURRENT
                elif queue_name == "institution_match_review_queue":
                    delta = QUEUE_WOULD_ENTER_MATCH_REVIEW
                elif queue_name == "contact_gap_queue":
                    delta = QUEUE_WOULD_ENTER_CONTACT_GAP
                else:
                    delta = QUEUE_UNCHANGED
            elif c_row and s_row and c_pri != s_pri:
                delta = QUEUE_WOULD_CHANGE_PRIORITY
            else:
                delta = QUEUE_UNCHANGED
            if delta == QUEUE_UNCHANGED and c_row and s_row:
                continue
            if delta == QUEUE_UNCHANGED and not (c_row and s_row):
                # Skip pure no-ops for non-tracked queues.
                if queue_name not in {
                    "current_opportunity_queue",
                    "institution_match_review_queue",
                    "contact_gap_queue",
                }:
                    continue
            sample = s_row or c_row or {}
            deltas.append(
                QueueShadowDelta(
                    queue_name=queue_name,
                    row_key=key,
                    delta_class=delta,
                    current_present=bool(c_row),
                    shadow_present=bool(s_row),
                    current_priority=str(c_pri) if c_pri else None,
                    shadow_priority=str(s_pri) if s_pri else None,
                    tender_id=str(sample.get("coalesced_tender_id") or "") or None,
                    institution_id=str(sample.get("institution_id") or "") or None,
                    equipment_category=str(sample.get("equipment_category") or "")
                    or None,
                )
            )
    return tuple(deltas)


def build_institution_deltas(
    current: InstitutionProspectPlanResult,
    shadow: InstitutionProspectPlanResult,
) -> tuple[InstitutionShadowDelta, ...]:
    cur_p = _profile_by_institution(current)
    sh_p = _profile_by_institution(shadow)
    ids = sorted(set(cur_p) | set(sh_p))
    out: list[InstitutionShadowDelta] = []
    for iid in ids:
        c = cur_p.get(iid, {})
        s = sh_p.get(iid, {})
        c_hist = len(c.get("equipment_history") or [])
        s_hist = len(s.get("equipment_history") or [])
        c_str = c.get("prospect_strength_band")
        s_str = s.get("prospect_strength_band")
        strength_delta = None
        if c_str != s_str:
            strength_delta = f"{c_str}->{s_str}"
        changed = bool(
            c_str != s_str
            or c.get("opportunity_urgency_band") != s.get("opportunity_urgency_band")
            or c_hist != s_hist
            or c.get("operator_next_action") != s.get("operator_next_action")
        )
        tender_ids = tuple(
            sorted(
                set(c.get("tender_ids") or [])
                | set(s.get("tender_ids") or [])
                | {
                    str(t.get("coalesced_tender_id"))
                    for t in (c.get("tenders") or []) + (s.get("tenders") or [])
                    if isinstance(t, dict) and t.get("coalesced_tender_id")
                }
            )
        )
        out.append(
            InstitutionShadowDelta(
                institution_id=iid,
                display_name=str(
                    s.get("display_name") or c.get("display_name") or iid
                ),
                current_prospect_strength=str(c_str) if c_str else None,
                shadow_prospect_strength=str(s_str) if s_str else None,
                strength_delta=strength_delta,
                current_urgency=str(c.get("opportunity_urgency_band") or "") or None,
                shadow_urgency=str(s.get("opportunity_urgency_band") or "") or None,
                current_equipment_history_count=c_hist,
                shadow_equipment_history_count=s_hist,
                history_delta=s_hist - c_hist,
                current_operator_next_action=str(c.get("operator_next_action") or "")
                or None,
                shadow_operator_next_action=str(s.get("operator_next_action") or "")
                or None,
                contact_readiness=str(
                    s.get("contact_readiness_band") or c.get("contact_readiness_band") or ""
                )
                or None,
                tender_ids=tender_ids,
                changed=changed,
            )
        )
    return tuple(out)


def build_tender_deltas(
    *,
    tenders: Sequence[dict[str, Any]],
    current: InstitutionProspectPlanResult,
    shadow: InstitutionProspectPlanResult,
    current_decisions: Mapping[str, TenderRelevanceDecision],
    shadow_decisions: Mapping[str, TenderRelevanceDecision],
    e2_comparisons: Sequence[TenderComparison],
    e2_claims: Sequence[AnexoClaim],
    queue_deltas: Sequence[QueueShadowDelta],
) -> tuple[TenderShadowDelta, ...]:
    e2_by = {c.tender_id: c for c in e2_comparisons}
    claims_by: dict[str, list[AnexoClaim]] = {}
    for claim in e2_claims:
        claims_by.setdefault(claim.tender_id, []).append(claim)

    cur_rows = _tender_rows_by_id(current)
    sh_rows = _tender_rows_by_id(shadow)
    cur_profiles = _profile_by_institution(current)
    sh_profiles = _profile_by_institution(shadow)

    # Map tender -> institution via rows or profiles.
    tender_institution: dict[str, str] = {}
    for plan in (current, shadow):
        for row in plan.tender_rows:
            tid = str(row.get("coalesced_tender_id") or "")
            iid = str(row.get("institution_id") or "")
            if tid and iid:
                tender_institution[tid] = iid

    queue_delta_by_tender: dict[str, set[str]] = {}
    for qd in queue_deltas:
        if qd.tender_id:
            queue_delta_by_tender.setdefault(qd.tender_id, set()).add(qd.delta_class)

    out: list[TenderShadowDelta] = []
    for tender in sorted(tenders, key=lambda t: str(t["tender_id"])):
        tid = str(tender["tender_id"])
        e2 = e2_by.get(tid)
        c_dec = current_decisions[tid]
        s_dec = shadow_decisions[tid]
        c_classes = tuple(sorted(c_dec.canonical_equipment_classes))
        s_classes = tuple(sorted(s_dec.canonical_equipment_classes))
        new_classes = tuple(sorted(set(s_classes) - set(c_classes)))
        caps = claim_capabilities_for(s_classes)

        coverage_complete = bool(e2.coverage.is_complete) if e2 else True
        has_bundle = bool(e2.coverage.has_anexo_bundle) if e2 else False
        debt = dict(e2.coverage.debt_outcome_counts) if e2 else {}
        coverage_status = (
            "complete"
            if coverage_complete
            else ("incomplete" if has_bundle else "no_anexo")
        )

        intent = commercial_intent_class(
            relevance_class=s_dec.relevance_class,
            negative_reason_codes=s_dec.negative_reason_codes,
            positive_reason_codes=s_dec.positive_reason_codes,
            ambiguity_reason_codes=s_dec.ambiguity_reason_codes,
            coverage_complete=coverage_complete,
            has_anexo_bundle=has_bundle,
            equipment_classes=s_classes,
        )

        c_mem = _memberships_for_tender(current, tid)
        s_mem = _memberships_for_tender(shadow, tid)
        c_keys = {
            _queue_row_key("current_opportunity_queue", r)
            for r in current.operator_queues.get("current_opportunity_queue", [])
            if str(r.get("coalesced_tender_id") or "") == tid
        }
        s_keys = {
            _queue_row_key("current_opportunity_queue", r)
            for r in shadow.operator_queues.get("current_opportunity_queue", [])
            if str(r.get("coalesced_tender_id") or "") == tid
        }

        human_review = bool(
            s_dec.relevance_class in {"ambiguous", "mixed_requires_review"}
            or (e2 and e2.change_class == "annex_created_conflict")
            or s_dec.product_resolution_status == "mixed_requires_review"
        )

        change = classify_shadow_change(
            current_classes=c_classes,
            shadow_classes=s_classes,
            capabilities=caps,
            intent=intent,
            e2_change_class=e2.change_class if e2 else None,
            e2_interpretation=e2.interpretation if e2 else None,
            coverage_complete=coverage_complete,
            has_anexo_bundle=has_bundle,
            current_queue_keys=c_keys,
            shadow_queue_keys=s_keys,
            human_review_required=human_review,
        )

        qdelta = tuple(
            sorted(
                queue_delta_by_tender.get(tid)
                or (
                    {QUEUE_WOULD_ENTER_CURRENT}
                    if s_keys - c_keys
                    else (
                        {QUEUE_WOULD_LEAVE_CURRENT}
                        if c_keys - s_keys
                        else {QUEUE_UNCHANGED}
                    )
                )
            )
        )

        iid = tender_institution.get(tid)
        c_prof = cur_profiles.get(iid or "", {})
        s_prof = sh_profiles.get(iid or "", {})
        c_str = c_prof.get("prospect_strength_band")
        s_str = s_prof.get("prospect_strength_band")
        strength_delta = None if c_str == s_str else f"{c_str}->{s_str}"

        claims = claims_by.get(tid, [])
        provenance = tuple(
            {
                "claim_id": cl.claim_id,
                "matched_category": cl.matched_category,
                "attachment_id": cl.attachment_id,
                "attachment_sha256": cl.attachment_sha256,
                "chunk_id": cl.chunk_id,
                "segment_id": cl.segment_id,
                "locator_display": cl.locator_display,
                "annex_effect": cl.annex_effect,
                "evidence_text_sha256": cl.evidence_text_sha256,
            }
            for cl in sorted(claims, key=lambda c: c.claim_id)
        )

        out.append(
            TenderShadowDelta(
                tender_id=tid,
                buyer_organization=str(tender.get("buyer_organization") or ""),
                lifecycle=str(tender.get("lifecycle_class") or ""),
                current_pr5d_decision=decision_summary(c_dec),
                shadow_annex_pr5d_decision=decision_summary(s_dec),
                current_prospect_disposition=_primary_disposition(cur_rows.get(tid, [])),
                shadow_prospect_disposition=_primary_disposition(sh_rows.get(tid, [])),
                current_equipment_classes=c_classes,
                shadow_equipment_classes=s_classes,
                new_annex_classes=new_classes,
                claim_level_capability=caps,
                commercial_intent_class=intent,
                coverage_status=coverage_status,
                coverage_debt=debt,
                current_queue_memberships=c_mem,
                shadow_queue_memberships=s_mem,
                queue_delta=qdelta,
                current_prospect_strength=str(c_str) if c_str else None,
                shadow_prospect_strength=str(s_str) if s_str else None,
                strength_delta=strength_delta,
                current_urgency=str(c_prof.get("opportunity_urgency_band") or "") or None,
                shadow_urgency=str(s_prof.get("opportunity_urgency_band") or "") or None,
                contact_resolution_status="contact_resolution_deferred",
                contact_readiness=str(s_prof.get("contact_readiness_band") or "") or None,
                annex_claim_ids=tuple(sorted(c.claim_id for c in claims)),
                annex_provenance_refs=provenance,
                change_class=change,
                human_review_required=human_review,
                e2_change_class=e2.change_class if e2 else None,
                e2_interpretation=e2.interpretation if e2 else None,
            )
        )
    return tuple(out)
