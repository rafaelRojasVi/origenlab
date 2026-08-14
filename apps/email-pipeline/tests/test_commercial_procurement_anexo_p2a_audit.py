"""Focused audit contracts for guarded P2A annex integration.

The annex rows in this module are local deterministic fixtures.  They enter
through the production P2A augmenter and canonical PR5D rules; the institution
results are deliberately minimal projections shaped like the production
planner output so this file can isolate the audit boundary.
"""

from __future__ import annotations

import copy
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

from origenlab_email_pipeline.chilecompra_anexo_evidence.redaction import (
    ArtifactRedactionError,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
)
from origenlab_email_pipeline.commercial_procurement_anexo_integration.audit import (
    build_annex_integration_audit,
    write_annex_integration_sidecars,
)
from origenlab_email_pipeline.commercial_procurement_anexo_integration.augment import (
    AnexoAugmentationResult,
    augment_product_relevance_plan,
)
from origenlab_email_pipeline.commercial_procurement_anexo_integration.models import (
    VerifiedAnexoEvidence,
)
from origenlab_email_pipeline.commercial_procurement_anexo_recognition.corpus import (
    AnexoEvidenceBundleSet,
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
from origenlab_email_pipeline.commercial_procurement_institution_prospects.constants import (
    OPERATOR_QUEUE_NAMES,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.planner import (
    InstitutionProspectPlanResult,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.queues import (
    EMPTY_QUEUE_HEADERS,
    build_operator_queues,
    queue_row_id,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
    ProductRelevancePlanResult,
)


AS_OF_UTC = "2026-08-12T12:00:00Z"
EXPIRED_CLOSE_UTC = "2026-07-28T17:00:00Z"
OPEN_CLOSE_UTC = "2026-08-20T17:00:00Z"


def _source_tender(tender_id: str) -> dict[str, Any]:
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
) -> tuple[CandidatePlanResult, ProductRelevancePlanResult]:
    """Make a minimal PR5C/PR5D pair, then stamp the real identity contract."""
    source_rows = tuple(_source_tender(tender_id) for tender_id in tender_ids)
    pair = build_relevance_plan_pair(
        source_rows,
        anexo=_empty_evidence(),
        as_of_utc=AS_OF_UTC,
    )
    candidate = build_candidate_plan(
        source_rows,
        as_of_utc=AS_OF_UTC,
        input_digest=pair.input_digest,
    )
    canonical_tenders = tuple(
        replace(
            tender,
            canonical_tender_key=tender.coalesced_tender_id,
            identity_namespace=IDENTITY_NS_MERCADO_PUBLICO,
            tender_key_kind=TENDER_KEY_KIND_MERCADO_PUBLICO,
        )
        for tender in candidate.coalesced_tenders
    )
    return replace(candidate, coalesced_tenders=canonical_tenders), pair.current


def _chunk(tender_id: str, text: str) -> dict[str, Any]:
    chunk_id = hashlib.sha256(f"{tender_id}\0{text}".encode()).hexdigest()[:32]
    return {
        "tender_id": tender_id,
        "attachment_id": f"att-{tender_id}",
        "chunk_id": chunk_id,
        "ordinal": 1,
        "source_format": "docx",
        "locator_type": "docx_table",
        "locator": {"section": "body", "table": 1},
        "text": f"[r1] {text}",
    }


def _verified_evidence(
    texts: dict[str, str],
    *,
    incomplete_ids: frozenset[str] = frozenset(),
) -> VerifiedAnexoEvidence:
    bundles: dict[str, TenderAttachmentBundle] = {}
    tender_digests: dict[str, str] = {}

    for tender_id, text in sorted(
        texts.items(),
        key=lambda item: item[0].casefold(),
    ):
        incomplete = tender_id in incomplete_ids
        outcome = (
            OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT
            if incomplete
            else OUTCOME_EXTRACTION_SUCCESS
        )

        raw_chunk = _chunk(tender_id, text)
        attachment_id = f"att-{tender_id}"

        chunk = EvidenceChunk(
            chunk_id=str(raw_chunk["chunk_id"]),
            attachment_id=attachment_id,
            tender_id=tender_id,
            source_format=str(raw_chunk["source_format"]),
            locator_type=str(raw_chunk["locator_type"]),
            locator=dict(raw_chunk["locator"]),
            text=text,
            text_sha256=hashlib.sha256(
                text.encode("utf-8")
            ).hexdigest(),
            char_count=len(text),
            ordinal=int(raw_chunk.get("ordinal") or 1),
            container_attachment_id=raw_chunk.get(
                "container_attachment_id"
            ),
            member_path=raw_chunk.get("member_path"),
            warnings=tuple(raw_chunk.get("warnings") or ()),
        )

        attachment_sha = hashlib.sha256(
            f"p2a-audit-fixture\\0{tender_id}".encode()
        ).hexdigest()

        attachment = AttachmentRecord(
            attachment_id=attachment_id,
            tender_id=tender_id,
            listing_ordinal=1,
            row_ordinal=1,
            portal_filename=f"anexo-{tender_id}.docx",
            safe_filename=f"anexo-{tender_id}.docx",
            tipo="fixture",
            descripcion="P2A audit fixture",
            fecha_adjunto="2026-08-12",
            content_type=(
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
            detected_format=FORMAT_DOCX,
            extension=".docx",
            byte_count=len(text.encode("utf-8")),
            sha256=attachment_sha,
            download_status="downloaded",
            outcome=outcome,
            role_tag=ROLE_TECHNICAL_SPEC,
            evidence_attachment_id=attachment_id,
            extraction=AttachmentExtraction(
                outcome=outcome,
                detected_format=FORMAT_DOCX,
                chunk_count=1,
                char_count=len(text),
                extractor="p2a_audit_fixture",
            ),
        )

        incomplete_reason_codes = (
            (OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT,)
            if incomplete
            else ()
        )

        digest = bundle_semantic_digest(
            tender_id=tender_id,
            attachments=(attachment,),
            chunks=(chunk,),
            incomplete_reason_codes=incomplete_reason_codes,
        )

        bundle = TenderAttachmentBundle(
            tender_id=tender_id,
            source_kind="p2a_audit_fixture",
            listing_page_count=1,
            attachments_discovered=1,
            attachments_downloaded=1,
            attachments=(attachment,),
            chunks=(chunk,),
            bytes_downloaded=attachment.byte_count,
            incomplete_reason_codes=incomplete_reason_codes,
            semantic_digest=digest,
            outcome_counts={outcome: 1},
        )

        bundles[tender_id] = bundle
        tender_digests[tender_id] = digest

    digest = canonical_json_digest(
        {
            "tender_semantic_digests": dict(
                sorted(tender_digests.items())
            )
        }
    )

    return VerifiedAnexoEvidence(
        bundles=bundles,
        semantic_digest=digest,
        tender_semantic_digests=tender_digests,
        summary={"fixture": True},
        attachment_manifest={"fixture": True},
        extraction_manifest={"fixture": True},
    )


def _augment(
    production_ids: tuple[str, ...],
    evidence_texts: dict[str, str],
    *,
    incomplete_ids: frozenset[str] = frozenset(),
) -> AnexoAugmentationResult:
    pr5c, baseline = _production_plans(production_ids)
    return augment_product_relevance_plan(
        pr5c=pr5c,
        baseline=baseline,
        verified=_verified_evidence(
            evidence_texts,
            incomplete_ids=incomplete_ids,
        ),
    )


def _baseline_row(tender_code: str) -> dict[str, Any]:
    return {
        "coalesced_tender_id": tender_code,
        "tender_code": tender_code,
        "institution_id": f"institution:{tender_code.casefold()}",
        "display_name": "Organismo de prueba",
        "projected_lifecycle_class": "closed",
        "lifecycle_class": "closed",
        "review_disposition": "not_catalog_eligible",
        "commercial_signal_type": "non_acquisition_context",
        "catalog_fit_status": "not_applicable",
        "catalog_match_status": "outside_recorded_catalog",
        "canonical_equipment_category": "",
        "equipment_scopes": [],
        "purchase_intent": False,
        "commercial_truth_source": "no_product_line_evidence",
        "publication_timestamp": "2026-05-26T16:53:56Z",
        "close_timestamp": EXPIRED_CLOSE_UTC,
        "closing_soon_bucket": "closed",
        "opportunity_urgency_band": "none",
        "prospect_strength_band": "none",
        "reason_codes": [],
        "eligibility_reason_codes": [],
        "claim_ids": [],
    }


def _enabled_row(
    tender_code: str,
    category: str,
    *,
    current: bool = False,
    out_of_scope: bool = False,
    purchase: bool = True,
) -> dict[str, Any]:
    lifecycle = "active_open" if current else "closed"
    actionable = not out_of_scope
    return {
        "coalesced_tender_id": tender_code,
        "tender_code": tender_code,
        "institution_id": f"institution:{tender_code.casefold()}",
        "display_name": "Organismo de prueba",
        "projected_lifecycle_class": lifecycle,
        "lifecycle_class": lifecycle,
        "review_disposition": (
            "possible_fit_needs_specs" if actionable else "out_of_catalog_scope"
        ),
        "commercial_signal_type": (
            "equipment_purchase_signal" if purchase else "supplier_required_context"
        ),
        "catalog_fit_status": (
            "possible_fit_needs_specs" if actionable else "out_of_catalog_scope"
        ),
        "catalog_match_status": (
            "catalog_equipment_class_needs_confirmation"
            if actionable
            else "outside_recorded_catalog"
        ),
        "canonical_equipment_category": category,
        "equipment_scopes": ["complete_equipment"] if purchase else [],
        "purchase_intent": purchase,
        "commercial_truth_source": "line_claim",
        "publication_timestamp": "2026-05-26T16:53:56Z",
        "close_timestamp": OPEN_CLOSE_UTC if current else EXPIRED_CLOSE_UTC,
        "closing_soon_bucket": "open" if current else "closed",
        "opportunity_urgency_band": "normal" if current else "none",
        "prospect_strength_band": "medium",
        "reason_codes": ["annex_evidence"],
        "eligibility_reason_codes": ["annex_equipment_purchase"],
        "claim_ids": [f"production-line-claim:{tender_code}:{category}"],
        # PR3: this fixture predates procurement-method evidence; default to
        # open/public for actionable rows so the fail-closed eligibility gate
        # does not block scenarios this fixture already marks as in-scope.
        "procurement_eligibility_status": "open_public" if actionable else "unknown",
    }


def _line_row(tender_code: str) -> dict[str, Any]:
    return {
        "coalesced_tender_id": tender_code,
        "unit_id": f"line:{tender_code}",
        "line_disposition": "assigned",
    }


def _institution_result(
    tender_rows: list[dict[str, Any]],
    line_rows: list[dict[str, Any]],
) -> InstitutionProspectPlanResult:
    queues = build_operator_queues(
        profiles=[],
        tender_rows=copy.deepcopy(tender_rows),
        line_rows=copy.deepcopy(line_rows),
        families=[],
        clusters=[],
        match_review=[],
        contact_gaps=[],
        as_of_utc=AS_OF_UTC,
    )
    semantic_digest = canonical_json_digest(
        {"tender_rows": tender_rows, "line_rows": line_rows, "queues": queues}
    )
    return InstitutionProspectPlanResult(
        as_of_utc=AS_OF_UTC,
        run_context="deterministic_p2a_audit_fixture",
        planner_version="production_shape_test_fixture",
        profiles=[],
        reconciliation={"ok": True},
        fingerprints={"semantic_digest": semantic_digest},
        dependency_fingerprints={
            "pr5e_semantic_digest": hashlib.sha256(
                f"pr5e:{semantic_digest}".encode()
            ).hexdigest()
        },
        counts={"source_tenders": len(tender_rows)},
        match_review_queue=list(queues["institution_match_review_queue"]),
        contact_gap_queue=list(queues["contact_gap_queue"]),
        operator_queues=queues,
        tender_rows=copy.deepcopy(tender_rows),
        line_rows=copy.deepcopy(line_rows),
    )


def _audit_pair(
    augmentation: AnexoAugmentationResult,
    *,
    enabled_rows: list[dict[str, Any]],
) -> tuple[InstitutionProspectPlanResult, InstitutionProspectPlanResult]:
    production_ids = tuple(
        tender.coalesced_tender_id
        for tender in sorted(
            augmentation.plan.tender_decisions,
            key=lambda decision: decision.coalesced_tender_id,
        )
    )
    baseline = _institution_result(
        [_baseline_row(tender_id) for tender_id in production_ids],
        [],
    )
    enabled = _institution_result(
        enabled_rows,
        [_line_row(str(row["coalesced_tender_id"])) for row in enabled_rows],
    )
    return baseline, enabled


def _single_claim_category(
    augmentation: AnexoAugmentationResult, source_tender_id: str
) -> str:
    categories = {
        claim.matched_category
        for claim in augmentation.claims
        if claim.tender_id == source_tender_id
    }
    assert len(categories) == 1
    return categories.pop()


def test_time_independent_eligibility_is_exact_and_expired_not_current() -> None:
    evidence_texts = {
        "4034-16-LE26": "adquisición de balanza digital de laboratorio",
        "1057510-40-LP26": "compra de centrífuga de sobremesa",
        "1057510-53-LP26": "compra de centrífuga refrigerada",
    }
    production_ids = tuple(sorted(evidence_texts))
    augmentation = _augment(production_ids, evidence_texts)
    enabled_rows = [
        _enabled_row(
            tender_id,
            _single_claim_category(augmentation, tender_id),
        )
        for tender_id in production_ids
    ]
    baseline, enabled = _audit_pair(augmentation, enabled_rows=enabled_rows)

    reconciliation, provenance = build_annex_integration_audit(
        augmentation=augmentation,
        baseline=baseline,
        enabled=enabled,
    )

    expected = ["1057510-40-LP26", "1057510-53-LP26", "4034-16-LE26"]
    assert reconciliation["time_independent_annex_eligible_ids"] == expected
    assert reconciliation["expired_but_annex_eligible_ids"] == expected
    assert reconciliation["current_enabled_queue_delta"] == {
        "baseline_count": 0,
        "enabled_count": 0,
        "annex_caused_entry_queue_row_ids": [],
        "annex_caused_entry_tender_ids": [],
        "annex_caused_removal_queue_row_ids": [],
        "annex_caused_removal_tender_ids": [],
    }
    assert enabled.operator_queues["current_opportunity_queue"] == []
    assert {row["record_kind"] for row in provenance} == {
        "annex_commercial_eligibility"
    }
    assert all(
        set(row["current_opportunity_blockers"])
        == {"lifecycle_not_active_open", "close_timestamp_elapsed"}
        for row in provenance
    )


def test_source_tender_id_casing_is_preserved_in_eligibility_and_provenance() -> None:
    production_id = "2001-77-LE26"
    source_id = "2001-77-Le26"
    augmentation = _augment(
        (production_id,),
        {source_id: "compra de centrífuga de laboratorio"},
    )
    category = _single_claim_category(augmentation, source_id)
    baseline, enabled = _audit_pair(
        augmentation,
        enabled_rows=[_enabled_row(production_id, category)],
    )

    reconciliation, provenance = build_annex_integration_audit(
        augmentation=augmentation,
        baseline=baseline,
        enabled=enabled,
    )

    assert augmentation.bound_tender_ids == (source_id,)
    assert reconciliation["time_independent_annex_eligible_ids"] == [source_id]
    assert reconciliation["expired_but_annex_eligible_ids"] == [source_id]
    assert [row["tender_id"] for row in provenance] == [source_id]


def test_current_queue_delta_uses_stable_ids_and_links_complete_provenance() -> None:
    production_ids = ("2003-2-LE26", "2003-1-LE26")
    evidence_texts = {
        "2003-2-LE26": "adquisición de balanza analítica",
        "2003-1-LE26": "compra de centrífuga refrigerada",
    }
    augmentation = _augment(production_ids, evidence_texts)
    enabled_rows = [
        _enabled_row(
            tender_id,
            _single_claim_category(augmentation, tender_id),
            current=True,
        )
        for tender_id in reversed(production_ids)
    ]
    baseline, enabled = _audit_pair(augmentation, enabled_rows=enabled_rows)

    reconciliation, provenance = build_annex_integration_audit(
        augmentation=augmentation,
        baseline=baseline,
        enabled=enabled,
    )

    queue_rows = enabled.operator_queues["current_opportunity_queue"]
    expected_ids = sorted(str(row["queue_row_id"]) for row in queue_rows)
    delta = reconciliation["current_enabled_queue_delta"]
    assert delta["annex_caused_entry_queue_row_ids"] == expected_ids
    assert delta["annex_caused_entry_tender_ids"] == [
        "2003-1-LE26",
        "2003-2-LE26",
    ]
    assert reconciliation["operator_queue_entries_caused_by_annex"] == 2
    assert reconciliation["operator_queue_rows_annex_backed"] == 2
    assert [row["queue_row_id"] for row in provenance] == expected_ids
    assert all(row["record_kind"] == "current_queue_row" for row in provenance)
    assert all(row["entry_caused_by_annex"] is True for row in provenance)
    assert all(row["annex_claim_ids"] for row in provenance)
    assert all(row["unit_ids"] and row["segment_ids"] for row in provenance)
    for row in queue_rows:
        assert row["queue_row_id"] == queue_row_id(
            "current_opportunity_queue", row
        )


def test_out_of_scope_acquisitions_and_suppressed_context_remain_distinct() -> None:
    autoclave_id = "2002-1-LE26"
    microscope_id = "2002-2-LE26"
    supplier_id = "2002-3-LE26"
    evidence_texts = {
        autoclave_id: "adquisición de autoclave",
        microscope_id: "compra de microscopio de laboratorio",
        supplier_id: "el oferente deberá disponer de autoclave",
    }
    production_ids = tuple(evidence_texts)
    augmentation = _augment(
        production_ids,
        evidence_texts,
        incomplete_ids=frozenset({microscope_id}),
    )
    enabled_rows = [
        _enabled_row(autoclave_id, "autoclave", out_of_scope=True),
        _enabled_row(microscope_id, "microscope", out_of_scope=True),
        _enabled_row(
            supplier_id,
            "autoclave",
            out_of_scope=True,
            purchase=False,
        ),
    ]
    baseline, enabled = _audit_pair(augmentation, enabled_rows=enabled_rows)

    reconciliation, _provenance = build_annex_integration_audit(
        augmentation=augmentation,
        baseline=baseline,
        enabled=enabled,
    )

    assert reconciliation["out_of_scope_annex_acquisitions"] == [
        {
            "tender_id": autoclave_id,
            "equipment_category": "autoclave",
            "reason_codes": [
                "catalog_fit_status_not_actionable",
                "catalog_match_status_not_actionable",
                "disposition_not_catalog_eligible",
            ],
        },
        {
            "tender_id": microscope_id,
            "equipment_category": "microscope",
            "reason_codes": [
                "catalog_fit_status_not_actionable",
                "catalog_match_status_not_actionable",
                "disposition_not_catalog_eligible",
            ],
        },
    ]
    assert supplier_id in reconciliation["suppressed_non_acquisition_ids"]
    assert all(
        row["tender_id"] != supplier_id
        for row in reconciliation["out_of_scope_annex_acquisitions"]
    )
    assert reconciliation["coverage_debt_ids"] == [microscope_id]
    assert reconciliation["annex_incomplete_tender_count"] == 1
    assert reconciliation["coverage_debt_outcome_counts"] == {
        "partial_due_to_safety_limit": 1
    }


def test_frozen52_named_out_of_scope_and_negative_contracts() -> None:
    autoclave_id = "986278-12-LE26"
    microscope_id = "1057510-30-LR26"
    accessory_id = "699866-86-LE26"
    maintenance_id = "745712-14-LE26"
    method_id = "1267885-18-LE26"
    supplier_id = "1057374-17-LP26"
    evidence_texts = {
        autoclave_id: "adquisición de autoclave",
        microscope_id: "Nombre: Microscopio binocular para laboratorio clínico",
        accessory_id: "Papel limpia lentes para microscopio de laboratorio",
        maintenance_id: "Mantención y calibración de centrífuga clínica instalada",
        method_id: (
            "Velocidad de sedimentación ensayo diagnóstico automatizado"
        ),
        supplier_id: (
            "El oferente debe disponer de forma permanente y a su costo "
            "de un microscopio óptico"
        ),
    }
    augmentation = _augment(tuple(evidence_texts), evidence_texts)
    enabled_rows = [
        _enabled_row(autoclave_id, "autoclave", out_of_scope=True),
        _enabled_row(microscope_id, "microscope", out_of_scope=True),
        *[
            _baseline_row(tender_id)
            for tender_id in (
                accessory_id,
                maintenance_id,
                method_id,
                supplier_id,
            )
        ],
    ]
    baseline, enabled = _audit_pair(augmentation, enabled_rows=enabled_rows)

    reconciliation, _provenance = build_annex_integration_audit(
        augmentation=augmentation,
        baseline=baseline,
        enabled=enabled,
    )

    assert {
        (row["tender_id"], row["equipment_category"])
        for row in reconciliation["out_of_scope_annex_acquisitions"]
    } == {
        (autoclave_id, "autoclave"),
        (microscope_id, "microscope"),
    }
    assert set(reconciliation["suppressed_non_acquisition_ids"]) == {
        accessory_id,
        maintenance_id,
        method_id,
        supplier_id,
    }
    assert reconciliation["time_independent_annex_eligible_ids"] == []
    assert reconciliation["operator_queue_entries_caused_by_annex"] == 0


def test_reconciliation_is_deterministic_and_all_safety_authorities_are_false() -> (
    None
):
    tender_id = "2004-1-LE26"
    augmentation = _augment(
        (tender_id,),
        {tender_id: "compra de centrífuga de laboratorio"},
    )
    category = _single_claim_category(augmentation, tender_id)
    baseline, enabled = _audit_pair(
        augmentation,
        enabled_rows=[_enabled_row(tender_id, category, current=True)],
    )

    first = build_annex_integration_audit(
        augmentation=augmentation,
        baseline=baseline,
        enabled=enabled,
    )
    second = build_annex_integration_audit(
        augmentation=augmentation,
        baseline=copy.deepcopy(baseline),
        enabled=copy.deepcopy(enabled),
    )

    assert second == first
    reconciliation, provenance = first
    without_digest = {
        key: value for key, value in reconciliation.items() if key != "semantic_digest"
    }
    assert reconciliation["semantic_digest"] == canonical_json_digest(without_digest)
    assert reconciliation["ok"] is True
    for key in (
        "persistent_state_mutated",
        "sqlite_writes",
        "postgres_writes",
        "gmail_writes",
        "network_annex_acquisition",
        "contact_authorization",
        "outreach_authorization",
        "pr5f_started",
        "p2b_started",
    ):
        assert reconciliation[key] is False
    assert reconciliation["current_opportunity_gate_unchanged"] is True
    assert all(row["contact_authorization"] is False for row in provenance)
    assert all(row["outreach_authorization"] is False for row in provenance)
    assert all(row["persistent_state_mutated"] is False for row in provenance)


def test_sidecars_round_trip_without_raw_annex_text_or_queue_schema_mutation(
    tmp_path: Path,
) -> None:
    tender_id = "2005-1-LE26"
    raw_text = "adquisición de centrífuga refrigerada de laboratorio"
    augmentation = _augment((tender_id,), {tender_id: raw_text})
    category = _single_claim_category(augmentation, tender_id)
    baseline, enabled = _audit_pair(
        augmentation,
        enabled_rows=[_enabled_row(tender_id, category, current=True)],
    )
    baseline_queues_before = copy.deepcopy(baseline.operator_queues)
    enabled_queues_before = copy.deepcopy(enabled.operator_queues)

    reconciliation, provenance = build_annex_integration_audit(
        augmentation=augmentation,
        baseline=baseline,
        enabled=enabled,
    )
    paths = write_annex_integration_sidecars(
        tmp_path,
        reconciliation=reconciliation,
        provenance_rows=provenance,
    )

    assert set(paths) == {
        "annex_integration_reconciliation.json",
        "annex_opportunity_provenance.jsonl",
    }
    parsed_reconciliation = json.loads(
        (tmp_path / "annex_integration_reconciliation.json").read_text(
            encoding="utf-8"
        )
    )
    parsed_provenance = [
        json.loads(line)
        for line in (tmp_path / "annex_opportunity_provenance.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert parsed_reconciliation == reconciliation
    assert parsed_provenance == list(provenance)
    serialized = json.dumps(
        {"reconciliation": parsed_reconciliation, "provenance": parsed_provenance},
        ensure_ascii=False,
        sort_keys=True,
    )
    assert raw_text not in serialized
    for forbidden_field in ("text_raw", "text_normalized", "evidence_excerpt"):
        assert forbidden_field not in serialized

    assert baseline.operator_queues == baseline_queues_before
    assert enabled.operator_queues == enabled_queues_before
    assert tuple(sorted(enabled.operator_queues)) == tuple(sorted(OPERATOR_QUEUE_NAMES))
    for row in enabled.operator_queues["current_opportunity_queue"]:
        assert set(row) == set(EMPTY_QUEUE_HEADERS["current_opportunity_queue"])


def test_unredacted_portal_token_fails_before_sidecar_publication() -> None:
    tender_id = "2006-1-LE26"
    augmentation = _augment(
        (tender_id,),
        {tender_id: "adquisición de centrífuga"},
    )
    category = _single_claim_category(augmentation, tender_id)
    baseline, enabled = _audit_pair(
        augmentation,
        enabled_rows=[_enabled_row(tender_id, category, current=True)],
    )
    enabled.operator_queues["current_opportunity_queue"][0]["institution_id"] = (
        "https://portal.invalid/documento?qs=opaque-secret"
    )

    with pytest.raises(ArtifactRedactionError, match="portal token"):
        build_annex_integration_audit(
            augmentation=augmentation,
            baseline=baseline,
            enabled=enabled,
        )
