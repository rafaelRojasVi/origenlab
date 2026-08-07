"""Line-level commercial claims built from full product text.

A single procurement line often mixes intents ("mantención de centrífugas y
adquisición de un procesador ultrasónico"). Collapsing that to one equipment
class loses the part that is actually sellable, so each clause becomes its own
claim and the tender axes are aggregated from all claims.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable

from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
    ProductTextUnit,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.normalize import (
    normalize_product_text,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.rules import (
    classify_product_text_unit,
)

SCOPE_COMPLETE_EQUIPMENT = "complete_equipment"
SCOPE_ACCESSORY_OR_PART = "accessory_or_part"
SCOPE_CONSUMABLE_OR_REAGENT = "consumable_or_reagent"
SCOPE_MAINTENANCE_OR_CALIBRATION = "maintenance_or_calibration"
SCOPE_RENTAL_OR_COMODATO = "rental_or_comodato"
SCOPE_INSTALLED_BASE_EVIDENCE = "installed_base_evidence"
SCOPE_NON_LAB_FALSE_POSITIVE = "non_laboratory_false_positive"
SCOPE_AMBIGUOUS = "ambiguous"
SCOPE_UNRELATED = "unrelated"

EQUIPMENT_SCOPES = (
    SCOPE_COMPLETE_EQUIPMENT,
    SCOPE_ACCESSORY_OR_PART,
    SCOPE_CONSUMABLE_OR_REAGENT,
    SCOPE_MAINTENANCE_OR_CALIBRATION,
    SCOPE_RENTAL_OR_COMODATO,
    SCOPE_INSTALLED_BASE_EVIDENCE,
    SCOPE_NON_LAB_FALSE_POSITIVE,
    SCOPE_AMBIGUOUS,
    SCOPE_UNRELATED,
)

# Scopes that can carry a real purchase opportunity for a complete instrument.
PURCHASE_SCOPES = frozenset({SCOPE_COMPLETE_EQUIPMENT})
# Scopes that are commercially meaningful but are not a new-instrument purchase.
ASSIGNED_NON_PURCHASE_SCOPES = frozenset(
    {
        SCOPE_ACCESSORY_OR_PART,
        SCOPE_CONSUMABLE_OR_REAGENT,
        SCOPE_MAINTENANCE_OR_CALIBRATION,
        SCOPE_RENTAL_OR_COMODATO,
        SCOPE_INSTALLED_BASE_EVIDENCE,
    }
)

_RELEVANCE_TO_SCOPE = {
    "exact_catalog_product": SCOPE_COMPLETE_EQUIPMENT,
    "strong_equipment_class": SCOPE_COMPLETE_EQUIPMENT,
    "compatible_equipment_class": SCOPE_COMPLETE_EQUIPMENT,
    "service_or_maintenance_only": SCOPE_MAINTENANCE_OR_CALIBRATION,
    "rental_or_comodato": SCOPE_RENTAL_OR_COMODATO,
    "consumable_or_reagent": SCOPE_CONSUMABLE_OR_REAGENT,
    "non_laboratory_false_positive": SCOPE_NON_LAB_FALSE_POSITIVE,
    "ambiguous": SCOPE_AMBIGUOUS,
    "laboratory_context_only": SCOPE_AMBIGUOUS,
    "unrelated": SCOPE_UNRELATED,
}

ACCESSORY_RE = re.compile(
    r"\baccesorios?\b|\brepuestos?\b|\bmanga\s+protectora\b|\bfunda\b|"
    r"\bkit\s+de\s+repuesto|\bpieza[s]?\s+de\s+recambio|\bcono\s+centrifuga\b"
)
INSTALLED_BASE_RE = re.compile(
    r"\bequipos?\s+existentes?\b|\bparque\s+(de\s+)?equipos?\b|"
    r"\bequipamiento\s+instalado\b|\bequipos?\s+en\s+uso\b|"
    r"\bequipos?\s+de\s+propiedad\s+del\s+(hospital|servicio|establecimiento)\b"
)
CLAUSE_SPLIT_RE = re.compile(r"\s+y\s+|;|/")
_MIN_CLAUSE_LENGTH = 6


def _digest(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:32]


def split_clauses(text: str | None) -> list[str]:
    """Split mixed-intent line text into independently classifiable clauses."""
    raw = (text or "").strip()
    if not raw:
        return []
    parts = [p.strip(" -–—,.") for p in CLAUSE_SPLIT_RE.split(raw)]
    clauses = [p for p in parts if len(p) >= _MIN_CLAUSE_LENGTH]
    return clauses or [raw]


@dataclass(frozen=True)
class LineClaim:
    """One commercial claim extracted from a clause of a product-text unit."""

    claim_id: str
    unit_id: str
    coalesced_tender_id: str
    clause_index: int
    clause_text: str
    equipment_scope: str
    relevance_class: str
    canonical_equipment_classes: tuple[str, ...]
    purchase_intent: bool
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "unit_id": self.unit_id,
            "coalesced_tender_id": self.coalesced_tender_id,
            "clause_index": self.clause_index,
            "clause_text": self.clause_text,
            "equipment_scope": self.equipment_scope,
            "relevance_class": self.relevance_class,
            "canonical_equipment_classes": list(self.canonical_equipment_classes),
            "purchase_intent": self.purchase_intent,
            "reason_codes": list(self.reason_codes),
        }


def _clause_unit(
    clause: str, *, unit: ProductTextUnit, index: int
) -> ProductTextUnit:
    return ProductTextUnit(
        unit_id=f"{unit.unit_id}#claim{index}",
        coalesced_tender_id=unit.coalesced_tender_id,
        evidence_ref_id=unit.evidence_ref_id,
        link_status=unit.link_status,
        unresolved_reason=unit.unresolved_reason,
        field_path=unit.field_path,
        text_raw=clause,
        text_normalized=normalize_product_text(clause),
        evidence_tier=unit.evidence_tier,
        source_plane=unit.source_plane,
        snapshot_id=unit.snapshot_id,
        observation_id=unit.observation_id,
        tender_observation_id=unit.tender_observation_id,
        line_observation_id=unit.line_observation_id,
        pr4_procurement_id=unit.pr4_procurement_id,
        contributing_evidence_ref_ids=unit.contributing_evidence_ref_ids,
    )


def _scope_for(
    relevance_class: str, clause_normalized: str, classes: tuple[str, ...]
) -> tuple[str, list[str]]:
    scope = _RELEVANCE_TO_SCOPE.get(relevance_class, SCOPE_AMBIGUOUS)
    reasons = [f"relevance_class={relevance_class}"]
    if scope == SCOPE_CONSUMABLE_OR_REAGENT and ACCESSORY_RE.search(clause_normalized):
        return SCOPE_ACCESSORY_OR_PART, reasons + ["accessory_or_part_wording"]
    if scope == SCOPE_MAINTENANCE_OR_CALIBRATION and INSTALLED_BASE_RE.search(
        clause_normalized
    ):
        return SCOPE_INSTALLED_BASE_EVIDENCE, reasons + ["installed_base_wording"]
    if scope == SCOPE_COMPLETE_EQUIPMENT and not classes:
        return SCOPE_AMBIGUOUS, reasons + ["equipment_wording_without_class"]
    return scope, reasons


def build_line_claims(unit: ProductTextUnit) -> tuple[LineClaim, ...]:
    """Build one claim per clause from the unit's full text."""
    text = unit.text_raw or unit.text_normalized or ""
    claims: list[LineClaim] = []
    for index, clause in enumerate(split_clauses(text)):
        decision = classify_product_text_unit(_clause_unit(clause, unit=unit, index=index))
        clause_normalized = normalize_product_text(clause)
        classes = tuple(decision.canonical_equipment_classes)
        scope, reasons = _scope_for(decision.relevance_class, clause_normalized, classes)
        claims.append(
            LineClaim(
                claim_id=_digest("line_claim", unit.unit_id, str(index), clause),
                unit_id=unit.unit_id,
                coalesced_tender_id=unit.coalesced_tender_id,
                clause_index=index,
                clause_text=clause,
                equipment_scope=scope,
                relevance_class=decision.relevance_class,
                canonical_equipment_classes=classes,
                purchase_intent=scope in PURCHASE_SCOPES,
                reason_codes=tuple(
                    sorted(set(reasons) | set(decision.positive_reason_codes))
                ),
            )
        )
    return tuple(claims)


