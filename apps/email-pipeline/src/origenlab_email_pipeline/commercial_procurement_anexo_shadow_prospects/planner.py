"""ANEXO-P1 orchestration: CURRENT vs SHADOW prospect comparison.

SHADOW ONLY. Reuses ``build_institution_prospects_from_plans`` twice with the
same PR5C and frozen PR5E plans; only the PR5D plan differs (baseline vs
baseline+annex). Never mutates production queues or commercial state.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.chilecompra_anexo_evidence.redaction import (
    assert_no_portal_tokens,
)
from origenlab_email_pipeline.commercial_procurement_anexo_recognition.corpus import (
    AnexoEvidenceBundleSet,
)
from origenlab_email_pipeline.commercial_procurement_anexo_recognition.planner import (
    run_comparison,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.output_safety import (
    write_atomically,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.planner import (
    InstitutionProspectPlanResult,
    build_institution_prospects_from_plans,
)

from .capability import capability_digest
from .compare import (
    build_institution_deltas,
    build_queue_deltas,
    build_tender_deltas,
)
from .constants import (
    AS_OF_UTC_DEFAULT,
    COMPARISON_VERSION,
    SAFETY_FALSE_FLAGS,
    SHADOW_ACCESSORY_OR_CONSUMABLE_ONLY,
    SHADOW_BUNDLED_PARTIAL_CAPABILITY,
    SHADOW_CHANGE_CLASSES,
    SHADOW_INCOMPLETE_NO_CONCLUSION,
    SHADOW_METHOD_OR_EXAM_ONLY,
    SHADOW_NEW_IN_SCOPE_OPPORTUNITY,
    SHADOW_NEW_OUT_OF_SCOPE_EQUIPMENT,
    SHADOW_SERVICE_OR_MAINTENANCE_ONLY,
    SHADOW_STRENGTHENED_EXISTING_OPPORTUNITY,
    SHADOW_SUPPLIER_REQUIRED_EQUIPMENT,
    QUEUE_WOULD_CHANGE_PRIORITY,
    QUEUE_WOULD_ENTER_CURRENT,
    QUEUE_WOULD_LEAVE_CURRENT,
)
from .fingerprint import plan_fingerprint, shadow_semantic_digest
from .models import ShadowProspectComparisonResult
from .plans import (
    build_candidate_plan,
    build_frozen_contact_plan,
    build_relevance_plan_pair,
)
from .walkthrough import render_walkthrough


class AnexoShadowProspectError(RuntimeError):
    """Raised when a read-only / shadow invariant is violated."""


def assert_shadow_invariants(result: ShadowProspectComparisonResult) -> None:
    """Fail closed if shadow artifacts look like production outputs."""
    for flag in SAFETY_FALSE_FLAGS:
        if getattr(result, flag, None) is not False:
            raise AnexoShadowProspectError(f"{flag} must be False")
    if result.lane != "anexo_p1_shadow_only":
        raise AnexoShadowProspectError("lane must remain anexo_p1_shadow_only")
    if result.comparison_version != COMPARISON_VERSION:
        raise AnexoShadowProspectError("unexpected comparison_version")
    for delta in result.tender_deltas:
        if delta.change_class not in SHADOW_CHANGE_CLASSES:
            raise AnexoShadowProspectError(
                f"unknown shadow change class {delta.change_class!r}"
            )


def _equipment_purchase_signal_metrics(
    plan: InstitutionProspectPlanResult,
) -> dict[str, int]:
    """Grain-explicit counts over category-scoped tender rows.

    ``equipment_purchase_signal_rows`` is a *row* count (institution × tender ×
    equipment category). It is not unique tenders, institutions, or queue
    memberships.
    """
    rows = [
        row
        for row in plan.tender_rows
        if row.get("commercial_signal_type") == "equipment_purchase_signal"
    ]
    tender_ids = {
        str(row.get("coalesced_tender_id"))
        for row in rows
        if row.get("coalesced_tender_id")
    }
    institution_ids = {
        str(row.get("institution_id")) for row in rows if row.get("institution_id")
    }
    return {
        "equipment_purchase_signal_rows": len(rows),
        "equipment_purchase_tender_count": len(tender_ids),
        "equipment_purchase_institution_count": len(institution_ids),
    }


def run_shadow_comparison(
    tenders: Sequence[dict[str, Any]],
    *,
    anexo: AnexoEvidenceBundleSet,
    as_of_utc: str = AS_OF_UTC_DEFAULT,
) -> tuple[ShadowProspectComparisonResult, InstitutionProspectPlanResult, InstitutionProspectPlanResult]:
    """Run CURRENT and SHADOW prospect planners and diff them in memory."""
    relevance = build_relevance_plan_pair(tenders, anexo=anexo, as_of_utc=as_of_utc)
    pr5c = build_candidate_plan(
        tenders, as_of_utc=as_of_utc, input_digest=relevance.input_digest
    )
    pr5e = build_frozen_contact_plan(tenders, as_of_utc=as_of_utc)

    current = build_institution_prospects_from_plans(
        pr5c=pr5c,
        pr5d=relevance.current,
        pr5e=pr5e,
        as_of_utc=as_of_utc,
        run_context="anexo_p1_shadow",
    )
    shadow = build_institution_prospects_from_plans(
        pr5c=pr5c,
        pr5d=relevance.shadow,
        pr5e=pr5e,
        as_of_utc=as_of_utc,
        run_context="anexo_p1_shadow",
    )

    # E2 comparison supplies provenance / coverage / interpretation.
    e2 = run_comparison(tenders, anexo=anexo)

    queue_deltas = build_queue_deltas(current, shadow)
    institution_deltas = build_institution_deltas(current, shadow)
    tender_deltas = build_tender_deltas(
        tenders=tenders,
        current=current,
        shadow=shadow,
        current_decisions=relevance.current_decisions_by_tender,
        shadow_decisions=relevance.shadow_decisions_by_tender,
        e2_comparisons=e2.comparisons,
        e2_claims=e2.claims,
        queue_deltas=queue_deltas,
    )

    current_metrics = _equipment_purchase_signal_metrics(current)
    shadow_metrics = _equipment_purchase_signal_metrics(shadow)
    would_enter_tenders = sorted(
        {
            str(d.tender_id)
            for d in queue_deltas
            if d.delta_class == QUEUE_WOULD_ENTER_CURRENT and d.tender_id
        }
    )

    result = ShadowProspectComparisonResult(
        comparison_version=COMPARISON_VERSION,
        corpus_digest=relevance.input_digest,
        semantic_digest=shadow_semantic_digest(
            tender_deltas, institution_deltas, queue_deltas
        ),
        catalog_capability_digest=capability_digest(),
        as_of_utc=as_of_utc,
        tender_deltas=tender_deltas,
        institution_deltas=institution_deltas,
        queue_deltas=queue_deltas,
        current_fingerprint=plan_fingerprint(current),
        shadow_fingerprint=plan_fingerprint(shadow),
        e2_semantic_digest=e2.semantic_digest,
        notes={
            "current_equipment_purchase_signal_rows": current_metrics[
                "equipment_purchase_signal_rows"
            ],
            "shadow_equipment_purchase_signal_rows": shadow_metrics[
                "equipment_purchase_signal_rows"
            ],
            "current_equipment_purchase_tender_count": current_metrics[
                "equipment_purchase_tender_count"
            ],
            "shadow_equipment_purchase_tender_count": shadow_metrics[
                "equipment_purchase_tender_count"
            ],
            "current_equipment_purchase_institution_count": current_metrics[
                "equipment_purchase_institution_count"
            ],
            "shadow_equipment_purchase_institution_count": shadow_metrics[
                "equipment_purchase_institution_count"
            ],
            "current_current_opportunity_queue_count": len(
                current.operator_queues.get("current_opportunity_queue", [])
            ),
            "shadow_current_opportunity_queue_count": len(
                shadow.operator_queues.get("current_opportunity_queue", [])
            ),
            "would_enter_current_opportunity_tender_ids": would_enter_tenders,
            "metric_grain": {
                "equipment_purchase_signal_rows": (
                    "category-scoped tender_rows with "
                    "commercial_signal_type=equipment_purchase_signal "
                    "(not unique tenders / not queue membership)"
                ),
                "equipment_purchase_tender_count": (
                    "distinct coalesced_tender_id among those rows"
                ),
                "equipment_purchase_institution_count": (
                    "distinct institution_id among those rows"
                ),
                "current_opportunity_queue_count": (
                    "rows in operator_queues['current_opportunity_queue']"
                ),
            },
            "variable": "product_evidence_only",
            "pr5c_shared": True,
            "pr5e_frozen": True,
        },
    )
    assert_shadow_invariants(result)
    return result, current, shadow


def build_summary(
    result: ShadowProspectComparisonResult,
) -> dict[str, Any]:
    """Aggregate-only shadow summary; never embed production queue paths."""
    assert_shadow_invariants(result)
    change_counts = {
        cls: sum(1 for d in result.tender_deltas if d.change_class == cls)
        for cls in SHADOW_CHANGE_CLASSES
    }
    queue_counts = Counter(d.delta_class for d in result.queue_deltas)
    service_or_maintenance = change_counts[SHADOW_SERVICE_OR_MAINTENANCE_ONLY]
    return {
        "algorithm": "anexo_shadow_prospect_comparison_v1",
        "comparison_version": result.comparison_version,
        "lane": result.lane,
        "as_of_utc": result.as_of_utc,
        "corpus_digest": result.corpus_digest,
        "semantic_digest": result.semantic_digest,
        "catalog_capability_digest": result.catalog_capability_digest,
        "e2_semantic_digest": result.e2_semantic_digest,
        "current_fingerprint": result.current_fingerprint,
        "shadow_fingerprint": result.shadow_fingerprint,
        "tenders_evaluated": len(result.tender_deltas),
        "metric_grain": result.notes.get("metric_grain"),
        "current_equipment_purchase_signal_rows": result.notes.get(
            "current_equipment_purchase_signal_rows"
        ),
        "shadow_equipment_purchase_signal_rows": result.notes.get(
            "shadow_equipment_purchase_signal_rows"
        ),
        "current_equipment_purchase_tender_count": result.notes.get(
            "current_equipment_purchase_tender_count"
        ),
        "shadow_equipment_purchase_tender_count": result.notes.get(
            "shadow_equipment_purchase_tender_count"
        ),
        "current_equipment_purchase_institution_count": result.notes.get(
            "current_equipment_purchase_institution_count"
        ),
        "shadow_equipment_purchase_institution_count": result.notes.get(
            "shadow_equipment_purchase_institution_count"
        ),
        "current_current_opportunity_queue_count": result.notes.get(
            "current_current_opportunity_queue_count"
        ),
        "shadow_current_opportunity_queue_count": result.notes.get(
            "shadow_current_opportunity_queue_count"
        ),
        "would_enter_current_opportunity_tender_ids": list(
            result.notes.get("would_enter_current_opportunity_tender_ids") or []
        ),
        "change_class_counts": change_counts,
        "new_in_scope_opportunities": change_counts[SHADOW_NEW_IN_SCOPE_OPPORTUNITY],
        "partial_capability_opportunities": change_counts[
            SHADOW_BUNDLED_PARTIAL_CAPABILITY
        ],
        "out_of_scope_equipment_detections": change_counts[
            SHADOW_NEW_OUT_OF_SCOPE_EQUIPMENT
        ],
        "strengthened_prospects": change_counts[SHADOW_STRENGTHENED_EXISTING_OPPORTUNITY],
        "suppressed_service_or_maintenance_cases": service_or_maintenance,
        "suppressed_accessory_cases": change_counts[SHADOW_ACCESSORY_OR_CONSUMABLE_ONLY],
        "suppressed_method_exam_cases": change_counts[SHADOW_METHOD_OR_EXAM_ONLY],
        "supplier_required_equipment_cases": change_counts[
            SHADOW_SUPPLIER_REQUIRED_EQUIPMENT
        ],
        "incomplete_absence_unproven_cases": change_counts[
            SHADOW_INCOMPLETE_NO_CONCLUSION
        ],
        "queue_entries": queue_counts.get(QUEUE_WOULD_ENTER_CURRENT, 0),
        "queue_exits": queue_counts.get(QUEUE_WOULD_LEAVE_CURRENT, 0),
        "priority_changes": queue_counts.get(QUEUE_WOULD_CHANGE_PRIORITY, 0),
        "institution_profiles_changed": sum(
            1 for d in result.institution_deltas if d.changed
        ),
        "queue_delta_counts": dict(sorted(queue_counts.items())),
        "contact_authorization": False,
        "outreach_authorization": False,
        "production_queue_mutated": False,
        "persisted": False,
        "pr5f_started": False,
        "annex_production_integration": False,
        "shadow_results_are_not_production": True,
        "canonical_production_queues_untouched": True,
    }


def _dump_json(payload: Any, path: Path, *, where: str) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    assert_no_portal_tokens(text, where=where)
    path.write_text(text, encoding="utf-8")


def _dump_jsonl(rows: Sequence[dict[str, Any]], path: Path, *, where: str) -> None:
    lines = [json.dumps(row, sort_keys=True, ensure_ascii=True) for row in rows]
    text = "\n".join(lines) + ("\n" if lines else "")
    assert_no_portal_tokens(text, where=where)
    path.write_text(text, encoding="utf-8")


def _dump_csv(rows: Sequence[dict[str, Any]], path: Path, *, where: str) -> None:
    fieldnames = [
        "tender_id",
        "buyer_organization",
        "change_class",
        "commercial_intent_class",
        "shadow_equipment_classes",
        "claim_level_capability",
        "queue_delta",
        "coverage_status",
        "human_review_required",
    ]
    buf_lines: list[str] = []
    # Build via csv module into memory then redact-check.
    from io import StringIO

    buf = StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                "tender_id": row.get("tender_id"),
                "buyer_organization": row.get("buyer_organization"),
                "change_class": row.get("change_class"),
                "commercial_intent_class": row.get("commercial_intent_class"),
                "shadow_equipment_classes": "|".join(
                    row.get("shadow_equipment_classes") or []
                ),
                "claim_level_capability": "|".join(
                    f"{c['equipment_class']}:{c['capability']}"
                    for c in (row.get("claim_level_capability") or [])
                ),
                "queue_delta": "|".join(row.get("queue_delta") or []),
                "coverage_status": row.get("coverage_status"),
                "human_review_required": row.get("human_review_required"),
            }
        )
    text = buf.getvalue()
    assert_no_portal_tokens(text, where=where)
    path.write_text(text, encoding="utf-8")
    buf_lines.clear()


def write_shadow_outputs(
    result: ShadowProspectComparisonResult,
    out_dir: Path,
    *,
    repo_email_pipeline_root: Path,
    require_git_ignored: bool = True,
) -> dict[str, str]:
    """Atomically publish the gitignored shadow review packet."""
    assert_shadow_invariants(result)
    summary = build_summary(result)

    def writer(staging: Path) -> dict[str, str]:
        tender_rows = [d.to_dict() for d in result.tender_deltas]
        institution_rows = [d.to_dict() for d in result.institution_deltas]
        queue_rows = [d.to_dict() for d in result.queue_deltas]
        _dump_json(summary, staging / "summary.json", where="summary.json")
        _dump_jsonl(tender_rows, staging / "tender_deltas.jsonl", where="tender_deltas")
        _dump_jsonl(
            institution_rows,
            staging / "institution_deltas.jsonl",
            where="institution_deltas",
        )
        _dump_jsonl(queue_rows, staging / "queue_deltas.jsonl", where="queue_deltas")
        _dump_csv(tender_rows, staging / "shadow_opportunities.csv", where="csv")
        walkthrough = render_walkthrough(result, summary)
        assert_no_portal_tokens(walkthrough, where="walkthrough.md")
        (staging / "walkthrough.md").write_text(walkthrough, encoding="utf-8")
        return {
            "summary": str(staging / "summary.json"),
            "tender_deltas": str(staging / "tender_deltas.jsonl"),
            "institution_deltas": str(staging / "institution_deltas.jsonl"),
            "queue_deltas": str(staging / "queue_deltas.jsonl"),
            "shadow_opportunities": str(staging / "shadow_opportunities.csv"),
            "walkthrough": str(staging / "walkthrough.md"),
        }

    return write_atomically(
        out_dir,
        repo_email_pipeline_root=repo_email_pipeline_root,
        writer=writer,
        require_git_ignored=require_git_ignored,
    )
