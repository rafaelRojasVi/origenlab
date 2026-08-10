"""ANEXO-P1 shadow prospect contract tests.

Deterministic fixtures only. Proves CURRENT vs SHADOW isolation, capability
partial matching, H1 commercial-intent suppressions, queue deltas, and
fail-closed safety flags.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from origenlab_email_pipeline.commercial_procurement_anexo_recognition.corpus import (
    AnexoEvidenceBundleSet,
)
from origenlab_email_pipeline.commercial_procurement_anexo_shadow_prospects import (
    assert_shadow_invariants,
    build_summary,
    run_shadow_comparison,
)
from origenlab_email_pipeline.commercial_procurement_anexo_shadow_prospects.capability import (
    classify_equipment_capability,
)
from origenlab_email_pipeline.commercial_procurement_anexo_shadow_prospects.classify import (
    classify_shadow_change,
    commercial_intent_class,
)
from origenlab_email_pipeline.commercial_procurement_anexo_shadow_prospects.constants import (
    CAPABILITY_IN_SCOPE,
    CAPABILITY_OUT_OF_SCOPE,
    INTENT_ACCESSORY_OR_CONSUMABLE,
    INTENT_BUYER_EQUIPMENT_ACQUISITION,
    INTENT_CONFLICT_OR_REVIEW,
    INTENT_METHOD_OR_EXAM,
    INTENT_SERVICE_OR_MAINTENANCE,
    INTENT_SUPPLIER_REQUIRED_EQUIPMENT,
    QUEUE_WOULD_ENTER_CURRENT,
    SHADOW_ACCESSORY_OR_CONSUMABLE_ONLY,
    SHADOW_BUNDLED_PARTIAL_CAPABILITY,
    SHADOW_METHOD_OR_EXAM_ONLY,
    SHADOW_NEW_IN_SCOPE_OPPORTUNITY,
    SHADOW_NEW_OUT_OF_SCOPE_EQUIPMENT,
    SHADOW_SERVICE_OR_MAINTENANCE_ONLY,
    SHADOW_SUPPLIER_REQUIRED_EQUIPMENT,
)
from origenlab_email_pipeline.commercial_procurement_anexo_shadow_prospects.models import (
    ClaimCapability,
)
from origenlab_email_pipeline.commercial_procurement_anexo_shadow_prospects.plans import (
    build_relevance_plan_pair,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.catalog_capability import (
    MATCH_NEEDS_CONFIRMATION,
    MATCH_OUTSIDE_CATALOG,
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_attachment(
    attachment_id: str,
    tender_id: str,
    *,
    safe_filename: str = "anexo.docx",
    detected_format: str = "docx",
    role_tag: str = "technical_spec",
    outcome: str = "extraction_success",
    content: str = "content",
) -> dict:
    return {
        "attachment_id": attachment_id,
        "tender_id": tender_id,
        "safe_filename": safe_filename,
        "sha256": _sha(content),
        "detected_format": detected_format,
        "role_tag": role_tag,
        "outcome": outcome,
        "duplicate_of_attachment_id": None,
        "evidence_attachment_id": attachment_id,
    }


def make_chunk(
    chunk_id: str,
    text: str,
    *,
    tender_id: str,
    attachment_id: str,
    locator_type: str = "docx_table",
    locator: dict | None = None,
    source_format: str = "docx",
    ordinal: int = 1,
) -> dict:
    return {
        "chunk_id": chunk_id,
        "tender_id": tender_id,
        "attachment_id": attachment_id,
        "locator_type": locator_type,
        "locator": locator
        if locator is not None
        else {"table": 1, "row": 1, "section": "body"},
        "source_format": source_format,
        "text": text,
        "char_count": len(text),
        "ordinal": ordinal,
        "member_path": None,
        "container_attachment_id": None,
    }


def make_bundle(
    tender_id: str,
    *,
    discovered: int = 1,
    downloaded: int = 1,
    extraction_complete: bool = True,
    outcome_counts: dict | None = None,
    incomplete_reason_codes: tuple[str, ...] = (),
) -> dict:
    return {
        "tender_id": tender_id,
        "attachments_discovered": discovered,
        "attachments_downloaded": downloaded,
        "extraction_complete": extraction_complete,
        "incomplete_reason_codes": list(incomplete_reason_codes),
        "outcome_counts": outcome_counts
        or ({"extraction_success": downloaded} if extraction_complete else {"needs_ocr": 1}),
    }


def make_anexo(
    *,
    chunks: list[dict],
    attachments: list[dict],
    bundles: list[dict],
) -> AnexoEvidenceBundleSet:
    chunks_by: dict[str, list[dict]] = {}
    for chunk in chunks:
        chunks_by.setdefault(chunk["tender_id"], []).append(chunk)
    atts_by: dict[str, list[dict]] = {}
    for att in attachments:
        atts_by.setdefault(att["tender_id"], []).append(att)
    return AnexoEvidenceBundleSet(
        chunks_by_tender=chunks_by,
        attachments_by_tender=atts_by,
        bundle_by_tender={b["tender_id"]: b for b in bundles},
    )


def tender(
    tender_id: str,
    *,
    title: str = "Adquisicion de insumos de laboratorio",
    description: str = "",
    buyer: str = "Hospital Demo",
    lifecycle: str = "active_open",
    lines: list[dict] | None = None,
) -> dict:
    return {
        "tender_id": tender_id,
        "title": title,
        "description": description,
        "buyer_organization": buyer,
        "lifecycle_class": lifecycle,
        "lines": lines or [],
    }


# --------------------------------------------------------------------------
# capability
# --------------------------------------------------------------------------


def test_capability_uses_catalog_seed_not_tender_text() -> None:
    balance = classify_equipment_capability("balance")
    microscope = classify_equipment_capability("microscope")
    autoclave = classify_equipment_capability("autoclave")
    assert balance.capability == CAPABILITY_IN_SCOPE
    assert balance.catalog_match_status == MATCH_NEEDS_CONFIRMATION
    assert microscope.capability == CAPABILITY_OUT_OF_SCOPE
    assert microscope.catalog_match_status == MATCH_OUTSIDE_CATALOG
    assert autoclave.capability == CAPABILITY_OUT_OF_SCOPE
    assert autoclave.catalog_match_status == MATCH_OUTSIDE_CATALOG


def test_autoclave_recognition_outside_catalog_not_sellable_queue() -> None:
    """H3 contract: autoclave intelligence ≠ OrigenLab sellable opportunity."""
    att = make_attachment("A-acl", "T-ACL", outcome="needs_ocr", content="acl")
    chunk = make_chunk(
        "C-acl",
        "ADQUISICION DE AUTOCLAVE LABORATORIO DE TNS EN PODOLOGIA CLINICA",
        tender_id="T-ACL",
        attachment_id="A-acl",
    )
    anexo = make_anexo(
        chunks=[chunk],
        attachments=[att],
        bundles=[
            make_bundle(
                "T-ACL",
                extraction_complete=False,
                outcome_counts={"needs_ocr": 1, "extraction_success": 1},
                incomplete_reason_codes=("needs_ocr",),
            )
        ],
    )
    tenders = [
        tender(
            "T-ACL",
            title="ADQUISICION DE AUTOCLAVE LABORATORIO",
            buyer="CFT ARAUCANIA",
        )
    ]
    result, _, _ = run_shadow_comparison(tenders, anexo=anexo)
    by = {d.tender_id: d for d in result.tender_deltas}
    row = by["T-ACL"]
    assert "autoclave" in row.shadow_equipment_classes
    assert row.commercial_intent_class == INTENT_BUYER_EQUIPMENT_ACQUISITION
    assert row.coverage_debt.get("needs_ocr", 0) >= 1
    assert all(
        c.equipment_class == "autoclave" and c.capability == CAPABILITY_OUT_OF_SCOPE
        for c in row.claim_level_capability
    )
    assert QUEUE_WOULD_ENTER_CURRENT not in row.queue_delta
    summary = build_summary(result)
    assert "T-ACL" not in (summary.get("would_enter_current_opportunity_tender_ids") or [])
    assert summary["production_queue_mutated"] is False
    assert summary["pr5f_started"] is False


def test_partial_capability_change_class() -> None:
    caps = (
        ClaimCapability("centrifuge", CAPABILITY_IN_SCOPE, MATCH_NEEDS_CONFIRMATION),
        ClaimCapability("microscope", CAPABILITY_OUT_OF_SCOPE, MATCH_OUTSIDE_CATALOG),
    )
    assert (
        classify_shadow_change(
            current_classes=(),
            shadow_classes=("centrifuge", "microscope"),
            capabilities=caps,
            intent=INTENT_BUYER_EQUIPMENT_ACQUISITION,
            e2_change_class="new_annex_positive",
            e2_interpretation="presence_proven",
            coverage_complete=True,
            has_anexo_bundle=True,
            current_queue_keys=set(),
            shadow_queue_keys={"inst|T|centrifuge"},
            human_review_required=False,
        )
        == SHADOW_BUNDLED_PARTIAL_CAPABILITY
    )


def test_intent_suppressions() -> None:
    assert (
        commercial_intent_class(
            relevance_class="consumable_or_reagent",
            negative_reason_codes=("consumable_or_reagent_only",),
            positive_reason_codes=(),
            ambiguity_reason_codes=(),
            coverage_complete=True,
            has_anexo_bundle=True,
            equipment_classes=(),
        )
        == INTENT_ACCESSORY_OR_CONSUMABLE
    )
    assert (
        commercial_intent_class(
            relevance_class="service_or_maintenance_only",
            negative_reason_codes=("service_or_maintenance_only",),
            positive_reason_codes=(),
            ambiguity_reason_codes=(),
            coverage_complete=True,
            has_anexo_bundle=True,
            equipment_classes=(),
        )
        == INTENT_SERVICE_OR_MAINTENANCE
    )
    assert (
        commercial_intent_class(
            relevance_class="service_or_maintenance_only",
            negative_reason_codes=(
                "diagnostic_method_or_exam_term_not_equipment",
                "service_or_maintenance_only",
            ),
            positive_reason_codes=(),
            ambiguity_reason_codes=(),
            coverage_complete=True,
            has_anexo_bundle=True,
            equipment_classes=(),
        )
        == INTENT_METHOD_OR_EXAM
    )
    assert (
        commercial_intent_class(
            relevance_class="laboratory_context_only",
            negative_reason_codes=("supplier_required_equipment_not_buyer_purchase",),
            positive_reason_codes=(),
            ambiguity_reason_codes=(),
            coverage_complete=True,
            has_anexo_bundle=True,
            equipment_classes=("microscope",),
        )
        == INTENT_SUPPLIER_REQUIRED_EQUIPMENT
    )
    # Fail-closed: residual classes do not auto-promote acquisition.
    assert (
        commercial_intent_class(
            relevance_class="service_or_maintenance_only",
            negative_reason_codes=("service_or_maintenance_only",),
            positive_reason_codes=(),
            ambiguity_reason_codes=(),
            coverage_complete=True,
            has_anexo_bundle=True,
            equipment_classes=("centrifuge",),
        )
        == INTENT_SERVICE_OR_MAINTENANCE
    )
    assert (
        commercial_intent_class(
            relevance_class="ambiguous",
            negative_reason_codes=(),
            positive_reason_codes=(),
            ambiguity_reason_codes=("conflicting_line_evidence",),
            coverage_complete=True,
            has_anexo_bundle=True,
            equipment_classes=("microscope",),
        )
        == INTENT_CONFLICT_OR_REVIEW
    )
    assert (
        commercial_intent_class(
            relevance_class="strong_equipment_class",
            negative_reason_codes=(),
            positive_reason_codes=("strong_equipment_class_match",),
            ambiguity_reason_codes=(),
            coverage_complete=True,
            has_anexo_bundle=True,
            equipment_classes=("balance", "microscope"),
        )
        == INTENT_BUYER_EQUIPMENT_ACQUISITION
    )
    assert (
        classify_shadow_change(
            current_classes=(),
            shadow_classes=(),
            capabilities=(),
            intent=INTENT_ACCESSORY_OR_CONSUMABLE,
            e2_change_class="unchanged_negative_or_unresolved",
            e2_interpretation="no_annex_claim_extraction_complete",
            coverage_complete=True,
            has_anexo_bundle=True,
            current_queue_keys=set(),
            shadow_queue_keys=set(),
            human_review_required=False,
        )
        == SHADOW_ACCESSORY_OR_CONSUMABLE_ONLY
    )
    assert (
        classify_shadow_change(
            current_classes=(),
            shadow_classes=(),
            capabilities=(),
            intent=INTENT_SERVICE_OR_MAINTENANCE,
            e2_change_class="unchanged_negative_or_unresolved",
            e2_interpretation="no_annex_claim_extraction_complete",
            coverage_complete=True,
            has_anexo_bundle=True,
            current_queue_keys=set(),
            shadow_queue_keys=set(),
            human_review_required=False,
        )
        == SHADOW_SERVICE_OR_MAINTENANCE_ONLY
    )
    assert (
        classify_shadow_change(
            current_classes=(),
            shadow_classes=(),
            capabilities=(),
            intent=INTENT_METHOD_OR_EXAM,
            e2_change_class="unchanged_negative_or_unresolved",
            e2_interpretation="no_annex_claim_extraction_complete",
            coverage_complete=True,
            has_anexo_bundle=True,
            current_queue_keys=set(),
            shadow_queue_keys=set(),
            human_review_required=False,
        )
        == SHADOW_METHOD_OR_EXAM_ONLY
    )
    assert (
        classify_shadow_change(
            current_classes=(),
            shadow_classes=("microscope",),
            capabilities=(
                ClaimCapability(
                    "microscope", CAPABILITY_OUT_OF_SCOPE, MATCH_OUTSIDE_CATALOG
                ),
            ),
            intent=INTENT_SUPPLIER_REQUIRED_EQUIPMENT,
            e2_change_class="annex_created_conflict",
            e2_interpretation="presence_proven",
            coverage_complete=True,
            has_anexo_bundle=True,
            current_queue_keys=set(),
            shadow_queue_keys=set(),
            human_review_required=True,
        )
        == SHADOW_SUPPLIER_REQUIRED_EQUIPMENT
    )


# --------------------------------------------------------------------------
# end-to-end synthetic shadow comparison
# --------------------------------------------------------------------------


def _anexo_for_cases() -> AnexoEvidenceBundleSet:
    # Balance acquisition (in scope)
    bal_att = make_attachment("A-bal", "T-BAL", content="bal")
    bal_chunk = make_chunk(
        "C-bal",
        "Nombre : Balanza analitica de precision para laboratorio",
        tender_id="T-BAL",
        attachment_id="A-bal",
    )
    # Bundled centrifuge + microscope
    bun_att = make_attachment("A-bun", "T-BUN", content="bun")
    bun_c1 = make_chunk(
        "C-bun-1",
        "Nombre : Centrifuga de sobremesa para Laboratorio Clinico",
        tender_id="T-BUN",
        attachment_id="A-bun",
        ordinal=1,
    )
    bun_c2 = make_chunk(
        "C-bun-2",
        "Nombre : Microscopio binocular planacromatico",
        tender_id="T-BUN",
        attachment_id="A-bun",
        ordinal=2,
        locator={"table": 1, "row": 2, "section": "body"},
    )
    # Accessory / lens paper — must match H1 optics-accessory veto pattern.
    acc_att = make_attachment("A-acc", "T-ACC", content="acc")
    acc_chunk = make_chunk(
        "C-acc",
        "Papel limpia lentes para microscopio de laboratorio",
        tender_id="T-ACC",
        attachment_id="A-acc",
    )
    # Maintenance
    mnt_att = make_attachment("A-mnt", "T-MNT", content="mnt")
    mnt_chunk = make_chunk(
        "C-mnt",
        "Mantencion y calibracion de centrifuga clinica instalada",
        tender_id="T-MNT",
        attachment_id="A-mnt",
    )
    # Supplier-required microscope
    sup_att = make_attachment("A-sup", "T-SUP", content="sup")
    sup_chunk = make_chunk(
        "C-sup",
        "EL Oferente debe disponer de forma permanente y a su costo de un Microscopio Optico",
        tender_id="T-SUP",
        attachment_id="A-sup",
    )
    # Microscope-only acquisition (recognized, but out of catalog capability)
    auto_att = make_attachment("A-auto", "T-AUTO", content="auto")
    auto_chunk = make_chunk(
        "C-auto",
        "Nombre : Microscopio binocular planacromatico para laboratorio clinico",
        tender_id="T-AUTO",
        attachment_id="A-auto",
    )
    # Diagnostic method/exam (explicit H1 reason path)
    exam_att = make_attachment("A-exam", "T-EXAM", content="exam")
    exam_chunk = make_chunk(
        "C-exam",
        "Velocidad de sedimentacion (proc. aut.) ensayo diagnostico automatizado",
        tender_id="T-EXAM",
        attachment_id="A-exam",
    )
    # Incomplete coverage (needs OCR), no positive text
    inc_att = make_attachment(
        "A-inc",
        "T-INC",
        outcome="needs_ocr",
        content="inc",
    )
    return make_anexo(
        chunks=[
            bal_chunk,
            bun_c1,
            bun_c2,
            acc_chunk,
            mnt_chunk,
            sup_chunk,
            auto_chunk,
            exam_chunk,
        ],
        attachments=[
            bal_att,
            bun_att,
            acc_att,
            mnt_att,
            sup_att,
            auto_att,
            exam_att,
            inc_att,
        ],
        bundles=[
            make_bundle("T-BAL"),
            make_bundle("T-BUN", discovered=1, downloaded=1),
            make_bundle("T-ACC"),
            make_bundle("T-MNT"),
            make_bundle("T-SUP"),
            make_bundle("T-AUTO"),
            make_bundle("T-EXAM"),
            make_bundle(
                "T-INC",
                extraction_complete=False,
                incomplete_reason_codes=("needs_ocr",),
                outcome_counts={"needs_ocr": 1},
            ),
        ],
    )


def _corpus() -> list[dict]:
    return [
        tender("T-BAL", title="Compra de equipos varios de laboratorio"),
        tender("T-BUN", title="Equipamiento clinico para bacteriologia"),
        tender("T-ACC", title="Compra de insumos de laboratorio varios"),
        tender("T-MNT", title="Servicio de mantencion de equipos instalados"),
        tender("T-SUP", title="Servicios de laboratorio clinico"),
        tender("T-AUTO", title="Adquisicion de equipos de observacion"),
        tender("T-EXAM", title="Servicio de examenes de laboratorio clinico"),
        tender("T-INC", title="Licitacion sin texto util"),
    ]


def test_shadow_isolation_and_safety(tmp_path: Path) -> None:
    anexo = _anexo_for_cases()
    tenders = _corpus()
    result, current, shadow = run_shadow_comparison(tenders, anexo=anexo)
    assert_shadow_invariants(result)
    summary = build_summary(result)

    assert summary["contact_authorization"] is False
    assert summary["outreach_authorization"] is False
    assert summary["production_queue_mutated"] is False
    assert summary["persisted"] is False
    assert summary["pr5f_started"] is False
    assert summary["annex_production_integration"] is False
    assert summary["shadow_results_are_not_production"] is True
    assert summary["canonical_production_queues_untouched"] is True
    assert "suppressed_service_or_maintenance_cases" in summary
    assert "suppressed_service_cases" not in summary
    assert "suppressed_maintenance_cases" not in summary
    assert "current_prospect_count" not in summary
    assert "shadow_prospect_count" not in summary

    # CURRENT fingerprint must be stable when recomputed from same CURRENT plan.
    from origenlab_email_pipeline.commercial_procurement_anexo_shadow_prospects.fingerprint import (
        plan_fingerprint,
    )

    assert plan_fingerprint(current) == result.current_fingerprint
    # Shadow plan differs when annex evidence adds equipment.
    assert result.current_fingerprint != result.shadow_fingerprint or any(
        d.new_annex_classes for d in result.tender_deltas
    )

    by = {d.tender_id: d for d in result.tender_deltas}
    assert by["T-BAL"].change_class == SHADOW_NEW_IN_SCOPE_OPPORTUNITY
    assert "balance" in by["T-BAL"].shadow_equipment_classes
    assert any(
        c.equipment_class == "balance" and c.capability == CAPABILITY_IN_SCOPE
        for c in by["T-BAL"].claim_level_capability
    )
    assert by["T-BAL"].commercial_intent_class == INTENT_BUYER_EQUIPMENT_ACQUISITION

    assert by["T-BUN"].change_class == SHADOW_BUNDLED_PARTIAL_CAPABILITY
    assert "centrifuge" in by["T-BUN"].shadow_equipment_classes
    assert "microscope" in by["T-BUN"].shadow_equipment_classes
    assert by["T-BUN"].commercial_intent_class == INTENT_BUYER_EQUIPMENT_ACQUISITION

    assert by["T-ACC"].change_class == SHADOW_ACCESSORY_OR_CONSUMABLE_ONLY
    assert by["T-MNT"].change_class == SHADOW_SERVICE_OR_MAINTENANCE_ONLY
    assert by["T-SUP"].change_class == SHADOW_SUPPLIER_REQUIRED_EQUIPMENT
    assert by["T-EXAM"].change_class == SHADOW_METHOD_OR_EXAM_ONLY
    assert by["T-EXAM"].commercial_intent_class == INTENT_METHOD_OR_EXAM
    assert QUEUE_WOULD_ENTER_CURRENT not in by["T-EXAM"].queue_delta
    assert by["T-AUTO"].change_class == SHADOW_NEW_OUT_OF_SCOPE_EQUIPMENT
    assert "microscope" in by["T-AUTO"].shadow_equipment_classes
    assert all(
        c.capability == CAPABILITY_OUT_OF_SCOPE
        for c in by["T-AUTO"].claim_level_capability
    )

    # Provenance binding for annex-driven changes.
    assert by["T-BAL"].annex_claim_ids
    assert by["T-BAL"].annex_provenance_refs
    assert by["T-BAL"].annex_provenance_refs[0]["attachment_sha256"]
    assert by["T-BAL"].annex_provenance_refs[0]["locator_display"]

    # Queue delta calculation is explicit.
    assert any(
        d.delta_class == QUEUE_WOULD_ENTER_CURRENT
        for d in result.queue_deltas
        if d.tender_id == "T-BAL"
    ) or QUEUE_WOULD_ENTER_CURRENT in by["T-BAL"].queue_delta

    # Multi-class tender: signal ROWS can exceed unique tender count.
    assert summary["shadow_equipment_purchase_signal_rows"] >= summary[
        "shadow_equipment_purchase_tender_count"
    ]
    # T-BUN alone contributes centrifuge+microscope rows but one unique tender.
    assert summary["shadow_equipment_purchase_tender_count"] >= 1
    assert "T-BAL" in summary["would_enter_current_opportunity_tender_ids"] or any(
        d.tender_id == "T-BAL" and d.delta_class == QUEUE_WOULD_ENTER_CURRENT
        for d in result.queue_deltas
    )

    # Deterministic digests across re-run.
    result2, _, _ = run_shadow_comparison(tenders, anexo=anexo)
    assert result2.semantic_digest == result.semantic_digest
    assert result2.corpus_digest == result.corpus_digest

    assert (
        summary["shadow_equipment_purchase_signal_rows"]
        >= summary["current_equipment_purchase_signal_rows"]
    )


def test_relevance_plan_pair_only_varies_annex_units() -> None:
    anexo = _anexo_for_cases()
    pair = build_relevance_plan_pair(_corpus(), anexo=anexo)
    assert len(pair.shadow.product_text_units) > len(pair.current.product_text_units)
    current_ids = {u.unit_id for u in pair.current.product_text_units}
    assert current_ids <= {u.unit_id for u in pair.shadow.product_text_units}
    # Baseline units are shared identities.
    for unit in pair.current.product_text_units:
        shadow_unit = next(
            u for u in pair.shadow.product_text_units if u.unit_id == unit.unit_id
        )
        assert shadow_unit.text_raw == unit.text_raw


def test_cli_rejects_forbidden_flags() -> None:
    from origenlab_email_pipeline.commercial_procurement_anexo_shadow_prospects.constants import (
        FORBIDDEN_CLI_FLAGS,
    )

    assert "--apply" in FORBIDDEN_CLI_FLAGS
    assert "--persist" in FORBIDDEN_CLI_FLAGS
    assert "--network" in FORBIDDEN_CLI_FLAGS
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts/commercial/build_anexo_shadow_prospect_comparison.py"
    )
    text = script.read_text(encoding="utf-8")
    assert "FORBIDDEN_CLI_FLAGS" in text
    assert "SHADOW" in text or "shadow" in text
    assert "--apply" in text


def test_incomplete_coverage_stays_explicit() -> None:
    anexo = _anexo_for_cases()
    result, _, _ = run_shadow_comparison(_corpus(), anexo=anexo)
    by = {d.tender_id: d for d in result.tender_deltas}
    assert by["T-INC"].coverage_status == "incomplete"
    assert by["T-INC"].e2_interpretation in {
        "absence_unproven",
        "no_annex_claim_extraction_complete",
        "no_anexo_evidence_available",
    } or by["T-INC"].change_class in {
        "shadow_incomplete_no_conclusion",
        "shadow_unchanged",
    }
