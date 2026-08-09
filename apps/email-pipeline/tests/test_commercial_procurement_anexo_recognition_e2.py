"""ANEXO-E2 offline test matrix.

Covers the comparison lane's semantics (presence vs absence, provenance,
coverage debt), the equipment-scope edges that annex reading amplifies, and the
read-only invariants that keep this lane out of production.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from origenlab_email_pipeline.commercial_procurement_anexo_recognition import (
    build_summary,
    run_comparison,
    segment_chunks,
    write_comparison_outputs,
)
from origenlab_email_pipeline.commercial_procurement_anexo_recognition.constants import (
    CHANGE_ANNEX_CREATED_CONFLICT,
    CHANGE_ANNEX_INCOMPLETE_NO_CONCLUSION,
    CHANGE_ANNEX_STRENGTHENED_EXISTING,
    CHANGE_NEW_ANNEX_POSITIVE,
    CHANGE_UNCHANGED_NEGATIVE_OR_UNRESOLVED,
    EFFECT_CREATED_NEW_CLAIM,
    EFFECT_STRENGTHENED_EXISTING,
    INTERPRETATION_ABSENCE_UNPROVEN,
    INTERPRETATION_NO_CLAIM_COVERAGE_COMPLETE,
    INTERPRETATION_PRESENCE_PROVEN,
)
from origenlab_email_pipeline.commercial_procurement_anexo_recognition.corpus import (
    AnexoEvidenceBundleSet,
)
from origenlab_email_pipeline.commercial_procurement_anexo_recognition.planner import (
    AnexoComparisonError,
    assert_read_only_invariants,
)

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))


# --------------------------------------------------------------------------
# builders
# --------------------------------------------------------------------------
def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_attachment(
    attachment_id: str,
    tender_id: str = "T-1",
    *,
    safe_filename: str = "anexo.docx",
    detected_format: str = "docx",
    role_tag: str = "technical_spec",
    outcome: str = "extraction_success",
    content: str = "content",
    duplicate_of: str | None = None,
    evidence_attachment_id: str | None = None,
) -> dict:
    return {
        "attachment_id": attachment_id,
        "tender_id": tender_id,
        "safe_filename": safe_filename,
        "sha256": _sha(content),
        "detected_format": detected_format,
        "role_tag": role_tag,
        "outcome": outcome,
        "duplicate_of_attachment_id": duplicate_of,
        "evidence_attachment_id": evidence_attachment_id or attachment_id,
    }


def make_chunk(
    chunk_id: str,
    text: str,
    *,
    tender_id: str = "T-1",
    attachment_id: str = "A-1",
    locator_type: str = "docx_paragraph",
    locator: dict | None = None,
    source_format: str = "docx",
    ordinal: int = 1,
    member_path: str | None = None,
    container_attachment_id: str | None = None,
) -> dict:
    return {
        "chunk_id": chunk_id,
        "tender_id": tender_id,
        "attachment_id": attachment_id,
        "locator_type": locator_type,
        "locator": locator if locator is not None else {"paragraph": 1, "section": "body"},
        "source_format": source_format,
        "text": text,
        "char_count": len(text),
        "ordinal": ordinal,
        "member_path": member_path,
        "container_attachment_id": container_attachment_id,
        "warnings": [],
    }


def make_bundle(
    tender_id: str = "T-1",
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
        "outcome_counts": outcome_counts or {"extraction_success": downloaded},
    }


def make_evidence(chunks, attachments, bundles) -> AnexoEvidenceBundleSet:
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


def make_tender(
    tender_id: str = "T-1",
    *,
    title: str = "Licitación de equipamiento",
    description: str = "Equipamiento según Anexo Técnico",
    lines: list[dict] | None = None,
) -> dict:
    return {
        "tender_id": tender_id,
        "title": title,
        "description": description,
        "buyer_organization": "HOSPITAL DE PRUEBA",
        "lifecycle_class": "active_open",
        "lines": lines if lines is not None else [],
    }


def only(comparisons):
    assert len(comparisons) == 1
    return comparisons[0]


# --------------------------------------------------------------------------
# 1-4: annex evidence creates claims the baseline could not
# --------------------------------------------------------------------------
def test_baseline_unresolved_annex_pdf_creates_centrifuge_claim():
    chunk = make_chunk(
        "C-1",
        "Centrífuga refrigerada de sobremesa 15.000 rpm con rotor incluido",
        locator_type="pdf_page",
        locator={"page": 17, "page_count": 35, "part": 1},
        source_format="pdf",
    )
    result = run_comparison(
        [make_tender()],
        anexo=make_evidence(
            [chunk], [make_attachment("A-1", safe_filename="anexo.pdf", detected_format="pdf")], [make_bundle()]
        ),
    )
    row = only(result.comparisons)
    assert row.baseline_categories == ()
    assert "centrifuge" in row.augmented_categories
    assert row.change_class == CHANGE_NEW_ANNEX_POSITIVE
    assert row.interpretation == INTERPRETATION_PRESENCE_PROVEN

    claim = only([c for c in result.claims if c.matched_category == "centrifuge"])
    assert claim.annex_effect == EFFECT_CREATED_NEW_CLAIM
    assert "page 17" in claim.locator_display


def test_baseline_unresolved_docx_table_creates_sonicator_claim():
    table = "\n".join(
        [
            "[r1] Ítem | Producto | Cantidad",
            "[r2] 1 | Procesador ultrasónico de sonda 20 kHz para laboratorio | 1",
            "[r3] 2 | Vasos de precipitado | 10",
        ]
    )
    chunk = make_chunk(
        "C-1", table, locator_type="docx_table", locator={"table": 3, "section": "body"}
    )
    result = run_comparison(
        [make_tender()],
        anexo=make_evidence([chunk], [make_attachment("A-1")], [make_bundle()]),
    )
    claim = only([c for c in result.claims if c.matched_category == "ultrasonic_processor"])
    assert claim.segment_locator == {"row": 2}
    assert "table 3 row 2" in claim.locator_display
    assert only(result.comparisons).change_class == CHANGE_NEW_ANNEX_POSITIVE


def test_xlsx_hidden_sheet_creates_equipment_claim():
    chunk = make_chunk(
        "C-1",
        "[r4]  | Balanza analítica de precisión 0,1 mg | 2",
        locator_type="xlsx_rows",
        locator={"sheet": "Oculta", "start_row": 1, "end_row": 25},
        source_format="xlsx",
    )
    result = run_comparison(
        [make_tender()],
        anexo=make_evidence(
            [chunk],
            [make_attachment("A-1", safe_filename="anexo.xlsx", detected_format="xlsx")],
            [make_bundle()],
        ),
    )
    claim = only([c for c in result.claims if c.matched_category == "balance"])
    assert "sheet 'Oculta'" in claim.locator_display
    assert claim.segment_locator == {"row": 4}


def test_evidence_far_beyond_early_sampling_positions_is_recognized():
    """Evidence at row 141 and page 30 must be found, not sampled away."""
    late_rows = "\n".join(
        [f"[r{n}]  | Insumo genérico {n} | 1" for n in range(1, 141)]
        + ["[r141]  | Centrífuga de sobremesa con rotor de ángulo fijo | 1"]
    )
    chunks = [
        make_chunk(
            "C-1",
            late_rows,
            locator_type="xlsx_rows",
            locator={"sheet": "Detalle", "start_row": 1, "end_row": 200},
            source_format="xlsx",
        ),
        make_chunk(
            "C-2",
            "Homogeneizador rotor-estator Ultra-Turrax para laboratorio",
            locator_type="pdf_page",
            locator={"page": 30, "page_count": 35, "part": 1},
            source_format="pdf",
            attachment_id="A-2",
            ordinal=2,
        ),
    ]
    result = run_comparison(
        [make_tender()],
        anexo=make_evidence(
            chunks,
            [make_attachment("A-1"), make_attachment("A-2")],
            [make_bundle(discovered=2, downloaded=2)],
        ),
    )
    categories = only(result.comparisons).augmented_categories
    assert "centrifuge" in categories
    assert "homogenizer" in categories
    assert any(c.segment_locator == {"row": 141} for c in result.claims)


# --------------------------------------------------------------------------
# 5-8: reconciliation and provenance
# --------------------------------------------------------------------------
def test_same_claim_in_several_attachments_reconciles_deterministically():
    text = "Centrífuga refrigerada de sobremesa 15.000 rpm"
    chunks = [
        make_chunk("C-1", text, attachment_id="A-1"),
        make_chunk("C-2", text, attachment_id="A-2", ordinal=2),
    ]
    evidence = make_evidence(
        chunks,
        [
            make_attachment("A-1", safe_filename="tecnico.docx"),
            make_attachment("A-2", safe_filename="bases.docx", role_tag="administrative_basis"),
        ],
        [make_bundle(discovered=2, downloaded=2)],
    )
    result = run_comparison([make_tender()], anexo=evidence)
    row = only(result.comparisons)
    assert row.augmented_categories == ("centrifuge",)
    centrifuge_claims = [c for c in result.claims if c.matched_category == "centrifuge"]
    assert len(centrifuge_claims) == 2, "both occurrences keep their own provenance"
    assert {c.attachment_id for c in centrifuge_claims} == {"A-1", "A-2"}


def test_same_filename_different_bytes_keeps_separate_provenance():
    chunks = [
        make_chunk("C-1", "Centrífuga refrigerada de sobremesa", attachment_id="A-1"),
        make_chunk("C-2", "Balanza analítica de precisión", attachment_id="A-2", ordinal=2),
    ]
    evidence = make_evidence(
        chunks,
        [
            make_attachment("A-1", safe_filename="anexo.docx", content="v1"),
            make_attachment("A-2", safe_filename="anexo.docx", content="v2"),
        ],
        [make_bundle(discovered=2, downloaded=2)],
    )
    result = run_comparison([make_tender()], anexo=evidence)
    digests = {c.attachment_sha256 for c in result.claims}
    assert len(digests) == 2, "same filename, different bytes must not merge"


def test_same_bytes_different_filenames_do_not_inflate_claims():
    """#450 reuses extraction for identical bytes, so chunks exist once."""
    chunk = make_chunk("C-1", "Centrífuga refrigerada de sobremesa", attachment_id="A-1")
    evidence = make_evidence(
        [chunk],
        [
            make_attachment("A-1", safe_filename="anexo_tecnico.docx", content="same"),
            make_attachment(
                "A-2",
                safe_filename="anexo_tecnico_v2.docx",
                content="same",
                duplicate_of="A-1",
                evidence_attachment_id="A-1",
            ),
        ],
        [make_bundle(discovered=2, downloaded=2)],
    )
    result = run_comparison([make_tender()], anexo=evidence)
    centrifuge = [c for c in result.claims if c.matched_category == "centrifuge"]
    assert len(centrifuge) == 1, "duplicate bytes must not inflate semantic claims"
    assert centrifuge[0].evidence_attachment_id == "A-1"
    # Both attachment rows are still visible in the corpus for provenance.
    assert len(evidence.attachments("T-1")) == 2


