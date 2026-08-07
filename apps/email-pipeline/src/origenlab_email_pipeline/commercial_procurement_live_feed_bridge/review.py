"""Assemble operator review rows from PR5C/D/E for live-backed tenders."""

from __future__ import annotations

import csv
import io
import json
from collections import Counter
from typing import Any, Mapping, Sequence

from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    CandidatePlanResult,
    CoalescedProcurementTender,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.models import (
    ContactResolutionPlanResult,
    ContactResolutionSummary,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.policy import (
    GATED_EXTERNAL_OR_LEAD_ACTIONS,
    tender_allows_gated_lead_or_research,
)
from origenlab_email_pipeline.commercial_procurement_live_feed_bridge.buckets import (
    classify_commercial_bucket,
    reconcile_bucket_counts,
)
from origenlab_email_pipeline.commercial_procurement_live_feed_bridge.constants import (
    LIVE_BACKED_SOURCE_KINDS,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
    ProductRelevancePlanResult,
    TenderRelevanceDecision,
)
from origenlab_email_pipeline.equipment_first_chilecompra_publish import (
    build_mercado_publico_search_url,
)


def _line_evidence(tender: CoalescedProcurementTender, decision: TenderRelevanceDecision) -> list[dict[str, Any]]:
    """Product-fit evidence without raw personal contact data."""
    spans = []
    for span in decision.matched_spans[:12]:
        spans.append(
            {
                "field_path": span.field_path,
                "rule_id": span.rule_id,
                "matched_text": span.matched_text,
            }
        )
    return [
        {
            "evidence_tier": decision.evidence_tier,
            "canonical_equipment_classes": list(decision.canonical_equipment_classes),
            "positive_reason_codes": list(decision.positive_reason_codes)[:20],
            "negative_reason_codes": list(decision.negative_reason_codes)[:20],
            "matched_spans": spans,
            "contributing_evidence_ref_ids": list(decision.contributing_evidence_ref_ids)[:20],
            "coalesced_tender_id": tender.coalesced_tender_id,
        }
    ]


def _actionability(
    *,
    summary: ContactResolutionSummary,
    tender: CoalescedProcurementTender,
    decision: TenderRelevanceDecision,
) -> dict[str, Any]:
    gated = set(GATED_EXTERNAL_OR_LEAD_ACTIONS)
    next_action = summary.next_action
    allowed, reasons = tender_allows_gated_lead_or_research(
        lifecycle_class=tender.lifecycle_class,
        relevance_class=decision.relevance_class,
        currentness_class=tender.currentness_class,
        label_status=None,
        independently_reviewed=False,
    )
    contact_auth = bool(allowed and next_action == "use_existing_contact")
    outreach_auth = False  # never authorized by this bridge
    if next_action in gated and allowed:
        actionability = "gated_but_unvalidated"  # still not an approved lead
    else:
        actionability = "blocked"
    blocker = None
    if not allowed:
        blocker = reasons[0] if reasons else "fail_closed_gate"
    elif next_action in gated:
        blocker = "relevance_unvalidated_or_contact_not_approved"
    elif summary.final_contact_status == "contact_resolution_deferred":
        blocker = f"org_or_contact_deferred:{summary.reason_code}"
    else:
        blocker = f"next_action:{next_action}"
    return {
        "actionability": actionability,
        "exact_blocker": blocker,
        "contact_authorization": contact_auth,
        "outreach_authorization": outreach_auth,
        "recommended_next_human_action": _recommend_human_action(
            bucket_hint=None,
            summary=summary,
            next_action=next_action,
        ),
    }


def _recommend_human_action(
    *,
    bucket_hint: str | None,
    summary: ContactResolutionSummary,
    next_action: str,
) -> str:
    if bucket_hint == "current_opportunity":
        return "review_product_fit_and_buyer_then_decide_manual_outreach"
    if bucket_hint == "needs_review":
        return "resolve_ambiguity_or_refresh_detail_before_acting"
    if bucket_hint == "historical_market_signal":
        return "archive_as_market_signal_only"
    if bucket_hint == "rejected":
        return "no_action_explicit_non_fit"
    if next_action == "resolve_account":
        return "resolve_buyer_account_linkage"
    if next_action == "review_contact_role":
        return "review_contact_role_without_outreach"
    return "operator_review_only"


def build_review_rows(
    *,
    pr5c: CandidatePlanResult,
    pr5d: ProductRelevancePlanResult,
    pr5e: ContactResolutionPlanResult,
) -> list[dict[str, Any]]:
    decisions = {d.coalesced_tender_id: d for d in pr5d.tender_decisions}
    orgs = {o.coalesced_tender_id: o for o in pr5e.organization_resolutions}
    summaries = {s.coalesced_tender_id: s for s in pr5e.contact_summaries}
    rows: list[dict[str, Any]] = []
    for tender in sorted(pr5c.coalesced_tenders, key=lambda t: t.coalesced_tender_id):
        if tender.candidate_source_kind not in LIVE_BACKED_SOURCE_KINDS:
            continue
        decision = decisions.get(tender.coalesced_tender_id)
        org = orgs.get(tender.coalesced_tender_id)
        summary = summaries.get(tender.coalesced_tender_id)
        if decision is None or org is None or summary is None:
            raise ValueError(
                f"live-backed tender missing PR5D/PR5E binding: {tender.coalesced_tender_id}"
            )
        bucket, bucket_reason = classify_commercial_bucket(
            candidate_source_kind=tender.candidate_source_kind,
            lifecycle_class=tender.lifecycle_class,
            currentness_class=tender.currentness_class,
            relevance_class=decision.relevance_class,
            evidence_tier=decision.evidence_tier,
        )
        act = _actionability(summary=summary, tender=tender, decision=decision)
        act["recommended_next_human_action"] = _recommend_human_action(
            bucket_hint=bucket, summary=summary, next_action=summary.next_action
        )
        code = tender.canonical_tender_key
        rows.append(
            {
                "coalesced_tender_id": tender.coalesced_tender_id,
                "relevance_decision_id": decision.decision_id,
                "tender_code": code,
                "mercado_publico_url": build_mercado_publico_search_url(code),
                "buyer_organization": tender.buyer_display_selected,
                "buyer_source_id": tender.buyer_source_id_selected,
                "tender_title": tender.title_selected,
                "line_item_evidence": _line_evidence(tender, decision),
                "acquisition_snapshot_ids": list(tender.acquisition_snapshot_ids),
                "publication_timestamp": tender.publication_timestamp_selected,
                "close_timestamp": tender.close_timestamp_selected,
                "candidate_source_kind": tender.candidate_source_kind,
                "lifecycle_class": tender.lifecycle_class,
                "currentness_class": tender.currentness_class,
                "closing_soon_bucket": tender.closing_soon_bucket,
                "relevance_class": decision.relevance_class,
                "evidence_tier": decision.evidence_tier,
                "product_resolution_status": decision.product_resolution_status,
                "relevance_reason_codes": {
                    "positive": list(decision.positive_reason_codes),
                    "negative": list(decision.negative_reason_codes),
                    "ambiguity": list(decision.ambiguity_reason_codes),
                },
                "organization_resolution_status": org.resolution_status,
                "organization_reason_code": org.reason_code,
                "contact_resolution_status": summary.final_contact_status,
                "contact_reason_code": summary.reason_code,
                "contact_next_action": summary.next_action,
                "selected_contact_present": bool(summary.selected_contact_id),
                "commercial_bucket": bucket,
                "commercial_bucket_reason": bucket_reason,
                "product_fit": decision.relevance_class
                in {
                    "exact_catalog_product",
                    "strong_equipment_class",
                    "compatible_equipment_class",
                    "laboratory_context_only",
                },
                "current_tender_opportunity": bucket == "current_opportunity",
                "actionable_lead": False,
                **act,
            }
        )
    reconcile_bucket_counts(rows)
    return rows


def review_summary_aggregate(
    *,
    rows: Sequence[Mapping[str, Any]],
    bridge_coverage: Mapping[str, Any],
    freshness: Mapping[str, Any],
    pr5c: CandidatePlanResult,
    pr5d: ProductRelevancePlanResult,
    pr5e: ContactResolutionPlanResult,
    input_paths: Mapping[str, str],
    fingerprints: Mapping[str, Any],
) -> dict[str, Any]:
    """Aggregate-only summary — no row-bearing content."""
    bucket_recon = reconcile_bucket_counts(list(rows))
    by_source = Counter(t.candidate_source_kind for t in pr5c.coalesced_tenders)
    by_life = Counter(
        r["lifecycle_class"] for r in rows
    )
    by_rel = Counter(r["relevance_class"] for r in rows)
    by_org = Counter(r["organization_resolution_status"] for r in rows)
    by_contact = Counter(r["contact_resolution_status"] for r in rows)
    return {
        "ok": True,
        "not_persisted": True,
        "planner_version": "procurement_live_feed_bridge_pr5b2_v1",
        "as_of_utc": pr5c.as_of_utc,
        "input_paths": dict(input_paths),
        "freshness": dict(freshness),
        "coverage": dict(bridge_coverage),
        "pr5c_counts": {
            "coalesced_tenders": len(pr5c.coalesced_tenders),
            "by_candidate_source_kind": dict(sorted(by_source.items())),
        },
        "live_backed_review_population": bucket_recon["live_backed_review_population"],
        "by_commercial_bucket": bucket_recon["by_bucket"],
        "by_lifecycle_class": dict(sorted(by_life.items())),
        "by_relevance_class": dict(sorted(by_rel.items())),
        "by_organization_resolution_status": dict(sorted(by_org.items())),
        "by_contact_resolution_status": dict(sorted(by_contact.items())),
        "actionable_lead_count": 0,
        "contact_authorization_count": sum(
            1 for r in rows if r.get("contact_authorization")
        ),
        "outreach_authorization_count": sum(
            1 for r in rows if r.get("outreach_authorization")
        ),
        "pr5d_fingerprints": dict(pr5d.fingerprints),
        "pr5e_fingerprints": dict(pr5e.fingerprints),
        "pr5e_dependency_fingerprints": dict(pr5e.dependency_fingerprints),
        "bridge_fingerprints": dict(fingerprints),
        "separations": {
            "product_fit": "relevance_class / product_fit flag",
            "current_tender_opportunity": "commercial_bucket == current_opportunity",
            "actionable_lead": "always false in PR5B.2",
            "contact_outreach_authorization": "fail-closed; never send authorization",
        },
        "artifact_layout": {
            "summary_is_aggregate_only": True,
            "row_bearing": [
                "operator_review_packet.json",
                "operator_review.csv",
            ],
            "walkthrough": "walkthrough.md",
        },
    }


def rows_to_csv(rows: Sequence[Mapping[str, Any]]) -> str:
    fieldnames = [
        "tender_code",
        "mercado_publico_url",
        "buyer_organization",
        "tender_title",
        "candidate_source_kind",
        "lifecycle_class",
        "currentness_class",
        "closing_soon_bucket",
        "relevance_class",
        "evidence_tier",
        "organization_resolution_status",
        "contact_resolution_status",
        "commercial_bucket",
        "commercial_bucket_reason",
        "product_fit",
        "current_tender_opportunity",
        "actionability",
        "exact_blocker",
        "contact_authorization",
        "outreach_authorization",
        "recommended_next_human_action",
        "publication_timestamp",
        "close_timestamp",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k) for k in fieldnames})
    return buf.getvalue()


