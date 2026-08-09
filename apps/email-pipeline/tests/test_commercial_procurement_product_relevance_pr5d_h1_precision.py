"""PR5D-H1 equipment-recognition precision regressions (ANEXO-E2 human gold).

Human gold packet (gitignored; tests are self-contained):
  version: anexo_e2_human_adjudication_v1_signed
  digest:  ebe8773c88ffc36dc2d2d6072eb4af345475537a01dabec0a6705f57a9dc18fe

H1 scope: unit-level precision only (accessory / maintenance / method /
bare-agitador / supplier-required). No new positive equipment classes.
No multi-equipment aggregation changes (deferred to H2).
"""

from __future__ import annotations

import pytest

from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
    ProductTextUnit,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.normalize import (
    normalize_product_text,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.rules import (
    classify_product_text_unit,
)

# Anchor for reviewers — must not be loaded at runtime from gitignored reports.
HUMAN_GOLD_PACKET_VERSION = "anexo_e2_human_adjudication_v1_signed"
HUMAN_GOLD_DIGEST = (
    "ebe8773c88ffc36dc2d2d6072eb4af345475537a01dabec0a6705f57a9dc18fe"
)


def _unit(text: str, *, tier: str = "line_product_text") -> ProductTextUnit:
    return ProductTextUnit(
        unit_id=f"u_{normalize_product_text(text)[:24] or 'empty'}",
        coalesced_tender_id="h1",
        evidence_ref_id="eref_h1",
        link_status="linked" if text else "unresolved_empty_text",
        unresolved_reason=None if text else "empty",
        field_path="line_observation[0].description",
        text_raw=text,
        text_normalized=normalize_product_text(text),
        evidence_tier=tier if text else "no_usable_product_text",
        source_plane="test",
        snapshot_id=None,
        observation_id=None,
        tender_observation_id=None,
        line_observation_id=None,
        pr4_procurement_id=None,
        contributing_evidence_ref_ids=("eref_h1",),
    )


def _classify(text: str):
    return classify_product_text_unit(_unit(text))


# --- A. Accessory / consumable -------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected_negative"),
    [
        (
            "PAPEL LIMPIA LENTES PARA MICROSCOPIO",
            "microscope_optics_or_camera_accessory_not_purchase",
        ),
        (
            "Cámara para Microscopio Hayear 500M - 5MP USB Tipo C",
            "microscope_optics_or_camera_accessory_not_purchase",
        ),
        (
            "Cámara Microscopio Hayear 500M - 5MP, USB Tipo C",
            "microscope_optics_or_camera_accessory_not_purchase",
        ),
    ],
)
def test_h1_microscope_accessory_not_equipment(
    text: str, expected_negative: str
) -> None:
    d = _classify(text)
    assert d.relevance_class == "consumable_or_reagent"
    assert "microscope" not in d.canonical_equipment_classes
    assert expected_negative in d.negative_reason_codes


def test_h1_genuine_microscope_equipment_retained() -> None:
    d = _classify("Microscopio Estereoscópico Binocular 20-40x")
    assert d.relevance_class == "strong_equipment_class"
    assert d.canonical_equipment_classes == ("microscope",)


def test_h1_sonotrodo_repuesto_not_ultrasonic_purchase() -> None:
    d = _classify("Sonotrodo de repuesto para procesador ultrasónico")
    assert d.relevance_class == "consumable_or_reagent"
    assert "ultrasonic_processor" not in d.canonical_equipment_classes
    assert (
        "ultrasonic_accessory_or_replacement_not_purchase" in d.negative_reason_codes
    )


def test_h1_processor_with_sonotrodo_remains_equipment() -> None:
    d = _classify("Procesador ultrasónico UP100H con sonotrodo")
    assert d.relevance_class == "strong_equipment_class"
    assert "ultrasonic_processor" in d.canonical_equipment_classes


@pytest.mark.parametrize(
    "text",
    [
        "PUNTA PARA PIPETA CON CORONA 100-10000 UI TIPO EPPENDORT",
        "Pipeta Pasteur plástica",
        "Pipeta graduada de vidrio",
        "Pipeta serológica estéril",
    ],
)
def test_h1_pipette_consumable_labware_is_not_equipment(text: str) -> None:
    """No pipette equipment detector today — guard against future over-broad rules."""
    d = _classify(text)
    assert d.canonical_equipment_classes == ()
    assert d.relevance_class not in {
        "strong_equipment_class",
        "compatible_equipment_class",
        "exact_catalog_product",
    }


