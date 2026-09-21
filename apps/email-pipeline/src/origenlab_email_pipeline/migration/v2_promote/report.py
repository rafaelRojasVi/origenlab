"""The PII-safe aggregate report.

Counts, never values. No address, no person name and no organization name reaches this
report, a console line or a log: promotion operates on real contact data, and an aggregate
is the only shape of it that is safe to print, paste into a PR or attach to an issue.

The review sections are the point of the report. A promotion that silently created ten
thousand rows would be a worse outcome than one that created none, so the report leads with
what was *not* decided and who has to decide it.
"""

from __future__ import annotations

from typing import Any

from origenlab_email_pipeline.migration.v2_promote.apply import PromotionResult
from origenlab_email_pipeline.migration.v2_promote.plan import PromotionPlan

#: The invariants every run asserts and every report restates, so that a reader who sees
#: only the report still learns what promotion refuses to do.
INVARIANTS: tuple[str, ...] = (
    "no crm.person is created — the evidence records no display name for anybody",
    "no contact point is attached to an organization — a domain is never an identity key",
    "no organization and no person is ever merged automatically",
    "no consent, permission or subscription fact is created",
    "nothing is sent, and no campaign or sender state is touched",
)


def build_report(
    plan: PromotionPlan,
    *,
    mode: str,
    target: str | None = None,
    applied: PromotionResult | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "mode": mode,
        "target": target,
        "invariants": list(INVARIANTS),
        "planned": plan.to_report(),
        "review_queue": _review_queue(plan),
    }
    if applied is not None:
        report["applied"] = applied.to_report()
    return report


def _review_queue(plan: PromotionPlan) -> dict[str, Any]:
    """What a human still has to decide, grouped by why."""
    by_note: dict[str, int] = {}
    for review in plan.reviews:
        # The note's leading clause is its category; the rest carries the specifics.
        category = review.note.split(";")[0].split(":")[0].strip()
        by_note[category] = by_note.get(category, 0) + 1
    return {
        "ambiguous_assertions": len(plan.reviews),
        "ambiguous_by_reason": dict(sorted(by_note.items())),
        "machine_proposed_contact_points": len(plan.contact_points),
        "machine_proposed_organizations": len(plan.organizations),
        "personal_shaped_addresses_without_a_person": plan.counts.get(
            "contact_points_personal_shaped_no_person_created", 0
        ),
    }


def render_console(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"mode: {report['mode']}")
    lines.append(f"target: {report.get('target') or '(none)'}")

    lines.append("")
    lines.append("invariants held by every run:")
    for item in report["invariants"]:
        lines.append(f"  - {item}")

    planned = report["planned"]
    lines.append("")
    lines.append("planned:")
    lines.append(f"  crm.contact_point                {planned['contact_points']:>8}")
    lines.append(f"  crm.organization                 {planned['organizations']:>8}")
    lines.append(f"  assertions routed to review      {planned['reviews']:>8}")

    lines.append("")
    lines.append("counts:")
    for key, value in planned["counts"].items():
        lines.append(f"  {key:<46} {value:>8}")

    queue = report["review_queue"]
    lines.append("")
    lines.append("review queue:")
    lines.append(f"  ambiguous assertions             {queue['ambiguous_assertions']:>8}")
    for reason, count in queue["ambiguous_by_reason"].items():
        lines.append(f"    {reason[:60]:<60} {count:>6}")
    lines.append(
        f"  machine-proposed contact points  {queue['machine_proposed_contact_points']:>8}"
    )
    lines.append(
        f"  machine-proposed organizations   {queue['machine_proposed_organizations']:>8}"
    )
    lines.append(
        "  personal-shaped addresses with no person created "
        f"{queue['personal_shaped_addresses_without_a_person']:>8}"
    )

    applied = report.get("applied")
    if applied is not None:
        lines.append("")
        lines.append("applied:")
        for key in (
            "organizations_created",
            "organizations_already_present",
            "contact_points_created",
            "contact_points_already_present",
            "assertions_promoted",
            "assertions_marked_ambiguous",
            "assertions_already_resolved",
            "events_written",
            "persons_created",
            "crm_person_rows_after",
        ):
            lines.append(f"  {key:<46} {applied[key]:>8}")
        for note in applied["notes"]:
            lines.append(f"  note: {note}")

    return "\n".join(lines)