def test_zip_member_evidence_carries_full_member_provenance():
    chunk = make_chunk(
        "C-1",
        "Procesador ultrasónico de sonda 20 kHz para laboratorio",
        attachment_id="A-2",
        container_attachment_id="A-1",
        member_path="FichaTecnica.docx",
    )
    evidence = make_evidence(
        [chunk],
        [
            make_attachment("A-1", safe_filename="Anexo.zip", detected_format="zip"),
            make_attachment("A-2", safe_filename="FichaTecnica.docx"),
        ],
        [make_bundle(discovered=2, downloaded=2)],
    )
    result = run_comparison([make_tender()], anexo=evidence)
    claim = only([c for c in result.claims if c.matched_category == "ultrasonic_processor"])
    assert claim.member_path == "FichaTecnica.docx"
    assert claim.container_attachment_id == "A-1"
    assert "::FichaTecnica.docx" in claim.locator_display


# --------------------------------------------------------------------------
# 9-11: incomplete extraction may never prove absence
# --------------------------------------------------------------------------
def test_incomplete_bundle_without_match_is_absence_unproven():
    evidence = make_evidence(
        [make_chunk("C-1", "Bases administrativas generales del proceso")],
        [make_attachment("A-1")],
        [
            make_bundle(
                discovered=3,
                downloaded=2,
                extraction_complete=False,
                outcome_counts={"extraction_success": 2, "download_failed": 1},
                incomplete_reason_codes=("attachment_budget_exceeded",),
            )
        ],
    )
    row = only(run_comparison([make_tender()], anexo=evidence).comparisons)
    assert row.augmented_categories == ()
    assert row.change_class == CHANGE_ANNEX_INCOMPLETE_NO_CONCLUSION
    assert row.interpretation == INTERPRETATION_ABSENCE_UNPROVEN


