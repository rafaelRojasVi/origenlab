"""Catalog-scope / provisional disposition recognition (no tender-code allowlists)."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from origenlab_email_pipeline.commercial_procurement_institution_prospects.constants import (
    CONSUMABLE_RELEVANCE,
    EXCLUDED_RELEVANCE,
    INSTALLED_BASE_RELEVANCE,
    PURCHASE_RELEVANCE,
    RENTAL_RELEVANCE,
    REVIEW_RELEVANCE,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.signals import (
    classify_commercial_evidence_signal,
)

DISPOSITION_CATALOG_FIT = "catalog_fit_candidate"
DISPOSITION_POSSIBLE_FIT = "possible_fit_needs_specs"
DISPOSITION_OUTSIDE_CATALOG = "valid_equipment_outside_catalog"
DISPOSITION_INSTALLED_OR_RENTAL = "installed_base_or_rental"
DISPOSITION_CONSUMABLE = "consumable_or_accessory"
DISPOSITION_LEXICAL_FP = "lexical_false_positive"
DISPOSITION_REVIEW = "review_required"

# Verified current catalog equipment classes (OrigenLab commercial scope).
VERIFIED_CATALOG_CLASSES = frozenset(
    {
        "centrifuge",
        "incubator",
        "microscope",
        "balance",
        "homogenizer",
        "shaker",
        "magnetic_stirrer",
        "vortex_mixer",
        "ultrasonic_processor",
        "ultrasonic_bath",
        "tablet_hardness_tester",
        "dissolution_apparatus",
        "sedimentation_settlometer",
    }
)

OUTSIDE_CATALOG_RE = re.compile(
    r"neurocirug|microscopi[oa]\s+invertid|microscopia\s+especular|"
    r"topografia\s+corneal|bolsas?\s+de\s+sangre|"
    r"centrifuga\s+de\s+bolsas|tallimetro|caliper|"
    r"cintas?\s+metricas|laboratorio\s+de\s+ciencias|"
    r"materiales\s+para\s+laboratorio\s+de\s+ciencias|"
    r"camillas?\s+de\s+transporte|refrigeradores?\s+clinicos|"
    r"balanza\s+(digital\s+)?veterinari"
)
VETERINARY_CLINIC_RE = re.compile(
    r"clinica\s+veterinari|veterinaria\s+municipal|"
    r"atencion\s+en\s+veterinaria|equipamiento\s+clinica\s+veterinari"
)
SPECS_REQUIRED_RE = re.compile(
    r"equipos?\s+de\s+laboratorio\s+y\s+monitoreo|"
    r"implementacion\s+y\s+equipamiento|"
    r"insumos\s+para\s+laboratorio\s+comunal"
)
RENTAL_OR_SERVICE_TITLE_RE = re.compile(
    r"\barriendo\b|\bcomodato\b|\balquiler\b|"
    r"mantenimiento|mantencion|reparacion|"
    r"servicio\s+tecnico|serv\.\s*mtto|servicio\s+mant"
)
REACTIVO_TITLE_RE = re.compile(
    r"\breactivos?\b|\binsumos\s+de\s+laboratorio\b|"
    r"insumos\s+para\s+laboratorio|\bmanga\s+protectora\b|"
    r"funda\s+esteril|cono\s+centrifuga\s+bomba"
)


def _norm(text: str | None) -> str:
    if not text:
        return ""
    nk = unicodedata.normalize("NFKD", str(text))
    raw = "".join(c for c in nk if not unicodedata.combining(c)).casefold()
    return re.sub(r"\s+", " ", raw).strip()


def refine_commercial_signal(
    *,
    relevance_class: str,
    title: str | None,
    negative_reason_codes: tuple[str, ...] | list[str] = (),
) -> tuple[str, list[str]]:
    """Map PR5D relevance → commercial signal with title-level rental/service refine."""
    base = classify_commercial_evidence_signal(relevance_class)
    reasons: list[str] = []
    text = _norm(title)
    negs = set(negative_reason_codes)

    if "centrifugal_pump_not_lab_centrifuge" in negs or relevance_class in EXCLUDED_RELEVANCE:
        return "excluded_unrelated", ["lexical_or_excluded_relevance"]

    if relevance_class in CONSUMABLE_RELEVANCE or "microscope_cover_or_accessory_not_purchase" in negs:
        return "consumable_or_reagent_signal", ["consumable_relevance"]

    if relevance_class in INSTALLED_BASE_RELEVANCE:
        return "installed_base_signal", ["maintenance_relevance"]

    if relevance_class in RENTAL_RELEVANCE:
        return "rental_or_comodato_signal", ["rental_relevance"]

    if RENTAL_OR_SERVICE_TITLE_RE.search(text) and base == "equipment_purchase_signal":
        if re.search(r"\barriendo\b|\bcomodato\b|\balquiler\b", text):
            return "rental_or_comodato_signal", [
                "rental_title_overrides_equipment_keyword"
            ]
        return "installed_base_signal", [
            "maintenance_title_overrides_equipment_keyword"
        ]

    if REACTIVO_TITLE_RE.search(text) and base == "equipment_purchase_signal":
        return "consumable_or_reagent_signal", [
            "reagent_or_accessory_title_overrides_equipment_keyword"
        ]

    if relevance_class in REVIEW_RELEVANCE:
        return "review_required_signal", ["review_relevance"]

    if relevance_class in PURCHASE_RELEVANCE:
        return "equipment_purchase_signal", ["purchase_relevance"]

    return base, reasons or ["signal_passthrough"]


def classify_provisional_disposition(
    *,
    relevance_class: str,
    commercial_signal: str,
    canonical_equipment_classes: tuple[str, ...] | list[str],
    title: str | None,
    positive_reason_codes: tuple[str, ...] | list[str] = (),
    negative_reason_codes: tuple[str, ...] | list[str] = (),
    ambiguity_reason_codes: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    """
    Deterministic provisional disposition for operator review.

    Does not hard-code tender codes. Uses public text + relevance + signal only.
    Provenance for fixtures: analyst_reviewed_provisional.
    """
    text = _norm(title)
    classes = [c for c in canonical_equipment_classes if not str(c).startswith("relevance:")]
    negs = set(negative_reason_codes)
    ambs = set(ambiguity_reason_codes)
    reasons: list[str] = []

    if (
        commercial_signal == "excluded_unrelated"
        or relevance_class == "non_laboratory_false_positive"
        or "centrifugal_pump_not_lab_centrifuge" in negs
        or "non_laboratory_incubator" in negs
    ):
        return {
            "review_disposition": DISPOSITION_LEXICAL_FP,
            "commercial_signal_type": commercial_signal,
            "catalog_fit_status": "not_applicable",
            "canonical_equipment_category": classes[0] if classes else None,
            "reason_codes": sorted(
                {
                    "lexical_false_positive",
                    *negs,
                    *reasons,
                }
            ),
        }

    if commercial_signal in {
        "installed_base_signal",
        "rental_or_comodato_signal",
    } or relevance_class in INSTALLED_BASE_RELEVANCE | RENTAL_RELEVANCE:
        return {
            "review_disposition": DISPOSITION_INSTALLED_OR_RENTAL,
            "commercial_signal_type": commercial_signal,
            "catalog_fit_status": "not_purchase",
            "canonical_equipment_category": classes[0] if classes else None,
            "reason_codes": ["installed_base_or_rental_signal"],
        }

    if commercial_signal == "consumable_or_reagent_signal" or relevance_class in CONSUMABLE_RELEVANCE:
        return {
            "review_disposition": DISPOSITION_CONSUMABLE,
            "commercial_signal_type": commercial_signal,
            "catalog_fit_status": "not_purchase",
            "canonical_equipment_category": None,
            "reason_codes": ["consumable_or_accessory_signal"],
        }

    if OUTSIDE_CATALOG_RE.search(text) or (
        "anthropometric_or_veterinary_scale_not_analytical_balance" in ambs
        and not VETERINARY_CLINIC_RE.search(text)
    ):
        return {
            "review_disposition": DISPOSITION_OUTSIDE_CATALOG,
            "commercial_signal_type": commercial_signal
            if commercial_signal != "excluded_unrelated"
            else "equipment_purchase_signal",
            "catalog_fit_status": "outside_verified_catalog",
            "canonical_equipment_category": classes[0] if classes else None,
            "reason_codes": ["outside_verified_catalog_context"],
        }

    if VETERINARY_CLINIC_RE.search(text) or SPECS_REQUIRED_RE.search(text):
        return {
            "review_disposition": DISPOSITION_POSSIBLE_FIT,
            "commercial_signal_type": commercial_signal,
            "catalog_fit_status": "possible_fit_needs_specs",
            "canonical_equipment_category": classes[0] if classes else None,
            "reason_codes": ["possible_fit_specifications_required"],
        }

    catalog_classes = [c for c in classes if c in VERIFIED_CATALOG_CLASSES]
    if (
        commercial_signal == "equipment_purchase_signal"
        and catalog_classes
        and relevance_class in PURCHASE_RELEVANCE
    ):
        return {
            "review_disposition": DISPOSITION_CATALOG_FIT,
            "commercial_signal_type": commercial_signal,
            "catalog_fit_status": "catalog_fit_candidate",
            "canonical_equipment_category": catalog_classes[0],
            "reason_codes": ["verified_catalog_class_and_purchase_signal"],
        }

    if commercial_signal == "equipment_purchase_signal" and classes:
        # Equipment mentioned but not in verified catalog class set / weak context.
        return {
            "review_disposition": DISPOSITION_OUTSIDE_CATALOG,
            "commercial_signal_type": commercial_signal,
            "catalog_fit_status": "outside_verified_catalog",
            "canonical_equipment_category": classes[0],
            "reason_codes": ["equipment_outside_verified_catalog_classes"],
        }

    if commercial_signal == "review_required_signal" or relevance_class in REVIEW_RELEVANCE:
        # Mixed laboratory tenders with catalog classes still surface as catalog-fit
        # candidates when strong classes are present on the tender decision.
        if catalog_classes:
            return {
                "review_disposition": DISPOSITION_CATALOG_FIT,
                "commercial_signal_type": "equipment_purchase_signal",
                "catalog_fit_status": "catalog_fit_candidate",
                "canonical_equipment_category": catalog_classes[0],
                "reason_codes": [
                    "mixed_tender_retains_catalog_class_lines",
                    "ambiguous_aggregate_with_catalog_classes",
                ],
            }
        # Clinical supply / sampling context without durable purchase language.
        if re.search(
            r"servicios?\s+clinicos|toma\s+de\s+muestras|"
            r"insumos\s+para\s+laboratorio\s+clinico",
            text,
        ):
            return {
                "review_disposition": DISPOSITION_INSTALLED_OR_RENTAL,
                "commercial_signal_type": "installed_base_signal",
                "catalog_fit_status": "not_purchase",
                "canonical_equipment_category": None,
                "reason_codes": ["clinical_supply_installed_base_context"],
            }
        # Reagents / insumos titles without durable equipment → consumable.
        if REACTIVO_TITLE_RE.search(text) or re.search(
            r"\breactivos?\b|\binsumos\b", text
        ):
            return {
                "review_disposition": DISPOSITION_CONSUMABLE,
                "commercial_signal_type": "consumable_or_reagent_signal",
                "catalog_fit_status": "not_purchase",
                "canonical_equipment_category": None,
                "reason_codes": ["reagent_or_insumo_title_consumable"],
            }
        return {
            "review_disposition": DISPOSITION_REVIEW,
            "commercial_signal_type": commercial_signal,
            "catalog_fit_status": "review_required",
            "canonical_equipment_category": None,
            "reason_codes": ["review_required_no_catalog_class"],
        }

    return {
        "review_disposition": DISPOSITION_REVIEW,
        "commercial_signal_type": commercial_signal,
        "catalog_fit_status": "review_required",
        "canonical_equipment_category": classes[0] if classes else None,
        "reason_codes": ["disposition_fallback_review"],
    }


__all__ = [
    "DISPOSITION_CATALOG_FIT",
    "DISPOSITION_CONSUMABLE",
    "DISPOSITION_INSTALLED_OR_RENTAL",
    "DISPOSITION_LEXICAL_FP",
    "DISPOSITION_OUTSIDE_CATALOG",
    "DISPOSITION_POSSIBLE_FIT",
    "DISPOSITION_REVIEW",
    "VERIFIED_CATALOG_CLASSES",
    "classify_provisional_disposition",
    "refine_commercial_signal",
]
