"""Focused tests for PR5E.1 institution prospect intelligence."""

from __future__ import annotations

from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    CoalescedProcurementTender,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.models import (
    ContactCandidate,
    ContactResolutionSummary,
    OrganizationResolution,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.equipment_history import (
    aggregate_equipment_history,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.identity import (
    detect_name_identifier_conflicts,
    institution_identity_from_tender,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.overlay import (
    build_contact_overlay,
    score_opportunity_urgency,
    score_prospect_strength,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.reconcile import (
    InstitutionReconciliationError,
    reconcile_institution_prospects,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.signals import (
    classify_commercial_evidence_signal,
    recurrence_label,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
    MatchedEvidenceSpan,
    TenderRelevanceDecision,
)


def _tender(
    *,
    tid: str,
    buyer_name: str | None,
    buyer_id: str | None,
    lifecycle: str,
    source_kind: str = "live_snapshot",
    title: str = "Tender",
    close_bucket: str = "not_closing_soon",
    pub: str | None = "2026-01-01T00:00:00Z",
) -> CoalescedProcurementTender:
    return CoalescedProcurementTender(
        coalesced_tender_id=tid,
        canonical_tender_key=f"chilecompra:{tid}",
        identity_namespace="chilecompra",
        tender_key_kind="codigo_externo",
        candidate_source_kind=source_kind,
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
        close_timestamp_selected=None,
        status_code_selected=None,
        status_name_selected=None,
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
    )


def _decision(
    *,
    tid: str,
    relevance: str,
    classes: tuple[str, ...] = ("centrifuge",),
    evidence_refs: tuple[str, ...] = ("ev1",),
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
                field_path="title",
                rule_id="r1",
                matched_text="centrifuga",
            ),
        ),
        contributing_evidence_ref_ids=evidence_refs,
        unit_decision_ids=(),
        taxonomy_version="t1",
        rules_version="r1",
        input_fingerprint="i" * 64,
        semantic_fingerprint="s" * 64,
        lifecycle_class_echo=None,
        not_persisted=True,
    )


def _org(
    *,
    tid: str,
    status: str,
    reason: str,
    account_id: str | None = None,
) -> OrganizationResolution:
    return OrganizationResolution(
        organization_resolution_id=f"org-{tid}",
        coalesced_tender_id=tid,
        relevance_decision_id=f"dec-{tid}",
        resolution_status=status,
        resolution_source="test",
        account_id=account_id,
        link_route="test",
        reason_code=reason,
        candidate_account_ids=(),
        evidence_ref_ids=(),
        pr4_procurement_ids=(),
        pr4_resolution_ids=(),
        buyer_field_sufficiency="name_only",
        identity_fingerprint="x" * 64,
        not_persisted=True,
    )


def test_multiple_tenders_same_buyer_aggregate_one_identity() -> None:
    t1 = _tender(tid="A", buyer_name="Hospital X", buyer_id="100", lifecycle="closed")
    t2 = _tender(tid="B", buyer_name="Hospital X", buyer_id="100", lifecycle="active_open")
    i1 = institution_identity_from_tender(t1, None)
    i2 = institution_identity_from_tender(t2, None)
    assert i1.institution_id == i2.institution_id


def test_closed_tenders_contribute_history_not_urgency() -> None:
    strength = score_prospect_strength(
        purchase_tender_count=2,
        repeated_category_count=1,
        open_purchase_count=0,
        historical_purchase_count=2,
        recent_signal=True,
    )
    urgency = score_opportunity_urgency(
        open_tender_count=0,
        closing_soon_count=0,
        current_opportunity_like=0,
    )
    assert strength.band in {"medium", "high"}
    assert urgency.band == "none"
    assert "no_open_tender" in urgency.reason_codes


def test_open_and_closed_coexist_in_history() -> None:
    tenders = [
        _tender(tid="A", buyer_name="UOH", buyer_id="9", lifecycle="closed"),
        _tender(tid="B", buyer_name="UOH", buyer_id="9", lifecycle="active_open"),
    ]
    decisions = {
        "A": _decision(tid="A", relevance="strong_equipment_class"),
        "B": _decision(tid="B", relevance="strong_equipment_class"),
    }
    iids = {
        t.coalesced_tender_id: institution_identity_from_tender(t, None).institution_id
        for t in tenders
    }
    hist = aggregate_equipment_history(
        tenders=tenders,
        decisions_by_tender=decisions,
        institution_id_by_tender=iids,
    )
    iid = next(iter(set(iids.values())))
    row = hist[iid][0]
    assert row["distinct_tender_count"] == 2
    assert row["open_current_tender_count"] == 1
    assert row["historical_tender_count"] == 1
    assert row["demand_recurrence"] == "repeated_observed_demand"


def test_unlinked_institution_retained() -> None:
    t = _tender(tid="U1", buyer_name="New Hospital", buyer_id="555", lifecycle="closed")
    org = _org(tid="U1", status="unlinked", reason="buyer_domain_missing")
    ident = institution_identity_from_tender(t, org)
    assert ident.linked_account_present is False
    assert ident.chilecompra_buyer_source_id == "555"
    assert ident.institution_id


def test_linked_account_contact_overlay() -> None:
    t = _tender(tid="L1", buyer_name="ISP", buyer_id="7177", lifecycle="active_open")
    org = _org(
        tid="L1",
        status="linked",
        reason="exact_unique_alias",
        account_id="acc-isp",
    )
    ident = institution_identity_from_tender(t, org)
    summary = ContactResolutionSummary(
        contact_resolution_id="cr1",
        coalesced_tender_id="L1",
        relevance_decision_id="dec-L1",
        organization_resolution_id="org-L1",
        account_id="acc-isp",
        final_contact_status="no_contact_found",
        selected_contact_id=None,
        selected_candidate_id=None,
        search_stages_completed=("internal_search",),
        next_action="research_new_contact",
        reason_code="internal_search_exhausted_empty",
        considered_contact_count=0,
        suitable_contact_count=0,
        blocked_contact_count=0,
        relevance_class_echo="strong_equipment_class",
        lifecycle_class_echo="active_open",
        currentness_class_echo="current_authoritative_snapshot",
        relevance_validation_status_echo="ok",
        input_fingerprint="i" * 64,
        semantic_fingerprint="s" * 64,
        rules_version="v1",
        resolver_version="v1",
        not_persisted=True,
    )
    overlay = build_contact_overlay(
        identity=ident, org=org, summary=summary, candidates=[]
    )
    assert overlay.linked_account_present is True
    assert overlay.contact_gap_status == "linked_account_no_contact"
    assert overlay.contact_authorization is False
    assert overlay.outreach_authorization is False


def test_conflicting_buyer_ids_do_not_auto_merge_by_name() -> None:
    t1 = _tender(tid="C1", buyer_name="Same Name", buyer_id="1", lifecycle="closed")
    t2 = _tender(tid="C2", buyer_name="Same Name", buyer_id="2", lifecycle="closed")
    i1 = institution_identity_from_tender(t1, None)
    i2 = institution_identity_from_tender(t2, None)
    assert i1.institution_id != i2.institution_id
    remapped = detect_name_identifier_conflicts({"C1": i1, "C2": i2})
    assert remapped["C1"].identity_review_required is True
    assert remapped["C2"].identity_review_required is True


def test_different_units_do_not_merge_on_similar_name() -> None:
    t1 = _tender(
        tid="D1",
        buyer_name="Hospital Regional Unidad A",
        buyer_id="10",
        lifecycle="closed",
    )
    t2 = _tender(
        tid="D2",
        buyer_name="Hospital Regional Unidad B",
        buyer_id="11",
        lifecycle="closed",
    )
    assert (
        institution_identity_from_tender(t1, None).institution_id
        != institution_identity_from_tender(t2, None).institution_id
    )


def test_maintenance_and_rental_separate_from_purchases() -> None:
    assert (
        classify_commercial_evidence_signal("service_or_maintenance_only")
        == "installed_base_signal"
    )
    assert (
        classify_commercial_evidence_signal("rental_or_comodato")
        == "rental_or_comodato_signal"
    )
    assert (
        classify_commercial_evidence_signal("strong_equipment_class")
        == "equipment_purchase_signal"
    )


def test_consumables_do_not_inflate_purchase_history() -> None:
    tenders = [
        _tender(tid="Z1", buyer_name="Lab", buyer_id="77", lifecycle="closed"),
    ]
    decisions = {
        "Z1": _decision(tid="Z1", relevance="consumable_or_reagent", classes=("reagent",)),
    }
    iids = {
        "Z1": institution_identity_from_tender(tenders[0], None).institution_id,
    }
    hist = aggregate_equipment_history(
        tenders=tenders,
        decisions_by_tender=decisions,
        institution_id_by_tender=iids,
    )
    row = hist[iids["Z1"]][0]
    assert row["has_equipment_purchase_signal"] is False
    assert "consumable_or_reagent_signal" in row["commercial_evidence_signals"]


def test_observed_once_vs_repeated() -> None:
    assert recurrence_label(1) == "observed_once"
    assert recurrence_label(2) == "repeated_observed_demand"


def test_input_order_does_not_change_identity() -> None:
    t1 = _tender(tid="O1", buyer_name="Orden", buyer_id="42", lifecycle="closed")
    t2 = _tender(tid="O2", buyer_name="Orden", buyer_id="42", lifecycle="active_open")
    forward = [
        institution_identity_from_tender(t, None).institution_id for t in (t1, t2)
    ]
    reverse = [
        institution_identity_from_tender(t, None).institution_id for t in (t2, t1)
    ]
    assert forward[0] == reverse[1]
    assert forward[1] == reverse[0]
    assert forward[0] == forward[1]


def test_reconciliation_partition_and_no_silent_drop() -> None:
    recon = reconcile_institution_prospects(
        source_tender_ids={"a", "b", "c"},
        assigned_tender_ids={"a"},
        unresolved_tender_ids={"b"},
        excluded={"c": "missing_relevance_decision"},
    )
    assert recon["ok"] is True
    try:
        reconcile_institution_prospects(
            source_tender_ids={"a", "b"},
            assigned_tender_ids={"a"},
            unresolved_tender_ids=set(),
            excluded={},
        )
        raise AssertionError("expected reconciliation failure")
    except InstitutionReconciliationError:
        pass


def test_authorization_always_false_on_overlay() -> None:
    t = _tender(tid="A1", buyer_name="X", buyer_id="1", lifecycle="active_open")
    org = _org(tid="A1", status="linked", reason="exact_unique_alias", account_id="acc")
    ident = institution_identity_from_tender(t, org)
    cand = ContactCandidate(
        candidate_id="cand1",
        contact_resolution_id="cr",
        coalesced_tender_id="A1",
        account_id="acc",
        contact_id="ct1",
        rank=1,
        ranking_tier="primary",
        role_raw_digest="d" * 16,
        role_suitability="buyer",
        identity_status="verified",
        identity_confidence="high",
        has_usable_email=True,
        verification_status="verified",
        evidence_ids=(),
        suppression_result="clear",
        outreach_state_result="unknown",
        safety_blocked=False,
        safety_unknown=False,
        selectable=True,
        ranking_reason_codes=(),
    )
    summary = ContactResolutionSummary(
        contact_resolution_id="cr",
        coalesced_tender_id="A1",
        relevance_decision_id="dec",
        organization_resolution_id="org",
        account_id="acc",
        final_contact_status="selected",
        selected_contact_id="ct1",
        selected_candidate_id="cand1",
        search_stages_completed=("internal_search",),
        next_action="use_existing_contact",
        reason_code="selected",
        considered_contact_count=1,
        suitable_contact_count=1,
        blocked_contact_count=0,
        relevance_class_echo="strong_equipment_class",
        lifecycle_class_echo="active_open",
        currentness_class_echo="current",
        relevance_validation_status_echo="ok",
        input_fingerprint="i" * 64,
        semantic_fingerprint="s" * 64,
        rules_version="v1",
        resolver_version="v1",
    )
    overlay = build_contact_overlay(
        identity=ident, org=org, summary=summary, candidates=[cand]
    )
    assert overlay.contact_authorization is False
    assert overlay.outreach_authorization is False
    assert overlay.contact_gap_status == "existing_verified_contact"


def _empty_pr4():
    from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
        Pr4PlaneBundle,
    )

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


def test_build_profiles_from_plans_workflow() -> None:
    from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
        CandidatePlanResult,
    )
    from origenlab_email_pipeline.commercial_procurement_contact_resolution.models import (
        ContactResolutionPlanResult,
    )
    from origenlab_email_pipeline.commercial_procurement_institution_prospects.planner import (
        build_institution_prospects_from_plans,
    )
    from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
        ProductRelevancePlanResult,
    )

    closed = _tender(
        tid="T-closed",
        buyer_name="Hospital Demo",
        buyer_id="900",
        lifecycle="closed",
        source_kind="pr4",
        pub="2025-01-01T00:00:00Z",
    )
    open_t = _tender(
        tid="T-open",
        buyer_name="Hospital Demo",
        buyer_id="900",
        lifecycle="active_open",
        source_kind="live_snapshot",
        pub="2026-08-01T00:00:00Z",
    )
    unlinked = _tender(
        tid="T-unlinked",
        buyer_name="Otro Hospital",
        buyer_id="901",
        lifecycle="closed",
        source_kind="pr4",
    )
    tenders = (closed, open_t, unlinked)
    decisions = (
        _decision(tid="T-closed", relevance="strong_equipment_class"),
        _decision(tid="T-open", relevance="compatible_equipment_class"),
        _decision(tid="T-unlinked", relevance="service_or_maintenance_only"),
    )
    orgs = (
        _org(tid="T-closed", status="unlinked", reason="buyer_domain_missing"),
        _org(tid="T-open", status="unlinked", reason="buyer_domain_missing"),
        _org(tid="T-unlinked", status="unlinked", reason="buyer_domain_missing"),
    )
    summaries = tuple(
        ContactResolutionSummary(
            contact_resolution_id=f"cr-{o.coalesced_tender_id}",
            coalesced_tender_id=o.coalesced_tender_id,
            relevance_decision_id=f"dec-{o.coalesced_tender_id}",
            organization_resolution_id=o.organization_resolution_id,
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
        for o in orgs
    )
    pr5c = CandidatePlanResult(
        evidence_refs=(),
        coalesced_tenders=tenders,
        unresolved=(),
        conflicts=(),
        pr4=_empty_pr4(),
        acquisition_snapshot_ids=(),
        acquisition_instance_ids=(),
        as_of_utc="2026-08-06T02:00:00Z",
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
        unit_decisions=(),
        tender_decisions=decisions,
        field_sufficiency={},
        reconciliation={"ok": True},
        taxonomy_document={},
        fingerprints={"semantic_digest": "d" * 64},
        counts={},
        run_context="local_fixture",
        planner_version="v1",
        as_of_utc="2026-08-06T02:00:00Z",
        pr5c_semantic_digest="c" * 64,
    )
    pr5e = ContactResolutionPlanResult(
        as_of_utc="2026-08-06T02:00:00Z",
        run_context="local_fixture",
        planner_version="v1",
        organization_resolutions=orgs,
        contact_summaries=summaries,
        contact_candidates=(),
        evidence=(),
        conflicts=(),
        reconciliation={"ok": True},
        fingerprints={"semantic_digest": "e" * 64},
        dependency_fingerprints={},
        counts={},
        field_sufficiency_audit={},
    )
    result = build_institution_prospects_from_plans(
        pr5c=pr5c,
        pr5d=pr5d,
        pr5e=pr5e,
        as_of_utc="2026-08-06T02:00:00Z",
        run_context="local_fixture",
    )
    assert result.reconciliation["ok"] is True
    assert result.counts["institution_profiles"] == 2
    demo = next(
        p
        for p in result.profiles
        if p["identity"]["chilecompra_buyer_source_id"] == "900"
    )
    assert demo["counts"]["open_tender_count"] == 1
    assert demo["counts"]["historical_tender_count"] == 1
    assert demo["counts"]["equipment_purchase_tender_count"] == 2
    assert demo["axes"]["opportunity_urgency"]["band"] != "none"
    assert demo["account_contact_overlay"]["contact_gap_status"] == "account_unlinked"
    assert demo["contact_authorization"] is False
    # Order stability
    result2 = build_institution_prospects_from_plans(
        pr5c=CandidatePlanResult(
            **{
                **pr5c.__dict__,
                "coalesced_tenders": tuple(reversed(tenders)),
            }
        ),
        pr5d=pr5d,
        pr5e=pr5e,
        as_of_utc="2026-08-06T02:00:00Z",
        run_context="local_fixture",
    )
    assert result.fingerprints["semantic_digest"] == result2.fingerprints["semantic_digest"]
    assert [p["institution_id"] for p in result.profiles] == [
        p["institution_id"] for p in result2.profiles
    ]
