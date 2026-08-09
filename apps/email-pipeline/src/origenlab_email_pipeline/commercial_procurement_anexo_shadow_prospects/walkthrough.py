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
    lines: list[str] = [
        "# ANEXO-P1 shadow prospect walkthrough",
        "",
        "SHADOW ONLY — measurement artifacts. Not production queues.",
        "",
        f"- corpus_digest: `{result.corpus_digest}`",
        f"- semantic_digest: `{result.semantic_digest}`",
        f"- current_prospect_count: {summary.get('current_prospect_count')}",
        f"- shadow_prospect_count: {summary.get('shadow_prospect_count')}",
        f"- new_in_scope: {summary.get('new_in_scope_opportunities')}",
        f"- partial_capability: {summary.get('partial_capability_opportunities')}",
        f"- out_of_scope_equipment: {summary.get('out_of_scope_equipment_detections')}",
        f"- queue_entries: {summary.get('queue_entries')}",
        f"- queue_exits: {summary.get('queue_exits')}",
        f"- institution_profiles_changed: {summary.get('institution_profiles_changed')}",
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
