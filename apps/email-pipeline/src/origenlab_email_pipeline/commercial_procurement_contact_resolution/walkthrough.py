"""Redacted PR5E walkthrough cases A–E."""

from __future__ import annotations

from typing import Any, Iterable

from origenlab_email_pipeline.commercial_procurement_contact_resolution.constants import (
    CONTACT_RESOLUTION_DEFERRED,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.models import (
    ContactCandidate,
    ContactResolutionSummary,
    OrganizationResolution,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.redaction import (
    redact_id,
)


def _pick(
    summaries: Iterable[ContactResolutionSummary],
    *,
    status: str,
) -> ContactResolutionSummary | None:
    for s in summaries:
        if s.final_contact_status == status:
            return s
    return None


def build_contact_resolution_walkthrough(
    *,
    organizations: tuple[OrganizationResolution, ...],
    summaries: tuple[ContactResolutionSummary, ...],
    candidates: tuple[ContactCandidate, ...],
) -> dict[str, Any]:
    org_by_tender = {o.coalesced_tender_id: o for o in organizations}
    cands_by_summary = {}
    for c in candidates:
        cands_by_summary.setdefault(c.contact_resolution_id, []).append(c)

    def _case(
        letter: str,
        title: str,
        summary: ContactResolutionSummary | None,
        *,
        unavailable_reason: str | None = None,
        synthetic: bool = False,
    ) -> dict[str, Any]:
        if summary is None:
            return {
                "case": letter,
                "title": title,
                "availability": "unavailable",
                "reason": unavailable_reason or "no_matching_production_row",
                "synthetic": False,
            }
        org = org_by_tender.get(summary.coalesced_tender_id)
        cands = cands_by_summary.get(summary.contact_resolution_id, [])
        return {
            "case": letter,
            "title": title,
            "availability": "production_derived" if not synthetic else "synthetic",
            "synthetic": synthetic,
            "not_persisted": True,
            "source_evidence_redacted": {
                "coalesced_tender_token": redact_id(
                    "tender", summary.coalesced_tender_id
                ),
                "relevance_decision_token": redact_id(
                    "trd", summary.relevance_decision_id
                ),
                "relevance_class": summary.relevance_class_echo,
                "lifecycle_class": summary.lifecycle_class_echo,
            },
            "organization": {
                "resolution_status": org.resolution_status if org else None,
                "resolution_source": org.resolution_source if org else None,
                "link_route": org.link_route if org else None,
                "reason_code": org.reason_code if org else None,
                "account_token": redact_id("account", org.account_id) if org else None,
                "buyer_field_sufficiency": org.buyer_field_sufficiency if org else None,
            },
            "search_stages_completed": list(summary.search_stages_completed),
            "considered_contacts": [
                {
                    "rank": c.rank,
                    "ranking_tier": c.ranking_tier,
                    "role_suitability": c.role_suitability,
                    "verification_status": c.verification_status,
                    "suppression_result": c.suppression_result,
                    "outreach_state_result": c.outreach_state_result,
                    "safety_blocked": c.safety_blocked,
                    "safety_unknown": c.safety_unknown,
                    "selectable": c.selectable,
                    "contact_token": redact_id("contact", c.contact_id),
                    "ranking_reason_codes": list(c.ranking_reason_codes),
                }
                for c in cands[:10]
            ],
            "final_contact_status": summary.final_contact_status,
            "next_action": summary.next_action,
            "reason_code": summary.reason_code,
            "selected_contact_token": redact_id(
                "contact", summary.selected_contact_id
            ),
            "planned_object_not_persisted": True,
        }

    case_a = _case(
        "A",
        "Unresolved account → contact resolution deferred",
        _pick(summaries, status=CONTACT_RESOLUTION_DEFERRED),
    )
    case_b = _case(
        "B",
        "Uniquely resolved account with verified suitable contact",
        _pick(summaries, status="existing_verified_contact"),
        unavailable_reason=(
            "no production row met explicit verification + suitable role + clear safety"
        ),
    )
    case_c = _case(
        "C",
        "Resolved account with contacts requiring role review",
        _pick(summaries, status="existing_contact_needs_role_review"),
    )
    case_d = _case(
        "D",
        "Resolved account with no suitable internal contact",
        _pick(summaries, status="no_contact_found")
        or _pick(summaries, status="contact_research_required"),
    )
    case_e = _case(
        "E",
        "Ambiguous, conflicting, suppressed, or blocked contact path",
        _pick(summaries, status="ambiguous_contact")
        or _pick(summaries, status="contact_blocked"),
    )

    cases = [case_a, case_b, case_c, case_d, case_e]
    lines = [
        "# PR5E contact-resolution walkthrough",
        "",
        "All identifiers below are redacted tokens. Plan objects are not persisted.",
        "",
    ]
    for c in cases:
        lines.append(f"## Case {c['case']} — {c['title']}")
        lines.append(f"- availability: `{c['availability']}`")
        if c.get("reason"):
            lines.append(f"- reason: {c['reason']}")
        if c.get("final_contact_status"):
            lines.append(f"- final_contact_status: `{c['final_contact_status']}`")
            lines.append(f"- next_action: `{c['next_action']}`")
        lines.append("")

    return {
        "cases": cases,
        "markdown": "\n".join(lines),
        "not_persisted": True,
    }
