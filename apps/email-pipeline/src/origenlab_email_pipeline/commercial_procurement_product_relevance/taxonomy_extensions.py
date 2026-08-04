"""Taxonomy extensions for PR5D — single authoritative source is PR5A taxonomy.py.

Capability seeds (proposed catalog aliases) live here only. Canonical classes and
exact class mappings are NOT duplicated.
"""

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
from origenlab_email_pipeline.commercial_procurement_product_relevance.normalize import (
    normalize_product_text,
)

# Authoritative: PR5A objects used directly — no mutable PR5D copies.
PR5D_CANONICAL_EQUIPMENT_CLASSES: Final = CANONICAL_EQUIPMENT_CLASSES
# Same object as PR5A — not list(EXACT_CLASS_MAPPINGS).
PR5D_EXACT_CLASS_MAPPINGS: Final = EXACT_CLASS_MAPPINGS

VALID_PROPOSED_VERIFICATION_STATUSES: Final[frozenset[str]] = frozenset(
    {
        "proposed_seed_not_verified",
        "verified_against_sanitized_evidence",
    }
)

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
        "resolution": "canonical tablet_hardness_tester in PR5A taxonomy.py",
        "status": "addressed_class_level",
    },
    {
        "gap": "dissolution_equipment",
        "resolution": "canonical dissolution_apparatus in PR5A taxonomy.py",
        "status": "addressed_class_level",
    },
    {
        "gap": "sedimentation_settlometer",
        "resolution": "canonical sedimentation_settlometer in PR5A taxonomy.py",
        "status": "addressed_class_level",
    },
    {
        "gap": "exact_catalog_for_capability_seeds",
        "resolution": "kept as proposed_seed_not_verified",
        "status": "open_pending_sanitized_evidence",
    },
]


def _alias_rows_for_uniqueness(
    mappings: list[dict[str, Any]],
) -> list[tuple[str, str, str]]:
    """Return (normalized_alias, canonical, source_vocabulary) preserving duplicates."""
    rows: list[tuple[str, str, str]] = []
    for row in mappings:
        canonical = str(row.get("canonical") or "")
        vocab = str(row.get("source_vocabulary") or "")
        for alias in row.get("source_aliases") or []:
            rows.append((normalize_product_text(str(alias)), canonical, vocab))
    return rows


