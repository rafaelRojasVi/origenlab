"""Deterministic digests for ANEXO-P1 shadow prospect comparisons."""

from __future__ import annotations

from collections.abc import Sequence

from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.planner import (
    InstitutionProspectPlanResult,
)

from .constants import COMPARISON_ALGORITHM, COMPARISON_VERSION
from .models import InstitutionShadowDelta, QueueShadowDelta, TenderShadowDelta


def shadow_semantic_digest(
    tender_deltas: Sequence[TenderShadowDelta],
    institution_deltas: Sequence[InstitutionShadowDelta],
    queue_deltas: Sequence[QueueShadowDelta],
) -> str:
    payload = {
        "algorithm": COMPARISON_ALGORITHM,
        "version": COMPARISON_VERSION,
        "tenders": sorted(
            (
                {
                    "tender_id": d.tender_id,
                    "change_class": d.change_class,
                    "new_annex_classes": list(d.new_annex_classes),
                    "queue_delta": list(d.queue_delta),
                    "commercial_intent_class": d.commercial_intent_class,
                    "capability": [
                        {"class": c.equipment_class, "capability": c.capability}
                        for c in d.claim_level_capability
                    ],
                }
                for d in tender_deltas
            ),
            key=lambda r: r["tender_id"],
        ),
        "institutions": sorted(
            (
                {
                    "institution_id": d.institution_id,
                    "changed": d.changed,
                    "history_delta": d.history_delta,
                    "strength_delta": d.strength_delta,
                }
                for d in institution_deltas
            ),
            key=lambda r: r["institution_id"],
        ),
        "queues": sorted(
            (
                {
                    "queue_name": d.queue_name,
                    "row_key": d.row_key,
                    "delta_class": d.delta_class,
                }
                for d in queue_deltas
            ),
            key=lambda r: (r["queue_name"], r["row_key"]),
        ),
    }
    return canonical_json_digest(payload)


def plan_fingerprint(plan: InstitutionProspectPlanResult) -> str:
    """Stable fingerprint of an in-memory prospect plan (production identity check)."""
    queues = {
        name: sorted(
            (
                {
                    "queue_row_id": r.get("queue_row_id"),
                    "institution_id": r.get("institution_id"),
                    "tender_code": r.get("tender_code") or r.get("coalesced_tender_id"),
                    "equipment_category": r.get("equipment_category"),
                    "opportunity_urgency_band": r.get("opportunity_urgency_band"),
                    "prospect_strength_band": r.get("prospect_strength_band"),
                }
                for r in plan.operator_queues.get(name, [])
            ),
            key=lambda x: (
                str(x.get("queue_row_id") or ""),
                str(x.get("institution_id") or ""),
                str(x.get("equipment_category") or ""),
            ),
        )
        for name in sorted(plan.operator_queues)
    }
    profiles = sorted(
        (
            {
                "institution_id": p.get("institution_id"),
                "prospect_strength_band": p.get("prospect_strength_band"),
                "opportunity_urgency_band": p.get("opportunity_urgency_band"),
                "operator_next_action": p.get("operator_next_action"),
                "equipment_history_count": len(p.get("equipment_history") or []),
            }
            for p in plan.profiles
        ),
        key=lambda r: str(r.get("institution_id") or ""),
    )
    return canonical_json_digest(
        {
            "algorithm": f"{COMPARISON_ALGORITHM}.plan",
            "queues": queues,
            "profiles": profiles,
            "catalog_capability_digest": plan.catalog_capability_digest,
            "counts": dict(plan.counts or {}),
        }
    )
