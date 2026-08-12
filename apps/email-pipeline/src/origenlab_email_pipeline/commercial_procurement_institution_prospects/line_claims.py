"""Line-level commercial claims built from full product text.

A single procurement line often mixes intents ("mantención de centrífugas y
adquisición de un procesador ultrasónico"). Collapsing that to one equipment
class loses the part that is actually sellable, so each clause becomes its own
claim and the tender axes are aggregated from all claims.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from typing import Any, Iterable

from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
    ProductTextUnit,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.normalize import (
    normalize_product_text,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.rules import (
    BALANCE_EQ_RE,
    BARE_SONICATOR_RE,
    BUYER_ACQUISITION_EXPLICIT_RE,
    CENTRIFUGE_EQ_RE,
    DISSOLUTION_RE,
    HOMOGENIZER_RE,
    INCUBATOR_EQ_RE,
    MAGNETIC_STIRRER_RE,
    MICROSCOPE_EQ_RE,
    ORBITAL_SHAKER_RE,
    SEDIMENTATION_RE,
    SERVICE_ONLY_RE,
    TABLET_TEST_RE,
    ULTRASONIC_BATH_RE,
    ULTRASONIC_PROCESSOR_RE,
    VORTEX_RE,
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
# Semicolons are unambiguous clause boundaries.
#
# Coordinating conjunctions and "/" are conditional boundaries. A bare "y"
# is not enough to prove that a new commercial intent begins:
#
#   "mantención preventiva y correctiva de centrífuga"
#
# must remain one service claim, while:
#
#   "mantención de centrífuga y adquisición de sonicador"
#
# must become two independently classified claims.
#
# "/" is also conditional because it commonly appears inside non-clause text
# such as "y/o", "50/60 Hz", ratios, model names, etc.
_HARD_CLAUSE_SPLIT_RE = re.compile(r";")
_CONDITIONAL_CLAUSE_SPLIT_RE = re.compile(
    r"\s+(?:y/o|y)\s+|/",
    re.IGNORECASE,
)

# Segmentation needs a slightly broader acquisition-start vocabulary than the
# final PR5D purchase verdict. Downstream classification still decides whether
# the resulting clause is actually a buyer-equipment purchase.
_ACQUISITION_CLAUSE_START_RE = re.compile(
    r"^(?:adquisicion|compra|comprar|suministro|provision|reposicion)\b"
)

_MIN_CLAUSE_LENGTH = 6
_CLAUSE_TRIM_CHARS = " -–—,."

# Equipment category recognition reuses the PR5D taxonomy patterns so a claim can
# name the equipment a clause is about even when the clause is a maintenance or
# rental request that carries no purchase relevance class of its own.
CATEGORY_PATTERNS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    ("ultrasonic_processor", ULTRASONIC_PROCESSOR_RE),
    ("ultrasonic_bath", ULTRASONIC_BATH_RE),
    ("ultrasonic_processor", BARE_SONICATOR_RE),
    ("centrifuge", CENTRIFUGE_EQ_RE),
    ("balance", BALANCE_EQ_RE),
    ("microscope", MICROSCOPE_EQ_RE),
    ("incubator", INCUBATOR_EQ_RE),
    ("homogenizer", HOMOGENIZER_RE),
    ("shaker", ORBITAL_SHAKER_RE),
    ("vortex_mixer", VORTEX_RE),
    ("magnetic_stirrer", MAGNETIC_STIRRER_RE),
    ("tablet_hardness_tester", TABLET_TEST_RE),
    ("dissolution_apparatus", DISSOLUTION_RE),
    ("sedimentation_settlometer", SEDIMENTATION_RE),
)

# Scopes whose equipment identity may be recovered from clause wording when the
# relevance classifier emitted no class. Never applied to unrelated wording.
_CATEGORY_RECOVERY_SCOPES = frozenset(
    {
        SCOPE_ACCESSORY_OR_PART,
        SCOPE_CONSUMABLE_OR_REAGENT,
        SCOPE_MAINTENANCE_OR_CALIBRATION,
        SCOPE_RENTAL_OR_COMODATO,
        SCOPE_INSTALLED_BASE_EVIDENCE,
    }
)

SCOPE_TO_COMMERCIAL_SIGNAL: dict[str, str] = {
    SCOPE_COMPLETE_EQUIPMENT: "equipment_purchase_signal",
    SCOPE_MAINTENANCE_OR_CALIBRATION: "installed_base_signal",
    SCOPE_INSTALLED_BASE_EVIDENCE: "installed_base_signal",
    SCOPE_RENTAL_OR_COMODATO: "rental_or_comodato_signal",
    SCOPE_CONSUMABLE_OR_REAGENT: "consumable_or_reagent_signal",
    SCOPE_ACCESSORY_OR_PART: "consumable_or_reagent_signal",
    SCOPE_AMBIGUOUS: "review_required_signal",
    SCOPE_NON_LAB_FALSE_POSITIVE: "excluded_unrelated",
    SCOPE_UNRELATED: "excluded_unrelated",
}


def commercial_signal_for_scope(scope: str) -> str:
    """Commercial signal implied by one clause scope (claim-driven, not title)."""
    return SCOPE_TO_COMMERCIAL_SIGNAL.get(scope, "review_required_signal")


def _digest(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:32]


def _starts_new_commercial_intent(text: str) -> bool:
    """Return whether a conditional separator starts a new commercial intent."""
    normalized = normalize_product_text(text).lstrip()
    if not normalized:
        return False

    return bool(
        BUYER_ACQUISITION_EXPLICIT_RE.match(normalized)
        or SERVICE_ONLY_RE.match(normalized)
        or _ACQUISITION_CLAUSE_START_RE.match(normalized)
    )


def split_clause_spans(text: str | None) -> list[tuple[str, int, int]]:
    """Split mixed-intent line text into clauses with their source offsets.

    Semicolons always split. ``y``, ``y/o`` and ``/`` split only when the
    right-hand side starts another independently classifiable commercial
    intent.
    """
    raw = text or ""
    if not raw.strip():
        return []

    boundaries: list[tuple[int, int]] = [
        (match.start(), match.end())
        for match in _HARD_CLAUSE_SPLIT_RE.finditer(raw)
    ]

    for match in _CONDITIONAL_CLAUSE_SPLIT_RE.finditer(raw):
        rhs = raw[match.end() :]
        if _starts_new_commercial_intent(rhs):
            boundaries.append((match.start(), match.end()))

    boundaries.sort()

    spans: list[tuple[str, int]] = []
    cursor = 0

    for start, end in boundaries:
        if start < cursor:
            continue
        spans.append((raw[cursor:start], cursor))
        cursor = end

    spans.append((raw[cursor:], cursor))

    trimmed: list[tuple[str, int, int]] = []
    for piece, offset in spans:
        stripped = piece.strip(_CLAUSE_TRIM_CHARS)
        if not stripped:
            continue
        start = offset + piece.index(stripped)
        trimmed.append((stripped, start, start + len(stripped)))

    kept = [span for span in trimmed if len(span[0]) >= _MIN_CLAUSE_LENGTH]
    if kept:
        return kept

    whole = raw.strip()
    start = raw.index(whole) if whole else 0
    return [(whole, start, start + len(whole))] if whole else []


def split_clauses(text: str | None) -> list[str]:
    """Split mixed-intent line text into independently classifiable clauses."""
    return [clause for clause, _start, _end in split_clause_spans(text)]


def recognized_equipment_categories(clause_normalized: str) -> tuple[str, ...]:
    """Equipment categories named by a clause, in deterministic order."""
    found: list[str] = []
    for category, pattern in CATEGORY_PATTERNS:
        if category not in found and pattern.search(clause_normalized):
            found.append(category)
    return tuple(found)


@dataclass(frozen=True)
class LineClaim:
    """One commercial claim extracted from a clause of a product-text unit.

    A claim carries its own evidence provenance so a downstream row can be traced
    back to the exact clause of the exact observed line, without re-deriving
    anything from a tender-level relevance verdict.
    """

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
    # Source text and offsets: the claim quotes the line, never a matched span.
    source_text: str = ""
    clause_start: int = 0
    clause_end: int = 0
    field_path: str | None = None
    evidence_tier: str | None = None
    source_plane: str | None = None
    snapshot_id: str | None = None
    observation_id: str | None = None
    line_observation_id: str | None = None
    evidence_ref_id: str | None = None
    contributing_evidence_ref_ids: tuple[str, ...] = ()
    # Institution identity is resolved by the planner, not by the line layer.
    institution_id: str | None = None
    canonical_equipment_category: str | None = None
    commercial_signal_type: str = "review_required_signal"
    catalog_match_status: str | None = None
    positive_reason_codes: tuple[str, ...] = ()
    negative_reason_codes: tuple[str, ...] = ()
    ambiguity_reason_codes: tuple[str, ...] = ()
    confidence_band: str | None = None
    specification_status: str | None = None
    link_status: str = "linked"

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "unit_id": self.unit_id,
            "coalesced_tender_id": self.coalesced_tender_id,
            "institution_id": self.institution_id,
            "clause_index": self.clause_index,
            "clause_text": self.clause_text,
            "source_text": self.source_text,
            "clause_start": self.clause_start,
            "clause_end": self.clause_end,
            "field_path": self.field_path,
            "evidence_tier": self.evidence_tier,
            "source_plane": self.source_plane,
            "snapshot_id": self.snapshot_id,
            "observation_id": self.observation_id,
            "line_observation_id": self.line_observation_id,
            "evidence_ref_id": self.evidence_ref_id,
            "contributing_evidence_ref_ids": list(self.contributing_evidence_ref_ids),
            "equipment_scope": self.equipment_scope,
            "relevance_class": self.relevance_class,
            "canonical_equipment_category": self.canonical_equipment_category,
            "canonical_equipment_classes": list(self.canonical_equipment_classes),
            "commercial_signal_type": self.commercial_signal_type,
            "catalog_match_status": self.catalog_match_status,
            "purchase_intent": self.purchase_intent,
            "reason_codes": list(self.reason_codes),
            "positive_reason_codes": list(self.positive_reason_codes),
            "negative_reason_codes": list(self.negative_reason_codes),
            "ambiguity_reason_codes": list(self.ambiguity_reason_codes),
            "confidence_band": self.confidence_band,
            "specification_status": self.specification_status,
            "link_status": self.link_status,
        }

    def with_context(
        self,
        *,
        institution_id: str | None = None,
        catalog_match_status: str | None = None,
    ) -> "LineClaim":
        """Return a copy carrying planner-resolved context."""
        return replace(
            self,
            institution_id=institution_id
            if institution_id is not None
            else self.institution_id,
            catalog_match_status=catalog_match_status
            if catalog_match_status is not None
            else self.catalog_match_status,
        )


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
    if ACCESSORY_RE.search(clause_normalized) and scope in {
        SCOPE_COMPLETE_EQUIPMENT,
        SCOPE_CONSUMABLE_OR_REAGENT,
    }:
        # Spare parts for an instrument name the instrument but do not buy one.
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
    for index, (clause, start, end) in enumerate(split_clause_spans(text)):
        decision = classify_product_text_unit(_clause_unit(clause, unit=unit, index=index))
        clause_normalized = normalize_product_text(clause)
        classes = tuple(decision.canonical_equipment_classes)
        scope, reasons = _scope_for(decision.relevance_class, clause_normalized, classes)
        if not classes and scope in _CATEGORY_RECOVERY_SCOPES:
            recovered = recognized_equipment_categories(clause_normalized)
            if recovered:
                classes = recovered
                reasons = reasons + ["equipment_category_recovered_from_clause"]
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
                source_text=text,
                clause_start=start,
                clause_end=end,
                field_path=unit.field_path,
                evidence_tier=unit.evidence_tier,
                source_plane=unit.source_plane,
                snapshot_id=unit.snapshot_id,
                observation_id=unit.observation_id,
                line_observation_id=unit.line_observation_id,
                evidence_ref_id=unit.evidence_ref_id,
                contributing_evidence_ref_ids=tuple(
                    unit.contributing_evidence_ref_ids
                ),
                canonical_equipment_category=classes[0] if classes else None,
                commercial_signal_type=commercial_signal_for_scope(scope),
                positive_reason_codes=tuple(decision.positive_reason_codes),
                negative_reason_codes=tuple(decision.negative_reason_codes),
                ambiguity_reason_codes=tuple(decision.ambiguity_reason_codes),
                confidence_band=decision.confidence_band,
                specification_status=decision.product_resolution_status,
                link_status=unit.link_status,
            )
        )
    return tuple(claims)


def build_claims_for_units(
    units: Iterable[ProductTextUnit],
) -> dict[str, tuple[LineClaim, ...]]:
    """Map unit_id → claims, in deterministic unit order.

    An unresolved unit is evidence that failed to link to a tender; it must not
    produce a commercial claim that could later be counted as demand.
    """
    out: dict[str, tuple[LineClaim, ...]] = {}
    for unit in sorted(units, key=lambda u: u.unit_id):
        if unit.link_status == "unresolved" or not unit.coalesced_tender_id:
            out[unit.unit_id] = ()
            continue
        out[unit.unit_id] = build_line_claims(unit)
    return out


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


def aggregate_category_scoped_claims(
    claims: Iterable[LineClaim],
) -> list[dict[str, Any]]:
    """Group claims by (tender, equipment category) keeping each scope with its own
    category.

    Cross-producting every observed scope against every observed category is what
    lets a maintenance clause manufacture a purchase signal for an unrelated
    instrument, so grouping happens per claim, never per tender.
    """
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for claim in sorted(claims, key=lambda c: (c.unit_id, c.clause_index)):
        for category in claim.canonical_equipment_classes:
            if not category or str(category).startswith("relevance:"):
                continue
            key = (
                claim.coalesced_tender_id,
                str(category),
                claim.commercial_signal_type,
            )
            entry = grouped.setdefault(
                key,
                {
                    "institution_id": claim.institution_id,
                    "coalesced_tender_id": claim.coalesced_tender_id,
                    "equipment_category": str(category),
                    "commercial_signal": claim.commercial_signal_type,
                    "scopes": [],
                    "claim_ids": [],
                    "purchase_intent": False,
                },
            )
            if entry["institution_id"] is None and claim.institution_id:
                entry["institution_id"] = claim.institution_id
            if claim.equipment_scope not in entry["scopes"]:
                entry["scopes"].append(claim.equipment_scope)
            if claim.claim_id not in entry["claim_ids"]:
                entry["claim_ids"].append(claim.claim_id)
            entry["purchase_intent"] = entry["purchase_intent"] or claim.purchase_intent
    out = []
    for key in sorted(grouped):
        entry = grouped[key]
        entry["scopes"] = sorted(entry["scopes"])
        entry["claim_ids"] = sorted(entry["claim_ids"])
        out.append(entry)
    return out


__all__ = [
    "ASSIGNED_NON_PURCHASE_SCOPES",
    "CATEGORY_PATTERNS",
    "EQUIPMENT_SCOPES",
    "SCOPE_TO_COMMERCIAL_SIGNAL",
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
    "aggregate_category_scoped_claims",
    "aggregate_tender_axes",
    "build_claims_for_units",
    "build_line_claims",
    "commercial_signal_for_scope",
    "recognized_equipment_categories",
    "split_clause_spans",
    "split_clauses",
]
