"""Focused contracts for guarded P2A annex augmentation of production PR5D.

These fixtures are entirely local.  They exercise the production identity
boundary and the canonical PR5D classifier/aggregator without acquiring annex
evidence or constructing an opportunity queue.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from origenlab_email_pipeline.chilecompra_anexo_evidence.constants import (
    FORMAT_DOCX,
    OUTCOME_EXTRACTION_SUCCESS,
    OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT,
    ROLE_TECHNICAL_SPEC,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.fingerprint import (
    bundle_semantic_digest,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.models import (
    AttachmentExtraction,
    AttachmentRecord,
    EvidenceChunk,
    TenderAttachmentBundle,
)

from origenlab_email_pipeline.commercial_procurement_anexo_integration import (
    VerifiedAnexoEvidence,
)
from origenlab_email_pipeline.commercial_procurement_anexo_integration import (
    augment as augment_module,
)
from origenlab_email_pipeline.commercial_procurement_anexo_integration.augment import (
    AnexoIntegrationError,
    augment_product_relevance_plan,
)
from origenlab_email_pipeline.commercial_procurement_anexo_recognition.corpus import (
    AnexoEvidenceBundleSet,
)
from origenlab_email_pipeline.commercial_procurement_anexo_shadow_prospects.capability import (
    classify_equipment_capability,
)
from origenlab_email_pipeline.commercial_procurement_anexo_shadow_prospects.constants import (
    CAPABILITY_OUT_OF_SCOPE,
)
from origenlab_email_pipeline.commercial_procurement_anexo_shadow_prospects.plans import (
    build_candidate_plan,
    build_relevance_plan_pair,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.constants import (
    IDENTITY_NS_MERCADO_PUBLICO,
    TENDER_KEY_KIND_MERCADO_PUBLICO,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    CandidatePlanResult,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.catalog_capability import (
    MATCH_OUTSIDE_CATALOG,
    match_catalog_status,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
    ProductRelevancePlanResult,
)


AS_OF_UTC = "2026-08-12T16:15:00Z"
FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures/commercial_procurement_anexo_integration_p2a"
    / "historical_replay_cases.json"
)


def _tender(tender_id: str) -> dict[str, Any]:
    return {
        "tender_id": tender_id,
        "title": "Licitación pública institucional",
        "description": "Antecedentes generales del proceso",
        "buyer_organization": "Organismo de prueba",
        "lifecycle_class": "active_open",
        "lines": [],
    }


def _empty_evidence() -> AnexoEvidenceBundleSet:
    return AnexoEvidenceBundleSet(
        chunks_by_tender={},
        attachments_by_tender={},
        bundle_by_tender={},
    )


def _production_plans(
    tender_ids: tuple[str, ...],
    *,
    fake_namespace_ids: frozenset[str] = frozenset(),
    pr4_display_kind_ids: frozenset[str] = frozenset(),
) -> tuple[CandidatePlanResult, ProductRelevancePlanResult]:
    rows = tuple(_tender(tender_id) for tender_id in tender_ids)
    pair = build_relevance_plan_pair(rows, anexo=_empty_evidence(), as_of_utc=AS_OF_UTC)
    candidate = build_candidate_plan(
        rows,
        as_of_utc=AS_OF_UTC,
        input_digest=pair.input_digest,
    )
    canonical = tuple(
        replace(
            tender,
            canonical_tender_key=tender.coalesced_tender_id,
            identity_namespace=(
                "chilecompra"
                if tender.coalesced_tender_id in fake_namespace_ids
                else IDENTITY_NS_MERCADO_PUBLICO
            ),
            tender_key_kind=(
                "codigo_externo"
                if tender.coalesced_tender_id in pr4_display_kind_ids
                else TENDER_KEY_KIND_MERCADO_PUBLICO
            ),
        )
        for tender in candidate.coalesced_tenders
    )
    return replace(candidate, coalesced_tenders=canonical), pair.current


def _chunk_row(tender_id: str, text: str, ordinal: int) -> dict[str, Any]:
    attachment_id = f"att-{tender_id}"
    chunk_id = hashlib.sha256(
        f"chunk\0{tender_id}\0{ordinal}\0{text}".encode()
    ).hexdigest()[:32]
    return {
        "tender_id": tender_id,
        "attachment_id": attachment_id,
        "chunk_id": chunk_id,
        "ordinal": ordinal,
        "source_format": "docx",
        "locator_type": "docx_table",
        "locator": {"section": "body", "table": 1},
        "text": f"[r{ordinal}] {text}",
    }


def _verified_evidence(
    texts_by_tender: dict[str, tuple[str, ...]],
    *,
    incomplete_tender_ids: frozenset[str] = frozenset(),
    reverse_chunks: bool = False,
) -> VerifiedAnexoEvidence:
    bundles: dict[str, TenderAttachmentBundle] = {}
    tender_digests: dict[str, str] = {}

    for tender_id, texts in sorted(texts_by_tender.items()):
        incomplete = tender_id in incomplete_tender_ids
        outcome = (
            OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT
            if incomplete
            else OUTCOME_EXTRACTION_SUCCESS
        )

        raw_chunks = [
            _chunk_row(tender_id, value, ordinal)
            for ordinal, value in enumerate(texts, start=1)
        ]
        if reverse_chunks:
            raw_chunks.reverse()

        chunks = tuple(
            EvidenceChunk(
                chunk_id=str(row["chunk_id"]),
                attachment_id=str(row["attachment_id"]),
                tender_id=str(row["tender_id"]),
                source_format=str(row["source_format"]),
                locator_type=str(row["locator_type"]),
                locator=dict(row["locator"]),
                text=str(row["text"]),
                text_sha256=hashlib.sha256(
                    str(row["text"]).encode("utf-8")
                ).hexdigest(),
                char_count=len(str(row["text"])),
                ordinal=int(row["ordinal"]),
                container_attachment_id=row.get("container_attachment_id"),
                member_path=row.get("member_path"),
                warnings=tuple(row.get("warnings") or ()),
            )
            for row in raw_chunks
        )

        attachment_id = f"att-{tender_id}"
        attachment_sha = hashlib.sha256(
            f"fixture-attachment\\0{tender_id}".encode()
        ).hexdigest()
        byte_count = sum(len(value.encode("utf-8")) for value in texts)

        extraction = AttachmentExtraction(
            outcome=outcome,
            detected_format=FORMAT_DOCX,
            chunk_count=len(chunks),
            char_count=sum(chunk.char_count for chunk in chunks),
            extractor="p2a_fixture",
        )

        attachment = AttachmentRecord(
            attachment_id=attachment_id,
            tender_id=tender_id,
            listing_ordinal=1,
            row_ordinal=1,
            portal_filename=f"anexo-{tender_id}.docx",
            safe_filename=f"anexo-{tender_id}.docx",
            tipo="fixture",
            descripcion="fixture",
            fecha_adjunto="2026-08-12",
            content_type=(
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
            detected_format=FORMAT_DOCX,
            extension=".docx",
            byte_count=byte_count,
            sha256=attachment_sha,
            download_status="downloaded",
            outcome=outcome,
            role_tag=ROLE_TECHNICAL_SPEC,
            evidence_attachment_id=attachment_id,
            extraction=extraction,
        )

        incomplete_reasons = (
            (OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT,)
            if incomplete
            else ()
        )

        digest = bundle_semantic_digest(
            tender_id=tender_id,
            attachments=(attachment,),
            chunks=chunks,
            incomplete_reason_codes=incomplete_reasons,
        )

        bundle = TenderAttachmentBundle(
            tender_id=tender_id,
            source_kind="p2a_fixture",
            listing_page_count=1,
            attachments_discovered=1,
            attachments_downloaded=1,
            attachments=(attachment,),
            chunks=chunks,
            bytes_downloaded=byte_count,
            incomplete_reason_codes=incomplete_reasons,
            semantic_digest=digest,
            outcome_counts={outcome: 1},
        )

        bundles[tender_id] = bundle
        tender_digests[tender_id] = digest

    semantic_digest = hashlib.sha256(
        "|".join(
            f"{key}:{value}"
            for key, value in sorted(tender_digests.items())
        ).encode()
    ).hexdigest()

    return VerifiedAnexoEvidence(
        bundles=bundles,
        semantic_digest=semantic_digest,
        tender_semantic_digests=tender_digests,
        summary={"fixture": True},
        attachment_manifest={"fixture": True},
        extraction_manifest={"fixture": True},
    )


def _decision_by_tender(plan: ProductRelevancePlanResult) -> dict[str, Any]:
    return {
        decision.coalesced_tender_id: decision for decision in plan.tender_decisions
    }


def test_real_pr5c_namespace_binds_and_fake_namespace_fails_closed() -> None:
    tender_id = "986278-12-LE26"
    verified = _verified_evidence({tender_id: ("adquisición de autoclave",)})

    canonical_pr5c, canonical_baseline = _production_plans((tender_id,))
    canonical = augment_product_relevance_plan(
        pr5c=canonical_pr5c,
        baseline=canonical_baseline,
        verified=verified,
    )
    assert canonical.bound_tender_ids == (tender_id,)
    assert canonical.unused_tender_ids == ()
    assert len(canonical.bindings) == 1
    assert canonical.bindings[0].applied is True

    fake_pr5c, fake_baseline = _production_plans(
        (tender_id,), fake_namespace_ids=frozenset({tender_id})
    )
    rejected = augment_product_relevance_plan(
        pr5c=fake_pr5c,
        baseline=fake_baseline,
        verified=verified,
    )
    assert rejected.bound_tender_ids == ()
    assert rejected.unused_tender_ids == (tender_id,)
    assert rejected.bindings == ()
    assert {unit.unit_id: unit for unit in rejected.plan.product_text_units} == {
        unit.unit_id: unit for unit in fake_baseline.product_text_units
    }
    assert rejected.plan.tender_decisions == fake_baseline.tender_decisions


def test_authoritative_pr4_display_kind_binds_without_rewriting_provenance() -> None:
    tender_id = "4034-16-LE26"
    pr5c, baseline = _production_plans(
        (tender_id,),
        pr4_display_kind_ids=frozenset({tender_id}),
    )
    assert pr5c.coalesced_tenders[0].identity_namespace == (
        IDENTITY_NS_MERCADO_PUBLICO
    )
    assert pr5c.coalesced_tenders[0].tender_key_kind == "codigo_externo"

    result = augment_product_relevance_plan(
        pr5c=pr5c,
        baseline=baseline,
        verified=_verified_evidence(
            {tender_id: ("adquisición de balanza analítica",)}
        ),
    )

    assert result.bound_tender_ids == (tender_id,)
    assert result.unused_tender_ids == ()
    assert result.bindings[0].coalesced_tender_id == tender_id


def test_direct_verified_model_cannot_double_bind_casefold_tender_aliases() -> None:
    production_id = "1007-1-LE26"
    alias = production_id.casefold()
    pr5c, baseline = _production_plans((production_id,))
    verified = _verified_evidence(
        {
            production_id: ("adquisición de balanza analítica",),
            alias: ("compra de centrífuga refrigerada",),
        }
    )

    with pytest.raises(
        AnexoIntegrationError,
        match="multiple annex bundle identities bind to one production tender",
    ):
        augment_product_relevance_plan(
            pr5c=pr5c,
            baseline=baseline,
            verified=verified,
        )


def test_extra_evidence_is_unused_and_never_synthesizes_a_tender() -> None:
    known_id = "1001-1-LE26"
    extra_id = "9999-99-LE26"
    pr5c, baseline = _production_plans((known_id,))
    verified = _verified_evidence(
        {
            known_id: ("adquisición de autoclave",),
            extra_id: ("adquisición de centrífuga",),
        }
    )

    result = augment_product_relevance_plan(
        pr5c=pr5c, baseline=baseline, verified=verified
    )

    assert result.bound_tender_ids == (known_id,)
    assert result.unused_tender_ids == (extra_id,)
    assert {d.coalesced_tender_id for d in result.plan.tender_decisions} == {known_id}
    assert all(
        unit.coalesced_tender_id == known_id for unit in result.plan.product_text_units
    )
    assert all(binding.tender_id != extra_id for binding in result.bindings)


def test_baseline_units_and_unaffected_tender_decision_are_preserved_exactly() -> None:
    affected_id = "1002-1-LE26"
    unaffected_id = "1002-2-LE26"
    pr5c, baseline = _production_plans((affected_id, unaffected_id))
    verified = _verified_evidence({affected_id: ("adquisición de autoclave",)})
    baseline_units = {unit.unit_id: unit for unit in baseline.product_text_units}
    baseline_decisions = _decision_by_tender(baseline)

    result = augment_product_relevance_plan(
        pr5c=pr5c, baseline=baseline, verified=verified
    )
    final_units = {unit.unit_id: unit for unit in result.plan.product_text_units}
    final_decisions = _decision_by_tender(result.plan)

    assert baseline_units.keys() < final_units.keys()
    assert all(final_units[unit_id] == unit for unit_id, unit in baseline_units.items())
    assert final_decisions[unaffected_id] == baseline_decisions[unaffected_id]
    assert result.affected_coalesced_tender_ids == (affected_id,)


def test_annex_units_use_canonical_classifier_and_affected_aggregator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tender_id = "1003-1-LE26"
    pr5c, baseline = _production_plans((tender_id,))
    verified = _verified_evidence({tender_id: ("adquisición de autoclave",)})
    classifier_calls: list[str] = []
    aggregator_calls: list[str] = []
    real_classifier = augment_module.relevance_rules.classify_product_text_unit
    real_aggregator = augment_module.relevance_aggregate.aggregate_tender_decision

    def classifier_spy(unit: Any) -> Any:
        classifier_calls.append(unit.unit_id)
        return real_classifier(unit)

    def aggregator_spy(tender: Any, decisions: Any, **kwargs: Any) -> Any:
        aggregator_calls.append(tender.coalesced_tender_id)
        return real_aggregator(tender, decisions, **kwargs)

    monkeypatch.setattr(
        augment_module.relevance_rules,
        "classify_product_text_unit",
        classifier_spy,
    )
    monkeypatch.setattr(
        augment_module.relevance_aggregate,
        "aggregate_tender_decision",
        aggregator_spy,
    )

    result = augment_product_relevance_plan(
        pr5c=pr5c, baseline=baseline, verified=verified
    )

    assert classifier_calls == [result.bindings[0].unit.unit_id]
    assert aggregator_calls == [tender_id]


def test_incomplete_bundle_without_positive_withholds_negative_proof() -> None:
    tender_id = "1004-1-LE26"
    pr5c, baseline = _production_plans((tender_id,))
    verified = _verified_evidence(
        {tender_id: ("mantención preventiva de autoclave",)},
        incomplete_tender_ids=frozenset({tender_id}),
    )
    baseline_decision = _decision_by_tender(baseline)[tender_id]

    result = augment_product_relevance_plan(
        pr5c=pr5c, baseline=baseline, verified=verified
    )
    final_decision = _decision_by_tender(result.plan)[tender_id]

    assert result.coverage_by_tender[tender_id].is_complete is False
    assert result.bindings[0].decision.relevance_class == "service_or_maintenance_only"
    assert result.bindings[0].applied is False
    assert (
        result.bindings[0].withholding_reason
        == "negative_proof_withheld_incomplete_coverage"
    )
    assert result.affected_coalesced_tender_ids == ()
    assert final_decision == baseline_decision
    assert final_decision.relevance_class not in {
        "service_or_maintenance_only",
        "consumable_or_reagent",
    }


def test_incomplete_bundle_positive_survives_and_autoclave_stays_out_of_capability() -> (
    None
):
    tender_id = "986278-12-LE26"
    pr5c, baseline = _production_plans((tender_id,))
    verified = _verified_evidence(
        {tender_id: ("adquisición de autoclave",)},
        incomplete_tender_ids=frozenset({tender_id}),
    )

    result = augment_product_relevance_plan(
        pr5c=pr5c, baseline=baseline, verified=verified
    )
    decision = _decision_by_tender(result.plan)[tender_id]

    assert result.coverage_by_tender[tender_id].is_complete is False
    assert result.bindings[0].applied is True
    assert result.bindings[0].withholding_reason is None
    assert result.bindings[0].decision.relevance_class == "strong_equipment_class"
    assert result.bindings[0].decision.canonical_equipment_classes == ("autoclave",)
    assert decision.canonical_equipment_classes == ("autoclave",)
    assert match_catalog_status("autoclave") == MATCH_OUTSIDE_CATALOG
    assert (
        classify_equipment_capability("autoclave").capability == CAPABILITY_OUT_OF_SCOPE
    )


def test_augmentation_digest_plan_and_provenance_are_order_deterministic() -> None:
    tender_id = "1005-1-LE26"
    pr5c, baseline = _production_plans((tender_id,))
    texts = (
        "adquisición de autoclave",
        "compra de centrífuga refrigerada",
    )
    forward = augment_product_relevance_plan(
        pr5c=pr5c,
        baseline=baseline,
        verified=_verified_evidence({tender_id: texts}),
    )
    reversed_input = augment_product_relevance_plan(
        pr5c=pr5c,
        baseline=baseline,
        verified=_verified_evidence(
            {tender_id: texts},
            reverse_chunks=True,
        ),
    )

    assert reversed_input.semantic_digest == forward.semantic_digest
    assert reversed_input.plan == forward.plan
    assert reversed_input.plan.fingerprints == forward.plan.fingerprints
    assert [b.provenance_dict() for b in reversed_input.bindings] == [
        b.provenance_dict() for b in forward.bindings
    ]


def test_every_segment_binds_one_recomputable_unit_and_canonical_decision() -> None:
    tender_id = "1006-1-LE26"
    pr5c, baseline = _production_plans((tender_id,))
    verified = _verified_evidence(
        {
            tender_id: (
                "adquisición de autoclave",
                "compra de centrífuga refrigerada",
            )
        }
    )

    result = augment_product_relevance_plan(
        pr5c=pr5c, baseline=baseline, verified=verified
    )
    annex_reconciliation = result.plan.reconciliation["annex_integration"]
    binding_reconciliation = annex_reconciliation["annex_binding_reconciliation"]
    plan_units = {unit.unit_id: unit for unit in result.plan.product_text_units}
    plan_unit_decisions = {
        decision.unit_id: decision for decision in result.plan.unit_decisions
    }

    assert binding_reconciliation == {
        "ok": True,
        "equation": (
            "verified_annex_segments = applied_annex_units ⊎ "
            "explicitly_withheld_annex_units; each segment binds exactly one "
            "recomputable unit and its canonical unit decision"
        ),
        "segment_count": 2,
        "unit_count": 2,
        "applied_count": 2,
        "withheld_count": 0,
    }
    assert len({binding.segment.segment_id for binding in result.bindings}) == 2
    for binding in result.bindings:
        assert plan_units[binding.unit.unit_id] == binding.unit
        assert plan_unit_decisions[binding.unit.unit_id] == binding.decision
        assert binding.decision.unit_id == binding.unit.unit_id
        assert binding.provenance_dict()["segment_id"] == binding.segment.segment_id


def test_historical_replay_union_codes_bind_without_identity_rewriting() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    tender_ids = tuple(sorted(fixture["expected_historical_replay_union_ids"]))
    pr5c, baseline = _production_plans(tender_ids)
    verified = _verified_evidence(
        {tender_id: ("adquisición de autoclave",) for tender_id in tender_ids}
    )

    result = augment_product_relevance_plan(
        pr5c=pr5c, baseline=baseline, verified=verified
    )

    assert result.bound_tender_ids == tender_ids
    assert result.unused_tender_ids == ()
    assert result.affected_coalesced_tender_ids == tender_ids
    assert {binding.tender_id for binding in result.bindings} == set(tender_ids)
