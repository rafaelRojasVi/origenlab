"""Orchestrate commercial truth audit outputs."""

from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.qa.commercial_truth_audit.analytics import (
    build_already_contacted_breakdown,
    build_batch_readiness,
    build_bounce_leakage,
    build_campaign_batch_quality,
    build_classification_conflicts,
    build_classification_distribution,
    build_labdelivery_relationships,
    build_open_thread_without_next_action,
    build_operator_review_sample,
    build_opportunity_stage_candidates,
    build_product_interest_inventory,
    build_tender_account_links,
    headline_metrics,
)
from origenlab_email_pipeline.qa.commercial_truth_audit.constants import AUDIT_VERSION
from origenlab_email_pipeline.qa.commercial_truth_audit.evidence import (
    build_prospect_evidence_rows,
    count_email_source_tiers,
)
from origenlab_email_pipeline.qa.commercial_truth_audit.inventory import (
    build_account_identity_conflicts,
    build_contact_identity_conflicts,
    build_source_inventory,
    build_source_overlap,
)
from origenlab_email_pipeline.qa.commercial_truth_audit.report import (
    DEFAULT_LINEAGE_NOTES,
    render_audit_report_md,
)


@dataclass
class CommercialTruthAuditResult:
    output_dir: Path
    summary: dict[str, Any]
    artifact_paths: dict[str, Path] = field(default_factory=dict)

    @property
    def exit_code(self) -> int:
        return 0


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        fields = fieldnames or []
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
        return
    fields = fieldnames or list(rows[0].keys())
    # Stable union of keys across rows.
    for row in rows:
        for k in row:
            if k not in fields:
                fields.append(k)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_commercial_truth_audit(
    conn: sqlite3.Connection,
    *,
    sqlite_path: Path,
    output_dir: Path,
    generated_at_utc: str | None = None,
) -> CommercialTruthAuditResult:
    """Run the full read-only commercial truth audit and write artifacts."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    generated = generated_at_utc or _utc_now()

    email_tiers = count_email_source_tiers(conn)
    prospects = build_prospect_evidence_rows(conn)
    source_inventory = build_source_inventory(conn, prospects, email_tier_counts=email_tiers)
    source_overlap = build_source_overlap(prospects)
    account_conflicts = build_account_identity_conflicts(prospects)
    contact_conflicts = build_contact_identity_conflicts(prospects)
    classification_distribution = build_classification_distribution(prospects)
    classification_conflicts = build_classification_conflicts(prospects)
    already_contacted = build_already_contacted_breakdown(prospects)
    opportunity_stages = build_opportunity_stage_candidates(prospects)
    open_threads = build_open_thread_without_next_action(prospects)
    bounce_leakage = build_bounce_leakage(prospects)
    campaign_quality = build_campaign_batch_quality(prospects)
    product_interest = build_product_interest_inventory(prospects)
    batch_readiness = build_batch_readiness(prospects)
    labdelivery = build_labdelivery_relationships(conn, prospects)
    tenders = build_tender_account_links(prospects)
    operator_sample = build_operator_review_sample(
        prospects,
        account_conflicts=account_conflicts,
    )

    metrics = headline_metrics(
        prospects,
        already_contacted_rows=already_contacted,
        open_threads=open_threads,
        conflicts=classification_conflicts,
        bounce_rows=bounce_leakage,
        campaign_rows=campaign_quality,
        labdelivery_rows=labdelivery,
        tender_rows=tenders,
        batch_readiness=batch_readiness,
    )

    summary: dict[str, Any] = {
        "audit_version": AUDIT_VERSION,
        "generated_at_utc": generated,
        "sqlite_path": str(Path(sqlite_path).resolve()),
        "output_dir": str(out.resolve()),
        "read_only": True,
        "gmail_mutations": False,
        "sqlite_mutations": False,
        "postgres_mutations": False,
        "email_source_tiers": email_tiers,
        "metrics": metrics,
        "artifacts": {},
    }

    artifacts: dict[str, list[dict[str, Any]] | dict[str, Any] | str] = {
        "summary.json": summary,
        "source_inventory.csv": source_inventory,
        "source_overlap.csv": source_overlap,
        "account_identity_conflicts.csv": account_conflicts,
        "contact_identity_conflicts.csv": contact_conflicts,
        "classification_distribution.csv": classification_distribution,
        "classification_conflicts.csv": classification_conflicts,
        "already_contacted_breakdown.csv": already_contacted,
        "opportunity_stage_candidates.csv": opportunity_stages,
        "open_thread_without_next_action.csv": open_threads,
        "bounce_leakage.csv": bounce_leakage,
        "campaign_batch_quality.csv": campaign_quality,
        "product_interest_inventory.csv": product_interest,
        "batch_readiness.csv": batch_readiness,
        "labdelivery_relationships.csv": labdelivery,
        "tender_account_links.csv": tenders,
        "operator_review_sample.csv": operator_sample,
    }

    paths: dict[str, Path] = {}
    for name, payload in artifacts.items():
        path = out / name
        if name.endswith(".json"):
            assert isinstance(payload, dict)
            _write_json(path, payload)
        else:
            assert isinstance(payload, list)
            _write_csv(path, payload)
        paths[name] = path
        summary["artifacts"][name] = name

    report_md = render_audit_report_md(summary=summary, lineage_notes=DEFAULT_LINEAGE_NOTES)
    report_path = out / "audit_report.md"
    report_path.write_text(report_md, encoding="utf-8")
    paths["audit_report.md"] = report_path
    summary["artifacts"]["audit_report.md"] = "audit_report.md"
    # Rewrite summary with complete artifact list.
    _write_json(out / "summary.json", summary)

    return CommercialTruthAuditResult(output_dir=out, summary=summary, artifact_paths=paths)
