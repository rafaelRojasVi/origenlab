"""Orchestrate detail-cache bridge → PR5C → PR5D → PR5E → operator review."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.output_safety import (
    write_atomically,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.planner import (
    build_candidate_plan,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.planner import (
    build_contact_resolution_plan,
)
from origenlab_email_pipeline.commercial_procurement_live_feed_bridge.adapter import (
    BridgeAdapterResult,
    adapt_detail_cache_directory,
)
from origenlab_email_pipeline.commercial_procurement_live_feed_bridge.buckets import (
    bucket_policy_document,
)
from origenlab_email_pipeline.commercial_procurement_live_feed_bridge.constants import (
    BRIDGE_CONTRACT_VERSION,
    BRIDGE_PLANNER_VERSION,
    LIVE_FEED_MAX_AGE_HOURS,
)
from origenlab_email_pipeline.commercial_procurement_live_feed_bridge.freshness import (
    LiveFeedFreshnessError,
    evaluate_feed_freshness,
    load_json_object,
    resolve_acquired_at_utc,
)
from origenlab_email_pipeline.commercial_procurement_live_feed_bridge.review import (
    build_review_rows,
    render_walkthrough_md,
    review_summary_aggregate,
    rows_to_csv,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.planner import (
    build_product_relevance_plan,
)


def _email_pipeline_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_live_feed_review(
    *,
    sqlite_path: Path,
    detail_cache_dir: Path,
    as_of_utc: str,
    out_dir: Path,
    run_context: str,
    freshness_threshold_hours: int,
    equipment_manifest_path: Path | None = None,
    refresh_state_path: Path | None = None,
    allow_stale_feed: bool = False,
    max_feed_age_hours: int = LIVE_FEED_MAX_AGE_HOURS,
    labeling_queue_size: int = 200,
) -> dict[str, Any]:
    """Build and atomically publish the live-feed operator review bundle."""
    manifest = (
        load_json_object(equipment_manifest_path)
        if equipment_manifest_path is not None
        else None
    )
    refresh_state = (
        load_json_object(refresh_state_path) if refresh_state_path is not None else None
    )
    freshness = evaluate_feed_freshness(
        as_of_utc=as_of_utc,
        refresh_state=refresh_state,
        manifest=manifest,
        max_age_hours=max_feed_age_hours,
        allow_stale=allow_stale_feed,
    )
    acquired_at = resolve_acquired_at_utc(
        manifest=manifest, refresh_state=refresh_state
    )
    materialized_at = _utcnow()

    root = _email_pipeline_root()

    def _writer(dest: Path) -> dict[str, str]:
        staging = dest / "_bridge_snapshots"
        adapter: BridgeAdapterResult = adapt_detail_cache_directory(
            detail_cache_dir,
            staging_dir=staging,
            acquired_at_utc=acquired_at,
            materialized_at_utc=materialized_at,
            manifest_path=equipment_manifest_path,
            refresh_state_path=refresh_state_path,
        )
        if not adapter.snapshot_paths:
            raise ValueError(
                "no accepted acquisition snapshots from detail cache "
                f"(files={adapter.coverage.get('detail_cache_file_count')})"
            )

        pr5c = build_candidate_plan(
            sqlite_path=sqlite_path,
            acquisition_snapshot_paths=list(adapter.snapshot_paths),
            as_of_utc=as_of_utc,
            freshness_threshold_hours=freshness_threshold_hours,
            run_context=run_context,
        )
        pr5d = build_product_relevance_plan(
            sqlite_path=sqlite_path,
            acquisition_snapshot_paths=list(adapter.snapshot_paths),
            as_of_utc=as_of_utc,
            freshness_threshold_hours=freshness_threshold_hours,
            run_context=run_context,
            labeling_queue_size=labeling_queue_size,
            pr5c_plan=pr5c,
        )
        pr5e = build_contact_resolution_plan(
            sqlite_path=sqlite_path,
            acquisition_snapshot_paths=list(adapter.snapshot_paths),
            as_of_utc=as_of_utc,
            freshness_threshold_hours=freshness_threshold_hours,
            run_context=run_context,
            labeling_queue_size=labeling_queue_size,
            pr5d_plan=pr5d,
            pr5c_plan=pr5c,
        )
        rows = build_review_rows(pr5c=pr5c, pr5d=pr5d, pr5e=pr5e)
        live_kinds = {
            t.candidate_source_kind: 0 for t in pr5c.coalesced_tenders
        }
        for t in pr5c.coalesced_tenders:
            live_kinds[t.candidate_source_kind] = (
                live_kinds.get(t.candidate_source_kind, 0) + 1
            )
        live_backed = sum(
            1
            for t in pr5c.coalesced_tenders
            if t.candidate_source_kind in {"live_snapshot", "both"}
        )
        if live_backed != len(rows):
            raise ValueError(
                f"live-backed PR5C count {live_backed} != review rows {len(rows)}"
            )

        bridge_fp = {
            "bridge_planner_version": BRIDGE_PLANNER_VERSION,
            "bridge_contract_version": BRIDGE_CONTRACT_VERSION,
            "adapter_records_digest": canonical_json_digest(
                [r.__dict__ for r in adapter.records]
            ),
            "review_rows_digest": canonical_json_digest(
                [
                    {
                        "coalesced_tender_id": r["coalesced_tender_id"],
                        "commercial_bucket": r["commercial_bucket"],
                        "relevance_class": r["relevance_class"],
                        "lifecycle_class": r["lifecycle_class"],
                    }
                    for r in rows
                ]
            ),
            "pr5c_semantic_digest": pr5c.semantic_digest,
            "pr5d_semantic_digest": pr5d.fingerprints.get("semantic_digest")
            or pr5d.fingerprints.get("semantic_fingerprint"),
            "pr5e_semantic_digest": pr5e.fingerprints.get("semantic_digest"),
        }
        summary = review_summary_aggregate(
            rows=rows,
            bridge_coverage=adapter.coverage,
            freshness=freshness,
            pr5c=pr5c,
            pr5d=pr5d,
            pr5e=pr5e,
            input_paths={
                **adapter.input_paths,
                "sqlite_path": str(sqlite_path.resolve()),
            },
            fingerprints=bridge_fp,
        )
        summary["bucket_policy"] = bucket_policy_document()
        summary["source_reconciliation"] = {
            "records": [r.__dict__ for r in adapter.records],
            "accepted": adapter.coverage["accepted_snapshots"],
            "rejected_or_unresolved": adapter.coverage["rejected_or_unresolved"],
        }

        packet = {
            "ok": True,
            "not_persisted": True,
            "planner_version": BRIDGE_PLANNER_VERSION,
            "as_of_utc": as_of_utc,
            "rows": rows,
            "bucket_policy": bucket_policy_document(),
        }
        walkthrough = render_walkthrough_md(summary=summary, sample_rows=rows)
        csv_text = rows_to_csv(rows)

        (dest / "summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        (dest / "operator_review_packet.json").write_text(
            json.dumps(packet, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        (dest / "operator_review.csv").write_text(csv_text, encoding="utf-8")
        (dest / "walkthrough.md").write_text(walkthrough, encoding="utf-8")
        (dest / "bridge_source_reconciliation.json").write_text(
            json.dumps(
                summary["source_reconciliation"],
                indent=2,
                sort_keys=True,
                ensure_ascii=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return {
            "summary.json": str((dest / "summary.json").resolve()),
            "operator_review_packet.json": str(
                (dest / "operator_review_packet.json").resolve()
            ),
            "operator_review.csv": str((dest / "operator_review.csv").resolve()),
            "walkthrough.md": str((dest / "walkthrough.md").resolve()),
            "bridge_source_reconciliation.json": str(
                (dest / "bridge_source_reconciliation.json").resolve()
            ),
        }

    written = write_atomically(
        out_dir,
        repo_email_pipeline_root=root,
        writer=_writer,
        require_git_ignored=True,
    )
    # Re-load summary for return (aggregate only).
    summary_path = Path(written["summary.json"])
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    return {
        "written": written,
        "summary": summary,
        "freshness_code": "OK" if freshness.get("fresh_enough") else "STALE_ALLOWED",
    }


__all__ = [
    "LiveFeedFreshnessError",
    "build_live_feed_review",
]
