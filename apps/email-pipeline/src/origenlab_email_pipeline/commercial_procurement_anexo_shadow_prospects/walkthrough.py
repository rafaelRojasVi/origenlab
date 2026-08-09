"""Shareable markdown walkthrough for ANEXO-P1 shadow prospect packets."""

from __future__ import annotations

from typing import Any

from .models import ShadowProspectComparisonResult

_FOCUS = (
    "4034-16-LE26",
    "1057510-40-LP26",
    "1057510-53-LP26",
    "1057510-30-LR26",
    "699866-86-LE26",
    "745712-14-LE26",
    "1267885-18-LE26",
    "1057374-17-LP26",
    "986278-12-LE26",
)


def render_walkthrough(
    result: ShadowProspectComparisonResult, summary: dict[str, Any]
) -> str:
    by = {d.tender_id: d for d in result.tender_deltas}
    would_enter = summary.get("would_enter_current_opportunity_tender_ids") or []
    lines: list[str] = [
        "# ANEXO-P1 shadow prospect walkthrough",
        "",
        "SHADOW ONLY — measurement artifacts. Not production queues.",
        "",
        f"- corpus_digest: `{result.corpus_digest}`",
        f"- semantic_digest: `{result.semantic_digest}`",
        "",
        "## Metric grain (do not conflate)",
        "",
        "- `equipment_purchase_signal_rows` = category-scoped tender rows with "
        "`commercial_signal_type=equipment_purchase_signal` "
        "(not unique tenders; not queue membership).",
        f"- current equipment-signal rows: "
        f"{summary.get('current_equipment_purchase_signal_rows')}",
        f"- shadow equipment-signal rows: "
        f"{summary.get('shadow_equipment_purchase_signal_rows')}",
        f"- current unique equipment-signal tenders: "
        f"{summary.get('current_equipment_purchase_tender_count')}",
        f"- shadow unique equipment-signal tenders: "
        f"{summary.get('shadow_equipment_purchase_tender_count')}",
        f"- current unique equipment-signal institutions: "
        f"{summary.get('current_equipment_purchase_institution_count')}",
        f"- shadow unique equipment-signal institutions: "
        f"{summary.get('shadow_equipment_purchase_institution_count')}",
        f"- current opportunity queue rows: "
        f"{summary.get('current_current_opportunity_queue_count')}",
        f"- shadow opportunity queue rows: "
        f"{summary.get('shadow_current_opportunity_queue_count')}",
        f"- would-enter current opportunity tenders: "
        f"{', '.join(f'`{t}`' for t in would_enter) or '(none)'}",
        f"- queue_entries (row deltas): {summary.get('queue_entries')}",
        f"- queue_exits (row deltas): {summary.get('queue_exits')}",
        f"- partial_capability change-class count: "
        f"{summary.get('partial_capability_opportunities')}",
        f"- out_of_scope_equipment change-class count: "
        f"{summary.get('out_of_scope_equipment_detections')}",
        f"- suppressed_service_or_maintenance_cases: "
        f"{summary.get('suppressed_service_or_maintenance_cases')}",
        f"- suppressed_method_exam_cases: "
        f"{summary.get('suppressed_method_exam_cases')}",
        f"- institution_profiles_changed: "
        f"{summary.get('institution_profiles_changed')}",
        "",
        "## Safety",
        "",
        "- contact_authorization: false",
        "- outreach_authorization: false",
        "- production_queue_mutated: false",
        "- persisted: false",
        "- pr5f_started: false",
        "- annex_production_integration: false",
        "",
        "## Focus cases",
        "",
    ]
    for tid in _FOCUS:
        d = by.get(tid)
        if d is None:
            lines.append(f"### `{tid}`")
            lines.append("")
            lines.append("_not in this corpus_")
            lines.append("")
            continue
        caps = ", ".join(
            f"{c.equipment_class}={c.capability}" for c in d.claim_level_capability
        ) or "(none)"
        lines.extend(
            [
                f"### `{tid}` — {d.buyer_organization or 'unknown buyer'}",
                "",
                f"- change_class: `{d.change_class}`",
                f"- commercial_intent: `{d.commercial_intent_class}`",
                f"- current classes: {', '.join(d.current_equipment_classes) or '(none)'}",
                f"- shadow classes: {', '.join(d.shadow_equipment_classes) or '(none)'}",
                f"- capability: {caps}",
                f"- coverage: `{d.coverage_status}`",
                f"- queue_delta: {', '.join(d.queue_delta)}",
                f"- human_review_required: {d.human_review_required}",
                "",
            ]
        )
        if d.annex_provenance_refs:
            lines.append("Provenance:")
            for ref in d.annex_provenance_refs[:8]:
                lines.append(
                    f"- claim `{ref['claim_id']}` / {ref['matched_category']} @ "
                    f"{ref.get('locator_display')}"
                )
            lines.append("")
    return "\n".join(lines) + "\n"
