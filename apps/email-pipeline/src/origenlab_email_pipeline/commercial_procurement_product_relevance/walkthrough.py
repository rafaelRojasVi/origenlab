"""Redacted Cases A–E walkthrough for PR5D product relevance (shareable DTOs only)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    CoalescedProcurementTender,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.output_safety import (
    write_atomically,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.aggregate import (
    aggregate_tender_decision,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
    ProductRelevancePlanResult,
    ProductTextUnit,
    TenderRelevanceDecision,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.normalize import (
    normalize_product_text,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.redaction import (
    domain_redacted_alias,
    redact_product_wording,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.rules import (
    classify_product_text_unit,
)

_SYNTHETIC = "SYNTHETIC_CONTRACT_FIXTURE"


def _shareable_unit(
    *,
    case_id: str,
    ordinal: int,
    text_redacted: str,
    evidence_tier: str,
    field_path: str,
) -> dict[str, Any]:
    return {
        "unit_alias": domain_redacted_alias("unit", f"{case_id}:{ordinal}:{field_path}"),
        "field_path": field_path,
        "text_redacted": text_redacted,
        "text_normalized": normalize_product_text(text_redacted),
        "evidence_tier": evidence_tier,
        "source_plane": "shareable",
    }


def _shareable_decision(
    *,
    case_id: str,
    relevance_class: str,
    classes: list[str],
    resolution: str,
    confidence_band: str,
    positive: list[str],
    negative: list[str],
    ambiguity: list[str],
    aggregation: list[str] | None = None,
    matched_rule_ids: list[str] | None = None,
    lifecycle_echo: str | None = None,
) -> dict[str, Any]:
    return {
        "decision_alias": domain_redacted_alias("decision", f"{case_id}:{relevance_class}"),
        "tender_alias": domain_redacted_alias("tender", f"walkthrough_{case_id}"),
        "relevance_class": relevance_class,
        "canonical_equipment_classes": classes,
        "product_resolution_status": resolution,
        "confidence_band": confidence_band,
        "positive_reason_codes": positive,
        "negative_reason_codes": negative,
        "ambiguity_reason_codes": ambiguity,
        "aggregation_reason_codes": list(aggregation or []),
        "matched_rule_ids": list(matched_rule_ids or []),
        "lifecycle_class_echo": lifecycle_echo,
        "not_persisted": True,
        "identifiers_omitted": True,
    }


def _case_from_text(
    *,
    case_id: str,
    label: str,
    raw_text: str,
    is_synthetic: bool,
    field_path: str = "synthetic.line.description",
    evidence_tier: str = "line_product_text",
    lifecycle_echo: str = "closed",
) -> dict[str, Any]:
    redacted, proof = redact_product_wording(raw_text)
    unit = ProductTextUnit(
        unit_id=f"walkthrough_{case_id}_unit",
        coalesced_tender_id=f"walkthrough_{case_id}_tender",
        evidence_ref_id=f"walkthrough_{case_id}_ref",
        link_status="linked",
        unresolved_reason=None,
        field_path=field_path,
        text_raw=redacted,
        text_normalized=normalize_product_text(redacted),
        evidence_tier=evidence_tier,
        source_plane="synthetic" if is_synthetic else "fixture",
        snapshot_id=None,
        observation_id=None,
        tender_observation_id=None,
        line_observation_id=None,
        pr4_procurement_id=None,
        contributing_evidence_ref_ids=(f"walkthrough_{case_id}_ref",),
    )
    unit_decision = classify_product_text_unit(unit)
    shareable_decision = _shareable_decision(
        case_id=case_id,
        relevance_class=unit_decision.relevance_class,
        classes=list(unit_decision.canonical_equipment_classes),
        resolution=unit_decision.product_resolution_status,
        confidence_band=unit_decision.confidence_band,
        positive=list(unit_decision.positive_reason_codes),
        negative=list(unit_decision.negative_reason_codes),
        ambiguity=list(unit_decision.ambiguity_reason_codes),
        matched_rule_ids=[s.rule_id for s in unit_decision.matched_spans],
        lifecycle_echo=lifecycle_echo,
    )
    return {
        "case_id": case_id,
        "label": label,
        "is_synthetic": is_synthetic,
        "synthetic_marker": _SYNTHETIC if is_synthetic else None,
        "steps": {
            "1_raw_source_redacted": redacted,
            "2_normalized_representation": normalize_product_text(redacted),
            "3_pr5c_identity_resolution": {
                "tender_alias": domain_redacted_alias("tender", f"walkthrough_{case_id}"),
                "identity_note": (
                    "Shareable alias only; operational identifiers omitted."
                ),
            },
            "4_product_evidence": [
                _shareable_unit(
                    case_id=case_id,
                    ordinal=0,
                    text_redacted=redacted,
                    evidence_tier=evidence_tier,
                    field_path=field_path,
                )
            ],
            "5_conflicts_or_ambiguity": list(unit_decision.ambiguity_reason_codes),
            "6_pr5c_lifecycle_result": {
                "lifecycle_class": lifecycle_echo,
                "note": "Lifecycle is PR5C dimension; shown for context only.",
            },
            "7_product_relevance_result": shareable_decision,
            "8_final_planned_object_not_persisted": shareable_decision,
        },
        "redaction_proof": proof,
    }


def _shareable_from_plan_decision(
    *,
    case_id: str,
    label: str,
    decision: TenderRelevanceDecision,
    units: list[ProductTextUnit],
) -> dict[str, Any]:
    raw = " || ".join(u.text_raw for u in units if u.text_raw) or decision.relevance_class
    redacted, proof = redact_product_wording(raw)
    shareable_units = []
    for i, u in enumerate(units[:5]):
        text_r, _ = redact_product_wording(u.text_raw or "")
        shareable_units.append(
            _shareable_unit(
                case_id=case_id,
                ordinal=i,
                text_redacted=text_r,
                evidence_tier=u.evidence_tier,
                field_path=u.field_path,
            )
        )
    shareable_decision = _shareable_decision(
        case_id=case_id,
        relevance_class=decision.relevance_class,
        classes=list(decision.canonical_equipment_classes),
        resolution=decision.product_resolution_status,
        confidence_band=decision.confidence_band,
        positive=list(decision.positive_reason_codes),
        negative=list(decision.negative_reason_codes),
        ambiguity=list(decision.ambiguity_reason_codes),
        aggregation=list(decision.aggregation_reason_codes),
        matched_rule_ids=[s.rule_id for s in decision.matched_spans],
        lifecycle_echo=decision.lifecycle_class_echo,
    )
    return {
        "case_id": case_id,
        "label": label,
        "is_synthetic": False,
        "synthetic_marker": None,
        "source": "production_derived_or_fixture_plan_shareable",
        "steps": {
            "1_raw_source_redacted": redacted,
            "2_normalized_representation": normalize_product_text(redacted),
            "3_pr5c_identity_resolution": {
                "tender_alias": domain_redacted_alias(
                    "tender", decision.coalesced_tender_id
                ),
                "lifecycle_class_echo": decision.lifecycle_class_echo,
            },
            "4_product_evidence": shareable_units,
            "5_conflicts_or_ambiguity": list(decision.ambiguity_reason_codes),
            "6_pr5c_lifecycle_result": {
                "lifecycle_class": decision.lifecycle_class_echo,
            },
            "7_product_relevance_result": shareable_decision,
            "8_final_planned_object_not_persisted": shareable_decision,
        },
        "redaction_proof": proof,
    }


def build_cases_a_e(
    result: ProductRelevancePlanResult | None = None,
    *,
    verify_redaction: bool = True,
) -> dict[str, Any]:
    """Build shareable redacted Cases A–E using explicit shareable DTOs only."""
    by_class: dict[str, TenderRelevanceDecision] = {}
    units_by_tender: dict[str, list[ProductTextUnit]] = {}
    if result is not None:
        for d in result.tender_decisions:
            by_class.setdefault(d.relevance_class, d)
        for u in list(result.product_text_units) + list(result.unresolved_units):
            units_by_tender.setdefault(u.coalesced_tender_id, []).append(u)

    def from_decision(
        case_id: str,
        label: str,
        classes: tuple[str, ...],
        synthetic_fallback: dict[str, Any],
    ) -> dict[str, Any]:
        for cls in classes:
            d = by_class.get(cls)
            if d is None:
                continue
            units = units_by_tender.get(d.coalesced_tender_id, [])
            return _shareable_from_plan_decision(
                case_id=case_id, label=label, decision=d, units=units
            )
        return synthetic_fallback

    case_a = from_decision(
        "A",
        "exact verified product/model match",
        ("exact_catalog_product",),
        _case_from_text(
            case_id="A",
            label="exact catalog path unavailable — class-level ultrasonic processor",
            raw_text=(
                f"{_SYNTHETIC}: Adquisición de procesador ultrasónico de sonda "
                "UP200St para laboratorio (capability seed; class-level match only)."
            ),
            is_synthetic=True,
        ),
    )
    if case_a.get("is_synthetic"):
        case_a["exact_catalog_status"] = (
            "no_verified_catalog_aliases_in_repo; "
            "classified_as_equipment_class_not_exact_catalog"
        )

    case_b = from_decision(
        "B",
        "strong or compatible equipment-class match without exact SKU",
        ("strong_equipment_class", "compatible_equipment_class"),
        _case_from_text(
            case_id="B",
            label="strong equipment-class match without exact SKU",
            raw_text=(
                f"{_SYNTHETIC}: Adquisición de centrífuga de laboratorio refrigerada"
            ),
            is_synthetic=True,
        ),
    )

    case_c = from_decision(
        "C",
        "service, consumable, rental, or non-laboratory hard negative",
        (
            "consumable_or_reagent",
            "service_or_maintenance_only",
            "rental_or_comodato",
            "non_laboratory_false_positive",
        ),
        _case_from_text(
            case_id="C",
            label="consumable hard negative — tubos para centrífuga",
            raw_text=f"{_SYNTHETIC}: Compra de tubos para centrífuga cónicos 50ml",
            is_synthetic=True,
        ),
    )

    case_d = from_decision(
        "D",
        "context-required alias and abstention",
        ("ambiguous",),
        _case_from_text(
            case_id="D",
            label="bare sonicator requires context — abstain",
            raw_text=f"{_SYNTHETIC}: Adquisición de sonicador para laboratorio",
            is_synthetic=True,
        ),
    )

    # Case E: mixed aggregation + insufficient sibling (both shareable).
    tender_e = CoalescedProcurementTender(
        coalesced_tender_id="walkthrough_E_tender",
        canonical_tender_key="walkthrough_E",
        identity_namespace="synthetic",
        tender_key_kind="synthetic",
        candidate_source_kind="pr4",
        pr4_procurement_id=None,
        pr4_procurement_ids=(),
        acquisition_snapshot_ids=(),
        acquisition_instance_ids=(),
        acquisition_observation_ids=(),
        coalescence_status="pr4_only",
        source_precedence_reason="synthetic",
        currentness_class="historical_pr4_only",
        lifecycle_class="closed",
        closing_soon_bucket="not_applicable",
        publication_timestamp_selected=None,
        close_timestamp_selected=None,
        status_code_selected=None,
        status_name_selected=None,
        status_value_selected=None,
        source_status_system_selected=None,
        buyer_display_selected=None,
        buyer_source_id_selected=None,
        title_selected=f"{_SYNTHETIC}: mixed equipment + accessories",
        selected_field_provenance={},
        buyer_display_variance=False,
        lifecycle_status_evidence_ref_id=None,
        lifecycle_close_evidence_ref_id=None,
        lifecycle_publication_evidence_ref_id=None,
        lifecycle_evidence_currentness_class=None,
        lifecycle_reason_codes=(),
        evidence_ref_ids=("walkthrough_E_ref",),
        conflict_ids=(),
    )
    u_pos = ProductTextUnit(
        unit_id="walkthrough_E_pos",
        coalesced_tender_id=tender_e.coalesced_tender_id,
        evidence_ref_id="walkthrough_E_ref",
        link_status="linked",
        unresolved_reason=None,
        field_path="synthetic.line[0]",
        text_raw=f"{_SYNTHETIC}: Adquisición de microscopio óptico de laboratorio",
        text_normalized=normalize_product_text(
            "Adquisición de microscopio óptico de laboratorio"
        ),
        evidence_tier="line_product_text",
        source_plane="synthetic",
        snapshot_id=None,
        observation_id=None,
        tender_observation_id=None,
        line_observation_id=None,
        pr4_procurement_id=None,
        contributing_evidence_ref_ids=("walkthrough_E_ref",),
    )
    u_neg = ProductTextUnit(
        unit_id="walkthrough_E_neg",
        coalesced_tender_id=tender_e.coalesced_tender_id,
        evidence_ref_id="walkthrough_E_ref",
        link_status="linked",
        unresolved_reason=None,
        field_path="synthetic.line[1]",
        text_raw=f"{_SYNTHETIC}: portaobjetos y cubreobjetos",
        text_normalized=normalize_product_text("portaobjetos y cubreobjetos"),
        evidence_tier="line_product_text",
        source_plane="synthetic",
        snapshot_id=None,
        observation_id=None,
        tender_observation_id=None,
        line_observation_id=None,
        pr4_procurement_id=None,
        contributing_evidence_ref_ids=("walkthrough_E_ref",),
    )
    d_pos = classify_product_text_unit(u_pos)
    d_neg = classify_product_text_unit(u_neg)
    agg_e = aggregate_tender_decision(
        tender_e, [d_pos, d_neg], input_fingerprint="walkthrough"
    )
    case_e_alt = _case_from_text(
        case_id="E_insufficient",
        label="insufficient product text — ambiguous (never silent unrelated)",
        raw_text=f"{_SYNTHETIC}: ",
        is_synthetic=True,
        evidence_tier="no_usable_product_text",
    )
    case_e = {
        "case_id": "E",
        "label": "mixed durable equipment + consumable lines (aggregation)",
        "is_synthetic": True,
        "synthetic_marker": _SYNTHETIC,
        "steps": {
            "1_raw_source_redacted": (
                f"{_SYNTHETIC}: Línea 1 microscopio óptico; Línea 2 portaobjetos"
            ),
            "2_normalized_representation": (
                f"{u_pos.text_normalized} || {u_neg.text_normalized}"
            ),
            "3_pr5c_identity_resolution": {
                "tender_alias": domain_redacted_alias("tender", "walkthrough_E"),
            },
            "4_product_evidence": [
                _shareable_unit(
                    case_id="E",
                    ordinal=0,
                    text_redacted=redact_product_wording(u_pos.text_raw)[0],
                    evidence_tier="line_product_text",
                    field_path="synthetic.line[0]",
                ),
                _shareable_unit(
                    case_id="E",
                    ordinal=1,
                    text_redacted=redact_product_wording(u_neg.text_raw)[0],
                    evidence_tier="line_product_text",
                    field_path="synthetic.line[1]",
                ),
            ],
            "5_conflicts_or_ambiguity": list(agg_e.ambiguity_reason_codes)
            + list(agg_e.aggregation_reason_codes),
            "6_pr5c_lifecycle_result": {"lifecycle_class": "closed"},
            "7_product_relevance_result": _shareable_decision(
                case_id="E",
                relevance_class=agg_e.relevance_class,
                classes=list(agg_e.canonical_equipment_classes),
                resolution=agg_e.product_resolution_status,
                confidence_band=agg_e.confidence_band,
                positive=list(agg_e.positive_reason_codes),
                negative=list(agg_e.negative_reason_codes),
                ambiguity=list(agg_e.ambiguity_reason_codes),
                aggregation=list(agg_e.aggregation_reason_codes),
                matched_rule_ids=[s.rule_id for s in agg_e.matched_spans],
                lifecycle_echo="closed",
            ),
            "8_final_planned_object_not_persisted": _shareable_decision(
                case_id="E",
                relevance_class=agg_e.relevance_class,
                classes=list(agg_e.canonical_equipment_classes),
                resolution=agg_e.product_resolution_status,
                confidence_band=agg_e.confidence_band,
                positive=list(agg_e.positive_reason_codes),
                negative=list(agg_e.negative_reason_codes),
                ambiguity=list(agg_e.ambiguity_reason_codes),
                aggregation=list(agg_e.aggregation_reason_codes),
                matched_rule_ids=[s.rule_id for s in agg_e.matched_spans],
                lifecycle_echo="closed",
            ),
        },
        "redaction_proof": redact_product_wording(
            f"{_SYNTHETIC}: microscopio / portaobjetos"
        )[1],
        "insufficient_evidence_sibling": case_e_alt,
    }

    bundle = {
        "schema": "pr5d_product_relevance_walkthrough_shareable_v2",
        "not_persisted": True,
        "shareable_only": True,
        "cases": {
            "A": case_a,
            "B": case_b,
            "C": case_c,
            "D": case_d,
            "E": case_e,
        },
        "machine_checkable_redaction_proofs": {
            k: v.get("redaction_proof")
            for k, v in {
                "A": case_a,
                "B": case_b,
                "C": case_c,
                "D": case_d,
                "E": case_e,
            }.items()
        },
    }
    if verify_redaction:
        # Ensure operational ID shapes and common PII markers do not leak.
        forbidden_keys = (
            "text_raw",
            "coalesced_tender_id",
            "evidence_ref_id",
            "snapshot_id",
            "observation_id",
            "line_observation_id",
            "pr4_procurement_id",
            "unit_id",
            "decision_id",
            "unit_decision_id",
        )
        blob = json.dumps(bundle, sort_keys=True, ensure_ascii=True)
        for key in forbidden_keys:
            # Allow the key name only inside documentation strings if quoted carefully;
            # operational serialization must not include these field names as payload keys.
            if f'"{key}"' in blob:
                raise AssertionError(
                    f"shareable walkthrough leaked operational field key: {key}"
                )
    return bundle


def write_walkthrough_into(
    bundle: dict[str, Any],
    dest_dir: Path,
) -> dict[str, str]:
    """Write walkthrough files into an already-staged destination directory."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / "WALKTHROUGH_CASES_A_E.json"
    path.write_text(
        json.dumps(bundle, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    md = dest_dir / "WALKTHROUGH_CASES_A_E.md"
    lines = [
        "# PR5D product relevance — Cases A–E (shareable redacted)",
        "",
        "All planned objects are **not persisted**. Operational identifiers omitted.",
        "",
    ]
    for case_id, case in bundle["cases"].items():
        lines.append(f"## Case {case_id}: {case.get('label')}")
        if case.get("is_synthetic"):
            lines.append(f"- Marker: `{case.get('synthetic_marker')}`")
        steps = case.get("steps") or {}
        lines.append(f"- Raw (redacted): {steps.get('1_raw_source_redacted')}")
        lines.append(f"- Normalized: `{steps.get('2_normalized_representation')}`")
        rel = steps.get("7_product_relevance_result") or {}
        lines.append(
            f"- Relevance: `{rel.get('relevance_class')}` "
            f"/ resolution `{rel.get('product_resolution_status')}`"
        )
        lines.append(
            f"- Planned object not persisted: `{rel.get('not_persisted')}`"
        )
        lines.append("")
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "WALKTHROUGH_CASES_A_E.json": str(path),
        "WALKTHROUGH_CASES_A_E.md": str(md),
    }


def write_walkthrough(
    bundle: dict[str, Any],
    out_dir: Path,
    *,
    repo_email_pipeline_root: Path | None = None,
    require_git_ignored: bool = True,
) -> dict[str, str]:
    root = repo_email_pipeline_root or Path(__file__).resolve().parents[3]

    def _write(safe: Path) -> dict[str, str]:
        return write_walkthrough_into(bundle, safe)

    return write_atomically(
        out_dir,
        repo_email_pipeline_root=root,
        writer=_write,
        require_git_ignored=require_git_ignored,
    )
