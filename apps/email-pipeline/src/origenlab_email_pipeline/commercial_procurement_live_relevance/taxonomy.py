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

# Exact canonical-class mappings (one alias → one canonical).
EXACT_CLASS_MAPPINGS: Final[list[dict[str, Any]]] = [
    {
        "canonical": "centrifuge",
        "source_vocabulary": "business_mart_leads",
        "source_aliases": ["centrifuga"],
        "resolution_kind": "exact",
    },
    {
        "canonical": "centrifuge",
        "source_vocabulary": "equipment_first",
        "source_aliases": ["centrifuge"],
        "resolution_kind": "exact",
    },
    {
        "canonical": "centrifuge",
        "source_vocabulary": "web_families_or_filters",
        "source_aliases": ["centrifugas", "microcentrifugas", "microcentrífuga"],
        "resolution_kind": "exact",
    },
    {
        "canonical": "balance",
        "source_vocabulary": "business_mart_leads",
        "source_aliases": ["balanza", "termobalanza"],
        "resolution_kind": "exact",
    },
    {
        "canonical": "balance",
        "source_vocabulary": "equipment_first",
        "source_aliases": ["balance"],
        "resolution_kind": "exact",
    },
    {
        "canonical": "chromatography_hplc",
        "source_vocabulary": "business_mart_leads",
        "source_aliases": ["cromatografia_hplc"],
        "resolution_kind": "exact",
    },
    {
        "canonical": "ultrasonic_processor",
        "source_vocabulary": "equipment_first",
        "source_aliases": ["lab_ultrasonic_processor"],
        "resolution_kind": "exact",
        "notes": "Probe/processor class.",
    },
    {
        "canonical": "ultrasonic_bath",
        "source_vocabulary": "source_aliases",
        "source_aliases": ["lavadora_ultrasonica", "ultrasonic_bath"],
        "resolution_kind": "exact",
        "notes": "Bath/washer class; must not collapse into ultrasonic_processor.",
    },
    {
        "canonical": "incubator",
        "source_vocabulary": "business_mart_leads",
        "source_aliases": ["incubadora"],
        "resolution_kind": "exact",
    },
    {
        "canonical": "incubator",
        "source_vocabulary": "equipment_first",
        "source_aliases": ["incubator"],
        "resolution_kind": "exact",
    },
    {
        "canonical": "homogenizer",
        "source_vocabulary": "equipment_first",
        "source_aliases": ["homogenizer"],
        "resolution_kind": "exact",
        "notes": "Exact homogenizer spans only — not agitador/vortex/magnetic.",
    },
    {
        "canonical": "shaker",
        "source_vocabulary": "business_mart_leads",
        "source_aliases": ["shaker"],
        "resolution_kind": "exact",
    },
    {
        "canonical": "shaker",
        "source_vocabulary": "source_aliases",
        "source_aliases": ["agitador_orbital"],
        "resolution_kind": "exact",
    },
    {
        "canonical": "vortex_mixer",
        "source_vocabulary": "source_aliases",
        "source_aliases": ["vortex"],
        "resolution_kind": "exact",
        "notes": "Do not map vortex → homogenizer.",
    },
    {
        "canonical": "magnetic_stirrer",
        "source_vocabulary": "source_aliases",
        "source_aliases": ["agitador_magnetico", "magnetic_stirrer"],
        "resolution_kind": "exact",
        "notes": "Do not map magnetic stirrer → homogenizer.",
    },
    {
        "canonical": "reactor",
        "source_vocabulary": "future",
        "source_aliases": [],
        "resolution_kind": "exact",
        "future_or_unimplemented": True,
        "notes": "Operator catalogue / deal evidence (CRTOP/Ollital) — gap vs equipment-first.",
    },
    {
        "canonical": "osmometer",
        "source_vocabulary": "business_mart_leads",
        "source_aliases": ["osmometro"],
        "resolution_kind": "exact",
    },
    {
        "canonical": "osmometer",
        "source_vocabulary": "equipment_first",
        "source_aliases": ["osmometer"],
        "resolution_kind": "exact",
    },
    {
        "canonical": "spectrophotometer",
        "source_vocabulary": "business_mart_leads",
        "source_aliases": ["espectrofotometro", "fluorescencia"],
        "resolution_kind": "exact",
    },
    {
        "canonical": "ph_meter",
        "source_vocabulary": "business_mart_leads",
        "source_aliases": ["phmetro"],
        "resolution_kind": "exact",
    },
    {
        "canonical": "pipette",
        "source_vocabulary": "business_mart_leads",
        "source_aliases": ["pipetas"],
        "resolution_kind": "exact",
    },
    {
        "canonical": "plate_reader",
        "source_vocabulary": "business_mart_leads",
        "source_aliases": ["lector_placas"],
        "resolution_kind": "exact",
    },
    {
        "canonical": "lyophilizer",
        "source_vocabulary": "business_mart_leads",
        "source_aliases": ["liofilizador"],
        "resolution_kind": "exact",
    },
    {
        "canonical": "oven_muffle",
        "source_vocabulary": "business_mart_leads",
        "source_aliases": ["horno_mufla"],
        "resolution_kind": "exact",
    },
    {
        "canonical": "titrator",
        "source_vocabulary": "business_mart_leads",
        "source_aliases": ["titulador"],
        "resolution_kind": "exact",
    },
    {
        "canonical": "autoclave",
        "source_vocabulary": "business_mart_leads",
        "source_aliases": ["autoclave"],
        "resolution_kind": "exact",
    },
    {
        "canonical": "microscope",
        "source_vocabulary": "business_mart_leads",
        "source_aliases": ["microscopio"],
        "resolution_kind": "exact",
    },
]

