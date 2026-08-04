"""Taxonomy extensions and capability seeds for PR5D (builds on PR5A taxonomy)."""

from __future__ import annotations

from typing import Any, Final

from origenlab_email_pipeline.commercial_procurement_live_relevance.taxonomy import (
    CANONICAL_EQUIPMENT_CLASSES,
    CONTEXT_REQUIRED_ALIASES,
    EXACT_CLASS_MAPPINGS,
    FUTURE_OR_UNIMPLEMENTED,
    taxonomy_mapping_document,
    validate_taxonomy_mapping_completeness,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.constants import (
    PRODUCT_RELEVANCE_TAXONOMY_VERSION,
)

# Additive canonical classes for known commercial gaps (tablet / dissolution / sedimentation).
PR5D_CANONICAL_EQUIPMENT_CLASSES: Final = CANONICAL_EQUIPMENT_CLASSES + (
    "tablet_hardness_tester",
    "dissolution_apparatus",
    "sedimentation_settlometer",
)

PR5D_EXACT_CLASS_MAPPINGS: Final[list[dict[str, Any]]] = list(EXACT_CLASS_MAPPINGS) + [
    {
        "canonical": "tablet_hardness_tester",
        "source_vocabulary": "commercial_capability_seed",
        "source_aliases": [
            "dureza_comprimidos",
            "tablet_hardness",
            "ensayo_tabletas",
        ],
        "resolution_kind": "exact",
        "notes": "Class-level mapping; model aliases stay proposed until verified.",
    },
    {
        "canonical": "dissolution_apparatus",
        "source_vocabulary": "commercial_capability_seed",
        "source_aliases": ["disolucion_comprimidos", "dissolution_apparatus"],
        "resolution_kind": "exact",
    },
    {
        "canonical": "sedimentation_settlometer",
        "source_vocabulary": "commercial_capability_seed",
        "source_aliases": ["settlometer", "kit_sedimentacion", "nalgene_settlometer"],
        "resolution_kind": "exact",
        "notes": "Nalgene settlometer is a capability seed, not auto-gold.",
    },
]

# Capability seeds — NOT verified exact_catalog_product aliases until sanitized evidence exists.
PROPOSED_CATALOG_ALIASES: Final[list[dict[str, Any]]] = [
    {
        "alias_group": "hielscher_ultrasonic",
        "canonical_class": "ultrasonic_processor",
        "aliases": [
            "UP200St",
            "UP100H",
            "UP50H",
            "VialTweeter",
            "CupHorn",
            "UIP400MTP",
        ],
        "verification_status": "proposed_seed_not_verified",
        "notes": (
            "Commercial capability seeds from recent supplier evidence. "
            "Not accepted as exact_catalog_product until verified against "
            "catalog/supplier/sanitized commercial evidence in-repo."
        ),
    },
    {
        "alias_group": "pharma_test_tablet",
        "canonical_class": "tablet_hardness_tester",
        "aliases": ["PTB 311E", "WHT 4", "PTB 500"],
        "verification_status": "proposed_seed_not_verified",
        "notes": "Tablet-testing/dissolution commercial seeds; class-level rules may still fire.",
    },
    {
        "alias_group": "nalgene_settlometer",
        "canonical_class": "sedimentation_settlometer",
        "aliases": ["Nalgene settlometer", "Nalgene sedimentation"],
        "verification_status": "proposed_seed_not_verified",
    },
]

TAXONOMY_GAPS_AUDITED: Final[list[dict[str, Any]]] = [
    {
        "gap": "tablet_hardness_testing",
        "resolution": "added canonical tablet_hardness_tester",
        "status": "addressed_class_level",
    },
    {
        "gap": "dissolution_equipment",
        "resolution": "added canonical dissolution_apparatus",
        "status": "addressed_class_level",
    },
    {
        "gap": "sedimentation_settlometer",
        "resolution": "added canonical sedimentation_settlometer",
        "status": "addressed_class_level",
    },
    {
        "gap": "exact_catalog_for_capability_seeds",
        "resolution": "kept as proposed_seed_not_verified",
        "status": "open_pending_sanitized_evidence",
    },
]


def pr5d_taxonomy_document() -> dict[str, Any]:
    base = taxonomy_mapping_document()
    return {
        "taxonomy_version": PRODUCT_RELEVANCE_TAXONOMY_VERSION,
        "base_pr5a": base,
        "canonical_equipment_classes": list(PR5D_CANONICAL_EQUIPMENT_CLASSES),
        "exact_class_mappings": PR5D_EXACT_CLASS_MAPPINGS,
        "context_required_aliases": CONTEXT_REQUIRED_ALIASES,
        "future_or_unimplemented": sorted(FUTURE_OR_UNIMPLEMENTED),
        "proposed_catalog_aliases": PROPOSED_CATALOG_ALIASES,
        "taxonomy_gaps_audited": TAXONOMY_GAPS_AUDITED,
        "gmail_supplier_note": (
            "Gmail supplier orders/dispatches/quotations may inform capability "
            "taxonomy but are not PR5D procurement candidates."
        ),
    }


def validate_pr5d_taxonomy() -> dict[str, Any]:
    """Validate PR5A completeness plus PR5D additive classes."""
    base = validate_taxonomy_mapping_completeness()
    errors = list(base["errors"])
    canonical = set(PR5D_CANONICAL_EQUIPMENT_CLASSES)
    mapped = set(base["mapped_canonicals"])
    for row in PR5D_EXACT_CLASS_MAPPINGS:
        c = str(row.get("canonical") or "")
        if c not in canonical:
            errors.append(f"pr5d_unknown_canonical:{c}")
        else:
            mapped.add(c)
    for seed in PROPOSED_CATALOG_ALIASES:
        if seed.get("verification_status") == "verified_against_sanitized_evidence":
            continue
        # Proposed seeds must not be treated as verified exact catalog.
        if seed.get("verification_status") != "proposed_seed_not_verified":
            errors.append(
                f"seed_bad_status:{seed.get('alias_group')}:{seed.get('verification_status')}"
            )
    unmapped = sorted(
        c
        for c in canonical
        if c not in mapped and c not in FUTURE_OR_UNIMPLEMENTED
    )
    # PR5A unmapped errors already cover base; only add PR5D-specific unmapped.
    for c in unmapped:
        if c in (
            "tablet_hardness_tester",
            "dissolution_apparatus",
            "sedimentation_settlometer",
        ):
            errors.append(f"pr5d_unmapped_canonical:{c}")
    # Filter base unmapped for classes we intentionally extended mapping for — keep base errors.
    return {
        "ok": not errors,
        "errors": errors,
        "mapped_canonicals": sorted(mapped),
        "proposed_seed_count": len(PROPOSED_CATALOG_ALIASES),
        "verified_catalog_alias_count": sum(
            1
            for s in PROPOSED_CATALOG_ALIASES
            if s.get("verification_status") == "verified_against_sanitized_evidence"
        ),
    }


def taxonomy_fingerprint_payload() -> dict[str, Any]:
    return {
        "taxonomy_version": PRODUCT_RELEVANCE_TAXONOMY_VERSION,
        "canonical_equipment_classes": list(PR5D_CANONICAL_EQUIPMENT_CLASSES),
        "exact_alias_count": sum(
            len(r.get("source_aliases") or []) for r in PR5D_EXACT_CLASS_MAPPINGS
        ),
        "context_required_alias_count": len(CONTEXT_REQUIRED_ALIASES),
        "proposed_catalog_alias_groups": sorted(
            str(s["alias_group"]) for s in PROPOSED_CATALOG_ALIASES
        ),
        "verified_catalog_alias_count": sum(
            1
            for s in PROPOSED_CATALOG_ALIASES
            if s.get("verification_status") == "verified_against_sanitized_evidence"
        ),
    }