def validate_taxonomy_uniqueness(
    *,
    canonical_classes: tuple[str, ...] | list[str] | None = None,
    exact_mappings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Fail on duplicate canonicals / duplicate normalized aliases (list scan, not set hide)."""
    classes = list(
        canonical_classes if canonical_classes is not None else PR5D_CANONICAL_EQUIPMENT_CLASSES
    )
    mappings = (
        exact_mappings if exact_mappings is not None else list(PR5D_EXACT_CLASS_MAPPINGS)
    )
    errors: list[str] = []

    seen_classes: list[str] = []
    for c in classes:
        if c in seen_classes:
            errors.append(f"duplicate_canonical_class:{c}")
        seen_classes.append(c)

    # Duplicate exact mapping rows for the same (canonical, vocab, sorted aliases).
    mapping_keys: list[str] = []
    for row in mappings:
        key = (
            f"{row.get('canonical')}|{row.get('source_vocabulary')}|"
            f"{sorted(str(a) for a in (row.get('source_aliases') or []))}"
        )
        if key in mapping_keys:
            errors.append(f"duplicate_exact_mapping_row:{key}")
        mapping_keys.append(key)

    alias_rows = _alias_rows_for_uniqueness(mappings)
    seen_aliases: list[str] = []
    for norm_alias, canonical, vocab in alias_rows:
        if not norm_alias:
            continue
        # Same normalized alias mapping to more than one canonical is an error.
        prior = [a for a in seen_aliases if a.split("->", 1)[0] == norm_alias]
        for p in prior:
            prior_canonical = p.split("->", 1)[1]
            if prior_canonical != canonical:
                errors.append(
                    f"duplicate_normalized_alias_multi_canonical:"
                    f"{norm_alias}->{sorted({prior_canonical, canonical})}"
                )
            else:
                errors.append(
                    f"duplicate_normalized_alias_same_canonical:{norm_alias}->{canonical}"
                )
        seen_aliases.append(f"{norm_alias}->{canonical}")

    return {
        "ok": not errors,
        "errors": errors,
        "canonical_class_count": len(classes),
        "exact_mapping_row_count": len(mappings),
        "normalized_alias_row_count": len(alias_rows),
    }


def validate_proposed_catalog_aliases(
    seeds: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate proposed capability seeds independently of PR5A exact mappings."""
    rows = list(seeds if seeds is not None else PROPOSED_CATALOG_ALIASES)
    errors: list[str] = []
    canonical_set = set(PR5D_CANONICAL_EQUIPMENT_CLASSES)

    alias_groups: list[str] = []
    # (normalized_alias, canonical, verification_status, alias_group) — list, not set.
    alias_bindings: list[tuple[str, str, str, str]] = []

    for seed in rows:
        group = str(seed.get("alias_group") or "")
        if not group:
            errors.append("missing_alias_group")
            continue
        if group in alias_groups:
            errors.append(f"duplicate_alias_group:{group}")
        alias_groups.append(group)

        target = str(seed.get("canonical_class") or "")
        if target not in canonical_set:
            errors.append(f"invalid_canonical_target:{group}:{target}")

        status = str(seed.get("verification_status") or "")
        if status not in VALID_PROPOSED_VERIFICATION_STATUSES:
            errors.append(f"invalid_verification_status:{group}:{status}")

        for alias in seed.get("aliases") or []:
            norm = normalize_product_text(str(alias))
            if not norm:
                continue
            alias_bindings.append((norm, target, status, group))

    # Conflicting normalized aliases across groups/targets (order-independent).
    by_alias: dict[str, list[tuple[str, str, str]]] = {}
    for norm, target, status, group in alias_bindings:
        by_alias.setdefault(norm, []).append((target, status, group))

    for norm, bindings in sorted(by_alias.items()):
        targets = sorted({t for t, _s, _g in bindings})
        if len(targets) > 1:
            errors.append(
                f"proposed_alias_conflicting_targets:{norm}->{targets}"
            )
        # Verified duplicates must not be list-order dependent: any repeated
        # verified binding of the same alias is an error even to the same target.
        verified = [(t, g) for t, s, g in bindings if s == "verified_against_sanitized_evidence"]
        if len(verified) > 1:
            errors.append(
                f"verified_duplicate_alias_order_dependent:{norm}:"
                f"{sorted({g for _t, g in verified})}"
            )

    return {
        "ok": not errors,
        "errors": errors,
        "alias_group_count": len(alias_groups),
        "normalized_alias_binding_count": len(alias_bindings),
    }


def pr5d_taxonomy_document() -> dict[str, Any]:
    base = taxonomy_mapping_document()
    return {
        "taxonomy_version": PRODUCT_RELEVANCE_TAXONOMY_VERSION,
        "authoritative_source": "commercial_procurement_live_relevance.taxonomy",
        "base_pr5a": base,
        "canonical_equipment_classes": list(PR5D_CANONICAL_EQUIPMENT_CLASSES),
        "exact_class_mappings": list(PR5D_EXACT_CLASS_MAPPINGS),
        "context_required_aliases": CONTEXT_REQUIRED_ALIASES,
        "future_or_unimplemented": sorted(FUTURE_OR_UNIMPLEMENTED),
        "proposed_catalog_aliases": PROPOSED_CATALOG_ALIASES,
        "taxonomy_gaps_audited": TAXONOMY_GAPS_AUDITED,
        "gmail_supplier_note": (
            "Gmail supplier orders/dispatches/quotations may inform capability "
            "taxonomy but are not PR5D procurement candidates."
        ),
        "uniqueness": validate_taxonomy_uniqueness(),
        "proposed_alias_validation": validate_proposed_catalog_aliases(),
    }


def validate_pr5d_taxonomy() -> dict[str, Any]:
    """Validate PR5A completeness + uniqueness; seeds stay proposed."""
    base = validate_taxonomy_mapping_completeness()
    uniq = validate_taxonomy_uniqueness()
    proposed = validate_proposed_catalog_aliases()
    errors = list(base["errors"]) + list(uniq["errors"]) + list(proposed["errors"])

    for seed in PROPOSED_CATALOG_ALIASES:
        status = seed.get("verification_status")
        if status == "verified_against_sanitized_evidence":
            # Allowed only with explicit verification — currently none should be verified.
            continue
        if status != "proposed_seed_not_verified":
            errors.append(
                f"seed_bad_status:{seed.get('alias_group')}:{status}"
            )

    verified = sum(
        1
        for s in PROPOSED_CATALOG_ALIASES
        if s.get("verification_status") == "verified_against_sanitized_evidence"
    )
    return {
        "ok": not errors,
        "errors": errors,
        "mapped_canonicals": sorted(base["mapped_canonicals"]),
        "proposed_seed_count": len(PROPOSED_CATALOG_ALIASES),
        "verified_catalog_alias_count": verified,
        "uniqueness": uniq,
        "proposed_alias_validation": proposed,
    }


def taxonomy_fingerprint_payload() -> dict[str, Any]:
    """Full semantic taxonomy inputs — alias content changes must move the fingerprint."""
    exact = []
    for row in PR5D_EXACT_CLASS_MAPPINGS:
        exact.append(
            {
                "canonical": row.get("canonical"),
                "source_vocabulary": row.get("source_vocabulary"),
                "resolution_kind": row.get("resolution_kind"),
                "source_aliases_normalized": sorted(
                    normalize_product_text(str(a))
                    for a in (row.get("source_aliases") or [])
                    if str(a).strip()
                ),
                "future_or_unimplemented": bool(row.get("future_or_unimplemented")),
                "notes": row.get("notes"),
            }
        )
    context = []
    for row in CONTEXT_REQUIRED_ALIASES:
        context.append(
            {
                "source_vocabulary": row.get("source_vocabulary"),
                "source_alias_normalized": normalize_product_text(
                    str(row.get("source_alias") or "")
                ),
                "resolution_kind": row.get("resolution_kind"),
                "candidate_classes": list(row.get("candidate_classes") or []),
                "disambiguation_rules": list(row.get("disambiguation_rules") or []),
                "negative_context_rules": list(row.get("negative_context_rules") or []),
            }
        )
    seeds = []
    for seed in PROPOSED_CATALOG_ALIASES:
        seeds.append(
            {
                "alias_group": seed.get("alias_group"),
                "canonical_class": seed.get("canonical_class"),
                "verification_status": seed.get("verification_status"),
                "aliases_normalized": sorted(
                    normalize_product_text(str(a))
                    for a in (seed.get("aliases") or [])
                    if str(a).strip()
                ),
            }
        )
    return {
        "taxonomy_version": PRODUCT_RELEVANCE_TAXONOMY_VERSION,
        "canonical_equipment_classes": list(PR5D_CANONICAL_EQUIPMENT_CLASSES),
        "exact_class_mappings": exact,
        "context_required_aliases": context,
        "proposed_catalog_aliases": seeds,
        "future_or_unimplemented": sorted(FUTURE_OR_UNIMPLEMENTED),
    }
