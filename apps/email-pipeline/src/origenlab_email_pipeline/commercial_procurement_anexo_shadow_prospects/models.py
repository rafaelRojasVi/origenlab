"""Immutable ANEXO-P1 shadow prospect comparison models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ClaimCapability:
    """Catalog capability for one recognized equipment class."""

    equipment_class: str
    capability: str
    catalog_match_status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TenderShadowDelta:
    """Per-tender CURRENT vs SHADOW prospect measurement row."""

    tender_id: str
    buyer_organization: str
    lifecycle: str
    current_pr5d_decision: dict[str, Any]
    shadow_annex_pr5d_decision: dict[str, Any]
    current_prospect_disposition: str | None
    shadow_prospect_disposition: str | None
    current_equipment_classes: tuple[str, ...]
    shadow_equipment_classes: tuple[str, ...]
    new_annex_classes: tuple[str, ...]
    claim_level_capability: tuple[ClaimCapability, ...]
    commercial_intent_class: str
    coverage_status: str
    coverage_debt: dict[str, int]
    current_queue_memberships: tuple[str, ...]
    shadow_queue_memberships: tuple[str, ...]
    queue_delta: tuple[str, ...]
    current_prospect_strength: str | None
    shadow_prospect_strength: str | None
    strength_delta: str | None
    current_urgency: str | None
    shadow_urgency: str | None
    contact_resolution_status: str | None
    contact_readiness: str | None
    annex_claim_ids: tuple[str, ...]
    annex_provenance_refs: tuple[dict[str, Any], ...]
    change_class: str
    human_review_required: bool
    e2_change_class: str | None = None
    e2_interpretation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tender_id": self.tender_id,
            "buyer_organization": self.buyer_organization,
            "lifecycle": self.lifecycle,
            "current_pr5d_decision": dict(self.current_pr5d_decision),
            "shadow_annex_pr5d_decision": dict(self.shadow_annex_pr5d_decision),
            "current_prospect_disposition": self.current_prospect_disposition,
            "shadow_prospect_disposition": self.shadow_prospect_disposition,
            "current_equipment_classes": list(self.current_equipment_classes),
            "shadow_equipment_classes": list(self.shadow_equipment_classes),
            "new_annex_classes": list(self.new_annex_classes),
            "claim_level_capability": [c.to_dict() for c in self.claim_level_capability],
            "commercial_intent_class": self.commercial_intent_class,
            "coverage_status": self.coverage_status,
            "coverage_debt": dict(self.coverage_debt),
            "current_queue_memberships": list(self.current_queue_memberships),
            "shadow_queue_memberships": list(self.shadow_queue_memberships),
            "queue_delta": list(self.queue_delta),
            "current_prospect_strength": self.current_prospect_strength,
            "shadow_prospect_strength": self.shadow_prospect_strength,
            "strength_delta": self.strength_delta,
            "current_urgency": self.current_urgency,
            "shadow_urgency": self.shadow_urgency,
            "contact_resolution_status": self.contact_resolution_status,
            "contact_readiness": self.contact_readiness,
            "annex_claim_ids": list(self.annex_claim_ids),
            "annex_provenance_refs": [dict(r) for r in self.annex_provenance_refs],
            "change_class": self.change_class,
            "human_review_required": self.human_review_required,
            "e2_change_class": self.e2_change_class,
            "e2_interpretation": self.e2_interpretation,
        }


@dataclass(frozen=True)
class InstitutionShadowDelta:
    institution_id: str
    display_name: str
    current_prospect_strength: str | None
    shadow_prospect_strength: str | None
    strength_delta: str | None
    current_urgency: str | None
    shadow_urgency: str | None
    current_equipment_history_count: int
    shadow_equipment_history_count: int
    history_delta: int
    current_operator_next_action: str | None
    shadow_operator_next_action: str | None
    contact_readiness: str | None
    tender_ids: tuple[str, ...]
    changed: bool

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["tender_ids"] = list(self.tender_ids)
        return d


@dataclass(frozen=True)
class QueueShadowDelta:
    queue_name: str
    row_key: str
    delta_class: str
    current_present: bool
    shadow_present: bool
    current_priority: str | None
    shadow_priority: str | None
    tender_id: str | None
    institution_id: str | None
    equipment_category: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ShadowProspectComparisonResult:
    """In-memory CURRENT vs SHADOW comparison; never a production artifact."""

    comparison_version: str
    corpus_digest: str
    semantic_digest: str
    catalog_capability_digest: str
    as_of_utc: str
    tender_deltas: tuple[TenderShadowDelta, ...]
    institution_deltas: tuple[InstitutionShadowDelta, ...]
    queue_deltas: tuple[QueueShadowDelta, ...]
    current_fingerprint: str
    shadow_fingerprint: str
    e2_semantic_digest: str
    lane: str = "anexo_p1_shadow_only"
    contact_authorization: bool = False
    outreach_authorization: bool = False
    production_queue_mutated: bool = False
    persisted: bool = False
    pr5f_started: bool = False
    annex_production_integration: bool = False
    notes: dict[str, Any] = field(default_factory=dict)
