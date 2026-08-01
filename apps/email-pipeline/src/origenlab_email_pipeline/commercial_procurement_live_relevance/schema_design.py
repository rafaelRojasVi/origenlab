"""Proposed PR5 additive read-model contract (design only — no DDL applied)."""

from __future__ import annotations

from typing import Any

from origenlab_email_pipeline.commercial_procurement_live_relevance.constants import (
    PROPOSED_SCHEMA_VERSION,
)


def proposed_schema_document() -> dict[str, Any]:
    """Return the proposed namespace. Names are candidates, not final."""
    return {
        "schema_version": PROPOSED_SCHEMA_VERSION,
        "status": "design_only_not_created",
        "evidence_planes": {
            "plane_a_pr4": {
                "name": "persisted_pr4_evidence",
                "description": (
                    "Historical/file-backed procurement evidence truth in "
                    "commercial_procurement_*. Stable procurement_id and PR4 "
                    "account resolution when present."
                ),
                "not": "sole freshness source for live-only tenders",
            },
            "plane_b_live_snapshot": {
                "name": "versioned_live_acquisition_snapshot",
                "description": (
                    "Official API/OCDS source observations with immutable snapshot "
                    "id + fingerprint, tender/line source ids, query + acquisition "
                    "timestamp. Does not assume the tender already exists in PR4."
                ),
            },
            "coalescence": (
                "PR5 candidates consume either or both planes under deterministic "
                "coalescence_status rules."
            ),
        },
        "dependency": {
            "pr4": "Plane A — persisted historical/file-backed evidence",
            "live_snapshot": "Plane B — freshness / acquisition plane",
            "pr2": "logical account/contact links only; no physical FK required in v1",
        },
        "tables": [
            {
                "name": "commercial_procurement_candidate",
                "grain": "one row per canonical tender (aligned with PR4 tender key)",
                "primary_key": "candidate_id",
                "foreign_keys": [
                    "nullable logical pr4_procurement_id → commercial_procurement_signal",
                    "optional logical account_id → commercial_identity_account",
                ],
                "rebuildable": True,
                "key_columns": [
                    "canonical_tender_key",
                    "tender_key_kind",
                    "candidate_source_kind",  # pr4 | live_snapshot | both
                    "pr4_procurement_id",  # nullable
                    "live_source_snapshot_id",
                    "live_source_observation_id",
                    "acquisition_source_kind",
                    "acquisition_fingerprint",
                    "coalescence_status",
                    "source_precedence_reason",
                    "status_date_conflict_status",
                    "active_status_class",
                    "closing_soon_bucket",
                    "relevance_class",
                    "relevance_confidence",
                    "not_eligible_reason",
                    "equipment_class",
                    "product_resolution_status",
                    "candidate_outcome_state",
                    "operator_review_status",
                    "as_of_america_santiago",
                    "classifier_versions_json",
                ],
                "indexes": [
                    "(canonical_tender_key)",
                    "(active_status_class, relevance_class)",
                    "(candidate_outcome_state)",
                    "(candidate_source_kind)",
                    "(account_id)",
                ],
                "checks": [
                    "active_status_class IN known set (no active_closing_soon)",
                    "closing_soon_bucket IN known set including not_applicable",
                    "relevance_class IN known set",
                    "candidate_outcome_state IN known set including account_resolution_required",
                    "candidate_source_kind IN (pr4, live_snapshot, both)",
                    "exact_catalog_product requires product_resolution_status=exact_match",
                ],
                "semantic_fingerprint": True,
            },
            {
                "name": "commercial_procurement_line_relevance",
                "grain": "one row per tender line observation contributing to relevance",
                "primary_key": "line_relevance_id",
                "foreign_keys": ["candidate_id → commercial_procurement_candidate"],
                "rebuildable": True,
                "key_columns": [
                    "source_record_id",
                    "line_identifier",
                    "matched_spans_json",
                    "positive_rules_json",
                    "negative_rules_json",
                    "equipment_class",
                    "product_candidate_ids_json",
                    "relevance_class",
                    "confidence",
                ],
                "indexes": ["(candidate_id)", "(source_record_id)"],
                "checks": ["relevance_class IN known set"],
                "semantic_fingerprint": True,
            },
            {
                "name": "commercial_procurement_contact_resolution",
                "grain": "exactly one summary row per candidate",
                "primary_key": "contact_resolution_id",
                "foreign_keys": ["candidate_id → commercial_procurement_candidate UNIQUE"],
                "rebuildable": True,
                "key_columns": [
                    "final_contact_status",
                    "search_stages_completed_json",
                    "next_action",
                    "reason_code",
                    "considered_contact_count",
                ],
                "indexes": ["(candidate_id UNIQUE)", "(final_contact_status)"],
                "checks": [
                    "exactly one row per candidate",
                    "final_contact_status IN known set (includes no_contact_found)",
                ],
                "semantic_fingerprint": True,
                "notes": (
                    "Represents the no-contact decision as a summary row. "
                    "Individual contacts live in commercial_procurement_contact_candidate."
                ),
            },
            {
                "name": "commercial_procurement_contact_candidate",
                "grain": "zero or more considered contacts per candidate",
                "primary_key": "contact_candidate_id",
                "foreign_keys": [
                    "candidate_id → commercial_procurement_candidate",
                    "contact_resolution_id → commercial_procurement_contact_resolution",
                ],
                "rebuildable": True,
                "key_columns": [
                    "contact_evidence_source",
                    "role_or_department",
                    "role_suitability",
                    "confidence",
                    "suppression_result",
                    "outreach_state_result",
                    "verification_date",
                    "logical_contact_id",
                ],
                "indexes": ["(candidate_id)", "(contact_resolution_id)"],
                "checks": [],
                "semantic_fingerprint": True,
            },
            {
                "name": "commercial_procurement_candidate_evidence",
                "grain": "provenance pointer rows for candidate / line / contact decisions",
                "primary_key": "evidence_id",
                "foreign_keys": ["subject_id polymorphic"],
                "rebuildable": True,
                "key_columns": [
                    "subject_kind",
                    "subject_id",
                    "source_table",
                    "source_record_id",
                    "reason_code",
                ],
                "indexes": ["(subject_kind, subject_id)", "(source_table, source_record_id)"],
                "checks": ["subject_kind IN known set"],
                "semantic_fingerprint": True,
                "notes": (
                    "Routine negative relevance (consumable/service/rental/unrelated) "
                    "emits evidence + not_eligible_reason on the candidate — not a conflict."
                ),
            },
            {
                "name": "commercial_procurement_candidate_conflict",
                "grain": "contradictory or unresolved evidence only",
                "primary_key": "conflict_id",
                "foreign_keys": ["optional candidate_id"],
                "rebuildable": True,
                "key_columns": [
                    "conflict_kind",
                    "reason_code",
                    "detail_json",
                    "created_at",
                ],
                "indexes": ["(candidate_id)", "(conflict_kind)"],
                "checks": [
                    "conflict_kind IN ("
                    "strong_equipment_vs_consumable_unresolved, "
                    "status_conflict, date_conflict, "
                    "incompatible_equipment_classes, "
                    "ambiguous_exact_product_aliases, "
                    "buyer_identity_conflict"
                    ")"
                ],
                "semantic_fingerprint": "exclude created_at from semantic digest",
                "do_not_emit_for": list(
                    sorted(
                        {
                            "consumable_or_reagent",
                            "service_or_maintenance_only",
                            "rental_or_comodato",
                            "non_laboratory_false_positive",
                            "unrelated",
                        }
                    )
                ),
            },
            {
                "name": "commercial_procurement_candidate_build_meta",
                "grain": "key/value build contract + fingerprints",
                "primary_key": "meta_key",
                "foreign_keys": [],
                "rebuildable": True,
                "key_columns": ["meta_key", "meta_value"],
                "indexes": [],
                "checks": [],
                "semantic_fingerprint": False,
            },
        ],
        "coalescence_rules": {
            "exact_agreement": "Same canonical tender; status/dates/buyer agree",
            "live_source_newer": "Both present; live snapshot wins freshness fields",
            "pr4_only": "Present only in Plane A",
            "live_only": "Present only in Plane B (nullable pr4_procurement_id)",
            "status_conflict": "Disagree on lifecycle status",
            "date_conflict": "Disagree on publication/close dates",
            "buyer_identity_conflict": "Disagree on buyer identity plane",
        },
        "fingerprints": {
            "acquisition_source_snapshot_fingerprint": "hash of live acquisition payload ids+status+dates",
            "pr4_dependency_fingerprint": "PR4 source+semantic digests used as inputs",
            "pr2_dependency_fingerprint": "identity_fp_v2 from PR2 build meta",
            "product_taxonomy_fingerprint": "hash of canonical classes + alias map version",
            "relevance_plan_fingerprint": "hash of classifier version + rule pack",
            "semantic_plan_digest": (
                "order-independent hash of candidate/line/contact_resolution/"
                "contact_candidate/evidence/conflict rows excluding build_meta and "
                "wall-clock conflict.created_at"
            ),
        },
        "stale_plan": {
            "on_apply": "recompute plan; require --expected-* digests; abort on mismatch",
            "pr5a": "no apply implementation",
        },
        "implementation_sequence": [
            "PR5A — corrected design/audit",
            "PR5B — acquisition source contract, ticket/OCDS parsers and captured fixtures",
            "PR5C — deterministic candidate planner",
            "PR5D — additive persistence and gated apply",
            "PR5E — production acquisition scheduling and refresh operations",
            "PR6 — targeted external contact enrichment",
            "PR7 — API/dashboard exposure",
        ],
    }
