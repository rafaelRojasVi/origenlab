"""PR5D-H2 tender aggregation: multi-equipment vs true conflict; empty abstentions.

Stacked on H1. Aggregation policy v3.
Human gold: anexo_e2_human_adjudication_v1_signed
digest: ebe8773c88ffc36dc2d2d6072eb4af345475537a01dabec0a6705f57a9dc18fe
"""

from __future__ import annotations

from dataclasses import replace

from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    CoalescedProcurementTender,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.aggregate import (
    AGG_MULTI_EQUIPMENT,
    AGG_NEGATIVE_SURVIVES_EMPTY_ABSTAIN,
    aggregate_tender_decision,
    aggregation_policy_spec,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
    EvidenceUnitRelevanceDecision,
    ProductTextUnit,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.normalize import (
    normalize_product_text,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.rules import (
    classify_product_text_unit,
)

HUMAN_GOLD_DIGEST = (
    "ebe8773c88ffc36dc2d2d6072eb4af345475537a01dabec0a6705f57a9dc18fe"
)


def _tender(tid: str = "h2") -> CoalescedProcurementTender:
    return CoalescedProcurementTender(
        coalesced_tender_id=tid,
        canonical_tender_key=f"key_{tid}",
        identity_namespace="test",
        tender_key_kind="test",
        candidate_source_kind="pr4",
        pr4_procurement_id=None,
        pr4_procurement_ids=(),
        acquisition_snapshot_ids=(),
        acquisition_instance_ids=(),
        acquisition_observation_ids=(),
        coalescence_status="pr4_only",
        source_precedence_reason="test",
        currentness_class="historical_pr4_only",
        lifecycle_class="open_for_bidding",
        closing_soon_bucket="not_applicable",
        publication_timestamp_selected=None,
        close_timestamp_selected=None,
        status_code_selected=None,
        status_name_selected=None,
        status_value_selected=None,
        source_status_system_selected=None,
        buyer_display_selected=None,
        buyer_source_id_selected=None,
        title_selected="h2",
        selected_field_provenance={},
        buyer_display_variance=False,
        lifecycle_status_evidence_ref_id=None,
        lifecycle_close_evidence_ref_id=None,
        lifecycle_publication_evidence_ref_id=None,
        lifecycle_evidence_currentness_class=None,
        lifecycle_reason_codes=(),
        evidence_ref_ids=("eref",),
        conflict_ids=(),
    )


def _unit(text: str, *, tid: str = "h2", suffix: str = "") -> ProductTextUnit:
    n = normalize_product_text(text)
    return ProductTextUnit(
        unit_id=f"u_{n[:20]}_{suffix}" if suffix else f"u_{n[:24] or 'empty'}",
        coalesced_tender_id=tid,
        evidence_ref_id="eref",
        link_status="linked" if text else "unresolved_empty_text",
        unresolved_reason=None if text else "empty",
        field_path="line_observation[0].description",
        text_raw=text,
        text_normalized=n,
        evidence_tier="line_product_text" if text else "no_usable_product_text",
        source_plane="test",
        snapshot_id=None,
        observation_id=None,
        tender_observation_id=None,
        line_observation_id=None,
        pr4_procurement_id=None,
        contributing_evidence_ref_ids=("eref",),
    )


def _classify(text: str, *, suffix: str = "") -> EvidenceUnitRelevanceDecision:
    return classify_product_text_unit(_unit(text, suffix=suffix))


def test_h2_policy_version_is_v3() -> None:
    assert aggregation_policy_spec()["version"] == "aggregation_policy_v3"


def test_h2_centrifuge_plus_microscope_is_multi_equipment_not_conflict() -> None:
    a = _classify("Centrífuga refrigerada de laboratorio", suffix="a")
    b = _classify("Microscopio óptico de laboratorio", suffix="b")
    d = aggregate_tender_decision(_tender(), [a, b], input_fingerprint="x")
    assert d.relevance_class == "strong_equipment_class"
    assert d.product_resolution_status == "equipment_class_only"
    assert set(d.canonical_equipment_classes) == {"centrifuge", "microscope"}
    assert AGG_MULTI_EQUIPMENT in d.aggregation_reason_codes
    assert "conflicting_canonical_equipment_classes" not in d.ambiguity_reason_codes
    assert d.product_resolution_status != "mixed_requires_review"


def test_h2_balance_microscope_stirrer_combine() -> None:
    units = [
        _classify("Balanza analítica de laboratorio", suffix="bal"),
        _classify("Microscopio biológico monocular", suffix="mic"),
        _classify("Agitador magnético con calefacción", suffix="mag"),
    ]
    d = aggregate_tender_decision(_tender(), units, input_fingerprint="x")
    assert set(d.canonical_equipment_classes) == {
        "balance",
        "microscope",
        "magnetic_stirrer",
    }
    assert AGG_MULTI_EQUIPMENT in d.aggregation_reason_codes
    assert d.product_resolution_status != "mixed_requires_review"


def test_h2_same_class_multiple_units_preserves_precedence() -> None:
    a = _classify("Centrífuga refrigerada", suffix="a")
    b = replace(
        _classify("Centrífuga de laboratorio", suffix="b"),
        relevance_class="compatible_equipment_class",
        confidence_band="medium",
        unit_decision_id="u_compat_cent",
    )
    d = aggregate_tender_decision(_tender(), [a, b], input_fingerprint="x")
    assert d.relevance_class == "strong_equipment_class"
    assert "centrifuge" in d.canonical_equipment_classes
    assert "conflicting_canonical_equipment_classes" not in d.ambiguity_reason_codes


def test_h2_one_unit_multiple_classes_still_conflict() -> None:
    multi = _classify(
        "Centrífuga refrigerada y microscopio óptico de laboratorio",
        suffix="multi",
    )
    assert set(multi.canonical_equipment_classes) == {"centrifuge", "microscope"}
    d = aggregate_tender_decision(_tender(), [multi], input_fingerprint="x")
    assert d.relevance_class == "ambiguous"
    assert d.product_resolution_status == "mixed_requires_review"
    assert "conflicting_canonical_equipment_classes" in d.ambiguity_reason_codes


def test_h2_multi_equipment_survives_independent_negative() -> None:
    a = _classify("Centrífuga refrigerada de laboratorio", suffix="a")
    b = _classify("Microscopio óptico de laboratorio", suffix="b")
    neg = _classify("Portaobjetos y cubreobjetos", suffix="neg")
    d = aggregate_tender_decision(_tender(), [a, b, neg], input_fingerprint="x")
    assert "strong_positive_survives_negative_lines" in d.aggregation_reason_codes
    assert AGG_MULTI_EQUIPMENT in d.aggregation_reason_codes
    assert set(d.canonical_equipment_classes) == {"centrifuge", "microscope"}
    assert d.product_resolution_status != "mixed_requires_review"


def test_h2_multi_equipment_order_independent() -> None:
    a = _classify("Centrífuga refrigerada de laboratorio", suffix="a")
    b = _classify("Microscopio óptico de laboratorio", suffix="b")
    d1 = aggregate_tender_decision(_tender(), [a, b], input_fingerprint="x")
    d2 = aggregate_tender_decision(_tender(), [b, a], input_fingerprint="x")
    assert d1.semantic_fingerprint == d2.semantic_fingerprint
    assert d1.canonical_equipment_classes == d2.canonical_equipment_classes


# --- negative + abstention -----------------------------------------------------


def test_h2_method_exam_negative_plus_empty_abstention_survives() -> None:
    # diagnostic sedimentation → laboratory_context_only (empty classes) after H1
    exam = _classify("Velocidad de sedimentación (proc. aut.)", suffix="exam")
    assert exam.canonical_equipment_classes == ()
    # Force a hard negative alongside empty abstention
    maint = _classify("mantención preventiva de centrífugas", suffix="svc")
    empty = _classify("", suffix="empty")
    # Use consumable negative + empty abstention
    neg = _classify("Portaobjetos y cubreobjetos", suffix="neg")
    amb = replace(
        empty,
        relevance_class="ambiguous",
        product_resolution_status="insufficient_product_text",
        confidence_band="abstain",
        ambiguity_reason_codes=("insufficient_product_text",),
        canonical_equipment_classes=(),
        unit_decision_id="u_empty_abs",
    )
    d = aggregate_tender_decision(_tender(), [neg, amb], input_fingerprint="x")
    assert d.relevance_class == "consumable_or_reagent"
    assert d.product_resolution_status == "negative_class_only"
    assert AGG_NEGATIVE_SURVIVES_EMPTY_ABSTAIN in d.aggregation_reason_codes
    assert "conflicting_line_evidence" not in d.ambiguity_reason_codes
    assert exam.relevance_class == "laboratory_context_only"
    assert maint.relevance_class == "service_or_maintenance_only"


def test_h2_maintenance_plus_empty_abstention_survives() -> None:
    svc = _classify("calibración de balanza analítica", suffix="svc")
    amb = replace(
        _classify("", suffix="e"),
        relevance_class="ambiguous",
        product_resolution_status="insufficient_product_text",
        confidence_band="abstain",
        ambiguity_reason_codes=("insufficient_product_text",),
        canonical_equipment_classes=(),
        unit_decision_id="u_abs_empty",
    )
    d = aggregate_tender_decision(_tender(), [svc, amb], input_fingerprint="x")
    assert d.relevance_class == "service_or_maintenance_only"
    assert AGG_NEGATIVE_SURVIVES_EMPTY_ABSTAIN in d.aggregation_reason_codes


def test_h2_consumable_plus_empty_abstention_survives() -> None:
    neg = _classify("Tubos cónicos para centrífuga", suffix="tubes")
    amb = replace(
        _classify("", suffix="e2"),
        relevance_class="ambiguous",
        product_resolution_status="insufficient_product_text",
        confidence_band="abstain",
        ambiguity_reason_codes=("insufficient_product_text",),
        canonical_equipment_classes=(),
        unit_decision_id="u_abs2",
    )
    d = aggregate_tender_decision(_tender(), [neg, amb], input_fingerprint="x")
    assert d.relevance_class == "consumable_or_reagent"
    assert AGG_NEGATIVE_SURVIVES_EMPTY_ABSTAIN in d.aggregation_reason_codes


def test_h2_negative_plus_equipment_bearing_abstention_still_review() -> None:
    neg = _classify("Portaobjetos y cubreobjetos", suffix="neg")
    supplier = _classify(
        "El oferente debe disponer a su costo de un Microscopio Óptico",
        suffix="sup",
    )
    assert supplier.relevance_class == "laboratory_context_only"
    assert "microscope" in supplier.canonical_equipment_classes
    d = aggregate_tender_decision(_tender(), [neg, supplier], input_fingerprint="x")
    assert d.relevance_class == "ambiguous"
    assert d.product_resolution_status == "mixed_requires_review"
    assert "microscope" in d.canonical_equipment_classes
    assert "conflicting_line_evidence" in d.ambiguity_reason_codes


def test_h2_supplier_required_microscope_not_erased_by_negative() -> None:
    neg = _classify("Servicio de mantenimiento preventivo", suffix="svc")
    supplier = _classify(
        "El oferente debe disponer a su costo de un Microscopio Óptico",
        suffix="sup",
    )
    d = aggregate_tender_decision(_tender(), [neg, supplier], input_fingerprint="x")
    assert "microscope" in d.canonical_equipment_classes
    assert d.product_resolution_status == "mixed_requires_review"


def test_h2_only_abstentions_remain_ambiguous() -> None:
    a = replace(
        _classify("", suffix="a"),
        relevance_class="ambiguous",
        product_resolution_status="insufficient_product_text",
        confidence_band="abstain",
        ambiguity_reason_codes=("insufficient_product_text",),
        unit_decision_id="u_a",
    )
    b = replace(
        _classify("", suffix="b"),
        relevance_class="laboratory_context_only",
        product_resolution_status="negative_class_only",
        confidence_band="abstain",
        negative_reason_codes=("generic_insumos_without_equipment",),
        canonical_equipment_classes=(),
        unit_decision_id="u_b",
    )
    d = aggregate_tender_decision(_tender(), [a, b], input_fingerprint="x")
    assert d.relevance_class == "ambiguous"
    assert "all_units_ambiguous_or_empty" in d.aggregation_reason_codes


def test_h2_empty_never_silent_unrelated() -> None:
    empty = _classify("", suffix="empty")
    d = aggregate_tender_decision(_tender(), [empty], input_fingerprint="x")
    assert d.relevance_class == "ambiguous"
    assert d.product_resolution_status == "insufficient_product_text"


def test_h2_gold_digest_anchor() -> None:
    assert len(HUMAN_GOLD_DIGEST) == 64