def render_walkthrough_md(
    *,
    summary: Mapping[str, Any],
    sample_rows: Sequence[Mapping[str, Any]],
) -> str:
    lines = [
        "# PR5B.2 live equipment feed → PR5 review",
        "",
        f"- as_of_utc: `{summary.get('as_of_utc')}`",
        f"- live_backed_review_population: `{summary.get('live_backed_review_population')}`",
        f"- buckets: `{json.dumps(summary.get('by_commercial_bucket'), sort_keys=True)}`",
        f"- actionable_lead_count: `{summary.get('actionable_lead_count')}`",
        f"- outreach_authorization_count: `{summary.get('outreach_authorization_count')}`",
        "",
        "## Freshness / coverage",
        "",
        f"```json\n{json.dumps(summary.get('freshness'), indent=2, sort_keys=True)}\n```",
        "",
        f"```json\n{json.dumps(summary.get('coverage'), indent=2, sort_keys=True)}\n```",
        "",
        "## Sample rows (redacted contact identifiers)",
        "",
    ]
    for row in sample_rows[:8]:
        lines.append(
            f"- `{row.get('tender_code')}` bucket=`{row.get('commercial_bucket')}` "
            f"relevance=`{row.get('relevance_class')}` lifecycle=`{row.get('lifecycle_class')}` "
            f"org=`{row.get('organization_resolution_status')}` "
            f"contact=`{row.get('contact_resolution_status')}`"
        )
    lines.append("")
    lines.append(
        "Persistence, product fit, and contact discovery are **not** approval to research or send."
    )
    return "\n".join(lines) + "\n"