def test_needs_ocr_without_match_is_absence_unproven():
    evidence = make_evidence(
        [],
        [make_attachment("A-1", detected_format="pdf", outcome="needs_ocr")],
        [
            make_bundle(
                extraction_complete=False,
                outcome_counts={"needs_ocr": 1},
                incomplete_reason_codes=("attachment_needs_ocr",),
            )
        ],
    )
    row = only(run_comparison([make_tender()], anexo=evidence).comparisons)
    assert row.interpretation == INTERPRETATION_ABSENCE_UNPROVEN
    assert row.coverage.debt_outcome_counts == {"needs_ocr": 1}
    assert row.coverage.unread_attachment_ids == ("A-1",)


def test_unsupported_legacy_document_is_not_negative_evidence():
    evidence = make_evidence(
        [],
        [make_attachment("A-1", safe_filename="anexo.doc", detected_format="doc", outcome="needs_converter")],
        [
            make_bundle(
                extraction_complete=False,
                outcome_counts={"needs_converter": 1},
                incomplete_reason_codes=("attachment_needs_converter",),
            )
        ],
    )
    row = only(run_comparison([make_tender()], anexo=evidence).comparisons)
    assert row.interpretation == INTERPRETATION_ABSENCE_UNPROVEN
    assert row.change_class != CHANGE_UNCHANGED_NEGATIVE_OR_UNRESOLVED


