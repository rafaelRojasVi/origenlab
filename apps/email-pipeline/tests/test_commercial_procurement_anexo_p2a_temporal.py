"""Temporal contracts for guarded P2A annex evidence integration.

The historical acquisition observations in this module are deliberately
constructed test fixtures.  They preserve the reviewed tender facts in
``historical_replay_cases.json`` but do not claim that a contemporaneous
archived acquisition snapshot existed.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
)
from origenlab_email_pipeline.commercial_procurement_anexo_integration.augment import (
    AnexoAugmentationResult,
    augment_product_relevance_plan,
)
from origenlab_email_pipeline.commercial_procurement_anexo_integration.models import (
    VerifiedAnexoEvidence,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.constants import (
    OUTCOME_EXTRACTION_SUCCESS,
    OUTCOME_NEEDS_OCR,
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
from origenlab_email_pipeline.commercial_procurement_candidate_planner.constants import (
    IDENTITY_NS_MERCADO_PUBLICO,
    TENDER_KEY_KIND_MERCADO_PUBLICO,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    CandidatePlanResult,
    CoalescedProcurementTender,
    Pr4PlaneBundle,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.models import (
    ContactResolutionPlanResult,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.planner import (
    InstitutionProspectPlanResult,
    build_institution_prospects_from_plans,
    classify_acquisition_snapshots,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.queues import (
    current_opportunity_blockers,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
    ProductRelevancePlanResult,
    TenderRelevanceDecision,
)


FIXTURE_DIR = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "commercial_procurement_anexo_integration_p2a"
)
CURRENT_AS_OF_UTC = "2026-08-12T12:00:00Z"

# Reviewed annex rows, retained at their real document grain.  They are fed
# through P2A's production PR5D augmentation; no P1 timestamp-omitting adapter
# or copied classifier/aggregator logic is used here.
_ANNEX_ROWS: dict[str, dict[str, Any]] = {
    "1057510-40-LP26": {
        "text": (
            "[r9] Crítico | Descripción | Centrifuga de sobremesa para uso "
            "en Laboratorio Clínico y/o Citología"
        ),
        "source_format": "xlsx",
        "locator_type": "xlsx_rows",
        "locator": {"sheet": "CENTRÍFUGA", "start_row": 1, "end_row": 38},
        "safe_filename": "ANEXO Nº 4 EETT ORINA 2026 (1).xlsx",
        "coverage_complete": False,
    },
    "1057510-53-LP26": {
        "text": (
            "[r9] Crítico | Descripción | Centrifuga de sobremesa para uso "
            "en Laboratorio Clínico y/o Citología"
        ),
        "source_format": "xlsx",
        "locator_type": "xlsx_rows",
        "locator": {"sheet": "CENTRÍFUGA", "start_row": 1, "end_row": 38},
        "safe_filename": "ANEXO Nº 4 EETT ORINA 2026 (1).xlsx",
        "coverage_complete": False,
    },
    "4034-16-LE26": {
        "text": "[r23] 20) 3 Balanza Digital de laboratorio 3 Kg / 0,1 Gr",
        "source_format": "docx",
        "locator_type": "docx_table",
        "locator": {"section": "body", "table": 2},
        "safe_filename": "ANEXO N°3.docx",
        "coverage_complete": True,
    },
}


def _replay_fixture() -> dict[str, Any]:
    return json.loads(
        (FIXTURE_DIR / "historical_replay_cases.json").read_text(encoding="utf-8")
    )


def _origin_fixture() -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / "FIXTURE_ORIGIN.json").read_text(encoding="utf-8"))


def _parse_utc(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _snapshot_id(scenario_id: str, tender_id: str) -> str:
    return f"p2a-replay:{scenario_id}:{tender_id.casefold()}"


def _write_constructed_snapshot(
    path: Path,
    *,
    snapshot_id: str,
    acquired_at_utc: str | None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    page: dict[str, Any] = {}
    if acquired_at_utc is not None:
        page["acquired_at_utc"] = acquired_at_utc
    payload = {
        "snapshot_id": snapshot_id,
        "fixture_origin": (
            "deterministic_historical_replay_fixture_not_archived_snapshot"
        ),
        "archived_snapshot_existed": False,
        "pages": [page],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _empty_pr4(as_of_utc: str) -> Pr4PlaneBundle:
    return Pr4PlaneBundle(
        signals=(),
        account_resolutions=(),
        evidence_rows=(),
        conflict_rows=(),
        enrichment_rows=(),
        build_meta={"fixture_origin": "deterministic_historical_replay"},
        source_fingerprint="a" * 64,
        build_plan_fingerprint="b" * 64,
        semantic_plan_digest="c" * 64,
        readback_semantic_digest="c" * 64,
        schema_version="fixture-v1",
        build_contract="fixture-v1",
        identity_fingerprint="d" * 64,
        as_of_date=as_of_utc[:10],
        schema_status="ok",
        identity_diagnostic={},
    )


def _tender(case: dict[str, Any], *, snapshot_id: str) -> CoalescedProcurementTender:
    tender_id = str(case["tender_id"])
    return CoalescedProcurementTender(
        coalesced_tender_id=tender_id,
        canonical_tender_key=tender_id,
        identity_namespace=IDENTITY_NS_MERCADO_PUBLICO,
        tender_key_kind=TENDER_KEY_KIND_MERCADO_PUBLICO,
        candidate_source_kind="deterministic_historical_replay_fixture",
        pr4_procurement_id=None,
        pr4_procurement_ids=(),
        acquisition_snapshot_ids=(snapshot_id,),
        acquisition_instance_ids=(),
        acquisition_observation_ids=(),
        coalescence_status="coalesced",
        source_precedence_reason="constructed_fixture_preserving_real_tender_facts",
        currentness_class="historical_replay_observation",
        lifecycle_class="active_open",
        closing_soon_bucket="fixture_recomputed_by_production_lifecycle",
        publication_timestamp_selected=str(case["publication_timestamp_raw"]),
        close_timestamp_selected=str(case["close_timestamp_raw"]),
        status_code_selected=str(case["status_code"]),
        status_name_selected=str(case["status_name"]),
        status_value_selected=None,
        source_status_system_selected="chilecompra",
        buyer_display_selected=str(case["buyer_display"]),
        buyer_source_id_selected=str(case["buyer_source_id"]),
        title_selected=str(case["title"]),
        selected_field_provenance={},
        buyer_display_variance=False,
        lifecycle_status_evidence_ref_id=None,
        lifecycle_close_evidence_ref_id=None,
        lifecycle_publication_evidence_ref_id=None,
        lifecycle_evidence_currentness_class=None,
        lifecycle_reason_codes=("constructed_historical_replay_fixture",),
        evidence_ref_ids=(),
        conflict_ids=(),
        # PR3: this fixture predates procurement-method evidence; default to a
        # recognized open/public Ticket Tipo code (this test is about temporal
        # replay, not procurement-method eligibility) so historical-replay
        # tenders keep their original actionability intent.
        procurement_method_selected="LP",
        procurement_method_details_selected=None,
    )


def _baseline_decision(tender_id: str) -> TenderRelevanceDecision:
    """A canonical PR5D-shaped negative that annex evidence may augment."""
    return TenderRelevanceDecision(
        decision_id=f"baseline:{tender_id}",
        coalesced_tender_id=tender_id,
        relevance_class="unrelated",
        canonical_equipment_classes=(),
        product_resolution_status="no_equipment_match",
        evidence_tier="tender_title",
        confidence_band="high",
        positive_reason_codes=(),
        negative_reason_codes=(),
        ambiguity_reason_codes=(),
        aggregation_reason_codes=("no_linked_product_text",),
        matched_spans=(),
        contributing_evidence_ref_ids=(),
        unit_decision_ids=(),
        taxonomy_version="production_taxonomy_owned_by_augmentation",
        rules_version="production_rules_owned_by_augmentation",
        input_fingerprint="1" * 64,
        semantic_fingerprint="2" * 64,
    )


def _plans(
    cases: list[dict[str, Any]],
    *,
    as_of_utc: str,
    scenario_id: str,
) -> tuple[
    CandidatePlanResult,
    ProductRelevancePlanResult,
    ContactResolutionPlanResult,
]:
    tenders = tuple(
        _tender(
            case,
            snapshot_id=_snapshot_id(scenario_id, str(case["tender_id"])),
        )
        for case in cases
    )
    source_digest = canonical_json_digest(
        {
            "fixture_origin": "deterministic_historical_replay",
            "as_of_utc": as_of_utc,
            "tenders": [tender.to_dict() for tender in tenders],
        }
    )
    pr5c = CandidatePlanResult(
        evidence_refs=(),
        coalesced_tenders=tenders,
        unresolved=(),
        conflicts=(),
        pr4=_empty_pr4(as_of_utc),
        acquisition_snapshot_ids=tuple(
            snapshot_id
            for tender in tenders
            for snapshot_id in tender.acquisition_snapshot_ids
        ),
        acquisition_instance_ids=(),
        as_of_utc=as_of_utc,
        freshness_threshold_hours=48,
        input_source_fingerprint=source_digest,
        build_plan_fingerprint=canonical_json_digest(
            {"source": source_digest, "as_of_utc": as_of_utc}
        ),
        semantic_digest=source_digest,
        aggregate_reconciliation={"ok": True},
        field_precedence_matrix={},
        lifecycle_policy={},
        run_context="deterministic_historical_replay_fixture",
        planner_version="fixture-through-production-contracts",
        case_construction_meta={
            "archived_snapshot_existed": False,
            "fixture_origin": "deterministic_historical_replay",
        },
    )
    baseline_digest = canonical_json_digest(
        {
            "pr5c": pr5c.semantic_digest,
            "mode": "baseline_without_annex_units",
        }
    )
    baseline = ProductRelevancePlanResult(
        product_text_units=(),
        unresolved_units=(),
        unit_decisions=(),
        tender_decisions=tuple(
            _baseline_decision(tender.coalesced_tender_id) for tender in tenders
        ),
        field_sufficiency={},
        reconciliation={"ok": True},
        taxonomy_document={},
        fingerprints={"semantic_digest": baseline_digest},
        counts={},
        run_context="deterministic_historical_replay_fixture",
        planner_version="fixture-through-production-contracts",
        as_of_utc=as_of_utc,
        pr5c_semantic_digest=pr5c.semantic_digest,
    )
    pr5e = ContactResolutionPlanResult(
        as_of_utc=as_of_utc,
        run_context="deterministic_historical_replay_fixture",
        planner_version="fixture-through-production-contracts",
        organization_resolutions=(),
        contact_summaries=(),
        contact_candidates=(),
        evidence=(),
        conflicts=(),
        reconciliation={"ok": True},
        fingerprints={"semantic_digest": canonical_json_digest([])},
        dependency_fingerprints={"pr5d_semantic_digest": baseline_digest},
        counts={},
        field_sufficiency_audit={},
    )
    return pr5c, baseline, pr5e


def _verified_annex(
    cases: list[dict[str, Any]],
) -> VerifiedAnexoEvidence:
    bundles_by_tender: dict[str, TenderAttachmentBundle] = {}
    tender_digests: dict[str, str] = {}

    for case in cases:
        tender_id = str(case["tender_id"])
        row = _ANNEX_ROWS[tender_id]

        attachment_id = hashlib.sha256(
            f"p2a-fixture-attachment:{tender_id}".encode()
        ).hexdigest()[:24]

        chunk_id = hashlib.sha256(
            f"p2a-fixture-chunk:{tender_id}:{row['text']}".encode()
        ).hexdigest()[:24]

        text_value = str(row["text"])
        source_format = str(row["source_format"])

        chunk = EvidenceChunk(
            chunk_id=chunk_id,
            attachment_id=attachment_id,
            tender_id=tender_id,
            source_format=source_format,
            locator_type=str(row["locator_type"]),
            locator=dict(row["locator"]),
            text=text_value,
            text_sha256=hashlib.sha256(
                text_value.encode("utf-8")
            ).hexdigest(),
            char_count=len(text_value),
            ordinal=1,
        )

        attachment_sha = hashlib.sha256(
            text_value.encode("utf-8")
        ).hexdigest()

        primary = AttachmentRecord(
            attachment_id=attachment_id,
            tender_id=tender_id,
            listing_ordinal=1,
            row_ordinal=1,
            portal_filename=str(row["safe_filename"]),
            safe_filename=str(row["safe_filename"]),
            tipo="fixture",
            descripcion="reviewed local annex row",
            fecha_adjunto="2026-08-12",
            content_type="application/octet-stream",
            detected_format=source_format,
            extension=(
                "." + source_format
                if source_format
                else ""
            ),
            byte_count=len(text_value.encode("utf-8")),
            sha256=attachment_sha,
            download_status="downloaded",
            outcome=OUTCOME_EXTRACTION_SUCCESS,
            role_tag=ROLE_TECHNICAL_SPEC,
            evidence_attachment_id=attachment_id,
            extraction=AttachmentExtraction(
                outcome=OUTCOME_EXTRACTION_SUCCESS,
                detected_format=source_format,
                chunk_count=1,
                char_count=len(text_value),
                extractor="p2a_temporal_fixture",
            ),
        )

        complete = bool(row["coverage_complete"])
        attachments: tuple[AttachmentRecord, ...]

        if complete:
            attachments = (primary,)
            incomplete_reason_codes: tuple[str, ...] = ()
            outcome_counts = {
                OUTCOME_EXTRACTION_SUCCESS: 1,
            }
        else:
            unread_id = hashlib.sha256(
                f"p2a-fixture-unread:{tender_id}".encode()
            ).hexdigest()[:24]

            unread = AttachmentRecord(
                attachment_id=unread_id,
                tender_id=tender_id,
                listing_ordinal=1,
                row_ordinal=2,
                portal_filename=f"scan-{tender_id}.pdf",
                safe_filename=f"scan-{tender_id}.pdf",
                tipo="fixture",
                descripcion="synthetic unread annex",
                fecha_adjunto="2026-08-12",
                content_type="application/pdf",
                detected_format="pdf",
                extension=".pdf",
                byte_count=1,
                sha256=hashlib.sha256(
                    f"unread:{tender_id}".encode()
                ).hexdigest(),
                download_status="downloaded",
                outcome=OUTCOME_NEEDS_OCR,
                role_tag=ROLE_TECHNICAL_SPEC,
                evidence_attachment_id=unread_id,
                extraction=AttachmentExtraction(
                    outcome=OUTCOME_NEEDS_OCR,
                    detected_format="pdf",
                    chunk_count=0,
                    char_count=0,
                    extractor="p2a_temporal_fixture",
                ),
            )

            attachments = (primary, unread)
            incomplete_reason_codes = (
                OUTCOME_NEEDS_OCR,
            )
            outcome_counts = {
                OUTCOME_EXTRACTION_SUCCESS: 1,
                OUTCOME_NEEDS_OCR: 1,
            }

        digest = bundle_semantic_digest(
            tender_id=tender_id,
            attachments=attachments,
            chunks=(chunk,),
            incomplete_reason_codes=incomplete_reason_codes,
        )

        bundle = TenderAttachmentBundle(
            tender_id=tender_id,
            source_kind="p2a_temporal_fixture",
            listing_page_count=1,
            attachments_discovered=len(attachments),
            attachments_downloaded=len(attachments),
            attachments=attachments,
            chunks=(chunk,),
            bytes_downloaded=sum(
                attachment.byte_count
                for attachment in attachments
            ),
            incomplete_reason_codes=incomplete_reason_codes,
            semantic_digest=digest,
            outcome_counts=outcome_counts,
        )

        bundles_by_tender[tender_id] = bundle
        tender_digests[tender_id] = digest

    return VerifiedAnexoEvidence(
        bundles=bundles_by_tender,
        semantic_digest=canonical_json_digest(
            tender_digests
        ),
        tender_semantic_digests=tender_digests,
        summary={
            "fixture_origin": "reviewed_local_annex_rows",
        },
        attachment_manifest={},
        extraction_manifest={},
    )


def _build_enabled(
    cases: list[dict[str, Any]],
    *,
    as_of_utc: str,
    scenario_id: str,
    temporal_report: dict[str, Any],
) -> tuple[
    CandidatePlanResult,
    ProductRelevancePlanResult,
    ContactResolutionPlanResult,
    AnexoAugmentationResult,
    InstitutionProspectPlanResult,
]:
    pr5c, baseline, pr5e = _plans(
        cases,
        as_of_utc=as_of_utc,
        scenario_id=scenario_id,
    )
    augmentation = augment_product_relevance_plan(
        pr5c=pr5c,
        baseline=baseline,
        verified=_verified_annex(cases),
    )
    # This fixture has no contact candidates to recalculate, but it must still
    # model the production PR5E rebuild boundary faithfully: enabled PR5E is a
    # distinct result bound to the augmented PR5D semantic digest.
    augmented_pr5d_digest = augmentation.plan.fingerprints["semantic_digest"]
    enabled_pr5e = replace(
        pr5e,
        fingerprints={
            "semantic_digest": canonical_json_digest(
                {
                    "fixture": "rebuilt_empty_pr5e",
                    "pr5d_semantic_digest": augmented_pr5d_digest,
                }
            )
        },
        dependency_fingerprints={
            **pr5e.dependency_fingerprints,
            "pr5d_semantic_digest": augmented_pr5d_digest,
        },
    )
    assert enabled_pr5e.dependency_fingerprints["pr5d_semantic_digest"] == (
        augmented_pr5d_digest
    )
    enabled = build_institution_prospects_from_plans(
        pr5c=pr5c,
        pr5d=augmentation.plan,
        pr5e=enabled_pr5e,
        as_of_utc=as_of_utc,
        run_context="deterministic_historical_replay_fixture",
        snapshot_temporal_report=temporal_report,
    )
    return pr5c, baseline, enabled_pr5e, augmentation, enabled


def _valid_replay(
    scenario: dict[str, Any],
    *,
    root: Path,
) -> tuple[AnexoAugmentationResult, InstitutionProspectPlanResult, dict[str, Any]]:
    paths: list[Path] = []
    for case in scenario["tenders"]:
        tender_id = str(case["tender_id"])
        path = root / f"{tender_id}.json"
        _write_constructed_snapshot(
            path,
            snapshot_id=_snapshot_id(str(scenario["scenario_id"]), tender_id),
            acquired_at_utc=str(scenario["constructed_acquired_at_utc"]),
        )
        paths.append(path)
    temporal_report = classify_acquisition_snapshots(
        paths,
        as_of_utc=str(scenario["as_of_utc"]),
    )
    _pr5c, _baseline, _pr5e, augmentation, enabled = _build_enabled(
        list(scenario["tenders"]),
        as_of_utc=str(scenario["as_of_utc"]),
        scenario_id=str(scenario["scenario_id"]),
        temporal_report=temporal_report,
    )
    return augmentation, enabled, temporal_report


def _current_queue_ids(result: InstitutionProspectPlanResult) -> set[str]:
    return {
        str(row["tender_code"])
        for row in result.operator_queues["current_opportunity_queue"]
    }


def test_replay_fixture_is_explicitly_constructed_and_preserves_real_facts() -> None:
    origin = _origin_fixture()
    fixture = _replay_fixture()

    assert origin["test_fixture_only"] is True
    assert origin["snapshot_acquisition_observations_constructed"] is True
    assert origin["archived_snapshot_existed"] is False
    assert fixture["archived_snapshot_existed"] is False
    assert fixture["no_single_replay_is_required_to_contain_the_union"] is True

    cases = {
        str(case["tender_id"]): case
        for scenario in fixture["scenarios"]
        for case in scenario["tenders"]
    }
    assert cases["1057510-40-LP26"] == {
        "tender_id": "1057510-40-LP26",
        "status_code": "5",
        "status_name": "Publicada",
        "publication_timestamp_raw": "2026-05-26T16:53:56.35",
        "close_timestamp_raw": "2026-06-22T17:00:00",
        "title": (
            "CONVENIO SUMINISTRO REACTIVOS E INSUMOS PARA ANÁLISIS DE ORINA Y "
            "SEDIMENTO CON ENTREGA DE EQUIPO PARA LABORATORIO DEL HOSPITAL DE "
            "SAN CARLOS DR. BENICIO ARZOLA MEDINA"
        ),
        "buyer_display": (
            "SERVICIO DE SALUD NUBLE HOSPITAL DE SAN CARLOS DR BENICIO ARZOLA MEDIN"
        ),
        "buyer_source_id": "7469",
    }
    assert cases["1057510-53-LP26"]["publication_timestamp_raw"] == (
        "2026-07-08T16:31:51.227"
    )
    assert cases["1057510-53-LP26"]["close_timestamp_raw"] == ("2026-07-28T17:00:00")
    assert cases["4034-16-LE26"]["publication_timestamp_raw"] == (
        "2026-07-08T13:36:55.22"
    )
    assert cases["4034-16-LE26"]["close_timestamp_raw"] == ("2026-07-20T16:00:00")
    assert cases["4034-16-LE26"]["title"] == (
        "LABORATORIOS MOVILES DE CIENCIAS NATURALES"
    )
    assert cases["4034-16-LE26"]["buyer_display"] == "I MUNICIPALIDAD DE CANETE"
    assert cases["4034-16-LE26"]["buyer_source_id"] == "119488"

    for scenario in fixture["scenarios"]:
        assert _parse_utc(str(scenario["constructed_acquired_at_utc"])) <= _parse_utc(
            str(scenario["as_of_utc"])
        )


@pytest.mark.parametrize(
    "scenario_id",
    ["june_2026_open_window", "july_2026_open_window"],
)
def test_valid_historical_replay_surfaces_only_that_open_window(
    tmp_path: Path,
    scenario_id: str,
) -> None:
    fixture = _replay_fixture()
    scenario = next(
        scenario
        for scenario in fixture["scenarios"]
        if scenario["scenario_id"] == scenario_id
    )
    augmentation, enabled, temporal_report = _valid_replay(
        scenario,
        root=tmp_path / scenario_id,
    )

    expected = set(scenario["expected_current_opportunity_tender_ids"])
    assert set(augmentation.bound_tender_ids) == expected
    assert set(temporal_report["eligible_snapshot_ids"]) == {
        _snapshot_id(scenario_id, tender_id) for tender_id in expected
    }
    assert temporal_report["future_snapshot_ids"] == []
    assert temporal_report["missing_chronology_snapshot_ids"] == []
    assert temporal_report["invalid_chronology_snapshot_ids"] == []
    assert _current_queue_ids(enabled) == expected
    assert {
        str(row["tender_code"])
        for row in enabled.tender_rows
        if row["commercial_signal_type"] == "equipment_purchase_signal"
    } == expected


def test_historical_replay_union_is_exact_and_no_scenario_contains_union(
    tmp_path: Path,
) -> None:
    fixture = _replay_fixture()
    expected_union = set(fixture["expected_historical_replay_union_ids"])
    replay_results: dict[str, set[str]] = {}

    for scenario in fixture["scenarios"]:
        _augmentation, enabled, _temporal = _valid_replay(
            scenario,
            root=tmp_path / str(scenario["scenario_id"]),
        )
        replay_results[str(scenario["as_of_utc"])] = _current_queue_ids(enabled)

    assert (
        set().union(*replay_results.values())
        == expected_union
        == {
            "4034-16-LE26",
            "1057510-40-LP26",
            "1057510-53-LP26",
        }
    )
    assert all(result != expected_union for result in replay_results.values())
    assert replay_results["2026-06-20T12:00:00Z"] == {"1057510-40-LP26"}
    assert replay_results["2026-07-10T12:00:00Z"] == {
        "4034-16-LE26",
        "1057510-53-LP26",
    }
    assert {
        as_of: sorted(ids) for as_of, ids in sorted(replay_results.items())
    } == fixture["historical_replay_results_by_as_of"]
    assert sorted(set().union(*replay_results.values())) == fixture[
        "historical_replay_union_ids"
    ]


@pytest.mark.parametrize(
    ("chronology", "acquired_at_utc", "report_key", "result_key", "reason"),
    [
        (
            "future",
            "2026-06-21T12:00:00Z",
            "future_snapshot_ids",
            "future_observation_excluded",
            "acquisition_stamp_after_as_of",
        ),
        (
            "missing",
            None,
            "missing_chronology_snapshot_ids",
            "missing_chronology",
            "missing_acquisition_chronology",
        ),
        (
            "invalid",
            "not-a-timestamp",
            "invalid_chronology_snapshot_ids",
            "invalid_chronology",
            "invalid_acquisition_chronology",
        ),
    ],
)
def test_noneligible_acquisition_chronology_is_quarantined_before_recognition(
    tmp_path: Path,
    chronology: str,
    acquired_at_utc: str | None,
    report_key: str,
    result_key: str,
    reason: str,
) -> None:
    scenario = _replay_fixture()["scenarios"][0]
    case = dict(scenario["tenders"][0])
    scenario_id = f"{scenario['scenario_id']}-{chronology}"
    snapshot_id = _snapshot_id(scenario_id, str(case["tender_id"]))
    snapshot = tmp_path / f"{chronology}.json"
    _write_constructed_snapshot(
        snapshot,
        snapshot_id=snapshot_id,
        acquired_at_utc=acquired_at_utc,
    )
    report = classify_acquisition_snapshots(
        [snapshot],
        as_of_utc=str(scenario["as_of_utc"]),
    )
    assert report[report_key] == [snapshot_id]

    _pr5c, _baseline, _pr5e, augmentation, enabled = _build_enabled(
        [case],
        as_of_utc=str(scenario["as_of_utc"]),
        scenario_id=scenario_id,
        temporal_report=report,
    )

    # Annex PR5D recognition exists, but quarantined acquisition provenance may
    # not enter lifecycle, claims, profiles, history, or any current queue.
    decision = augmentation.plan.tender_decisions[0]
    assert decision.canonical_equipment_classes == ("centrifuge",)
    assert enabled.profiles == []
    assert enabled.lifecycle_projections == []
    assert enabled.line_claims == []
    assert enabled.operator_queues["current_opportunity_queue"] == []
    assert enabled.temporal_reconciliation[f"{result_key}_count"] == 1
    quarantined = enabled.temporal_reconciliation[result_key]
    assert quarantined[0]["tender_code"] == "1057510-40-LP26"
    assert quarantined[0]["reason"] == reason


def test_current_run_cannot_resurrect_expired_annex_backed_tenders(
    tmp_path: Path,
) -> None:
    fixture = _replay_fixture()
    all_cases: list[dict[str, Any]] = []
    snapshot_paths: list[Path] = []
    for scenario in fixture["scenarios"]:
        for case in scenario["tenders"]:
            all_cases.append(dict(case))
            tender_id = str(case["tender_id"])
            path = tmp_path / f"{tender_id}.json"
            _write_constructed_snapshot(
                path,
                snapshot_id=_snapshot_id("current_expired", tender_id),
                acquired_at_utc=str(scenario["constructed_acquired_at_utc"]),
            )
            snapshot_paths.append(path)

    temporal_report = classify_acquisition_snapshots(
        snapshot_paths,
        as_of_utc=CURRENT_AS_OF_UTC,
    )
    _pr5c, _baseline, _pr5e, augmentation, enabled = _build_enabled(
        all_cases,
        as_of_utc=CURRENT_AS_OF_UTC,
        scenario_id="current_expired",
        temporal_report=temporal_report,
    )

    expected = set(fixture["expected_historical_replay_union_ids"])
    assert set(augmentation.bound_tender_ids) == expected
    assert {claim.tender_id for claim in augmentation.claims} == expected
    assert set(enabled.temporal_reconciliation["temporally_eligible_tender_ids"]) == (
        expected
    )
    assert enabled.operator_queues["current_opportunity_queue"] == []

    line_evidence_counts: dict[str, int] = {}
    for row in enabled.line_rows:
        if row["line_disposition"] != "excluded":
            tender_id = str(row["coalesced_tender_id"])
            line_evidence_counts[tender_id] = line_evidence_counts.get(tender_id, 0) + 1

    equipment_rows = [
        row
        for row in enabled.tender_rows
        if row["commercial_signal_type"] == "equipment_purchase_signal"
    ]
    assert {str(row["tender_code"]) for row in equipment_rows} == expected
    for row in equipment_rows:
        blockers = current_opportunity_blockers(
            row,
            line_evidence_counts[str(row["coalesced_tender_id"])],
            as_of_utc=CURRENT_AS_OF_UTC,
        )
        assert "close_timestamp_elapsed" in blockers
        assert "lifecycle_not_active_open" in blockers


def test_disabled_path_is_exact_baseline_and_augmentation_does_not_mutate_it(
    tmp_path: Path,
) -> None:
    scenario = _replay_fixture()["scenarios"][0]
    case = dict(scenario["tenders"][0])
    path = tmp_path / "eligible.json"
    _write_constructed_snapshot(
        path,
        snapshot_id=_snapshot_id(str(scenario["scenario_id"]), str(case["tender_id"])),
        acquired_at_utc=str(scenario["constructed_acquired_at_utc"]),
    )
    report = classify_acquisition_snapshots(
        [path],
        as_of_utc=str(scenario["as_of_utc"]),
    )
    pr5c, baseline, pr5e = _plans(
        [case],
        as_of_utc=str(scenario["as_of_utc"]),
        scenario_id=str(scenario["scenario_id"]),
    )
    baseline_before = copy.deepcopy(baseline)

    disabled = build_institution_prospects_from_plans(
        pr5c=pr5c,
        pr5d=baseline,
        pr5e=pr5e,
        as_of_utc=str(scenario["as_of_utc"]),
        run_context="deterministic_historical_replay_fixture",
        snapshot_temporal_report=report,
    )
    disabled_repeat = build_institution_prospects_from_plans(
        pr5c=pr5c,
        pr5d=baseline,
        pr5e=pr5e,
        as_of_utc=str(scenario["as_of_utc"]),
        run_context="deterministic_historical_replay_fixture",
        snapshot_temporal_report=report,
    )
    augmentation = augment_product_relevance_plan(
        pr5c=pr5c,
        baseline=baseline,
        verified=_verified_annex([case]),
    )

    assert disabled == disabled_repeat
    assert disabled.operator_queues["current_opportunity_queue"] == []
    assert baseline == baseline_before
    assert augmentation.baseline_plan is baseline
    assert augmentation.baseline_plan == baseline_before
    assert augmentation.plan != baseline
