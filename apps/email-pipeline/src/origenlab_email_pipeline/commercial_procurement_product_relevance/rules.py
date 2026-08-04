"""Deterministic explainable product-relevance rules for PR5D."""

from __future__ import annotations

import re
from typing import Any, Final

from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.constants import (
    PRODUCT_RELEVANCE_RULES_VERSION,
    PRODUCT_RELEVANCE_TAXONOMY_VERSION,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
    EvidenceUnitRelevanceDecision,
    MatchedEvidenceSpan,
    ProductTextUnit,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.normalize import (
    normalize_product_text,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.taxonomy_extensions import (
    PROPOSED_CATALOG_ALIASES,
    taxonomy_fingerprint_payload,
)

CONSUMABLE_CENTRIFUGE_TUBE_RE = re.compile(
    r"\btubos?\s+(de\s+|para\s+)?(micro)?centrifug|"
    r"\btubos?\s+conic\w*\s+para\s+centrifug|"
    r"\btubos?\s+centrifug|"
    r"centrifug\w*\s+tubos?\b"
)
CONSUMABLE_MICROSCOPE_RE = re.compile(
    r"\bportaobjetos\b|\bcubreobjetos\b|\blaminas?\s+para\s+microscop|"
    r"\bslides?\b|\bcoverslips?\b|\baceite\s+de\s+inmersion\b"
)
SERVICE_ONLY_RE = re.compile(
    r"mantenimiento\s+preventiv|mantenimiento\s+correctiv|"
    r"mantencion\s+preventiv|mantencion\s+correctiv|"
    r"\breparacion\b|\bcalibracion\b|servicio\s+tecnico|"
    r"servicio\s+de\s+mantenimiento|instalacion\s+de\s+equipo\s+existente|"
    r"contratar\s+el\s+servicio"
)
EQUIPMENT_PURCHASE_RE = re.compile(
    r"adquisicion|compra\s+de|comprar\s+equipo|suministro\s+de\s+equipo|"
    r"provision\s+de\s+equipo|equipo\s+nuevo|reposicion\s+de\s+equipo"
)
RENTAL_RE = re.compile(r"\barriendo\b|\bcomodato\b|\balquiler\b")
GENERIC_INSUMOS_RE = re.compile(
    r"\binsumos\s+de\s+laboratorio\b|\bmateriales?\s+de\s+laboratorio\b|"
    r"\breactivos?\b|\bconsumibles?\b"
)
NON_LAB_INCUBATOR_RE = re.compile(
    r"incubadora\s+de\s+transporte|neonatolog|modelo\s+giraffe|"
    r"incubadora\s+empresarial|business\s+incubator|incubadora\s+de\s+negocios"
)
NON_LAB_FALSE_POSITIVE_RE = re.compile(
    r"\becograf\b|\bultrasonograf\b|\bpozo\b|\bsala\s+de\s+bombas\b|"
    r"\bparvulario\b|\bdidactic"
)
CENTRIFUGE_EQ_RE = re.compile(r"\bcentrifug(a|adora|adoras|as)?\b|\bmicrocentrifug")
BALANCE_EQ_RE = re.compile(r"\bbalanza\b")
MICROSCOPE_EQ_RE = re.compile(r"\bmicroscopi[oa]s?\b")
INCUBATOR_EQ_RE = re.compile(r"\bincubadora\b|estufa\s+de\s+incubacion")
ULTRASONIC_PROCESSOR_RE = re.compile(
    r"procesador\s+ultrasonico|ultrasonic\s+processor|"
    r"sonicador\s+de\s+sonda|\bprobe\s+sonicator\b|"
    r"\bup200st\b|\bup100h\b|\bup50h\b|\bvialtweeter\b|\bcuphorn\b|\buip400mtp\b"
)
ULTRASONIC_BATH_RE = re.compile(
    r"lavadora\s+ultrasonica|bano\s+ultrasonico|ultrasonic\s+bath"
)
BARE_SONICATOR_RE = re.compile(r"\b(sonicador|sonificador|sonicator)s?\b")
HOMOGENIZER_RE = re.compile(r"\bhomogeneizador(es)?\b|\bhomogenizer\b")
ORBITAL_SHAKER_RE = re.compile(r"agitador\s+orbital|\borbital\s+shaker\b|\bshaker\b")
VORTEX_RE = re.compile(r"\bvortex\b")
MAGNETIC_STIRRER_RE = re.compile(
    r"agitador\s+magnetico|magnetic\s+stirrer|\bstirrer\b"
)
BARE_AGITADOR_RE = re.compile(r"\bagitador(es)?\b")
TABLET_TEST_RE = re.compile(
    r"dureza\s+de\s+comprimidos|tablet\s+hardness|"
    r"\bptb\s*311e\b|\bwht\s*4\b|\bptb\s*500\b|ensayo\s+de\s+tabletas"
)
DISSOLUTION_RE = re.compile(
    r"\bdissolut\w*|\bdisolucion\s+de\s+comprimidos\b|apparatus\s+de\s+disolucion"
)
SEDIMENTATION_RE = re.compile(
    r"\bsettlometer\b|\bsedimentacion\b|\bnalgene\b.*sediment|"
    r"kit\s+de\s+sediment"
)
ACCESSORY_BUNDLE_RE = re.compile(
    r"\baccesorio\b|\brepuesto\b|\bsonotrodo\b|\btip\b"
)
REAL_CENTRIFUGE_PURCHASE_RE = re.compile(
    r"adquisicion\s+de\s+centrifug|equipo\s+centrifug|centrifugadora\s+de\s+laboratorio"
)


def _span(
    field_path: str, rule_id: str, match: re.Match[str]
) -> MatchedEvidenceSpan:
    return MatchedEvidenceSpan(
        field_path=field_path,
        rule_id=rule_id,
        matched_text=match.group(0)[:120],
        start=match.start(),
        end=match.end(),
    )


def _decision(
    unit: ProductTextUnit,
    *,
    relevance_class: str,
    classes: list[str],
    resolution: str,
    confidence_band: str,
    positive: list[str],
    negative: list[str],
    ambiguity: list[str],
    spans: list[MatchedEvidenceSpan],
) -> EvidenceUnitRelevanceDecision:
    payload = {
        "unit_id": unit.unit_id,
        "relevance_class": relevance_class,
        "classes": sorted(set(classes)),
        "resolution": resolution,
        "positive": sorted(positive),
        "negative": sorted(negative),
        "ambiguity": sorted(ambiguity),
        "rules_version": PRODUCT_RELEVANCE_RULES_VERSION,
    }
    decision_id = f"urd_{canonical_json_digest(payload)[:32]}"
    unit_input_fp = canonical_json_digest(
        {
            "unit_id": unit.unit_id,
            "text_normalized": unit.text_normalized,
            "evidence_tier": unit.evidence_tier,
            "field_path": unit.field_path,
        }
    )
    unit_sem_fp = canonical_json_digest(
        {
            "relevance_class": relevance_class,
            "classes": sorted(set(classes)),
            "resolution": resolution,
            "evidence_tier": unit.evidence_tier,
            "confidence_band": confidence_band,
            "positive": sorted(positive),
            "negative": sorted(negative),
            "ambiguity": sorted(ambiguity),
            # Rule IDs only — never raw matched_text (sensitive / non-semantic).
            "matched_rule_ids": sorted({s.rule_id for s in spans}),
        }
    )
    return EvidenceUnitRelevanceDecision(
        unit_decision_id=decision_id,
        unit_id=unit.unit_id,
        coalesced_tender_id=unit.coalesced_tender_id,
        relevance_class=relevance_class,
        canonical_equipment_classes=tuple(sorted(set(classes))),
        product_resolution_status=resolution,
        evidence_tier=unit.evidence_tier,
        confidence_band=confidence_band,
        positive_reason_codes=tuple(sorted(set(positive))),
        negative_reason_codes=tuple(sorted(set(negative))),
        ambiguity_reason_codes=tuple(sorted(set(ambiguity))),
        matched_spans=tuple(spans),
        contributing_evidence_ref_ids=unit.contributing_evidence_ref_ids,
        taxonomy_version=PRODUCT_RELEVANCE_TAXONOMY_VERSION,
        rules_version=PRODUCT_RELEVANCE_RULES_VERSION,
        unit_input_fingerprint=unit_input_fp,
        unit_semantic_fingerprint=unit_sem_fp,
    )


def _catalog_match(text: str) -> tuple[str | None, re.Match[str] | None]:
    for row in PROPOSED_CATALOG_ALIASES:
        if row.get("verification_status") != "verified_against_sanitized_evidence":
            continue
        for alias in row.get("aliases") or []:
            alias_n = normalize_product_text(alias)
            if not alias_n:
                continue
            pat = re.compile(rf"(?<!\w){re.escape(alias_n)}(?!\w)")
            m = pat.search(text)
            if m:
                return str(row["canonical_class"]), m
    return None, None


def classify_product_text_unit(unit: ProductTextUnit) -> EvidenceUnitRelevanceDecision:
    text = unit.text_normalized
    field_path = unit.field_path

    if not text or unit.evidence_tier == "no_usable_product_text":
        return _decision(
            unit,
            relevance_class="ambiguous",
            classes=[],
            resolution="insufficient_product_text",
            confidence_band="abstain",
            positive=[],
            negative=[],
            ambiguity=["insufficient_product_text"],
            spans=[],
        )

    canon, m_cat = _catalog_match(text)
    if canon and m_cat is not None:
        return _decision(
            unit,
            relevance_class="exact_catalog_product",
            classes=[canon],
            resolution="exact_catalog_verified",
            confidence_band="high",
            positive=["exact_catalog_model_match"],
            negative=[],
            ambiguity=[],
            spans=[_span(field_path, "exact_catalog_model", m_cat)],
        )

    m_tube = CONSUMABLE_CENTRIFUGE_TUBE_RE.search(text)
    if m_tube and not REAL_CENTRIFUGE_PURCHASE_RE.search(text):
        return _decision(
            unit,
            relevance_class="consumable_or_reagent",
            classes=[],
            resolution="negative_class_only",
            confidence_band="high",
            positive=[],
            negative=["consumable_centrifuge_tubes"],
            ambiguity=[],
            spans=[_span(field_path, "consumable_centrifuge_tubes", m_tube)],
        )

    m_micro_acc = CONSUMABLE_MICROSCOPE_RE.search(text)
    # Accessory wording wins over a bare "para microscopio" mention unless the
    # unit is an explicit microscope equipment purchase.
    if m_micro_acc and not re.search(
        r"adquisicion\s+de\s+microscop|compra\s+de\s+microscop|microscopi[oa]\s+optico",
        text,
    ):
        return _decision(
            unit,
            relevance_class="consumable_or_reagent",
            classes=[],
            resolution="negative_class_only",
            confidence_band="high",
            positive=[],
            negative=["consumable_microscope_accessories"],
            ambiguity=[],
            spans=[_span(field_path, "consumable_microscope_accessories", m_micro_acc)],
        )

    m_rent = RENTAL_RE.search(text)
    if m_rent and not EQUIPMENT_PURCHASE_RE.search(text):
        return _decision(
            unit,
            relevance_class="rental_or_comodato",
            classes=[],
            resolution="negative_class_only",
            confidence_band="high",
            positive=[],
            negative=["rental_or_comodato_only"],
            ambiguity=[],
            spans=[_span(field_path, "rental_or_comodato", m_rent)],
        )

    m_svc = SERVICE_ONLY_RE.search(text)
    if m_svc and not EQUIPMENT_PURCHASE_RE.search(text):
        return _decision(
            unit,
            relevance_class="service_or_maintenance_only",
            classes=[],
            resolution="negative_class_only",
            confidence_band="high",
            positive=[],
            negative=["service_or_maintenance_only"],
            ambiguity=[],
            spans=[_span(field_path, "service_or_maintenance", m_svc)],
        )

    m_nonlab_inc = NON_LAB_INCUBATOR_RE.search(text)
    if m_nonlab_inc:
        return _decision(
            unit,
            relevance_class="non_laboratory_false_positive",
            classes=[],
            resolution="negative_class_only",
            confidence_band="high",
            positive=[],
            negative=["non_laboratory_incubator"],
            ambiguity=[],
            spans=[_span(field_path, "non_lab_incubator", m_nonlab_inc)],
        )

    m_fp = NON_LAB_FALSE_POSITIVE_RE.search(text)
    if m_fp and not any(
        r.search(text)
        for r in (
            CENTRIFUGE_EQ_RE,
            ULTRASONIC_PROCESSOR_RE,
            ULTRASONIC_BATH_RE,
            MICROSCOPE_EQ_RE,
            BALANCE_EQ_RE,
        )
    ):
        return _decision(
            unit,
            relevance_class="non_laboratory_false_positive",
            classes=[],
            resolution="negative_class_only",
            confidence_band="medium",
            positive=[],
            negative=["non_laboratory_false_positive"],
            ambiguity=[],
            spans=[_span(field_path, "non_lab_false_positive", m_fp)],
        )

    if BARE_SONICATOR_RE.search(text) and not (
        ULTRASONIC_PROCESSOR_RE.search(text) or ULTRASONIC_BATH_RE.search(text)
    ):
        m = BARE_SONICATOR_RE.search(text)
        assert m is not None
        return _decision(
            unit,
            relevance_class="ambiguous",
            classes=["ultrasonic_processor", "ultrasonic_bath"],
            resolution="context_required_unresolved",
            confidence_band="abstain",
            positive=[],
            negative=[],
            ambiguity=["context_required_sonicator"],
            spans=[_span(field_path, "context_required_sonicator", m)],
        )

    spans: list[MatchedEvidenceSpan] = []
    classes: list[str] = []
    positive: list[str] = []
    ambiguity: list[str] = []

    m = ULTRASONIC_PROCESSOR_RE.search(text)
    if m:
        spans.append(_span(field_path, "ultrasonic_processor", m))
        classes.append("ultrasonic_processor")
        positive.append("strong_equipment_class_match")

    m = ULTRASONIC_BATH_RE.search(text)
    if m:
        spans.append(_span(field_path, "ultrasonic_bath", m))
        classes.append("ultrasonic_bath")
        positive.append("strong_equipment_class_match")

    m = CENTRIFUGE_EQ_RE.search(text)
    if m and not CONSUMABLE_CENTRIFUGE_TUBE_RE.search(text):
        spans.append(_span(field_path, "centrifuge", m))
        classes.append("centrifuge")
        positive.append("strong_equipment_class_match")

    m = BALANCE_EQ_RE.search(text)
    if m:
        spans.append(_span(field_path, "balance", m))
        classes.append("balance")
        positive.append("compatible_equipment_class_match")

    m = MICROSCOPE_EQ_RE.search(text)
    if m and (
        not CONSUMABLE_MICROSCOPE_RE.search(text)
        or re.search(
            r"adquisicion\s+de\s+microscop|compra\s+de\s+microscop|microscopi[oa]\s+optico",
            text,
        )
    ):
        spans.append(_span(field_path, "microscope", m))
        classes.append("microscope")
        positive.append("strong_equipment_class_match")

    m = INCUBATOR_EQ_RE.search(text)
    if m and not NON_LAB_INCUBATOR_RE.search(text):
        spans.append(_span(field_path, "incubator", m))
        classes.append("incubator")
        positive.append("strong_equipment_class_match")

    m = HOMOGENIZER_RE.search(text)
    if m:
        spans.append(_span(field_path, "homogenizer", m))
        classes.append("homogenizer")
        positive.append("strong_equipment_class_match")

    m = ORBITAL_SHAKER_RE.search(text)
    if m and "homogenizer" not in classes:
        spans.append(_span(field_path, "shaker", m))
        classes.append("shaker")
        positive.append("strong_equipment_class_match")

    m = VORTEX_RE.search(text)
    if m:
        spans.append(_span(field_path, "vortex_mixer", m))
        classes.append("vortex_mixer")
        positive.append("strong_equipment_class_match")

    m = MAGNETIC_STIRRER_RE.search(text)
    if m:
        spans.append(_span(field_path, "magnetic_stirrer", m))
        classes.append("magnetic_stirrer")
        positive.append("strong_equipment_class_match")

    m = TABLET_TEST_RE.search(text)
    if m:
        spans.append(_span(field_path, "tablet_hardness_tester", m))
        classes.append("tablet_hardness_tester")
        positive.append("compatible_equipment_class_match")

    m = DISSOLUTION_RE.search(text)
    if m:
        spans.append(_span(field_path, "dissolution_apparatus", m))
        classes.append("dissolution_apparatus")
        positive.append("compatible_equipment_class_match")

    m = SEDIMENTATION_RE.search(text)
    if m:
        spans.append(_span(field_path, "sedimentation_settlometer", m))
        classes.append("sedimentation_settlometer")
        positive.append("compatible_equipment_class_match")

    if (
        BARE_AGITADOR_RE.search(text)
        and not ORBITAL_SHAKER_RE.search(text)
        and not MAGNETIC_STIRRER_RE.search(text)
        and not HOMOGENIZER_RE.search(text)
    ):
        m = BARE_AGITADOR_RE.search(text)
        assert m is not None
        spans.append(_span(field_path, "context_required_agitador", m))
        return _decision(
            unit,
            relevance_class="ambiguous",
            classes=["shaker", "magnetic_stirrer", "homogenizer"],
            resolution="context_required_unresolved",
            confidence_band="abstain",
            positive=[],
            negative=[],
            ambiguity=["context_required_agitador"],
            spans=spans,
        )

    if classes:
        if ACCESSORY_BUNDLE_RE.search(text):
            positive.append("durable_equipment_with_accessories")
        rel = (
            "strong_equipment_class"
            if "strong_equipment_class_match" in positive
            else "compatible_equipment_class"
        )
        band = "high" if unit.evidence_tier == "line_product_text" else "medium"
        if unit.evidence_tier == "title_only":
            band = "low"
            ambiguity.append("title_only_weak_signal")
        return _decision(
            unit,
            relevance_class=rel,
            classes=classes,
            resolution="equipment_class_only",
            confidence_band=band,
            positive=positive,
            negative=[],
            ambiguity=ambiguity,
            spans=spans,
        )

    m_ins = GENERIC_INSUMOS_RE.search(text)
    if m_ins:
        return _decision(
            unit,
            relevance_class="laboratory_context_only",
            classes=[],
            resolution="negative_class_only",
            confidence_band="abstain",
            positive=[],
            negative=["generic_insumos_without_equipment"],
            ambiguity=[],
            spans=[_span(field_path, "generic_insumos", m_ins)],
        )

    # Absence of a known keyword is not proof of unrelatedness for title-only text.
    if unit.evidence_tier == "title_only":
        return _decision(
            unit,
            relevance_class="ambiguous",
            classes=[],
            resolution="insufficient_product_text",
            confidence_band="abstain",
            positive=[],
            negative=[],
            ambiguity=["title_only_weak_signal"],
            spans=[],
        )

    return _decision(
        unit,
        relevance_class="unrelated",
        classes=[],
        resolution="negative_class_only",
        confidence_band="medium",
        positive=[],
        negative=[],
        ambiguity=[],
        spans=[],
    )


# Declarative rule/aggregation contract used by execution semantics + fingerprints.
RULE_PRECEDENCE: Final[list[dict[str, Any]]] = [
    {
        "rule_id": "exact_catalog_model",
        "outcome_class": "exact_catalog_product",
        "confidence_band": "high",
        "requires_verified_catalog_alias": True,
    },
    {
        "rule_id": "consumable_centrifuge_tubes",
        "outcome_class": "consumable_or_reagent",
        "confidence_band": "high",
    },
    {
        "rule_id": "consumable_microscope_accessories",
        "outcome_class": "consumable_or_reagent",
        "confidence_band": "high",
    },
    {
        "rule_id": "rental_or_comodato",
        "outcome_class": "rental_or_comodato",
        "confidence_band": "high",
        "override": "skipped_when_equipment_purchase_signal",
    },
    {
        "rule_id": "service_or_maintenance",
        "outcome_class": "service_or_maintenance_only",
        "confidence_band": "high",
        "override": "skipped_when_equipment_purchase_signal",
    },
    {
        "rule_id": "non_lab_incubator",
        "outcome_class": "non_laboratory_false_positive",
        "confidence_band": "high",
    },
    {
        "rule_id": "non_lab_false_positive",
        "outcome_class": "non_laboratory_false_positive",
        "confidence_band": "medium",
    },
    {
        "rule_id": "context_required_sonicator",
        "outcome_class": "ambiguous",
        "confidence_band": "abstain",
        "resolution": "context_required_unresolved",
    },
    {
        "rule_id": "equipment_class_positive_hits",
        "outcome_classes": ["strong_equipment_class", "compatible_equipment_class"],
        "title_only_confidence_band": "low",
        "line_confidence_band": "high",
        "description_confidence_band": "medium",
    },
    {
        "rule_id": "context_required_agitador",
        "outcome_class": "ambiguous",
        "confidence_band": "abstain",
    },
    {
        "rule_id": "generic_insumos",
        "outcome_class": "laboratory_context_only",
        "confidence_band": "abstain",
        "manual_review_state": True,
    },
    {
        "rule_id": "title_only_no_match_abstain",
        "outcome_class": "ambiguous",
        "confidence_band": "abstain",
        "ambiguity": "title_only_weak_signal",
        "note": "Absence of keyword is not proof of unrelatedness",
    },
    {
        "rule_id": "line_or_description_no_match_unrelated",
        "outcome_class": "unrelated",
        "confidence_band": "medium",
        "applies_when_evidence_tier_not": "title_only",
    },
]


def rules_fingerprint_payload() -> dict[str, Any]:
    from origenlab_email_pipeline.commercial_procurement_product_relevance.aggregate import (
        aggregation_policy_spec,
    )

    pattern_digest = canonical_json_digest(
        {
            "consumable_centrifuge_tube": CONSUMABLE_CENTRIFUGE_TUBE_RE.pattern,
            "consumable_microscope": CONSUMABLE_MICROSCOPE_RE.pattern,
            "service_only": SERVICE_ONLY_RE.pattern,
            "equipment_purchase": EQUIPMENT_PURCHASE_RE.pattern,
            "rental": RENTAL_RE.pattern,
            "generic_insumos": GENERIC_INSUMOS_RE.pattern,
            "non_lab_incubator": NON_LAB_INCUBATOR_RE.pattern,
            "non_lab_fp": NON_LAB_FALSE_POSITIVE_RE.pattern,
            "centrifuge": CENTRIFUGE_EQ_RE.pattern,
            "balance": BALANCE_EQ_RE.pattern,
            "microscope": MICROSCOPE_EQ_RE.pattern,
            "incubator": INCUBATOR_EQ_RE.pattern,
            "ultrasonic_processor": ULTRASONIC_PROCESSOR_RE.pattern,
            "ultrasonic_bath": ULTRASONIC_BATH_RE.pattern,
            "bare_sonicator": BARE_SONICATOR_RE.pattern,
            "homogenizer": HOMOGENIZER_RE.pattern,
            "orbital_shaker": ORBITAL_SHAKER_RE.pattern,
            "vortex": VORTEX_RE.pattern,
            "magnetic_stirrer": MAGNETIC_STIRRER_RE.pattern,
            "bare_agitador": BARE_AGITADOR_RE.pattern,
            "tablet_test": TABLET_TEST_RE.pattern,
            "dissolution": DISSOLUTION_RE.pattern,
            "sedimentation": SEDIMENTATION_RE.pattern,
            "accessory_bundle": ACCESSORY_BUNDLE_RE.pattern,
            "real_centrifuge_purchase": REAL_CENTRIFUGE_PURCHASE_RE.pattern,
        }
    )
    return {
        "rules_version": PRODUCT_RELEVANCE_RULES_VERSION,
        "taxonomy": taxonomy_fingerprint_payload(),
        "pattern_digest": pattern_digest,
        "rule_precedence": RULE_PRECEDENCE,
        "aggregation_policy": aggregation_policy_spec(),
        "evidence_tier_behavior": {
            "title_only_no_hard_negative": "ambiguous+abstain+title_only_weak_signal",
            "title_only_with_equipment_hit": "class_match+low_confidence+title_only_weak_signal",
            "line_no_match": "unrelated",
            "empty_text": "ambiguous+insufficient_product_text",
        },
        "rule_ids": [r["rule_id"] for r in RULE_PRECEDENCE],
    }
