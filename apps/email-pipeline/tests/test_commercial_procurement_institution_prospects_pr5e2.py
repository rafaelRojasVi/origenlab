"""Focused tests for PR5E.2 recognition wiring in the institution prospect planner."""

from __future__ import annotations

import json
import random
from dataclasses import replace
from pathlib import Path

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
from origenlab_email_pipeline.commercial_procurement_institution_prospects.constants import (
    OPERATOR_QUEUE_NAMES,
    RECOGNITION_LAYER_VERSION,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.equipment_history import (
    FAMILY_NOT_APPLIED,
    aggregate_equipment_history,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.event_families import (
    resolve_procurement_event_families,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.identity import (
    institution_identity_from_tender,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.planner import (
    build_institution_prospects_from_plans,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.line_claims import (
    aggregate_category_scoped_claims,
    build_line_claims,
    reconcile_category_signal_conflicts,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.queues import (
    current_opportunity_blockers,
)
from origenlab_email_pipeline.commercial_procurement_live_feed_bridge.freshness import (
    is_future_observation,
    resolve_acquired_at_utc,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
    EvidenceUnitRelevanceDecision,
    MatchedEvidenceSpan,
    ProductRelevancePlanResult,
    ProductTextUnit,
    TenderRelevanceDecision,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.normalize import (
    normalize_product_text,
)

AS_OF = "2026-08-06T02:00:00Z"


def _tender(
    *,
    tid: str,
    buyer_name: str | None = "Hospital Demo",
    buyer_id: str | None = "900",
    lifecycle: str = "closed",
    title: str = "Adquisicion de centrifugas clinicas",
    status_code: str | None = None,
    status_name: str | None = None,
    close_ts: str | None = None,
    pub: str | None = "2026-01-01T00:00:00Z",
    close_bucket: str = "not_closing_soon",
    # PR3: defaults to a recognized open/public Ticket Tipo code so every
    # pre-existing test in this file keeps its original actionability intent
    # without individually threading procurement-method evidence through.
    # Tests exercising restricted/direct/unknown eligibility pass this
    # explicitly (e.g. procurement_method="CO" or procurement_method=None).
    procurement_method: str | None = "LP",
    procurement_method_details: str | None = None,
) -> CoalescedProcurementTender:
    return CoalescedProcurementTender(
        coalesced_tender_id=tid,
        canonical_tender_key=f"chilecompra:{tid}",
        identity_namespace="chilecompra",
        tender_key_kind="codigo_externo",
        candidate_source_kind="live_snapshot",
        pr4_procurement_id=None,
        pr4_procurement_ids=(),
        acquisition_snapshot_ids=("snap-1",),
        acquisition_instance_ids=(),
        acquisition_observation_ids=(),
        coalescence_status="coalesced",
        source_precedence_reason="test",
        currentness_class="current_authoritative_snapshot",
        lifecycle_class=lifecycle,
        closing_soon_bucket=close_bucket,
        publication_timestamp_selected=pub,
        close_timestamp_selected=close_ts,
        status_code_selected=status_code,
        status_name_selected=status_name,
        status_value_selected=None,
        source_status_system_selected=None,
        buyer_display_selected=buyer_name,
        buyer_source_id_selected=buyer_id,
        title_selected=title,
        selected_field_provenance={},
        buyer_display_variance=False,
        lifecycle_status_evidence_ref_id=None,
        lifecycle_close_evidence_ref_id=None,
        lifecycle_publication_evidence_ref_id=None,
        lifecycle_evidence_currentness_class=None,
        lifecycle_reason_codes=(),
        evidence_ref_ids=(),
        conflict_ids=(),
        procurement_method_selected=procurement_method,
        procurement_method_details_selected=procurement_method_details,
    )


def _decision(
    *,
    tid: str,
    relevance: str = "strong_equipment_class",
    classes: tuple[str, ...] = ("centrifuge",),
) -> TenderRelevanceDecision:
    return TenderRelevanceDecision(
        decision_id=f"dec-{tid}",
        coalesced_tender_id=tid,
        relevance_class=relevance,
        canonical_equipment_classes=classes,
        product_resolution_status="equipment_class_only",
        evidence_tier="line",
        confidence_band="medium",
        positive_reason_codes=("pos",),
        negative_reason_codes=(),
        ambiguity_reason_codes=(),
        aggregation_reason_codes=(),
        matched_spans=(
            MatchedEvidenceSpan(
                field_path="title", rule_id="r1", matched_text="centrifuga"
            ),
        ),
        contributing_evidence_ref_ids=("ev1",),
        unit_decision_ids=(),
        taxonomy_version="t1",
        rules_version="r1",
        input_fingerprint="i" * 64,
        semantic_fingerprint="s" * 64,
    )


def _unit(
    *,
    uid: str,
    tid: str,
    relevance: str,
    classes: tuple[str, ...] = ("centrifuge",),
    text: str = "centrifuga clinica",
) -> EvidenceUnitRelevanceDecision:
    return EvidenceUnitRelevanceDecision(
        unit_decision_id=f"ud-{uid}",
        unit_id=uid,
        coalesced_tender_id=tid,
        relevance_class=relevance,
        canonical_equipment_classes=classes,
        product_resolution_status="equipment_class_only",
        evidence_tier="line",
        confidence_band="medium",
        positive_reason_codes=(),
        negative_reason_codes=(),
        ambiguity_reason_codes=(),
        matched_spans=(
            MatchedEvidenceSpan(field_path="line", rule_id="r1", matched_text=text),
        ),
        contributing_evidence_ref_ids=(f"ev-{uid}",),
        taxonomy_version="t1",
        rules_version="r1",
        unit_input_fingerprint="i" * 64,
        unit_semantic_fingerprint="s" * 64,
    )


def _org(tid: str) -> OrganizationResolution:
    return OrganizationResolution(
        organization_resolution_id=f"org-{tid}",
        coalesced_tender_id=tid,
        relevance_decision_id=f"dec-{tid}",
        resolution_status="unlinked",
        resolution_source="test",
        account_id=None,
        link_route="test",
        reason_code="buyer_domain_missing",
        candidate_account_ids=(),
        evidence_ref_ids=(),
        pr4_procurement_ids=(),
        pr4_resolution_ids=(),
        buyer_field_sufficiency="name_only",
        identity_fingerprint="x" * 64,
        not_persisted=True,
    )


def _summary(tid: str) -> ContactResolutionSummary:
    return ContactResolutionSummary(
        contact_resolution_id=f"cr-{tid}",
        coalesced_tender_id=tid,
        relevance_decision_id=f"dec-{tid}",
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
        relevance_class_echo="strong_equipment_class",
        lifecycle_class_echo="closed",
        currentness_class_echo="historical",
        relevance_validation_status_echo="ok",
        input_fingerprint="i" * 64,
        semantic_fingerprint="s" * 64,
        rules_version="v1",
        resolver_version="v1",
    )


def _empty_pr4() -> Pr4PlaneBundle:
    return Pr4PlaneBundle(
        signals=(),
        account_resolutions=(),
        evidence_rows=(),
        conflict_rows=(),
        enrichment_rows=(),
        build_meta={},
        source_fingerprint="a" * 64,
        build_plan_fingerprint="b" * 64,
        semantic_plan_digest="c" * 64,
        readback_semantic_digest="c" * 64,
        schema_version="v1",
        build_contract="c1",
        identity_fingerprint="d" * 64,
        as_of_date="2026-08-01",
        schema_status="ok",
        identity_diagnostic={},
    )


def _plans(
    tenders: tuple[CoalescedProcurementTender, ...],
    decisions: tuple[TenderRelevanceDecision, ...],
    units: tuple[EvidenceUnitRelevanceDecision, ...] = (),
):
    orgs = tuple(_org(t.coalesced_tender_id) for t in tenders)
    pr5c = CandidatePlanResult(
        evidence_refs=(),
        coalesced_tenders=tenders,
        unresolved=(),
        conflicts=(),
        pr4=_empty_pr4(),
        acquisition_snapshot_ids=(),
        acquisition_instance_ids=(),
        as_of_utc=AS_OF,
        freshness_threshold_hours=48,
        input_source_fingerprint="a" * 64,
        build_plan_fingerprint="b" * 64,
        semantic_digest="c" * 64,
        aggregate_reconciliation={"ok": True},
        field_precedence_matrix={},
        lifecycle_policy={},
        run_context="local_fixture",
        planner_version="v1",
    )
    pr5d = ProductRelevancePlanResult(
        product_text_units=(),
        unresolved_units=(),
        unit_decisions=units,
        tender_decisions=decisions,
        field_sufficiency={},
        reconciliation={"ok": True},
        taxonomy_document={},
        fingerprints={"semantic_digest": "d" * 64},
        counts={},
        run_context="local_fixture",
        planner_version="v1",
        as_of_utc=AS_OF,
        pr5c_semantic_digest="c" * 64,
    )
    pr5e = ContactResolutionPlanResult(
        as_of_utc=AS_OF,
        run_context="local_fixture",
        planner_version="v1",
        organization_resolutions=orgs,
        contact_summaries=tuple(_summary(t.coalesced_tender_id) for t in tenders),
        contact_candidates=(),
        evidence=(),
        conflicts=(),
        reconciliation={"ok": True},
        fingerprints={"semantic_digest": "e" * 64},
        dependency_fingerprints={},
        counts={},
        field_sufficiency_audit={},
    )
    return pr5c, pr5d, pr5e


def _build(tenders, decisions, units=()):
    pr5c, pr5d, pr5e = _plans(tenders, decisions, units)
    return build_institution_prospects_from_plans(
        pr5c=pr5c,
        pr5d=pr5d,
        pr5e=pr5e,
        as_of_utc=AS_OF,
        run_context="local_fixture",
    )


def _line_evidence(
    tid: str, texts: tuple[str, ...]
) -> tuple[tuple[ProductTextUnit, ...], tuple[EvidenceUnitRelevanceDecision, ...]]:
    """Build matching ProductTextUnit + EvidenceUnitRelevanceDecision pairs.

    A LineClaim's relevance/scope always comes from re-classifying the raw
    clause text (see build_line_claims), independent of the synthetic
    unit-decision relevance passed here — that value only drives whether
    _build_line_rows treats the line as assigned rather than review/excluded.
    """
    units = tuple(
        ProductTextUnit(
            unit_id=f"{tid}-u{i}",
            coalesced_tender_id=tid,
            evidence_ref_id=f"{tid}-ev{i}",
            link_status="linked",
            unresolved_reason=None,
            field_path="line",
            text_raw=text,
            text_normalized=normalize_product_text(text),
            evidence_tier="line_product_text",
            source_plane="test",
            snapshot_id="snap-1",
            observation_id=None,
            tender_observation_id=None,
            line_observation_id=None,
            pr4_procurement_id=None,
            contributing_evidence_ref_ids=(),
        )
        for i, text in enumerate(texts)
    )
    decisions = tuple(
        _unit(uid=f"{tid}-u{i}", tid=tid, relevance="strong_equipment_class")
        for i in range(len(texts))
    )
    return units, decisions


def _build_with_lines(tender, decision, texts):
    units, unit_decisions = _line_evidence(tender.coalesced_tender_id, texts)
    pr5c, pr5d, pr5e = _plans((tender,), (decision,), units=unit_decisions)
    pr5d = replace(pr5d, product_text_units=units)
    return build_institution_prospects_from_plans(
        pr5c=pr5c,
        pr5d=pr5d,
        pr5e=pr5e,
        as_of_utc=AS_OF,
        run_context="local_fixture",
    )


# --- equipment_history -------------------------------------------------------


def test_equipment_history_backward_compatible_without_optional_args() -> None:
    tenders = [
        _tender(tid="A", lifecycle="closed"),
        _tender(tid="B", lifecycle="active_open"),
    ]
    decisions = {t.coalesced_tender_id: _decision(tid=t.coalesced_tender_id) for t in tenders}
    iids = {
        t.coalesced_tender_id: institution_identity_from_tender(t, None).institution_id
        for t in tenders
    }
    row = aggregate_equipment_history(
        tenders=tenders,
        decisions_by_tender=decisions,
        institution_id_by_tender=iids,
    )[next(iter(iids.values()))][0]
    assert row["demand_recurrence"] == "repeated_observed_demand"
    assert row["raw_tender_count"] == 2
    assert row["family_resolution_status"] == FAMILY_NOT_APPLIED
    assert row["open_current_tender_count"] == 1


def test_equipment_history_uses_families_and_projected_lifecycle() -> None:
    # Same buyer + identical procurement object a few weeks apart, with no
    # reissue marker: the relationship is unresolved, so recurrence is not
    # established and the pair must not be collapsed into a confirmed event.
    tenders = [
        _tender(tid="A", lifecycle="closed"),
        _tender(tid="B", lifecycle="closed"),
    ]
    decisions = {t.coalesced_tender_id: _decision(tid=t.coalesced_tender_id) for t in tenders}
    iids = {
        t.coalesced_tender_id: institution_identity_from_tender(t, None).institution_id
        for t in tenders
    }
    families, tender_to_family = resolve_procurement_event_families(tenders)
    family_by_id = {f.family_id: f.to_dict() for f in families}
    family_by_tender = {t: family_by_id[f] for t, f in tender_to_family.items()}
    row = aggregate_equipment_history(
        tenders=tenders,
        decisions_by_tender=decisions,
        institution_id_by_tender=iids,
        # Projected lifecycle flips one tender back to open.
        lifecycle_by_tender={"A": "active_open", "B": "closed"},
        family_by_tender=family_by_tender,
    )[next(iter(iids.values()))][0]
    assert row["raw_tender_count"] == 2
    assert row["procurement_event_family_count"] == 1
    assert row["independent_demand_event_count"] == 0
    assert row["demand_recurrence"] == "recurrence_not_established"
    assert row["family_resolution_status"] == "retender_review_required"
    assert row["family_reason_codes"]
    assert row["open_current_tender_count"] == 1
    assert row["historical_tender_count"] == 1


def test_equipment_history_confirmed_family_from_explicit_reissue_marker() -> None:
    # An explicit replacement marker is authoritative evidence of one event.
    base_title = "Adquisicion de centrifugas clinicas para laboratorio APS"
    tenders = [
        _tender(tid="A", lifecycle="closed", title=base_title),
        _tender(
            tid="B",
            lifecycle="closed",
            title=f"{base_title} EN REEMPLAZO",
            pub="2026-02-01T00:00:00Z",
        ),
    ]
    decisions = {
        t.coalesced_tender_id: _decision(tid=t.coalesced_tender_id) for t in tenders
    }
    iids = {
        t.coalesced_tender_id: institution_identity_from_tender(t, None).institution_id
        for t in tenders
    }
    families, tender_to_family = resolve_procurement_event_families(tenders)
    family_by_id = {f.family_id: f.to_dict() for f in families}
    family_by_tender = {t: family_by_id[f] for t, f in tender_to_family.items()}
    row = aggregate_equipment_history(
        tenders=tenders,
        decisions_by_tender=decisions,
        institution_id_by_tender=iids,
        lifecycle_by_tender={"A": "active_open", "B": "closed"},
        family_by_tender=family_by_tender,
    )[next(iter(iids.values()))][0]
    assert row["family_resolution_status"] == "confirmed_family"
    assert row["independent_demand_event_count"] == 1
    assert row["demand_recurrence"] == "observed_once_confirmed"


# --- lifecycle precedence wiring --------------------------------------------


def test_status_unknown_with_open_values_restored_to_current_queue() -> None:
    from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
        ProductTextUnit,
    )
    from origenlab_email_pipeline.commercial_procurement_product_relevance.normalize import (
        normalize_product_text,
    )

    restored = _tender(
        tid="R",
        lifecycle="status_unknown",
        status_code="5",
        status_name="publicada",
        close_ts="2026-09-01T00:00:00Z",
    )
    text = "centrifuga clinica de laboratorio"
    unit = ProductTextUnit(
        unit_id="u-r",
        coalesced_tender_id="R",
        evidence_ref_id="ev-r",
        link_status="linked",
        unresolved_reason=None,
        field_path="line",
        text_raw=text,
        text_normalized=normalize_product_text(text),
        evidence_tier="line_product_text",
        source_plane="test",
        snapshot_id="snap-1",
        observation_id=None,
        tender_observation_id=None,
        line_observation_id=None,
        pr4_procurement_id=None,
        contributing_evidence_ref_ids=(),
    )
    pr5c, pr5d, pr5e = _plans(
        (restored,),
        (_decision(tid="R"),),
        units=(_unit(uid="u-r", tid="R", relevance="strong_equipment_class"),),
    )
    pr5d = ProductRelevancePlanResult(
        product_text_units=(unit,),
        unresolved_units=(),
        unit_decisions=pr5d.unit_decisions,
        tender_decisions=pr5d.tender_decisions,
        field_sufficiency=pr5d.field_sufficiency,
        reconciliation=pr5d.reconciliation,
        taxonomy_document=pr5d.taxonomy_document,
        fingerprints=pr5d.fingerprints,
        counts=pr5d.counts,
        run_context=pr5d.run_context,
        planner_version=pr5d.planner_version,
        as_of_utc=pr5d.as_of_utc,
        pr5c_semantic_digest=pr5d.pr5c_semantic_digest,
    )
    result = build_institution_prospects_from_plans(
        pr5c=pr5c,
        pr5d=pr5d,
        pr5e=pr5e,
        as_of_utc=AS_OF,
        run_context="local_fixture",
    )
    projections = {p["coalesced_tender_id"]: p for p in result.lifecycle_projections}
    assert projections["R"]["projected_lifecycle_class"] == "active_open"
    assert projections["R"]["source_lifecycle_class"] == "status_unknown"
    assert projections["R"]["lifecycle_independent_of_relevance"] is True

    profile = result.profiles[0]
    assert profile["counts"]["open_tender_count"] == 1
    assert profile["axes"]["opportunity_urgency"]["band"] != "none"
    assert (
        "projected_lifecycle_precedence_applied"
        in profile["axes"]["opportunity_urgency"]["reason_codes"]
    )
    codes = {
        r["tender_code"] for r in result.operator_queues["current_opportunity_queue"]
    }
    assert "R" in codes


def test_terminal_status_overrides_stale_open_lifecycle() -> None:
    # Without observation chronology, open+terminal fails closed to conflict —
    # it must not invent a terminal win from elapsed close alone.
    stale = _tender(tid="S", lifecycle="active_open", status_code="6")
    result = _build((stale,), (_decision(tid="S"),))
    projections = {p["coalesced_tender_id"]: p for p in result.lifecycle_projections}
    assert projections["S"]["projected_lifecycle_class"] == "status_conflict"
    assert result.profiles[0]["counts"]["open_tender_count"] == 0
    assert not result.operator_queues["current_opportunity_queue"]


# --- event families ----------------------------------------------------------


def test_retender_does_not_count_as_repeated_demand() -> None:
    tenders = (
        _tender(tid="F1", lifecycle="closed"),
        _tender(tid="F2", lifecycle="closed"),
    )
    result = _build(tenders, tuple(_decision(tid=t.coalesced_tender_id) for t in tenders))
    profile = result.profiles[0]
    assert profile["counts"]["tender_count"] == 2
    assert profile["counts"]["procurement_event_family_count"] == 1
    assert profile["counts"]["repeated_equipment_category_count"] == 0
    assert result.counts["procurement_event_family_count"] == 1


def test_distinct_objects_remain_independent_demand_events() -> None:
    tenders = (
        _tender(tid="G1", lifecycle="closed", title="Adquisicion de centrifugas"),
        _tender(tid="G2", lifecycle="closed", title="Adquisicion de incubadoras"),
    )
    result = _build(tenders, tuple(_decision(tid=t.coalesced_tender_id) for t in tenders))
    assert result.counts["procurement_event_family_count"] == 2
    assert result.profiles[0]["counts"]["repeated_equipment_category_count"] == 1


# --- line-level reconciliation ----------------------------------------------


def test_every_unit_decision_is_reconciled() -> None:
    tenders = (_tender(tid="L1", lifecycle="closed"),)
    units = (
        _unit(uid="u-assigned", tid="L1", relevance="strong_equipment_class"),
        _unit(uid="u-review", tid="L1", relevance="ambiguous", classes=()),
        _unit(uid="u-excluded", tid="L1", relevance="unrelated", classes=()),
    )
    result = _build(tenders, (_decision(tid="L1"),), units)
    recon = result.line_reconciliation
    assert recon["ok"] is True
    assert recon["unit_decision_count"] == 3
    assert recon["assigned_line_count"] == 1
    assert recon["review_required_line_count"] == 1
    assert recon["excluded_line_count"] == 1
    assert (
        recon["assigned_line_count"]
        + recon["review_required_line_count"]
        + recon["excluded_line_count"]
        == recon["unit_decision_count"]
    )
    review_queue = result.operator_queues["line_evidence_review_queue"]
    assert [r["unit_id"] for r in review_queue] == ["u-review"]




def test_profile_purchase_metrics_follow_category_scoped_line_truth_once_per_tender() -> None:
    """Line truth drives profile metrics; multiple categories still count one tender."""
    from dataclasses import replace

    from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
        ProductTextUnit,
    )
    from origenlab_email_pipeline.commercial_procurement_product_relevance.normalize import (
        normalize_product_text,
    )

    tender = _tender(
        tid="LINE-TRUTH",
        lifecycle="closed",
        title="Insumos generales para laboratorio",
    )

    # Tender/title truth is deliberately non-purchase. Detailed line evidence
    # below establishes two equipment categories on the same tender.
    tender_decision = _decision(
        tid="LINE-TRUTH",
        relevance="consumable_or_reagent",
        classes=(),
    )

    raw_units = (
        (
            "lt-centrifuge",
            "centrifuga clinica de laboratorio",
            ("centrifuge",),
        ),
        (
            "lt-balance",
            "balanza analitica de laboratorio",
            ("balance",),
        ),
    )

    product_units = tuple(
        ProductTextUnit(
            unit_id=uid,
            coalesced_tender_id="LINE-TRUTH",
            evidence_ref_id=f"ev-{uid}",
            link_status="linked",
            unresolved_reason=None,
            field_path="line",
            text_raw=raw_text,
            text_normalized=normalize_product_text(raw_text),
            evidence_tier="line_product_text",
            source_plane="test",
            snapshot_id="snap-1",
            observation_id=None,
            tender_observation_id=None,
            line_observation_id=None,
            pr4_procurement_id=None,
            contributing_evidence_ref_ids=(),
        )
        for uid, raw_text, _classes in raw_units
    )

    unit_decisions = tuple(
        _unit(
            uid=uid,
            tid="LINE-TRUTH",
            relevance="strong_equipment_class",
            classes=classes,
            text=raw_text,
        )
        for uid, raw_text, classes in raw_units
    )

    pr5c, pr5d, pr5e = _plans(
        (tender,),
        (tender_decision,),
        units=unit_decisions,
    )

    pr5d = replace(
        pr5d,
        product_text_units=product_units,
    )

    result = build_institution_prospects_from_plans(
        pr5c=pr5c,
        pr5d=pr5d,
        pr5e=pr5e,
        as_of_utc=AS_OF,
        run_context="local_fixture",
    )

    profile = result.profiles[0]

    purchase_rows = [
        row
        for row in profile["historical_signals"]
        if row["commercial_signal_type"]
        == "equipment_purchase_signal"
    ]

    assert {
        row["canonical_equipment_category"]
        for row in purchase_rows
    } == {"balance", "centrifuge"}

    # Two equipment categories from one procurement event are still one
    # purchase tender for institution-level scoring.
    assert (
        profile["counts"]["equipment_purchase_tender_count"]
        == 1
    )

    strength = profile["axes"]["prospect_strength"]
    assert "has_equipment_purchase_evidence" in strength["reason_codes"]
    assert "historical_buying_intent" in strength["reason_codes"]

    historical = result.operator_queues[
        "historical_prospect_queue"
    ]
    assert {
        row["equipment_category"]
        for row in historical
        if row["commercial_signal_type"]
        == "equipment_purchase_signal"
    } == {"balance", "centrifuge"}


# --- queues and safety -------------------------------------------------------


def test_all_operator_queues_present_and_unauthorized() -> None:
    tenders = (
        _tender(tid="Q1", lifecycle="closed"),
        _tender(
            tid="Q2",
            buyer_name="Otro Hospital",
            buyer_id="901",
            lifecycle="active_open",
            title="Arriendo de centrifuga",
        ),
    )
    result = _build(
        tenders,
        (
            _decision(tid="Q1"),
            _decision(tid="Q2"),
        ),
        (_unit(uid="q-u1", tid="Q1", relevance="ambiguous", classes=()),),
    )
    assert set(result.operator_queues) == set(OPERATOR_QUEUE_NAMES)
    for rows in result.operator_queues.values():
        for row in rows:
            assert row["contact_authorization"] is False
            assert row["outreach_authorization"] is False
    # Rental title demotes an equipment keyword away from a purchase signal.
    q2 = next(r for r in result.tender_rows if r["tender_code"] == "Q2")
    assert q2["commercial_signal_type"] == "rental_or_comodato_signal"
    assert q2["review_disposition"] == "installed_base_or_rental"
    assert not result.operator_queues["current_opportunity_queue"]


def test_summary_reports_recognition_layer_and_queue_sizes() -> None:
    tenders = (_tender(tid="Z1", lifecycle="closed"),)
    result = _build(tenders, (_decision(tid="Z1"),))
    summary = result.to_summary_dict()
    assert summary["recognition_layer_version"] == RECOGNITION_LAYER_VERSION
    assert set(summary["operator_queue_sizes"]) == set(OPERATOR_QUEUE_NAMES)
    assert summary["line_reconciliation"]["ok"] is True
    # Production classification never reads analyst-reviewed labels; the fixture
    # comparison is a test-only harness.
    assert summary["reviewed_adjudication_applied"] is False
    assert result.reviewed_adjudication_results == []
    assert summary["separations"]["retender_ne_independent_demand_event"] is True
    assert summary["separations"]["outreach_authorization"] is False
    assert summary["temporal_reconciliation"][
        "acquisition_timestamps_immutable"
    ] is True
    assert set(summary["queue_grains"]) == set(OPERATOR_QUEUE_NAMES)


def test_recognition_output_is_order_stable() -> None:
    tenders = (
        _tender(tid="O1", lifecycle="closed", title="Adquisicion de centrifugas"),
        _tender(tid="O2", lifecycle="active_open", title="Adquisicion de incubadoras"),
    )
    decisions = tuple(_decision(tid=t.coalesced_tender_id) for t in tenders)
    forward = _build(tenders, decisions)
    reverse = _build(tuple(reversed(tenders)), tuple(reversed(decisions)))
    assert forward.fingerprints["semantic_digest"] == reverse.fingerprints[
        "semantic_digest"
    ]
    assert [f["family_id"] for f in forward.event_families] == [
        f["family_id"] for f in reverse.event_families
    ]
    assert [
        r["tender_code"] for r in forward.operator_queues["current_opportunity_queue"]
    ] == [
        r["tender_code"] for r in reverse.operator_queues["current_opportunity_queue"]
    ]


# --- acquired_at pinning -----------------------------------------------------


def test_acquired_at_is_never_rewritten_when_manifest_is_ahead_of_as_of() -> None:
    """Acquisition stamps are observed facts; a later stamp is quarantined, not moved."""
    manifest = {"generated_at_utc": "2026-08-06T09:00:00Z"}
    assert (
        resolve_acquired_at_utc(manifest=manifest, refresh_state=None)
        == "2026-08-06T09:00:00Z"
    )
    assert (
        resolve_acquired_at_utc(manifest=manifest, refresh_state=None, as_of_utc=AS_OF)
        == "2026-08-06T09:00:00Z"
    )
    assert is_future_observation("2026-08-06T09:00:00Z", as_of_utc=AS_OF) is True


def test_acquired_at_before_as_of_is_not_a_future_observation() -> None:
    manifest = {"generated_at_utc": "2026-08-05T09:00:00Z"}
    assert (
        resolve_acquired_at_utc(manifest=manifest, refresh_state=None, as_of_utc=AS_OF)
        == "2026-08-05T09:00:00Z"
    )
    assert is_future_observation("2026-08-05T09:00:00Z", as_of_utc=AS_OF) is False


def test_future_snapshot_detection_reads_page_level_acquisition_stamps(
    tmp_path,
) -> None:
    """Acquisition snapshots record fetch time per page, not at the top level."""
    from origenlab_email_pipeline.commercial_procurement_institution_prospects.planner import (
        _future_observation_snapshot_ids,
    )

    def _write(name: str, snapshot_id: str, page_stamp: str) -> Path:
        path = tmp_path / name
        path.write_text(
            json.dumps(
                {
                    "snapshot_id": snapshot_id,
                    "materialized_at_utc": "2026-08-06T21:02:56Z",
                    "pages": [{"acquired_at_utc": page_stamp}],
                }
            ),
            encoding="utf-8",
        )
        return path

    ahead = _write("ahead.json", "snap-ahead", "2026-08-06T09:00:00Z")
    behind = _write("behind.json", "snap-behind", "2026-08-05T09:00:00Z")
    assert _future_observation_snapshot_ids(
        [ahead, behind], as_of_utc=AS_OF
    ) == frozenset({"snap-ahead"})


def test_future_stamped_snapshot_tenders_are_quarantined_not_rewritten() -> None:
    tenders = (_tender(tid="FUT", lifecycle="active_open"),)
    pr5c, pr5d, pr5e = _plans(tenders, (_decision(tid="FUT"),))
    result = build_institution_prospects_from_plans(
        pr5c=pr5c,
        pr5d=pr5d,
        pr5e=pr5e,
        as_of_utc=AS_OF,
        run_context="local_fixture",
        future_observation_snapshot_ids=frozenset({"snap-1"}),
    )
    assert result.counts["future_observation_excluded_tenders"] == 1
    assert result.profiles == []
    quarantined = result.temporal_reconciliation["future_observation_excluded"]
    assert quarantined[0]["coalesced_tender_id"] == "FUT"
    assert quarantined[0]["reason"] == "acquisition_stamp_after_as_of"
    assert result.reconciliation["ok"] is True


# --- lifecycle precedence & false positives ---------------------------------


def test_status_unknown_cannot_overwrite_known_open_values() -> None:
    from origenlab_email_pipeline.commercial_procurement_institution_prospects.lifecycle_precedence import (
        project_tender_lifecycle,
    )

    tender = _tender(
        tid="L1",
        lifecycle="status_unknown",
        status_code="5",
        status_name="Publicada",
        close_ts="2026-09-01T00:00:00Z",
    )
    proj = project_tender_lifecycle(tender, as_of_utc=AS_OF)
    assert proj.projected_lifecycle_class == "active_open"
    assert "status_unknown_must_not_overwrite_known_open" in proj.reason_codes


def test_newer_terminal_overrides_older_open() -> None:
    from origenlab_email_pipeline.commercial_procurement_institution_prospects.lifecycle_precedence import (
        project_tender_lifecycle,
    )

    tender = _tender(
        tid="L2",
        lifecycle="active_open",
        status_code="6",
        status_name="Cerrada",
        close_ts="2026-07-01T00:00:00Z",
    )
    proj = project_tender_lifecycle(
        tender,
        as_of_utc=AS_OF,
        status_observed_at_utc="2026-07-15T12:00:00Z",
        lifecycle_observed_at_utc="2026-06-01T12:00:00Z",
    )
    assert proj.projected_lifecycle_class == "closed"


def test_missing_chronology_open_plus_terminal_is_conflict() -> None:
    from origenlab_email_pipeline.commercial_procurement_institution_prospects.lifecycle_precedence import (
        project_tender_lifecycle,
    )

    tender = _tender(
        tid="L2b",
        lifecycle="active_open",
        status_code="6",
        status_name="Cerrada",
        close_ts="2026-07-01T00:00:00Z",
    )
    proj = project_tender_lifecycle(tender, as_of_utc=AS_OF)
    assert proj.projected_lifecycle_class == "status_conflict"
    assert proj.temporal_resolution_status == "temporal_review_required"


def test_centrifugal_pump_not_centrifuge() -> None:
    from origenlab_email_pipeline.commercial_procurement_institution_prospects.adjudication import (
        adjudicate_public_evidence,
    )

    d = adjudicate_public_evidence(
        title="REEMPLAZO E INSTALACIÓN DE BOMBAS CENTRIFUGAS CAA"
    )
    assert d["review_disposition"] == "lexical_false_positive"


def test_microcentrifuge_tubes_are_consumables() -> None:
    from origenlab_email_pipeline.commercial_procurement_institution_prospects.adjudication import (
        adjudicate_public_evidence,
    )

    d = adjudicate_public_evidence(title="Tubos de microcentrifuga 1.5 ml")
    assert d["review_disposition"] == "consumable_or_accessory"


def test_business_incubator_not_lab_incubator() -> None:
    from origenlab_email_pipeline.commercial_procurement_institution_prospects.adjudication import (
        adjudicate_public_evidence,
    )

    d = adjudicate_public_evidence(title="FERIA DE PROGRAMACIÓN CBB-INCUBADORA")
    assert d["review_disposition"] == "lexical_false_positive"


def test_anthropometric_scale_not_analytical_balance() -> None:
    from origenlab_email_pipeline.commercial_procurement_institution_prospects.adjudication import (
        adjudicate_public_evidence,
    )

    d = adjudicate_public_evidence(
        title="Adquisición de balanzas tallímetros caliper y cintas métricas para atención en box"
    )
    assert d["review_disposition"] == "valid_equipment_outside_catalog"


def test_microscope_cover_is_accessory() -> None:
    from origenlab_email_pipeline.commercial_procurement_institution_prospects.adjudication import (
        adjudicate_public_evidence,
    )

    d = adjudicate_public_evidence(title="SUMINISTRO DE MANGA PROTECTORA MICROSCOPIO")
    assert d["review_disposition"] == "consumable_or_accessory"


def test_retender_pair_not_repeated_demand() -> None:
    tenders = (
        _tender(
            tid="407-115-co26",
            buyer_name="HOSPITAL GRANT BENAVENTE",
            buyer_id="407",
            title="REEMPLAZO E INSTALACIÓN DE BOMBAS CENTRIFUGAS CAA",
            lifecycle="active_open",
            status_code="5",
            status_name="Publicada",
            close_ts="2026-09-01T00:00:00Z",
        ),
        _tender(
            tid="407-89-le26",
            buyer_name="HOSPITAL GRANT BENAVENTE",
            buyer_id="407",
            title="REEMPLAZO E INSTALACIÓN DE BOMBAS CENTRIFUGAS CAA",
            lifecycle="closed",
        ),
    )
    families, _ = resolve_procurement_event_families(tenders)
    # Identical buyer + title is a suspicion, not proof of one procurement event:
    # the pair is held for operator review and recurrence stays unestablished.
    paired = [f for f in families if f.raw_tender_count == 2]
    assert paired
    assert paired[0].family_resolution_status == "retender_review_required"
    assert paired[0].independent_demand_event_count == 0
    assert paired[0].independent_event_count_lower_bound == 1
    assert paired[0].independent_event_count_upper_bound == 2
    assert paired[0].unresolved_relationship_count == 1
    assert paired[0].recurrence_status == "recurrence_not_established"
    decisions = (
        _decision(tid="407-115-co26", relevance="non_laboratory_false_positive", classes=()),
        _decision(tid="407-89-le26", relevance="non_laboratory_false_positive", classes=()),
    )
    result = _build(tenders, decisions)
    for row in result.tender_rows:
        assert row["review_disposition"] == "lexical_false_positive"
    assert result.counts["profiles_with_repeated_equipment_demand"] == 0


def test_rengo_retender_recurrence_not_established() -> None:
    tenders = (
        _tender(
            tid="2775-73-le26",
            buyer_name="I MUNICIPALIDAD DE RENGO",
            buyer_id="2775",
            title=(
                "IMPLEMENTACIÓN Y EQUIPAMIENTO CLÍNICA VETERINARIA: ADQUISICIÓN DE "
                "EQUIPOS DE LABORATORIO Y MONITOREO PARA ATENCIÓN EN VETERINARIA MUNICIPAL."
            ),
            lifecycle="closed",
        ),
        _tender(
            tid="2775-92-le26",
            buyer_name="I MUNICIPALIDAD DE RENGO",
            buyer_id="2775",
            title=(
                "IMPLEMENTACIÓN Y EQUIPAMIENTO CLÍNICA VETERINARIA: ADQUISICIÓN DE "
                "EQUIPOS DE LABORATORIO Y MONITOREO PARA ATENCIÓN EN VETERINARIA "
                "MUNICIPAL DE RENGO"
            ),
            lifecycle="status_unknown",
            status_code="5",
            status_name="Publicada",
            close_ts="2026-09-01T00:00:00Z",
        ),
    )
    families, _ = resolve_procurement_event_families(tenders)
    # The second title only extends the first with a locality suffix — near
    # identical, no authoritative relationship evidence, so it stays unresolved.
    assert any(
        f.family_resolution_status == "retender_review_required"
        and f.independent_demand_event_count == 0
        and f.recurrence_status == "recurrence_not_established"
        for f in families
    )
    decisions = (
        _decision(tid="2775-73-le26", relevance="compatible_equipment_class", classes=("balance",)),
        _decision(tid="2775-92-le26", relevance="compatible_equipment_class", classes=("balance",)),
    )
    result = _build(tenders, decisions)
    assert result.counts["profiles_with_repeated_equipment_demand"] == 0


def test_reviewed_adjudication_fixture_distribution() -> None:
    from helpers.pr5e2_adjudication import evaluate_reviewed_adjudication_fixture

    report = evaluate_reviewed_adjudication_fixture()
    assert report["total"] == 36
    assert report["matches"] == 36
    assert report["predicted_disposition_counts"] == report["expected_disposition_counts"]
    assert report["provenance"] == "analyst_reviewed_provisional"


# --- same-category purchase/rental signal reconciliation (Defect A/B) -------
#
# Tender codes below are generic fixture labels, not references to any real
# ChileCompra tender. Wording is modeled on the shapes of real cases audited
# separately (a long-term rental described with an "adquisicion del servicio
# de arriendo" tender description; a multi-item lab-equipment tender with
# concrete, non-generic line descriptions) without hardcoding production
# behavior to any specific tender ID.


def test_reconcile_category_signal_conflicts_marks_inferred_purchase_only() -> None:
    """Same-tender, same-category rental evidence plus a purchase-signal claim
    inferred only from a bare equipment noun (no explicit purchase verb at
    all) is an unresolved conflict — and the rental claim is not dropped.
    """
    tid = "GENERIC-CONFLICT-1"
    units, _ = _line_evidence(
        tid,
        (
            "arriendo de centrifuga",
            "centrifuga de laboratorio disponible en bodega",
        ),
    )
    claims = [c for u in units for c in build_line_claims(u)]
    groups = aggregate_category_scoped_claims(claims)
    reconciled = reconcile_category_signal_conflicts(claims, groups)

    purchase = next(
        g for g in reconciled if g["commercial_signal"] == "equipment_purchase_signal"
    )
    rental = next(
        g for g in reconciled if g["commercial_signal"] == "rental_or_comodato_signal"
    )
    assert purchase["category_signal_conflict"] is True
    assert purchase["category_signal_sibling_signals"] == ["rental_or_comodato_signal"]
    # Reconciliation must not silently discard the sibling's own claims.
    assert rental["category_signal_conflict"] is False
    assert rental["claim_ids"]


def test_reconcile_category_signal_conflicts_is_order_independent() -> None:
    """Reconciliation must be a pure function of bucket contents, not of the
    order claims/groups were passed in. In production, groups always arrive
    pre-sorted from aggregate_category_scoped_claims, which would mask an
    ordering bug here — so this test bypasses that by reversing (and
    shuffling) both inputs directly, covering a category with three sibling
    signals (purchase + rental + installed_base) so a same-signal insertion
    order swap has somewhere to hide if it existed.
    """
    tid = "GENERIC-ORDER-1"
    units, _ = _line_evidence(
        tid,
        (
            "centrifuga de laboratorio disponible en bodega",
            "arriendo de centrifuga",
            "mantencion de centrifuga de laboratorio",
        ),
    )
    claims = [c for u in units for c in build_line_claims(u)]
    groups = aggregate_category_scoped_claims(claims)

    forward = reconcile_category_signal_conflicts(claims, groups)
    reversed_result = reconcile_category_signal_conflicts(
        list(reversed(claims)), list(reversed(groups))
    )
    assert forward == reversed_result

    shuffled_claims = list(claims)
    shuffled_groups = list(groups)
    random.Random(42).shuffle(shuffled_claims)
    random.Random(7).shuffle(shuffled_groups)
    shuffled_result = reconcile_category_signal_conflicts(
        shuffled_claims, shuffled_groups
    )
    assert forward == shuffled_result

    # category_signal_sibling_signals must be sorted and deduplicated,
    # independent of how many times a signal's group appears in the bucket.
    purchase = next(
        g for g in forward if g["commercial_signal"] == "equipment_purchase_signal"
    )
    assert purchase["category_signal_sibling_signals"] == sorted(
        set(purchase["category_signal_sibling_signals"])
    )
    assert purchase["category_signal_sibling_signals"] == [
        "installed_base_signal",
        "rental_or_comodato_signal",
    ]


def test_reconcile_category_signal_conflicts_preserves_explicit_purchase() -> None:
    """An independently explicit equipment purchase alongside a same-category
    rental claim is not flagged as a conflict; the sibling stays traceable.
    """
    tid = "GENERIC-EXPLICIT-1"
    units, _ = _line_evidence(
        tid,
        (
            "compra de centrifuga de laboratorio",
            "arriendo de centrifuga",
        ),
    )
    claims = [c for u in units for c in build_line_claims(u)]
    groups = aggregate_category_scoped_claims(claims)
    reconciled = reconcile_category_signal_conflicts(claims, groups)

    purchase = next(
        g for g in reconciled if g["commercial_signal"] == "equipment_purchase_signal"
    )
    assert purchase["category_signal_conflict"] is False
    assert purchase["category_signal_sibling_signals"] == ["rental_or_comodato_signal"]


def test_reconcile_category_signal_conflicts_keeps_categories_independent() -> None:
    """Maintenance evidence for one category and a purchase claim for a
    different category on the same tender never conflict with each other.
    """
    tid = "GENERIC-CROSSCAT-1"
    units, _ = _line_evidence(
        tid,
        (
            "mantencion de centrifuga de laboratorio",
            "adquisicion de balanza analitica de laboratorio",
        ),
    )
    claims = [c for u in units for c in build_line_claims(u)]
    groups = aggregate_category_scoped_claims(claims)
    reconciled = reconcile_category_signal_conflicts(claims, groups)

    assert {g["equipment_category"] for g in reconciled} == {"centrifuge", "balance"}
    for g in reconciled:
        assert g["category_signal_conflict"] is False
        assert g["category_signal_sibling_signals"] == []


def test_current_opportunity_blockers_reports_conflict_reason_code() -> None:
    """Direct blocker check: a row flagged as a category-signal conflict is
    blocked with the deterministic reason code, independent of every other
    check current_opportunity_blockers performs.
    """
    row = {
        "projected_lifecycle_class": "active_open",
        "review_disposition": "catalog_fit_candidate",
        "commercial_signal_type": "equipment_purchase_signal",
        "catalog_fit_status": "catalog_fit_candidate",
        "catalog_match_status": "catalog_fit_candidate",
        "canonical_equipment_category": "centrifuge",
        "equipment_scopes": ["complete_equipment"],
        "purchase_intent": True,
        "commercial_truth_source": "category_scoped_line_claim_conflict",
        "category_signal_conflict": True,
    }
    blockers = current_opportunity_blockers(row, 1, as_of_utc=AS_OF)
    assert "unresolved_category_signal_conflict" in blockers

    resolved_row = {**row, "category_signal_conflict": False}
    assert "unresolved_category_signal_conflict" not in current_opportunity_blockers(
        resolved_row, 1, as_of_utc=AS_OF
    )


def test_chiloe_shaped_rental_tender_absent_from_current_opportunity_queue() -> None:
    """A generic fixture modeled on a real long-term rental tender: title
    names the rental directly, and the tender description separately reads
    "adquisicion del servicio de arriendo..." (a purchase verb modifying the
    rental service, not the equipment). Must stay visible in the institution
    profile's current opportunities (it is still open and real evidence) but
    must never reach the actionable current_opportunity_queue.

    Required regression 6: the real Chiloé tender's structured Tipo is "LE"
    (open/public per PR3's Ticket code mapping) — PR3 must not add a new
    blocker here or otherwise change PR2's rental-signal-driven exclusion.
    """
    tid = "GENERIC-RENTAL-1"
    tender = _tender(
        tid=tid,
        lifecycle="active_open",
        title="arriendo centrifuga inmunohematologica lavadora",
        close_ts="2026-09-01T00:00:00Z",
        procurement_method="LE",
        procurement_method_details="1",
    )
    decision = _decision(tid=tid, relevance="rental_or_comodato", classes=())
    result = _build_with_lines(
        tender,
        decision,
        (
            "arriendo centrifuga inmunohematologica lavadora",
            "disponer de 1 centrifuga inmunohematologica lavadora de celulas "
            "de manera segura, mediante la adquisicion del servicio de "
            "arriendo de equipos con modalidad de leasing.",
        ),
    )

    assert not any(
        r["tender_code"] == tid
        for r in result.operator_queues["current_opportunity_queue"]
    )
    profile = result.profiles[0]
    assert any(row["coalesced_tender_id"] == tid for row in profile["current_opportunities"])
    matched_rows = [row for row in result.tender_rows if row["coalesced_tender_id"] == tid]
    assert matched_rows, "expected at least one tender_rows entry for the Chiloé-shaped tender"
    for row in matched_rows:
        # Must retain an actual rental/service signal, not merely "not purchase".
        assert row["commercial_signal_type"] in (
            "rental_or_comodato_signal",
            "installed_base_signal",
        )
        assert "verified_catalog_class_and_purchase_signal" not in row["reason_codes"]
        # PR3: method is open/public — exclusion is driven solely by PR2's
        # existing rental-signal blocker, not by any new PR3 eligibility blocker.
        assert row["procurement_method"] == "LE"
        assert row["procurement_eligibility_status"] == "open_public"
        assert "restricted_procurement_invitation_unconfirmed" not in row["reason_codes"]
        assert "procurement_method_unknown_or_unmapped" not in row["reason_codes"]
        assert row["contact_authorization"] is False
        assert row["outreach_authorization"] is False
    assert profile["contact_authorization"] is False
    assert profile["outreach_authorization"] is False


def test_same_category_conflict_blocked_from_current_opportunity_queue() -> None:
    """End-to-end: rental evidence plus a bare-noun-inferred purchase claim
    for the same category is blocked from current_opportunity_queue with the
    deterministic conflict reason code, while staying visible in the profile.
    """
    tid = "GENERIC-CONFLICT-2"
    tender = _tender(tid=tid, lifecycle="active_open", title="Centrifuga de laboratorio")
    decision = _decision(tid=tid, relevance="ambiguous", classes=())
    result = _build_with_lines(
        tender,
        decision,
        (
            "arriendo de centrifuga",
            "centrifuga de laboratorio disponible en bodega",
        ),
    )

    assert not any(
        r["tender_code"] == tid
        for r in result.operator_queues["current_opportunity_queue"]
    )
    conflict_rows = [
        row for row in result.tender_rows if row["coalesced_tender_id"] == tid
    ]
    assert any(row["category_signal_conflict"] for row in conflict_rows)
    conflict_row = next(row for row in conflict_rows if row["category_signal_conflict"])
    assert "unresolved_category_signal_conflict" in conflict_row["reason_codes"]
    assert conflict_row["category_signal_sibling_signals"] == ["rental_or_comodato_signal"]
    assert conflict_row["contact_authorization"] is False
    assert conflict_row["outreach_authorization"] is False
    profile = result.profiles[0]
    assert any(row["coalesced_tender_id"] == tid for row in profile["current_opportunities"])


def test_explicit_purchase_with_separate_rental_sibling_remains_actionable() -> None:
    """An explicit, independently-worded equipment purchase clause stays
    actionable even when a separate rental clause for the same category and
    tender exists — the sibling stays traceable but does not block the queue.
    """
    tid = "GENERIC-EXPLICIT-2"
    tender = _tender(tid=tid, lifecycle="active_open", title="Centrifuga de laboratorio")
    decision = _decision(tid=tid, relevance="ambiguous", classes=())
    result = _build_with_lines(
        tender,
        decision,
        (
            "compra de centrifuga de laboratorio",
            "arriendo de centrifuga",
        ),
    )

    queue_row = next(
        r
        for r in result.operator_queues["current_opportunity_queue"]
        if r["tender_code"] == tid
    )
    assert queue_row["contact_authorization"] is False
    assert queue_row["outreach_authorization"] is False
    row = next(row for row in result.tender_rows if row["coalesced_tender_id"] == tid)
    assert row["category_signal_conflict"] is False
    assert row["category_signal_sibling_signals"] == ["rental_or_comodato_signal"]


def test_explicit_purchase_with_ancillary_maintenance_remains_actionable() -> None:
    """Ancillary maintenance bundled into one explicit purchase clause (no
    separate sibling claim at all) stays a clean, unconflicted purchase.
    """
    tid = "GENERIC-ANCILLARY-1"
    tender = _tender(tid=tid, lifecycle="active_open", title="Centrifuga de laboratorio")
    decision = _decision(tid=tid, relevance="ambiguous", classes=())
    result = _build_with_lines(
        tender,
        decision,
        (
            "Adquisición de centrífuga con mantención preventiva incluida "
            "por 12 meses",
        ),
    )

    assert any(
        r["tender_code"] == tid
        for r in result.operator_queues["current_opportunity_queue"]
    )
    row = next(row for row in result.tender_rows if row["coalesced_tender_id"] == tid)
    assert row["category_signal_conflict"] is False
    assert row["category_signal_sibling_signals"] == []


def test_maintenance_one_category_purchase_another_no_cross_conflict() -> None:
    """Maintenance evidence for centrifuge and a purchase claim for balance on
    the same tender must not block each other — different categories only.
    """
    tid = "GENERIC-CROSSCAT-2"
    tender = _tender(tid=tid, lifecycle="active_open", title="Equipamiento de laboratorio")
    decision = _decision(tid=tid, relevance="ambiguous", classes=())
    result = _build_with_lines(
        tender,
        decision,
        (
            "mantencion de centrifuga de laboratorio",
            "adquisicion de balanza analitica de laboratorio",
        ),
    )

    balance_queue_row = next(
        r
        for r in result.operator_queues["current_opportunity_queue"]
        if r["tender_code"] == tid and r["equipment_category"] == "balance"
    )
    assert balance_queue_row["contact_authorization"] is False
    assert balance_queue_row["outreach_authorization"] is False
    balance_row = next(
        row
        for row in result.tender_rows
        if row["coalesced_tender_id"] == tid
        and row["canonical_equipment_category"] == "balance"
    )
    assert balance_row["category_signal_conflict"] is False
    assert balance_row["category_signal_sibling_signals"] == []


def test_sag_shaped_centrifuge_purchase_remains_actionable() -> None:
    """A real, concrete SAG-style line description (centrifuge for eppendorf
    tubes) untouched by any conflicting sibling stays a clean purchase.
    """
    tid = "GENERIC-SAG-1"
    tender = _tender(tid=tid, lifecycle="active_open", title="Equipos de laboratorio")
    decision = _decision(tid=tid, relevance="ambiguous", classes=())
    result = _build_with_lines(
        tender,
        decision,
        (
            "Item N12 Centrifuga para tubos eppendorf de 1,5 a 2ml + UPS. "
            "Detalles en bases tecnicas.",
        ),
    )

    queue_row = next(
        r
        for r in result.operator_queues["current_opportunity_queue"]
        if r["tender_code"] == tid
    )
    assert queue_row["equipment_category"] == "centrifuge"
    assert queue_row["contact_authorization"] is False
    assert queue_row["outreach_authorization"] is False


def test_antofagasta_shaped_centrifuge_purchase_remains_actionable() -> None:
    """A real, concrete Antofagasta-style line description (refrigerated
    centrifuge) untouched by any conflicting sibling stays a clean purchase.
    """
    tid = "GENERIC-ANTOF-1"
    tender = _tender(tid=tid, lifecycle="active_open", title="Equipos de laboratorio")
    decision = _decision(tid=tid, relevance="ambiguous", classes=())
    result = _build_with_lines(
        tender,
        decision,
        ("Centrifuga Refrigerada Universal.",),
    )

    queue_row = next(
        r
        for r in result.operator_queues["current_opportunity_queue"]
        if r["tender_code"] == tid
    )
    assert queue_row["equipment_category"] == "centrifuge"
    assert queue_row["contact_authorization"] is False
    assert queue_row["outreach_authorization"] is False


# --- PR3: procurement-method participation eligibility -----------------------
#
# Tender codes below are generic fixture labels, not references to any real
# ChileCompra tender. Wording/method codes are modeled on the shapes of real
# cases audited separately (see the PR3 audit report for ISP/SAG/Chiloé raw
# evidence), without hardcoding production behavior to any specific tender ID.
# No test here asserts a specific invited-supplier list, count, or that
# OrigenLab was excluded — the pipeline has no invitee-identity evidence at
# all (see PR3 audit); only "invitation unconfirmed" is ever asserted.


def test_isp_shaped_co_acquisition_excluded_for_unconfirmed_invitation() -> None:
    """Required regression 4: a genuine equipment-purchase signal (ISP-shaped,
    Tipo=CO, TipoConvocatoria=0) is correctly recognized as a purchase but
    blocked from current_opportunity_queue because the structured procurement
    method is private/restricted and no invitee evidence exists — while
    staying visible in tender_rows and the institution profile.
    """
    tid = "GENERIC-ISP-1"
    tender = _tender(
        tid=tid,
        lifecycle="active_open",
        title="PPRIV 05- Adquisicion de Equipos de Laboratorio Proyecto CORFO",
        procurement_method="CO",
        procurement_method_details="0",
    )
    decision = _decision(tid=tid, relevance="unrelated", classes=())
    result = _build_with_lines(
        tender,
        decision,
        ("6.- CENTRIFUGA REFRIGERADA DE SOBREMESA",),
    )

    assert not any(
        r["tender_code"] == tid
        for r in result.operator_queues["current_opportunity_queue"]
    )
    profile = result.profiles[0]
    assert any(row["coalesced_tender_id"] == tid for row in profile["current_opportunities"])
    matched_rows = [row for row in result.tender_rows if row["coalesced_tender_id"] == tid]
    assert matched_rows
    for row in matched_rows:
        assert row["commercial_signal_type"] == "equipment_purchase_signal"
        assert row["procurement_method"] == "CO"
        assert row["procurement_method_details"] == "0"
        assert row["procurement_eligibility_status"] == "restricted_invitation_unconfirmed"
        assert "restricted_procurement_invitation_unconfirmed" in row["reason_codes"]
        assert row["contact_authorization"] is False
        assert row["outreach_authorization"] is False
    assert profile["contact_authorization"] is False
    assert profile["outreach_authorization"] is False


def test_sag_shaped_lp_purchase_is_open_public_and_actionable() -> None:
    """Required regression 5: SAG-shaped LP purchase classifies open_public
    and remains actionable — no new PR3 blocker.
    """
    tid = "GENERIC-SAG-2"
    tender = _tender(
        tid=tid,
        lifecycle="active_open",
        title="SC 6732026- Equipos de laboratorio- RED SAG de Laboratorios",
        procurement_method="LP",
        procurement_method_details="1",
    )
    decision = _decision(tid=tid, relevance="unrelated", classes=())
    result = _build_with_lines(
        tender,
        decision,
        ("Centrifuga Refrigerada Universal.",),
    )

    row = next(r for r in result.tender_rows if r["coalesced_tender_id"] == tid)
    assert row["procurement_eligibility_status"] == "open_public"
    assert "restricted_procurement_invitation_unconfirmed" not in row["reason_codes"]
    assert "procurement_method_unknown_or_unmapped" not in row["reason_codes"]
    assert row["contact_authorization"] is False
    assert row["outreach_authorization"] is False
    assert any(
        r["tender_code"] == tid
        for r in result.operator_queues["current_opportunity_queue"]
    )


def test_lr_equipment_purchase_remains_open_and_actionable() -> None:
    """Required regression 7: LR (glossary-confirmed public code) is treated
    the same as the other public Ticket codes.
    """
    tid = "GENERIC-LR-1"
    tender = _tender(
        tid=tid,
        lifecycle="active_open",
        title="Adquisicion equipos laboratorio",
        procurement_method="LR",
        procurement_method_details="1",
    )
    decision = _decision(tid=tid, relevance="unrelated", classes=())
    result = _build_with_lines(
        tender,
        decision,
        ("Centrifuga Refrigerada Universal.",),
    )

    row = next(r for r in result.tender_rows if r["coalesced_tender_id"] == tid)
    assert row["procurement_eligibility_status"] == "open_public"
    assert row["contact_authorization"] is False
    assert row["outreach_authorization"] is False
    assert any(
        r["tender_code"] == tid
        for r in result.operator_queues["current_opportunity_queue"]
    )


def test_public_method_with_ppriv_in_title_stays_public() -> None:
    """Required regression 8: title text ("PPRIV") never controls eligibility —
    only the structured procurement_method does. A public Tipo code with a
    misleadingly "PPRIV"-worded title must not be blocked.
    """
    tid = "GENERIC-PPRIV-PUBLIC-1"
    tender = _tender(
        tid=tid,
        lifecycle="active_open",
        title="PPRIV falso positivo Adquisicion equipos",
        procurement_method="LP",
        procurement_method_details="1",
    )
    decision = _decision(tid=tid, relevance="unrelated", classes=())
    result = _build_with_lines(
        tender,
        decision,
        ("Centrifuga Refrigerada Universal.",),
    )

    row = next(r for r in result.tender_rows if r["coalesced_tender_id"] == tid)
    assert row["procurement_eligibility_status"] == "open_public"
    assert "restricted_procurement_invitation_unconfirmed" not in row["reason_codes"]
    assert any(
        r["tender_code"] == tid
        for r in result.operator_queues["current_opportunity_queue"]
    )


def test_co_method_without_ppriv_in_title_stays_restricted() -> None:
    """Required regression 9: structured source evidence controls eligibility,
    not title wording — a CO tender with no "PPRIV" anywhere in its title is
    still restricted.
    """
    tid = "GENERIC-CO-NOPPRIV-1"
    tender = _tender(
        tid=tid,
        lifecycle="active_open",
        title="Adquisicion de Equipos de Laboratorio",
        procurement_method="CO",
        procurement_method_details="0",
    )
    decision = _decision(tid=tid, relevance="unrelated", classes=())
    result = _build_with_lines(
        tender,
        decision,
        ("Centrifuga Refrigerada Universal.",),
    )

    row = next(r for r in result.tender_rows if r["coalesced_tender_id"] == tid)
    assert row["procurement_eligibility_status"] == "restricted_invitation_unconfirmed"
    assert "restricted_procurement_invitation_unconfirmed" in row["reason_codes"]
    assert not any(
        r["tender_code"] == tid
        for r in result.operator_queues["current_opportunity_queue"]
    )


def test_ticket_direct_nonopen_type_is_blocked() -> None:
    """Required regression 10: a direct/non-open Ticket Tipo code (not merely
    "private") is blocked the same way as private codes.
    """
    tid = "GENERIC-DIRECT-1"
    tender = _tender(
        tid=tid,
        lifecycle="active_open",
        title="Trato directo equipos laboratorio",
        procurement_method="D1",
        procurement_method_details=None,
    )
    decision = _decision(tid=tid, relevance="unrelated", classes=())
    result = _build_with_lines(
        tender,
        decision,
        ("Centrifuga Refrigerada Universal.",),
    )

    row = next(r for r in result.tender_rows if r["coalesced_tender_id"] == tid)
    assert row["procurement_eligibility_status"] == "restricted_invitation_unconfirmed"
    assert "restricted_procurement_invitation_unconfirmed" in row["reason_codes"]
    assert not any(
        r["tender_code"] == tid
        for r in result.operator_queues["current_opportunity_queue"]
    )


def test_missing_procurement_method_is_blocked_as_unknown() -> None:
    """Required regression 12: missing/unmapped method evidence must not be
    silently presented as confirmed eligibility — it fails closed.
    """
    tid = "GENERIC-UNKNOWN-1"
    tender = _tender(
        tid=tid,
        lifecycle="active_open",
        title="Adquisicion equipos laboratorio",
        procurement_method=None,
        procurement_method_details=None,
    )
    decision = _decision(tid=tid, relevance="unrelated", classes=())
    result = _build_with_lines(
        tender,
        decision,
        ("Centrifuga Refrigerada Universal.",),
    )

    row = next(r for r in result.tender_rows if r["coalesced_tender_id"] == tid)
    assert row["procurement_eligibility_status"] == "unknown"
    assert "procurement_method_unknown_or_unmapped" in row["reason_codes"]
    assert row["contact_authorization"] is False
    assert row["outreach_authorization"] is False
    assert not any(
        r["tender_code"] == tid
        for r in result.operator_queues["current_opportunity_queue"]
    )


def test_procurement_eligibility_blocker_reason_code_appears_exactly_once() -> None:
    """PR3 verification gate: the eligibility blocker reason code must be
    deterministic and appear exactly once on a row's reason_codes, even
    across a multi-line-item tender (mirroring ISP's real two-item shape)
    and even though two independent layers evaluate eligibility —
    planner.py (which writes row["reason_codes"]) and
    queues.py::current_opportunity_blockers (which independently decides
    queue inclusion but must never itself write back into the row).
    Historical/profile visibility stays separate from queue actionability.
    """
    tid = "GENERIC-ISP-DEDUP-1"
    tender = _tender(
        tid=tid,
        lifecycle="active_open",
        title="PPRIV 05- Adquisicion de Equipos de Laboratorio Proyecto CORFO",
        procurement_method="CO",
        procurement_method_details="0",
    )
    decision = _decision(tid=tid, relevance="unrelated", classes=())
    result = _build_with_lines(
        tender,
        decision,
        (
            "6.- CENTRIFUGA REFRIGERADA DE SOBREMESA",
            "Otra centrifuga de laboratorio.",
        ),
    )

    matched_rows = [row for row in result.tender_rows if row["coalesced_tender_id"] == tid]
    assert matched_rows
    for row in matched_rows:
        count = row["reason_codes"].count(
            "restricted_procurement_invitation_unconfirmed"
        )
        assert count == 1, f"expected exactly one occurrence, got {count}"
        # Ordering convention: eligibility reason appended after disposition/
        # signal reasons, matching the existing CATEGORY_SIGNAL_CONFLICT_REASON
        # append-last convention.
        assert row["reason_codes"][-1] == "restricted_procurement_invitation_unconfirmed"

        # Independently calling the blocker function (as queues.py does, and
        # as a second "planning/queue layer" would) multiple times must not
        # mutate or duplicate the row's own reason_codes.
        before = list(row["reason_codes"])
        current_opportunity_blockers(row, 1, as_of_utc=AS_OF)
        current_opportunity_blockers(row, 1, as_of_utc=AS_OF)
        assert row["reason_codes"] == before

    # Historical/profile visibility stays separate from queue actionability.
    assert not any(
        r["tender_code"] == tid
        for r in result.operator_queues["current_opportunity_queue"]
    )
    profile = result.profiles[0]
    assert any(row["coalesced_tender_id"] == tid for row in profile["current_opportunities"])
