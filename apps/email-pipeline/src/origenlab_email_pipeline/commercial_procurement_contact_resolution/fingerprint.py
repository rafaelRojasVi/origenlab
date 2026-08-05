"""PR5E fingerprints — input, rules, build, semantic digest."""

from __future__ import annotations

from typing import Iterable

from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
)
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
    ContactResolutionSummary,
    OrganizationResolution,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.policy import (
    contact_resolution_policy_spec,
)


def rules_fingerprint_payload() -> dict:
    return {
        "algorithm": CONTACT_RULES_FP_ALGORITHM,
        "rules_version": CONTACT_RESOLUTION_RULES_VERSION,
        "resolver_version": CONTACT_RESOLVER_VERSION,
        "policy": contact_resolution_policy_spec(),
    }


def contact_rules_fingerprint() -> str:
    return canonical_json_digest(rules_fingerprint_payload())


def contact_input_fingerprint(
    *,
    pr5c_semantic_digest: str,
    pr5d_semantic_digest: str,
    identity_fingerprint: str,
    organization_resolutions: Iterable[OrganizationResolution],
) -> str:
    orgs = sorted(
        (
            {
                "organization_resolution_id": o.organization_resolution_id,
                "coalesced_tender_id": o.coalesced_tender_id,
                "resolution_status": o.resolution_status,
                "account_id": o.account_id,
                "link_route": o.link_route,
                "reason_code": o.reason_code,
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
            "organization_resolutions": orgs,
        }
    )


def contact_semantic_digest(
    *,
    summaries: Iterable[ContactResolutionSummary],
    candidates: Iterable[ContactCandidate],
) -> str:
    sum_rows = sorted(
        (
            {
                "contact_resolution_id": s.contact_resolution_id,
                "coalesced_tender_id": s.coalesced_tender_id,
                "final_contact_status": s.final_contact_status,
                "account_id": s.account_id,
                "selected_contact_id": s.selected_contact_id,
                "search_stages_completed": list(s.search_stages_completed),
                "next_action": s.next_action,
                "reason_code": s.reason_code,
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
                "contact_id": c.contact_id,
                "rank": c.rank,
                "ranking_tier": c.ranking_tier,
                "role_suitability": c.role_suitability,
                "selectable": c.selectable,
                "safety_blocked": c.safety_blocked,
            }
            for c in candidates
        ),
        key=lambda r: r["candidate_id"],
    )
    return canonical_json_digest(
        {
            "algorithm": CONTACT_SEMANTIC_DIGEST_ALGORITHM,
            "summaries": sum_rows,
            "candidates": cand_rows,
        }
    )


def contact_build_fingerprint(
    *,
    input_fingerprint: str,
    rules_fingerprint: str,
    semantic_digest: str,
    identity_fingerprint: str,
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
            "pr5c_semantic_digest": pr5c_semantic_digest,
            "pr5d_semantic_digest": pr5d_semantic_digest,
        }
    )


def all_fingerprints(
    *,
    pr5c_semantic_digest: str,
    pr5d_semantic_digest: str,
    identity_fingerprint: str,
    organization_resolutions: Iterable[OrganizationResolution],
    summaries: Iterable[ContactResolutionSummary],
    candidates: Iterable[ContactCandidate],
) -> dict[str, str]:
    rules_fp = contact_rules_fingerprint()
    input_fp = contact_input_fingerprint(
        pr5c_semantic_digest=pr5c_semantic_digest,
        pr5d_semantic_digest=pr5d_semantic_digest,
        identity_fingerprint=identity_fingerprint,
        organization_resolutions=organization_resolutions,
    )
    semantic = contact_semantic_digest(summaries=summaries, candidates=candidates)
    build = contact_build_fingerprint(
        input_fingerprint=input_fp,
        rules_fingerprint=rules_fp,
        semantic_digest=semantic,
        identity_fingerprint=identity_fingerprint,
        pr5c_semantic_digest=pr5c_semantic_digest,
        pr5d_semantic_digest=pr5d_semantic_digest,
    )
    return {
        "input_fingerprint": input_fp,
        "rules_fingerprint": rules_fp,
        "build_fingerprint": build,
        "semantic_digest": semantic,
    }
