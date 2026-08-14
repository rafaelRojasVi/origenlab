"""Production-wiring regressions for PR5E.2 recognition.

These prove the planner actually enforces hardening helpers — not that the
helpers exist in isolation. Expected to FAIL on 0c4bb96 and PASS after wiring.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest

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
from origenlab_email_pipeline.commercial_procurement_institution_prospects.planner import (
    InstitutionReconciliationError,
    build_institution_prospects_from_plans,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.queues import (
    build_operator_queues,
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
SRC = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "origenlab_email_pipeline"
    / "commercial_procurement_institution_prospects"
)


def _tender(
    *,
    tid: str,
    snapshot_ids: tuple[str, ...] = ("snap-eligible",),
    lifecycle: str = "active_open",
    title: str = "Adquisicion de centrifuga clinica de laboratorio",
    close_ts: str | None = "2026-09-01T00:00:00Z",
    status_code: str | None = "5",
    status_name: str | None = "Publicada",
) -> CoalescedProcurementTender:
    return CoalescedProcurementTender(
        coalesced_tender_id=tid,
        canonical_tender_key=f"chilecompra:{tid}",
        identity_namespace="chilecompra",
        tender_key_kind="codigo_externo",
        candidate_source_kind="live_snapshot",
        pr4_procurement_id=None,
        pr4_procurement_ids=(),
        acquisition_snapshot_ids=snapshot_ids,
        acquisition_instance_ids=(),
        acquisition_observation_ids=(),
        coalescence_status="coalesced",
        source_precedence_reason="test",
        currentness_class="current_authoritative_snapshot",
        lifecycle_class=lifecycle,
        closing_soon_bucket="within_30d",
        publication_timestamp_selected="2026-07-01T00:00:00Z",
        close_timestamp_selected=close_ts,
        status_code_selected=status_code,
        status_name_selected=status_name,
        status_value_selected=None,
        source_status_system_selected=None,
        buyer_display_selected="Hospital Wiring",
        buyer_source_id_selected="9100",
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
        # PR3: default to a recognized open/public Ticket Tipo code so
        # pre-existing tests keep their original actionability intent.
        procurement_method_selected="LP",
        procurement_method_details_selected=None,
    )


def _decision(tid: str) -> TenderRelevanceDecision:
    return TenderRelevanceDecision(
        decision_id=f"dec-{tid}",
        coalesced_tender_id=tid,
        relevance_class="strong_equipment_class",
        canonical_equipment_classes=("centrifuge",),
        product_resolution_status="equipment_class_only",
        evidence_tier="line",
        confidence_band="medium",
        positive_reason_codes=("pos",),
        negative_reason_codes=(),
        ambiguity_reason_codes=(),
        aggregation_reason_codes=(),
        matched_spans=(
            MatchedEvidenceSpan(
                field_path="line", rule_id="r1", matched_text="centrifuga"
            ),
        ),
        contributing_evidence_ref_ids=("ev1",),
        unit_decision_ids=(),
        taxonomy_version="t1",
        rules_version="r1",
        input_fingerprint="i" * 64,
        semantic_fingerprint="s" * 64,
    )


def _unit_decision(
    *, uid: str, tid: str, relevance: str = "strong_equipment_class"
) -> EvidenceUnitRelevanceDecision:
    return EvidenceUnitRelevanceDecision(
        unit_decision_id=f"ud-{uid}",
        unit_id=uid,
        coalesced_tender_id=tid,
        relevance_class=relevance,
        canonical_equipment_classes=("centrifuge",)
        if relevance != "unrelated"
        else (),
        product_resolution_status="equipment_class_only",
        evidence_tier="line",
        confidence_band="medium",
        positive_reason_codes=(),
        negative_reason_codes=(),
        ambiguity_reason_codes=(),
        matched_spans=(),
        contributing_evidence_ref_ids=(f"ev-{uid}",),
        taxonomy_version="t1",
        rules_version="r1",
        unit_input_fingerprint="i" * 64,
        unit_semantic_fingerprint="s" * 64,
    )


def _text_unit(
    *,
    uid: str,
    tid: str | None,
    text: str,
    link_status: str = "linked",
    unresolved: str | None = None,
) -> ProductTextUnit:
    return ProductTextUnit(
        unit_id=uid,
        coalesced_tender_id=tid or "",
        evidence_ref_id=f"ev-{uid}",
        link_status=link_status,
        unresolved_reason=unresolved,
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
        lifecycle_class_echo="active_open",
        currentness_class_echo="current",
        relevance_validation_status_echo="ok",
        input_fingerprint="i" * 64,
        semantic_fingerprint="s" * 64,
        rules_version="v1",
        resolver_version="v1",
    )


def _plans(
    tenders: tuple[CoalescedProcurementTender, ...],
    *,
    product_text_units: tuple[ProductTextUnit, ...] = (),
    unresolved_units: tuple[ProductTextUnit, ...] = (),
    unit_decisions: tuple[EvidenceUnitRelevanceDecision, ...] = (),
    decisions: tuple[TenderRelevanceDecision, ...] | None = None,
) -> tuple[CandidatePlanResult, ProductRelevancePlanResult, ContactResolutionPlanResult]:
    decisions = decisions or tuple(_decision(t.coalesced_tender_id) for t in tenders)
    orgs = tuple(_org(t.coalesced_tender_id) for t in tenders)
    pr5c = CandidatePlanResult(
        evidence_refs=(),
        coalesced_tenders=tenders,
        unresolved=(),
        conflicts=(),
        pr4=_empty_pr4(),
        acquisition_snapshot_ids=tuple(
            sid for t in tenders for sid in (t.acquisition_snapshot_ids or ())
        ),
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
        product_text_units=product_text_units,
        unresolved_units=unresolved_units,
        unit_decisions=unit_decisions,
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


def _write_snapshot(
    path: Path,
    *,
    snapshot_id: str,
    acquired_at_utc: str | None,
    malformed: bool = False,
) -> None:
    pages: list[dict[str, Any]]
    if malformed:
        pages = [{"acquired_at_utc": "not-a-timestamp"}]
    elif acquired_at_utc is None:
        pages = [{}]
    else:
        pages = [{"acquired_at_utc": acquired_at_utc}]
    path.write_text(
        json.dumps(
            {
                "snapshot_id": snapshot_id,
                "pages": pages,
                "acquired_at_utc": acquired_at_utc,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# A. Temporal eligibility wiring
# ---------------------------------------------------------------------------


def test_a_snapshot_classifier_quarantines_missing_and_invalid(tmp_path: Path) -> None:
    from origenlab_email_pipeline.commercial_procurement_institution_prospects import (
        planner,
    )

    classify = getattr(planner, "classify_acquisition_snapshots", None)
    assert classify is not None, (
        "planner must expose classify_acquisition_snapshots(); "
        "_future_observation_snapshot_ids only detects future stamps"
    )

    eligible = tmp_path / "eligible.json"
    future = tmp_path / "future.json"
    missing = tmp_path / "missing.json"
    invalid = tmp_path / "invalid.json"
    _write_snapshot(eligible, snapshot_id="snap-eligible", acquired_at_utc="2026-08-06T01:00:00Z")
    _write_snapshot(future, snapshot_id="snap-future", acquired_at_utc="2026-08-06T20:00:00Z")
    _write_snapshot(missing, snapshot_id="snap-missing", acquired_at_utc=None)
    _write_snapshot(invalid, snapshot_id="snap-invalid", acquired_at_utc="x", malformed=True)

    report = classify(
        [eligible, future, missing, invalid],
        as_of_utc=AS_OF,
    )
    by_status = report.get("by_status") or report
    assert "snap-eligible" in set(by_status.get("eligible") or report.get("eligible_snapshot_ids") or [])
    assert "snap-future" in set(by_status.get("future_observation") or report.get("future_snapshot_ids") or [])
    assert "snap-missing" in set(
        by_status.get("missing_chronology") or report.get("missing_chronology_snapshot_ids") or []
    )
    assert "snap-invalid" in set(
        by_status.get("invalid_chronology") or report.get("invalid_chronology_snapshot_ids") or []
    )


def test_a_missing_chronology_tender_excluded_from_recognition_planes() -> None:
    """Missing chronology must not feed lifecycle/families/claims/history/queues."""
    tender = _tender(tid="MISSING", snapshot_ids=("snap-missing",))
    text = _text_unit(
        uid="u-missing",
        tid="MISSING",
        text="adquisición de centrífuga refrigerada de laboratorio",
    )
    pr5c, pr5d, pr5e = _plans(
        (tender,),
        product_text_units=(text,),
        unit_decisions=(_unit_decision(uid="u-missing", tid="MISSING"),),
    )
    result = build_institution_prospects_from_plans(
        pr5c=pr5c,
        pr5d=pr5d,
        pr5e=pr5e,
        as_of_utc=AS_OF,
        run_context="local_fixture",
        # Production must classify missing separately; passing only future IDs is
        # the defect. After fix, callers pass full temporal classification.
        snapshot_temporal_report={
            "eligible_snapshot_ids": [],
            "future_snapshot_ids": [],
            "missing_chronology_snapshot_ids": ["snap-missing"],
            "invalid_chronology_snapshot_ids": [],
        },
    )
    temporal = result.temporal_reconciliation
    assert temporal.get("missing_chronology_count", 0) >= 1
    assert any(
        row.get("coalesced_tender_id") == "MISSING"
        for row in (temporal.get("missing_chronology") or [])
    )
    assert "MISSING" not in {
        p.get("coalesced_tender_id") for p in result.lifecycle_projections
    }
    assert all(
        "MISSING" not in (f.get("member_tender_ids") or [])
        for f in result.event_families
    )
    assert not any(
        c.get("coalesced_tender_id") == "MISSING" for c in result.line_claims
    )
    assert not result.operator_queues.get("current_opportunity_queue")


def test_a_future_only_helper_is_not_sole_snapshot_gate() -> None:
    src = (SRC / "planner.py").read_text(encoding="utf-8")
    assert "observation_temporal_status" in src or "classify_acquisition_snapshots" in src
    # Production path must not rely only on is_future_observation for snapshot gates.
    future_fn = inspect.getsource(
        __import__(
            "origenlab_email_pipeline.commercial_procurement_institution_prospects.planner",
            fromlist=["_future_observation_snapshot_ids"],
        )._future_observation_snapshot_ids
        if hasattr(
            __import__(
                "origenlab_email_pipeline.commercial_procurement_institution_prospects.planner",
                fromlist=["planner"],
            ),
            "_future_observation_snapshot_ids",
        )
        else (lambda: None)
    )
    # Prefer the classifier; if the old helper remains it must consult full status.
    from origenlab_email_pipeline.commercial_procurement_institution_prospects import (
        planner as planner_mod,
    )

    if hasattr(planner_mod, "classify_acquisition_snapshots"):
        return
    assert "observation_temporal_status" in future_fn


# ---------------------------------------------------------------------------
# B. Unresolved product text units
# ---------------------------------------------------------------------------


def test_b_unresolved_unit_cannot_emit_commercial_claims_via_planner() -> None:
    tender = _tender(tid="U1", snapshot_ids=("snap-eligible",))
    unresolved = _text_unit(
        uid="u-unresolved",
        tid="U1",
        text="adquisición de centrífuga refrigerada de laboratorio",
        link_status="unresolved",
        unresolved="no_tender_link",
    )
    # Defect path: unresolved unit also present in product_text_units.
    pr5c, pr5d, pr5e = _plans(
        (tender,),
        product_text_units=(unresolved,),
        unresolved_units=(unresolved,),
        unit_decisions=(),
        decisions=(_decision("U1"),),
    )
    result = build_institution_prospects_from_plans(
        pr5c=pr5c,
        pr5d=pr5d,
        pr5e=pr5e,
        as_of_utc=AS_OF,
        run_context="local_fixture",
        snapshot_temporal_report={
            "eligible_snapshot_ids": ["snap-eligible"],
            "future_snapshot_ids": [],
            "missing_chronology_snapshot_ids": [],
            "invalid_chronology_snapshot_ids": [],
        },
    )
    assert result.line_claims == []
    assert not any(
        "complete_equipment" in (r.get("equipment_scopes") or [])
        for r in result.line_rows
    )
    for profile in result.profiles:
        for hist in profile.get("equipment_history") or []:
            assert hist.get("has_equipment_purchase_signal") is not True or not any(
                "u-unresolved" in str(x) for x in (hist.get("line_description_snippets") or [])
            )
    assert not any(
        r.get("coalesced_tender_id") == "U1"
        and r.get("commercial_signal_type") == "equipment_purchase_signal"
        for r in result.operator_queues.get("current_opportunity_queue", [])
    )
    recon = result.line_reconciliation
    assert recon.get("unresolved_unit_count", 0) >= 1 or "u-unresolved" in str(recon)


# ---------------------------------------------------------------------------
# C. Production line reconciliation wiring
# ---------------------------------------------------------------------------


def test_c_planner_fails_closed_on_orphan_decision_via_reconcile_line_partition() -> None:
    tender = _tender(tid="O1", snapshot_ids=("snap-eligible",), lifecycle="closed")
    present = _text_unit(
        uid="u-present",
        tid="O1",
        text="centrifuga clinica de laboratorio",
    )
    # Decision references a unit that is not in product_text_units.
    orphan = _unit_decision(uid="missing-unit", tid="O1")
    present_decision = _unit_decision(uid="u-present", tid="O1")
    pr5c, pr5d, pr5e = _plans(
        (tender,),
        product_text_units=(present,),
        unit_decisions=(orphan, present_decision),
        decisions=(_decision("O1"),),
    )
    with pytest.raises(InstitutionReconciliationError):
        build_institution_prospects_from_plans(
            pr5c=pr5c,
            pr5d=pr5d,
            pr5e=pr5e,
            as_of_utc=AS_OF,
            run_context="local_fixture",
            snapshot_temporal_report={
                "eligible_snapshot_ids": ["snap-eligible"],
                "future_snapshot_ids": [],
                "missing_chronology_snapshot_ids": [],
                "invalid_chronology_snapshot_ids": [],
            },
        )


def test_c_production_line_reconciliation_includes_failure_codes_field() -> None:
    """Authoritative recon must surface failure_codes from reconcile_line_partition."""
    tender = _tender(tid="OK1", snapshot_ids=("snap-eligible",), lifecycle="closed")
    text = _text_unit(
        uid="u-ok",
        tid="OK1",
        text="centrifuga clinica de laboratorio",
    )
    pr5c, pr5d, pr5e = _plans(
        (tender,),
        product_text_units=(text,),
        unit_decisions=(_unit_decision(uid="u-ok", tid="OK1"),),
    )
    result = build_institution_prospects_from_plans(
        pr5c=pr5c,
        pr5d=pr5d,
        pr5e=pr5e,
        as_of_utc=AS_OF,
        run_context="local_fixture",
        snapshot_temporal_report={
            "eligible_snapshot_ids": ["snap-eligible"],
            "future_snapshot_ids": [],
            "missing_chronology_snapshot_ids": [],
            "invalid_chronology_snapshot_ids": [],
        },
    )
    assert "failure_codes" in result.line_reconciliation
    assert result.line_reconciliation.get("ok") is True


def test_c_planner_source_calls_reconcile_line_partition() -> None:
    src = (SRC / "planner.py").read_text(encoding="utf-8")
    # Must be invoked inside _build_line_rows (production path for
    # build_institution_prospects_from_plans), not only defined.
    helper_def = src.find("def reconcile_line_partition")
    build_lines = src.find("def _build_line_rows")
    helper_call_in_build_lines = src.find("reconcile_line_partition(", build_lines)
    assert helper_def >= 0
    assert build_lines > helper_def
    assert helper_call_in_build_lines > build_lines
    # Planner must feed eligible_tender_ids into the line builder.
    from_plans = src.find("def build_institution_prospects_from_plans")
    call_chunk = src[from_plans : from_plans + 20000]
    assert "eligible_tender_ids=eligible_tender_ids" in call_chunk
    assert "_build_line_rows(" in call_chunk


# ---------------------------------------------------------------------------
# D. as_of queue safety
# ---------------------------------------------------------------------------


def test_d_elapsed_close_excluded_when_as_of_forwarded() -> None:
    row = {
        "institution_id": "inst-1",
        "display_name": "Buyer",
        "tender_code": "ELAPSED-1",
        "coalesced_tender_id": "elapsed-1",
        "canonical_equipment_category": "centrifuge",
        "projected_lifecycle_class": "active_open",
        "lifecycle_class": "active_open",
        "review_disposition": "catalog_fit_candidate",
        "commercial_signal_type": "equipment_purchase_signal",
        "catalog_fit_status": "catalog_fit_candidate",
        "catalog_match_status": "verified_catalog_equipment_class",
        "equipment_scopes": ["complete_equipment"],
        "purchase_intent": True,
        "opportunity_urgency_band": "high",
        "prospect_strength_band": "high",
        "closing_soon_bucket": "d4_to_d7",
        "publication_timestamp": "2026-07-01T00:00:00Z",
        "close_timestamp": "2026-07-15T00:00:00Z",  # before AS_OF
        "reason_codes": [],
        "eligibility_reason_codes": [],
    }
    line_row = {
        "coalesced_tender_id": "elapsed-1",
        "line_disposition": "assigned",
        "unit_id": "u1",
    }
    queues = build_operator_queues(
        profiles=[],
        tender_rows=[row],
        line_rows=[line_row],
        families=[],
        clusters=[],
        match_review=[],
        contact_gaps=[],
        as_of_utc=AS_OF,
    )
    assert queues["current_opportunity_queue"] == []


def test_d_planner_forwards_as_of_utc_to_build_operator_queues() -> None:
    src = inspect.getsource(
        __import__(
            "origenlab_email_pipeline.commercial_procurement_institution_prospects.planner",
            fromlist=["build_institution_prospects_from_plans"],
        ).build_institution_prospects_from_plans
    )
    assert "as_of_utc=as_of_utc" in src or "as_of_utc=as_of" in src
    assert "build_operator_queues(" in src
    # Call site must pass as_of into queues.
    call_start = src.find("build_operator_queues(")
    call_chunk = src[call_start : call_start + 400]
    assert "as_of_utc" in call_chunk

    queue_src = inspect.getsource(build_operator_queues)
    assert "_is_current_opportunity(row, evidence_count, as_of_utc=as_of_utc)" in (
        queue_src.replace(" ", "")
        .replace("\n", "")
    ) or "as_of_utc=as_of_utc" in queue_src


# ---------------------------------------------------------------------------
# E. Event-family exact-once reconciliation
# ---------------------------------------------------------------------------


def test_e_event_family_recon_detects_duplicates_with_counter() -> None:
    from origenlab_email_pipeline.commercial_procurement_institution_prospects import (
        planner,
    )

    fn = getattr(planner, "reconcile_event_family_coverage", None)
    assert fn is not None, "planner must expose reconcile_event_family_coverage()"

    result = fn(
        eligible_tender_ids=["a", "b", "c"],
        family_member_tender_ids=["a", "b", "b"],  # duplicate b, missing c
    )
    assert result["ok"] is False
    assert "c" in (result.get("missing_eligible_tender_ids") or [])
    assert "b" in (result.get("duplicate_member_tender_ids") or []) or (
        (result.get("duplicate_memberships") or {}).get("b", 0) > 1
    )


def test_e_event_family_recon_ok_derived_not_hardcoded_true() -> None:
    src = (SRC / "planner.py").read_text(encoding="utf-8")
    block_start = src.find("event_family_reconciliation")
    block = src[block_start : block_start + 900]
    assert '"ok": True' not in block and "'ok': True" not in block
    assert "Counter" in src or "reconcile_event_family_coverage" in src
