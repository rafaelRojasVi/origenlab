"""Reconcile annex-backed planning without changing canonical queue schemas."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.chilecompra_anexo_evidence.redaction import (
    assert_no_portal_tokens,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.planner import (
    InstitutionProspectPlanResult,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.queues import (
    current_opportunity_blockers,
)

from .augment import AnexoAugmentationResult, AnnexUnitBinding
from .constants import ANEXO_INTEGRATION_VERSION


_TEMPORAL_BLOCKERS = frozenset({"lifecycle_not_active_open", "close_timestamp_elapsed"})


def _tender_code(row: dict[str, Any]) -> str:
    return str(row.get("tender_code") or "").strip()


def _category(row: dict[str, Any]) -> str:
    return str(
        row.get("canonical_equipment_category") or row.get("equipment_category") or ""
    ).strip()


def _line_evidence_counts(result: InstitutionProspectPlanResult) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for row in result.line_rows:
        if row.get("line_disposition") == "excluded":
            continue
        tender_id = str(row.get("coalesced_tender_id") or "")
        counts[tender_id] += 1
    return dict(counts)


def _eligibility_rows(
    result: InstitutionProspectPlanResult,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Rows passing the existing queue gate after removing chronology only.

    The queue policy itself is not modified or reimplemented: this calls
    ``current_opportunity_blockers`` and removes only its two explicit temporal
    blockers for a separately named counterfactual eligibility oracle.
    """
    evidence_counts = _line_evidence_counts(result)
    eligible: dict[tuple[str, str], dict[str, Any]] = {}
    for row in result.tender_rows:
        tender_id = str(row.get("coalesced_tender_id") or "")
        blockers = current_opportunity_blockers(
            row,
            evidence_counts.get(tender_id, 0),
            as_of_utc=result.as_of_utc,
        )
        non_temporal = [b for b in blockers if b not in _TEMPORAL_BLOCKERS]
        if non_temporal:
            continue
        key = (_tender_code(row).casefold(), _category(row))
        eligible[key] = {
            **row,
            "current_opportunity_blockers": blockers,
            "annex_queue_eligible_except_temporal_gate": True,
        }
    return eligible


def _queue_index(
    result: InstitutionProspectPlanResult, queue_name: str
) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("queue_row_id") or ""): row
        for row in result.operator_queues.get(queue_name, [])
    }


def _claims_by_key(
    augmentation: AnexoAugmentationResult,
) -> dict[tuple[str, str], list[Any]]:
    claims: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for claim in augmentation.claims:
        claims[(claim.tender_id.casefold(), claim.matched_category)].append(claim)
    return claims


def _bindings_by_source_tender(
    augmentation: AnexoAugmentationResult,
) -> dict[str, list[AnnexUnitBinding]]:
    out: dict[str, list[AnnexUnitBinding]] = defaultdict(list)
    for binding in augmentation.bindings:
        out[binding.tender_id.casefold()].append(binding)
    return out


def _changed_profile_ids(
    baseline: InstitutionProspectPlanResult,
    enabled: InstitutionProspectPlanResult,
) -> list[str]:
    def index(result: InstitutionProspectPlanResult) -> dict[str, str]:
        return {
            str(profile.get("institution_id") or ""): canonical_json_digest(profile)
            for profile in result.profiles
        }

    before = index(baseline)
    after = index(enabled)
    return sorted(
        key for key in before.keys() | after.keys() if before.get(key) != after.get(key)
    )


def _changed_contact_gap_ids(
    baseline: InstitutionProspectPlanResult,
    enabled: InstitutionProspectPlanResult,
) -> list[str]:
    before = {
        str(row.get("institution_id") or ""): canonical_json_digest(row)
        for row in baseline.contact_gap_queue
    }
    after = {
        str(row.get("institution_id") or ""): canonical_json_digest(row)
        for row in enabled.contact_gap_queue
    }
    return sorted(
        key for key in before.keys() | after.keys() if before.get(key) != after.get(key)
    )


