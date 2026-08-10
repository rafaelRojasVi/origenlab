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
from origenlab_email_pipeline.commercial_procurement_product_relevance.semantic_payload import (
    digest_unit_semantic_payload,
    matched_rule_ids_from_spans,
    unit_semantic_payload,
)

CONSUMABLE_CENTRIFUGE_TUBE_RE = re.compile(
    r"\btubos?\s+(de\s+|para\s+)?(micro)?centrifug|"
    r"\btubos?\s+conic\w*\s+para\s+centrifug|"
    r"\btubos?\s+centrifug|"
    r"centrifug\w*\s+tubos?\b"
)
# Pump cone / centrifugal-pump supply accessory (consumable path before pump FP).
CENTRIFUGAL_PUMP_CONE_ACCESSORY_RE = re.compile(
    r"\bcono\s+centrifuga\s+bomba\b|\bcono\s+(de\s+)?bomba\s+centrifug|"
    r"revolution\s+centrifugal\s+pump"
)
CONSUMABLE_MICROSCOPE_RE = re.compile(
    r"\bportaobjetos\b|\bcubreobjetos\b|\blaminas?\s+para\s+microscop|"
    r"\bslides?\b|\bcoverslips?\b|\baceite\s+de\s+inmersion\b"
)
# Lens-cleaning paper / optics consumables and camera accessories for microscopes.
# Unit-level veto only — does not suppress genuine microscope equipment lines.
MICROSCOPE_OPTICS_OR_CAMERA_ACCESSORY_RE = re.compile(
    r"papel\s+(limpia\s+)?lentes?\s+(para\s+)?microscop|"
    r"papel\s+para\s+limpieza\s+de\s+lentes?\s+(de\s+)?microscop|"
    r"limpia\s+lentes?\s+para\s+microscop|"
    r"limpiador\w*\s+(de\s+)?lentes?\s+(para\s+)?microscop|"
    r"\bcamara\s+para\s+microscop|"
    r"\bcamara\s+microscop|"
    r"\bcamera\s+(for\s+)?microscope\b"
)
# Explicit microscope equipment purchase — overrides accessory-only consumable hit.
MICROSCOPE_PURCHASE_RE = re.compile(
    r"adquisicion\s+de\s+microscop|compra\s+de\s+microscop|microscopi[oa]\s+optico"
)
# Dynamic catalog-alias boundary template (alias contents live in taxonomy fingerprint).
CATALOG_ALIAS_BOUNDARY_TEMPLATE = r"(?<!\w){alias}(?!\w)"
CATALOG_ALIAS_BOUNDARY_FLAGS = 0
SERVICE_ONLY_RE = re.compile(
    r"mantenimiento\s+preventiv|mantenimiento\s+correctiv|"
    r"mantencion\s+preventiv|mantencion\s+correctiv|"
    r"\bmantenimiento\s+de\b|\bmantencion\s+de\b|"
    r"\bmantenciones?\s+(de\s+)?equipos?\b|"
    r"capacitacion(es)?\s+(en\s+)?mantencion|"
    r"\bserv\.\s*mtto\b|\bservicio\s+de\s+mant|"
    r"\breparacion\b|\bcalibracion\b|servicio\s+tecnico|"
    r"servicio\s+de\s+mantenimiento|instalacion\s+de\s+equipo\s+existente|"
    r"contratar\s+el\s+servicio"
)
EQUIPMENT_PURCHASE_RE = re.compile(
    r"adquisicion|compra\s+de|comprar\s+equipo|suministro\s+de\s+equipo|"
    r"provision\s+de\s+equipo|equipo\s+nuevo|reposicion\s+de\s+equipo"
)
# Explicit buyer-acquisition wording (stricter than EQUIPMENT_PURCHASE_RE).
# Used to keep supplier-required equipment from being treated as buyer purchase
# when the only "purchase-like" cue is a bare equipment name (e.g. microscopio optico).
BUYER_ACQUISITION_EXPLICIT_RE = re.compile(
    r"\badquisicion\b|\bcompra\s+de\b|\bcomprar\s+equipo\b|"
    r"\bsuministro\s+de\s+equipo\b|\bprovision\s+de\s+equipo\b|"
    r"\bequipo\s+nuevo\b|\breposicion\s+de\s+equipo\b"
)
# Supplier must own/provide equipment to deliver a service — not buyer acquisition.
SUPPLIER_REQUIRED_EQUIPMENT_RE = re.compile(
    r"\b(oferente|proveedor|contratista)\b.{0,100}\bdisponer\b|"
    r"\bdisponer\s+(a\s+su\s+costo|de\s+forma\s+permanente)|"
    r"\bdebe\s+disponer\b"
)
RENTAL_RE = re.compile(r"\barriendo\b|\bcomodato\b|\balquiler\b")
GENERIC_INSUMOS_RE = re.compile(
    r"\binsumos\s+de\s+laboratorio\b|\bmateriales?\s+de\s+laboratorio\b|"
    r"\breactivos?\b|\bconsumibles?\b"
)
NON_LAB_INCUBATOR_RE = re.compile(
    r"incubadora\s+de\s+transporte|neonatolog|modelo\s+giraffe|"
    r"incubadora\s+empresarial|business\s+incubator|"
    r"incubadoras?\s+de\s+negocios|incubadora\s+de\s+emprendimiento|"
    r"\bincubadora\b.*\b(feria|programacion|cbb)\b|"
    r"\b(feria|programacion|cbb).*\bincubadora\b"
)
NON_LAB_FALSE_POSITIVE_RE = re.compile(
    r"\becograf\b|\bultrasonograf\b|\bpozo\b|\bsala\s+de\s+bombas\b|"
    r"\bparvulario\b|\bdidactic"
)
# Industrial / fluid pumps — not laboratory centrifuges (line-scoped veto).
CENTRIFUGAL_PUMP_RE = re.compile(
    r"\bbombas?\s+centrifug|\bcono\s+(de\s+)?(centrifuga\s+)?bomba|"
    r"\bcentrifugal\s+pump\b|\brevolution\s+centrifugal\s+pump\b|"
    r"bombas?\s+sumergibles?\s+y\s+centrifug|bomba\s+centrifuga\s+horizontal"
)
ANTHROPOMETRIC_OR_VET_SCALE_RE = re.compile(
    r"\btallimetro|\bcaliper\b|\bcintas?\s+metricas?\b|"
    r"balanza\s+(digital\s+)?veterinari|"
    r"antropometr|atencion\s+en\s+box|box\s+veterinari"
)
MICROSCOPE_COVER_OR_ACCESSORY_RE = re.compile(
    r"\bmanga\s+protectora\s+microscop|funda\s+esteril\s+para\s+microscop|"
    r"cubierta\s+(para\s+)?microscop|protector\s+(de\s+)?microscop|"
    r"cover\s+(for\s+)?microscope"
)
# Replacement / accessory headed as the subject (not a complete processor purchase).
ULTRASONIC_ACCESSORY_OR_REPLACEMENT_RE = re.compile(
    r"\bsonotrodo\s+(de\s+)?repuesto\b|"
    r"\brepuesto\s+para\s+(procesador\s+ultrason|ultrason)|"
    r"\bsonotrodo\b.{0,50}\bpara\b.{0,50}(procesador|ultrason)|"
    r"\baccesorio\b.{0,50}\bpara\b.{0,50}(procesador|ultrason)"
)
# Headed autoclave accessory/replacement — not a complete autoclave purchase.
AUTOCLAVE_ACCESSORY_OR_REPLACEMENT_RE = re.compile(
    r"\brepuestos?\s+(para|de)\s+autoclaves?\b|"
    r"\baccesorios?\s+(para|de)\s+autoclaves?\b|"
    r"\bpiezas?\s+de\s+repuesto\b.{0,40}\bautoclaves?\b"
)
# Direct purchase of the autoclave instrument (not of parts/accessories for it).
# Distinct from BUYER_ACQUISITION_EXPLICIT_RE, which also matches
# "adquisicion de repuestos para autoclave".
AUTOCLAVE_PURCHASE_RE = re.compile(
    r"\b(adquisicion|compra|suministro|provision)\s+de\s+"
    r"((un|una|\d+)\s+)?"
    r"autoclaves?\b"
)
# Exam / procedure wording that mentions sedimentacion without an instrument.
DIAGNOSTIC_SEDIMENTATION_EXAM_RE = re.compile(
    r"velocidad\s+de\s+sedimentacion|"
    r"sedimentacion\s*\(\s*proc|"
    r"\bvs[gh]?\b.{0,20}sedimentacion"
)
CENTRIFUGE_EQ_RE = re.compile(r"\bcentrifug(a|adora|adoras|as)?\b|\bmicrocentrifug")
BALANCE_EQ_RE = re.compile(r"\bbalanza\b")
MICROSCOPE_EQ_RE = re.compile(r"\bmicroscopi[oa]s?\b")
INCUBATOR_EQ_RE = re.compile(r"\bincubadora\b|estufa\s+de\s+incubacion")
# Complete autoclave instrument wording only (H3). Do not infer from
# "esterilizacion" / "vapor" / biosafety alone — those are too broad.
AUTOCLAVE_EQ_RE = re.compile(r"\bautoclaves?\b")
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
# Instrument / kit context only — bare "sedimentacion" exam wording is excluded.
SEDIMENTATION_RE = re.compile(
    r"\bsettlometer\b|"
    r"\bnalgene\b.{0,40}sediment|"
    r"kit\s+de\s+sediment|"
    r"equipo\s+de\s+sedimentacion|"
    r"sedimentacion\s+(nalgene|kit|equipo)"
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
    unit_sem_fp = digest_unit_semantic_payload(
        unit_semantic_payload(
            relevance_class=relevance_class,
            classes=classes,
            resolution=resolution,
            evidence_tier=unit.evidence_tier,
            confidence_band=confidence_band,
            positive=positive,
            negative=negative,
            ambiguity=ambiguity,
            matched_rule_ids=matched_rule_ids_from_spans(spans),
        )
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


def _catalog_alias_pattern(alias_normalized: str) -> re.Pattern[str]:
    return re.compile(
        CATALOG_ALIAS_BOUNDARY_TEMPLATE.format(alias=re.escape(alias_normalized)),
        CATALOG_ALIAS_BOUNDARY_FLAGS,
    )


def _catalog_match(text: str) -> tuple[str | None, re.Match[str] | None]:
    for row in PROPOSED_CATALOG_ALIASES:
        if row.get("verification_status") != "verified_against_sanitized_evidence":
            continue
        for alias in row.get("aliases") or []:
            alias_n = normalize_product_text(alias)
            if not alias_n:
                continue
            m = _catalog_alias_pattern(alias_n).search(text)
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

    m_pump_cone = CENTRIFUGAL_PUMP_CONE_ACCESSORY_RE.search(text)
    if m_pump_cone:
        return _decision(
            unit,
            relevance_class="consumable_or_reagent",
            classes=[],
            resolution="negative_class_only",
            confidence_band="high",
            positive=[],
            negative=["consumable_or_reagent_only"],
            ambiguity=[],
            spans=[_span(field_path, "centrifugal_pump_cone_accessory", m_pump_cone)],
        )

    m_micro_acc = CONSUMABLE_MICROSCOPE_RE.search(text)
    # Accessory wording wins over a bare "para microscopio" mention unless the
    # unit is an explicit microscope equipment purchase.
    if m_micro_acc and not MICROSCOPE_PURCHASE_RE.search(text):
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

    m_micro_optics = MICROSCOPE_OPTICS_OR_CAMERA_ACCESSORY_RE.search(text)
    if m_micro_optics and not MICROSCOPE_PURCHASE_RE.search(text):
        return _decision(
            unit,
            relevance_class="consumable_or_reagent",
            classes=[],
            resolution="negative_class_only",
            confidence_band="high",
            positive=[],
            negative=["microscope_optics_or_camera_accessory_not_purchase"],
            ambiguity=[],
            spans=[
                _span(field_path, "microscope_optics_or_camera_accessory", m_micro_optics)
            ],
        )

    m_sono_acc = ULTRASONIC_ACCESSORY_OR_REPLACEMENT_RE.search(text)
    # Accessory/replacement as the subject (e.g. sonotrodo de repuesto para …).
    # Complete processor purchases that merely include a sonotrode still match
    # ULTRASONIC_PROCESSOR_RE without this headed-accessory shape.
    if m_sono_acc and not BUYER_ACQUISITION_EXPLICIT_RE.search(text):
        return _decision(
            unit,
            relevance_class="consumable_or_reagent",
            classes=[],
            resolution="negative_class_only",
            confidence_band="high",
            positive=[],
            negative=["ultrasonic_accessory_or_replacement_not_purchase"],
            ambiguity=[],
            spans=[
                _span(field_path, "ultrasonic_accessory_or_replacement", m_sono_acc)
            ],
        )

    m_auto_acc = AUTOCLAVE_ACCESSORY_OR_REPLACEMENT_RE.search(text)
    # Accessory/replacement as the subject of purchase (e.g. "adquisicion de
    # repuestos para autoclave") must not become autoclave equipment merely
    # because a broad buyer-acquisition verb is present. A direct autoclave
    # instrument purchase ("adquisicion de autoclave y accesorios…") keeps going.
    if m_auto_acc and not AUTOCLAVE_PURCHASE_RE.search(text):
        return _decision(
            unit,
            relevance_class="consumable_or_reagent",
            classes=[],
            resolution="negative_class_only",
            confidence_band="high",
            positive=[],
            negative=["autoclave_accessory_or_replacement_not_purchase"],
            ambiguity=[],
            spans=[
                _span(field_path, "autoclave_accessory_or_replacement", m_auto_acc)
            ],
        )

    m_sed_exam = DIAGNOSTIC_SEDIMENTATION_EXAM_RE.search(text)
    if m_sed_exam and not SEDIMENTATION_RE.search(text):
        # Abstain-class (not hard unrelated): exam rows must not create settlometer
        # claims, and must not push multi-unit annex aggregation into
        # negative+abstention mixed_requires_review / false annex_created_conflict.
        return _decision(
            unit,
            relevance_class="laboratory_context_only",
            classes=[],
            resolution="negative_class_only",
            confidence_band="high",
            positive=[],
            negative=["diagnostic_method_or_exam_term_not_equipment"],
            ambiguity=[],
            spans=[_span(field_path, "diagnostic_sedimentation_exam", m_sed_exam)],
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

    # Line-scoped veto: centrifugal pumps / blood-pump cones ≠ lab centrifuges.
    m_pump = CENTRIFUGAL_PUMP_RE.search(text)
    if m_pump:
        return _decision(
            unit,
            relevance_class="non_laboratory_false_positive",
            classes=[],
            resolution="negative_class_only",
            confidence_band="high",
            positive=[],
            negative=["centrifugal_pump_not_lab_centrifuge"],
            ambiguity=[],
            spans=[_span(field_path, "centrifugal_pump", m_pump)],
        )

    # Line-scoped veto: microscope covers / protective sleeves ≠ microscope purchase.
    m_micro_cover = MICROSCOPE_COVER_OR_ACCESSORY_RE.search(text)
    if m_micro_cover and not MICROSCOPE_PURCHASE_RE.search(text):
        return _decision(
            unit,
            relevance_class="consumable_or_reagent",
            classes=[],
            resolution="negative_class_only",
            confidence_band="high",
            positive=[],
            negative=["microscope_cover_or_accessory_not_purchase"],
            ambiguity=[],
            spans=[_span(field_path, "microscope_cover_accessory", m_micro_cover)],
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
            AUTOCLAVE_EQ_RE,
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
    if (
        m
        and not CONSUMABLE_CENTRIFUGE_TUBE_RE.search(text)
        and not CENTRIFUGAL_PUMP_RE.search(text)
    ):
        spans.append(_span(field_path, "centrifuge", m))
        classes.append("centrifuge")
        positive.append("strong_equipment_class_match")

    m = BALANCE_EQ_RE.search(text)
    if m:
        if ANTHROPOMETRIC_OR_VET_SCALE_RE.search(text):
            # Keep as equipment-class evidence but flag: not analytical-balance catalog fit.
            spans.append(_span(field_path, "balance_non_analytical_context", m))
            classes.append("balance")
            positive.append("compatible_equipment_class_match")
            ambiguity.append("anthropometric_or_veterinary_scale_not_analytical_balance")
        else:
            spans.append(_span(field_path, "balance", m))
            classes.append("balance")
            positive.append("compatible_equipment_class_match")

    m = MICROSCOPE_EQ_RE.search(text)
    if (
        m
        and (
            not CONSUMABLE_MICROSCOPE_RE.search(text)
            or MICROSCOPE_PURCHASE_RE.search(text)
        )
        and not MICROSCOPE_COVER_OR_ACCESSORY_RE.search(text)
        and not MICROSCOPE_OPTICS_OR_CAMERA_ACCESSORY_RE.search(text)
    ):
        spans.append(_span(field_path, "microscope", m))
        classes.append("microscope")
        positive.append("strong_equipment_class_match")

    m = INCUBATOR_EQ_RE.search(text)
    if m and not NON_LAB_INCUBATOR_RE.search(text):
        spans.append(_span(field_path, "incubator", m))
        classes.append("incubator")
        positive.append("strong_equipment_class_match")

    m = AUTOCLAVE_EQ_RE.search(text)
    # Keep autoclave when the unit is a direct instrument purchase even if an
    # accessory phrase also appears ("autoclave y accesorios para autoclave").
    accessory_subject = AUTOCLAVE_ACCESSORY_OR_REPLACEMENT_RE.search(text)
    direct_autoclave_purchase = AUTOCLAVE_PURCHASE_RE.search(text)
    if m and (not accessory_subject or direct_autoclave_purchase):
        spans.append(_span(field_path, "autoclave", m))
        classes.append("autoclave")
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
        # Generic "agitador" is ambiguous: do NOT fan out into shaker /
        # magnetic_stirrer / homogenizer as if three instruments were proven.
        return _decision(
            unit,
            relevance_class="ambiguous",
            classes=[],
            resolution="context_required_unresolved",
            confidence_band="abstain",
            positive=[],
            negative=[],
            ambiguity=["context_required_agitador"],
            spans=spans,
        )

    if classes:
        # Supplier must provide/own the equipment — keep the equipment reference
        # but do not promote as buyer acquisition (existing laboratory_context_only).
        m_supplier = SUPPLIER_REQUIRED_EQUIPMENT_RE.search(text)
        if m_supplier and not BUYER_ACQUISITION_EXPLICIT_RE.search(text):
            spans.append(_span(field_path, "supplier_required_equipment", m_supplier))
            return _decision(
                unit,
                relevance_class="laboratory_context_only",
                classes=classes,
                resolution="negative_class_only",
                confidence_band="medium",
                positive=[],
                negative=["supplier_required_equipment_not_buyer_purchase"],
                ambiguity=[],
                spans=spans,
            )
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
        "rule_id": "microscope_optics_or_camera_accessory",
        "outcome_class": "consumable_or_reagent",
        "confidence_band": "high",
        "note": "lens_paper_or_camera_for_microscope_ne_microscope_purchase",
    },
    {
        "rule_id": "ultrasonic_accessory_or_replacement",
        "outcome_class": "consumable_or_reagent",
        "confidence_band": "high",
        "note": "sonotrodo_repuesto_headed_accessory_ne_processor_purchase",
    },
    {
        "rule_id": "autoclave_accessory_or_replacement",
        "outcome_class": "consumable_or_reagent",
        "confidence_band": "high",
        "note": "repuesto_or_accesorio_para_autoclave_ne_complete_purchase",
        "override": "skipped_when_direct_autoclave_purchase",
    },
    {
        "rule_id": "diagnostic_sedimentation_exam",
        "outcome_class": "laboratory_context_only",
        "confidence_band": "high",
        "note": "exam_procedure_sedimentacion_ne_settlometer",
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
        "rule_id": "centrifugal_pump",
        "outcome_class": "non_laboratory_false_positive",
        "confidence_band": "high",
        "note": "line_scoped_veto_centrifugal_pump_ne_lab_centrifuge",
    },
    {
        "rule_id": "microscope_cover_accessory",
        "outcome_class": "consumable_or_reagent",
        "confidence_band": "high",
        "note": "line_scoped_veto_cover_ne_microscope_purchase",
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
        "note": "bare_agitador_does_not_fan_out_to_specific_classes",
    },
    {
        "rule_id": "supplier_required_equipment",
        "outcome_class": "laboratory_context_only",
        "confidence_band": "medium",
        "note": "equipment_reference_preserved_not_buyer_acquisition",
        "override": "skipped_when_explicit_buyer_acquisition",
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

    def _pat(compiled: re.Pattern[str]) -> dict[str, Any]:
        return {"pattern": compiled.pattern, "flags": int(compiled.flags)}

    pattern_digest = canonical_json_digest(
        {
            "consumable_centrifuge_tube": _pat(CONSUMABLE_CENTRIFUGE_TUBE_RE),
            "consumable_microscope": _pat(CONSUMABLE_MICROSCOPE_RE),
            "microscope_optics_or_camera_accessory": _pat(
                MICROSCOPE_OPTICS_OR_CAMERA_ACCESSORY_RE
            ),
            "ultrasonic_accessory_or_replacement": _pat(
                ULTRASONIC_ACCESSORY_OR_REPLACEMENT_RE
            ),
            "autoclave_accessory_or_replacement": _pat(
                AUTOCLAVE_ACCESSORY_OR_REPLACEMENT_RE
            ),
            "autoclave_purchase": _pat(AUTOCLAVE_PURCHASE_RE),
            "diagnostic_sedimentation_exam": _pat(DIAGNOSTIC_SEDIMENTATION_EXAM_RE),
            "microscope_purchase": _pat(MICROSCOPE_PURCHASE_RE),
            "catalog_alias_boundary": {
                "pattern": CATALOG_ALIAS_BOUNDARY_TEMPLATE,
                "flags": int(CATALOG_ALIAS_BOUNDARY_FLAGS),
            },
            "service_only": _pat(SERVICE_ONLY_RE),
            "equipment_purchase": _pat(EQUIPMENT_PURCHASE_RE),
            "buyer_acquisition_explicit": _pat(BUYER_ACQUISITION_EXPLICIT_RE),
            "supplier_required_equipment": _pat(SUPPLIER_REQUIRED_EQUIPMENT_RE),
            "rental": _pat(RENTAL_RE),
            "generic_insumos": _pat(GENERIC_INSUMOS_RE),
            "non_lab_incubator": _pat(NON_LAB_INCUBATOR_RE),
            "non_lab_fp": _pat(NON_LAB_FALSE_POSITIVE_RE),
            "centrifuge": _pat(CENTRIFUGE_EQ_RE),
            "balance": _pat(BALANCE_EQ_RE),
            "microscope": _pat(MICROSCOPE_EQ_RE),
            "incubator": _pat(INCUBATOR_EQ_RE),
            "autoclave": _pat(AUTOCLAVE_EQ_RE),
            "ultrasonic_processor": _pat(ULTRASONIC_PROCESSOR_RE),
            "ultrasonic_bath": _pat(ULTRASONIC_BATH_RE),
            "bare_sonicator": _pat(BARE_SONICATOR_RE),
            "homogenizer": _pat(HOMOGENIZER_RE),
            "orbital_shaker": _pat(ORBITAL_SHAKER_RE),
            "vortex": _pat(VORTEX_RE),
            "magnetic_stirrer": _pat(MAGNETIC_STIRRER_RE),
            "bare_agitador": _pat(BARE_AGITADOR_RE),
            "tablet_test": _pat(TABLET_TEST_RE),
            "dissolution": _pat(DISSOLUTION_RE),
            "sedimentation": _pat(SEDIMENTATION_RE),
            "accessory_bundle": _pat(ACCESSORY_BUNDLE_RE),
            "real_centrifuge_purchase": _pat(REAL_CENTRIFUGE_PURCHASE_RE),
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
