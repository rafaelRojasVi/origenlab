"""PR5E fingerprints — input, rules, build, semantic digest (PII-safe)."""

from __future__ import annotations

import hashlib
from typing import Iterable

from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution import policy as policy_mod
from origenlab_email_pipeline.commercial_procurement_contact_resolution.constants import (
    CONTACT_BUILD_FP_ALGORITHM,
    CONTACT_INPUT_FP_ALGORITHM,
    CONTACT_RESOLUTION_PLANNER_VERSION,
    CONTACT_RESOLUTION_RULES_VERSION,
    CONTACT_RESOLVER_VERSION,
    CONTACT_RULES_FP_ALGORITHM,
    CONTACT_SEMANTIC_DIGEST_ALGORITHM,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.models import (
    ContactCandidate,
    ContactResolutionConflict,
    ContactResolutionEvidence,
    ContactResolutionSummary,
    OrganizationResolution,
)


def _tok(value: str | None) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def rules_fingerprint_payload() -> dict:
    return {
        "algorithm": CONTACT_RULES_FP_ALGORITHM,
        "rules_version": CONTACT_RESOLUTION_RULES_VERSION,
        "resolver_version": CONTACT_RESOLVER_VERSION,
        "policy": policy_mod.contact_resolution_policy_spec(),
    }


def contact_rules_fingerprint() -> str:
    return canonical_json_digest(rules_fingerprint_payload())


def contact_input_fingerprint(
    *,
    pr5c_semantic_digest: str,
    pr5d_semantic_digest: str,
    identity_fingerprint: str,
    safety_fingerprint: str,
    frozen_source_fingerprint: str,
    organization_resolutions: Iterable[OrganizationResolution],
    pr4_resolution_ids: Iterable[str],
    pr2_contact_ids: Iterable[str],
    pr2_evidence_ids: Iterable[str],
) -> str:
    orgs = sorted(
        (
            {
                "organization_resolution_id": o.organization_resolution_id,
                "coalesced_tender_id": o.coalesced_tender_id,
                "relevance_decision_id": o.relevance_decision_id,
                "resolution_status": o.resolution_status,
                "resolution_source": o.resolution_source,
                "account_id_token": _tok(o.account_id),
                "link_route": o.link_route,
                "reason_code": o.reason_code,
                "candidate_account_id_tokens": sorted(
                    _tok(a) for a in o.candidate_account_ids
                ),
                "pr4_procurement_id_tokens": sorted(
                    _tok(p) for p in o.pr4_procurement_ids
                ),
                "pr4_resolution_id_tokens": sorted(
                    _tok(r) for r in o.pr4_resolution_ids
                ),
                "buyer_field_sufficiency": o.buyer_field_sufficiency,
                "identity_fingerprint": o.identity_fingerprint,
            }
            for o in organization_resolutions
        ),
        key=lambda r: r["organization_resolution_id"],
    )
    return canonical_json_digest(
        {
            "algorithm": CONTACT_INPUT_FP_ALGORITHM,
            "planner_version": CONTACT_RESOLUTION_PLANNER_VERSION,
            "pr5c_semantic_digest": pr5c_semantic_digest,
            "pr5d_semantic_digest": pr5d_semantic_digest,
            "identity_fingerprint": identity_fingerprint,
            "safety_fingerprint": safety_fingerprint,
            "frozen_source_fingerprint": frozen_source_fingerprint,
            "organization_resolutions": orgs,
            "pr4_resolution_id_tokens": sorted(_tok(x) for x in pr4_resolution_ids),
            "pr2_contact_id_tokens": sorted(_tok(x) for x in pr2_contact_ids),
            "pr2_evidence_id_tokens": sorted(_tok(x) for x in pr2_evidence_ids),
        }
    )


def contact_semantic_digest(
    *,
    summaries: Iterable[ContactResolutionSummary],
    candidates: Iterable[ContactCandidate],
    evidence: Iterable[ContactResolutionEvidence],
    conflicts: Iterable[ContactResolutionConflict],
) -> str:
    sum_rows = sorted(
        (
            {
                "contact_resolution_id": s.contact_resolution_id,
                "coalesced_tender_id": s.coalesced_tender_id,
                "relevance_decision_id": s.relevance_decision_id,
                "organization_resolution_id": s.organization_resolution_id,
                "final_contact_status": s.final_contact_status,
                "account_id_token": _tok(s.account_id),
                "selected_contact_id_token": _tok(s.selected_contact_id),
                "selected_candidate_id": s.selected_candidate_id,
                "search_stages_completed": list(s.search_stages_completed),
                "next_action": s.next_action,
                "reason_code": s.reason_code,
                "considered_contact_count": s.considered_contact_count,
                "suitable_contact_count": s.suitable_contact_count,
                "blocked_contact_count": s.blocked_contact_count,
                "relevance_class_echo": s.relevance_class_echo,
                "lifecycle_class_echo": s.lifecycle_class_echo,
                "currentness_class_echo": s.currentness_class_echo,
                "relevance_validation_status_echo": s.relevance_validation_status_echo,
            }
            for s in summaries
        ),
        key=lambda r: r["contact_resolution_id"],
    )
    cand_rows = sorted(
        (
            {
                "candidate_id": c.candidate_id,
                "contact_resolution_id": c.contact_resolution_id,
                "contact_id_token": _tok(c.contact_id),
                "account_id_token": _tok(c.account_id),
                "rank": c.rank,
                "ranking_tier": c.ranking_tier,
                "role_raw_digest": c.role_raw_digest,
                "role_suitability": c.role_suitability,
                "identity_status": c.identity_status,
                "identity_confidence": c.identity_confidence,
                "has_usable_email": c.has_usable_email,
                "verification_status": c.verification_status,
                "evidence_id_tokens": sorted(_tok(e) for e in c.evidence_ids),
                "suppression_result": c.suppression_result,
                "outreach_state_result": c.outreach_state_result,
                "safety_blocked": c.safety_blocked,
                "safety_unknown": c.safety_unknown,
                "selectable": c.selectable,
                "ranking_reason_codes": list(c.ranking_reason_codes),
            }
            for c in candidates
        ),
        key=lambda r: r["candidate_id"],
    )
    ev_rows = sorted(
        (
            {
                "evidence_id_token": _tok(e.evidence_id),
                "subject_kind": e.subject_kind,
                "subject_id_token": _tok(e.subject_id),
                "source_table": e.source_table,
                "source_record_id_token": _tok(e.source_record_id),
                "source_plane": e.source_plane,
                "origin_plane": e.origin_plane,
                "evidence_type": e.evidence_type,
                "evidence_at_token": _tok(e.evidence_at),
                "matching_reason_code": e.matching_reason_code,
                "confidence": e.confidence,
            }
            for e in evidence
        ),
        key=lambda r: r["evidence_id_token"],
    )
    conf_rows = sorted(
        (
            {
                "conflict_id": c.conflict_id,
                "coalesced_tender_id": c.coalesced_tender_id,
                "conflict_type": c.conflict_type,
                "reason_code": c.reason_code,
                "subject_key_tokens": sorted(_tok(s) for s in c.subject_keys),
                "evidence_id_tokens": sorted(_tok(e) for e in c.evidence_ids),
            }
            for c in conflicts
        ),
        key=lambda r: r["conflict_id"],
    )
    return canonical_json_digest(
        {
            "algorithm": CONTACT_SEMANTIC_DIGEST_ALGORITHM,
            "summaries": sum_rows,
            "candidates": cand_rows,
            "evidence": ev_rows,
            "conflicts": conf_rows,
        }
    )


def contact_build_fingerprint(
    *,
    input_fingerprint: str,
    rules_fingerprint: str,
    semantic_digest: str,
    identity_fingerprint: str,
    safety_fingerprint: str,
    pr5c_semantic_digest: str,
    pr5d_semantic_digest: str,
) -> str:
    return canonical_json_digest(
        {
            "algorithm": CONTACT_BUILD_FP_ALGORITHM,
            "planner_version": CONTACT_RESOLUTION_PLANNER_VERSION,
            "input_fingerprint": input_fingerprint,
            "rules_fingerprint": rules_fingerprint,
            "semantic_digest": semantic_digest,
            "identity_fingerprint": identity_fingerprint,
            "safety_fingerprint": safety_fingerprint,
            "pr5c_semantic_digest": pr5c_semantic_digest,
            "pr5d_semantic_digest": pr5d_semantic_digest,
        }
    )


def all_fingerprints(
    *,
    pr5c_semantic_digest: str,
    pr5d_semantic_digest: str,
    identity_fingerprint: str,
    safety_fingerprint: str,
    frozen_source_fingerprint: str,
    organization_resolutions: Iterable[OrganizationResolution],
    summaries: Iterable[ContactResolutionSummary],
    candidates: Iterable[ContactCandidate],
    evidence: Iterable[ContactResolutionEvidence],
    conflicts: Iterable[ContactResolutionConflict],
    pr4_resolution_ids: Iterable[str],
    pr2_contact_ids: Iterable[str],
    pr2_evidence_ids: Iterable[str],
) -> dict[str, str]:
    rules_fp = contact_rules_fingerprint()
    input_fp = contact_input_fingerprint(
        pr5c_semantic_digest=pr5c_semantic_digest,
        pr5d_semantic_digest=pr5d_semantic_digest,
        identity_fingerprint=identity_fingerprint,
        safety_fingerprint=safety_fingerprint,
        frozen_source_fingerprint=frozen_source_fingerprint,
        organization_resolutions=organization_resolutions,
        pr4_resolution_ids=pr4_resolution_ids,
        pr2_contact_ids=pr2_contact_ids,
        pr2_evidence_ids=pr2_evidence_ids,
    )
    semantic = contact_semantic_digest(
        summaries=summaries,
        candidates=candidates,
        evidence=evidence,
        conflicts=conflicts,
    )
    build = contact_build_fingerprint(
        input_fingerprint=input_fp,
        rules_fingerprint=rules_fp,
        semantic_digest=semantic,
        identity_fingerprint=identity_fingerprint,
        safety_fingerprint=safety_fingerprint,
        pr5c_semantic_digest=pr5c_semantic_digest,
        pr5d_semantic_digest=pr5d_semantic_digest,
    )
    return {
        "input_fingerprint": input_fp,
        "rules_fingerprint": rules_fp,
        "build_fingerprint": build,
        "semantic_digest": semantic,
        "safety_fingerprint": safety_fingerprint,
        "frozen_source_fingerprint": frozen_source_fingerprint,
    }