# --- B. Maintenance / service --------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "mantención preventiva de centrífugas",
        "Capacitaciones en mantenciones de equipos (centrífugas)",
        "reparación de centrífuga",
        "calibración de balanza analítica",
    ],
)
def test_h1_maintenance_training_not_acquisition(text: str) -> None:
    d = _classify(text)
    assert d.relevance_class == "service_or_maintenance_only"
    assert d.canonical_equipment_classes == ()
    assert "service_or_maintenance_only" in d.negative_reason_codes


@pytest.mark.parametrize(
    ("text", "expected_class"),
    [
        ("adquisición de centrífuga de laboratorio", "centrifuge"),
        ("compra de balanza analítica", "balance"),
    ],
)
def test_h1_genuine_purchase_preserved(text: str, expected_class: str) -> None:
    d = _classify(text)
    assert d.relevance_class in {
        "strong_equipment_class",
        "compatible_equipment_class",
    }
    assert expected_class in d.canonical_equipment_classes


# --- Supplier-required equipment ----------------------------------------------


def test_h1_supplier_required_microscope_preserves_reference_not_acquisition() -> None:
    d = _classify(
        "El oferente debe disponer a su costo de un Microscopio Óptico"
    )
    assert d.relevance_class == "laboratory_context_only"
    assert "microscope" in d.canonical_equipment_classes
    assert (
        "supplier_required_equipment_not_buyer_purchase" in d.negative_reason_codes
    )
    assert "strong_equipment_class_match" not in d.positive_reason_codes


def test_h1_supplier_required_bare_agitador_no_three_way_fanout() -> None:
    d = _classify(
        "El oferente debe disponer a su costo de un agitador de muestra"
    )
    assert d.relevance_class == "ambiguous"
    assert d.canonical_equipment_classes == ()
    assert "context_required_agitador" in d.ambiguity_reason_codes
    for cls in ("homogenizer", "magnetic_stirrer", "shaker"):
        assert cls not in d.canonical_equipment_classes


# --- C. Method / exam ----------------------------------------------------------


def test_h1_sedimentation_exam_not_settlometer() -> None:
    d = _classify("03 01 086 | Velocidad de sedimentación (proc. aut.)")
    assert "sedimentation_settlometer" not in d.canonical_equipment_classes
    assert d.relevance_class == "laboratory_context_only"
    assert (
        "diagnostic_method_or_exam_term_not_equipment" in d.negative_reason_codes
    )


def test_h1_settlometer_equipment_preserved() -> None:
    d = _classify("Settlometer Nalgene")
    assert d.canonical_equipment_classes == ("sedimentation_settlometer",)


@pytest.mark.parametrize(
    "text",
    [
        "Hemoglobina glicada por HPLC",
        "Método de medición HPLC de intercambio iónico",
        "HEMOGLOBINA GLICADA POR HPLC",
    ],
)
def test_h1_hplc_method_language_not_instrument(text: str) -> None:
    d = _classify(text)
    assert d.canonical_equipment_classes == ()
    assert d.relevance_class not in {
        "strong_equipment_class",
        "compatible_equipment_class",
        "exact_catalog_product",
    }


@pytest.mark.parametrize(
    "text",
    [
        "Reactivo liofilizado para hematología",
        "Calibradores liofilizados",
    ],
)
def test_h1_liofilizado_adjective_not_lyophilizer(text: str) -> None:
    d = _classify(text)
    assert d.canonical_equipment_classes == ()
    assert d.relevance_class not in {
        "strong_equipment_class",
        "compatible_equipment_class",
        "exact_catalog_product",
    }


# --- D. Bare agitador ----------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected_class"),
    [
        ("Agitador magnético con calefacción", "magnetic_stirrer"),
        ("Agitador orbital", "shaker"),
        ("Homogeneizador", "homogenizer"),
    ],
)
def test_h1_specific_agitador_family_retained(
    text: str, expected_class: str
) -> None:
    d = _classify(text)
    assert d.relevance_class == "strong_equipment_class"
    assert d.canonical_equipment_classes == (expected_class,)


@pytest.mark.parametrize("text", ["Agitador de muestra", "Agitador"])
def test_h1_bare_agitador_no_class_fanout(text: str) -> None:
    d = _classify(text)
    assert d.relevance_class == "ambiguous"
    assert d.canonical_equipment_classes == ()
    assert "context_required_agitador" in d.ambiguity_reason_codes


def test_h1_gold_anchors_documented() -> None:
    assert HUMAN_GOLD_PACKET_VERSION == "anexo_e2_human_adjudication_v1_signed"
    assert len(HUMAN_GOLD_DIGEST) == 64