# Ambiguous / context-required source-alias resolution (machine-readable).
CONTEXT_REQUIRED_ALIASES: Final[list[dict[str, Any]]] = [
    {
        "source_vocabulary": "equipment_first",
        "source_alias": "sonicator",
        "resolution_kind": "context_required",
        "candidate_classes": ["ultrasonic_processor", "ultrasonic_bath"],
        "disambiguation_rules": [
            "probe/processor/sonde/ultrasonic processor → ultrasonic_processor",
            "bath/washer/lavadora ultrasónica → ultrasonic_bath",
        ],
        "negative_context_rules": [
            "lavadora ultrasónica must not resolve to ultrasonic_processor",
        ],
    },
    {
        "source_vocabulary": "equipment_first",
        "source_alias": "homogenizer_regex_hit",
        "resolution_kind": "context_required",
        "candidate_classes": [
            "homogenizer",
            "shaker",
            "vortex_mixer",
            "magnetic_stirrer",
        ],
        "disambiguation_rules": [
            "exact homogenizer / homogeneizador spans → homogenizer",
            "agitador / orbital spans → shaker",
            "vortex spans → vortex_mixer",
            "agitador magnético / magnetic stirrer → magnetic_stirrer",
        ],
        "negative_context_rules": [
            "insufficient context remains ambiguous (do not default to homogenizer)",
            "shaker/vortex/magnetic_stirrer must not silently become homogenizer",
        ],
    },
    {
        "source_vocabulary": "source_aliases",
        "source_alias": "agitador",
        "resolution_kind": "context_required",
        "candidate_classes": ["shaker", "magnetic_stirrer", "homogenizer"],
        "disambiguation_rules": [
            "agitador orbital → shaker",
            "agitador magnético → magnetic_stirrer",
            "homogeneizador / homogenizer nearby → homogenizer",
        ],
        "negative_context_rules": [
            "bare agitador without context remains ambiguous",
        ],
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

# Back-compat document shape used by audit JSON (exact + context rows).
TAXONOMY_MAPPING: Final[list[dict[str, Any]]] = [
    {
        **row,
        "business_mart_leads": row["source_aliases"]
        if row.get("source_vocabulary") == "business_mart_leads"
        else [],
        "equipment_first": row["source_aliases"]
        if row.get("source_vocabulary") == "equipment_first"
        else [],
        "web_families_or_filters": row["source_aliases"]
        if row.get("source_vocabulary") == "web_families_or_filters"
        else [],
        "ambiguous": False,
    }
    for row in EXACT_CLASS_MAPPINGS
] + [
    {
        "canonical": None,
        "source_vocabulary": row["source_vocabulary"],
        "source_alias": row["source_alias"],
        "resolution_kind": row["resolution_kind"],
        "candidate_classes": list(row["candidate_classes"]),
        "disambiguation_rules": list(row["disambiguation_rules"]),
        "negative_context_rules": list(row["negative_context_rules"]),
        "ambiguous": True,
        "business_mart_leads": [],
        "equipment_first": [row["source_alias"]]
        if row["source_vocabulary"] == "equipment_first"
        else [],
        "web_families_or_filters": [],
        "source_aliases": [row["source_alias"]],
        "notes": "context_required alias — not a single canonical mapping",
    }
    for row in CONTEXT_REQUIRED_ALIASES
]


def taxonomy_mapping_document() -> dict[str, Any]:
    return {
        "canonical_equipment_classes": list(CANONICAL_EQUIPMENT_CLASSES),
        "future_or_unimplemented": sorted(FUTURE_OR_UNIMPLEMENTED),
        "exact_class_mappings": EXACT_CLASS_MAPPINGS,
        "context_required_aliases": CONTEXT_REQUIRED_ALIASES,
        "mappings": TAXONOMY_MAPPING,
        "recommendation": RECOMMENDATION,
    }


def validate_taxonomy_mapping_completeness(
    exact_mappings: list[dict[str, Any]] | None = None,
    context_aliases: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return completeness diagnostics used by unit tests."""
    exact = exact_mappings if exact_mappings is not None else EXACT_CLASS_MAPPINGS
    context = context_aliases if context_aliases is not None else CONTEXT_REQUIRED_ALIASES
    canonical = set(CANONICAL_EQUIPMENT_CLASSES)
    errors: list[str] = []
    alias_to_canonical: dict[str, set[str]] = {}
    mapped_canonicals: set[str] = set()

    for row in exact:
        c = str(row.get("canonical") or "")
        if c not in canonical:
            errors.append(f"unknown_canonical:{c}")
            continue
        mapped_canonicals.add(c)
        kind = str(row.get("resolution_kind") or "exact")
        if kind != "exact":
            errors.append(f"exact_row_bad_kind:{c}:{kind}")
        for alias in row.get("source_aliases") or []:
            alias_l = str(alias).strip().lower()
            if not alias_l:
                continue
            alias_to_canonical.setdefault(alias_l, set()).add(c)

    for alias, targets in sorted(alias_to_canonical.items()):
        if len(targets) > 1:
            errors.append(f"exact_alias_multi_canonical:{alias}->{sorted(targets)}")

    for row in context:
        alias = str(row.get("source_alias") or "").strip().lower()
        kind = str(row.get("resolution_kind") or "")
        if kind != "context_required":
            errors.append(f"context_row_bad_kind:{alias}:{kind}")
        candidates = [str(c) for c in (row.get("candidate_classes") or [])]
        if len(candidates) < 2 and not row.get("disambiguation_rules"):
            errors.append(f"context_alias_insufficient_contract:{alias}")
        if len(candidates) < 2:
            errors.append(f"context_alias_needs_two_candidates:{alias}")
        for c in candidates:
            if c not in canonical:
                errors.append(f"context_unknown_candidate:{alias}->{c}")
            else:
                mapped_canonicals.add(c)
        if not row.get("disambiguation_rules"):
            errors.append(f"context_alias_missing_disambiguation:{alias}")

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
        "exact_alias_count": len(alias_to_canonical),
        "context_required_alias_count": len(context),
    }
