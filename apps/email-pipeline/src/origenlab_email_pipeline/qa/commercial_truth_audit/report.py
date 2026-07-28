"""Human-readable audit report markdown generator."""

from __future__ import annotations

from typing import Any


def _metric_line(name: str, payload: Any) -> str:
    if isinstance(payload, dict) and "value" in payload:
        conf = payload.get("confidence") or "unknown"
        denom = payload.get("denominator")
        denom_bit = f" (denominator={denom})" if denom is not None else ""
        definition = payload.get("definition") or ""
        return (
            f"- `{name}` = **{payload.get('value')}** "
            f"[{conf}]{denom_bit}"
            + (f" — {definition}" if definition else "")
        )
    return f"- `{name}` = **{payload}**"


def render_audit_report_md(
    *,
    summary: dict[str, Any],
    lineage_notes: list[str],
) -> str:
    m = summary.get("metrics") or {}
    flat = m.get("_flat") if isinstance(m, dict) else None
    lines = [
        "# Commercial Truth Audit Report",
        "",
        "Status: generated artifact (gitignored when written under `reports/out/`).",
        "This report is **read-only evidence**. It does not change classifications, send email,",
        "or mutate SQLite / Postgres / Gmail.",
        "",
        f"- Audit version: `{summary.get('audit_version')}`",
        f"- Generated at (UTC): `{summary.get('generated_at_utc')}`",
        f"- SQLite path: `{summary.get('sqlite_path')}`",
        f"- Output dir: `{summary.get('output_dir')}`",
        "",
        "## Metric confidence legend",
        "",
        "- `observed` — directly counted from stored rows",
        "- `derived_high_confidence` — deterministic from dated/explicit evidence",
        "- `derived_medium_confidence` — derived with incomplete dates or weaker joins",
        "- `heuristic` — audit candidate inference; not production truth",
        "- `unavailable` — cannot be proven with current evidence",
        "",
        "## Headline metrics (sanitized)",
        "",
    ]
    # Prefer structured metrics (skip _flat).
    for key, payload in sorted((m or {}).items()):
        if key.startswith("_"):
            continue
        lines.append(_metric_line(key, payload))

    if flat:
        lines.extend(
            [
                "",
                "### Flat CLI snapshot",
                "",
                f"- prospects={flat.get('prospect_rows')}",
                f"- already_contacted={flat.get('already_contacted_count')}",
                f"- current_fulfillment_pct={flat.get('already_contacted_current_fulfillment_or_post_sale_pct')}",
                f"- history_only_pct={flat.get('already_contacted_customer_or_commercial_history_pct')}",
                f"- prospect_cohort_dup_rate_valid_emails={flat.get('prospect_cohort_duplicate_rate_of_valid_email_rows')}",
                f"- hidden_active={flat.get('active_cases_hidden_in_generic_buckets_count')}",
                f"- cohort_current_suppressed={flat.get('prospect_cohort_current_suppressed_count')}",
            ]
        )

    lines.extend(
        [
            "",
            "## Important qualifications",
            "",
            "- **Prospect cohort ≠ Gmail campaign.** `prospect_source_batch_quality.csv` measures",
            "  `lead_research_prospect` rows by `dataset_label`/`source_type`, not Sent-folder recipients.",
            "- **Lifetime invoice/purchase counts ≠ current fulfilment.** Those counts are historical.",
            "  Current fulfilment requires dated deal status or explicit recent logistics evidence.",
            "- **Tender context is independent** of relationship and commercial stage",
            "  (`audit_procurement_context`).",
            "- Missing emails are **not** duplicates.",
            "",
            "## Lineage (current system)",
            "",
        ]
    )
    for note in lineage_notes:
        lines.append(f"- {note}")

    limitations = summary.get("limitations") or []
    if limitations:
        lines.extend(["", "## Remaining limitations", ""])
        for lim in limitations:
            lines.append(f"- {lim}")

    lines.extend(
        [
            "",
            "## Output artifacts",
            "",
            "See CSV/JSON siblings in this directory. Emails in CSV outputs are redacted.",
            "`sent_campaign_quality.csv` is the actual Sent-folder report when columns permit.",
            "",
        ]
    )
    return "\n".join(lines)


DEFAULT_LINEAGE_NOTES = [
    "DeepSearch / research CSVs → `lead_research_builder` → `lead_research_prospect` (+ evidence/block_reason).",
    "Gmail IMAP / mbox ingest → `emails` (canonical OrigenLab vs legacy Labdelivery tiers).",
    "Safety sidecars: `contact_email_suppression`, `contact_domain_suppression`, `outreach_contact_state`.",
    "Operational overlay (`lead_research_operational_overlay`) adjusts classification/status for mirror/UI.",
    "Commercial action buckets (`commercial_action_buckets`) map overlay rows → dashboard queues.",
    "Business mart: `contact_master` / `organization_master` (historical quote/invoice/purchase counts).",
    "Commercial intel v1: signal facts/rollups + opportunity facts (rebuildable).",
    "Commercial deals (when present) provide dated stage evidence when timestamps exist.",
    "Tenders: Chilecompra/equipment-first queues + `public_tender_review` → audit_procurement_context only.",
    "Postgres mirror loaders → `apps/api` read-only routes → `apps/dashboard` Prospectos filters/exports.",
]