def build_annex_integration_audit(
    *,
    augmentation: AnexoAugmentationResult,
    baseline: InstitutionProspectPlanResult,
    enabled: InstitutionProspectPlanResult,
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    """Build enabled-only reconciliation and provenance sidecar rows."""
    baseline_current = _queue_index(baseline, "current_opportunity_queue")
    enabled_current = _queue_index(enabled, "current_opportunity_queue")
    added_queue_ids = sorted(enabled_current.keys() - baseline_current.keys())
    removed_queue_ids = sorted(baseline_current.keys() - enabled_current.keys())

    claims_by_key = _claims_by_key(augmentation)
    bindings_by_tender = _bindings_by_source_tender(augmentation)
    baseline_eligibility = _eligibility_rows(baseline)
    enabled_eligibility = _eligibility_rows(enabled)

    def source_tender_id(row: dict[str, Any]) -> str:
        claims = claims_by_key.get((_tender_code(row).casefold(), _category(row)))
        return claims[0].tender_id if claims else _tender_code(row)

    eligible_delta_keys = sorted(
        key
        for key in enabled_eligibility.keys() - baseline_eligibility.keys()
        if key in claims_by_key
    )
    eligible_ids = sorted(
        {claims_by_key[key][0].tender_id for key in eligible_delta_keys}
    )
    expired_eligible_ids = sorted(
        {
            claims_by_key[key][0].tender_id
            for key in eligible_delta_keys
            if set(enabled_eligibility[key]["current_opportunity_blockers"])
            & _TEMPORAL_BLOCKERS
        }
    )

    annex_backed_current: list[dict[str, Any]] = []
    for row in enabled_current.values():
        if (_tender_code(row).casefold(), _category(row)) in claims_by_key:
            annex_backed_current.append(row)

    out_of_scope: list[dict[str, Any]] = []
    evidence_counts = _line_evidence_counts(enabled)
    for row in enabled.tender_rows:
        key = (_tender_code(row).casefold(), _category(row))
        if key not in claims_by_key:
            continue
        blockers = current_opportunity_blockers(
            row,
            evidence_counts.get(str(row.get("coalesced_tender_id") or ""), 0),
            as_of_utc=enabled.as_of_utc,
        )
        catalog_blockers = sorted(
            blocker
            for blocker in blockers
            if blocker
            in {
                "disposition_not_catalog_eligible",
                "catalog_fit_status_not_actionable",
                "catalog_match_status_not_actionable",
            }
        )
        if (
            row.get("commercial_signal_type") == "equipment_purchase_signal"
            and catalog_blockers
        ):
            out_of_scope.append(
                {
                    "tender_id": claims_by_key[key][0].tender_id,
                    "equipment_category": _category(row),
                    "reason_codes": catalog_blockers,
                }
            )

    suppressed_non_acquisition: list[str] = []
    for tender_key, bindings in sorted(bindings_by_tender.items()):
        strong = any(
            binding.applied
            and binding.decision.relevance_class
            in {
                "exact_catalog_product",
                "strong_equipment_class",
                "compatible_equipment_class",
            }
            and bool(binding.decision.canonical_equipment_classes)
            for binding in bindings
        )
        if not strong:
            suppressed_non_acquisition.append(bindings[0].tender_id)
    suppressed_non_acquisition_set = set(suppressed_non_acquisition)
    out_of_scope = [
        row
        for row in out_of_scope
        if row["tender_id"] not in suppressed_non_acquisition_set
    ]

    coverage_debt_ids = sorted(
        tender_id
        for tender_id, coverage in augmentation.coverage_by_tender.items()
        if not coverage.is_complete
    )
    debt_outcomes: Counter[str] = Counter()
    for coverage in augmentation.coverage_by_tender.values():
        debt_outcomes.update(coverage.debt_outcome_counts)

    provenance_rows: list[dict[str, Any]] = []

    def provenance_record(
        *,
        record_kind: str,
        row: dict[str, Any],
        queue_row_id: str | None,
        entry_caused: bool,
    ) -> dict[str, Any]:
        key = (_tender_code(row).casefold(), _category(row))
        claims = sorted(claims_by_key[key], key=lambda claim: claim.claim_id)
        source_tender_id = claims[0].tender_id
        bindings = [
            binding
            for binding in bindings_by_tender[source_tender_id.casefold()]
            if _category(row) in binding.decision.canonical_equipment_classes
        ]
        coverage = augmentation.coverage_by_tender[source_tender_id]
        return {
            "record_kind": record_kind,
            "queue": "current_opportunity_queue" if queue_row_id else None,
            "queue_row_id": queue_row_id,
            "coalesced_tender_id": row.get("coalesced_tender_id"),
            "tender_id": source_tender_id,
            "institution_id": row.get("institution_id"),
            "equipment_category": _category(row),
            "annex_backed": True,
            "entry_caused_by_annex": entry_caused,
            "annex_queue_eligible_except_temporal_gate": record_kind
            == "annex_commercial_eligibility",
            "current_opportunity_blockers": list(
                row.get("current_opportunity_blockers") or []
            ),
            "annex_claim_ids": [claim.claim_id for claim in claims],
            "production_line_claim_ids": list(row.get("claim_ids") or []),
            "unit_ids": sorted({binding.unit.unit_id for binding in bindings}),
            "unit_decision_ids": sorted(
                {binding.decision.unit_decision_id for binding in bindings}
            ),
            "attachment_ids": sorted(
                {binding.segment.attachment_id for binding in bindings}
            ),
            "evidence_attachment_ids": sorted(
                {binding.segment.evidence_attachment_id for binding in bindings}
            ),
            "attachment_sha256s": sorted(
                {binding.segment.attachment_sha256 for binding in bindings}
            ),
            "chunk_ids": sorted({binding.segment.chunk_id for binding in bindings}),
            "segment_ids": sorted({binding.segment.segment_id for binding in bindings}),
            "locators": [
                {
                    "segment_id": binding.segment.segment_id,
                    "locator_type": binding.segment.locator_type,
                    "locator": dict(binding.segment.locator),
                    "segment_locator": dict(binding.segment.segment_locator),
                    "locator_display": binding.segment.locator_display,
                }
                for binding in sorted(
                    bindings, key=lambda item: item.segment.segment_id
                )
            ],
            "source_formats": sorted(
                {binding.segment.detected_format for binding in bindings}
            ),
            "coverage_status": "complete" if coverage.is_complete else "incomplete",
            "coverage_debt": dict(coverage.debt_outcome_counts),
            "coverage_incomplete_reason_codes": list(coverage.incomplete_reason_codes),
            "coverage_unread_attachment_ids": list(coverage.unread_attachment_ids),
            "tender_annex_semantic_digest": bindings[0].tender_semantic_digest,
            "annex_evidence_digest": augmentation.evidence_digest,
            "annex_integration_semantic_digest": augmentation.semantic_digest,
            "reason_codes": sorted(
                {
                    reason
                    for binding in bindings
                    for reason in (
                        *binding.decision.positive_reason_codes,
                        *binding.decision.negative_reason_codes,
                        *binding.decision.ambiguity_reason_codes,
                    )
                }
            ),
            "contact_authorization": False,
            "outreach_authorization": False,
            "persistent_state_mutated": False,
        }

    for row in sorted(
        annex_backed_current,
        key=lambda item: str(item.get("queue_row_id") or ""),
    ):
        queue_id = str(row.get("queue_row_id") or "")
        provenance_rows.append(
            provenance_record(
                record_kind="current_queue_row",
                row=row,
                queue_row_id=queue_id,
                entry_caused=queue_id in added_queue_ids,
            )
        )
    current_provenance_keys = {
        (row["tender_id"].casefold(), row["equipment_category"])
        for row in provenance_rows
    }
    for key in eligible_delta_keys:
        row = enabled_eligibility[key]
        if key in current_provenance_keys:
            continue
        provenance_rows.append(
            provenance_record(
                record_kind="annex_commercial_eligibility",
                row=row,
                queue_row_id=None,
                entry_caused=False,
            )
        )
    provenance_rows.sort(
        key=lambda row: (
            str(row.get("record_kind") or ""),
            str(row.get("queue_row_id") or ""),
            str(row.get("tender_id") or ""),
            str(row.get("equipment_category") or ""),
        )
    )

    baseline_queue_payload = {
        key: baseline.operator_queues.get(key, [])
        for key in sorted(baseline.operator_queues)
    }
    enabled_queue_payload = {
        key: enabled.operator_queues.get(key, [])
        for key in sorted(enabled.operator_queues)
    }
    profile_changes = _changed_profile_ids(baseline, enabled)
    contact_gap_changes = _changed_contact_gap_ids(baseline, enabled)
    reconciliation: dict[str, Any] = {
        "ok": True,
        "integration_contract_version": ANEXO_INTEGRATION_VERSION,
        "integration_mode": "enabled_local_verified",
        "annex_evidence_applied": any(
            binding.applied for binding in augmentation.bindings
        ),
        "annex_evidence_digest": augmentation.evidence_digest,
        "annex_integration_semantic_digest": augmentation.semantic_digest,
        "as_of_utc": enabled.as_of_utc,
        "planning_tender_count": int(enabled.counts.get("source_tenders") or 0),
        "annex_bundle_tender_count": len(augmentation.bound_tender_ids)
        + len(augmentation.unused_tender_ids),
        "annex_bound_tender_count": len(augmentation.bound_tender_ids),
        "annex_unused_tender_count": len(augmentation.unused_tender_ids),
        "annex_bound_tender_ids": list(augmentation.bound_tender_ids),
        "annex_unused_tender_ids": list(augmentation.unused_tender_ids),
        "annex_positive_claim_count": sum(
            claim.relevance_class
            in {
                "exact_catalog_product",
                "strong_equipment_class",
                "compatible_equipment_class",
            }
            for claim in augmentation.claims
        ),
        "annex_incomplete_tender_count": len(coverage_debt_ids),
        "annex_applied_unit_count": sum(
            binding.applied for binding in augmentation.bindings
        ),
        "annex_withheld_unit_count": sum(
            not binding.applied for binding in augmentation.bindings
        ),
        "coverage_debt_ids": coverage_debt_ids,
        "coverage_debt_outcome_counts": dict(sorted(debt_outcomes.items())),
        "current_enabled_queue_delta": {
            "baseline_count": len(baseline_current),
            "enabled_count": len(enabled_current),
            "annex_caused_entry_queue_row_ids": added_queue_ids,
            "annex_caused_entry_tender_ids": sorted(
                {source_tender_id(enabled_current[row_id]) for row_id in added_queue_ids}
            ),
            "annex_caused_removal_queue_row_ids": removed_queue_ids,
            "annex_caused_removal_tender_ids": sorted(
                {source_tender_id(baseline_current[row_id]) for row_id in removed_queue_ids}
            ),
        },
        "time_independent_annex_eligible_ids": eligible_ids,
        "expired_but_annex_eligible_ids": expired_eligible_ids,
        "operator_queue_rows_annex_backed": len(annex_backed_current),
        "operator_queue_entries_caused_by_annex": len(added_queue_ids),
        "operator_queue_removals_caused_by_annex": len(removed_queue_ids),
        "institution_profile_changed_ids": profile_changes,
        "contact_gap_changed_ids": contact_gap_changes,
        "out_of_scope_annex_acquisitions": sorted(
            out_of_scope,
            key=lambda row: (row["tender_id"], row["equipment_category"]),
        ),
        "suppressed_non_acquisition_ids": sorted(suppressed_non_acquisition),
        "dependencies": {
            "baseline_pr5d_semantic_digest": augmentation.baseline_plan.fingerprints.get(
                "semantic_digest"
            ),
            "augmented_pr5d_semantic_digest": augmentation.plan.fingerprints.get(
                "semantic_digest"
            ),
            "enabled_pr5e_semantic_digest": enabled.dependency_fingerprints.get(
                "pr5e_semantic_digest"
            ),
            "enabled_institution_semantic_digest": enabled.fingerprints.get(
                "semantic_digest"
            ),
        },
        "baseline_operator_queue_digest": canonical_json_digest(baseline_queue_payload),
        "enabled_operator_queue_digest": canonical_json_digest(enabled_queue_payload),
        "operator_plan_changed": baseline_queue_payload != enabled_queue_payload,
        "persistent_state_mutated": False,
        "sqlite_writes": False,
        "postgres_writes": False,
        "gmail_writes": False,
        "network_annex_acquisition": False,
        "contact_authorization": False,
        "outreach_authorization": False,
        "pr5f_started": False,
        "p2b_started": False,
        "current_opportunity_gate_unchanged": True,
    }
    reconciliation["semantic_digest"] = canonical_json_digest(reconciliation)

    serialized_reconciliation = json.dumps(
        reconciliation, ensure_ascii=False, sort_keys=True
    )
    serialized_provenance = "\n".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) for row in provenance_rows
    )
    assert_no_portal_tokens(
        serialized_reconciliation, where="annex integration reconciliation"
    )
    assert_no_portal_tokens(serialized_provenance, where="annex opportunity provenance")
    return reconciliation, tuple(provenance_rows)


def write_annex_integration_sidecars(
    dest: Path,
    *,
    reconciliation: dict[str, Any],
    provenance_rows: tuple[dict[str, Any], ...],
) -> dict[str, str]:
    """Write enabled-only sidecars inside the caller's atomic staging dir."""
    reconciliation_path = dest / "annex_integration_reconciliation.json"
    reconciliation_path.write_text(
        json.dumps(reconciliation, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    provenance_path = dest / "annex_opportunity_provenance.jsonl"
    provenance_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in provenance_rows
        ),
        encoding="utf-8",
    )
    return {
        reconciliation_path.name: str(reconciliation_path),
        provenance_path.name: str(provenance_path),
    }


__all__ = [
    "build_annex_integration_audit",
    "write_annex_integration_sidecars",
]
