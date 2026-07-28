"""Human-readable audit report markdown generator."""

from __future__ import annotations

from typing import Any


def render_audit_report_md(
    *,
    summary: dict[str, Any],
    lineage_notes: list[str],
) -> str:
    m = summary.get("metrics") or {}
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
        f"- Prospect rows analyzed: **{m.get('prospect_rows', 0)}**",
        "",
        "## Headline metrics (sanitized)",
        "",
        f"- `already_contacted` count: {m.get('already_contacted_count', 0)}",
        f"- Campaign-recipient-only share of `already_contacted`: {m.get('already_contacted_campaign_recipient_only_pct', 0)}%",
        f"- Active inquiry share: {m.get('already_contacted_active_inquiry_pct', 0)}%",
        f"- Quotation-related share: {m.get('already_contacted_quotation_related_pct', 0)}%",
        f"- Purchase-pending share: {m.get('already_contacted_purchase_pending_pct', 0)}%",
        f"- Existing-customer share: {m.get('already_contacted_existing_customer_pct', 0)}%",
        f"- Fulfilment/post-sale share: {m.get('already_contacted_fulfillment_or_post_sale_pct', 0)}%",
        f"- Dormant share: {m.get('already_contacted_dormant_pct', 0)}%",
        f"- Undetermined share: {m.get('already_contacted_undetermined_pct', 0)}%",
        f"- Open threads without useful next action: {m.get('open_thread_without_next_action_count', 0)}",
        f"- Sent-only treated like opportunities: {m.get('sent_only_treated_as_opportunity_count', 0)}",
        f"- Active cases hidden in generic buckets: {m.get('active_cases_hidden_in_generic_buckets_count', 0)}",
        f"- Hard-bounce leakage rate (campaign batches): {m.get('hard_bounce_leakage_rate', 0)}%",
        f"- Duplicate-recipient rate: {m.get('duplicate_recipient_rate', 0)}%",
        f"- Suppressed-recipient leakage (must be 0): {m.get('suppressed_recipient_leakage', 0)}",
        f"- Labdelivery recoverable contacts / orgs: {m.get('labdelivery_unique_contacts', 0)} / {m.get('labdelivery_unique_orgs', 0)}",
        f"- Tender rows with account-link attempt: {m.get('tender_rows_linked', 0)}",
        f"- Product categories with any evidence: {m.get('batch_readiness_categories_with_evidence', 0)}",
        f"- Product categories batch-ready: {m.get('batch_ready_categories', 0)}",
        "",
        "## Lineage (current system)",
        "",
    ]
    for note in lineage_notes:
        lines.append(f"- {note}")
    lines.extend(
        [
            "",
            "## Output artifacts",
            "",
            "See CSV/JSON siblings in this directory. Emails in CSV outputs are redacted.",
            "The operator review sample is stratified and redacted; local unredacted review",
            "must stay under gitignored `reports/out/`.",
            "",
            "## Interpretation guardrails",
            "",
            "- Audit dimensions (`audit_*`) are **candidates**, not production schema.",
            "- Do not gate sends on `lead_research_prospect.classification` alone.",
            "- Do not invent product interest; `unknown` means insufficient evidence.",
            "- Consumer email domains must not be joined into accounts by domain alone.",
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
    "Business mart: `contact_master` / `organization_master` (quote/invoice/purchase counts, equipment tags).",
    "Commercial intel v1: signal facts/rollups + opportunity facts (rebuildable).",
    "Commercial deals / purchase events (when present) provide stronger stage evidence.",
    "Tenders: Chilecompra/equipment-first queues + `public_tender_review` prospects (CSV-first; not auto-outreach).",
    "Postgres mirror loaders → `apps/api` read-only routes → `apps/dashboard` Prospectos filters/exports.",
]
