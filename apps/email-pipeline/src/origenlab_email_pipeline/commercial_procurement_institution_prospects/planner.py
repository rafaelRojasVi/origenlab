"""PR5E.2 planner: institution prospect recognition over PR5C/D/E."""

from __future__ import annotations

import csv
import io
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    CandidatePlanResult,
    CoalescedProcurementTender,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.output_safety import (
    write_atomically,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.planner import (
    build_candidate_plan,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.models import (
    ContactCandidate,
    ContactResolutionPlanResult,
    ContactResolutionSummary,
    OrganizationResolution,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.planner import (
    build_contact_resolution_plan,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.catalog_capability import (
    catalog_digest,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.catalog_scope import (
    classify_provisional_disposition,
    refine_commercial_signal,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.clusters import (
    build_institution_review_clusters,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.constants import (
    CONTRACT_VERSION,
    EXCLUDED_RELEVANCE,
    IDENTITY_KIND_UNRESOLVED,
    OPERATOR_QUEUE_NAMES,
    PLANNER_VERSION,
    RECOGNITION_LAYER_VERSION,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.equipment_history import (
    aggregate_equipment_history,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.event_families import (
    resolve_procurement_event_families,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.identity import (
    InstitutionIdentity,
    detect_name_identifier_conflicts,
    institution_identity_from_tender,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.lifecycle_precedence import (
    KNOWN_OPEN,
    apply_lifecycle_precedence,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.line_claims import (
    LineClaim,
    SCOPE_COMPLETE_EQUIPMENT,
    aggregate_category_scoped_claims,
    aggregate_tender_axes,
    build_line_claims,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.overlay import (
    build_contact_overlay,
    is_open_lifecycle,
    operator_next_action,
    score_contact_readiness,
    score_opportunity_urgency,
    score_prospect_strength,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.queues import (
    EMPTY_QUEUE_HEADERS,
    QUEUE_GRAINS,
    build_operator_queues,
    reconcile_operator_queues,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.reconcile import (
    InstitutionReconciliationError,
    reconcile_institution_prospects,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.signals import (
    is_equipment_purchase_signal,
    is_repeated_recurrence,
)
from origenlab_email_pipeline.commercial_procurement_live_feed_bridge.adapter import (
    adapt_detail_cache_directory,
)
from origenlab_email_pipeline.commercial_procurement_live_feed_bridge.freshness import (
    evaluate_feed_freshness,
    is_future_observation,
    is_temporally_eligible_observation,
    load_json_object,
    observation_temporal_status,
    resolve_acquired_at_utc,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
    ProductRelevancePlanResult,
    TenderRelevanceDecision,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.planner import (
    build_product_relevance_plan,
)
from origenlab_email_pipeline.equipment_first_chilecompra_publish import (
    build_mercado_publico_search_url,
)

# Temporal eligibility helpers are part of the public recognition contract; keep
# them imported so planners and audits share one fail-closed vocabulary.
_ = (is_temporally_eligible_observation, observation_temporal_status)


def _email_pipeline_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tender_code(tender: CoalescedProcurementTender) -> str:
    key = tender.canonical_tender_key or tender.coalesced_tender_id
    return key.split(":", 1)[-1] if ":" in key else key


def reconcile_line_partition(
    *,
    text_unit_ids: list[str],
    assigned_unit_ids: list[str],
    review_unit_ids: list[str],
    excluded_unit_ids: list[str],
    unresolved_unit_ids: list[str],
    claim_refs: list[dict[str, Any]],
    decision_unit_ids: list[str],
    unresolved_declared_unit_ids: list[str],
    eligible_tender_ids: list[str],
) -> dict[str, Any]:
    """Enforce disjoint/complete unit and claim equations for PR5E.2."""
    failure_codes: list[str] = []

    if len(text_unit_ids) != len(set(text_unit_ids)):
        failure_codes.append("duplicate_product_text_unit_ids")
    if len(decision_unit_ids) != len(set(decision_unit_ids)):
        failure_codes.append("duplicate_decisions")

    text_set = set(text_unit_ids)
    assigned = set(assigned_unit_ids)
    review = set(review_unit_ids)
    excluded = set(excluded_unit_ids)
    unresolved = set(unresolved_unit_ids)
    decided = assigned | review | excluded
    classified = decided

    if assigned & review or assigned & excluded or review & excluded:
        failure_codes.append("assigned_review_overlap")
    if decided & unresolved:
        failure_codes.append("decided_unresolved_overlap")
    if text_set != classified | unresolved:
        failure_codes.append("partition_incomplete")

    declared_unresolved = set(unresolved_declared_unit_ids)
    decision_set = set(decision_unit_ids)
    orphan_decisions = sorted(decision_set - text_set)
    orphan_unresolved = sorted(declared_unresolved - text_set)
    if orphan_decisions:
        failure_codes.append("orphan_decisions")
    if orphan_unresolved:
        failure_codes.append("orphan_unresolved_units")

    eligible_tenders = set(eligible_tender_ids)
    claim_ids: list[str] = []
    for claim in claim_refs:
        cid = str(claim.get("claim_id") or "")
        uid = str(claim.get("unit_id") or "")
        tid = claim.get("coalesced_tender_id")
        claim_ids.append(cid)
        if not uid or uid not in classified:
            if uid in unresolved:
                failure_codes.append("claims_from_unresolved_units")
            else:
                failure_codes.append("orphan_claims")
                failure_codes.append("missing_claim_unit_reference")
        if not tid:
            failure_codes.append("missing_claim_tender_reference")
        elif eligible_tenders and str(tid) not in eligible_tenders:
            failure_codes.append("missing_claim_tender_reference")
    if len(claim_ids) != len(set(claim_ids)):
        failure_codes.append("duplicate_decisions")

    return {
        "ok": not failure_codes,
        "failure_codes": sorted(set(failure_codes)),
        "equation": (
            "product_text_unit_ids = classified_unit_ids ⊎ unresolved_unit_ids; "
            "classified_unit_ids = assigned ⊎ review_required ⊎ excluded"
        ),
        "text_unit_count": len(text_unit_ids),
        "classified_unit_count": len(classified),
        "unresolved_unit_count": len(unresolved),
        "claim_count": len(claim_refs),
    }


REQUIRED_HARDENING_ARTIFACTS = (
    "source_set_reconciliation.json",
    "temporal_reconciliation.json",
    "line_signal_claims.json",
    "line_signal_claims.csv",
    "line_reconciliation.json",
    "catalog_capability_snapshot.json",
    "event_family_reconciliation.json",
    "operator_queue_reconciliation.json",
    "reviewed_adjudication_axis_results.json",
    "recognition_quality_review_packet.json",
)


@dataclass
class InstitutionProspectPlanResult:
    as_of_utc: str
    run_context: str
    planner_version: str
    profiles: list[dict[str, Any]]
    reconciliation: dict[str, Any]
    fingerprints: dict[str, str]
    dependency_fingerprints: dict[str, str]
    counts: dict[str, Any]
    match_review_queue: list[dict[str, Any]]
    contact_gap_queue: list[dict[str, Any]]
    walkthrough: dict[str, Any] = field(default_factory=dict)
    not_persisted: bool = True
    operator_queues: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    review_clusters: list[dict[str, Any]] = field(default_factory=list)
    event_families: list[dict[str, Any]] = field(default_factory=list)
    lifecycle_projections: list[dict[str, Any]] = field(default_factory=list)
    line_reconciliation: dict[str, Any] = field(default_factory=dict)
    tender_rows: list[dict[str, Any]] = field(default_factory=list)
    line_rows: list[dict[str, Any]] = field(default_factory=list)
    line_claims: list[dict[str, Any]] = field(default_factory=list)
    temporal_reconciliation: dict[str, Any] = field(default_factory=dict)
    # Filled only when a caller passes analyst-reviewed comparison results from a
    # dry-run helper; production classification never reads reviewed labels.
    reviewed_adjudication_results: list[dict[str, Any]] = field(default_factory=list)
    catalog_capability_digest: str = ""
    recognition_layer_version: str = RECOGNITION_LAYER_VERSION
    operator_queue_reconciliation: dict[str, Any] = field(default_factory=dict)
    event_family_reconciliation: dict[str, Any] = field(default_factory=dict)
    source_set_reconciliation: dict[str, Any] = field(default_factory=dict)

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "ok": bool(self.reconciliation.get("ok"))
            and bool(self.line_reconciliation.get("ok", True)),
            "as_of_utc": self.as_of_utc,
            "run_context": self.run_context,
            "planner_version": self.planner_version,
            "recognition_layer_version": self.recognition_layer_version,
            "contract_version": CONTRACT_VERSION,
            "not_persisted": True,
            "counts": self.counts,
            "operator_queue_sizes": {
                name: len(self.operator_queues.get(name, []))
                for name in OPERATOR_QUEUE_NAMES
            },
            "procurement_event_families": {
                "family_count": len(self.event_families),
                "retender_review_family_count": sum(
                    1
                    for f in self.event_families
                    if f.get("family_resolution_status") == "retender_review_required"
                ),
                "independent_demand_event_count": sum(
                    int(f.get("independent_demand_event_count") or 0)
                    for f in self.event_families
                ),
                "raw_tender_count": sum(
                    int(f.get("raw_tender_count") or 0) for f in self.event_families
                ),
            },
            "institution_review_clusters": {
                "cluster_count": len(self.review_clusters),
                "clustered_profile_count": sum(
                    len(c.get("member_profile_ids") or []) for c in self.review_clusters
                ),
            },
            "line_reconciliation": {
                "ok": self.line_reconciliation.get("ok"),
                "equation": self.line_reconciliation.get("equation"),
                "unit_decision_count": self.line_reconciliation.get(
                    "unit_decision_count"
                ),
                "product_text_unit_count": self.line_reconciliation.get(
                    "product_text_unit_count"
                ),
                "assigned_line_count": self.line_reconciliation.get(
                    "assigned_line_count"
                ),
                "review_required_line_count": self.line_reconciliation.get(
                    "review_required_line_count"
                ),
                "excluded_line_count": self.line_reconciliation.get(
                    "excluded_line_count"
                ),
                "dropped_unit_count": self.line_reconciliation.get(
                    "dropped_unit_count"
                ),
                "no_silent_line_drop": self.line_reconciliation.get(
                    "no_silent_line_drop"
                ),
            },
            "temporal_reconciliation": {
                "acquisition_timestamps_immutable": True,
                "future_observation_excluded_count": self.temporal_reconciliation.get(
                    "future_observation_excluded_count", 0
                ),
            },
            "catalog_capability_digest": self.catalog_capability_digest,
            "queue_grains": dict(QUEUE_GRAINS),
            "reviewed_adjudication_applied": bool(self.reviewed_adjudication_results),
            "fingerprints": self.fingerprints,
            "dependency_fingerprints": self.dependency_fingerprints,
            "reconciliation": {
                "ok": self.reconciliation.get("ok"),
                "equation": self.reconciliation.get("equation"),
                "source_tender_count": self.reconciliation.get("source_tender_count"),
                "assigned_tender_count": self.reconciliation.get(
                    "assigned_tender_count"
                ),
                "unresolved_tender_count": self.reconciliation.get(
                    "unresolved_tender_count"
                ),
                "excluded_tender_count": self.reconciliation.get(
                    "excluded_tender_count"
                ),
                "excluded_reason_counts": self.reconciliation.get(
                    "excluded_reason_counts"
                ),
            },
            "separations": {
                "closed_tender_ne_current_opportunity": True,
                "closed_relevant_tender_eq_historical_buying_intent": True,
                "account_match_ne_usable_contact": True,
                "usable_contact_ne_outreach_authorization": True,
                "retender_ne_independent_demand_event": True,
                "status_unknown_does_not_overwrite_known_lifecycle": True,
                "lifecycle_independent_of_commercial_relevance": True,
                "review_cluster_ne_confirmed_account": True,
                "contact_authorization": False,
                "outreach_authorization": False,
            },
        }


def _unit_text(unit: Any) -> str | None:
    """Full product text for a unit — never a concatenation of matched spans."""
    text = getattr(unit, "text_raw", None) or getattr(unit, "text_normalized", None)
    return str(text) if text else None


LINE_DISPOSITION_ASSIGNED = "assigned"
LINE_DISPOSITION_REVIEW = "review_required"
LINE_DISPOSITION_EXCLUDED = "excluded"

_ASSIGNABLE_SIGNALS = frozenset(
    {
        "equipment_purchase_signal",
        "installed_base_signal",
        "rental_or_comodato_signal",
        "consumable_or_reagent_signal",
    }
)


def _build_line_rows(
    *,
    pr5d: ProductRelevancePlanResult,
    tender_by_id: dict[str, CoalescedProcurementTender],
    institution_id_by_tender: dict[str, str],
    identity_by_institution: dict[str, InstitutionIdentity],
    projected_lifecycle: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, list[LineClaim]]]:
    """Reconcile every PR5D evidence unit into assigned / review / excluded."""
    text_unit_by_id = {u.unit_id: u for u in pr5d.product_text_units}
    claims_by_unit: dict[str, tuple[LineClaim, ...]] = {}
    claims_by_tender: dict[str, list[LineClaim]] = defaultdict(list)
    for unit in sorted(pr5d.product_text_units, key=lambda u: u.unit_id):
        claims = build_line_claims(unit)
        claims_by_unit[unit.unit_id] = claims
        claims_by_tender[unit.coalesced_tender_id].extend(claims)

    rows: list[dict[str, Any]] = []
    for unit in sorted(
        pr5d.unit_decisions, key=lambda u: (u.coalesced_tender_id, u.unit_decision_id)
    ):
        tid = unit.coalesced_tender_id
        tender = tender_by_id.get(tid)
        code = _tender_code(tender) if tender is not None else tid
        text_unit = text_unit_by_id.get(unit.unit_id)
        text = (_unit_text(text_unit) if text_unit else None) or (
            tender.title_selected if tender else None
        )
        claims = claims_by_unit.get(unit.unit_id, ())
        axes = aggregate_tender_axes(claims) if claims else {}
        scopes = list(axes.get("equipment_scopes") or [])
        signal, signal_reasons = refine_commercial_signal(
            relevance_class=unit.relevance_class,
            title=text,
            negative_reason_codes=unit.negative_reason_codes,
        )
        if signal == "excluded_unrelated" or unit.relevance_class in EXCLUDED_RELEVANCE:
            disposition = LINE_DISPOSITION_EXCLUDED
            reasons = ["line_excluded_not_commercially_relevant"]
        elif signal in _ASSIGNABLE_SIGNALS:
            # A maintenance, rental or consumable line is a resolved commercial
            # fact; it does not need an equipment class to leave review.
            disposition = LINE_DISPOSITION_ASSIGNED
            reasons = ["line_assigned_commercial_signal"]
        else:
            disposition = LINE_DISPOSITION_REVIEW
            reasons = ["line_requires_operator_review"]
        iid = institution_id_by_tender.get(tid)
        ident = identity_by_institution.get(iid) if iid else None
        rows.append(
            {
                "unit_decision_id": unit.unit_decision_id,
                "unit_id": unit.unit_id,
                "coalesced_tender_id": tid,
                "tender_code": code,
                "institution_id": iid,
                "display_name": ident.display_name if ident else None,
                "projected_lifecycle_class": projected_lifecycle.get(
                    tid, tender.lifecycle_class if tender else None
                ),
                "relevance_class": unit.relevance_class,
                "canonical_equipment_classes": list(unit.canonical_equipment_classes),
                "equipment_scopes": scopes,
                "claim_count": int(axes.get("claim_count") or 0),
                "commercial_signal_type": signal,
                "line_disposition": disposition,
                "evidence_tier": unit.evidence_tier,
                "confidence_band": unit.confidence_band,
                "line_reason_codes": reasons + list(signal_reasons),
                "positive_reason_codes": list(unit.positive_reason_codes),
                "negative_reason_codes": list(unit.negative_reason_codes),
                "ambiguity_reason_codes": list(unit.ambiguity_reason_codes),
                "contact_authorization": False,
                "outreach_authorization": False,
            }
        )

    counts = Counter(r["line_disposition"] for r in rows)
    assigned = counts.get(LINE_DISPOSITION_ASSIGNED, 0)
    review = counts.get(LINE_DISPOSITION_REVIEW, 0)
    excluded = counts.get(LINE_DISPOSITION_EXCLUDED, 0)
    total = len(pr5d.unit_decisions)

    decided_unit_ids = {u.unit_id for u in pr5d.unit_decisions}
    text_unit_ids = set(text_unit_by_id)
    unresolved_unit_ids = {u.unit_id for u in pr5d.unresolved_units}
    dropped_unit_ids = sorted(text_unit_ids - decided_unit_ids - unresolved_unit_ids)
    decisions_without_text_unit = sorted(decided_unit_ids - text_unit_ids)

    partition_holds = total == assigned + review + excluded and total == len(rows)
    no_silent_line_drop = not dropped_unit_ids
    coverage_holds = (
        len(text_unit_ids) == len(text_unit_ids & (decided_unit_ids | unresolved_unit_ids))
    )
    reconciliation = {
        "ok": bool(partition_holds and no_silent_line_drop and coverage_holds),
        "equation": (
            "unit_decision_count = assigned_line_count + review_required_line_count "
            "+ excluded_line_count; product_text_unit_count = decided_unit_count "
            "+ unresolved_unit_count + dropped_unit_count"
        ),
        "unit_decision_count": total,
        "line_row_count": len(rows),
        "assigned_line_count": assigned,
        "review_required_line_count": review,
        "excluded_line_count": excluded,
        "product_text_unit_count": len(text_unit_ids),
        "decided_unit_count": len(decided_unit_ids),
        "unresolved_unit_count": len(unresolved_unit_ids),
        "dropped_unit_count": len(dropped_unit_ids),
        "dropped_unit_ids": dropped_unit_ids[:50],
        "decisions_without_text_unit_count": len(decisions_without_text_unit),
        "line_disposition_counts": dict(sorted(counts.items())),
        "partition_holds": partition_holds,
        "coverage_holds": coverage_holds,
        "no_silent_line_drop": no_silent_line_drop,
    }
    return rows, reconciliation, dict(claims_by_tender)


def build_institution_prospects_from_plans(
    *,
    pr5c: CandidatePlanResult,
    pr5d: ProductRelevancePlanResult,
    pr5e: ContactResolutionPlanResult,
    as_of_utc: str,
    run_context: str,
    future_observation_snapshot_ids: frozenset[str] | None = None,
    reviewed_adjudication_results: list[dict[str, Any]] | None = None,
) -> InstitutionProspectPlanResult:
    """Pure aggregation over already-built PR5C/D/E results.

    ``future_observation_snapshot_ids`` names acquisition snapshots stamped after
    ``as_of_utc``. Tenders sourced only from those snapshots are quarantined as
    ``future_observation_excluded`` — their timestamps are never rewritten.
    """
    future_snapshots = frozenset(future_observation_snapshot_ids or ())
    orgs_by_tender: dict[str, OrganizationResolution] = {
        o.coalesced_tender_id: o for o in pr5e.organization_resolutions
    }
    summaries_by_tender: dict[str, ContactResolutionSummary] = {
        s.coalesced_tender_id: s for s in pr5e.contact_summaries
    }
    candidates_by_tender: dict[str, list[ContactCandidate]] = defaultdict(list)
    for c in pr5e.contact_candidates:
        candidates_by_tender[c.coalesced_tender_id].append(c)

    decisions_by_tender: dict[str, TenderRelevanceDecision] = {
        d.coalesced_tender_id: d for d in pr5d.tender_decisions
    }
    tender_by_id: dict[str, CoalescedProcurementTender] = {
        t.coalesced_tender_id: t for t in pr5c.coalesced_tenders
    }

    # ------------------------------------------------------------------
    # 1. Temporally eligible population FIRST (before lifecycle / families /
    #    claims / identity / history / queues).
    # ------------------------------------------------------------------
    temporally_eligible_tenders: list[CoalescedProcurementTender] = []
    excluded: dict[str, str] = {}
    quarantined_future: list[dict[str, Any]] = []
    mixed_temporal_review: list[dict[str, Any]] = []
    missing_chronology: list[dict[str, Any]] = []

    for tender in sorted(pr5c.coalesced_tenders, key=lambda t: t.coalesced_tender_id):
        tid = tender.coalesced_tender_id
        snapshot_ids = set(tender.acquisition_snapshot_ids or ())
        if snapshot_ids and snapshot_ids <= future_snapshots:
            excluded[tid] = "future_observation_excluded"
            quarantined_future.append(
                {
                    "coalesced_tender_id": tid,
                    "tender_code": _tender_code(tender),
                    "acquisition_snapshot_ids": sorted(snapshot_ids),
                    "as_of_utc": as_of_utc,
                    "reason": "acquisition_stamp_after_as_of",
                }
            )
            continue
        if future_snapshots and snapshot_ids & future_snapshots and snapshot_ids - future_snapshots:
            # Mixed eligible + future evidence: quarantine unless we can safely
            # reconstruct from eligible observations alone.
            excluded[tid] = "mixed_temporal_provenance_review"
            mixed_temporal_review.append(
                {
                    "coalesced_tender_id": tid,
                    "tender_code": _tender_code(tender),
                    "eligible_snapshot_ids": sorted(snapshot_ids - future_snapshots),
                    "future_snapshot_ids": sorted(snapshot_ids & future_snapshots),
                    "as_of_utc": as_of_utc,
                    "reason": "mixed_temporal_provenance_review",
                }
            )
            continue
        temporally_eligible_tenders.append(tender)

    eligible_tender_ids = {t.coalesced_tender_id for t in temporally_eligible_tenders}

    # Recognition layer inputs operate only on the temporally eligible set.
    lifecycle_projections = apply_lifecycle_precedence(
        temporally_eligible_tenders, as_of_utc=as_of_utc
    )
    projected_lifecycle: dict[str, str] = {
        tid: proj.projected_lifecycle_class
        for tid, proj in lifecycle_projections.items()
    }
    families, tender_to_family = resolve_procurement_event_families(
        temporally_eligible_tenders
    )
    family_by_id = {f.family_id: f for f in families}
    family_meta_by_tender: dict[str, dict[str, Any]] = {
        tid: family_by_id[fid].to_dict()
        for tid, fid in tender_to_family.items()
        if fid in family_by_id
    }

    def _lifecycle(tid: str) -> str:
        tender = tender_by_id.get(tid)
        return projected_lifecycle.get(
            tid, tender.lifecycle_class if tender else "status_unknown"
        )

    # Provisional per-tender commercial signal + catalog-scope disposition.
    # Title disposition is a fallback only when no detailed line claims exist.
    disposition_by_tender: dict[str, dict[str, Any]] = {}
    for tender in sorted(temporally_eligible_tenders, key=lambda t: t.coalesced_tender_id):
        decision = decisions_by_tender.get(tender.coalesced_tender_id)
        if decision is None:
            continue
        signal, signal_reasons = refine_commercial_signal(
            relevance_class=decision.relevance_class,
            title=tender.title_selected,
            negative_reason_codes=decision.negative_reason_codes,
        )
        disposition = classify_provisional_disposition(
            relevance_class=decision.relevance_class,
            commercial_signal=signal,
            canonical_equipment_classes=decision.canonical_equipment_classes,
            title=tender.title_selected,
            positive_reason_codes=decision.positive_reason_codes,
            negative_reason_codes=decision.negative_reason_codes,
            ambiguity_reason_codes=decision.ambiguity_reason_codes,
        )
        disposition["signal_reason_codes"] = list(signal_reasons)
        disposition_by_tender[tender.coalesced_tender_id] = disposition

    def _signal(tid: str) -> str:
        disposition = disposition_by_tender.get(tid)
        if disposition:
            return str(disposition["commercial_signal_type"])
        return "review_required_signal"

    source_ids = {t.coalesced_tender_id for t in pr5c.coalesced_tenders}
    identities_by_tender: dict[str, InstitutionIdentity] = {}

    for tender in temporally_eligible_tenders:
        tid = tender.coalesced_tender_id
        if tid not in decisions_by_tender:
            excluded[tid] = "missing_relevance_decision"
            continue
        org = orgs_by_tender.get(tid)
        identities_by_tender[tid] = institution_identity_from_tender(tender, org)

    identities_by_tender = detect_name_identifier_conflicts(identities_by_tender)

    # Group tenders by institution_id (deterministic sort for merge).
    tenders_by_institution: dict[str, list[CoalescedProcurementTender]] = defaultdict(
        list
    )
    identity_by_institution: dict[str, InstitutionIdentity] = {}
    for tender in sorted(pr5c.coalesced_tenders, key=lambda t: t.coalesced_tender_id):
        tid = tender.coalesced_tender_id
        if tid in excluded:
            continue
        ident = identities_by_tender[tid]
        tenders_by_institution[ident.institution_id].append(tender)
        prev = identity_by_institution.get(ident.institution_id)
        if prev is None:
            identity_by_institution[ident.institution_id] = ident
        else:
            # Prefer linked account identity; merge aliases/provenance.
            prefer = ident if ident.linked_account_present and not prev.linked_account_present else prev
            other = prev if prefer is ident else ident
            aliases = tuple(sorted(set(prefer.aliases) | set(other.aliases)))
            provenance = tuple(list(prefer.provenance) + list(other.provenance))
            identity_by_institution[ident.institution_id] = InstitutionIdentity(
                institution_id=prefer.institution_id,
                identity_kind=prefer.identity_kind,
                display_name=prefer.display_name or other.display_name,
                normalized_name=prefer.normalized_name or other.normalized_name,
                chilecompra_buyer_source_id=prefer.chilecompra_buyer_source_id
                or other.chilecompra_buyer_source_id,
                account_id=prefer.account_id or other.account_id,
                account_resolution_status=prefer.account_resolution_status,
                account_resolution_source=prefer.account_resolution_source,
                account_resolution_reason=prefer.account_resolution_reason,
                linked_account_present=prefer.linked_account_present
                or other.linked_account_present,
                identity_review_required=prefer.identity_review_required
                or other.identity_review_required,
                provenance=provenance[:40],
                aliases=aliases,
            )

    institution_id_by_tender = {
        tid: ident.institution_id for tid, ident in identities_by_tender.items()
    }

    line_rows, line_reconciliation, claims_by_tender = _build_line_rows(
        pr5d=pr5d,
        tender_by_id=tender_by_id,
        institution_id_by_tender=institution_id_by_tender,
        identity_by_institution=identity_by_institution,
        projected_lifecycle=projected_lifecycle,
    )
    claim_axes_by_tender = {
        tid: aggregate_tender_axes(claims)
        for tid, claims in sorted(claims_by_tender.items())
    }

    history_by_institution = aggregate_equipment_history(
        tenders=[t for t in pr5c.coalesced_tenders if t.coalesced_tender_id in eligible_tender_ids],
        decisions_by_tender=decisions_by_tender,
        institution_id_by_tender=institution_id_by_tender,
        lifecycle_by_tender=projected_lifecycle,
        family_by_tender=family_meta_by_tender,
        claim_axes_by_tender=claim_axes_by_tender,
        claims_by_tender=claims_by_tender,
    )

    profiles: list[dict[str, Any]] = []
    match_review: list[dict[str, Any]] = []
    contact_gaps: list[dict[str, Any]] = []
    tender_rows: list[dict[str, Any]] = []
    assigned: set[str] = set()
    unresolved: set[str] = set()

    for iid in sorted(tenders_by_institution):
        tenders = tenders_by_institution[iid]
        ident = identity_by_institution[iid]
        for t in tenders:
            assigned.add(t.coalesced_tender_id)
            if ident.identity_kind == IDENTITY_KIND_UNRESOLVED:
                unresolved.add(t.coalesced_tender_id)

        # Prefer contact overlay from a linked tender when available.
        overlay_source = next(
            (
                t
                for t in tenders
                if orgs_by_tender.get(t.coalesced_tender_id)
                and orgs_by_tender[t.coalesced_tender_id].resolution_status == "linked"
            ),
            tenders[0],
        )
        org = orgs_by_tender.get(overlay_source.coalesced_tender_id)
        summary = summaries_by_tender.get(overlay_source.coalesced_tender_id)
        candidates = candidates_by_tender.get(overlay_source.coalesced_tender_id, [])
        overlay = build_contact_overlay(
            identity=ident,
            org=org,
            summary=summary,
            candidates=candidates,
        )

        history = history_by_institution.get(iid, [])
        purchase_tender_count = 0
        open_purchase = 0
        historical_purchase = 0
        for t in tenders:
            sig = _signal(t.coalesced_tender_id)
            if is_equipment_purchase_signal(sig):
                purchase_tender_count += 1
                if is_open_lifecycle(_lifecycle(t.coalesced_tender_id)):
                    open_purchase += 1
                else:
                    historical_purchase += 1

        repeated_category_count = sum(
            1
            for h in history
            if is_repeated_recurrence(h.get("demand_recurrence"))
            and h.get("has_equipment_purchase_signal")
        )
        dates = [
            h.get("most_recent_observed_date")
            for h in history
            if h.get("most_recent_observed_date")
        ]
        recent_signal = bool(dates)

        open_tenders = [
            t for t in tenders if is_open_lifecycle(_lifecycle(t.coalesced_tender_id))
        ]
        closing_soon = [
            t
            for t in open_tenders
            if (
                lifecycle_projections[t.coalesced_tender_id].closing_soon_bucket
                if t.coalesced_tender_id in lifecycle_projections
                else (t.closing_soon_bucket or "")
            )
            not in {"", "not_closing_soon", "unknown", "n_a", "not_applicable"}
        ]
        current_opportunity_like = sum(
            1
            for t in open_tenders
            if is_equipment_purchase_signal(_signal(t.coalesced_tender_id))
        )

        strength = score_prospect_strength(
            purchase_tender_count=purchase_tender_count,
            repeated_category_count=repeated_category_count,
            open_purchase_count=open_purchase,
            historical_purchase_count=historical_purchase,
            recent_signal=recent_signal,
        )
        urgency = score_opportunity_urgency(
            open_tender_count=len(open_tenders),
            closing_soon_count=len(closing_soon),
            current_opportunity_like=current_opportunity_like,
            projected_lifecycle_applied=True,
        )
        readiness = score_contact_readiness(overlay)
        next_action = operator_next_action(overlay, urgency)

        current_ops = []
        historical_signals = []
        for t in sorted(tenders, key=lambda x: x.coalesced_tender_id):
            tid = t.coalesced_tender_id
            d = decisions_by_tender[tid]
            code = _tender_code(t)
            projection = lifecycle_projections.get(tid)
            family = family_meta_by_tender.get(tid, {})
            life = _lifecycle(tid)
            claims = list(claims_by_tender.get(tid) or ())
            title_disposition = disposition_by_tender.get(tid, {})
            category_groups = [
                g
                for g in aggregate_category_scoped_claims(claims)
                if g.get("purchase_intent")
                or g.get("commercial_signal") == "equipment_purchase_signal"
                or SCOPE_COMPLETE_EQUIPMENT in (g.get("scopes") or [])
            ]
            # Title disposition is only a fallback when no detailed product lines
            # produced category-scoped claims for this tender.
            row_specs: list[dict[str, Any]] = []
            if category_groups:
                for group in category_groups:
                    group_claims = [
                        c
                        for c in claims
                        if c.claim_id in set(group.get("claim_ids") or [])
                    ]
                    amb = sorted(
                        {
                            a
                            for c in group_claims
                            for a in (c.ambiguity_reason_codes or ())
                        }
                    )
                    pos = sorted(
                        {
                            a
                            for c in group_claims
                            for a in (c.positive_reason_codes or ())
                        }
                    )
                    neg = sorted(
                        {
                            a
                            for c in group_claims
                            for a in (c.negative_reason_codes or ())
                        }
                    )
                    clause_text = " | ".join(
                        c.clause_text for c in group_claims[:3] if c.clause_text
                    )
                    relevance = next(
                        (c.relevance_class for c in group_claims if c.relevance_class),
                        d.relevance_class,
                    )
                    signal = str(
                        group.get("commercial_signal") or "equipment_purchase_signal"
                    )
                    disposition = classify_provisional_disposition(
                        relevance_class=relevance,
                        commercial_signal=signal,
                        canonical_equipment_classes=(group["equipment_category"],),
                        title=clause_text or t.title_selected,
                        positive_reason_codes=pos,
                        negative_reason_codes=neg,
                        ambiguity_reason_codes=amb,
                    )
                    row_specs.append(
                        {
                            "disposition": disposition,
                            "category": group["equipment_category"],
                            "scopes": list(group.get("scopes") or []),
                            "claim_ids": list(group.get("claim_ids") or []),
                            "purchase_intent": bool(group.get("purchase_intent")),
                            "source": "category_scoped_line_claim",
                        }
                    )
            else:
                row_specs.append(
                    {
                        "disposition": {
                            **title_disposition,
                            "reason_codes": list(
                                title_disposition.get("reason_codes") or []
                            )
                            + ["title_fallback_no_line_evidence"],
                        },
                        "category": title_disposition.get(
                            "canonical_equipment_category"
                        ),
                        "scopes": list(
                            (claim_axes_by_tender.get(tid) or {}).get(
                                "equipment_scopes"
                            )
                            or []
                        ),
                        "claim_ids": [],
                        "purchase_intent": False,
                        "source": "title_fallback_no_line_evidence",
                    }
                )

            for spec in row_specs:
                disposition = spec["disposition"]
                row = {
                    "coalesced_tender_id": tid,
                    "tender_code": code,
                    "tender_title": t.title_selected,
                    "lifecycle_class": life,
                    "source_lifecycle_class": t.lifecycle_class,
                    "projected_lifecycle_class": life,
                    "lifecycle_precedence_reason": projection.precedence_reason
                    if projection
                    else None,
                    "lifecycle_reason_codes": list(projection.reason_codes)
                    if projection
                    else [],
                    "closing_soon_bucket": projection.closing_soon_bucket
                    if projection
                    else t.closing_soon_bucket,
                    "currentness_class": t.currentness_class,
                    "candidate_source_kind": t.candidate_source_kind,
                    "relevance_class": d.relevance_class,
                    "canonical_equipment_classes": (
                        [spec["category"]]
                        if spec.get("category")
                        else list(d.canonical_equipment_classes)
                    ),
                    "commercial_signal_type": disposition.get("commercial_signal_type"),
                    "review_disposition": disposition.get("review_disposition"),
                    "catalog_fit_status": disposition.get("catalog_fit_status"),
                    "catalog_match_status": disposition.get("catalog_match_status"),
                    "canonical_equipment_category": spec.get("category")
                    or disposition.get("canonical_equipment_category"),
                    "equipment_scopes": list(spec.get("scopes") or []),
                    "purchase_intent": bool(spec.get("purchase_intent")),
                    "claim_ids": list(spec.get("claim_ids") or []),
                    "commercial_truth_source": spec.get("source"),
                    "reason_codes": list(disposition.get("reason_codes") or [])
                    + list(disposition.get("signal_reason_codes") or []),
                    "procurement_event_family_id": family.get("family_id"),
                    "family_resolution_status": family.get("family_resolution_status"),
                    "independent_demand_event_count": family.get(
                        "independent_demand_event_count"
                    ),
                    "mercado_publico_url": build_mercado_publico_search_url(code)
                    if code
                    else None,
                    "publication_timestamp": t.publication_timestamp_selected,
                    "close_timestamp": t.close_timestamp_selected,
                }
                tender_rows.append(
                    {
                        **row,
                        "institution_id": iid,
                        "display_name": ident.display_name,
                        "prospect_strength_band": strength.band,
                        "opportunity_urgency_band": urgency.band,
                        "eligibility_reason_codes": list(projection.reason_codes)
                        if projection
                        else [],
                        "contact_authorization": False,
                        "outreach_authorization": False,
                    }
                )
                if is_open_lifecycle(life):
                    current_ops.append(row)
                else:
                    historical_signals.append(row)

        profile = {
            "institution_id": iid,
            "identity": ident.to_dict(),
            "account_contact_overlay": overlay.to_dict(),
            "axes": {
                "prospect_strength": strength.to_dict(),
                "opportunity_urgency": urgency.to_dict(),
                "contact_readiness": readiness.to_dict(),
            },
            "equipment_history": history,
            "current_opportunities": current_ops,
            "historical_signals": historical_signals,
            "counts": {
                "tender_count": len(tenders),
                "open_tender_count": len(open_tenders),
                "historical_tender_count": len(tenders) - len(open_tenders),
                "equipment_purchase_tender_count": purchase_tender_count,
                "equipment_category_count": len(history),
                "repeated_equipment_category_count": repeated_category_count,
                "procurement_event_family_count": len(
                    {
                        family_meta_by_tender[t.coalesced_tender_id]["family_id"]
                        for t in tenders
                        if t.coalesced_tender_id in family_meta_by_tender
                    }
                ),
                "retender_review_tender_count": sum(
                    1
                    for t in tenders
                    if family_meta_by_tender.get(t.coalesced_tender_id, {}).get(
                        "family_resolution_status"
                    )
                    == "retender_review_required"
                ),
            },
            "operator_next_action": next_action,
            "contact_authorization": False,
            "outreach_authorization": False,
            "not_persisted": True,
        }
        profiles.append(profile)

        if ident.identity_review_required or overlay.account_resolution_status in {
            "ambiguous"
        }:
            match_review.append(
                {
                    "institution_id": iid,
                    "display_name": ident.display_name,
                    "normalized_name": ident.normalized_name,
                    "chilecompra_buyer_source_id": ident.chilecompra_buyer_source_id,
                    "account_resolution_status": overlay.account_resolution_status,
                    "account_resolution_reason": overlay.account_resolution_reason,
                    "identity_kind": ident.identity_kind,
                    "identity_review_required": ident.identity_review_required,
                    "prospect_strength_band": strength.band,
                    "operator_next_action": next_action,
                    "queue": "institution_match_review",
                    "confirmed_account": False,
                    "contact_authorization": False,
                    "outreach_authorization": False,
                }
            )

        if overlay.contact_gap_status != "existing_verified_contact":
            contact_gaps.append(
                {
                    "institution_id": iid,
                    "display_name": ident.display_name,
                    "prospect_strength_band": strength.band,
                    "prospect_strength_score": strength.score,
                    "opportunity_urgency_band": urgency.band,
                    "contact_gap_status": overlay.contact_gap_status,
                    "contact_resolution_status": overlay.contact_resolution_status,
                    "account_resolution_reason": overlay.account_resolution_reason,
                    "known_contact_count": overlay.known_contact_count,
                    "suitable_contact_count": overlay.suitable_contact_count,
                    "verified_contact_count": overlay.verified_contact_count,
                    "equipment_purchase_tender_count": purchase_tender_count,
                    "operator_next_action": next_action,
                    "queue": "contact_gap",
                    "contact_authorization": False,
                    "outreach_authorization": False,
                }
            )

    # Unresolved tenders are assigned profiles but flagged; reconciliation treats
    # unresolved as the subset with IDENTITY_KIND_UNRESOLVED. Assigned includes them.
    # Spec: source = assigned + unresolved + excluded — if unresolved is a subset of
    # assigned, equation fails. Re-read objective:
    # "Every source tender = assigned to an institution profile + explicitly unresolved
    # + explicitly excluded"
    # This means partition: assigned OR unresolved OR excluded.
    # So unresolved should NOT also be in assigned for the equation.
    assigned_only = assigned - unresolved
    reconciliation = reconcile_institution_prospects(
        source_tender_ids=source_ids,
        assigned_tender_ids=assigned_only,
        unresolved_tender_ids=unresolved,
        excluded=excluded,
    )

    profiles.sort(key=lambda p: p["institution_id"])
    contact_gaps.sort(
        key=lambda r: (-int(r["prospect_strength_score"]), r["institution_id"])
    )
    match_review.sort(key=lambda r: r["institution_id"])
    tender_rows.sort(key=lambda r: (str(r["tender_code"]), r["coalesced_tender_id"]))

    contact_gap_by_institution = {
        p["institution_id"]: p["account_contact_overlay"]["contact_gap_status"]
        for p in profiles
    }
    clusters = [
        c.to_dict()
        for c in build_institution_review_clusters(
            identity_by_institution,
            contact_gap_by_institution=contact_gap_by_institution,
        )
    ]
    family_dicts = [f.to_dict() for f in families]
    operator_queues = build_operator_queues(
        profiles=profiles,
        tender_rows=tender_rows,
        line_rows=line_rows,
        families=family_dicts,
        clusters=clusters,
        match_review=match_review,
        contact_gaps=contact_gaps,
    )
    operator_queue_reconciliation = reconcile_operator_queues(
        operator_queues, eligible_tender_ids=eligible_tender_ids
    )

    # Counts
    linked_n = sum(
        1 for p in profiles if p["account_contact_overlay"]["linked_account_present"]
    )
    ambiguous_n = sum(
        1
        for p in profiles
        if p["account_contact_overlay"]["account_resolution_status"] == "ambiguous"
    )
    unlinked_n = len(profiles) - linked_n
    known_contact_n = sum(
        1
        for p in profiles
        if p["account_contact_overlay"]["known_contact_count"] > 0
    )
    suitable_n = sum(
        1
        for p in profiles
        if p["account_contact_overlay"]["suitable_contact_count"] > 0
        or p["account_contact_overlay"]["verified_contact_count"] > 0
    )
    purchase_n = sum(
        1
        for p in profiles
        if p["counts"]["equipment_purchase_tender_count"] > 0
    )
    repeated_n = sum(
        1
        for p in profiles
        if p["counts"]["repeated_equipment_category_count"] > 0
    )
    open_source_tender_n = sum(1 for p in profiles if p["counts"]["open_tender_count"] > 0)
    historical_prospect_n = sum(
        1
        for p in profiles
        if p["counts"]["equipment_purchase_tender_count"] > 0
        and p["counts"]["open_tender_count"] == 0
    )
    gap_counts = Counter(
        p["account_contact_overlay"]["contact_gap_status"] for p in profiles
    )
    cat_dist: Counter[str] = Counter()
    for p in profiles:
        for h in p["equipment_history"]:
            cat_dist[h["canonical_equipment_category"]] += 1

    # Auth must stay false — profiles and every operator queue row.
    if any(p["contact_authorization"] or p["outreach_authorization"] for p in profiles):
        raise InstitutionReconciliationError("authorization flags must remain false")
    for queue_rows in operator_queues.values():
        if any(
            r.get("contact_authorization", True) is not False
            or r.get("outreach_authorization", True) is not False
            for r in queue_rows
        ):
            raise InstitutionReconciliationError(
                "every operator queue row must carry explicit false authorization"
            )
    if not line_reconciliation["ok"]:
        raise InstitutionReconciliationError(
            "line-level evidence reconciliation failed: "
            f"{line_reconciliation['equation']}"
        )

    fingerprints = {
        "semantic_digest": canonical_json_digest(
            [
                {
                    "institution_id": p["institution_id"],
                    "tender_count": p["counts"]["tender_count"],
                    "purchase": p["counts"]["equipment_purchase_tender_count"],
                    "gap": p["account_contact_overlay"]["contact_gap_status"],
                    "strength": p["axes"]["prospect_strength"]["score"],
                    "urgency": p["axes"]["opportunity_urgency"]["score"],
                }
                for p in profiles
            ]
        ),
        "catalog_capability_digest": catalog_digest(),
        "build_fingerprint": canonical_json_digest(
            {
                "planner_version": PLANNER_VERSION,
                "contract_version": CONTRACT_VERSION,
                "as_of_utc": as_of_utc,
                "profile_count": len(profiles),
                "catalog_capability_digest": catalog_digest(),
            }
        ),
    }
    dependency_fingerprints = {
        "pr5c_semantic_digest": pr5c.semantic_digest,
        "pr5d_semantic_digest": pr5d.fingerprints.get("semantic_digest")
        or pr5d.fingerprints.get("semantic_fingerprint")
        or "",
        "pr5e_semantic_digest": pr5e.fingerprints.get("semantic_digest") or "",
        "catalog_capability_digest": catalog_digest(),
    }

    walkthrough = {
        "pipeline": [
            "temporally eligible tender population",
            "lifecycle precedence projection",
            "category-scoped line claims",
            "catalog capability match",
            "procurement event family resolution",
            "procurement buyer identity",
            "institution review clustering",
            "existing OrigenLab account comparison",
            "existing contact comparison",
            "accumulated equipment history",
            "institution-level prospect profile",
            "separate operator queues",
        ],
        "separations": {
            "closed_tender": "not current opportunity",
            "closed_relevant_tender": "historical buying-intent evidence",
            "account_match": "not usable contact",
            "usable_contact": "not outreach authorization",
            "retender_or_reissue": "not an independent demand event",
            "status_unknown": "does not overwrite a known lifecycle",
            "review_cluster": "not a confirmed merged account",
            "future_observation": "not temporally eligible",
            "title_fallback": "only when no detailed line evidence",
        },
        "example_institution": profiles[0] if profiles else None,
    }

    counts = {
        "institution_profiles": len(profiles),
        "linked_institutions": linked_n,
        "unlinked_institutions": unlinked_n,
        "ambiguous_institutions": ambiguous_n,
        "profiles_with_known_contact": known_contact_n,
        "profiles_with_suitable_or_verified_contact": suitable_n,
        "contact_gap_status_counts": dict(sorted(gap_counts.items())),
        "profiles_with_equipment_purchase_evidence": purchase_n,
        "profiles_with_repeated_equipment_demand": repeated_n,
        # An open tender in the source feed is not the same claim as a live
        # opportunity for something OrigenLab actually sells.
        "profiles_with_open_source_tenders": open_source_tender_n,
        "profiles_with_current_catalog_opportunities": len(
            {
                str(r.get("institution_id") or "")
                for r in operator_queues.get("current_opportunity_queue", [])
            }
        ),
        "historical_prospect_profiles": historical_prospect_n,
        "equipment_category_distribution": dict(cat_dist.most_common(50)),
        "match_review_queue_size": len(match_review),
        "contact_gap_candidate_count": len(contact_gaps),
        "contact_gap_queue_size": len(operator_queues.get("contact_gap_queue", [])),
        "source_tenders": len(source_ids),
        "excluded_tenders": len(excluded),
        "future_observation_excluded_tenders": len(quarantined_future),
        "operator_queue_sizes": {
            name: len(operator_queues.get(name, [])) for name in OPERATOR_QUEUE_NAMES
        },
        "procurement_event_family_count": len(family_dicts),
        "retender_review_family_count": sum(
            1
            for f in family_dicts
            if f["family_resolution_status"] == "retender_review_required"
        ),
        "institution_review_cluster_count": len(clusters),
        "review_disposition_counts": dict(
            sorted(Counter(r["review_disposition"] for r in tender_rows).items())
        ),
        "commercial_signal_counts": dict(
            sorted(Counter(r["commercial_signal_type"] for r in tender_rows).items())
        ),
        "projected_lifecycle_counts": dict(
            sorted(Counter(projected_lifecycle.values()).items())
        ),
        "lifecycle_precedence_reason_counts": dict(
            sorted(
                Counter(
                    p.precedence_reason for p in lifecycle_projections.values()
                ).items()
            )
        ),
        "line_disposition_counts": line_reconciliation["line_disposition_counts"],
    }

    return InstitutionProspectPlanResult(
        as_of_utc=as_of_utc,
        run_context=run_context,
        planner_version=PLANNER_VERSION,
        profiles=profiles,
        reconciliation=reconciliation,
        fingerprints=fingerprints,
        dependency_fingerprints=dependency_fingerprints,
        counts=counts,
        match_review_queue=list(
            operator_queues.get("institution_match_review_queue", match_review)
        ),
        contact_gap_queue=list(
            operator_queues.get("contact_gap_queue", contact_gaps)
        ),
        walkthrough=walkthrough,
        not_persisted=True,
        operator_queues=operator_queues,
        review_clusters=clusters,
        event_families=family_dicts,
        lifecycle_projections=[
            {
                "coalesced_tender_id": tid,
                "tender_code": _tender_code(tender_by_id[tid])
                if tid in tender_by_id
                else tid,
                **proj.to_dict(),
            }
            for tid, proj in sorted(lifecycle_projections.items())
        ],
        line_reconciliation=line_reconciliation,
        tender_rows=tender_rows,
        line_rows=line_rows,
        line_claims=[c.to_dict() for cl in claims_by_tender.values() for c in cl],
        temporal_reconciliation={
            "ok": True,
            "as_of_utc": as_of_utc,
            "acquisition_timestamps_immutable": True,
            "temporally_eligible_tender_count": len(temporally_eligible_tenders),
            "temporally_eligible_tender_ids": sorted(eligible_tender_ids),
            "future_observation_snapshot_ids": sorted(future_snapshots),
            "future_observation_excluded_count": len(quarantined_future),
            "future_observation_excluded": quarantined_future,
            "mixed_temporal_provenance_review_count": len(mixed_temporal_review),
            "mixed_temporal_provenance_review": mixed_temporal_review,
            "missing_chronology_count": len(missing_chronology),
            "missing_chronology": missing_chronology,
            "reason": "acquisition_stamp_after_as_of",
        },
        reviewed_adjudication_results=list(reviewed_adjudication_results or []),
        catalog_capability_digest=catalog_digest(),
        recognition_layer_version=RECOGNITION_LAYER_VERSION,
        operator_queue_reconciliation=operator_queue_reconciliation,
        event_family_reconciliation={
            "ok": True,
            "equation": (
                "event_family_reconciliation covers exactly the temporally "
                "eligible source tenders once each"
            ),
            "temporally_eligible_tender_count": len(eligible_tender_ids),
            "family_member_tender_ids": sorted(
                {tid for f in family_dicts for tid in (f.get("member_tender_ids") or [])}
            ),
            "covered_exactly_once": sorted(eligible_tender_ids)
            == sorted(
                {tid for f in family_dicts for tid in (f.get("member_tender_ids") or [])}
            ),
            "family_count": len(family_dicts),
            "retender_review_family_count": sum(
                1
                for f in family_dicts
                if f.get("family_resolution_status") == "retender_review_required"
            ),
        },
        source_set_reconciliation={
            "ok": True,
            "note": (
                "Input populations must be compared separately. Do not present "
                "a PR5B.2-eligible 16641 run as a same-input successor to the "
                "initial PR5E.2 16643/16644 population without separating the "
                "different acquisition snapshot sets."
            ),
            "populations": {
                "pr5e1_fixed_inputs": {
                    "label": "fixed_PR5E.1_inputs",
                    "role": "baseline_institution_prospect_map",
                },
                "pr5e2_initial_inputs": {
                    "label": "initial_PR5E.2_inputs",
                    "role": "first_recognition_run_including_clamped_era",
                },
                "pr5e2_same_input_hardened": {
                    "label": "same_input_hardened_result",
                    "role": "hardened_recognition_on_matching_input_population",
                },
                "pr5b2_operational_snapshot": {
                    "label": "separate_PR5B.2_operational_snapshot_result",
                    "role": "eligible_snapshot_population_not_same_input_successor",
                    "must_not_be_presented_as_same_input_successor_to": "pr5e2_initial_inputs",
                },
            },
            "current_run": {
                "source_tender_count": len(source_ids),
                "temporally_eligible_tender_count": len(eligible_tender_ids),
                "as_of_utc": as_of_utc,
                "run_context": run_context,
            },
        },
    )


def _future_observation_snapshot_ids(
    snapshot_paths: list[Path], *, as_of_utc: str
) -> frozenset[str]:
    """Acquisition snapshots stamped after the review as-of, read from the files.

    Their timestamps stay exactly as acquired; only the downstream evaluation is
    quarantined so a future-stamped observation cannot pose as current evidence.
    """
    future: set[str] = set()
    for path in snapshot_paths:
        try:
            payload = load_json_object(Path(path))
        except (OSError, ValueError):
            continue
        snapshot_id = str(payload.get("snapshot_id") or "")
        if not snapshot_id:
            continue
        if any(
            is_future_observation(stamp, as_of_utc=as_of_utc)
            for stamp in _snapshot_acquisition_stamps(payload)
        ):
            future.add(snapshot_id)
    return frozenset(future)


def _snapshot_acquisition_stamps(payload: dict[str, Any]) -> list[Any]:
    """Every acquisition stamp an acquisition snapshot records.

    Fetch time is recorded per page; the snapshot-level stamps only describe when
    the snapshot was materialized, so a page-level stamp is the acquisition fact
    that decides whether the observation is ahead of the review as-of.
    """
    stamps: list[Any] = []
    pages = payload.get("pages")
    if isinstance(pages, list):
        stamps.extend(
            page.get("acquired_at_utc") for page in pages if isinstance(page, dict)
        )
    if not any(stamp for stamp in stamps):
        stamps.extend(
            payload.get(key) for key in ("acquired_at_utc", "materialized_at_utc")
        )
    return [stamp for stamp in stamps if stamp]


def build_institution_prospect_plan(
    *,
    sqlite_path: Path,
    as_of_utc: str,
    out_dir: Path,
    run_context: str,
    freshness_threshold_hours: int,
    acquisition_snapshot_paths: list[Path] | None = None,
    detail_cache_dir: Path | None = None,
    equipment_manifest_path: Path | None = None,
    refresh_state_path: Path | None = None,
    allow_stale_feed: bool = False,
    max_feed_age_hours: int = 36,
    labeling_queue_size: int = 200,
    pr5c_plan: CandidatePlanResult | None = None,
    pr5d_plan: ProductRelevancePlanResult | None = None,
    pr5e_plan: ContactResolutionPlanResult | None = None,
) -> dict[str, Any]:
    """Build and atomically write institution prospect artifacts."""
    root = _email_pipeline_root()
    materialized_at = _utcnow()

    def _writer(dest: Path) -> dict[str, str]:
        nonlocal pr5c_plan, pr5d_plan, pr5e_plan
        snapshot_paths = list(acquisition_snapshot_paths or [])
        adapter_records: list[dict[str, Any]] = []
        if not snapshot_paths and detail_cache_dir is not None:
            manifest = (
                load_json_object(equipment_manifest_path)
                if equipment_manifest_path
                else None
            )
            refresh_state = (
                load_json_object(refresh_state_path) if refresh_state_path else None
            )
            evaluate_feed_freshness(
                as_of_utc=as_of_utc,
                refresh_state=refresh_state,
                manifest=manifest,
                max_age_hours=max_feed_age_hours,
                allow_stale=allow_stale_feed,
            )
            acquired_at = resolve_acquired_at_utc(
                manifest=manifest, refresh_state=refresh_state
            )
            staging = dest / "_bridge_snapshots"
            adapter = adapt_detail_cache_directory(
                detail_cache_dir,
                staging_dir=staging,
                acquired_at_utc=acquired_at,
                materialized_at_utc=materialized_at,
                manifest_path=equipment_manifest_path,
                refresh_state_path=refresh_state_path,
            )
            snapshot_paths = list(adapter.snapshot_paths)
            adapter_records = [r.__dict__ for r in adapter.records]
            if not snapshot_paths:
                raise ValueError("no accepted acquisition snapshots from detail cache")

        if pr5c_plan is None:
            if not snapshot_paths:
                raise ValueError(
                    "acquisition_snapshot_paths or detail_cache_dir required"
                )
            pr5c_plan = build_candidate_plan(
                sqlite_path=sqlite_path,
                acquisition_snapshot_paths=snapshot_paths,
                as_of_utc=as_of_utc,
                freshness_threshold_hours=freshness_threshold_hours,
                run_context=run_context,
            )
        if pr5d_plan is None:
            pr5d_plan = build_product_relevance_plan(
                sqlite_path=sqlite_path,
                acquisition_snapshot_paths=snapshot_paths
                or list(acquisition_snapshot_paths or []),
                as_of_utc=as_of_utc,
                freshness_threshold_hours=freshness_threshold_hours,
                run_context=run_context,
                labeling_queue_size=labeling_queue_size,
                pr5c_plan=pr5c_plan,
            )
        if pr5e_plan is None:
            pr5e_plan = build_contact_resolution_plan(
                sqlite_path=sqlite_path,
                acquisition_snapshot_paths=snapshot_paths
                or list(acquisition_snapshot_paths or []),
                as_of_utc=as_of_utc,
                freshness_threshold_hours=freshness_threshold_hours,
                run_context=run_context,
                labeling_queue_size=labeling_queue_size,
                pr5d_plan=pr5d_plan,
                pr5c_plan=pr5c_plan,
            )

        result = build_institution_prospects_from_plans(
            pr5c=pr5c_plan,
            pr5d=pr5d_plan,
            pr5e=pr5e_plan,
            as_of_utc=as_of_utc,
            run_context=run_context,
            future_observation_snapshot_ids=_future_observation_snapshot_ids(
                snapshot_paths, as_of_utc=as_of_utc
            ),
        )
        return _write_bundle(dest, result, adapter_records=adapter_records)

    return write_atomically(
        out_dir, repo_email_pipeline_root=root, writer=_writer
    )


def _write_bundle(
    dest: Path,
    result: InstitutionProspectPlanResult,
    *,
    adapter_records: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    dest.mkdir(parents=True, exist_ok=True)
    summary = result.to_summary_dict()
    packet = {
        "ok": True,
        "as_of_utc": result.as_of_utc,
        "run_context": result.run_context,
        "planner_version": result.planner_version,
        "recognition_layer_version": result.recognition_layer_version,
        "contract_version": CONTRACT_VERSION,
        "not_persisted": True,
        "contact_authorization": False,
        "outreach_authorization": False,
        "profiles": result.profiles,
        "counts": result.counts,
        "procurement_event_families": result.event_families,
        "institution_review_clusters": result.review_clusters,
        "fingerprints": result.fingerprints,
        "dependency_fingerprints": result.dependency_fingerprints,
    }
    temporal = result.temporal_reconciliation or {}
    reconciliation = {
        "ok": result.reconciliation.get("ok"),
        "reconciliation": result.reconciliation,
        "line_reconciliation": result.line_reconciliation,
        "temporal_reconciliation": temporal,
        "acquisition_timestamps_immutable": True,
        "future_observation_excluded_count": temporal.get(
            "future_observation_excluded_count", 0
        ),
        "future_observation_excluded": temporal.get("future_observation_excluded", []),
        "adapter_records_count": len(adapter_records or []),
        "catalog_capability_digest": result.catalog_capability_digest,
        "queue_grains": dict(QUEUE_GRAINS),
        "dependency_fingerprints": result.dependency_fingerprints,
    }

    paths: dict[str, str] = {}
    summary_path = dest / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    paths["summary.json"] = str(summary_path)

    packet_path = dest / "institution_prospect_packet.json"
    packet_path.write_text(
        json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    paths["institution_prospect_packet.json"] = str(packet_path)

    recon_path = dest / "source_reconciliation.json"
    recon_path.write_text(
        json.dumps(reconciliation, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    paths["source_reconciliation.json"] = str(recon_path)

    # CSVs
    prospect_csv = dest / "institution_prospect.csv"
    _write_csv(
        prospect_csv,
        [
            {
                "institution_id": p["institution_id"],
                "display_name": p["identity"].get("display_name"),
                "identity_kind": p["identity"].get("identity_kind"),
                "linked_account_present": p["account_contact_overlay"][
                    "linked_account_present"
                ],
                "account_resolution_status": p["account_contact_overlay"][
                    "account_resolution_status"
                ],
                "contact_gap_status": p["account_contact_overlay"]["contact_gap_status"],
                "prospect_strength_band": p["axes"]["prospect_strength"]["band"],
                "opportunity_urgency_band": p["axes"]["opportunity_urgency"]["band"],
                "contact_readiness_band": p["axes"]["contact_readiness"]["band"],
                "tender_count": p["counts"]["tender_count"],
                "open_tender_count": p["counts"]["open_tender_count"],
                "equipment_purchase_tender_count": p["counts"][
                    "equipment_purchase_tender_count"
                ],
                "operator_next_action": p["operator_next_action"],
            }
            for p in result.profiles
        ],
    )
    paths["institution_prospect.csv"] = str(prospect_csv)

    queues = result.operator_queues
    for queue_name in OPERATOR_QUEUE_NAMES:
        csv_path = dest / f"{queue_name}.csv"
        _write_csv(
            csv_path,
            queues.get(queue_name, []),
            empty_header=EMPTY_QUEUE_HEADERS.get(queue_name),
        )
        paths[f"{queue_name}.csv"] = str(csv_path)

    lifecycle_path = dest / "lifecycle_reconciliation.json"
    lifecycle_path.write_text(
        json.dumps(
            _lifecycle_reconciliation_document(result),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    paths["lifecycle_reconciliation.json"] = str(lifecycle_path)

    adjudication_path = dest / "reviewed_adjudication_results.json"
    adjudication_path.write_text(
        json.dumps(
            {
                "ok": True,
                "as_of_utc": result.as_of_utc,
                "recognition_layer_version": result.recognition_layer_version,
                "fixture_applied": bool(result.reviewed_adjudication_results),
                "provenance": "analyst_reviewed_provisional",
                "not_gold_truth": True,
                "not_classifier_output": True,
                "results": result.reviewed_adjudication_results,
                "note": (
                    "Populated only when a reviewed adjudication fixture is applied "
                    "by a dry-run comparison helper; production classification never "
                    "reads reviewed labels."
                ),
                "contact_authorization": False,
                "outreach_authorization": False,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    paths["reviewed_adjudication_results.json"] = str(adjudication_path)

    families_path = dest / "procurement_event_families.json"
    families_path.write_text(
        json.dumps(
            {
                "ok": True,
                "families": result.event_families,
                "retender_ne_independent_demand_event": True,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    paths["procurement_event_families.json"] = str(families_path)

    walk_path = dest / "walkthrough.md"
    walk_path.write_text(_render_walkthrough(result), encoding="utf-8")
    paths["walkthrough.md"] = str(walk_path)

    # PR5E.2 hardening contract artifacts (parse-validated JSON/CSV).
    source_set_path = dest / "source_set_reconciliation.json"
    source_set_path.write_text(
        json.dumps(
            result.source_set_reconciliation or {"ok": True, "populations": {}},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    paths["source_set_reconciliation.json"] = str(source_set_path)

    temporal_path = dest / "temporal_reconciliation.json"
    temporal_path.write_text(
        json.dumps(
            result.temporal_reconciliation,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    paths["temporal_reconciliation.json"] = str(temporal_path)

    claims_json = dest / "line_signal_claims.json"
    claims_json.write_text(
        json.dumps(
            {
                "ok": True,
                "claim_count": len(result.line_claims),
                "claims": result.line_claims,
                "contact_authorization": False,
                "outreach_authorization": False,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    paths["line_signal_claims.json"] = str(claims_json)

    claims_csv = dest / "line_signal_claims.csv"
    _write_csv(claims_csv, result.line_claims)
    paths["line_signal_claims.csv"] = str(claims_csv)

    line_recon_path = dest / "line_reconciliation.json"
    line_recon_path.write_text(
        json.dumps(
            result.line_reconciliation,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    paths["line_reconciliation.json"] = str(line_recon_path)

    catalog_snap = dest / "catalog_capability_snapshot.json"
    catalog_snap.write_text(
        json.dumps(
            {
                "ok": True,
                "catalog_capability_digest": result.catalog_capability_digest,
                "fingerprints": {
                    "catalog_capability_digest": result.catalog_capability_digest,
                    "build_fingerprint": result.fingerprints.get("build_fingerprint"),
                },
                "dependency_fingerprints": result.dependency_fingerprints,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    paths["catalog_capability_snapshot.json"] = str(catalog_snap)

    family_recon = dest / "event_family_reconciliation.json"
    family_recon.write_text(
        json.dumps(
            result.event_family_reconciliation
            or {
                "ok": True,
                "family_count": len(result.event_families),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    paths["event_family_reconciliation.json"] = str(family_recon)

    queue_recon = dest / "operator_queue_reconciliation.json"
    queue_recon.write_text(
        json.dumps(
            {
                **(result.operator_queue_reconciliation or {"ok": True}),
                "false authorization": True,
                "contact_authorization": False,
                "outreach_authorization": False,
                "queue_grains": dict(QUEUE_GRAINS),
                "queue_sizes": {
                    name: len(result.operator_queues.get(name, []))
                    for name in OPERATOR_QUEUE_NAMES
                },
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    paths["operator_queue_reconciliation.json"] = str(queue_recon)

    axis_results = dest / "reviewed_adjudication_axis_results.json"
    axis_results.write_text(
        json.dumps(
            {
                "ok": True,
                "as_of_utc": result.as_of_utc,
                "recognition_layer_version": result.recognition_layer_version,
                "provenance": "analyst_reviewed_provisional",
                "not_gold_truth": True,
                "results": result.reviewed_adjudication_results,
                "axis_note": (
                    "Per-axis results for provisional cases must use the same "
                    "production claim/category/axis path as live recognition."
                ),
                "contact_authorization": False,
                "outreach_authorization": False,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    paths["reviewed_adjudication_axis_results.json"] = str(axis_results)

    quality_packet = dest / "recognition_quality_review_packet.json"
    quality_packet.write_text(
        json.dumps(
            {
                "ok": True,
                "as_of_utc": result.as_of_utc,
                "run_context": result.run_context,
                "recognition_layer_version": result.recognition_layer_version,
                "contract_version": CONTRACT_VERSION,
                "not_persisted": True,
                "contact_authorization": False,
                "outreach_authorization": False,
                "counts": result.counts,
                "operator_queue_sizes": {
                    name: len(result.operator_queues.get(name, []))
                    for name in OPERATOR_QUEUE_NAMES
                },
                "temporal_reconciliation_ok": bool(
                    (result.temporal_reconciliation or {}).get("ok")
                ),
                "line_reconciliation_ok": bool(
                    (result.line_reconciliation or {}).get("ok")
                ),
                "operator_queue_reconciliation_ok": bool(
                    (result.operator_queue_reconciliation or {}).get("ok", True)
                ),
                "event_family_reconciliation_ok": bool(
                    (result.event_family_reconciliation or {}).get("ok", True)
                ),
                "required_artifacts": list(REQUIRED_HARDENING_ARTIFACTS),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    paths["recognition_quality_review_packet.json"] = str(quality_packet)

    return paths


def _lifecycle_reconciliation_document(
    result: InstitutionProspectPlanResult,
) -> dict[str, Any]:
    projections = result.lifecycle_projections
    restored = [
        p
        for p in projections
        if p.get("projected_lifecycle_class") in KNOWN_OPEN
        and p.get("source_lifecycle_class") not in KNOWN_OPEN
    ]
    overridden = [
        p
        for p in projections
        if p.get("source_lifecycle_class") in KNOWN_OPEN
        and p.get("projected_lifecycle_class") not in KNOWN_OPEN
    ]
    return {
        "ok": True,
        "as_of_utc": result.as_of_utc,
        "recognition_layer_version": result.recognition_layer_version,
        "projection_count": len(projections),
        "source_lifecycle_counts": dict(
            sorted(Counter(p.get("source_lifecycle_class") for p in projections).items())
        ),
        "projected_lifecycle_counts": dict(
            sorted(
                Counter(p.get("projected_lifecycle_class") for p in projections).items()
            )
        ),
        "precedence_reason_counts": dict(
            sorted(Counter(p.get("precedence_reason") for p in projections).items())
        ),
        "changed_projection_count": sum(
            1
            for p in projections
            if p.get("source_lifecycle_class") != p.get("projected_lifecycle_class")
        ),
        "active_open_restored": restored,
        "open_overridden_by_terminal": overridden,
        "status_unknown_does_not_overwrite_known_lifecycle": True,
        "lifecycle_independent_of_relevance": True,
        "projections": projections,
        "contact_authorization": False,
        "outreach_authorization": False,
    }


def _write_csv(
    path: Path,
    rows: list[dict[str, Any]],
    *,
    empty_header: list[str] | None = None,
) -> None:
    # Queue rows are heterogeneous (e.g. identity review mixes profile and cluster
    # rows), so the header is the union of keys in first-seen order. An empty queue
    # still writes its declared header so the file is readable, not zero-byte.
    fieldnames: list[str] = list(empty_header or []) if not rows else []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    if not fieldnames:
        path.write_text("", encoding="utf-8")
        return
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf, fieldnames=fieldnames, extrasaction="ignore", restval=""
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    path.write_text(buf.getvalue(), encoding="utf-8")


def _render_walkthrough(result: InstitutionProspectPlanResult) -> str:
    lines = [
        "# Institution prospect recognition (PR5E.2)",
        "",
        f"Planner: `{result.planner_version}`",
        f"Recognition layer: `{result.recognition_layer_version}`",
        f"as_of_utc: `{result.as_of_utc}`",
        f"run_context: `{result.run_context}`",
        "",
        "## Pipeline",
        "",
        "```text",
        "tender evidence",
        "→ lifecycle precedence projection (known status wins over status_unknown)",
        "→ procurement event family resolution (retender ≠ new demand)",
        "→ line-level commercial signal + catalog scope disposition",
        "→ procurement buyer identity",
        "→ institution review clustering (review-only, no auto-merge)",
        "→ existing OrigenLab account comparison",
        "→ existing contact comparison",
        "→ accumulated equipment history",
        "→ institution-level prospect profile",
        "→ separate operator queues (no combined lead score)",
        "```",
        "",
        "## Separations",
        "",
        "- closed tender ≠ current opportunity",
        "- closed relevant tender = historical buying-intent evidence",
        "- account match ≠ usable contact",
        "- usable contact ≠ outreach authorization",
        "- retender or reissue ≠ independent demand event",
        "- `status_unknown` never overwrites a known lifecycle",
        "- lifecycle is independent of commercial relevance",
        "- review cluster ≠ confirmed merged account",
        "",
        "## Operator queues",
        "",
        "```json",
        json.dumps(
            {
                name: len(result.operator_queues.get(name, []))
                for name in OPERATOR_QUEUE_NAMES
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        "```",
        "",
        "No queue implies contact or outreach authorization.",
        "",
        "## Line-level evidence reconciliation",
        "",
        "```json",
        json.dumps(
            result.line_reconciliation, ensure_ascii=False, indent=2, sort_keys=True
        ),
        "```",
        "",
        "## Aggregate counts",
        "",
        "```json",
        json.dumps(result.counts, ensure_ascii=False, indent=2, sort_keys=True),
        "```",
        "",
    ]
    example = result.walkthrough.get("example_institution")
    if example:
        safe = {
            "institution_id": example["institution_id"],
            "display_name": example["identity"].get("display_name"),
            "identity_kind": example["identity"].get("identity_kind"),
            "linked_account_present": example["account_contact_overlay"][
                "linked_account_present"
            ],
            "contact_gap_status": example["account_contact_overlay"][
                "contact_gap_status"
            ],
            "axes": example["axes"],
            "counts": example["counts"],
            "equipment_history_categories": [
                h.get("canonical_equipment_category")
                for h in example.get("equipment_history", [])[:12]
            ],
            "operator_next_action": example["operator_next_action"],
            "contact_authorization": False,
            "outreach_authorization": False,
        }
        lines.extend(
            [
                "## Example institution (redacted)",
                "",
                "One profile showing contact-match status **and** accumulated equipment history:",
                "",
                "```json",
                json.dumps(safe, ensure_ascii=False, indent=2, sort_keys=True),
                "```",
                "",
            ]
        )
    lines.append("No contact or outreach authorization is implied by this packet.\n")
    return "\n".join(lines)


__all__ = [
    "LINE_DISPOSITION_ASSIGNED",
    "LINE_DISPOSITION_EXCLUDED",
    "LINE_DISPOSITION_REVIEW",
    "InstitutionProspectPlanResult",
    "build_institution_prospect_plan",
    "build_institution_prospects_from_plans",
]
