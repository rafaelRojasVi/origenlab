"""Equipment vocabulary mapping audit for PR5A (design only; no renames)."""

from __future__ import annotations

from typing import Any, Final

# Canonical internal equipment_class vocabulary (proposed).
CANONICAL_EQUIPMENT_CLASSES: Final = (
    "centrifuge",
    "balance",
    "autoclave",
    "microscope",
    "spectrophotometer",
    "chromatography_hplc",
    "incubator",
    "sonicator",
    "homogenizer",
    "osmometer",
    "ph_meter",
    "pipette",
    "plate_reader",
    "lyophilizer",
    "oven_muffle",
    "titrator",
    "shaker",
    "reactor",
    "ultrasonic_processor",
    "other_lab_equipment",
)

TAXONOMY_MAPPING: Final[list[dict[str, Any]]] = [
    {
        "canonical": "centrifuge",
        "business_mart_leads": ["centrifuga"],
        "equipment_first": ["centrifuge"],
        "web_families_or_filters": ["centrifugas", "microcentrifugas", "microcentrífuga"],
        "notes": "Spanish mart tag vs English queue category; web editorial family.",
    },
    {
        "canonical": "balance",
        "business_mart_leads": ["balanza", "termobalanza"],
        "equipment_first": ["balance"],
        "web_families_or_filters": [],
        "notes": "Non-lab balance exclusions already exist in equipment-first rules.",
    },
    {
        "canonical": "chromatography_hplc",
        "business_mart_leads": ["cromatografia_hplc"],
        "equipment_first": [],
        "web_families_or_filters": [],
        "notes": "Present in mart; absent from equipment-first EQUIPMENT_RULES.",
    },
    {
        "canonical": "ultrasonic_processor",
        "business_mart_leads": [],
        "equipment_first": ["lab_ultrasonic_processor", "sonicator"],
        "web_families_or_filters": [],
        "notes": "sonicator vs lab ultrasonic processor must stay distinct from clinical ultrasound.",
    },
    {
        "canonical": "incubator",
        "business_mart_leads": ["incubadora"],
        "equipment_first": ["incubator"],
        "web_families_or_filters": [],
        "notes": "Neonatal incubator excluded in equipment-first; keep exclusion.",
    },
    {
        "canonical": "reactor",
        "business_mart_leads": [],
        "equipment_first": [],
        "web_families_or_filters": [],
        "notes": "Operator catalogue / deal evidence (CRTOP/Ollital) — gap vs equipment-first.",
    },
    {
        "canonical": "osmometer",
        "business_mart_leads": ["osmometro"],
        "equipment_first": ["osmometer"],
        "web_families_or_filters": [],
        "notes": "Aligned.",
    },
    {
        "canonical": "homogenizer",
        "business_mart_leads": [],
        "equipment_first": ["homogenizer"],
        "web_families_or_filters": [],
        "notes": "English-only in equipment-first today.",
    },
    {
        "canonical": "autoclave",
        "business_mart_leads": ["autoclave"],
        "equipment_first": [],
        "web_families_or_filters": [],
        "notes": "Mart-only; add alias for PR5 relevance.",
    },
    {
        "canonical": "microscope",
        "business_mart_leads": ["microscopio"],
        "equipment_first": [],
        "web_families_or_filters": [],
        "notes": "Mart-only; equipment class, never exact SKU.",
    },
]

RECOMMENDATION: Final = {
    "canonical_vocabulary": "CANONICAL_EQUIPMENT_CLASSES",
    "alias_layers": [
        "business_mart / leads_equipment Spanish tags",
        "equipment_first English categories",
        "website families/filterTags (editorial only)",
        "optional ChileCompra UNSPSC / nivel_* as source aliases",
    ],
    "do_not": [
        "Silently rename persisted lead_master.equipment_tags or historical CSVs in PR5A",
        "Treat website filterTags as SKU truth",
        "Promote equipment_class to exact_catalog_product without model/SKU/alias evidence",
    ],
    "product_resolution_status_when_no_sku": "equipment_class_only",
}


def taxonomy_mapping_document() -> dict[str, Any]:
    return {
        "canonical_equipment_classes": list(CANONICAL_EQUIPMENT_CLASSES),
        "mappings": TAXONOMY_MAPPING,
        "recommendation": RECOMMENDATION,
    }
