"""Immutable PR5E organization/contact-resolution models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class OrganizationResolution:
    organization_resolution_id: str
    coalesced_tender_id: str
    relevance_decision_id: str
    resolution_status: str
    resolution_source: str
    account_id: str | None
    link_route: str | None
    reason_code: str
    candidate_account_ids: tuple[str, ...]
    evidence_ref_ids: tuple[str, ...]
    pr4_procurement_ids: tuple[str, ...]
    pr4_resolution_ids: tuple[str, ...]
    buyer_field_sufficiency: str
    identity_fingerprint: str
    not_persisted: bool = True

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["candidate_account_ids"] = list(self.candidate_account_ids)
        d["evidence_ref_ids"] = list(self.evidence_ref_ids)
        d["pr4_procurement_ids"] = list(self.pr4_procurement_ids)
        d["pr4_resolution_ids"] = list(self.pr4_resolution_ids)
        return d


@dataclass(frozen=True)
class ContactResolutionEvidence:
    evidence_id: str
    subject_kind: str
    subject_id: str
    source_table: str
    source_record_id: str
    source_plane: str
    origin_plane: str
    evidence_type: str
    evidence_at: str
    matching_reason_code: str
    confidence: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ContactCandidate:
    candidate_id: str
    contact_resolution_id: str
    coalesced_tender_id: str
    account_id: str
    contact_id: str
    rank: int
    ranking_tier: str
    role_raw_digest: str
    role_suitability: str
    identity_status: str
    identity_confidence: str
    has_usable_email: bool
    verification_status: str
    evidence_ids: tuple[str, ...]
    suppression_result: str
    outreach_state_result: str
    safety_blocked: bool
    safety_unknown: bool
    selectable: bool
    ranking_reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["evidence_ids"] = list(self.evidence_ids)
        d["ranking_reason_codes"] = list(self.ranking_reason_codes)
        return d


@dataclass(frozen=True)
class ContactResolutionConflict:
    conflict_id: str
    coalesced_tender_id: str
    conflict_type: str
    reason_code: str
    subject_keys: tuple[str, ...]
    evidence_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["subject_keys"] = list(self.subject_keys)
        d["evidence_ids"] = list(self.evidence_ids)
        return d


@dataclass(frozen=True)
class ContactResolutionSummary:
    contact_resolution_id: str
    coalesced_tender_id: str
    relevance_decision_id: str
    organization_resolution_id: str
    account_id: str | None
    final_contact_status: str
    selected_contact_id: str | None
    selected_candidate_id: str | None
    search_stages_completed: tuple[str, ...]
    next_action: str
    reason_code: str
    considered_contact_count: int
    suitable_contact_count: int
    blocked_contact_count: int
    relevance_class_echo: str
    lifecycle_class_echo: str
    input_fingerprint: str
    semantic_fingerprint: str
    rules_version: str
    resolver_version: str
    not_persisted: bool = True

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["search_stages_completed"] = list(self.search_stages_completed)
        return d


@dataclass(frozen=True)
class ContactResolutionPlanResult:
    as_of_utc: str
    run_context: str
    planner_version: str
    organization_resolutions: tuple[OrganizationResolution, ...]
    contact_summaries: tuple[ContactResolutionSummary, ...]
    contact_candidates: tuple[ContactCandidate, ...]
    evidence: tuple[ContactResolutionEvidence, ...]
    conflicts: tuple[ContactResolutionConflict, ...]
    reconciliation: dict[str, Any]
    fingerprints: dict[str, str]
    dependency_fingerprints: dict[str, str]
    counts: dict[str, Any]
    field_sufficiency_audit: dict[str, Any]
    walkthrough: dict[str, Any] = field(default_factory=dict)
    not_persisted: bool = True

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "ok": bool(self.reconciliation.get("ok")),
            "as_of_utc": self.as_of_utc,
            "run_context": self.run_context,
            "planner_version": self.planner_version,
            "not_persisted": True,
            "counts": self.counts,
            "fingerprints": self.fingerprints,
            "dependency_fingerprints": self.dependency_fingerprints,
            "reconciliation": {
                "ok": self.reconciliation.get("ok"),
                "equations": self.reconciliation.get("equations"),
            },
            "field_sufficiency_audit": self.field_sufficiency_audit,
        }
