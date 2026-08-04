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
    aggregate_only_metrics,
    assert_blind_sealed_operational_record_id_join,
    build_labeling_queue,
    compute_evaluation_metrics,
    is_manual_review_prediction,
    sampling_distribution_report,
    to_blind_packet,
    to_sealed_scoring,
    write_human_review_packet,
    write_operational_evaluation_records,
    write_sealed_scoring_manifest,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.evidence_adapter import (
    ExtractionAttempt,
    extract_all_product_text_units,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.field_sufficiency import (
    field_sufficiency_document,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.fingerprint import (
    all_fingerprints,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
    MaterializationRecord,
    ProductRelevancePlanResult,
    ProductTextUnit,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.redaction import (
    assert_human_packet_prediction_blind,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.rules import (
    classify_product_text_unit,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.taxonomy_extensions import (
    pr5d_taxonomy_document,
    validate_pr5d_taxonomy,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.walkthrough import (
    build_cases_a_e,
    write_walkthrough_into,
)


class RelevanceReconciliationError(ValueError):
    """Reconciliation equations failed."""


def _email_pipeline_root() -> Path:
    return Path(__file__).resolve().parents[3]


_ATTEMPT_UNIT_IDENTITY_FIELDS = (
    "coalesced_tender_id",
    "evidence_ref_id",
    "field_path",
    "snapshot_id",
    "observation_id",
    "tender_observation_id",
    "line_observation_id",
)


def reconcile_relevance(
    *,
    coalesced_tender_ids: list[str],
    decision_tender_ids: list[str],
    linked_units: list[ProductTextUnit],
    unresolved_units: list[ProductTextUnit],
    extraction_attempts: list[ExtractionAttempt],
    materialization_ledger: list[MaterializationRecord] | list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Independently prove each attempt↔unit binding (ledger is not sole truth).

    For every occurrence, validate attempt identity, unit attempt stamps, ledger
    row, deterministic unit_id recomputed from material semantic fields, and
    linked/unresolved partition. ID-list cardinality alone is insufficient.
    """
    from origenlab_email_pipeline.commercial_procurement_product_relevance.evidence_adapter import (
        recompute_unit_id,
    )

    linked_unit_ids = [u.unit_id for u in linked_units]
    unresolved_unit_ids = [u.unit_id for u in unresolved_units]
    extraction_attempt_ids = [a.attempt_id for a in extraction_attempts]

    tender_set = set(coalesced_tender_ids)
    decision_set = set(decision_tender_ids)
    linked_set = set(linked_unit_ids)
    unresolved_set = set(unresolved_unit_ids)
    units_by_id = {u.unit_id: u for u in list(linked_units) + list(unresolved_units)}

    missing_decisions = sorted(tender_set - decision_set)
    extra_decisions = sorted(decision_set - tender_set)
    duplicate_tender_decisions = sorted(
        tid for tid, n in Counter(decision_tender_ids).items() if n > 1
    )
    duplicate_unit_ids = sorted(
        uid
        for uid, n in Counter(linked_unit_ids + unresolved_unit_ids).items()
        if n > 1
    )
    duplicate_attempts = sorted(
        aid for aid, n in Counter(extraction_attempt_ids).items() if n > 1
    )
    overlap = sorted(linked_set & unresolved_set)

    mats: list[dict[str, Any]] = []
    for row in materialization_ledger or []:
        if isinstance(row, MaterializationRecord):
            mats.append(row.to_dict())
        else:
            mats.append(dict(row))

    attempt_occurrences = list(range(len(extraction_attempts)))
    mat_occurrences = [int(m["attempt_occurrence"]) for m in mats]
    mat_attempt_ids = [str(m["attempt_id"]) for m in mats]
    mat_unit_ids = [str(m["unit_id"]) for m in mats]

    missing_materializations = sorted(set(attempt_occurrences) - set(mat_occurrences))
    extra_materializations = sorted(set(mat_occurrences) - set(attempt_occurrences))
    duplicate_materialization_occurrences = sorted(
        occ for occ, n in Counter(mat_occurrences).items() if n > 1
    )
    duplicate_materialized_unit_ids = sorted(
        uid for uid, n in Counter(mat_unit_ids).items() if n > 1
    )

    wrong_attempt_association: list[dict[str, Any]] = []
    identity_binding_failures: list[dict[str, Any]] = []
    unit_id_semantic_mismatches: list[dict[str, Any]] = []
    by_occ = {int(m["attempt_occurrence"]): m for m in mats}

    for occ, attempt in enumerate(extraction_attempts):
        row = by_occ.get(occ)
        if row is None:
            continue
        if str(row["attempt_id"]) != attempt.attempt_id:
            wrong_attempt_association.append(
                {
                    "attempt_occurrence": occ,
                    "expected_attempt_id": attempt.attempt_id,
                    "actual_attempt_id": row["attempt_id"],
                    "unit_id": row["unit_id"],
                }
            )
            continue

        unit = units_by_id.get(str(row["unit_id"]))
        if unit is None:
            identity_binding_failures.append(
                {
                    "attempt_occurrence": occ,
                    "attempt_id": attempt.attempt_id,
                    "unit_id": row["unit_id"],
                    "error": "ledger_unit_missing_from_partitions",
                }
            )
            continue

        # Unit stamps must independently agree with attempt + occurrence.
        if unit.attempt_id != attempt.attempt_id:
            identity_binding_failures.append(
                {
                    "attempt_occurrence": occ,
                    "attempt_id": attempt.attempt_id,
                    "unit_id": unit.unit_id,
                    "unit_attempt_id": unit.attempt_id,
                    "error": "unit_attempt_id_mismatch",
                }
            )
        if unit.attempt_occurrence != occ:
            identity_binding_failures.append(
                {
                    "attempt_occurrence": occ,
                    "attempt_id": attempt.attempt_id,
                    "unit_id": unit.unit_id,
                    "unit_attempt_occurrence": unit.attempt_occurrence,
                    "error": "unit_attempt_occurrence_mismatch",
                }
            )
        if int(row["attempt_occurrence"]) != occ:
            identity_binding_failures.append(
                {
                    "attempt_occurrence": occ,
                    "ledger_occurrence": row["attempt_occurrence"],
                    "error": "ledger_occurrence_mismatch",
                }
            )

        for attr in _ATTEMPT_UNIT_IDENTITY_FIELDS:
            expected = getattr(attempt, attr)
            actual = getattr(unit, attr)
            if actual != expected:
                identity_binding_failures.append(
                    {
                        "attempt_occurrence": occ,
                        "attempt_id": attempt.attempt_id,
                        "unit_id": unit.unit_id,
                        "field": attr,
                        "expected": expected,
                        "actual": actual,
                        "error": "attempt_unit_identity_field_mismatch",
                    }
                )

        if row.get("field_path") is not None and str(row["field_path"]) != attempt.field_path:
            identity_binding_failures.append(
                {
                    "attempt_occurrence": occ,
                    "attempt_id": attempt.attempt_id,
                    "unit_id": unit.unit_id,
                    "error": "ledger_field_path_mismatch",
                    "expected": attempt.field_path,
                    "actual": row.get("field_path"),
                }
            )
        if (
            row.get("attempt_kind") is not None
            and str(row["attempt_kind"]) != attempt.attempt_kind
        ):
            identity_binding_failures.append(
                {
                    "attempt_occurrence": occ,
                    "attempt_id": attempt.attempt_id,
                    "unit_id": unit.unit_id,
                    "error": "ledger_attempt_kind_mismatch",
                    "expected": attempt.attempt_kind,
                    "actual": row.get("attempt_kind"),
                }
            )

        # Deterministic unit_id from material semantic fields — not ledger claim.
        expected_uid = recompute_unit_id(unit)
        if unit.unit_id != expected_uid:
            unit_id_semantic_mismatches.append(
                {
                    "attempt_occurrence": occ,
                    "attempt_id": attempt.attempt_id,
                    "claimed_unit_id": unit.unit_id,
                    "recomputed_unit_id": expected_uid,
                    "error": "unit_id_inconsistent_with_semantic_fields",
                }
            )
        if str(row["unit_id"]) != expected_uid:
            unit_id_semantic_mismatches.append(
                {
                    "attempt_occurrence": occ,
                    "attempt_id": attempt.attempt_id,
                    "ledger_unit_id": row["unit_id"],
                    "recomputed_unit_id": expected_uid,
                    "error": "ledger_unit_id_inconsistent_with_semantic_fields",
                }
            )

    # Partition membership must match linked/unresolved sets + typed status.
    partition_mismatch: list[dict[str, Any]] = []
    for m in mats:
        uid = str(m["unit_id"])
        part = str(m["partition"])
        in_linked = uid in linked_set
        in_unresolved = uid in unresolved_set
        unit = units_by_id.get(uid)
        if part == "linked" and not in_linked:
            partition_mismatch.append({**m, "error": "marked_linked_missing_from_linked"})
        if part == "unresolved" and not in_unresolved:
            partition_mismatch.append(
                {**m, "error": "marked_unresolved_missing_from_unresolved"}
            )
        if part not in {"linked", "unresolved"}:
            partition_mismatch.append({**m, "error": "invalid_partition"})
        if unit is not None:
            if part == "linked" and unit.link_status != "linked":
                partition_mismatch.append(
                    {**m, "error": "partition_linked_but_unit_not_linked"}
                )
            if part == "unresolved" and unit.link_status == "linked":
                partition_mismatch.append(
                    {**m, "error": "partition_unresolved_but_unit_linked"}
                )
            status = str(m.get("materialization_status") or "")
            if part == "linked" and status not in {"linked", ""}:
                # linked rows use materialization_status "linked"
                if status != "linked":
                    partition_mismatch.append(
                        {**m, "error": "linked_partition_status_mismatch"}
                    )

    units_without_attempt = sorted(
        (linked_set | unresolved_set) - set(mat_unit_ids)
    )
    attempts_without_unit = [
        {
            "attempt_occurrence": occ,
            "attempt_id": extraction_attempts[occ].attempt_id,
        }
        for occ in missing_materializations
    ]

    identity_ok = not identity_binding_failures and not unit_id_semantic_mismatches

    bijection_ok = (
        len(extraction_attempts) == len(mats)
        and not missing_materializations
        and not extra_materializations
        and not duplicate_materialization_occurrences
        and not duplicate_materialized_unit_ids
        and not wrong_attempt_association
        and not partition_mismatch
        and not units_without_attempt
        and mat_attempt_ids == list(extraction_attempt_ids)
        and identity_ok
    )

    ok = not (
        missing_decisions
        or extra_decisions
        or duplicate_tender_decisions
        or duplicate_unit_ids
        or duplicate_attempts
        or overlap
        or not bijection_ok
        or len(coalesced_tender_ids) != len(decision_tender_ids)
    )
    return {
        "ok": ok,
        "equations": {
            "coalesced_tenders_eq_decisions": (
                len(coalesced_tender_ids) == len(decision_tender_ids)
                and not missing_decisions
                and not extra_decisions
                and not duplicate_tender_decisions
            ),
            "attempt_occurrence_bijection_with_materialization": bijection_ok,
            "attempt_unit_identity_binding": identity_ok,
        },
        "coalesced_tender_count": len(coalesced_tender_ids),
        "decision_count": len(decision_tender_ids),
        "linked_unit_count": len(linked_unit_ids),
        "unresolved_unit_count": len(unresolved_unit_ids),
        "extraction_attempt_count": len(extraction_attempt_ids),
        "materialization_count": len(mats),
        "missing_decisions": missing_decisions[:50],
        "extra_decisions": extra_decisions[:50],
        "duplicate_tender_decisions": duplicate_tender_decisions[:50],
        "duplicate_unit_ids": duplicate_unit_ids[:50],
        "duplicate_extraction_attempts": duplicate_attempts[:50],
        "linked_unresolved_overlap": overlap[:50],
        "missing_materializations": attempts_without_unit[:50],
        "extra_materializations": extra_materializations[:50],
        "duplicate_materialization_occurrences": duplicate_materialization_occurrences[
            :50
        ],
        "duplicate_materialized_unit_ids": duplicate_materialized_unit_ids[:50],
        "wrong_attempt_association": wrong_attempt_association[:50],
        "identity_binding_failures": identity_binding_failures[:50],
        "unit_id_semantic_mismatches": unit_id_semantic_mismatches[:50],
        "partition_mismatch": partition_mismatch[:50],
        "units_without_attempt": units_without_attempt[:50],
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

    linked, unresolved, adapter_meta, extraction_attempts, materialization_ledger = (
        extract_all_product_text_units(plan, snapshot_paths=acquisition_snapshot_paths)
    )

    unit_decisions = tuple(
        sorted(
            (classify_product_text_unit(u) for u in linked),
            key=lambda d: d.unit_decision_id,
        )
    )
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
    provisional_input = plan.semantic_digest
    tender_decisions = [
        aggregate_tender_decision(
            tender,
            by_tender.get(tender.coalesced_tender_id, []),
            input_fingerprint=provisional_input,
        )
        for tender in plan.coalesced_tenders
    ]
    tender_decisions_t = tuple(sorted(tender_decisions, key=lambda d: d.decision_id))

    fps = all_fingerprints(
        pr5c_semantic_digest=plan.semantic_digest,
        linked_units=linked,
        unresolved_units=unresolved,
        tender_decisions=tender_decisions_t,
        unit_decisions=all_unit_decisions,
    )
    tender_decisions_final = [
        aggregate_tender_decision(
            tender,
            by_tender.get(tender.coalesced_tender_id, []),
            input_fingerprint=fps["input_fingerprint"],
        )
        for tender in plan.coalesced_tenders
    ]
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
        coalesced_tender_ids=[t.coalesced_tender_id for t in plan.coalesced_tenders],
        decision_tender_ids=[d.coalesced_tender_id for d in tender_decisions_t],
        linked_units=list(linked),
        unresolved_units=list(unresolved),
        extraction_attempts=list(extraction_attempts),
        materialization_ledger=list(materialization_ledger),
    )
    if not reconciliation["ok"]:
        raise RelevanceReconciliationError(
            f"relevance reconciliation failed: {reconciliation}"
        )

    class_counts = Counter(d.relevance_class for d in tender_decisions_t)
    abstain = sum(
        1
        for d in tender_decisions_t
        if is_manual_review_prediction(
            relevance_class=d.relevance_class,
            confidence_band=d.confidence_band,
            resolution_status=d.product_resolution_status,
            ambiguity_reason_codes=d.ambiguity_reason_codes,
            aggregation_reason_codes=d.aggregation_reason_codes,
        )
    )

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

    queue, sampling_report = build_labeling_queue(
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
        "abstention_or_manual_review": abstain,
        "abstention_or_ambiguous": abstain,  # back-compat key
        "proposed_evaluation_record_count": len(queue),
        "adapter": adapter_meta,
        "sampling": sampling_report,
    }

    blind = tuple(r.to_dict() for r in to_blind_packet(queue))
    sealed = tuple(r.to_dict() for r in to_sealed_scoring(queue))
    operational = tuple(r.to_dict() for r in queue)

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
            "sampling": sampling_report,
        },
        human_review_packet=blind,
        sealed_scoring_manifest=sealed,
        operational_evaluation_records=operational,
        materialization_ledger=tuple(m.to_dict() for m in materialization_ledger),
    )


def _write_plan_files(safe: Path, result: ProductRelevancePlanResult) -> dict[str, str]:
    def dump(name: str, payload: Any) -> str:
        path = safe / name
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
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
            "note": "Operational full-detail bundle — not the shareable walkthrough.",
        },
    )
    written["evaluation_metrics.json"] = dump(
        "evaluation_metrics.json",
        aggregate_only_metrics(result.evaluation_meta.get("metrics") or {}),
    )
    written["sampling_distribution.json"] = dump(
        "sampling_distribution.json",
        result.evaluation_meta.get("sampling")
        or sampling_distribution_report([]),
    )

    # Distinct artifacts — never a convenience file recombining blind + sealed.
    blind = list(result.human_review_packet)
    sealed = list(result.sealed_scoring_manifest)
    operational = list(result.operational_evaluation_records)
    assert_blind_sealed_operational_record_id_join(blind, sealed, operational)
    assert_human_packet_prediction_blind(
        {"schema": "pr5d_human_review_packet_v1", "records": blind},
        context="human_review_packet_prepublish",
    )

    human_path = safe / "human_review_packet.json"
    human_path.write_text(
        json.dumps(
            {
                "schema": "pr5d_human_review_packet_v1",
                "distributable": True,
                "prediction_isolated": True,
                "records": blind,
            },
            indent=2,
            sort_keys=True,
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )
    written["human_review_packet.json"] = str(human_path)

    sealed_path = safe / "sealed_scoring_manifest.json"
    sealed_path.write_text(
        json.dumps(
            {
                "schema": "pr5d_sealed_scoring_manifest_v1",
                "sealed": True,
                "not_for_human_reviewers": True,
                "records": sealed,
            },
            indent=2,
            sort_keys=True,
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )
    written["sealed_scoring_manifest.json"] = str(sealed_path)

    ops_path = safe / "operational_evaluation_records.json"
    ops_path.write_text(
        json.dumps(
            {
                "schema": "pr5d_operational_evaluation_records_v1",
                "sealed": True,
                "not_for_human_reviewers": True,
                "records": operational,
            },
            indent=2,
            sort_keys=True,
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )
    written["operational_evaluation_records.json"] = str(ops_path)

    written["summary.json"] = dump("summary.json", result.to_summary_dict())
    return written


def write_relevance_outputs(
    result: ProductRelevancePlanResult,
    out_dir: Path,
    *,
    repo_email_pipeline_root: Path | None = None,
    require_git_ignored: bool = True,
    include_walkthrough: bool = True,
) -> dict[str, str]:
    """Publish plan (+ optional walkthrough) as one atomic staged bundle."""
    root = repo_email_pipeline_root or _email_pipeline_root()
    walkthrough_bundle = build_cases_a_e(result) if include_walkthrough else None

    def _write(safe: Path) -> dict[str, str]:
        written = _write_plan_files(safe, result)
        if walkthrough_bundle is not None:
            walk_dir = safe / "walkthrough"
            walk_written = write_walkthrough_into(walkthrough_bundle, walk_dir)
            for k, v in walk_written.items():
                written[f"walkthrough/{k}"] = v
        return written

    return write_atomically(
        out_dir,
        repo_email_pipeline_root=root,
        writer=_write,
        require_git_ignored=require_git_ignored,
    )


__all__ = [
    "RelevanceReconciliationError",
    "ReportOutputError",
    "build_product_relevance_plan",
    "reconcile_relevance",
    "require_reports_out_dir",
    "write_relevance_outputs",
    "write_human_review_packet",
    "write_sealed_scoring_manifest",
    "write_operational_evaluation_records",
]
