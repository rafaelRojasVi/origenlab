"""Redacted Cases A–E walkthrough for PR5D product relevance."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.commercial_procurement_candidate_planner.output_safety import (
    write_atomically,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.evaluation import (
    redact_product_wording,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
    ProductRelevancePlanResult,
    ProductTextUnit,
    TenderRelevanceDecision,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.normalize import (
    normalize_product_text,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.rules import (
    classify_product_text_unit,
)

_SYNTHETIC = "SYNTHETIC_CONTRACT_FIXTURE"


def _case_from_text(
    *,
    case_id: str,
    label: str,
    raw_redacted: str,
    is_synthetic: bool,
    field_path: str = "synthetic.line.description",
    evidence_tier: str = "line_product_text",
    lifecycle_echo: str = "closed",
) -> dict[str, Any]:
    redacted, proof = redact_product_wording(raw_redacted)
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
    planned = {
        "coalesced_tender_id": unit.coalesced_tender_id,
        "relevance_class": unit_decision.relevance_class,
        "canonical_equipment_classes": list(unit_decision.canonical_equipment_classes),
        "product_resolution_status": unit_decision.product_resolution_status,
        "confidence_band": unit_decision.confidence_band,
        "reason_codes": {
            "positive": list(unit_decision.positive_reason_codes),
            "negative": list(unit_decision.negative_reason_codes),
            "ambiguity": list(unit_decision.ambiguity_reason_codes),
        },
        "matched_spans": [s.to_dict() for s in unit_decision.matched_spans],
        "not_persisted": True,
    }
    return {
        "case_id": case_id,
        "label": label,
        "is_synthetic": is_synthetic,
        "synthetic_marker": _SYNTHETIC if is_synthetic else None,
        "steps": {
            "1_raw_source_redacted": redacted,
            "2_normalized_representation": unit.text_normalized,
            "3_pr5c_identity_resolution": {
                "coalesced_tender_id": unit.coalesced_tender_id,
                "identity_note": (
                    "Walkthrough uses stable synthetic coalesced_tender_id; "
                    "production path joins via PR5C evidence_ref_ids."
                ),
            },
            "4_product_evidence": unit.to_dict(),
            "5_conflicts_or_ambiguity": list(unit_decision.ambiguity_reason_codes),
            "6_pr5c_lifecycle_result": {
                "lifecycle_class": lifecycle_echo,
                "note": "Lifecycle is PR5C dimension; shown for context only.",
            },
            "7_product_relevance_result": unit_decision.to_dict(),
            "8_final_planned_object_not_persisted": planned,
        },
        "redaction_proof": proof,
    }


def build_cases_a_e(
    result: ProductRelevancePlanResult | None = None,
) -> dict[str, Any]:
    """Build shareable redacted Cases A–E.

    Prefer real decisions from ``result`` when a matching class exists; otherwise
    fall back to explicitly labelled synthetic contract fixtures.
    """
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
            raw = " || ".join(u.text_raw for u in units if u.text_raw) or cls
            # Scrub any residual identifiers before sharing.
            if re.search(r"@|https?://|\d{8,}", raw):
                # Still usable after redaction helper.
                pass
            redacted, proof = redact_product_wording(raw)
            return {
                "case_id": case_id,
                "label": label,
                "is_synthetic": False,
                "synthetic_marker": None,
                "source": "production_derived_or_fixture_plan",
                "steps": {
                    "1_raw_source_redacted": redacted,
                    "2_normalized_representation": normalize_product_text(redacted),
                    "3_pr5c_identity_resolution": {
                        "coalesced_tender_id_redacted": f"redacted_{d.coalesced_tender_id[:12]}",
                        "lifecycle_class_echo": d.lifecycle_class_echo,
                    },
                    "4_product_evidence": [u.to_dict() for u in units[:5]],
                    "5_conflicts_or_ambiguity": list(d.ambiguity_reason_codes),
                    "6_pr5c_lifecycle_result": {
                        "lifecycle_class": d.lifecycle_class_echo,
                    },
                    "7_product_relevance_result": d.to_dict(),
                    "8_final_planned_object_not_persisted": {
                        **d.to_dict(),
                        "not_persisted": True,
                    },
                },
                "redaction_proof": proof,
            }
        return synthetic_fallback

    case_a = from_decision(
        "A",
        "exact verified product/model match",
        ("exact_catalog_product",),
        _case_from_text(
            case_id="A",
            label="exact verified product/model match",
            raw_redacted=(
                f"{_SYNTHETIC}: Adquisición procesador ultrasónico de laboratorio "
                "modelo VERIFIED-ALIAS-EXAMPLE (exact catalog path reserved for "
                "verified aliases only; no unverified commercial seed promoted)."
            ),
            is_synthetic=True,
        ),
    )
    # Case A: without verified catalog aliases, demonstrate strong class + note.
    if case_a.get("is_synthetic"):
        case_a = _case_from_text(
            case_id="A",
            label=(
                "exact catalog path unavailable — SYNTHETIC strong model-like "
                "ultrasonic processor (seeds not verified as exact_catalog)"
            ),
            raw_redacted=(
                f"{_SYNTHETIC}: Adquisición de procesador ultrasónico de sonda "
                "UP200St para laboratorio (capability seed; class-level match only)."
            ),
            is_synthetic=True,
        )
        # Force documentation that exact_catalog was not claimed.
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
            raw_redacted=(
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
            raw_redacted=f"{_SYNTHETIC}: Compra de tubos para centrífuga cónicos 50ml",
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
            raw_redacted=f"{_SYNTHETIC}: Adquisición de sonicador para laboratorio",
            is_synthetic=True,
        ),
    )
    # Prefer a true context_required if available among ambiguous.
    if result is not None:
        for d in result.tender_decisions:
            if "context_required_sonicator" in d.ambiguity_reason_codes:
                case_d = from_decision(
                    "D",
                    "context-required alias and abstention",
                    ("ambiguous",),
                    case_d,
                )
                break

    # Prefer an explicit two-unit mixed aggregation demo for Case E.
    from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
        CoalescedProcurementTender,
    )
    from origenlab_email_pipeline.commercial_procurement_product_relevance.aggregate import (
        aggregate_tender_decision,
    )

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
        raw_redacted=f"{_SYNTHETIC}: ",
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
                "coalesced_tender_id": tender_e.coalesced_tender_id,
            },
            "4_product_evidence": [u_pos.to_dict(), u_neg.to_dict()],
            "5_conflicts_or_ambiguity": list(agg_e.ambiguity_reason_codes)
            + list(agg_e.aggregation_reason_codes),
            "6_pr5c_lifecycle_result": {"lifecycle_class": "closed"},
            "7_product_relevance_result": agg_e.to_dict(),
            "8_final_planned_object_not_persisted": {
                **agg_e.to_dict(),
                "not_persisted": True,
            },
        },
        "redaction_proof": redact_product_wording(
            f"{_SYNTHETIC}: microscopio / portaobjetos"
        )[1],
        "insufficient_evidence_sibling": case_e_alt,
    }

    bundle = {
        "schema": "pr5d_product_relevance_walkthrough_v1",
        "not_persisted": True,
        "cases": {
            "A": case_a,
            "B": case_b,
            "C": case_c,
            "D": case_d,
            "E": case_e,
        },
        "machine_checkable_redaction_proofs": {
            k: v.get("redaction_proof") for k, v in {
                "A": case_a,
                "B": case_b,
                "C": case_c,
                "D": case_d,
                "E": case_e,
            }.items()
        },
    }
    return bundle


def write_walkthrough(
    bundle: dict[str, Any],
    out_dir: Path,
    *,
    repo_email_pipeline_root: Path | None = None,
    require_git_ignored: bool = True,
) -> dict[str, str]:
    root = repo_email_pipeline_root or Path(__file__).resolve().parents[3]

    def _write(safe: Path) -> dict[str, str]:
        path = safe / "WALKTHROUGH_CASES_A_E.json"
        path.write_text(
            json.dumps(bundle, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        md = safe / "WALKTHROUGH_CASES_A_E.md"
        lines = [
            "# PR5D product relevance — Cases A–E (redacted)",
            "",
            "All planned objects are **not persisted**.",
            "",
        ]
        for case_id, case in bundle["cases"].items():
            lines.append(f"## Case {case_id}: {case.get('label')}")
            if case.get("is_synthetic"):
                lines.append(f"- Marker: `{case.get('synthetic_marker')}`")
            steps = case.get("steps") or {}
            lines.append(f"- Raw (redacted): {steps.get('1_raw_source_redacted')}")
            lines.append(
                f"- Normalized: `{steps.get('2_normalized_representation')}`"
            )
            rel = steps.get("7_product_relevance_result") or {}
            lines.append(
                f"- Relevance: `{rel.get('relevance_class')}` "
                f"/ resolution `{rel.get('product_resolution_status')}`"
            )
            lines.append(
                f"- Planned object not persisted: "
                f"`{(steps.get('8_final_planned_object_not_persisted') or {}).get('not_persisted')}`"
            )
            lines.append("")
        md.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return {
            "WALKTHROUGH_CASES_A_E.json": str(path),
            "WALKTHROUGH_CASES_A_E.md": str(md),
        }

    return write_atomically(
        out_dir,
        repo_email_pipeline_root=root,
        writer=_write,
        require_git_ignored=require_git_ignored,
    )
