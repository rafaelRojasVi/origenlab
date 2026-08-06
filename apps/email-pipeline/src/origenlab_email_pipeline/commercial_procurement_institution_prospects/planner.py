"""PR5E.1 planner: institution prospect intelligence over PR5C/D/E."""

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
from origenlab_email_pipeline.commercial_procurement_institution_prospects.constants import (
    CONTRACT_VERSION,
    IDENTITY_KIND_UNRESOLVED,
    PLANNER_VERSION,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.equipment_history import (
    aggregate_equipment_history,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.identity import (
    InstitutionIdentity,
    detect_name_identifier_conflicts,
    institution_identity_from_tender,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.overlay import (
    build_contact_overlay,
    is_open_lifecycle,
    operator_next_action,
    score_contact_readiness,
    score_opportunity_urgency,
    score_prospect_strength,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.reconcile import (
    InstitutionReconciliationError,
    reconcile_institution_prospects,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.signals import (
    classify_commercial_evidence_signal,
    is_equipment_purchase_signal,
    recurrence_label,
)
from origenlab_email_pipeline.commercial_procurement_live_feed_bridge.adapter import (
    adapt_detail_cache_directory,
)
from origenlab_email_pipeline.commercial_procurement_live_feed_bridge.freshness import (
    evaluate_feed_freshness,
    load_json_object,
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


def _email_pipeline_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tender_code(tender: CoalescedProcurementTender) -> str:
    key = tender.canonical_tender_key or tender.coalesced_tender_id
    return key.split(":", 1)[-1] if ":" in key else key


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

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "ok": bool(self.reconciliation.get("ok")),
            "as_of_utc": self.as_of_utc,
            "run_context": self.run_context,
            "planner_version": self.planner_version,
            "contract_version": CONTRACT_VERSION,
            "not_persisted": True,
            "counts": self.counts,
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
                "contact_authorization": False,
                "outreach_authorization": False,
            },
        }


def build_institution_prospects_from_plans(
    *,
    pr5c: CandidatePlanResult,
    pr5d: ProductRelevancePlanResult,
    pr5e: ContactResolutionPlanResult,
    as_of_utc: str,
    run_context: str,
) -> InstitutionProspectPlanResult:
    """Pure aggregation over already-built PR5C/D/E results."""
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

    source_ids = {t.coalesced_tender_id for t in pr5c.coalesced_tenders}
    excluded: dict[str, str] = {}
    identities_by_tender: dict[str, InstitutionIdentity] = {}

    for tender in sorted(pr5c.coalesced_tenders, key=lambda t: t.coalesced_tender_id):
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
    history_by_institution = aggregate_equipment_history(
        tenders=pr5c.coalesced_tenders,
        decisions_by_tender=decisions_by_tender,
        institution_id_by_tender=institution_id_by_tender,
    )

    profiles: list[dict[str, Any]] = []
    match_review: list[dict[str, Any]] = []
    contact_gaps: list[dict[str, Any]] = []
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
            d = decisions_by_tender[t.coalesced_tender_id]
            sig = classify_commercial_evidence_signal(d.relevance_class)
            if is_equipment_purchase_signal(sig):
                purchase_tender_count += 1
                if is_open_lifecycle(t.lifecycle_class):
                    open_purchase += 1
                else:
                    historical_purchase += 1

        repeated_category_count = sum(
            1
            for h in history
            if h.get("demand_recurrence") == recurrence_label(2)
            and h.get("has_equipment_purchase_signal")
        )
        dates = [
            h.get("most_recent_observed_date")
            for h in history
            if h.get("most_recent_observed_date")
        ]
        recent_signal = bool(dates)

        open_tenders = [t for t in tenders if is_open_lifecycle(t.lifecycle_class)]
        closing_soon = [
            t
            for t in open_tenders
            if (t.closing_soon_bucket or "")
            not in {"", "not_closing_soon", "unknown", "n_a"}
        ]
        current_opportunity_like = sum(
            1
            for t in open_tenders
            if is_equipment_purchase_signal(
                classify_commercial_evidence_signal(
                    decisions_by_tender[t.coalesced_tender_id].relevance_class
                )
            )
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
        )
        readiness = score_contact_readiness(overlay)
        next_action = operator_next_action(overlay, urgency)

        current_ops = []
        historical_signals = []
        for t in sorted(tenders, key=lambda x: x.coalesced_tender_id):
            d = decisions_by_tender[t.coalesced_tender_id]
            code = _tender_code(t)
            row = {
                "coalesced_tender_id": t.coalesced_tender_id,
                "tender_code": code,
                "tender_title": t.title_selected,
                "lifecycle_class": t.lifecycle_class,
                "currentness_class": t.currentness_class,
                "candidate_source_kind": t.candidate_source_kind,
                "relevance_class": d.relevance_class,
                "canonical_equipment_classes": list(d.canonical_equipment_classes),
                "mercado_publico_url": build_mercado_publico_search_url(code)
                if code
                else None,
                "publication_timestamp": t.publication_timestamp_selected,
                "close_timestamp": t.close_timestamp_selected,
            }
            if is_open_lifecycle(t.lifecycle_class):
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
    open_opp_n = sum(1 for p in profiles if p["counts"]["open_tender_count"] > 0)
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

    # Auth must stay false
    if any(p["contact_authorization"] or p["outreach_authorization"] for p in profiles):
        raise InstitutionReconciliationError("authorization flags must remain false")

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
        "build_fingerprint": canonical_json_digest(
            {
                "planner_version": PLANNER_VERSION,
                "contract_version": CONTRACT_VERSION,
                "as_of_utc": as_of_utc,
                "profile_count": len(profiles),
            }
        ),
    }
    dependency_fingerprints = {
        "pr5c_semantic_digest": pr5c.semantic_digest,
        "pr5d_semantic_digest": pr5d.fingerprints.get("semantic_digest")
        or pr5d.fingerprints.get("semantic_fingerprint")
        or "",
        "pr5e_semantic_digest": pr5e.fingerprints.get("semantic_digest") or "",
    }

    walkthrough = {
        "pipeline": [
            "tender evidence",
            "procurement buyer identity",
            "existing OrigenLab account comparison",
            "existing contact comparison",
            "accumulated equipment history",
            "institution-level prospect profile",
        ],
        "separations": {
            "closed_tender": "not current opportunity",
            "closed_relevant_tender": "historical buying-intent evidence",
            "account_match": "not usable contact",
            "usable_contact": "not outreach authorization",
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
        "profiles_with_open_opportunity": open_opp_n,
        "historical_prospect_profiles": historical_prospect_n,
        "equipment_category_distribution": dict(cat_dist.most_common(50)),
        "match_review_queue_size": len(match_review),
        "contact_gap_queue_size": len(contact_gaps),
        "source_tenders": len(source_ids),
        "excluded_tenders": len(excluded),
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
        match_review_queue=match_review,
        contact_gap_queue=contact_gaps,
        walkthrough=walkthrough,
        not_persisted=True,
    )


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
        "contract_version": CONTRACT_VERSION,
        "not_persisted": True,
        "contact_authorization": False,
        "outreach_authorization": False,
        "profiles": result.profiles,
        "counts": result.counts,
        "fingerprints": result.fingerprints,
        "dependency_fingerprints": result.dependency_fingerprints,
    }
    reconciliation = {
        "ok": result.reconciliation.get("ok"),
        "reconciliation": result.reconciliation,
        "adapter_records_count": len(adapter_records or []),
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

    match_csv = dest / "institution_match_review_queue.csv"
    _write_csv(match_csv, result.match_review_queue)
    paths["institution_match_review_queue.csv"] = str(match_csv)

    gap_csv = dest / "contact_gap_queue.csv"
    _write_csv(gap_csv, result.contact_gap_queue)
    paths["contact_gap_queue.csv"] = str(gap_csv)

    walk_path = dest / "walkthrough.md"
    walk_path.write_text(_render_walkthrough(result), encoding="utf-8")
    paths["walkthrough.md"] = str(walk_path)
    return paths


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    path.write_text(buf.getvalue(), encoding="utf-8")


def _render_walkthrough(result: InstitutionProspectPlanResult) -> str:
    lines = [
        "# Institution prospect intelligence (PR5E.1)",
        "",
        f"Planner: `{result.planner_version}`",
        f"as_of_utc: `{result.as_of_utc}`",
        f"run_context: `{result.run_context}`",
        "",
        "## Pipeline",
        "",
        "```text",
        "tender evidence",
        "→ procurement buyer identity",
        "→ existing OrigenLab account comparison",
        "→ existing contact comparison",
        "→ accumulated equipment history",
        "→ institution-level prospect profile",
        "```",
        "",
        "## Separations",
        "",
        "- closed tender ≠ current opportunity",
        "- closed relevant tender = historical buying-intent evidence",
        "- account match ≠ usable contact",
        "- usable contact ≠ outreach authorization",
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
    "InstitutionProspectPlanResult",
    "build_institution_prospect_plan",
    "build_institution_prospects_from_plans",
]