def build_claims_for_units(
    units: Iterable[ProductTextUnit],
) -> dict[str, tuple[LineClaim, ...]]:
    """Map unit_id → claims, in deterministic unit order."""
    return {
        unit.unit_id: build_line_claims(unit)
        for unit in sorted(units, key=lambda u: u.unit_id)
    }


def aggregate_tender_axes(claims: Iterable[LineClaim]) -> dict[str, Any]:
    """Aggregate claims into tender-level axes instead of a single class."""
    claim_list = sorted(claims, key=lambda c: (c.unit_id, c.clause_index))
    scopes = sorted({c.equipment_scope for c in claim_list})
    purchase_classes = sorted(
        {
            klass
            for c in claim_list
            if c.equipment_scope == SCOPE_COMPLETE_EQUIPMENT
            for klass in c.canonical_equipment_classes
        }
    )
    all_classes = sorted(
        {klass for c in claim_list for klass in c.canonical_equipment_classes}
    )
    scope_counts = {
        scope: sum(1 for c in claim_list if c.equipment_scope == scope)
        for scope in scopes
    }
    return {
        "claim_count": len(claim_list),
        "equipment_scopes": scopes,
        "equipment_scope_counts": scope_counts,
        "complete_equipment_classes": purchase_classes,
        "canonical_equipment_classes": all_classes,
        "has_complete_equipment_purchase": bool(purchase_classes),
        "has_maintenance_or_calibration": SCOPE_MAINTENANCE_OR_CALIBRATION in scopes,
        "has_rental_or_comodato": SCOPE_RENTAL_OR_COMODATO in scopes,
        "has_consumable_or_reagent": SCOPE_CONSUMABLE_OR_REAGENT in scopes,
        "has_installed_base_evidence": SCOPE_INSTALLED_BASE_EVIDENCE in scopes,
        "mixed_scope": len([s for s in scopes if s != SCOPE_UNRELATED]) > 1,
        "ambiguous_claim_count": scope_counts.get(SCOPE_AMBIGUOUS, 0),
    }


__all__ = [
    "ASSIGNED_NON_PURCHASE_SCOPES",
    "EQUIPMENT_SCOPES",
    "LineClaim",
    "PURCHASE_SCOPES",
    "SCOPE_ACCESSORY_OR_PART",
    "SCOPE_AMBIGUOUS",
    "SCOPE_COMPLETE_EQUIPMENT",
    "SCOPE_CONSUMABLE_OR_REAGENT",
    "SCOPE_INSTALLED_BASE_EVIDENCE",
    "SCOPE_MAINTENANCE_OR_CALIBRATION",
    "SCOPE_NON_LAB_FALSE_POSITIVE",
    "SCOPE_RENTAL_OR_COMODATO",
    "SCOPE_UNRELATED",
    "aggregate_tender_axes",
    "build_claims_for_units",
    "build_line_claims",
    "split_clauses",
]
