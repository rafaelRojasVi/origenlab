"""PR5D product-text evidence adapter over PR5C + PR5B snapshots.

Extraction attempts are ledgered independently of linked/unresolved partitioning.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
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
    MaterializationRecord,
    ProductTextUnit,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.normalize import (
    normalize_product_text,
)


class ProductTextAdapterError(ValueError):
    """Adapter cannot resolve product text without silent drop."""


@dataclass(frozen=True)
class ExtractionAttempt:
    """One expected source-field extraction attempt (pre-partition ledger row)."""

    attempt_id: str
    coalesced_tender_id: str
    evidence_ref_id: str | None
    field_path: str
    attempt_kind: str
    line_observation_id: str | None
    tender_observation_id: str | None
    snapshot_id: str | None
    observation_id: str | None
    # Frozen from source-derived materialization — not recomputed from submitted units.
    expected_materialization_digest: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "coalesced_tender_id": self.coalesced_tender_id,
            "evidence_ref_id": self.evidence_ref_id,
            "field_path": self.field_path,
            "attempt_kind": self.attempt_kind,
            "line_observation_id": self.line_observation_id,
            "tender_observation_id": self.tender_observation_id,
            "snapshot_id": self.snapshot_id,
            "observation_id": self.observation_id,
            "expected_materialization_digest": self.expected_materialization_digest,
        }


def _stable_unit_id(payload: Mapping[str, Any]) -> str:
    digest = canonical_json_digest(dict(payload))
    return f"ptu_{digest[:32]}"


def unit_id_semantic_payload(unit: ProductTextUnit) -> dict[str, Any]:
    """Material fields that deterministically define ``unit_id`` (no attempt stamps)."""
    return {
        "adapter": PRODUCT_TEXT_ADAPTER_VERSION,
        "coalesced_tender_id": unit.coalesced_tender_id,
        "evidence_ref_id": unit.evidence_ref_id,
        "field_path": unit.field_path,
        "text_normalized": unit.text_normalized,
        "line_observation_id": unit.line_observation_id,
        "tender_observation_id": unit.tender_observation_id,
        "link_status": unit.link_status,
        "unresolved_reason": unit.unresolved_reason,
    }


def recompute_unit_id(unit: ProductTextUnit) -> str:
    """Independently recompute unit_id from material semantic fields."""
    return _stable_unit_id(unit_id_semantic_payload(unit))


def materialization_binding_payload(
    *,
    unit: ProductTextUnit,
    attempt_kind: str,
    materialization_status: str,
) -> dict[str, Any]:
    """Canonical source-derived binding identity (includes classification-relevant fields).

    Distinct from ``unit_id`` payload: covers evidence_tier, source_plane, attempt
    kind, materialization status, and PR4 identity so a self-consistent fabricated
    unit cannot satisfy reconciliation by recomputing only from itself.
    """
    return {
        "adapter": PRODUCT_TEXT_ADAPTER_VERSION,
        "binding_version": "materialization_binding_v1",
        "coalesced_tender_id": unit.coalesced_tender_id,
        "evidence_ref_id": unit.evidence_ref_id,
        "field_path": unit.field_path,
        "text_normalized": unit.text_normalized,
        "text_normalized_digest": canonical_json_digest(unit.text_normalized or ""),
        "evidence_tier": unit.evidence_tier,
        "source_plane": unit.source_plane,
        "link_status": unit.link_status,
        "unresolved_reason": unit.unresolved_reason,
        "snapshot_id": unit.snapshot_id,
        "observation_id": unit.observation_id,
        "tender_observation_id": unit.tender_observation_id,
        "line_observation_id": unit.line_observation_id,
        "pr4_procurement_id": unit.pr4_procurement_id,
        "attempt_kind": attempt_kind,
        "materialization_status": materialization_status,
    }


def materialization_binding_digest(
    *,
    unit: ProductTextUnit,
    attempt_kind: str,
    materialization_status: str,
) -> str:
    """Digest of the frozen materialization-binding payload."""
    return canonical_json_digest(
        materialization_binding_payload(
            unit=unit,
            attempt_kind=attempt_kind,
            materialization_status=materialization_status,
        )
    )


def _attempt_id(payload: Mapping[str, Any]) -> str:
    digest = canonical_json_digest(
        {"adapter": PRODUCT_TEXT_ADAPTER_VERSION, "attempt": dict(payload)}
    )
    return f"pta_{digest[:32]}"


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
    attempt_id: str | None = None,
    attempt_occurrence: int | None = None,
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
        attempt_id=attempt_id,
        attempt_occurrence=attempt_occurrence,
    )


def _make_attempt(
    *,
    coalesced_tender_id: str,
    evidence_ref_id: str | None,
    field_path: str,
    attempt_kind: str,
    line_observation_id: str | None = None,
    tender_observation_id: str | None = None,
    snapshot_id: str | None = None,
    observation_id: str | None = None,
) -> ExtractionAttempt:
    return ExtractionAttempt(
        attempt_id=_attempt_id(
            {
                "coalesced_tender_id": coalesced_tender_id,
                "evidence_ref_id": evidence_ref_id,
                "field_path": field_path,
                "attempt_kind": attempt_kind,
                "line_observation_id": line_observation_id,
                "tender_observation_id": tender_observation_id,
                "snapshot_id": snapshot_id,
                "observation_id": observation_id,
            }
        ),
        coalesced_tender_id=coalesced_tender_id,
        evidence_ref_id=evidence_ref_id,
        field_path=field_path,
        attempt_kind=attempt_kind,
        line_observation_id=line_observation_id,
        tender_observation_id=tender_observation_id,
        snapshot_id=snapshot_id,
        observation_id=observation_id,
    )


def enumerate_extraction_attempts(
    tender: CoalescedProcurementTender,
    *,
    refs_by_id: Mapping[str, ProcurementEvidenceRef],
    snapshots_by_id: Mapping[str, AcquisitionSnapshot],
) -> list[ExtractionAttempt]:
    """Enumerate expected source-field attempts before any unit is created."""
    attempts: list[ExtractionAttempt] = []
    seen_attempt_ids: list[str] = []
    title_raw_emitted = False

    def register(attempt: ExtractionAttempt) -> None:
        # Preserve duplicates in the list so reconciliation can detect them.
        if attempt.attempt_id in seen_attempt_ids:
            # Still append — duplicate attempt IDs are a contract failure.
            attempts.append(attempt)
            seen_attempt_ids.append(attempt.attempt_id)
            return
        attempts.append(attempt)
        seen_attempt_ids.append(attempt.attempt_id)

    for ref_id in tender.evidence_ref_ids:
        ref = refs_by_id.get(ref_id)
        if ref is None:
            register(
                _make_attempt(
                    coalesced_tender_id=tender.coalesced_tender_id,
                    evidence_ref_id=ref_id,
                    field_path="evidence_ref",
                    attempt_kind="missing_evidence_ref",
                )
            )
            continue

        if ref.title_raw:
            register(
                _make_attempt(
                    coalesced_tender_id=tender.coalesced_tender_id,
                    evidence_ref_id=ref.evidence_ref_id,
                    field_path="evidence_ref.title_raw",
                    attempt_kind="title_raw",
                    snapshot_id=ref.snapshot_id,
                    observation_id=ref.observation_id,
                )
            )
            title_raw_emitted = True

        if ref.evidence_plane == "acquisition" and ref.snapshot_id and ref.observation_id:
            snap = snapshots_by_id.get(ref.snapshot_id)
            if snap is None:
                register(
                    _make_attempt(
                        coalesced_tender_id=tender.coalesced_tender_id,
                        evidence_ref_id=ref.evidence_ref_id,
                        field_path="acquisition.snapshot",
                        attempt_kind="missing_snapshot",
                        snapshot_id=ref.snapshot_id,
                        observation_id=ref.observation_id,
                    )
                )
                continue
            tender_obs = _tender_for_observation(snap, ref.observation_id)
            if tender_obs is None:
                register(
                    _make_attempt(
                        coalesced_tender_id=tender.coalesced_tender_id,
                        evidence_ref_id=ref.evidence_ref_id,
                        field_path="acquisition.tender_observation",
                        attempt_kind="missing_tender_observation",
                        snapshot_id=ref.snapshot_id,
                        observation_id=ref.observation_id,
                    )
                )
                continue
            if tender_obs.description:
                register(
                    _make_attempt(
                        coalesced_tender_id=tender.coalesced_tender_id,
                        evidence_ref_id=ref.evidence_ref_id,
                        field_path="tender_observation.description",
                        attempt_kind="tender_description",
                        tender_observation_id=tender_obs.tender_observation_id,
                        snapshot_id=ref.snapshot_id,
                        observation_id=ref.observation_id,
                    )
                )
            for line in _lines_for_tender(snap, tender_obs.tender_observation_id):
                register(
                    _make_attempt(
                        coalesced_tender_id=tender.coalesced_tender_id,
                        evidence_ref_id=ref.evidence_ref_id,
                        field_path=(
                            f"line_observation[{line.ordinal}]."
                            f"product|description|category"
                        ),
                        attempt_kind="line_product_fields",
                        line_observation_id=line.line_observation_id,
                        tender_observation_id=tender_obs.tender_observation_id,
                        snapshot_id=ref.snapshot_id,
                        observation_id=ref.observation_id,
                    )
                )
        elif ref.evidence_plane == "pr4":
            if not ref.title_raw and ref.constituent_source_ids:
                register(
                    _make_attempt(
                        coalesced_tender_id=tender.coalesced_tender_id,
                        evidence_ref_id=ref.evidence_ref_id,
                        field_path="pr4.constituent_source_ids",
                        attempt_kind="pr4_raw_deferred",
                    )
                )

    if tender.title_selected and not title_raw_emitted:
        register(
            _make_attempt(
                coalesced_tender_id=tender.coalesced_tender_id,
                evidence_ref_id=None,
                field_path="tender.title_selected",
                attempt_kind="title_selected",
            )
        )

    if not attempts:
        register(
            _make_attempt(
                coalesced_tender_id=tender.coalesced_tender_id,
                evidence_ref_id=None,
                field_path="tender",
                attempt_kind="no_product_text_fields",
            )
        )
    return attempts


def materialize_attempt(
    attempt: ExtractionAttempt,
    *,
    tender: CoalescedProcurementTender,
    refs_by_id: Mapping[str, ProcurementEvidenceRef],
    snapshots_by_id: Mapping[str, AcquisitionSnapshot],
) -> ProductTextUnit:
    """Materialize exactly one unit for one ledger attempt."""
    ref = (
        refs_by_id.get(attempt.evidence_ref_id)
        if attempt.evidence_ref_id
        else None
    )
    contrib = (attempt.evidence_ref_id,) if attempt.evidence_ref_id else tuple(
        tender.evidence_ref_ids
    )

    if attempt.attempt_kind == "missing_evidence_ref":
        return _unit(
            coalesced_tender_id=tender.coalesced_tender_id,
            evidence_ref_id=attempt.evidence_ref_id,
            link_status="unresolved_missing_observation",
            unresolved_reason="evidence_ref_missing_from_plan",
            field_path=attempt.field_path,
            text_raw="",
            evidence_tier="no_usable_product_text",
            source_plane="unknown",
            snapshot_id=None,
            observation_id=None,
            tender_observation_id=None,
            line_observation_id=None,
            pr4_procurement_id=None,
            contributing_evidence_ref_ids=contrib,
        )

    if attempt.attempt_kind == "title_raw" and ref is not None:
        return _unit(
            coalesced_tender_id=tender.coalesced_tender_id,
            evidence_ref_id=ref.evidence_ref_id,
            link_status="linked",
            unresolved_reason=None,
            field_path=attempt.field_path,
            text_raw=ref.title_raw or "",
            evidence_tier="title_only",
            source_plane=ref.evidence_plane,
            snapshot_id=ref.snapshot_id,
            observation_id=ref.observation_id,
            tender_observation_id=None,
            line_observation_id=None,
            pr4_procurement_id=ref.pr4_procurement_id,
            contributing_evidence_ref_ids=contrib,
        )

    if attempt.attempt_kind == "missing_snapshot" and ref is not None:
        return _unit(
            coalesced_tender_id=tender.coalesced_tender_id,
            evidence_ref_id=ref.evidence_ref_id,
            link_status="unresolved_missing_snapshot",
            unresolved_reason="snapshot_not_in_registry",
            field_path=attempt.field_path,
            text_raw="",
            evidence_tier="no_usable_product_text",
            source_plane="acquisition",
            snapshot_id=attempt.snapshot_id,
            observation_id=attempt.observation_id,
            tender_observation_id=None,
            line_observation_id=None,
            pr4_procurement_id=None,
            contributing_evidence_ref_ids=contrib,
        )

    if attempt.attempt_kind == "missing_tender_observation" and ref is not None:
        return _unit(
            coalesced_tender_id=tender.coalesced_tender_id,
            evidence_ref_id=ref.evidence_ref_id,
            link_status="unresolved_missing_observation",
            unresolved_reason="tender_observation_not_found",
            field_path=attempt.field_path,
            text_raw="",
            evidence_tier="no_usable_product_text",
            source_plane="acquisition",
            snapshot_id=attempt.snapshot_id,
            observation_id=attempt.observation_id,
            tender_observation_id=None,
            line_observation_id=None,
            pr4_procurement_id=None,
            contributing_evidence_ref_ids=contrib,
        )

    if attempt.attempt_kind == "tender_description" and ref is not None:
        snap = snapshots_by_id.get(ref.snapshot_id or "")
        tender_obs = (
            _tender_for_observation(snap, ref.observation_id or "")
            if snap and ref.observation_id
            else None
        )
        text = (tender_obs.description if tender_obs else None) or ""
        return _unit(
            coalesced_tender_id=tender.coalesced_tender_id,
            evidence_ref_id=ref.evidence_ref_id,
            link_status="linked",
            unresolved_reason=None,
            field_path=attempt.field_path,
            text_raw=text,
            evidence_tier="tender_description",
            source_plane="acquisition",
            snapshot_id=ref.snapshot_id,
            observation_id=ref.observation_id,
            tender_observation_id=attempt.tender_observation_id,
            line_observation_id=None,
            pr4_procurement_id=None,
            contributing_evidence_ref_ids=contrib,
        )

    if attempt.attempt_kind == "line_product_fields" and ref is not None:
        snap = snapshots_by_id.get(ref.snapshot_id or "")
        line = None
        if snap and attempt.line_observation_id:
            line = next(
                (
                    ln
                    for ln in snap.line_observations
                    if ln.line_observation_id == attempt.line_observation_id
                ),
                None,
            )
        parts = [
            ((line.product if line else None) or "").strip(),
            ((line.description if line else None) or "").strip(),
            ((line.category if line else None) or "").strip(),
            ((line.unspsc_or_classification if line else None) or "").strip(),
        ]
        blob = " | ".join(p for p in parts if p)
        if not blob:
            return _unit(
                coalesced_tender_id=tender.coalesced_tender_id,
                evidence_ref_id=ref.evidence_ref_id,
                link_status="unresolved_empty_text",
                unresolved_reason="empty_line_product_fields",
                field_path=attempt.field_path,
                text_raw="",
                evidence_tier="no_usable_product_text",
                source_plane="acquisition",
                snapshot_id=ref.snapshot_id,
                observation_id=ref.observation_id,
                tender_observation_id=attempt.tender_observation_id,
                line_observation_id=attempt.line_observation_id,
                pr4_procurement_id=None,
                contributing_evidence_ref_ids=contrib,
            )
        return _unit(
            coalesced_tender_id=tender.coalesced_tender_id,
            evidence_ref_id=ref.evidence_ref_id,
            link_status="linked",
            unresolved_reason=None,
            field_path=attempt.field_path,
            text_raw=blob,
            evidence_tier="line_product_text",
            source_plane="acquisition",
            snapshot_id=ref.snapshot_id,
            observation_id=ref.observation_id,
            tender_observation_id=attempt.tender_observation_id,
            line_observation_id=attempt.line_observation_id,
            pr4_procurement_id=None,
            contributing_evidence_ref_ids=contrib,
        )

    if attempt.attempt_kind == "pr4_raw_deferred" and ref is not None:
        return _unit(
            coalesced_tender_id=tender.coalesced_tender_id,
            evidence_ref_id=ref.evidence_ref_id,
            link_status="unresolved_pr4_raw_not_loaded",
            unresolved_reason="pr4_raw_payload_enrichment_deferred",
            field_path=attempt.field_path,
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

    if attempt.attempt_kind == "title_selected":
        return _unit(
            coalesced_tender_id=tender.coalesced_tender_id,
            evidence_ref_id=None,
            link_status="linked",
            unresolved_reason=None,
            field_path=attempt.field_path,
            text_raw=tender.title_selected or "",
            evidence_tier="title_only",
            source_plane="coalesced",
            snapshot_id=None,
            observation_id=None,
            tender_observation_id=None,
            line_observation_id=None,
            pr4_procurement_id=tender.pr4_procurement_id,
            contributing_evidence_ref_ids=tuple(tender.evidence_ref_ids),
        )

    if attempt.attempt_kind == "no_product_text_fields":
        return _unit(
            coalesced_tender_id=tender.coalesced_tender_id,
            evidence_ref_id=None,
            link_status="unresolved_empty_text",
            unresolved_reason="no_product_text_fields",
            field_path=attempt.field_path,
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

    if attempt.attempt_kind == "duplicate_extraction_attempt":
        return _unit(
            coalesced_tender_id=tender.coalesced_tender_id,
            evidence_ref_id=attempt.evidence_ref_id,
            link_status="unresolved_empty_text",
            unresolved_reason="duplicate_extraction_attempt",
            field_path=attempt.field_path,
            text_raw="",
            evidence_tier="no_usable_product_text",
            source_plane="adapter",
            snapshot_id=attempt.snapshot_id,
            observation_id=attempt.observation_id,
            tender_observation_id=attempt.tender_observation_id,
            line_observation_id=attempt.line_observation_id,
            pr4_procurement_id=None,
            contributing_evidence_ref_ids=contrib,
        )

    raise ProductTextAdapterError(f"unknown attempt_kind: {attempt.attempt_kind}")


def _with_attempt(
    unit: ProductTextUnit,
    *,
    attempt_id: str,
    attempt_occurrence: int,
) -> ProductTextUnit:
    return ProductTextUnit(
        unit_id=unit.unit_id,
        coalesced_tender_id=unit.coalesced_tender_id,
        evidence_ref_id=unit.evidence_ref_id,
        link_status=unit.link_status,
        unresolved_reason=unit.unresolved_reason,
        field_path=unit.field_path,
        text_raw=unit.text_raw,
        text_normalized=unit.text_normalized,
        evidence_tier=unit.evidence_tier,
        source_plane=unit.source_plane,
        snapshot_id=unit.snapshot_id,
        observation_id=unit.observation_id,
        tender_observation_id=unit.tender_observation_id,
        line_observation_id=unit.line_observation_id,
        pr4_procurement_id=unit.pr4_procurement_id,
        contributing_evidence_ref_ids=unit.contributing_evidence_ref_ids,
        attempt_id=attempt_id,
        attempt_occurrence=attempt_occurrence,
    )


def extract_units_for_tender(
    tender: CoalescedProcurementTender,
    *,
    refs_by_id: Mapping[str, ProcurementEvidenceRef],
    snapshots_by_id: Mapping[str, AcquisitionSnapshot],
) -> tuple[
    list[ProductTextUnit],
    list[ProductTextUnit],
    list[ExtractionAttempt],
    list[MaterializationRecord],
]:
    """Return (linked, unresolved, attempts_with_frozen_expectation, ledger).

    The attempt ledger is built before materialization. Every attempt occurrence
    produces exactly one materialization record associating attempt_id → unit_id.
    Expected materialization digests are frozen from source-derived units at
    emission time and attached to both the attempt and the ledger row — not
    recomputed solely from later-submitted units.
    """
    attempts = enumerate_extraction_attempts(
        tender, refs_by_id=refs_by_id, snapshots_by_id=snapshots_by_id
    )
    linked: list[ProductTextUnit] = []
    unresolved: list[ProductTextUnit] = []
    materializations: list[MaterializationRecord] = []
    attempts_out: list[ExtractionAttempt] = []
    seen_attempt_ids: set[str] = set()
    emitted_unit_ids: set[str] = set()

    def _freeze(
        *,
        attempt: ExtractionAttempt,
        occurrence: int,
        unit: ProductTextUnit,
        partition: str,
        materialization_status: str,
        unresolved_reason: str | None,
        attempt_kind: str,
    ) -> None:
        digest = materialization_binding_digest(
            unit=unit,
            attempt_kind=attempt_kind,
            materialization_status=materialization_status,
        )
        attempts_out.append(replace(attempt, expected_materialization_digest=digest))
        materializations.append(
            MaterializationRecord(
                attempt_occurrence=occurrence,
                attempt_id=attempt.attempt_id,
                unit_id=unit.unit_id,
                partition=partition,
                materialization_status=materialization_status,
                unresolved_reason=unresolved_reason,
                field_path=attempt.field_path,
                attempt_kind=attempt_kind,
                expected_materialization_digest=digest,
            )
        )

    for occurrence, attempt in enumerate(attempts):
        if attempt.attempt_id in seen_attempt_ids:
            dup = ExtractionAttempt(
                attempt_id=attempt.attempt_id,
                coalesced_tender_id=attempt.coalesced_tender_id,
                evidence_ref_id=attempt.evidence_ref_id,
                field_path=attempt.field_path,
                attempt_kind="duplicate_extraction_attempt",
                line_observation_id=attempt.line_observation_id,
                tender_observation_id=attempt.tender_observation_id,
                snapshot_id=attempt.snapshot_id,
                observation_id=attempt.observation_id,
            )
            unit = _with_attempt(
                materialize_attempt(
                    dup,
                    tender=tender,
                    refs_by_id=refs_by_id,
                    snapshots_by_id=snapshots_by_id,
                ),
                attempt_id=attempt.attempt_id,
                attempt_occurrence=occurrence,
            )
            unresolved.append(unit)
            _freeze(
                attempt=attempt,
                occurrence=occurrence,
                unit=unit,
                partition="unresolved",
                materialization_status="duplicate_extraction_attempt",
                unresolved_reason="duplicate_extraction_attempt",
                attempt_kind="duplicate_extraction_attempt",
            )
            continue
        seen_attempt_ids.add(attempt.attempt_id)

        unit = _with_attempt(
            materialize_attempt(
                attempt,
                tender=tender,
                refs_by_id=refs_by_id,
                snapshots_by_id=snapshots_by_id,
            ),
            attempt_id=attempt.attempt_id,
            attempt_occurrence=occurrence,
        )

        if unit.unit_id in emitted_unit_ids:
            collision = _with_attempt(
                ProductTextUnit(
                    unit_id=unit.unit_id,
                    coalesced_tender_id=unit.coalesced_tender_id,
                    evidence_ref_id=unit.evidence_ref_id,
                    link_status="unresolved_empty_text",
                    unresolved_reason="duplicate_unit_id_collision",
                    field_path=unit.field_path,
                    text_raw=unit.text_raw,
                    text_normalized=unit.text_normalized,
                    evidence_tier="no_usable_product_text",
                    source_plane=unit.source_plane,
                    snapshot_id=unit.snapshot_id,
                    observation_id=unit.observation_id,
                    tender_observation_id=unit.tender_observation_id,
                    line_observation_id=unit.line_observation_id,
                    pr4_procurement_id=unit.pr4_procurement_id,
                    contributing_evidence_ref_ids=unit.contributing_evidence_ref_ids,
                ),
                attempt_id=attempt.attempt_id,
                attempt_occurrence=occurrence,
            )
            unresolved.append(collision)
            _freeze(
                attempt=attempt,
                occurrence=occurrence,
                unit=collision,
                partition="unresolved",
                materialization_status="duplicate_unit_id_collision",
                unresolved_reason="duplicate_unit_id_collision",
                attempt_kind=attempt.attempt_kind,
            )
            continue
        emitted_unit_ids.add(unit.unit_id)

        if unit.link_status == "linked" and unit.text_normalized:
            linked.append(unit)
            _freeze(
                attempt=attempt,
                occurrence=occurrence,
                unit=unit,
                partition="linked",
                materialization_status="linked",
                unresolved_reason=None,
                attempt_kind=attempt.attempt_kind,
            )
        elif unit.link_status == "linked" and not unit.text_normalized:
            empty = _with_attempt(
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
                ),
                attempt_id=attempt.attempt_id,
                attempt_occurrence=occurrence,
            )
            unresolved.append(empty)
            _freeze(
                attempt=attempt,
                occurrence=occurrence,
                unit=empty,
                partition="unresolved",
                materialization_status="empty_normalized_product_text",
                unresolved_reason="empty_normalized_product_text",
                attempt_kind=attempt.attempt_kind,
            )
        else:
            unresolved.append(unit)
            _freeze(
                attempt=attempt,
                occurrence=occurrence,
                unit=unit,
                partition="unresolved",
                materialization_status=unit.unresolved_reason
                or unit.link_status
                or "unresolved",
                unresolved_reason=unit.unresolved_reason,
                attempt_kind=attempt.attempt_kind,
            )

    linked.sort(key=lambda u: u.unit_id)
    unresolved.sort(key=lambda u: u.unit_id)
    return linked, unresolved, attempts_out, materializations


def extract_all_product_text_units(
    plan: CandidatePlanResult,
    *,
    snapshot_paths: list[Path],
) -> tuple[
    tuple[ProductTextUnit, ...],
    tuple[ProductTextUnit, ...],
    dict[str, Any],
    tuple[ExtractionAttempt, ...],
    tuple[MaterializationRecord, ...],
]:
    """Extract units; return full attempts + materialization ledger (not derived from units)."""
    registry = build_snapshot_registry(snapshot_paths)
    refs_by_id = {r.evidence_ref_id: r for r in plan.evidence_refs}
    linked_all: list[ProductTextUnit] = []
    unresolved_all: list[ProductTextUnit] = []
    attempts_all: list[ExtractionAttempt] = []
    materializations_all: list[MaterializationRecord] = []
    occurrence_offset = 0
    for tender in plan.coalesced_tenders:
        linked, unresolved, attempts, mats = extract_units_for_tender(
            tender, refs_by_id=refs_by_id, snapshots_by_id=registry
        )
        attempts_all.extend(attempts)
        for mat in mats:
            materializations_all.append(
                MaterializationRecord(
                    attempt_occurrence=occurrence_offset + mat.attempt_occurrence,
                    attempt_id=mat.attempt_id,
                    unit_id=mat.unit_id,
                    partition=mat.partition,
                    materialization_status=mat.materialization_status,
                    unresolved_reason=mat.unresolved_reason,
                    field_path=mat.field_path,
                    attempt_kind=mat.attempt_kind,
                    expected_materialization_digest=mat.expected_materialization_digest,
                )
            )
        remapped_linked = []
        remapped_unresolved = []
        for u in linked:
            remapped_linked.append(
                _with_attempt(
                    u,
                    attempt_id=u.attempt_id or "",
                    attempt_occurrence=occurrence_offset + int(u.attempt_occurrence or 0),
                )
            )
        for u in unresolved:
            remapped_unresolved.append(
                _with_attempt(
                    u,
                    attempt_id=u.attempt_id or "",
                    attempt_occurrence=occurrence_offset + int(u.attempt_occurrence or 0),
                )
            )
        linked_all.extend(remapped_linked)
        unresolved_all.extend(remapped_unresolved)
        occurrence_offset += len(attempts)
    linked_all.sort(key=lambda u: (u.coalesced_tender_id, u.unit_id))
    unresolved_all.sort(key=lambda u: (u.coalesced_tender_id, u.unit_id))
    meta = {
        "adapter_version": PRODUCT_TEXT_ADAPTER_VERSION,
        "snapshots_loaded": sorted(registry.keys()),
        "linked_unit_count": len(linked_all),
        "unresolved_unit_count": len(unresolved_all),
        "extraction_attempt_count": len(attempts_all),
        "materialization_count": len(materializations_all),
    }
    return (
        tuple(linked_all),
        tuple(unresolved_all),
        meta,
        tuple(attempts_all),
        tuple(materializations_all),
    )
