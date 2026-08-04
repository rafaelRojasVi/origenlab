"""PR5D fingerprints — inputs, taxonomy, rules, build, semantic."""

from __future__ import annotations

from typing import Iterable

from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.constants import (
    PRODUCT_RELEVANCE_PLANNER_VERSION,
    PRODUCT_RELEVANCE_RULES_VERSION,
    PRODUCT_RELEVANCE_TAXONOMY_VERSION,
    PRODUCT_TEXT_ADAPTER_VERSION,
    RELEVANCE_BUILD_FP_ALGORITHM,
    RELEVANCE_INPUT_FP_ALGORITHM,
    RELEVANCE_RULES_FP_ALGORITHM,
    RELEVANCE_SEMANTIC_DIGEST_ALGORITHM,
    RELEVANCE_TAXONOMY_FP_ALGORITHM,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
    EvidenceUnitRelevanceDecision,
    ProductTextUnit,
    TenderRelevanceDecision,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.rules import (
    rules_fingerprint_payload,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.semantic_payload import (
    tender_semantic_payload_from_decision,
    unit_semantic_payload_from_decision,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.taxonomy_extensions import (
    taxonomy_fingerprint_payload,
)


def relevance_input_fingerprint(
    *,
    pr5c_semantic_digest: str,
    linked_units: Iterable[ProductTextUnit],
    unresolved_units: Iterable[ProductTextUnit],
) -> str:
    linked = sorted(
        (
            {
                "unit_id": u.unit_id,
                "coalesced_tender_id": u.coalesced_tender_id,
                "text_normalized": u.text_normalized,
                "field_path": u.field_path,
                "evidence_tier": u.evidence_tier,
            }
            for u in linked_units
        ),
        key=lambda r: r["unit_id"],
    )
    unresolved = sorted(
        (
            {
                "unit_id": u.unit_id,
                "coalesced_tender_id": u.coalesced_tender_id,
                "link_status": u.link_status,
                "unresolved_reason": u.unresolved_reason,
            }
            for u in unresolved_units
        ),
        key=lambda r: r["unit_id"],
    )
    payload = {
        "algorithm": RELEVANCE_INPUT_FP_ALGORITHM,
        "adapter_version": PRODUCT_TEXT_ADAPTER_VERSION,
        "pr5c_semantic_digest": pr5c_semantic_digest,
        "linked_units": linked,
        "unresolved_units": unresolved,
    }
    return canonical_json_digest(payload)


def relevance_taxonomy_fingerprint() -> str:
    payload = {
        "algorithm": RELEVANCE_TAXONOMY_FP_ALGORITHM,
        **taxonomy_fingerprint_payload(),
    }
    return canonical_json_digest(payload)


def relevance_rules_fingerprint() -> str:
    payload = {
        "algorithm": RELEVANCE_RULES_FP_ALGORITHM,
        **rules_fingerprint_payload(),
    }
    return canonical_json_digest(payload)


def relevance_build_fingerprint(
    *,
    input_fingerprint: str,
    taxonomy_fingerprint: str,
    rules_fingerprint: str,
) -> str:
    payload = {
        "algorithm": RELEVANCE_BUILD_FP_ALGORITHM,
        "planner_version": PRODUCT_RELEVANCE_PLANNER_VERSION,
        "taxonomy_version": PRODUCT_RELEVANCE_TAXONOMY_VERSION,
        "rules_version": PRODUCT_RELEVANCE_RULES_VERSION,
        "input_fingerprint": input_fingerprint,
        "taxonomy_fingerprint": taxonomy_fingerprint,
        "rules_fingerprint": rules_fingerprint,
    }
    return canonical_json_digest(payload)


def relevance_semantic_digest(
    *,
    tender_decisions: Iterable[TenderRelevanceDecision],
    unit_decisions: Iterable[EvidenceUnitRelevanceDecision],
) -> str:
    """Digest covering every material classification field (no raw matched text)."""
    tenders = sorted(
        (
            {
                "decision_id": d.decision_id,
                **tender_semantic_payload_from_decision(d),
            }
            for d in tender_decisions
        ),
        key=lambda r: r["decision_id"],
    )
    units = sorted(
        (
            {
                "unit_decision_id": d.unit_decision_id,
                "unit_id": d.unit_id,
                **unit_semantic_payload_from_decision(d),
            }
            for d in unit_decisions
        ),
        key=lambda r: r["unit_decision_id"],
    )
    payload = {
        "algorithm": RELEVANCE_SEMANTIC_DIGEST_ALGORITHM,
        "tender_decisions": tenders,
        "unit_decisions": units,
    }
    return canonical_json_digest(payload)


def all_fingerprints(
    *,
    pr5c_semantic_digest: str,
    linked_units: Iterable[ProductTextUnit],
    unresolved_units: Iterable[ProductTextUnit],
    tender_decisions: Iterable[TenderRelevanceDecision],
    unit_decisions: Iterable[EvidenceUnitRelevanceDecision],
) -> dict[str, str]:
    input_fp = relevance_input_fingerprint(
        pr5c_semantic_digest=pr5c_semantic_digest,
        linked_units=linked_units,
        unresolved_units=unresolved_units,
    )
    tax_fp = relevance_taxonomy_fingerprint()
    rules_fp = relevance_rules_fingerprint()
    build_fp = relevance_build_fingerprint(
        input_fingerprint=input_fp,
        taxonomy_fingerprint=tax_fp,
        rules_fingerprint=rules_fp,
    )
    sem = relevance_semantic_digest(
        tender_decisions=tender_decisions,
        unit_decisions=unit_decisions,
    )
    return {
        "input_fingerprint": input_fp,
        "taxonomy_fingerprint": tax_fp,
        "rules_fingerprint": rules_fp,
        "build_fingerprint": build_fp,
        "semantic_digest": sem,
    }
