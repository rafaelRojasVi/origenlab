"""PR5D product-text evidence adapter over PR5C + PR5B snapshots."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.models import (
    AcquisitionSnapshot,
    ProcurementLineObservation,
    ProcurementTenderObservation,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    CandidatePlanResult,
    CoalescedProcurementTender,
    ProcurementEvidenceRef,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.plane_b_acquisition import (
    load_acquisition_snapshot_json,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.constants import (
    PRODUCT_TEXT_ADAPTER_VERSION,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
    ProductTextUnit,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.normalize import (
    normalize_product_text,
)


class ProductTextAdapterError(ValueError):
    """Adapter cannot resolve product text without silent drop."""


def _stable_unit_id(payload: Mapping[str, Any]) -> str:
    digest = canonical_json_digest(dict(payload))
    return f"ptu_{digest[:32]}"


def build_snapshot_registry(
    snapshot_paths: list[Path],
) -> dict[str, AcquisitionSnapshot]:
    """Load snapshots keyed by snapshot_id. Reject divergent duplicates."""
    registry: dict[str, AcquisitionSnapshot] = {}
    for path in snapshot_paths:
        snap = load_acquisition_snapshot_json(path)
        prior = registry.get(snap.snapshot_id)
        if prior is not None and prior.normalized_semantic_digest != snap.normalized_semantic_digest:
            raise ProductTextAdapterError(
                f"divergent snapshot payloads for snapshot_id={snap.snapshot_id}"
            )
        registry[snap.snapshot_id] = snap
    return registry


def _tender_for_observation(
    snap: AcquisitionSnapshot, observation_id: str
) -> ProcurementTenderObservation | None:
    for tender in snap.tender_observations:
        if tender.source_observation_id == observation_id:
            return tender
    return None


def _lines_for_tender(
    snap: AcquisitionSnapshot, tender_observation_id: str
) -> list[ProcurementLineObservation]:
    return [
        line
        for line in snap.line_observations
        if line.tender_observation_id == tender_observation_id
    ]


def _unit(
    *,
    coalesced_tender_id: str,
    evidence_ref_id: str | None,
    link_status: str,
    unresolved_reason: str | None,
    field_path: str,
    text_raw: str,
    evidence_tier: str,
    source_plane: str,
    snapshot_id: str | None,
    observation_id: str | None,
    tender_observation_id: str | None,
    line_observation_id: str | None,
    pr4_procurement_id: str | None,
    contributing_evidence_ref_ids: tuple[str, ...],
) -> ProductTextUnit:
    text_norm = normalize_product_text(text_raw)
    unit_id = _stable_unit_id(
        {
            "adapter": PRODUCT_TEXT_ADAPTER_VERSION,
            "coalesced_tender_id": coalesced_tender_id,
            "evidence_ref_id": evidence_ref_id,
            "field_path": field_path,
            "text_normalized": text_norm,
            "line_observation_id": line_observation_id,
            "tender_observation_id": tender_observation_id,
            "link_status": link_status,
            "unresolved_reason": unresolved_reason,
        }
    )
    return ProductTextUnit(
        unit_id=unit_id,
        coalesced_tender_id=coalesced_tender_id,
        evidence_ref_id=evidence_ref_id,
        link_status=link_status,
        unresolved_reason=unresolved_reason,
        field_path=field_path,
        text_raw=text_raw,
        text_normalized=text_norm,
        evidence_tier=evidence_tier,
        source_plane=source_plane,
        snapshot_id=snapshot_id,
        observation_id=observation_id,
        tender_observation_id=tender_observation_id,
        line_observation_id=line_observation_id,
        pr4_procurement_id=pr4_procurement_id,
        contributing_evidence_ref_ids=contributing_evidence_ref_ids,
    )


def extract_units_for_tender(
    tender: CoalescedProcurementTender,
    *,
    refs_by_id: Mapping[str, ProcurementEvidenceRef],
    snapshots_by_id: Mapping[str, AcquisitionSnapshot],
) -> tuple[list[ProductTextUnit], list[ProductTextUnit]]:
    """Return (linked_units, unresolved_units) for one coalesced tender."""
    linked: list[ProductTextUnit] = []
    unresolved: list[ProductTextUnit] = []
    seen_ids: set[str] = set()

    def add(unit: ProductTextUnit) -> None:
        if unit.unit_id in seen_ids:
            return
        seen_ids.add(unit.unit_id)
        if unit.link_status == "linked" and unit.text_normalized:
            linked.append(unit)
        elif unit.link_status == "linked" and not unit.text_normalized:
            unresolved.append(
                ProductTextUnit(
                    unit_id=unit.unit_id,
                    coalesced_tender_id=unit.coalesced_tender_id,
                    evidence_ref_id=unit.evidence_ref_id,
                    link_status="unresolved_empty_text",
                    unresolved_reason="empty_normalized_product_text",
                    field_path=unit.field_path,
                    text_raw=unit.text_raw,
                    text_normalized="",
                    evidence_tier="no_usable_product_text",
                    source_plane=unit.source_plane,
                    snapshot_id=unit.snapshot_id,
                    observation_id=unit.observation_id,
                    tender_observation_id=unit.tender_observation_id,
                    line_observation_id=unit.line_observation_id,
                    pr4_procurement_id=unit.pr4_procurement_id,
                    contributing_evidence_ref_ids=unit.contributing_evidence_ref_ids,
                )
            )
        else:
            unresolved.append(unit)

    for ref_id in tender.evidence_ref_ids:
        ref = refs_by_id.get(ref_id)
        if ref is None:
            add(
                _unit(
                    coalesced_tender_id=tender.coalesced_tender_id,
                    evidence_ref_id=ref_id,
                    link_status="unresolved_missing_observation",
                    unresolved_reason="evidence_ref_missing_from_plan",
                    field_path="evidence_ref",
                    text_raw="",
                    evidence_tier="no_usable_product_text",
                    source_plane="unknown",
                    snapshot_id=None,
                    observation_id=None,
                    tender_observation_id=None,
                    line_observation_id=None,
                    pr4_procurement_id=None,
                    contributing_evidence_ref_ids=(ref_id,),
                )
            )
            continue

        contrib = (ref.evidence_ref_id,)

        if ref.title_raw:
            add(
                _unit(
                    coalesced_tender_id=tender.coalesced_tender_id,
                    evidence_ref_id=ref.evidence_ref_id,
                    link_status="linked",
                    unresolved_reason=None,
                    field_path="evidence_ref.title_raw",
                    text_raw=ref.title_raw,
                    evidence_tier="title_only",
                    source_plane=ref.evidence_plane,
                    snapshot_id=ref.snapshot_id,
                    observation_id=ref.observation_id,
                    tender_observation_id=None,
                    line_observation_id=None,
                    pr4_procurement_id=ref.pr4_procurement_id,
                    contributing_evidence_ref_ids=contrib,
                )
            )

        if ref.evidence_plane == "acquisition" and ref.snapshot_id and ref.observation_id:
            snap = snapshots_by_id.get(ref.snapshot_id)
            if snap is None:
                add(
                    _unit(
                        coalesced_tender_id=tender.coalesced_tender_id,
                        evidence_ref_id=ref.evidence_ref_id,
                        link_status="unresolved_missing_snapshot",
                        unresolved_reason="snapshot_not_in_registry",
                        field_path="acquisition.snapshot",
                        text_raw="",
                        evidence_tier="no_usable_product_text",
                        source_plane="acquisition",
                        snapshot_id=ref.snapshot_id,
                        observation_id=ref.observation_id,
                        tender_observation_id=None,
                        line_observation_id=None,
                        pr4_procurement_id=None,
                        contributing_evidence_ref_ids=contrib,
                    )
                )
                continue

            tender_obs = _tender_for_observation(snap, ref.observation_id)
            if tender_obs is None:
                add(
                    _unit(
                        coalesced_tender_id=tender.coalesced_tender_id,
                        evidence_ref_id=ref.evidence_ref_id,
                        link_status="unresolved_missing_observation",
                        unresolved_reason="tender_observation_not_found",
                        field_path="acquisition.tender_observation",
                        text_raw="",
                        evidence_tier="no_usable_product_text",
                        source_plane="acquisition",
                        snapshot_id=ref.snapshot_id,
                        observation_id=ref.observation_id,
                        tender_observation_id=None,
                        line_observation_id=None,
                        pr4_procurement_id=None,
                        contributing_evidence_ref_ids=contrib,
                    )
                )
                continue

            if tender_obs.description:
                add(
                    _unit(
                        coalesced_tender_id=tender.coalesced_tender_id,
                        evidence_ref_id=ref.evidence_ref_id,
                        link_status="linked",
                        unresolved_reason=None,
                        field_path="tender_observation.description",
                        text_raw=tender_obs.description,
                        evidence_tier="tender_description",
                        source_plane="acquisition",
                        snapshot_id=ref.snapshot_id,
                        observation_id=ref.observation_id,
                        tender_observation_id=tender_obs.tender_observation_id,
                        line_observation_id=None,
                        pr4_procurement_id=None,
                        contributing_evidence_ref_ids=contrib,
                    )
                )

            for line in _lines_for_tender(snap, tender_obs.tender_observation_id):
                parts = [
                    (line.product or "").strip(),
                    (line.description or "").strip(),
                    (line.category or "").strip(),
                    (line.unspsc_or_classification or "").strip(),
                ]
                blob = " | ".join(p for p in parts if p)
                if not blob:
                    add(
                        _unit(
                            coalesced_tender_id=tender.coalesced_tender_id,
                            evidence_ref_id=ref.evidence_ref_id,
                            link_status="unresolved_empty_text",
                            unresolved_reason="empty_line_product_fields",
                            field_path=f"line_observation[{line.ordinal}]",
                            text_raw="",
                            evidence_tier="no_usable_product_text",
                            source_plane="acquisition",
                            snapshot_id=ref.snapshot_id,
                            observation_id=ref.observation_id,
                            tender_observation_id=tender_obs.tender_observation_id,
                            line_observation_id=line.line_observation_id,
                            pr4_procurement_id=None,
                            contributing_evidence_ref_ids=contrib,
                        )
                    )
                    continue
                add(
                    _unit(
                        coalesced_tender_id=tender.coalesced_tender_id,
                        evidence_ref_id=ref.evidence_ref_id,
                        link_status="linked",
                        unresolved_reason=None,
                        field_path=(
                            f"line_observation[{line.ordinal}]."
                            f"product|description|category"
                        ),
                        text_raw=blob,
                        evidence_tier="line_product_text",
                        source_plane="acquisition",
                        snapshot_id=ref.snapshot_id,
                        observation_id=ref.observation_id,
                        tender_observation_id=tender_obs.tender_observation_id,
                        line_observation_id=line.line_observation_id,
                        pr4_procurement_id=None,
                        contributing_evidence_ref_ids=contrib,
                    )
                )

        elif ref.evidence_plane == "pr4":
            # Title already emitted. Raw constituent payloads are optional and not
            # loaded in this slice — emit typed unresolved when no title.
            if not ref.title_raw and ref.constituent_source_ids:
                add(
                    _unit(
                        coalesced_tender_id=tender.coalesced_tender_id,
                        evidence_ref_id=ref.evidence_ref_id,
                        link_status="unresolved_pr4_raw_not_loaded",
                        unresolved_reason="pr4_raw_payload_enrichment_deferred",
                        field_path="pr4.constituent_source_ids",
                        text_raw="",
                        evidence_tier="no_usable_product_text",
                        source_plane="pr4",
                        snapshot_id=None,
                        observation_id=None,
                        tender_observation_id=None,
                        line_observation_id=None,
                        pr4_procurement_id=ref.pr4_procurement_id,
                        contributing_evidence_ref_ids=contrib,
                    )
                )

    if tender.title_selected and not any(
        u.field_path.endswith("title_raw") or u.field_path == "tender.title_selected"
        for u in linked
    ):
        add(
            _unit(
                coalesced_tender_id=tender.coalesced_tender_id,
                evidence_ref_id=None,
                link_status="linked",
                unresolved_reason=None,
                field_path="tender.title_selected",
                text_raw=tender.title_selected,
                evidence_tier="title_only",
                source_plane="coalesced",
                snapshot_id=None,
                observation_id=None,
                tender_observation_id=None,
                line_observation_id=None,
                pr4_procurement_id=tender.pr4_procurement_id,
                contributing_evidence_ref_ids=tuple(tender.evidence_ref_ids),
            )
        )

    if not linked and not unresolved:
        add(
            _unit(
                coalesced_tender_id=tender.coalesced_tender_id,
                evidence_ref_id=None,
                link_status="unresolved_empty_text",
                unresolved_reason="no_product_text_fields",
                field_path="tender",
                text_raw="",
                evidence_tier="no_usable_product_text",
                source_plane="coalesced",
                snapshot_id=None,
                observation_id=None,
                tender_observation_id=None,
                line_observation_id=None,
                pr4_procurement_id=tender.pr4_procurement_id,
                contributing_evidence_ref_ids=tuple(tender.evidence_ref_ids),
            )
        )

    linked.sort(key=lambda u: u.unit_id)
    unresolved.sort(key=lambda u: u.unit_id)
    return linked, unresolved


def extract_all_product_text_units(
    plan: CandidatePlanResult,
    *,
    snapshot_paths: list[Path],
) -> tuple[tuple[ProductTextUnit, ...], tuple[ProductTextUnit, ...], dict[str, Any]]:
    registry = build_snapshot_registry(snapshot_paths)
    refs_by_id = {r.evidence_ref_id: r for r in plan.evidence_refs}
    linked_all: list[ProductTextUnit] = []
    unresolved_all: list[ProductTextUnit] = []
    for tender in plan.coalesced_tenders:
        linked, unresolved = extract_units_for_tender(
            tender, refs_by_id=refs_by_id, snapshots_by_id=registry
        )
        linked_all.extend(linked)
        unresolved_all.extend(unresolved)
    linked_all.sort(key=lambda u: (u.coalesced_tender_id, u.unit_id))
    unresolved_all.sort(key=lambda u: (u.coalesced_tender_id, u.unit_id))
    meta = {
        "adapter_version": PRODUCT_TEXT_ADAPTER_VERSION,
        "snapshots_loaded": sorted(registry.keys()),
        "linked_unit_count": len(linked_all),
        "unresolved_unit_count": len(unresolved_all),
    }
    return tuple(linked_all), tuple(unresolved_all), meta
