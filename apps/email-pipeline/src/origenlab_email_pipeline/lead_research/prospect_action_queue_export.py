"""CSV export helpers for Prospectos commercial action queues (read-only mirror)."""

from __future__ import annotations

import csv
import io
from typing import Any, Final

from origenlab_email_pipeline.lead_research.commercial_action_buckets import (
    BUCKET_ALREADY_CONTACTED,
    BUCKET_NEEDS_EMAIL_ENRICHMENT,
    BUCKET_READY_TO_CONTACT,
    BUCKET_REVIEW_HISTORY,
    BUCKET_TENDER_OPPORTUNITY,
    enrich_prospect_for_dashboard,
    is_followup_eligible,
)
from origenlab_email_pipeline.lead_research.lead_research_mirror_safety import (
    assert_mirror_row_safe,
)

EXPORT_FIELDNAMES: tuple[str, ...] = (
    "organization_name",
    "contact_name",
    "email",
    "domain",
    "sector",
    "region",
    "final_score",
    "commercial_action_bucket",
    "operational_next_action",
    "gmail_sent_count",
    "gmail_received_count",
    "gmail_last_contacted_at",
    "gmail_latest_subject_safe",
    "evidence_url",
    "product_angle",
    "recommended_next_action",
)

EXPORT_QUEUE_ALL_VISIBLE: Final = "all_visible"
EXPORT_QUEUE_READY_TO_CONTACT: Final = "ready_to_contact"
EXPORT_QUEUE_NEEDS_EMAIL_ENRICHMENT: Final = "needs_email_enrichment"
EXPORT_QUEUE_TENDER_OPPORTUNITY: Final = "tender_opportunity"
EXPORT_QUEUE_REVIEW_HISTORY: Final = "review_history"
EXPORT_QUEUE_FOLLOWUP_REVIEW: Final = "already_contacted_followup_review"

EXPORT_QUEUES: frozenset[str] = frozenset(
    {
        EXPORT_QUEUE_ALL_VISIBLE,
        EXPORT_QUEUE_READY_TO_CONTACT,
        EXPORT_QUEUE_NEEDS_EMAIL_ENRICHMENT,
        EXPORT_QUEUE_TENDER_OPPORTUNITY,
        EXPORT_QUEUE_REVIEW_HISTORY,
        EXPORT_QUEUE_FOLLOWUP_REVIEW,
    }
)

EXPORT_QUEUE_FILENAMES: dict[str, str] = {
    EXPORT_QUEUE_ALL_VISIBLE: "prospectos-filtered-export.csv",
    EXPORT_QUEUE_READY_TO_CONTACT: "prospectos-ready-to-contact.csv",
    EXPORT_QUEUE_NEEDS_EMAIL_ENRICHMENT: "prospectos-needs-email-enrichment.csv",
    EXPORT_QUEUE_TENDER_OPPORTUNITY: "prospectos-tender-opportunities.csv",
    EXPORT_QUEUE_REVIEW_HISTORY: "prospectos-review-history.csv",
    EXPORT_QUEUE_FOLLOWUP_REVIEW: "prospectos-followup-review.csv",
}

CSV_UTF8_BOM: Final = "\ufeff"


_CSV_FORMULA_PREFIXES: frozenset[str] = frozenset({"=", "+", "-", "@", "\t", "\r"})


def sanitize_csv_cell(value: object) -> str:
    """Escape spreadsheet formula injection triggers for operator CSV exports."""
    if value is None:
        return ""
    text = str(value)
    if not text:
        return ""
    if text[0] in _CSV_FORMULA_PREFIXES:
        return f"'{text}"
    return text


def prospect_to_export_row(prospect: dict[str, Any]) -> dict[str, str]:
    enriched = enrich_prospect_for_dashboard(prospect)
    row = {field: sanitize_csv_cell(enriched.get(field)) for field in EXPORT_FIELDNAMES}
    assert_mirror_row_safe(row, table="prospect")
    return row


def matches_export_queue(prospect: dict[str, Any], export_queue: str) -> bool:
    queue = (export_queue or EXPORT_QUEUE_ALL_VISIBLE).strip()
    if queue == EXPORT_QUEUE_ALL_VISIBLE:
        return True
    if queue == EXPORT_QUEUE_READY_TO_CONTACT:
        return prospect.get("commercial_action_bucket") == BUCKET_READY_TO_CONTACT
    if queue == EXPORT_QUEUE_NEEDS_EMAIL_ENRICHMENT:
        return prospect.get("commercial_action_bucket") == BUCKET_NEEDS_EMAIL_ENRICHMENT
    if queue == EXPORT_QUEUE_TENDER_OPPORTUNITY:
        return prospect.get("commercial_action_bucket") == BUCKET_TENDER_OPPORTUNITY
    if queue == EXPORT_QUEUE_REVIEW_HISTORY:
        return prospect.get("commercial_action_bucket") == BUCKET_REVIEW_HISTORY
    if queue == EXPORT_QUEUE_FOLLOWUP_REVIEW:
        return is_followup_eligible(prospect)
    return False


def filter_prospects_for_export(
    prospects: list[dict[str, Any]],
    *,
    export_queue: str = EXPORT_QUEUE_ALL_VISIBLE,
    commercial_action_bucket: str | None = None,
) -> list[dict[str, str]]:
    enriched = [enrich_prospect_for_dashboard(dict(p)) for p in prospects]
    enriched.sort(
        key=lambda p: (-int(p.get("final_score") or 0), str(p.get("organization_name") or "")),
    )
    bucket_filter = (commercial_action_bucket or "").strip() or None
    rows: list[dict[str, str]] = []
    for prospect in enriched:
        if bucket_filter and prospect.get("commercial_action_bucket") != bucket_filter:
            continue
        if not matches_export_queue(prospect, export_queue):
            continue
        rows.append(prospect_to_export_row(prospect))
    return rows


def render_prospects_csv(rows: list[dict[str, str]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(EXPORT_FIELDNAMES))
    writer.writeheader()
    writer.writerows(rows)
    return f"{CSV_UTF8_BOM}{buffer.getvalue()}"


def export_filename_for_queue(export_queue: str) -> str:
    queue = (export_queue or EXPORT_QUEUE_ALL_VISIBLE).strip()
    return EXPORT_QUEUE_FILENAMES.get(queue, EXPORT_QUEUE_FILENAMES[EXPORT_QUEUE_ALL_VISIBLE])
