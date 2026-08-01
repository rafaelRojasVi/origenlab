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
        "dependency": {
            "pr4": "commercial_procurement_* remains procurement evidence truth",
            "pr2": "logical account/contact links only; no physical FK required in v1",
            "sources": "rebuildable from PR4 + live acquisition snapshot + taxonomy fingerprint",
        },
        "tables": [
            {
                "name": "commercial_procurement_candidate",
                "grain": "one row per canonical tender (aligned with PR4 tender key)",
                "primary_key": "candidate_id",
                "foreign_keys": [
                    "logical procurement_id → commercial_procurement_signal.procurement_id",
                    "optional logical account_id → commercial_identity_account.account_id",
                ],
                "rebuildable": True,
                "key_columns": [
                    "canonical_tender_key",
                    "tender_key_kind",
                    "active_status_class",
                    "closing_soon_bucket",
                    "relevance_class",
                    "relevance_confidence",
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
                    "(account_id)",
                ],
                "checks": [
                    "active_status_class IN known set",
                    "relevance_class IN known set",
                    "candidate_outcome_state IN known set",
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
                "grain": "one row per candidate contact decision (0..n contacts)",
                "primary_key": "contact_resolution_id",
                "foreign_keys": [
                    "candidate_id → commercial_procurement_candidate",
                    "optional logical contact_id / account_id",
                ],
                "rebuildable": True,
                "key_columns": [
                    "contact_status",
                    "contact_evidence_source",
                    "role_suitability",
                    "confidence",
                    "suppression_result",
                    "outreach_state_result",
                    "verification_date",
                ],
                "indexes": ["(candidate_id)", "(contact_status)"],
                "checks": ["contact_status IN known set"],
                "semantic_fingerprint": True,
            },
            {
                "name": "commercial_procurement_candidate_evidence",
                "grain": "provenance pointer rows for candidate / line / contact decisions",
                "primary_key": "evidence_id",
                "foreign_keys": ["subject_id polymorphic to candidate/line/contact"],
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
            },
            {
                "name": "commercial_procurement_candidate_conflict",
                "grain": "active/status/date/relevance/contact conflicts",
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
                "checks": ["conflict_kind IN known set"],
                "semantic_fingerprint": "exclude created_at from semantic digest",
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
        "fingerprints": {
            "acquisition_source_snapshot_fingerprint": "hash of live acquisition payload ids+status+dates",
            "pr4_dependency_fingerprint": "PR4 source+semantic digests used as inputs",
            "pr2_dependency_fingerprint": "identity_fp_v2 from PR2 build meta",
            "product_taxonomy_fingerprint": "hash of canonical classes + alias map version",
            "relevance_plan_fingerprint": "hash of classifier version + rule pack",
            "semantic_plan_digest": "order-independent hash of candidate/line/contact/evidence/conflict rows excluding build_meta and wall-clock conflict.created_at",
        },
        "stale_plan": {
            "on_apply": "recompute plan; require --expected-* digests; abort on mismatch (no blind retry)",
            "pr5a": "no apply implementation",
        },
    }