def test_complete_extraction_without_match_is_reported_separately():
    """A complete read that finds nothing is still not proof of absence."""
    evidence = make_evidence(
        [make_chunk("C-1", "Bases administrativas generales del proceso")],
        [make_attachment("A-1")],
        [make_bundle()],
    )
    row = only(run_comparison([make_tender()], anexo=evidence).comparisons)
    assert row.interpretation == INTERPRETATION_NO_CLAIM_COVERAGE_COMPLETE
    assert row.change_class == CHANGE_UNCHANGED_NEGATIVE_OR_UNRESOLVED


# --------------------------------------------------------------------------
# 12-13: interaction with baseline claims
# --------------------------------------------------------------------------
def test_matching_annex_strengthens_without_double_counting():
    tender = make_tender(
        lines=[{"ordinal": 1, "product": "Centrífuga de sobremesa", "description": "", "category": "", "classification": ""}]
    )
    evidence = make_evidence(
        [make_chunk("C-1", "Centrífuga refrigerada de sobremesa 15.000 rpm")],
        [make_attachment("A-1")],
        [make_bundle()],
    )
    result = run_comparison([tender], anexo=evidence)
    row = only(result.comparisons)
    assert row.baseline_categories == ("centrifuge",)
    assert row.augmented_categories == ("centrifuge",)
    assert row.change_class == CHANGE_ANNEX_STRENGTHENED_EXISTING
    claim = only(result.claims)
    assert claim.annex_effect == EFFECT_STRENGTHENED_EXISTING
    assert claim.existed_in_baseline is True


def test_api_centrifuge_plus_annex_balance_yields_multi_category_evidence():
    tender = make_tender(
        lines=[{"ordinal": 1, "product": "Centrífuga de sobremesa", "description": "", "category": "", "classification": ""}]
    )
    evidence = make_evidence(
        [make_chunk("C-1", "Balanza analítica de precisión 0,1 mg")],
        [make_attachment("A-1")],
        [make_bundle()],
    )
    result = run_comparison([tender], anexo=evidence)
    row = only(result.comparisons)
    assert "centrifuge" in row.augmented_categories
    assert "balance" in row.augmented_categories
    assert row.new_categories == ("balance",)


# --------------------------------------------------------------------------
# 14-16: equipment scope edges annex reading amplifies
# --------------------------------------------------------------------------
def test_accessory_only_annex_line_is_flagged_as_false_positive_risk():
    """H1: headed accessory/replacement is vetoed at PR5D recognition (no claim).

    Prior E2 advisory-only behavior (accessory_headed_line risk on a still-emitted
    ultrasonic claim) is superseded: sonotrodo de repuesto must not create an
    equipment claim. Complete processor purchases remain claimable elsewhere.
    """
    evidence = make_evidence(
        [make_chunk("C-1", "Sonotrodo de repuesto para procesador ultrasónico")],
        [make_attachment("A-1")],
        [make_bundle()],
    )
    result = run_comparison([make_tender()], anexo=evidence)
    assert result.claims == ()
    assert build_summary(result)["claims_with_accessory_risk"] == 0
    assert build_summary(result)["annex_claims"] == 0


