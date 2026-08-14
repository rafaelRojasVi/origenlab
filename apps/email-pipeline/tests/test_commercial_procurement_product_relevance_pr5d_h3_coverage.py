"""PR5D-H3 canonical equipment recognition coverage (autoclave).

Recognition ≠ catalog capability. Autoclave already exists in the canonical
taxonomy; H3 adds a narrow complete-equipment detector without expanding the
OrigenLab catalog seed. Aggregation policy remains v3; taxonomy version
unchanged; rules version bumps to v4.
"""

from __future__ import annotations

import pytest

from origenlab_email_pipeline.commercial_procurement_anexo_shadow_prospects.capability import (
    classify_equipment_capability,
)
from origenlab_email_pipeline.commercial_procurement_anexo_shadow_prospects.constants import (
    CAPABILITY_OUT_OF_SCOPE,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.catalog_capability import (
    MATCH_OUTSIDE_CATALOG,
    match_catalog_status,
)
from origenlab_email_pipeline.commercial_procurement_live_relevance.taxonomy import (
    CANONICAL_EQUIPMENT_CLASSES,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.aggregate import (
    aggregation_policy_spec,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.constants import (
    PRODUCT_RELEVANCE_RULES_VERSION,
    PRODUCT_RELEVANCE_TAXONOMY_VERSION,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
    ProductTextUnit,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.normalize import (
    normalize_product_text,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.rules import (
    AUTOCLAVE_EQ_RE,
    classify_product_text_unit,
)


def _unit(text: str, *, tier: str = "line_product_text") -> ProductTextUnit:
    return ProductTextUnit(
        unit_id=f"u_{normalize_product_text(text)[:24] or 'empty'}",
        coalesced_tender_id="h3",
        evidence_ref_id="eref_h3",
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
        contributing_evidence_ref_ids=("eref_h3",),
    )


def _classify(text: str, *, tier: str = "line_product_text"):
    return classify_product_text_unit(_unit(text, tier=tier))


def test_h3_versioning_bumps_rules_only() -> None:
    assert PRODUCT_RELEVANCE_RULES_VERSION == "procurement_product_relevance_rules_v5"
    assert PRODUCT_RELEVANCE_TAXONOMY_VERSION == "procurement_product_relevance_taxonomy_v1"
    assert aggregation_policy_spec()["version"] == "aggregation_policy_v3"
    assert "autoclave" in CANONICAL_EQUIPMENT_CLASSES


@pytest.mark.parametrize(
    "text",
    [
        "adquisición de autoclave",
        "ADQUISICION DE AUTOCLAVE LABORATORIO",
        "compra de autoclaves",
        "adquisición de un autoclave",
        "adquisición de 2 autoclaves",
        "suministro de autoclave",
        "provisión de autoclave",
    ],
)
def test_h3_autoclave_acquisition_is_strong_equipment(text: str) -> None:
    d = _classify(text)
    assert d.canonical_equipment_classes == ("autoclave",)
    assert d.relevance_class == "strong_equipment_class"
    assert "strong_equipment_class_match" in d.positive_reason_codes


@pytest.mark.parametrize(
    "text",
    [
        "mantención de autoclave",
        "mantenimiento preventivo de autoclave",
        "reparación de autoclave",
    ],
)
def test_h3_autoclave_service_is_not_buyer_acquisition(text: str) -> None:
    d = _classify(text)
    assert d.relevance_class == "service_or_maintenance_only"
    assert d.canonical_equipment_classes == ()
    assert "service_or_maintenance_only" in d.negative_reason_codes


@pytest.mark.parametrize(
    "text",
    [
        "repuesto para autoclave",
        "accesorio para autoclave",
        "repuestos de autoclave",
        "adquisición de repuestos para autoclave",
        "compra de accesorios para autoclave",
        "suministro de piezas de repuesto para autoclave",
        "adquisición de piezas de repuesto para autoclave",
        "suministro de repuestos de autoclave",
    ],
)
def test_h3_autoclave_accessory_not_complete_equipment(text: str) -> None:
    d = _classify(text)
    assert "autoclave" not in d.canonical_equipment_classes
    assert d.relevance_class == "consumable_or_reagent"
    assert (
        "autoclave_accessory_or_replacement_not_purchase" in d.negative_reason_codes
    )


@pytest.mark.parametrize(
    "text",
    [
        "adquisición de autoclave y accesorios para autoclave",
        "compra de un autoclave con accesorios",
    ],
)
def test_h3_complete_autoclave_plus_accessories_keeps_equipment(text: str) -> None:
    d = _classify(text)
    assert d.canonical_equipment_classes == ("autoclave",)
    assert d.relevance_class == "strong_equipment_class"
    assert "strong_equipment_class_match" in d.positive_reason_codes
    assert "autoclave_accessory_or_replacement_not_purchase" not in d.negative_reason_codes


def test_h3_autoclave_with_singular_accessory_keeps_durable_bundle_flag() -> None:
    d = _classify("adquisición de autoclave y accesorio para autoclave")
    assert d.canonical_equipment_classes == ("autoclave",)
    assert d.relevance_class == "strong_equipment_class"
    assert "durable_equipment_with_accessories" in d.positive_reason_codes
    assert "autoclave_accessory_or_replacement_not_purchase" not in d.negative_reason_codes


def test_h3_supplier_required_autoclave_keeps_class_not_buyer_purchase() -> None:
    d = _classify("El oferente deberá disponer de autoclave de laboratorio")
    assert d.canonical_equipment_classes == ("autoclave",)
    assert d.relevance_class == "laboratory_context_only"
    assert "supplier_required_equipment_not_buyer_purchase" in d.negative_reason_codes


def test_h3_autoclave_recognition_does_not_imply_catalog_capability() -> None:
    """Central H3 contract: recognition ≠ sellable OrigenLab opportunity."""
    d = _classify("adquisición de autoclave de laboratorio")
    assert d.canonical_equipment_classes == ("autoclave",)
    assert match_catalog_status("autoclave", is_purchase_signal=True) == MATCH_OUTSIDE_CATALOG
    claim = classify_equipment_capability("autoclave")
    assert claim.capability == CAPABILITY_OUT_OF_SCOPE
    assert claim.catalog_match_status == MATCH_OUTSIDE_CATALOG
    # Equipment intelligence present; sellable opportunity false.
    assert AUTOCLAVE_EQ_RE.search(normalize_product_text("autoclave"))


@pytest.mark.parametrize(
    "text",
    [
        "Hemoglobina glicada por HPLC",
        "Método de medición HPLC de intercambio iónico",
        "HEMOGLOBINA GLICADA POR HPLC",
    ],
)
def test_h3_hplc_method_language_still_non_equipment(text: str) -> None:
    d = _classify(text)
    assert d.canonical_equipment_classes == ()
    assert "chromatography_hplc" not in d.canonical_equipment_classes
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
def test_h3_liofilizado_adjective_still_not_lyophilizer(text: str) -> None:
    d = _classify(text)
    assert d.canonical_equipment_classes == ()
    assert "lyophilizer" not in d.canonical_equipment_classes


@pytest.mark.parametrize(
    "text",
    [
        "PUNTA PARA PIPETA CON CORONA 100-10000 UI TIPO EPPENDORT",
        "Pipeta Pasteur plástica",
        "Pipeta graduada de vidrio",
        "Pipeta serológica estéril",
    ],
)
def test_h3_pipette_labware_still_not_equipment(text: str) -> None:
    d = _classify(text)
    assert d.canonical_equipment_classes == ()
    assert "pipette" not in d.canonical_equipment_classes
