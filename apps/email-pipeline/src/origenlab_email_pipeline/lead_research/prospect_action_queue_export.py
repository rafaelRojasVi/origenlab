"""Export Prospectos commercial action queues from the lead research mirror."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from origenlab_email_pipeline.lead_research.commercial_action_buckets import (
    BUCKET_ALREADY_CONTACTED,
    BUCKET_NEEDS_EMAIL_ENRICHMENT,
    BUCKET_READY_TO_CONTACT,
    BUCKET_REVIEW_HISTORY,
    BUCKET_TENDER_OPPORTUNITY,
    enrich_prospect_for_dashboard,
    is_followup_eligible,
)
from origenlab_email_pipeline.lead_research.lead_research_mirror_read_model import (
    load_lead_research_mirror_payload,
)
from origenlab_email_pipeline.lead_research.lead_research_mirror_safety import (
    assert_mirror_row_safe,
)

DEFAULT_OUTPUT_DIR = Path("reports/out/active/current/prospect_action_queues")

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

SUMMARY_FILENAME = "prospect_action_queues_summary.json"


@dataclass(frozen=True)
class QueueExportSpec:
    filename: str
    predicate: Callable[[dict[str, Any]], bool]


QUEUE_EXPORT_SPECS: tuple[QueueExportSpec, ...] = (
    QueueExportSpec(
        "ready_to_contact.csv",
        lambda p: p.get("commercial_action_bucket") == BUCKET_READY_TO_CONTACT,
    ),
    QueueExportSpec(
        "needs_email_enrichment.csv",
        lambda p: p.get("commercial_action_bucket") == BUCKET_NEEDS_EMAIL_ENRICHMENT,
    ),
    QueueExportSpec(
        "tender_opportunities.csv",
        lambda p: p.get("commercial_action_bucket") == BUCKET_TENDER_OPPORTUNITY,
    ),
    QueueExportSpec(
        "review_history.csv",
        lambda p: p.get("commercial_action_bucket") == BUCKET_REVIEW_HISTORY,
    ),
    QueueExportSpec(
        "already_contacted_followup_review.csv",
        lambda p: is_followup_eligible(p),
    ),
)


def _scalar(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def prospect_to_export_row(prospect: dict[str, Any]) -> dict[str, str]:
    enriched = enrich_prospect_for_dashboard(prospect)
    row = {field: _scalar(enriched.get(field)) for field in EXPORT_FIELDNAMES}
    assert_mirror_row_safe(row, table="prospect")
    return row


def prepare_enriched_prospects(prospects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched = [enrich_prospect_for_dashboard(dict(p)) for p in prospects]
    enriched.sort(
        key=lambda p: (-int(p.get("final_score") or 0), str(p.get("organization_name") or "")),
    )
    return enriched


def partition_prospect_queues(
    prospects: list[dict[str, Any]],
) -> dict[str, list[dict[str, str]]]:
    enriched = prepare_enriched_prospects(prospects)
    partitioned: dict[str, list[dict[str, str]]] = {}
    for spec in QUEUE_EXPORT_SPECS:
        rows = [prospect_to_export_row(p) for p in enriched if spec.predicate(p)]
        partitioned[spec.filename] = rows
    return partitioned


def write_queue_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(EXPORT_FIELDNAMES))
        writer.writeheader()
        writer.writerows(rows)


def build_summary(
    *,
    total_active_prospects: int,
    exports: dict[str, list[dict[str, str]]],
    output_dir: Path,
) -> dict[str, Any]:
    return {
        "read_only": True,
        "data_source": "sqlite_lead_research_mirror",
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "output_dir": str(output_dir),
        "total_active_prospects": total_active_prospects,
        "exports": {filename: len(rows) for filename, rows in exports.items()},
    }


def export_prospect_action_queues_from_prospects(
    prospects: list[dict[str, Any]],
    *,
    output_dir: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    partitioned = partition_prospect_queues(prospects)
    summary = build_summary(
        total_active_prospects=len(prospects),
        exports=partitioned,
        output_dir=output_dir,
    )
    if dry_run:
        return summary

    for filename, rows in partitioned.items():
        write_queue_csv(output_dir / filename, rows)
    summary_path = output_dir / SUMMARY_FILENAME
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return summary


def export_prospect_action_queues(
    conn: Any,
    *,
    output_dir: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    payload = load_lead_research_mirror_payload(conn)
    return export_prospect_action_queues_from_prospects(
        payload["prospects"],
        output_dir=output_dir,
        dry_run=dry_run,
    )