def test_equipment_line_mentioning_an_accessory_is_not_flagged():
    evidence = make_evidence(
        [make_chunk("C-1", "Centrífuga de sobremesa con rotor de ángulo fijo + UPS")],
        [make_attachment("A-1")],
        [make_bundle()],
    )
    result = run_comparison([make_tender()], anexo=evidence)
    claim = only(result.claims)
    assert claim.matched_category == "centrifuge"
    assert claim.risk_reason_codes == ()


def test_equipment_scoped_by_what_it_holds_is_not_flagged():
    evidence = make_evidence(
        [make_chunk("C-1", "Centrífuga para tubos eppendorf de 1,5 a 2 ml")],
        [make_attachment("A-1")],
        [make_bundle()],
    )
    claim = only(run_comparison([make_tender()], anexo=evidence).claims)
    assert claim.matched_category == "centrifuge"
    assert claim.risk_reason_codes == ()


def test_centrifuge_tubes_do_not_become_centrifuge_equipment():
    evidence = make_evidence(
        [make_chunk("C-1", "Adquisición de tubos de centrífuga cónicos de 50 ml")],
        [make_attachment("A-1")],
        [make_bundle()],
    )
    result = run_comparison([make_tender()], anexo=evidence)
    assert result.claims == ()
    assert only(result.comparisons).augmented_categories == ()


def test_clinical_ultrasound_language_does_not_become_a_lab_sonicator():
    evidence = make_evidence(
        [
            make_chunk("C-1", "Servicio de ultrasonografía para el sistema de salud naval"),
            make_chunk("C-2", "Ecógrafo doppler color para diagnóstico clínico", ordinal=2),
        ],
        [make_attachment("A-1")],
        [make_bundle()],
    )
    result = run_comparison([make_tender()], anexo=evidence)
    assert not any(
        c.matched_category.startswith("ultrasonic") for c in result.claims
    ), "clinical imaging must never resolve to laboratory ultrasonic equipment"


# --------------------------------------------------------------------------
# 17-19: role tags, conflicts
# --------------------------------------------------------------------------
def test_administrative_document_table_is_still_searched():
    table = "\n".join(
        ["[r1] Ítem | Producto", "[r2] 1 | Centrífuga refrigerada de sobremesa 15.000 rpm"]
    )
    evidence = make_evidence(
        [make_chunk("C-1", table, locator_type="docx_table", locator={"table": 1, "section": "body"})],
        [make_attachment("A-1", safe_filename="bases_administrativas.docx", role_tag="administrative_basis")],
        [make_bundle()],
    )
    claim = only(run_comparison([make_tender()], anexo=evidence).claims)
    assert claim.document_role == "administrative_basis"
    assert claim.matched_category == "centrifuge"


@pytest.mark.parametrize(
    "role", ["technical_spec", "administrative_basis", "economic_form", "template", "unknown"]
)
def test_role_tag_does_not_gate_recognition(role):
    evidence = make_evidence(
        [make_chunk("C-1", "Centrífuga refrigerada de sobremesa 15.000 rpm")],
        [make_attachment("A-1", role_tag=role)],
        [make_bundle()],
    )
    result = run_comparison([make_tender()], anexo=evidence)
    assert only(result.claims).matched_category == "centrifuge"


def test_conflicting_annex_evidence_yields_an_explicit_conflict_state():
    tender = make_tender(
        lines=[
            {
                "ordinal": 1,
                "product": "Centrífuga de sobremesa",
                "description": "",
                "category": "",
                "classification": "",
            }
        ]
    )
    evidence = make_evidence(
        [make_chunk("C-1", "Baño ultrasónico de 10 litros para laboratorio")],
        [make_attachment("A-1")],
        [make_bundle()],
    )
    row = only(run_comparison([tender], anexo=evidence).comparisons)
    assert row.change_class == CHANGE_ANNEX_CREATED_CONFLICT
    assert row.augmented_resolution_status == "mixed_requires_review"


