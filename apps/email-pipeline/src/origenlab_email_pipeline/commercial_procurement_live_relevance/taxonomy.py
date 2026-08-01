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
    "ultrasonic_processor",
    "ultrasonic_bath",
    "homogenizer",
    "shaker",
    "vortex_mixer",
    "magnetic_stirrer",
    "osmometer",
    "ph_meter",
    "pipette",
    "plate_reader",
    "lyophilizer",
    "oven_muffle",
    "titrator",
    "reactor",
    "other_lab_equipment",
)

# Explicit future/unimplemented markers (still valid canonical entries).
FUTURE_OR_UNIMPLEMENTED: Final = frozenset(
    {
        "reactor",  # deal/catalogue evidence exists; equipment-first gap
        "other_lab_equipment",
    }
)

TAXONOMY_MAPPING: Final[list[dict[str, Any]]] = [
    {
        "canonical": "centrifuge",
        "business_mart_leads": ["centrifuga"],
        "equipment_first": ["centrifuge"],
        "web_families_or_filters": ["centrifugas", "microcentrifugas", "microcentrífuga"],
        "ambiguous": False,
        "notes": "Spanish mart tag vs English queue category; web editorial family.",
    },
    {
        "canonical": "balance",
        "business_mart_leads": ["balanza", "termobalanza"],
        "equipment_first": ["balance"],
        "web_families_or_filters": [],
        "ambiguous": False,
        "notes": "Non-lab balance exclusions already exist in equipment-first rules.",
    },
    {
        "canonical": "chromatography_hplc",
        "business_mart_leads": ["cromatografia_hplc"],
        "equipment_first": [],
        "web_families_or_filters": [],
        "ambiguous": False,
        "notes": "Present in mart; absent from equipment-first EQUIPMENT_RULES.",
    },
    {
        "canonical": "ultrasonic_processor",
        "business_mart_leads": [],
        "equipment_first": ["lab_ultrasonic_processor"],
        "web_families_or_filters": [],
        "ambiguous": False,
        "notes": (
            "Probe/processor class. Legacy equipment-first label `sonicator` is a "
            "source alias requiring contextual resolution — not a canonical class."
        ),
    },
    {
        "canonical": "ultrasonic_bath",
        "business_mart_leads": [],
        "equipment_first": [],
        "web_families_or_filters": [],
        "source_aliases": ["lavadora_ultrasonica", "ultrasonic_bath"],
        "ambiguous": False,
        "notes": "Bath/washer class; must not collapse into ultrasonic_processor.",
    },
    {
        "canonical": "ultrasonic_processor",
        "business_mart_leads": [],
        "equipment_first": ["sonicator"],
        "web_families_or_filters": [],
        "ambiguous": True,
        "ambiguity_reason": (
            "equipment-first `sonicator` regex also matches lavadora ultrasónica; "
            "resolve contextually to ultrasonic_processor vs ultrasonic_bath"
        ),
        "notes": "Alias-only row; not a second canonical class.",
    },
    {
        "canonical": "incubator",
        "business_mart_leads": ["incubadora"],
        "equipment_first": ["incubator"],
        "web_families_or_filters": [],
        "ambiguous": False,
        "notes": "Neonatal incubator excluded in equipment-first; keep exclusion.",
    },
    {
        "canonical": "homogenizer",
        "business_mart_leads": [],
        "equipment_first": ["homogenizer"],
        "web_families_or_filters": [],
        "ambiguous": False,
        "notes": (
            "Must NOT absorb shaker / vortex / magnetic stirrer. Current "
            "equipment-first homogenizer regex includes agitador|vortex — treat "
            "those hits as ambiguous until disambiguated to shaker/vortex_mixer/"
            "magnetic_stirrer."
        ),
    },
    {
        "canonical": "shaker",
        "business_mart_leads": ["shaker"],
        "equipment_first": [],
        "web_families_or_filters": [],
        "source_aliases": ["agitador", "agitador_orbital"],
        "ambiguous": False,
        "notes": "Split out from homogenizer absorption risk.",
    },
    {
        "canonical": "vortex_mixer",
        "business_mart_leads": [],
        "equipment_first": [],
        "web_families_or_filters": [],
        "source_aliases": ["vortex"],
        "ambiguous": False,
        "notes": "Do not map vortex → homogenizer.",
    },
    {
        "canonical": "magnetic_stirrer",
        "business_mart_leads": [],
        "equipment_first": [],
        "web_families_or_filters": [],
        "source_aliases": ["agitador_magnetico", "magnetic_stirrer"],
        "ambiguous": False,
        "notes": "Do not map magnetic stirrer → homogenizer.",
    },
    {
        "canonical": "reactor",
        "business_mart_leads": [],
        "equipment_first": [],
        "web_families_or_filters": [],
        "ambiguous": False,
        "notes": "Operator catalogue / deal evidence (CRTOP/Ollital) — gap vs equipment-first.",
        "future_or_unimplemented": True,
    },
    {
        "canonical": "osmometer",
        "business_mart_leads": ["osmometro"],
        "equipment_first": ["osmometer"],
        "web_families_or_filters": [],
        "ambiguous": False,
        "notes": "Aligned.",
    },
    {
        "canonical": "spectrophotometer",
        "business_mart_leads": ["espectrofotometro", "fluorescencia"],
        "equipment_first": [],
        "web_families_or_filters": [],
        "ambiguous": False,
        "notes": "Mart-only.",
    },
    {
        "canonical": "ph_meter",
        "business_mart_leads": ["phmetro"],
        "equipment_first": [],
        "web_families_or_filters": [],
        "ambiguous": False,
        "notes": "Mart-only.",
    },
    {
        "canonical": "pipette",
        "business_mart_leads": ["pipetas"],
        "equipment_first": [],
        "web_families_or_filters": [],
        "ambiguous": False,
        "notes": "Mart-only; watch consumable pipette tip exclusions separately.",
    },
    {
        "canonical": "plate_reader",
        "business_mart_leads": ["lector_placas"],
        "equipment_first": [],
        "web_families_or_filters": [],
        "ambiguous": False,
        "notes": "Mart-only.",
    },
    {
        "canonical": "lyophilizer",
        "business_mart_leads": ["liofilizador"],
        "equipment_first": [],
        "web_families_or_filters": [],
        "ambiguous": False,
        "notes": "Mart-only.",
    },
    {
        "canonical": "oven_muffle",
        "business_mart_leads": ["horno_mufla"],
        "equipment_first": [],
        "web_families_or_filters": [],
        "ambiguous": False,
        "notes": "Mart-only.",
    },
    {
        "canonical": "titrator",
        "business_mart_leads": ["titulador"],
        "equipment_first": [],
        "web_families_or_filters": [],
        "ambiguous": False,
        "notes": "Mart-only.",
    },
    {
        "canonical": "autoclave",
        "business_mart_leads": ["autoclave"],
        "equipment_first": [],
        "web_families_or_filters": [],
        "ambiguous": False,
        "notes": "Mart-only; add alias for PR5 relevance.",
    },
    {
        "canonical": "microscope",
        "business_mart_leads": ["microscopio"],
        "equipment_first": [],
        "web_families_or_filters": [],
        "ambiguous": False,
        "notes": "Mart-only; equipment class, never exact SKU.",
    },
]

