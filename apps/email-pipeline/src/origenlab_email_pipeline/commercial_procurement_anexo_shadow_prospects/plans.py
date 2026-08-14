"""Build injectable PR5C / PR5D / PR5E plans for shadow measurement.

Reuses ANEXO-E2 recognition helpers and the production PR5D classifier. Does not
fork PR5E.2 business logic — callers pass the resulting plans into
``build_institution_prospects_from_plans`` twice (current vs shadow).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from origenlab_email_pipeline.commercial_procurement_anexo_recognition.corpus import (
    AnexoEvidenceBundleSet,
)
from origenlab_email_pipeline.commercial_procurement_anexo_recognition.fingerprint import (
    corpus_digest,
)
from origenlab_email_pipeline.commercial_procurement_anexo_recognition.recognize import (
    aggregate_for_tender,
    build_annex_units,
    build_baseline_units,
    classify_units,
)
from origenlab_email_pipeline.commercial_procurement_anexo_recognition.segment import (
    segment_chunks,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    CandidatePlanResult,
    CoalescedProcurementTender,
    Pr4PlaneBundle,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.models import (
    ContactResolutionPlanResult,
    ContactResolutionSummary,
    OrganizationResolution,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
    EvidenceUnitRelevanceDecision,
    ProductRelevancePlanResult,
    ProductTextUnit,
    TenderRelevanceDecision,
)

from .constants import AS_OF_UTC_DEFAULT, PLANNER_VERSION, SHADOW_SOURCE_PLANE


def _empty_pr4() -> Pr4PlaneBundle:
    return Pr4PlaneBundle(
        signals=(),
        account_resolutions=(),
        evidence_rows=(),
        conflict_rows=(),
        enrichment_rows=(),
        build_meta={"lane": SHADOW_SOURCE_PLANE},
        source_fingerprint="a" * 64,
        build_plan_fingerprint="b" * 64,
        semantic_plan_digest="c" * 64,
        readback_semantic_digest="c" * 64,
        schema_version="v1",
        build_contract="anexo_p1_shadow",
        identity_fingerprint="d" * 64,
        as_of_date=AS_OF_UTC_DEFAULT[:10],
        schema_status="ok",
        identity_diagnostic={},
    )


def tender_dict_to_coalesced(tender: dict[str, Any]) -> CoalescedProcurementTender:
    """Map an ANEXO detail-cache tender dict into a PR5C coalesced tender."""
    tid = str(tender["tender_id"])
    lifecycle = str(tender.get("lifecycle_class") or "unknown")
    return CoalescedProcurementTender(
        coalesced_tender_id=tid,
        canonical_tender_key=f"chilecompra:{tid}",
        identity_namespace="chilecompra",
        tender_key_kind="codigo_externo",
        candidate_source_kind="detail_cache_shadow",
        pr4_procurement_id=None,
        pr4_procurement_ids=(),
        acquisition_snapshot_ids=(),
        acquisition_instance_ids=(),
        acquisition_observation_ids=(),
        coalescence_status="coalesced",
        source_precedence_reason="anexo_p1_shadow",
        currentness_class="current_authoritative_snapshot",
        lifecycle_class=lifecycle,
        closing_soon_bucket="not_closing_soon",
        publication_timestamp_selected=None,
        close_timestamp_selected=None,
        status_code_selected=None,
        status_name_selected=None,
        status_value_selected=None,
        source_status_system_selected=None,
        buyer_display_selected=str(tender.get("buyer_organization") or "") or None,
        buyer_source_id_selected=None,
        title_selected=str(tender.get("title") or ""),
        selected_field_provenance={},
        buyer_display_variance=False,
        lifecycle_status_evidence_ref_id=None,
        lifecycle_close_evidence_ref_id=None,
        lifecycle_publication_evidence_ref_id=None,
        lifecycle_evidence_currentness_class=None,
        lifecycle_reason_codes=(),
        evidence_ref_ids=(),
        conflict_ids=(),
        # PR3: propagate when the ANEXO detail-cache dict carries it (same
        # None-preserving convention as ticket_api.py); absent for fixtures
        # that predate procurement-method evidence.
        procurement_method_selected=(
            str(tender["procurement_method"])
            if tender.get("procurement_method")
            else None
        ),
        procurement_method_details_selected=(
            str(tender["procurement_method_details"])
            if tender.get("procurement_method_details")
            else None
        ),
    )


def build_candidate_plan(
    tenders: Sequence[dict[str, Any]],
    *,
    as_of_utc: str = AS_OF_UTC_DEFAULT,
    input_digest: str,
) -> CandidatePlanResult:
    coalesced = tuple(
        tender_dict_to_coalesced(t)
        for t in sorted(tenders, key=lambda x: str(x["tender_id"]))
    )
    return CandidatePlanResult(
        evidence_refs=(),
        coalesced_tenders=coalesced,
        unresolved=(),
        conflicts=(),
        pr4=_empty_pr4(),
        acquisition_snapshot_ids=(),
        acquisition_instance_ids=(),
        as_of_utc=as_of_utc,
        freshness_threshold_hours=48,
        input_source_fingerprint=input_digest,
        build_plan_fingerprint=input_digest,
        semantic_digest=input_digest,
        aggregate_reconciliation={"ok": True, "lane": SHADOW_SOURCE_PLANE},
        field_precedence_matrix={},
        lifecycle_policy={},
        run_context="anexo_p1_shadow",
        planner_version=PLANNER_VERSION,
    )


def _org(tid: str, decision_id: str) -> OrganizationResolution:
    return OrganizationResolution(
        organization_resolution_id=f"org-{tid}",
        coalesced_tender_id=tid,
        relevance_decision_id=decision_id,
        resolution_status="unlinked",
        resolution_source="anexo_p1_shadow_frozen_contact",
        account_id=None,
        link_route="shadow_measurement",
        reason_code="contact_overlay_frozen_for_shadow",
        candidate_account_ids=(),
        evidence_ref_ids=(),
        pr4_procurement_ids=(),
        pr4_resolution_ids=(),
        buyer_field_sufficiency="name_only",
        identity_fingerprint="x" * 64,
        not_persisted=True,
    )


def _summary(tid: str, decision_id: str, lifecycle: str) -> ContactResolutionSummary:
    return ContactResolutionSummary(
        contact_resolution_id=f"cr-{tid}",
        coalesced_tender_id=tid,
        relevance_decision_id=decision_id,
        organization_resolution_id=f"org-{tid}",
        account_id=None,
        final_contact_status="contact_resolution_deferred",
        selected_contact_id=None,
        selected_candidate_id=None,
        search_stages_completed=(),
        next_action="resolve_account",
        reason_code="account_unresolved",
        considered_contact_count=0,
        suitable_contact_count=0,
        blocked_contact_count=0,
        relevance_class_echo="unknown",
        lifecycle_class_echo=lifecycle,
        currentness_class_echo="historical",
        relevance_validation_status_echo="ok",
        input_fingerprint="i" * 64,
        semantic_fingerprint="s" * 64,
        rules_version="shadow",
        resolver_version="shadow",
    )


def build_frozen_contact_plan(
    tenders: Sequence[dict[str, Any]],
    *,
    as_of_utc: str = AS_OF_UTC_DEFAULT,
) -> ContactResolutionPlanResult:
    """Freeze contact overlay so CURRENT vs SHADOW differs only on PR5D evidence."""
    orgs: list[OrganizationResolution] = []
    summaries: list[ContactResolutionSummary] = []
    for tender in sorted(tenders, key=lambda t: str(t["tender_id"])):
        tid = str(tender["tender_id"])
        decision_id = f"dec-{tid}"
        orgs.append(_org(tid, decision_id))
        summaries.append(
            _summary(tid, decision_id, str(tender.get("lifecycle_class") or "unknown"))
        )
    return ContactResolutionPlanResult(
        as_of_utc=as_of_utc,
        run_context="anexo_p1_shadow",
        planner_version=PLANNER_VERSION,
        organization_resolutions=tuple(orgs),
        contact_summaries=tuple(summaries),
        contact_candidates=(),
        evidence=(),
        conflicts=(),
        reconciliation={"ok": True, "lane": SHADOW_SOURCE_PLANE},
        fingerprints={"semantic_digest": "e" * 64},
        dependency_fingerprints={},
        counts={"organization_count": len(orgs)},
        field_sufficiency_audit={},
    )


def decision_summary(decision: TenderRelevanceDecision) -> dict[str, Any]:
    return {
        "decision_id": decision.decision_id,
        "relevance_class": decision.relevance_class,
        "canonical_equipment_classes": list(decision.canonical_equipment_classes),
        "product_resolution_status": decision.product_resolution_status,
        "confidence_band": decision.confidence_band,
        "positive_reason_codes": list(decision.positive_reason_codes),
        "negative_reason_codes": list(decision.negative_reason_codes),
        "ambiguity_reason_codes": list(decision.ambiguity_reason_codes),
        "aggregation_reason_codes": list(decision.aggregation_reason_codes),
    }


def _make_pr5d(
    *,
    units: tuple[ProductTextUnit, ...],
    unit_decisions: tuple[EvidenceUnitRelevanceDecision, ...],
    tender_decisions: tuple[TenderRelevanceDecision, ...],
    as_of_utc: str,
    input_digest: str,
    lane_tag: str,
) -> ProductRelevancePlanResult:
    return ProductRelevancePlanResult(
        product_text_units=units,
        unresolved_units=(),
        unit_decisions=unit_decisions,
        tender_decisions=tender_decisions,
        field_sufficiency={},
        reconciliation={"ok": True, "lane": lane_tag},
        taxonomy_document={},
        fingerprints={"semantic_digest": input_digest, "lane": lane_tag},
        counts={
            "unit_count": len(units),
            "unit_decision_count": len(unit_decisions),
            "tender_decision_count": len(tender_decisions),
        },
        run_context="anexo_p1_shadow",
        planner_version=PLANNER_VERSION,
        as_of_utc=as_of_utc,
        pr5c_semantic_digest=input_digest,
    )


@dataclass(frozen=True)
class RelevancePlanPair:
    current: ProductRelevancePlanResult
    shadow: ProductRelevancePlanResult
    current_decisions_by_tender: dict[str, TenderRelevanceDecision]
    shadow_decisions_by_tender: dict[str, TenderRelevanceDecision]
    input_digest: str


def build_relevance_plan_pair(
    tenders: Sequence[dict[str, Any]],
    *,
    anexo: AnexoEvidenceBundleSet,
    as_of_utc: str = AS_OF_UTC_DEFAULT,
) -> RelevancePlanPair:
    """Build CURRENT (baseline) and SHADOW (baseline+annex) PR5D plans.

    The only intentional variable between the two plans is annex evidence units.
    """
    input_digest = corpus_digest(tenders)
    current_units: list[ProductTextUnit] = []
    current_unit_decisions: list[EvidenceUnitRelevanceDecision] = []
    current_tender_decisions: list[TenderRelevanceDecision] = []
    shadow_units: list[ProductTextUnit] = []
    shadow_unit_decisions: list[EvidenceUnitRelevanceDecision] = []
    shadow_tender_decisions: list[TenderRelevanceDecision] = []

    for tender in sorted(tenders, key=lambda t: str(t["tender_id"])):
        tid = str(tender["tender_id"])
        lifecycle = str(tender.get("lifecycle_class") or "unknown")

        baseline_units = build_baseline_units(tender)
        baseline_decisions = classify_units(baseline_units)
        baseline_refs = [u.evidence_ref_id or "" for u in baseline_units]
        baseline_agg = aggregate_for_tender(
            tid,
            baseline_decisions,
            lifecycle_class=lifecycle,
            evidence_ref_ids=baseline_refs,
            input_fingerprint=input_digest,
        )

        chunks = anexo.chunks(tid)
        attachments = anexo.attachments(tid)
        segments = segment_chunks(chunks, attachments=attachments)
        annex_pairs = build_annex_units(segments)
        annex_units = tuple(unit for unit, _ in annex_pairs)
        annex_decisions = classify_units(annex_units)
        annex_refs = [s.chunk_id for s in segments]
        shadow_agg = aggregate_for_tender(
            tid,
            tuple(baseline_decisions) + tuple(annex_decisions),
            lifecycle_class=lifecycle,
            evidence_ref_ids=baseline_refs + annex_refs,
            input_fingerprint=input_digest,
        )

        current_units.extend(baseline_units)
        current_unit_decisions.extend(baseline_decisions)
        current_tender_decisions.append(baseline_agg)

        shadow_units.extend(baseline_units)
        shadow_units.extend(annex_units)
        shadow_unit_decisions.extend(baseline_decisions)
        shadow_unit_decisions.extend(annex_decisions)
        shadow_tender_decisions.append(shadow_agg)

    current = _make_pr5d(
        units=tuple(current_units),
        unit_decisions=tuple(current_unit_decisions),
        tender_decisions=tuple(current_tender_decisions),
        as_of_utc=as_of_utc,
        input_digest=input_digest,
        lane_tag="anexo_p1_current",
    )
    shadow = _make_pr5d(
        units=tuple(shadow_units),
        unit_decisions=tuple(shadow_unit_decisions),
        tender_decisions=tuple(shadow_tender_decisions),
        as_of_utc=as_of_utc,
        input_digest=input_digest,
        lane_tag="anexo_p1_shadow",
    )
    return RelevancePlanPair(
        current=current,
        shadow=shadow,
        current_decisions_by_tender={d.coalesced_tender_id: d for d in current_tender_decisions},
        shadow_decisions_by_tender={d.coalesced_tender_id: d for d in shadow_tender_decisions},
        input_digest=input_digest,
    )