# --------------------------------------------------------------------------
# 20-23: determinism and provenance integrity
# --------------------------------------------------------------------------
def test_result_is_independent_of_input_ordering():
    chunks = [
        make_chunk("C-1", "Centrífuga refrigerada de sobremesa", ordinal=1),
        make_chunk("C-2", "Balanza analítica de precisión", ordinal=2, attachment_id="A-2"),
        make_chunk("C-3", "Baño ultrasónico de 10 litros", ordinal=3, attachment_id="A-2"),
    ]
    attachments = [make_attachment("A-1"), make_attachment("A-2")]
    bundles = [make_bundle(discovered=2, downloaded=2)]

    forward = run_comparison([make_tender()], anexo=make_evidence(chunks, attachments, bundles))
    reversed_ = run_comparison(
        [make_tender()],
        anexo=make_evidence(list(reversed(chunks)), list(reversed(attachments)), bundles),
    )
    assert forward.semantic_digest == reversed_.semantic_digest
    assert [c.claim_id for c in forward.claims] == [c.claim_id for c in reversed_.claims]


def test_semantic_digest_is_stable_across_reruns():
    def build():
        return run_comparison(
            [make_tender()],
            anexo=make_evidence(
                [make_chunk("C-1", "Centrífuga refrigerada de sobremesa")],
                [make_attachment("A-1")],
                [make_bundle()],
            ),
        )

    assert build().semantic_digest == build().semantic_digest


def test_every_claim_references_a_real_chunk_and_segment():
    result = run_comparison(
        [make_tender()],
        anexo=make_evidence(
            [make_chunk("C-1", "Centrífuga refrigerada de sobremesa 15.000 rpm")],
            [make_attachment("A-1")],
            [make_bundle()],
        ),
    )
    chunk_ids = {s.chunk_id for s in result.segments}
    segment_ids = {s.segment_id for s in result.segments}
    assert result.claims
    for claim in result.claims:
        assert claim.chunk_id in chunk_ids
        assert claim.segment_id in segment_ids
        assert claim.attachment_sha256


def test_orphan_claim_provenance_is_rejected():
    result = run_comparison(
        [make_tender()],
        anexo=make_evidence(
            [make_chunk("C-1", "Centrífuga refrigerada de sobremesa")],
            [make_attachment("A-1")],
            [make_bundle()],
        ),
    )
    tampered = result.__class__(**{**result.__dict__, "segments": ()})
    with pytest.raises(AnexoComparisonError, match="unknown chunk"):
        assert_read_only_invariants(tampered)


def test_every_chunk_is_accounted_for_even_when_it_yields_no_segment():
    chunks = [
        make_chunk("C-1", "Centrífuga refrigerada de sobremesa"),
        make_chunk("C-2", "  ", ordinal=2),
    ]
    row = only(
        run_comparison(
            [make_tender()],
            anexo=make_evidence(chunks, [make_attachment("A-1")], [make_bundle()]),
        ).comparisons
    )
    assert row.annex_chunk_total == 2
    assert row.annex_chunks_without_segments == 1


# --------------------------------------------------------------------------
# 24-27: artifacts, authorization, production isolation
# --------------------------------------------------------------------------
def test_published_artifacts_carry_no_portal_tokens(tmp_path):
    app_root = Path(__file__).resolve().parents[1]
    out_dir = app_root / "reports" / "out" / "anexo_e2_test_artifacts"
    result = run_comparison(
        [make_tender()],
        anexo=make_evidence(
            [make_chunk("C-1", "Centrífuga refrigerada de sobremesa 15.000 rpm")],
            [make_attachment("A-1")],
            [make_bundle()],
        ),
    )
    paths = write_comparison_outputs(result, out_dir, repo_email_pipeline_root=app_root)
    try:
        for path in paths.values():
            text = Path(path).read_text(encoding="utf-8").lower()
            for token in ("qs=", "enc=", "ticket="):
                assert token not in text, f"{token} leaked into {path}"
        summary = json.loads(Path(paths["summary"]).read_text(encoding="utf-8"))
        assert summary["contact_authorization"] is False
        assert summary["outreach_authorization"] is False
    finally:
        import shutil

        shutil.rmtree(out_dir, ignore_errors=True)


def test_authorization_flags_remain_false():
    result = run_comparison(
        [make_tender()],
        anexo=make_evidence([], [], []),
    )
    assert result.contact_authorization is False
    assert result.outreach_authorization is False
    assert result.persisted is False
    assert result.production_queue_mutated is False
    assert build_summary(result)["persisted"] is False


