"""PR5D product-relevance planner — compose over PR5C, classify, evaluate foundation."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    CandidatePlanResult,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.output_safety import (
    ReportOutputError,
    require_reports_out_dir,
    write_atomically,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.planner import (
    build_candidate_plan,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.aggregate import (
    aggregate_tender_decision,
    group_unit_decisions_by_tender,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.constants import (
    PRODUCT_RELEVANCE_PLANNER_VERSION,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.evaluation import (
    build_labeling_queue,
    compute_evaluation_metrics,
    write_labeling_queue,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.evidence_adapter import (
    extract_all_product_text_units,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.field_sufficiency import (
    field_sufficiency_document,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.fingerprint import (
    all_fingerprints,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
    ProductRelevancePlanResult,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.rules import (
    classify_product_text_unit,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.taxonomy_extensions import (
    pr5d_taxonomy_document,
    validate_pr5d_taxonomy,
)


class RelevanceReconciliationError(ValueError):
    """Reconciliation equations failed."""


def _email_pipeline_root() -> Path:
    return Path(__file__).resolve().parents[3]


def reconcile_relevance(
    *,
    coalesced_tender_ids: set[str],
    decision_tender_ids: set[str],
    linked_unit_ids: set[str],
    unresolved_unit_ids: set[str],
    all_unit_ids: set[str],
) -> dict[str, Any]:
    missing_decisions = sorted(coalesced_tender_ids - decision_tender_ids)
    extra_decisions = sorted(decision_tender_ids - coalesced_tender_ids)
    unit_union = linked_unit_ids | unresolved_unit_ids
    missing_units = sorted(all_unit_ids - unit_union)
    overlap = sorted(linked_unit_ids & unresolved_unit_ids)
    ok = not (missing_decisions or extra_decisions or missing_units or overlap)
    return {
        "ok": ok,
        "equations": {
            "coalesced_tenders_eq_decisions": len(coalesced_tender_ids)
            == len(decision_tender_ids)
            and not missing_decisions
            and not extra_decisions,
            "units_eq_linked_plus_unresolved": not missing_units and not overlap,
        },
        "coalesced_tender_count": len(coalesced_tender_ids),
        "decision_count": len(decision_tender_ids),
        "linked_unit_count": len(linked_unit_ids),
        "unresolved_unit_count": len(unresolved_unit_ids),
        "missing_decisions": missing_decisions[:50],
        "extra_decisions": extra_decisions[:50],
        "missing_units": missing_units[:50],
        "linked_unresolved_overlap": overlap[:50],
    }


def build_product_relevance_plan(
    *,
    sqlite_path: Path,
    acquisition_snapshot_paths: list[Path],
    as_of_utc: str,
    freshness_threshold_hours: int,
    run_context: str,
    labeling_queue_size: int = 200,
    pr5c_plan: CandidatePlanResult | None = None,
) -> ProductRelevancePlanResult:
    plan = pr5c_plan or build_candidate_plan(
        sqlite_path=sqlite_path,
        acquisition_snapshot_paths=acquisition_snapshot_paths,
        as_of_utc=as_of_utc,
        freshness_threshold_hours=freshness_threshold_hours,
        run_context=run_context,
    )

    linked, unresolved, adapter_meta = extract_all_product_text_units(
        plan, snapshot_paths=acquisition_snapshot_paths
    )

    unit_decisions = tuple(
        sorted(
            (classify_product_text_unit(u) for u in linked),
            key=lambda d: d.unit_decision_id,
        )
    )
    # Unresolved empty units still need explainable unit-level decisions
    # so tenders never silently become unrelated.
    unresolved_decisions = tuple(
        sorted(
            (classify_product_text_unit(u) for u in unresolved),
            key=lambda d: d.unit_decision_id,
        )
    )
    all_unit_decisions = tuple(
        sorted(
            list(unit_decisions) + list(unresolved_decisions),
            key=lambda d: d.unit_decision_id,
        )
    )

    by_tender = group_unit_decisions_by_tender(all_unit_decisions)
    # provisional input fp for per-tender (final fingerprints computed after)
    provisional_input = plan.semantic_digest
    tender_decisions = []
    for tender in plan.coalesced_tenders:
        units = by_tender.get(tender.coalesced_tender_id, [])
        tender_decisions.append(
            aggregate_tender_decision(
                tender,
                units,
                input_fingerprint=provisional_input,
            )
        )
    tender_decisions_t = tuple(
        sorted(tender_decisions, key=lambda d: d.decision_id)
    )

    fps = all_fingerprints(
        pr5c_semantic_digest=plan.semantic_digest,
        linked_units=linked,
        unresolved_units=unresolved,
        tender_decisions=tender_decisions_t,
        unit_decisions=all_unit_decisions,
    )
    # Re-stamp tender decisions with final input fingerprint (identity stable on semantics).
    tender_decisions_final = []
    for tender in plan.coalesced_tenders:
        units = by_tender.get(tender.coalesced_tender_id, [])
        tender_decisions_final.append(
            aggregate_tender_decision(
                tender,
                units,
                input_fingerprint=fps["input_fingerprint"],
            )
        )
    tender_decisions_t = tuple(
        sorted(tender_decisions_final, key=lambda d: d.decision_id)
    )
    fps = all_fingerprints(
        pr5c_semantic_digest=plan.semantic_digest,
        linked_units=linked,
        unresolved_units=unresolved,
        tender_decisions=tender_decisions_t,
        unit_decisions=all_unit_decisions,
    )

    reconciliation = reconcile_relevance(
        coalesced_tender_ids={t.coalesced_tender_id for t in plan.coalesced_tenders},
        decision_tender_ids={d.coalesced_tender_id for d in tender_decisions_t},
        linked_unit_ids={u.unit_id for u in linked},
        unresolved_unit_ids={u.unit_id for u in unresolved},
        all_unit_ids={u.unit_id for u in linked} | {u.unit_id for u in unresolved},
    )
    if not reconciliation["ok"]:
        raise RelevanceReconciliationError(
            f"relevance reconciliation failed: {reconciliation}"
        )

    class_counts = Counter(d.relevance_class for d in tender_decisions_t)
    abstain = sum(
        1
        for d in tender_decisions_t
        if d.confidence_band == "abstain" or d.relevance_class == "ambiguous"
    )

    # Labeling queue inputs
    text_by: dict[str, str] = {}
    lines_by: dict[str, bool] = {}
    plane_by: dict[str, str] = {}
    for tender in plan.coalesced_tenders:
        units = [u for u in linked if u.coalesced_tender_id == tender.coalesced_tender_id]
        text_by[tender.coalesced_tender_id] = " || ".join(
            u.text_raw for u in units if u.text_raw
        ) or (tender.title_selected or "")
        lines_by[tender.coalesced_tender_id] = any(
            u.evidence_tier == "line_product_text" for u in units
        )
        plane_by[tender.coalesced_tender_id] = tender.candidate_source_kind

    queue = build_labeling_queue(
        tender_decisions_t,
        product_text_by_tender=text_by,
        has_lines_by_tender=lines_by,
        source_plane_by_tender=plane_by,
        target_size=int(labeling_queue_size),
    )
    metrics = compute_evaluation_metrics(queue)

    counts = {
        "coalesced_tenders": len(plan.coalesced_tenders),
        "relevance_decisions": len(tender_decisions_t),
        "linked_units": len(linked),
        "unresolved_units": len(unresolved),
        "unit_decisions": len(all_unit_decisions),
        "by_relevance_class": dict(sorted(class_counts.items())),
        "abstention_or_ambiguous": abstain,
        "labeling_queue_size": len(queue),
        "adapter": adapter_meta,
    }

    return ProductRelevancePlanResult(
        product_text_units=linked,
        unresolved_units=unresolved,
        unit_decisions=all_unit_decisions,
        tender_decisions=tender_decisions_t,
        field_sufficiency=field_sufficiency_document(),
        reconciliation=reconciliation,
        taxonomy_document=pr5d_taxonomy_document(),
        fingerprints=fps,
        counts=counts,
        run_context=run_context,
        planner_version=PRODUCT_RELEVANCE_PLANNER_VERSION,
        as_of_utc=plan.as_of_utc,
        pr5c_semantic_digest=plan.semantic_digest,
        evaluation_meta={
            "queue_record_count": len(queue),
            "metrics": metrics,
            "taxonomy_validation": validate_pr5d_taxonomy(),
            "labeling_queue": [r.to_dict() for r in queue],
        },
    )


def write_relevance_outputs(
    result: ProductRelevancePlanResult,
    out_dir: Path,
    *,
    repo_email_pipeline_root: Path | None = None,
    require_git_ignored: bool = True,
) -> dict[str, str]:
    root = repo_email_pipeline_root or _email_pipeline_root()

    def _write(safe: Path) -> dict[str, str]:
        def dump(name: str, payload: Any) -> str:
            path = safe / name
            path.write_text(
                json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True)
                + "\n",
                encoding="utf-8",
            )
            return str(path)

        written: dict[str, str] = {}
        written["RUN_MANIFEST.json"] = dump(
            "RUN_MANIFEST.json",
            {
                "planner_version": result.planner_version,
                "run_context": result.run_context,
                "as_of_utc": result.as_of_utc,
                "pr5c_semantic_digest": result.pr5c_semantic_digest,
                "fingerprints": result.fingerprints,
                "counts": result.counts,
                "reconciliation": result.reconciliation,
                "not_persisted": True,
                "scope": "product_relevance_only",
                "exclusions": [
                    "contact_resolution",
                    "lead_persistence",
                    "outreach",
                    "scheduling",
                    "gmail",
                    "postgres",
                    "llm",
                ],
            },
        )
        written["field_sufficiency.json"] = dump(
            "field_sufficiency.json", result.field_sufficiency
        )
        written["taxonomy.json"] = dump("taxonomy.json", result.taxonomy_document)
        written["tender_relevance_decisions.json"] = dump(
            "tender_relevance_decisions.json",
            [d.to_dict() for d in result.tender_decisions],
        )
        written["unit_relevance_decisions.json"] = dump(
            "unit_relevance_decisions.json",
            [d.to_dict() for d in result.unit_decisions],
        )
        written["product_text_units.json"] = dump(
            "product_text_units.json",
            {
                "linked": [u.to_dict() for u in result.product_text_units],
                "unresolved": [u.to_dict() for u in result.unresolved_units],
            },
        )
        written["evaluation_metrics.json"] = dump(
            "evaluation_metrics.json",
            result.evaluation_meta.get("metrics") or {},
        )
        queue_path = safe / "labeling_queue.json"
        # evaluation_meta embeds queue; also write dedicated file
        from origenlab_email_pipeline.commercial_procurement_product_relevance.evaluation import (
            EvaluationRecord,
        )

        records = [
            EvaluationRecord(**r) if isinstance(r, dict) else r
            for r in (result.evaluation_meta.get("labeling_queue") or [])
        ]
        # Records may already be dicts from to_dict — write raw
        queue_path.write_text(
            json.dumps(
                {
                    "schema": "pr5d_labeling_queue_v1",
                    "records": result.evaluation_meta.get("labeling_queue") or [],
                    "metrics": result.evaluation_meta.get("metrics") or {},
                },
                indent=2,
                sort_keys=True,
                ensure_ascii=True,
            )
            + "\n",
            encoding="utf-8",
        )
        written["labeling_queue.json"] = str(queue_path)
        written["summary.json"] = dump("summary.json", result.to_summary_dict())
        _ = records  # reserved for typed rewrite path
        return written

    return write_atomically(
        out_dir,
        repo_email_pipeline_root=root,
        writer=_write,
        require_git_ignored=require_git_ignored,
    )


# Re-export for CLI convenience
__all__ = [
    "RelevanceReconciliationError",
    "ReportOutputError",
    "build_product_relevance_plan",
    "require_reports_out_dir",
    "write_relevance_outputs",
    "write_labeling_queue",
]
