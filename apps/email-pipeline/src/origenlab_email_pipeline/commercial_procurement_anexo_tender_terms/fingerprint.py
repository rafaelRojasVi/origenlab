"""Deterministic semantic digest for ANEXO-T1 tender terms."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
)

from .constants import TENDER_TERMS_DIGEST_ALGORITHM
from .models import (
    FactCandidate,
    TenderItemTerms,
    TenderTermFact,
    TenderTermsBundle,
    TermEvidence,
)


def _evidence_payload(row: TermEvidence) -> dict[str, Any]:
    # Raw excerpts are intentionally excluded from the semantic digest. Their
    # hash plus addressable provenance is sufficient and keeps the digest safe
    # to share.
    return {
        "evidence_id": row.evidence_id,
        "attachment_id": row.attachment_id,
        "evidence_attachment_id": row.evidence_attachment_id,
        "attachment_sha256": row.attachment_sha256,
        "chunk_id": row.chunk_id,
        "locator_type": row.locator_type,
        "locator": row.locator,
        "evidence_text_sha256": row.evidence_text_sha256,
        "member_path": row.member_path,
    }


def _candidate_payload(row: FactCandidate) -> dict[str, Any]:
    return {
        "candidate_id": row.candidate_id,
        "value": row.value,
        "evidence": sorted(
            (_evidence_payload(ev) for ev in row.evidence),
            key=lambda item: item["evidence_id"],
        ),
    }


def _fact_payload(row: TenderTermFact) -> dict[str, Any]:
    return {
        "fact_id": row.fact_id,
        "field_name": row.field_name,
        "state": row.state,
        "coverage_status": row.coverage_status,
        "value": row.value,
        "value_type": row.value_type,
        "unit": row.unit,
        "currency": row.currency,
        "tax_basis": row.tax_basis,
        "reason_codes": sorted(row.reason_codes),
        "evidence": sorted(
            (_evidence_payload(ev) for ev in row.evidence),
            key=lambda item: item["evidence_id"],
        ),
        "candidates": sorted(
            (_candidate_payload(candidate) for candidate in row.candidates),
            key=lambda item: item["candidate_id"],
        ),
    }


def _item_payload(row: TenderItemTerms) -> dict[str, Any]:
    return {
        "item_id": row.item_id,
        "item_number": row.item_number,
        "item_label": row.item_label,
        "facts": sorted(
            (_fact_payload(fact) for fact in row.facts),
            key=lambda item: item["fact_id"],
        ),
    }


def tender_terms_semantic_digest(bundle: TenderTermsBundle) -> str:
    """Digest facts/provenance independent of construction ordering."""

    payload = {
        "algorithm": TENDER_TERMS_DIGEST_ALGORITHM,
        "terms_version": bundle.terms_version,
        "tender_id": bundle.tender_id,
        "source_bundle_semantic_digest": bundle.source_bundle_semantic_digest,
        "coverage": {
            "has_source_bundle": bundle.coverage.has_source_bundle,
            "extraction_complete": bundle.coverage.extraction_complete,
            "attachments_discovered": bundle.coverage.attachments_discovered,
            "attachments_downloaded": bundle.coverage.attachments_downloaded,
            "incomplete_reason_codes": sorted(
                bundle.coverage.incomplete_reason_codes
            ),
            "unread_attachment_ids": sorted(bundle.coverage.unread_attachment_ids),
            "outcome_counts": dict(sorted(bundle.coverage.outcome_counts.items())),
        },
        "tender_facts": sorted(
            (_fact_payload(fact) for fact in bundle.tender_facts),
            key=lambda item: item["fact_id"],
        ),
        "items": sorted(
            (_item_payload(item) for item in bundle.items),
            key=lambda item: item["item_id"],
        ),
    }
    return canonical_json_digest(payload)


def finalize_bundle(bundle: TenderTermsBundle) -> TenderTermsBundle:
    """Return the bundle carrying its deterministic semantic digest."""

    return replace(bundle, semantic_digest=tender_terms_semantic_digest(bundle))