RECOMMENDATION: Final = {
    "canonical_vocabulary": "CANONICAL_EQUIPMENT_CLASSES",
    "sonicator_policy": (
        "sonicator is a source alias, not a canonical class; resolve to "
        "ultrasonic_processor or ultrasonic_bath from context"
    ),
    "homogenizer_policy": (
        "shaker, vortex_mixer, and magnetic_stirrer are distinct canonical classes; "
        "do not silently absorb them into homogenizer"
    ),
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
        "Keep sonicator as a canonical class",
    ],
    "product_resolution_status_when_no_sku": "equipment_class_only",
}


def taxonomy_mapping_document() -> dict[str, Any]:
    return {
        "canonical_equipment_classes": list(CANONICAL_EQUIPMENT_CLASSES),
        "future_or_unimplemented": sorted(FUTURE_OR_UNIMPLEMENTED),
        "mappings": TAXONOMY_MAPPING,
        "recommendation": RECOMMENDATION,
    }


def validate_taxonomy_mapping_completeness(
    mappings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return completeness diagnostics used by unit tests."""
    rows = mappings if mappings is not None else TAXONOMY_MAPPING
    canonical = set(CANONICAL_EQUIPMENT_CLASSES)
    errors: list[str] = []
    alias_to_canonical: dict[str, set[str]] = {}

    mapped_canonicals: set[str] = set()
    for row in rows:
        c = str(row.get("canonical") or "")
        if c not in canonical:
            errors.append(f"unknown_canonical:{c}")
            continue
        mapped_canonicals.add(c)
        aliases: list[str] = []
        for key in (
            "business_mart_leads",
            "equipment_first",
            "web_families_or_filters",
            "source_aliases",
        ):
            aliases.extend(str(a) for a in (row.get(key) or []))
        for alias in aliases:
            alias_l = alias.strip().lower()
            if not alias_l:
                continue
            alias_to_canonical.setdefault(alias_l, set()).add(c)

    for alias, targets in sorted(alias_to_canonical.items()):
        if len(targets) > 1:
            # Allowed only when every contributing row is marked ambiguous
            ambiguous_ok = all(
                bool(r.get("ambiguous"))
                for r in rows
                if str(r.get("canonical")) in targets
                and alias
                in {
                    str(a).strip().lower()
                    for key in (
                        "business_mart_leads",
                        "equipment_first",
                        "web_families_or_filters",
                        "source_aliases",
                    )
                    for a in (r.get(key) or [])
                }
            )
            if not ambiguous_ok:
                errors.append(f"alias_multi_canonical:{alias}->{sorted(targets)}")

    unmapped = sorted(
        c
        for c in canonical
        if c not in mapped_canonicals and c not in FUTURE_OR_UNIMPLEMENTED
    )
    for c in unmapped:
        errors.append(f"unmapped_canonical:{c}")

    return {
        "ok": not errors,
        "errors": errors,
        "mapped_canonicals": sorted(mapped_canonicals),
        "alias_count": len(alias_to_canonical),
    }