@pytest.mark.parametrize(
    "field", ["contact_authorization", "outreach_authorization", "persisted", "production_queue_mutated"]
)
def test_invariants_fail_loudly_if_a_flag_is_ever_flipped(field):
    result = run_comparison([make_tender()], anexo=make_evidence([], [], []))
    tampered = result.__class__(**{**result.__dict__, field: True})
    with pytest.raises(AnexoComparisonError):
        assert_read_only_invariants(tampered)


def test_lane_imports_no_persistence_or_messaging_modules():
    """The comparison lane must not reach production mutation surfaces."""
    package = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "origenlab_email_pipeline"
        / "commercial_procurement_anexo_recognition"
    )
    forbidden = ("sqlite3", "psycopg", "smtplib", "googleapiclient", "gmail")
    for module_path in sorted(package.glob("*.py")):
        source = module_path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert f"import {needle}" not in source, f"{module_path.name} imports {needle}"


def test_production_queue_modules_are_untouched_by_running_the_lane():
    """Running E2 must not perturb PR5D rule/taxonomy identity."""
    from origenlab_email_pipeline.commercial_procurement_product_relevance.fingerprint import (
        relevance_rules_fingerprint,
        relevance_taxonomy_fingerprint,
    )

    before = (relevance_rules_fingerprint(), relevance_taxonomy_fingerprint())
    run_comparison(
        [make_tender()],
        anexo=make_evidence(
            [make_chunk("C-1", "Centrífuga refrigerada de sobremesa")],
            [make_attachment("A-1")],
            [make_bundle()],
        ),
    )
    assert (relevance_rules_fingerprint(), relevance_taxonomy_fingerprint()) == before


# --------------------------------------------------------------------------
# regression against the real #450/#451 extractor interfaces
# --------------------------------------------------------------------------
def test_segmenter_consumes_real_extractor_output_for_docx():
    """Fail loudly if #450 chunk shapes drift away from what E2 expects."""
    from chilecompra_anexo_evidence.synthetic_documents import make_docx  # type: ignore
    from origenlab_email_pipeline.chilecompra_anexo_evidence.extract import extract_payload

    payload = make_docx(
        paragraph="Anexo técnico",
        table_rows=[["Ítem", "Producto"], ["1", "Centrífuga refrigerada de sobremesa"]],
    )
    output = extract_payload(payload, detected_format="docx")
    assert output.outcome == "extraction_success"

    # Mirror how the #450 bundle builder promotes drafts into chunk rows.
    chunks = [
        make_chunk(
            f"C-{index}",
            draft.text,
            locator_type=draft.locator_type,
            locator=dict(draft.locator),
            ordinal=index,
        )
        for index, draft in enumerate(output.chunks, start=1)
    ]
    assert chunks, "extractor produced no chunks"
    segments = segment_chunks(chunks, attachments=[make_attachment("A-1")])
    assert segments
    assert {s.locator_type for s in segments} <= {"docx_paragraph", "docx_table"}
    assert any("Centrífuga" in s.text_raw for s in segments)


def test_segmenter_consumes_real_extractor_output_for_hidden_xlsx_sheet():
    """Hidden-sheet evidence must survive the #451-hardened XLSX path."""
    pytest.importorskip("openpyxl")
    from chilecompra_anexo_evidence.synthetic_documents import make_xlsx  # type: ignore
    from origenlab_email_pipeline.chilecompra_anexo_evidence.extract import extract_payload

    payload = make_xlsx(
        ["Portada", "Especificaciones"],
        cells={
            "Especificaciones": [
                (141, 2, "Centrífuga refrigerada de sobremesa 15.000 rpm"),
            ]
        },
        hidden_sheets=("Especificaciones",),
    )
    output = extract_payload(payload, detected_format="xlsx")
    assert output.outcome == "extraction_success"

    chunks = [
        make_chunk(
            f"C-{index}",
            draft.text,
            locator_type=draft.locator_type,
            locator=dict(draft.locator),
            source_format="xlsx",
            ordinal=index,
        )
        for index, draft in enumerate(output.chunks, start=1)
    ]
    result = run_comparison(
        [make_tender()],
        anexo=make_evidence(
            chunks,
            [make_attachment("A-1", detected_format="xlsx", safe_filename="anexo.xlsx")],
            [make_bundle()],
        ),
    )
    claim = only([c for c in result.claims if c.matched_category == "centrifuge"])
    assert claim.locator_type == "xlsx_rows"
    assert "sheet 'Especificaciones'" in claim.locator_display
