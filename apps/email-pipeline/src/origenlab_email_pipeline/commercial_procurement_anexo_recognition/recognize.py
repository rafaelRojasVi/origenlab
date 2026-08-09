"""Run the *existing* PR5D recognizer over baseline and annex evidence.

This lane deliberately owns no matching rules of its own. Baseline and
augmented runs call the same `classify_product_text_unit` and the same
`aggregate_tender_decision`; the only thing that differs between them is which
evidence units are supplied. Any measured difference is therefore attributable
to annex evidence and not to a second, divergent classifier.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from typing import Any

from ..commercial_procurement_candidate_planner.models import CoalescedProcurementTender
from ..commercial_procurement_product_relevance.aggregate import aggregate_tender_decision
from ..commercial_procurement_product_relevance.models import (
    EvidenceUnitRelevanceDecision,
    ProductTextUnit,
)
from ..commercial_procurement_product_relevance.normalize import normalize_product_text
from ..commercial_procurement_product_relevance.rules import classify_product_text_unit
from .constants import ANEXO_SOURCE_PLANE, BASELINE_SOURCE_PLANE
from .models import AnexoEvidenceSegment


def _unit_id(prefix: str, *parts: str) -> str:
    payload = "\0".join((prefix, *parts)).encode()
    return f"{prefix}_{hashlib.sha256(payload).hexdigest()[:24]}"


def _unit(
    *,
    unit_id: str,
    tender_id: str,
    evidence_ref_id: str,
    field_path: str,
    text_raw: str,
    evidence_tier: str,
    source_plane: str,
    line_observation_id: str | None = None,
) -> ProductTextUnit:
    return ProductTextUnit(
        unit_id=unit_id,
        coalesced_tender_id=tender_id,
        evidence_ref_id=evidence_ref_id,
        link_status="linked",
        unresolved_reason=None,
        field_path=field_path,
        text_raw=text_raw,
        text_normalized=normalize_product_text(text_raw),
        evidence_tier=evidence_tier,
        source_plane=source_plane,
        snapshot_id=None,
        observation_id=None,
        tender_observation_id=tender_id,
        line_observation_id=line_observation_id,
        pr4_procurement_id=None,
        contributing_evidence_ref_ids=(evidence_ref_id,),
    )


def build_baseline_units(tender: dict[str, Any]) -> tuple[ProductTextUnit, ...]:
    """Build API/item evidence units, mirroring the PR5D evidence adapter.

    Line text is the same `product | description | category | classification`
    blob the production adapter builds, so baseline recognition here matches
    what production would see from the same snapshot.
    """
    tender_id = str(tender["tender_id"])
    units: list[ProductTextUnit] = []

    title = (tender.get("title") or "").strip()
    if title:
        units.append(
            _unit(
                unit_id=_unit_id("bu", tender_id, "title"),
                tender_id=tender_id,
                evidence_ref_id=f"{tender_id}:title",
                field_path="tender.title",
                text_raw=title,
                evidence_tier="title_only",
                source_plane=BASELINE_SOURCE_PLANE,
            )
        )

    description = (tender.get("description") or "").strip()
    if description:
        units.append(
            _unit(
                unit_id=_unit_id("bu", tender_id, "description"),
                tender_id=tender_id,
                evidence_ref_id=f"{tender_id}:description",
                field_path="tender.description",
                text_raw=description,
                evidence_tier="tender_description",
                source_plane=BASELINE_SOURCE_PLANE,
            )
        )

    for line in tender.get("lines") or []:
        ordinal = int(line.get("ordinal") or 0)
        parts = [
            (line.get("product") or "").strip(),
            (line.get("description") or "").strip(),
            (line.get("category") or "").strip(),
            (line.get("classification") or "").strip(),
        ]
        blob = " | ".join(p for p in parts if p)
        if not blob:
            continue
        units.append(
            _unit(
                unit_id=_unit_id("bu", tender_id, f"line{ordinal}"),
                tender_id=tender_id,
                evidence_ref_id=f"{tender_id}:line:{ordinal}",
                field_path=f"line_observation[{ordinal}].product|description|category",
                text_raw=blob,
                evidence_tier="line_product_text",
                source_plane=BASELINE_SOURCE_PLANE,
                line_observation_id=f"{tender_id}:line:{ordinal}",
            )
        )
    return tuple(units)


def build_annex_units(
    segments: Iterable[AnexoEvidenceSegment],
) -> tuple[tuple[ProductTextUnit, AnexoEvidenceSegment], ...]:
    """Build one evidence unit per annex segment, paired with its provenance."""
    out: list[tuple[ProductTextUnit, AnexoEvidenceSegment]] = []
    for segment in segments:
        unit = _unit(
            unit_id=_unit_id("au", segment.segment_id),
            tender_id=segment.tender_id,
            evidence_ref_id=segment.chunk_id,
            field_path=f"anexo[{segment.locator_type}].{segment.segment_kind}",
            text_raw=segment.text_raw,
            evidence_tier=segment.evidence_tier,
            source_plane=ANEXO_SOURCE_PLANE,
        )
        out.append((unit, segment))
    return tuple(out)


def classify_units(
    units: Iterable[ProductTextUnit],
) -> tuple[EvidenceUnitRelevanceDecision, ...]:
    return tuple(classify_product_text_unit(unit) for unit in units)


def _tender_stub(
    tender_id: str, *, lifecycle_class: str, evidence_ref_ids: Sequence[str]
) -> CoalescedProcurementTender:
    """Minimal coalesced tender for the aggregator.

    `aggregate_tender_decision` reads only `coalesced_tender_id`,
    `evidence_ref_ids`, and `lifecycle_class`; the remaining fields exist to
    satisfy the frozen dataclass and are never consulted.
    """
    return CoalescedProcurementTender(
        coalesced_tender_id=tender_id,
        canonical_tender_key=tender_id,
        identity_namespace="anexo_e2_comparison",
        tender_key_kind="tender_code",
        candidate_source_kind="local_detail_cache",
        pr4_procurement_id=None,
        pr4_procurement_ids=(),
        acquisition_snapshot_ids=(),
        acquisition_instance_ids=(),
        acquisition_observation_ids=(),
        coalescence_status="single_source",
        source_precedence_reason="comparison_lane_stub",
        currentness_class="local_cached_snapshot",
        lifecycle_class=lifecycle_class,
        closing_soon_bucket="not_applicable",
        publication_timestamp_selected=None,
        close_timestamp_selected=None,
        status_code_selected=None,
        status_name_selected=None,
        status_value_selected=None,
        source_status_system_selected=None,
        buyer_display_selected=None,
        buyer_source_id_selected=None,
        title_selected=None,
        selected_field_provenance={},
        buyer_display_variance=False,
        lifecycle_status_evidence_ref_id=None,
        lifecycle_close_evidence_ref_id=None,
        lifecycle_publication_evidence_ref_id=None,
        lifecycle_evidence_currentness_class=None,
        lifecycle_reason_codes=(),
        evidence_ref_ids=tuple(evidence_ref_ids),
        conflict_ids=(),
    )


def aggregate_for_tender(
    tender_id: str,
    decisions: Sequence[EvidenceUnitRelevanceDecision],
    *,
    lifecycle_class: str,
    evidence_ref_ids: Sequence[str],
    input_fingerprint: str,
):
    """Roll unit decisions up to a tender decision using the PR5D aggregator."""
    return aggregate_tender_decision(
        _tender_stub(
            tender_id, lifecycle_class=lifecycle_class, evidence_ref_ids=evidence_ref_ids
        ),
        decisions,
        input_fingerprint=input_fingerprint,
    )
