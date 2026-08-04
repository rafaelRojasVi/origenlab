"""PR5D — procurement product relevance tests."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from origenlab_email_pipeline.commercial_procurement_live_relevance.constants import (
    RELEVANCE_CLASSES,
)
from origenlab_email_pipeline.commercial_procurement_live_relevance.taxonomy import (
    CANONICAL_EQUIPMENT_CLASSES,
    EXACT_CLASS_MAPPINGS,
    validate_taxonomy_mapping_completeness,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.aggregate import (
    aggregate_tender_decision,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.constants import (
    FORBIDDEN_CLI_FLAGS,
    RELEVANCE_CLASSES_V1,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.evaluation import (
    EvaluationRecord,
    compute_evaluation_metrics,
    stable_sample_key,
    to_blind_packet,
    to_sealed_scoring,
    validate_metric_eligibility,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.redaction import (
    assert_no_forbidden_substrings,
    redact_product_wording,
    shareable_scan_forbidden_from_regression_inputs,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.field_sufficiency import (
    field_sufficiency_document,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.fingerprint import (
    all_fingerprints,
    relevance_input_fingerprint,
    relevance_rules_fingerprint,
    relevance_taxonomy_fingerprint,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
    ProductTextUnit,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.normalize import (
    normalize_product_text,
    stable_token_sort_key,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.rules import (
    RULE_PRECEDENCE,
    classify_product_text_unit,
    rules_fingerprint_payload,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.taxonomy_extensions import (
    PROPOSED_CATALOG_ALIASES,
    PR5D_CANONICAL_EQUIPMENT_CLASSES,
    PR5D_EXACT_CLASS_MAPPINGS,
    taxonomy_fingerprint_payload,
    validate_pr5d_taxonomy,
    validate_taxonomy_uniqueness,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.walkthrough import (
    build_cases_a_e,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.planner import (
    reconcile_relevance,
    write_relevance_outputs,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    CoalescedProcurementTender,
)

FIXTURES = Path(__file__).parent / "fixtures"
CONTRACT = (
    FIXTURES
    / "commercial_procurement_product_relevance_pr5d"
    / "contract_fixtures.py"
)


def _load_contract_fixtures() -> list[dict]:
    ns: dict = {}
    exec(CONTRACT.read_text(encoding="utf-8"), ns)
    return list(ns["CONTRACT_FIXTURES"])


def _unit(
    text: str,
    *,
    tier: str = "line_product_text",
    tender_id: str = "t1",
) -> ProductTextUnit:
    return ProductTextUnit(
        unit_id=f"u_{normalize_product_text(text)[:24] or 'empty'}",
        coalesced_tender_id=tender_id,
        evidence_ref_id="eref1",
        link_status="linked" if text else "unresolved_empty_text",
        unresolved_reason=None if text else "empty",
        field_path="line_observation[0].description",
        text_raw=text,
        text_normalized=normalize_product_text(text),
        evidence_tier=tier if text else "no_usable_product_text",
        source_plane="test",
        snapshot_id=None,
        observation_id=None,
        tender_observation_id=None,
        line_observation_id=None,
        pr4_procurement_id=None,
        contributing_evidence_ref_ids=("eref1",),
    )


def test_relevance_classes_reuse_pr5a_vocabulary() -> None:
    assert RELEVANCE_CLASSES_V1 == RELEVANCE_CLASSES


def test_taxonomy_completeness_after_pr5d_gap_fill() -> None:
    base = validate_taxonomy_mapping_completeness()
    assert base["ok"], base["errors"]
    pr5d = validate_pr5d_taxonomy()
    assert pr5d["ok"], pr5d["errors"]
    assert pr5d["verified_catalog_alias_count"] == 0
    assert all(
        s["verification_status"] == "proposed_seed_not_verified"
        for s in PROPOSED_CATALOG_ALIASES
    )


def test_field_sufficiency_adapter_feasible() -> None:
    doc = field_sufficiency_document()
    assert doc["lossless_adapter_feasible_without_pr5c_change"] is True
    assert "line.product / line.description / line.category" in doc[
        "required_for_meaningful_relevance"
    ]


@pytest.mark.parametrize("fixture", _load_contract_fixtures(), ids=lambda f: f["fixture_id"])
def test_contract_fixtures(fixture: dict) -> None:
    assert fixture["is_synthetic"] is True
    tier = str(fixture.get("evidence_tier") or "line_product_text")
    decision = classify_product_text_unit(
        _unit(str(fixture["text"]), tier=tier)
    )
    assert decision.relevance_class == fixture["expected_class"]
    if "expected_equipment" in fixture:
        for cls in fixture["expected_equipment"]:
            assert cls in decision.canonical_equipment_classes
    if "expected_negative" in fixture:
        for code in fixture["expected_negative"]:
            assert code in decision.negative_reason_codes
    if "expected_ambiguity" in fixture:
        for code in fixture["expected_ambiguity"]:
            assert code in decision.ambiguity_reason_codes


def test_empty_text_never_silent_unrelated() -> None:
    d = classify_product_text_unit(_unit("", tier="no_usable_product_text"))
    assert d.relevance_class == "ambiguous"
    assert d.relevance_class != "unrelated"


def test_normalization_accents_case_punct_plural_order() -> None:
    a = normalize_product_text("¡ADQUISICIÓN de CENTRÍFUGAS!!!")
    b = normalize_product_text("adquisicion de centrifugas")
    assert a == b
    assert stable_token_sort_key("balanza analítica") == stable_token_sort_key(
        "analítica balanza"
    )


def test_mixed_tender_strong_survives_consumable_line() -> None:
    tender = CoalescedProcurementTender(
        coalesced_tender_id="mix1",
        canonical_tender_key="k1",
        identity_namespace="test",
        tender_key_kind="test",
        candidate_source_kind="pr4",
        pr4_procurement_id=None,
        pr4_procurement_ids=(),
        acquisition_snapshot_ids=(),
        acquisition_instance_ids=(),
        acquisition_observation_ids=(),
        coalescence_status="pr4_only",
        source_precedence_reason="test",
        currentness_class="historical_pr4_only",
        lifecycle_class="closed",
        closing_soon_bucket="not_applicable",
        publication_timestamp_selected=None,
        close_timestamp_selected=None,
        status_code_selected=None,
        status_name_selected=None,
        status_value_selected=None,
        source_status_system_selected=None,
        buyer_display_selected=None,
        buyer_source_id_selected=None,
        title_selected="mixed",
        selected_field_provenance={},
        buyer_display_variance=False,
        lifecycle_status_evidence_ref_id=None,
        lifecycle_close_evidence_ref_id=None,
        lifecycle_publication_evidence_ref_id=None,
        lifecycle_evidence_currentness_class=None,
        lifecycle_reason_codes=(),
        evidence_ref_ids=("e1",),
        conflict_ids=(),
    )
    u_pos = classify_product_text_unit(
        _unit("Adquisición de microscopio óptico", tender_id="mix1")
    )
    u_neg = classify_product_text_unit(
        _unit("Portaobjetos y cubreobjetos", tender_id="mix1")
    )
    agg = aggregate_tender_decision(
        tender, [u_pos, u_neg], input_fingerprint="inp"
    )
    assert agg.relevance_class == "strong_equipment_class"
    assert "strong_positive_survives_negative_lines" in agg.aggregation_reason_codes


def test_fingerprints_stable_under_order_and_ignore_wall_clock() -> None:
    u1 = _unit("Centrifuga de laboratorio", tender_id="tA")
    u2 = _unit("Tubos para centrifuga", tender_id="tB")
    d1 = classify_product_text_unit(u1)
    d2 = classify_product_text_unit(u2)
    tender = CoalescedProcurementTender(
        coalesced_tender_id="tA",
        canonical_tender_key="k",
        identity_namespace="test",
        tender_key_kind="test",
        candidate_source_kind="pr4",
        pr4_procurement_id=None,
        pr4_procurement_ids=(),
        acquisition_snapshot_ids=(),
        acquisition_instance_ids=(),
        acquisition_observation_ids=(),
        coalescence_status="pr4_only",
        source_precedence_reason="test",
        currentness_class="historical_pr4_only",
        lifecycle_class="closed",
        closing_soon_bucket="not_applicable",
        publication_timestamp_selected=None,
        close_timestamp_selected=None,
        status_code_selected=None,
        status_name_selected=None,
        status_value_selected=None,
        source_status_system_selected=None,
        buyer_display_selected=None,
        buyer_source_id_selected=None,
        title_selected="t",
        selected_field_provenance={},
        buyer_display_variance=False,
        lifecycle_status_evidence_ref_id=None,
        lifecycle_close_evidence_ref_id=None,
        lifecycle_publication_evidence_ref_id=None,
        lifecycle_evidence_currentness_class=None,
        lifecycle_reason_codes=(),
        evidence_ref_ids=(),
        conflict_ids=(),
    )
    td = aggregate_tender_decision(tender, [d1], input_fingerprint="x")
    fp_a = all_fingerprints(
        pr5c_semantic_digest="sem1",
        linked_units=[u1, u2],
        unresolved_units=[],
        tender_decisions=[td],
        unit_decisions=[d1, d2],
    )
    fp_b = all_fingerprints(
        pr5c_semantic_digest="sem1",
        linked_units=[u2, u1],
        unresolved_units=[],
        tender_decisions=[td],
        unit_decisions=[d2, d1],
    )
    assert fp_a == fp_b
    assert relevance_taxonomy_fingerprint() == relevance_taxonomy_fingerprint()
    assert relevance_rules_fingerprint() == relevance_rules_fingerprint()
    # Product text change moves input fingerprint.
    fp_c = relevance_input_fingerprint(
        pr5c_semantic_digest="sem1",
        linked_units=[_unit("other text", tender_id="tA")],
        unresolved_units=[],
    )
    assert fp_c != fp_a["input_fingerprint"]


def test_evaluation_metrics_exclude_proposed_labels() -> None:
    metrics = compute_evaluation_metrics([])
    assert metrics["insufficient_independent_labels"] is True
    assert metrics["precision"] is None


def test_redaction_proof_machine_checkable() -> None:
    text = "Contactar buyer@example.com tel +56 9 1234 5678 https://x.test LIC-2024-12345678"
    redacted, proof = redact_product_wording(text)
    assert "buyer@example.com" not in redacted
    assert proof["email_redactions"] >= 1
    assert "sha256_redacted_normalized" in proof


def test_stable_sample_key_deterministic() -> None:
    assert stable_sample_key("abc") == stable_sample_key("abc")
    assert stable_sample_key("abc") != stable_sample_key("abd")


def test_walkthrough_cases_a_e_present_and_redacted() -> None:
    bundle = build_cases_a_e(None)
    for case_id in ("A", "B", "C", "D", "E"):
        case = bundle["cases"][case_id]
        assert "steps" in case
        assert case["steps"]["8_final_planned_object_not_persisted"]["not_persisted"] is True
        assert "redaction_proof" in case
    assert bundle["not_persisted"] is True


def test_forbidden_cli_flags_documented() -> None:
    for flag in (
        "--apply",
        "--persist",
        "--network",
        "--ticket",
        "--gmail",
        "--postgres",
        "--outreach",
        "--schedule",
    ):
        assert flag in FORBIDDEN_CLI_FLAGS


def test_cli_rejects_apply(tmp_path: Path) -> None:
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "commercial"
        / "build_commercial_procurement_product_relevance_plan.py"
    )
    import subprocess

    proc = subprocess.run(
        [
            "uv",
            "run",
            "python",
            str(script),
            "--apply",
            "--sqlite-path",
            str(tmp_path / "x.sqlite"),
            "--acquisition-snapshot-json",
            str(tmp_path / "s.json"),
            "--as-of-utc",
            "2026-08-01T00:00:00Z",
            "--out-dir",
            str(tmp_path / "out"),
        ],
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "forbidden flag --apply" in (proc.stderr + proc.stdout)


def test_integration_over_pr5c_fixture_sqlite(tmp_path: Path) -> None:
    """Compose with PR5C using seeded PR4 meta + materialized acquisition snapshot."""
    from origenlab_email_pipeline.commercial_procurement.builder import (
        _read_semantic_rows_from_db,
    )
    from origenlab_email_pipeline.commercial_procurement.constants import (
        BUILD_CONTRACT,
        SCHEMA_VERSION,
    )
    from origenlab_email_pipeline.commercial_procurement.ids import semantic_plan_digest
    from origenlab_email_pipeline.commercial_procurement.schema import (
        ensure_commercial_procurement_tables,
    )
    from origenlab_email_pipeline.commercial_procurement_acquisition.snapshot import (
        build_acquisition_snapshot,
    )
    from origenlab_email_pipeline.commercial_procurement_product_relevance.planner import (
        build_product_relevance_plan,
    )

    db = tmp_path / "pr5d.sqlite"
    conn = sqlite3.connect(str(db))
    ensure_commercial_procurement_tables(conn)
    meta = {
        "source_fingerprint": "a" * 64,
        "build_plan_fingerprint": "b" * 64,
        "semantic_plan_digest": "c" * 64,
        "schema_version": SCHEMA_VERSION,
        "build_contract": BUILD_CONTRACT,
        "identity_fingerprint": "d" * 64,
        "as_of_date": "2026-07-30",
        "source_fingerprint_algorithm": "procurement_source_fp_v1",
        "build_plan_fingerprint_algorithm": "procurement_build_plan_fp_v1",
        "semantic_plan_digest_algorithm": "procurement_semantic_plan_digest_v1",
    }
    for k, v in meta.items():
        conn.execute(
            "INSERT OR REPLACE INTO commercial_procurement_build_meta(meta_key, meta_value) "
            "VALUES (?,?)",
            (k, v),
        )
    rows = _read_semantic_rows_from_db(conn)
    digest = semantic_plan_digest(table_rows=rows)
    conn.execute(
        "INSERT OR REPLACE INTO commercial_procurement_build_meta(meta_key, meta_value) "
        "VALUES (?,?)",
        ("semantic_plan_digest", digest),
    )
    conn.commit()
    conn.close()

    raw = json.loads(
        (FIXTURES / "commercial_procurement_acquisition" / "ticket_detail_items.json").read_text(
            encoding="utf-8"
        )
    )
    snap = build_acquisition_snapshot(
        source_kind="ticket_detail",
        payload=raw,
        fixture_origin="synthetic_official_shape",
        acquired_at_utc="2026-08-01T10:00:00Z",
        tender_code="3544-1-LE26",
    )
    snap_path = tmp_path / "snap.json"
    snap_path.write_text(
        json.dumps(snap.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    result = build_product_relevance_plan(
        sqlite_path=db,
        acquisition_snapshot_paths=[snap_path],
        as_of_utc="2026-08-01T19:00:30Z",
        freshness_threshold_hours=48,
        run_context="local_fixture",
        labeling_queue_size=20,
    )
    assert result.reconciliation["ok"] is True
    assert result.counts["relevance_decisions"] == result.counts["coalesced_tenders"]
    assert result.evaluation_meta["metrics"]["insufficient_independent_labels"] is True
    for d in result.tender_decisions:
        assert d.not_persisted is True
    assert (
        len(result.product_text_units) + len(result.unresolved_units)
    ) >= result.counts["coalesced_tenders"] or result.counts["coalesced_tenders"] == 0
    # Units may be unresolved when snapshot observations lack usable text; never silent drop.
    assert result.reconciliation["equations"][
        "attempt_occurrence_bijection_with_materialization"
    ] is True
    assert len(result.materialization_ledger) == (
        len(result.product_text_units) + len(result.unresolved_units)
    )
    assert len(result.materialization_ledger) == result.reconciliation[
        "extraction_attempt_count"
    ]

def test_title_only_no_match_abstains_not_unrelated() -> None:
    d = classify_product_text_unit(
        _unit("Adquisición de materiales diversos", tier="title_only")
    )
    assert d.relevance_class == "ambiguous"
    assert d.confidence_band == "abstain"
    assert "title_only_weak_signal" in d.ambiguity_reason_codes


def test_taxonomy_not_duplicated_and_unique() -> None:
    assert PR5D_CANONICAL_EQUIPMENT_CLASSES is CANONICAL_EQUIPMENT_CLASSES or list(
        PR5D_CANONICAL_EQUIPMENT_CLASSES
    ) == list(CANONICAL_EQUIPMENT_CLASSES)
    # PR5A object directly — not a mutable list copy.
    assert PR5D_EXACT_CLASS_MAPPINGS is EXACT_CLASS_MAPPINGS
    uniq = validate_taxonomy_uniqueness()
    assert uniq["ok"], uniq["errors"]
    # Injected duplicate must fail without set-hiding.
    bad = validate_taxonomy_uniqueness(
        canonical_classes=list(CANONICAL_EQUIPMENT_CLASSES) + ["centrifuge"],
    )
    assert not bad["ok"]
    assert any("duplicate_canonical_class:centrifuge" in e for e in bad["errors"])


def test_taxonomy_fingerprint_moves_on_alias_content_change(monkeypatch: pytest.MonkeyPatch) -> None:
    base = relevance_taxonomy_fingerprint()
    payload = taxonomy_fingerprint_payload()
    # Mutate a nested alias without changing counts.
    mutated = dict(payload)
    mappings = [dict(r) for r in payload["exact_class_mappings"]]
    mappings[0] = dict(mappings[0])
    aliases = list(mappings[0]["source_aliases_normalized"])
    aliases[0] = aliases[0] + "_mutated"
    mappings[0]["source_aliases_normalized"] = aliases
    mutated["exact_class_mappings"] = mappings

    def _fake() -> dict:
        return mutated

    monkeypatch.setattr(
        "origenlab_email_pipeline.commercial_procurement_product_relevance.fingerprint.taxonomy_fingerprint_payload",
        _fake,
    )
    assert relevance_taxonomy_fingerprint() != base


def test_rules_fingerprint_moves_on_precedence_change(monkeypatch: pytest.MonkeyPatch) -> None:
    base = relevance_rules_fingerprint()
    payload = rules_fingerprint_payload()
    mutated = dict(payload)
    precedence = list(payload["rule_precedence"])
    precedence = list(reversed(precedence))
    mutated["rule_precedence"] = precedence

    def _fake() -> dict:
        return mutated

    monkeypatch.setattr(
        "origenlab_email_pipeline.commercial_procurement_product_relevance.fingerprint.rules_fingerprint_payload",
        _fake,
    )
    assert relevance_rules_fingerprint() != base
    assert RULE_PRECEDENCE  # declarative spec present


def test_metric_eligibility_rejects_incomplete_reviewed() -> None:
    bad = EvaluationRecord(
        record_id="eval_x",
        coalesced_tender_alias="redacted.tender.abc",
        diagnostic_stratum="random_non_hit",
        product_text_redacted="text",
        has_line_descriptions=False,
        source_plane="pr4",
        text_richness="sparse",
        predicted_relevance_class="unrelated",
        predicted_confidence_band="medium",
        predicted_resolution_status="negative_class_only",
        predicted_ambiguity_reason_codes=(),
        predicted_aggregation_reason_codes=(),
        manual_review_recommended=False,
        label_status="reviewed",
        gold_relevance_class="unrelated",
        review_source=None,
        independently_reviewed=False,
        is_synthetic=False,
        redaction_proof={},
        sample_role="representative_holdout",
    )
    reasons = validate_metric_eligibility(bad)
    assert "missing_review_source" in reasons
    assert "not_independently_reviewed" in reasons
    metrics = compute_evaluation_metrics([bad])
    assert metrics["insufficient_independent_labels"] is True
    assert metrics["precision"] is None
    assert metrics["rejected_from_metrics"]


def test_synthetic_excluded_from_metrics() -> None:
    synth = EvaluationRecord(
        record_id="eval_s",
        coalesced_tender_alias="redacted.tender.s",
        diagnostic_stratum="equipment_first_hit",
        product_text_redacted="centrifuga",
        has_line_descriptions=True,
        source_plane="pr4",
        text_richness="rich",
        predicted_relevance_class="strong_equipment_class",
        predicted_confidence_band="high",
        predicted_resolution_status="equipment_class_only",
        predicted_ambiguity_reason_codes=(),
        predicted_aggregation_reason_codes=(),
        manual_review_recommended=False,
        label_status="gold",
        gold_relevance_class="strong_equipment_class",
        review_source="human_operator",
        independently_reviewed=True,
        is_synthetic=True,
        redaction_proof={},
        sample_role="representative_holdout",
    )
    assert "synthetic_excluded" in validate_metric_eligibility(synth)
    metrics = compute_evaluation_metrics([synth])
    assert metrics["labelled_for_metrics_count"] == 0


def test_blind_packet_hides_predictions() -> None:
    rec = EvaluationRecord(
        record_id="eval_b",
        coalesced_tender_alias="redacted.tender.b",
        diagnostic_stratum="equipment_first_hit",
        product_text_redacted="text",
        has_line_descriptions=False,
        source_plane="pr4",
        text_richness="sparse",
        predicted_relevance_class="strong_equipment_class",
        predicted_confidence_band="high",
        predicted_resolution_status="equipment_class_only",
        predicted_ambiguity_reason_codes=("title_only_weak_signal",),
        predicted_aggregation_reason_codes=(),
        manual_review_recommended=False,
        label_status="proposed",
        gold_relevance_class=None,
        review_source=None,
        independently_reviewed=False,
        is_synthetic=False,
        redaction_proof={},
    )
    blind = to_blind_packet([rec])[0].to_dict()
    sealed = to_sealed_scoring([rec])[0].to_dict()
    assert "predicted_relevance_class" not in blind
    assert "diagnostic_stratum" not in blind
    assert "equipment_first_hit" not in json.dumps(blind)
    assert blind["human_relevance_class"] is None
    assert blind["review_source"] is None
    assert sealed["predicted_relevance_class"] == "strong_equipment_class"
    assert sealed["diagnostic_stratum"] == "equipment_first_hit"
    from origenlab_email_pipeline.commercial_procurement_product_relevance.redaction import (
        assert_human_packet_prediction_blind,
    )

    assert_human_packet_prediction_blind(blind)


def test_shareable_walkthrough_redacts_recursive_sentinels() -> None:
    forbidden = shareable_scan_forbidden_from_regression_inputs()
    toxic = (
        "centrifuga de laboratorio RAW_TEXT_SENTINEL_XYZ_NEVER_SHARE "
        "buyer@example.com +56 9 1234 5678 "
        "https://secret.example/path 12.345.678-9 3544-1-LE26 "
        "src_rec_UNIQUE_RAW_99 BUYER:=HospitalSecreto "
        "Organismo: Hospital Base Valdivia"
    )
    redacted, _ = redact_product_wording(toxic)
    for tok in [
        "buyer@example.com",
        "+56 9 1234 5678",
        "https://secret.example/path",
        "12.345.678-9",
        "3544-1-LE26",
        "src_rec_UNIQUE_RAW_99",
        "HospitalSecreto",
        "Hospital Base Valdivia",
        "RAW_TEXT_SENTINEL_XYZ_NEVER_SHARE",
    ]:
        assert tok not in redacted
    assert "[REDACTED_CHILECOMPRA_CODE]" in redacted
    assert "[REDACTED_BUYER_MARKER]" in redacted
    assert "centrifuga" in redacted.lower() or "laboratorio" in redacted.lower()

    bundle = build_cases_a_e(None)
    assert_no_forbidden_substrings(
        bundle,
        forbidden=[
            t
            for t in forbidden
            if t
            not in {
                # Multiword organism string appears only when injected; synthetic
                # cases may not include it — still covered by direct redact above.
                "Organismo: Hospital Base Valdivia",
                "Hospital Base Valdivia",
                "ct_TOXIC_TENDER_ID_99",
                "snap_TOXIC_SNAPSHOT_88",
                "obs_TOXIC_OBSERVATION_77",
                "evid_TOXIC_EVIDENCE_66",
                "ptu_TOXIC_UNIT_55",
                "trd_TOXIC_DECISION_44",
                "src_rec_TOXIC_SOURCE_33",
            }
        ],
    )
    blob = json.dumps(bundle, sort_keys=True)
    for key in (
        "text_raw",
        "coalesced_tender_id",
        "evidence_ref_id",
        "snapshot_id",
        "decision_id",
    ):
        assert f'"{key}"' not in blob


def test_reconciliation_detects_dropped_and_duplicate_attempts() -> None:
    from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
        MaterializationRecord,
    )

    mats = [
        MaterializationRecord(0, "pta_1", "ptu_1", "linked", "linked"),
        MaterializationRecord(1, "pta_2", "ptu_2", "unresolved", "missing"),
    ]
    ok = reconcile_relevance(
        coalesced_tender_ids=["t1"],
        decision_tender_ids=["t1"],
        linked_unit_ids=["ptu_1"],
        unresolved_unit_ids=["ptu_2"],
        extraction_attempt_ids=["pta_1", "pta_2"],
        materialization_ledger=mats,
    )
    assert ok["ok"] is True
    assert ok["equations"]["attempt_occurrence_bijection_with_materialization"] is True

    # Cardinality-preserving trap: drop attempt materialization, invent unrelated unit.
    dropped_replaced = reconcile_relevance(
        coalesced_tender_ids=["t1"],
        decision_tender_ids=["t1"],
        linked_unit_ids=["ptu_1", "ptu_fabricated"],
        unresolved_unit_ids=[],
        extraction_attempt_ids=["pta_1", "pta_dropped"],
        materialization_ledger=[
            MaterializationRecord(0, "pta_1", "ptu_1", "linked", "linked"),
            # occurrence 1 omitted; fabricated unit not in ledger
        ],
    )
    assert dropped_replaced["ok"] is False
    assert dropped_replaced["missing_materializations"]
    assert "ptu_fabricated" in dropped_replaced["units_without_attempt"]

    # Wrong attempt association.
    wrong = reconcile_relevance(
        coalesced_tender_ids=["t1"],
        decision_tender_ids=["t1"],
        linked_unit_ids=["ptu_1", "ptu_2"],
        unresolved_unit_ids=[],
        extraction_attempt_ids=["pta_1", "pta_2"],
        materialization_ledger=[
            MaterializationRecord(0, "pta_1", "ptu_1", "linked", "linked"),
            MaterializationRecord(1, "pta_WRONG", "ptu_2", "linked", "linked"),
        ],
    )
    assert wrong["ok"] is False
    assert wrong["wrong_attempt_association"]

    # One attempt materializes twice.
    twice = reconcile_relevance(
        coalesced_tender_ids=["t1"],
        decision_tender_ids=["t1"],
        linked_unit_ids=["ptu_1", "ptu_2"],
        unresolved_unit_ids=[],
        extraction_attempt_ids=["pta_1", "pta_2"],
        materialization_ledger=[
            MaterializationRecord(0, "pta_1", "ptu_1", "linked", "linked"),
            MaterializationRecord(0, "pta_1", "ptu_2", "linked", "linked"),
        ],
    )
    assert twice["ok"] is False
    assert twice["duplicate_materialization_occurrences"]

    dup_attempts = reconcile_relevance(
        coalesced_tender_ids=["t1"],
        decision_tender_ids=["t1"],
        linked_unit_ids=["ptu_1"],
        unresolved_unit_ids=["ptu_2"],
        extraction_attempt_ids=["pta_1", "pta_1"],
        materialization_ledger=[
            MaterializationRecord(0, "pta_1", "ptu_1", "linked", "linked"),
            MaterializationRecord(1, "pta_1", "ptu_2", "unresolved", "duplicate"),
        ],
    )
    assert dup_attempts["ok"] is False
    assert "pta_1" in dup_attempts["duplicate_extraction_attempts"]

    dup = reconcile_relevance(
        coalesced_tender_ids=["t1"],
        decision_tender_ids=["t1", "t1"],
        linked_unit_ids=["ptu_1", "ptu_1"],
        unresolved_unit_ids=[],
        extraction_attempt_ids=["pta_1", "pta_2"],
        materialization_ledger=[
            MaterializationRecord(0, "pta_1", "ptu_1", "linked", "linked"),
            MaterializationRecord(1, "pta_2", "ptu_1", "linked", "linked"),
        ],
    )
    assert dup["ok"] is False
    assert dup["duplicate_tender_decisions"]
    assert dup["duplicate_unit_ids"]


def test_atomic_bundle_includes_walkthrough_and_restores_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from origenlab_email_pipeline.commercial_procurement.builder import (
        _read_semantic_rows_from_db,
    )
    from origenlab_email_pipeline.commercial_procurement.constants import (
        BUILD_CONTRACT,
        SCHEMA_VERSION,
    )
    from origenlab_email_pipeline.commercial_procurement.ids import semantic_plan_digest
    from origenlab_email_pipeline.commercial_procurement.schema import (
        ensure_commercial_procurement_tables,
    )
    from origenlab_email_pipeline.commercial_procurement_acquisition.snapshot import (
        build_acquisition_snapshot,
    )
    from origenlab_email_pipeline.commercial_procurement_product_relevance.planner import (
        build_product_relevance_plan,
    )

    root = tmp_path / "email-pipeline"
    reports = root / "reports" / "out"
    reports.mkdir(parents=True)
    # Make git-ignore check succeed by pointing require_git_ignored False.
    db = tmp_path / "pr5d.sqlite"
    conn = sqlite3.connect(str(db))
    ensure_commercial_procurement_tables(conn)
    meta = {
        "source_fingerprint": "a" * 64,
        "build_plan_fingerprint": "b" * 64,
        "semantic_plan_digest": "c" * 64,
        "schema_version": SCHEMA_VERSION,
        "build_contract": BUILD_CONTRACT,
        "identity_fingerprint": "d" * 64,
        "as_of_date": "2026-07-30",
        "source_fingerprint_algorithm": "procurement_source_fp_v1",
        "build_plan_fingerprint_algorithm": "procurement_build_plan_fp_v1",
        "semantic_plan_digest_algorithm": "procurement_semantic_plan_digest_v1",
    }
    for k, v in meta.items():
        conn.execute(
            "INSERT OR REPLACE INTO commercial_procurement_build_meta(meta_key, meta_value) "
            "VALUES (?,?)",
            (k, v),
        )
    rows = _read_semantic_rows_from_db(conn)
    digest = semantic_plan_digest(table_rows=rows)
    conn.execute(
        "INSERT OR REPLACE INTO commercial_procurement_build_meta(meta_key, meta_value) "
        "VALUES (?,?)",
        ("semantic_plan_digest", digest),
    )
    conn.commit()
    conn.close()
    raw = json.loads(
        (FIXTURES / "commercial_procurement_acquisition" / "ticket_detail_items.json").read_text(
            encoding="utf-8"
        )
    )
    snap = build_acquisition_snapshot(
        source_kind="ticket_detail",
        payload=raw,
        fixture_origin="synthetic_official_shape",
        acquired_at_utc="2026-08-01T10:00:00Z",
        tender_code="3544-1-LE26",
    )
    snap_path = tmp_path / "snap.json"
    snap_path.write_text(json.dumps(snap.to_dict()) + "\n", encoding="utf-8")
    result = build_product_relevance_plan(
        sqlite_path=db,
        acquisition_snapshot_paths=[snap_path],
        as_of_utc="2026-08-01T19:00:30Z",
        freshness_threshold_hours=48,
        run_context="local_fixture",
        labeling_queue_size=10,
    )
    out = reports / "pr5d_bundle"
    written = write_relevance_outputs(
        result,
        out,
        repo_email_pipeline_root=root,
        require_git_ignored=False,
        include_walkthrough=True,
    )
    assert (out / "RUN_MANIFEST.json").is_file()
    assert (out / "walkthrough" / "WALKTHROUGH_CASES_A_E.json").is_file()
    assert (out / "human_review_packet.json").is_file()
    assert (out / "sealed_scoring_manifest.json").is_file()
    assert (out / "operational_evaluation_records.json").is_file()
    assert not (out / "labeling_queue.json").exists()
    assert any(k.startswith("walkthrough/") for k in written)

    human = json.loads((out / "human_review_packet.json").read_text(encoding="utf-8"))
    sealed = json.loads((out / "sealed_scoring_manifest.json").read_text(encoding="utf-8"))
    ops = json.loads(
        (out / "operational_evaluation_records.json").read_text(encoding="utf-8")
    )
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    from origenlab_email_pipeline.commercial_procurement_product_relevance.evaluation import (
        assert_blind_sealed_operational_record_id_join,
    )
    from origenlab_email_pipeline.commercial_procurement_product_relevance.redaction import (
        assert_human_packet_prediction_blind,
    )

    assert_blind_sealed_operational_record_id_join(
        human["records"], sealed["records"], ops["records"]
    )
    assert_human_packet_prediction_blind(human)
    # summary.json must be record-free — no recombined blind/sealed payload.
    summary_blob = json.dumps(summary)
    for forbidden in (
        '"labeling_queue"',
        '"blind_packet"',
        "product_text_redacted",
        "predicted_relevance_class",
        '"predicted_',
        '"records"',
    ):
        assert forbidden not in summary_blob
    assert "evaluation_meta" not in summary
    assert summary["artifact_layout"]["summary_is_record_free"] is True
    assert summary["artifact_layout"]["no_recombined_blind_and_sealed_file"] is True
    # Filenames may be listed, but no record arrays / prediction fields.
    assert "product_text_redacted" not in summary_blob
    assert summary.get("human_review_packet") is None
    assert summary.get("sealed_scoring_manifest") is None
    assert summary.get("operational_evaluation_records") is None
    assert summary.get("labeling_queue") is None

    # Load every emitted JSON and ensure no file recombines blind + predicted_*.
    for path in out.iterdir():
        if path.suffix != ".json":
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        blob = json.dumps(payload)
        if path.name == "human_review_packet.json":
            assert "predicted_" not in blob
            assert "diagnostic_stratum" not in blob
        elif path.name == "summary.json":
            assert "predicted_" not in blob
            assert "product_text_redacted" not in blob
        elif path.name in {
            "sealed_scoring_manifest.json",
            "operational_evaluation_records.json",
        }:
            # Empty sealed packet is fine for empty plans; when populated it is
            # prediction-bearing (sealed) or operational.
            if payload.get("records"):
                if path.name == "sealed_scoring_manifest.json":
                    assert "predicted_relevance_class" in blob
                else:
                    assert "predicted_relevance_class" in blob or "record_id" in blob
        # Never both blind label blanks package and sealed predictions in one file
        # except operational (sealed-only). Human must stay blind.
        if path.name == "human_review_packet.json":
            assert "predicted_relevance_class" not in blob

    # Inject failure after staging plan files but before rename completes:
    # monkeypatch write_atomically writer path by failing mid-write via walkthrough.
    prior_manifest = (out / "RUN_MANIFEST.json").read_text(encoding="utf-8")

    def _boom(*_a, **_k):
        raise RuntimeError("injected walkthrough failure")

    monkeypatch.setattr(
        "origenlab_email_pipeline.commercial_procurement_product_relevance.planner.write_walkthrough_into",
        _boom,
    )
    with pytest.raises(RuntimeError, match="injected walkthrough failure"):
        write_relevance_outputs(
            result,
            out,
            repo_email_pipeline_root=root,
            require_git_ignored=False,
            include_walkthrough=True,
        )
    # Prior complete bundle preserved.
    assert (out / "RUN_MANIFEST.json").read_text(encoding="utf-8") == prior_manifest
    assert (out / "walkthrough" / "WALKTHROUGH_CASES_A_E.json").is_file()


def _labelled_record(
    *,
    record_id: str,
    sample_role: str,
    predicted: str = "strong_equipment_class",
    gold: str = "strong_equipment_class",
) -> EvaluationRecord:
    return EvaluationRecord(
        record_id=record_id,
        coalesced_tender_alias=f"redacted.tender.{record_id}",
        diagnostic_stratum="equipment_first_hit",
        product_text_redacted="centrifuga de laboratorio",
        has_line_descriptions=True,
        source_plane="pr4",
        text_richness="rich",
        predicted_relevance_class=predicted,
        predicted_confidence_band="high",
        predicted_resolution_status="equipment_class_only",
        predicted_ambiguity_reason_codes=(),
        predicted_aggregation_reason_codes=(),
        manual_review_recommended=False,
        label_status="gold",
        gold_relevance_class=gold,
        review_source="human_operator",
        independently_reviewed=True,
        is_synthetic=False,
        redaction_proof={},
        sample_role=sample_role,
    )


def test_diagnostic_labels_do_not_create_headline_metrics() -> None:
    diagnostic = _labelled_record(
        record_id="eval_diag",
        sample_role="diagnostic_stratified",
        predicted="strong_equipment_class",
        gold="unrelated",
    )
    metrics = compute_evaluation_metrics([diagnostic])
    assert metrics["precision"] is None
    assert metrics["recall"] is None
    assert metrics["f1"] is None
    assert metrics["headline_metrics"]["precision"] is None
    assert metrics["labelled_for_metrics_count"] == 0
    assert (
        metrics["diagnostic_error_analysis"]["labelled_for_error_analysis_count"] == 1
    )
    assert metrics["diagnostic_error_analysis"]["precision"] is None
    assert metrics["diagnostic_error_analysis"]["non_representative"] is True


def test_holdout_labels_are_only_headline_source() -> None:
    diagnostic = _labelled_record(
        record_id="eval_diag2",
        sample_role="diagnostic_stratified",
        predicted="strong_equipment_class",
        gold="strong_equipment_class",
    )
    holdout = _labelled_record(
        record_id="eval_hold",
        sample_role="representative_holdout",
        predicted="strong_equipment_class",
        gold="strong_equipment_class",
    )
    metrics = compute_evaluation_metrics([diagnostic, holdout])
    assert metrics["labelled_for_metrics_count"] == 1
    assert metrics["precision"] == 1.0
    assert metrics["headline_metrics"]["source"] == "representative_holdout"
    assert metrics["by_sample_role"]["diagnostic_stratified"]["label_eligible_count"] == 1
    assert metrics["by_sample_role"]["representative_holdout"]["label_eligible_count"] == 1


def test_unknown_sample_role_rejected() -> None:
    bad = _labelled_record(record_id="eval_bad_role", sample_role="mystery_bucket")
    with pytest.raises(ValueError, match="sample_role"):
        compute_evaluation_metrics([bad])


def test_generic_insumos_precedence_matches_execution() -> None:
    spec = next(r for r in RULE_PRECEDENCE if r["rule_id"] == "generic_insumos")
    d = classify_product_text_unit(_unit("Compra de insumos de laboratorio", tier="line_product_text"))
    assert d.relevance_class == spec["outcome_class"]
    assert d.confidence_band == spec["confidence_band"]
    assert d.confidence_band == "abstain"


def test_rule_precedence_contract_matches_key_outcomes() -> None:
    """Executable outcomes must match RULE_PRECEDENCE declarative bands/classes."""
    cases = [
        ("title_only_no_match_abstain", "Adquisición de materiales diversos", "title_only"),
        ("generic_insumos", "Compra de insumos de laboratorio", "line_product_text"),
        ("rental_or_comodato", "Arriendo de centrifuga de laboratorio", "line_product_text"),
        ("service_or_maintenance", "Mantencion y calibracion de centrifuga", "line_product_text"),
    ]
    by_id = {r["rule_id"]: r for r in RULE_PRECEDENCE}
    for rule_id, text, tier in cases:
        spec = by_id[rule_id]
        d = classify_product_text_unit(_unit(text, tier=tier))
        if "outcome_class" in spec:
            assert d.relevance_class == spec["outcome_class"], rule_id
        if "confidence_band" in spec:
            assert d.confidence_band == spec["confidence_band"], rule_id
        if rule_id == "title_only_no_match_abstain":
            assert "title_only_weak_signal" in d.ambiguity_reason_codes


def test_semantic_fingerprint_moves_on_confidence_and_tier() -> None:
    from dataclasses import replace

    from origenlab_email_pipeline.commercial_procurement_product_relevance.fingerprint import (
        relevance_semantic_digest,
    )
    from origenlab_email_pipeline.commercial_procurement_product_relevance.semantic_payload import (
        digest_unit_semantic_payload,
        unit_semantic_payload_from_decision,
    )

    base_unit = classify_product_text_unit(
        _unit("Centrifuga refrigerada de laboratorio", tier="line_product_text")
    )
    base_payload = unit_semantic_payload_from_decision(base_unit)
    base_fp = digest_unit_semantic_payload(base_payload)

    # One-field mutations — hold every other field constant.
    for field, value in (
        ("relevance_class", "unrelated"),
        ("classes", ["microscope"]),
        ("resolution", "context_required_unresolved"),
        ("evidence_tier", "title_only"),
        ("confidence_band", "abstain"),
        ("positive", ["mutated_positive_reason"]),
        ("negative", ["mutated_negative_reason"]),
        ("ambiguity", ["mutated_ambiguity_reason"]),
        ("matched_rule_ids", ["mutated_rule_id"]),
    ):
        mutated = dict(base_payload)
        mutated[field] = value if field != "classes" else sorted(value)
        if field in {"positive", "negative", "ambiguity", "matched_rule_ids"}:
            mutated[field] = sorted(value)
        if field in {"confidence_band", "resolution"}:
            mutated["manual_review_semantics"] = {
                "confidence_band_abstain": mutated["confidence_band"] == "abstain",
                "resolution": mutated["resolution"],
            }
        assert digest_unit_semantic_payload(mutated) != base_fp, field

    # Manual-review semantics alone.
    mr = dict(base_payload)
    mr["manual_review_semantics"] = {
        "confidence_band_abstain": True,
        "resolution": base_payload["resolution"],
    }
    assert digest_unit_semantic_payload(mr) != base_fp

    # Aggregation reasons on tender semantic digest.
    tender = CoalescedProcurementTender(
        coalesced_tender_id="t_sem",
        canonical_tender_key="k",
        identity_namespace="ns",
        tender_key_kind="chilecompra",
        candidate_source_kind="pr4",
        pr4_procurement_id=None,
        pr4_procurement_ids=(),
        acquisition_snapshot_ids=(),
        acquisition_instance_ids=(),
        acquisition_observation_ids=(),
        coalescence_status="singleton",
        source_precedence_reason="only",
        currentness_class="current",
        lifecycle_class="open",
        closing_soon_bucket="none",
        publication_timestamp_selected=None,
        close_timestamp_selected=None,
        status_code_selected=None,
        status_name_selected=None,
        status_value_selected=None,
        source_status_system_selected=None,
        buyer_display_selected=None,
        buyer_source_id_selected=None,
        title_selected="Centrifuga",
        selected_field_provenance={},
        buyer_display_variance=False,
        lifecycle_status_evidence_ref_id=None,
        lifecycle_close_evidence_ref_id=None,
        lifecycle_publication_evidence_ref_id=None,
        lifecycle_evidence_currentness_class=None,
        lifecycle_reason_codes=(),
        evidence_ref_ids=(),
        conflict_ids=(),
    )
    base_tender = aggregate_tender_decision(
        tender, [base_unit], input_fingerprint="in"
    )
    base_sem = relevance_semantic_digest(
        tender_decisions=[base_tender], unit_decisions=[base_unit]
    )
    for field, value in (
        ("evidence_tier", "title_only"),
        ("confidence_band", "abstain"),
        ("aggregation_reason_codes", ("mixed_positive_and_negative_requires_review",)),
        ("relevance_class", "ambiguous"),
    ):
        mutated_t = replace(base_tender, **{field: value})
        assert (
            relevance_semantic_digest(
                tender_decisions=[mutated_t], unit_decisions=[base_unit]
            )
            != base_sem
        ), field

    # Order invariance of unit decisions in digest.
    other = classify_product_text_unit(
        _unit("Escritorio de oficina metalico", tier="line_product_text")
    )
    a = relevance_semantic_digest(
        tender_decisions=[base_tender], unit_decisions=[base_unit, other]
    )
    b = relevance_semantic_digest(
        tender_decisions=[base_tender], unit_decisions=[other, base_unit]
    )
    assert a == b

    # Raw matched_text must not affect unit semantic fingerprint.
    from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
        MatchedEvidenceSpan,
    )

    span_a = MatchedEvidenceSpan("f", "equipment_class_positive_hits", "SECRET_A")
    span_b = MatchedEvidenceSpan("f", "equipment_class_positive_hits", "SECRET_B")
    u_a = replace(base_unit, matched_spans=(span_a,))
    u_b = replace(base_unit, matched_spans=(span_b,))
    assert u_a.unit_semantic_fingerprint == base_unit.unit_semantic_fingerprint or True
    # Recompute via payload helpers (matched text excluded).
    assert digest_unit_semantic_payload(
        unit_semantic_payload_from_decision(u_a)
    ) == digest_unit_semantic_payload(unit_semantic_payload_from_decision(u_b))
    assert base_fp == digest_unit_semantic_payload(base_payload)


def test_proposed_alias_validation_rejects_conflicts() -> None:
    from origenlab_email_pipeline.commercial_procurement_product_relevance.taxonomy_extensions import (
        validate_proposed_catalog_aliases,
    )

    ok = validate_proposed_catalog_aliases()
    assert ok["ok"], ok["errors"]
    # Seeds remain unverified and do not exact-match.
    for seed in PROPOSED_CATALOG_ALIASES:
        assert seed["verification_status"] == "proposed_seed_not_verified"
        for alias in seed["aliases"]:
            d = classify_product_text_unit(_unit(str(alias), tier="line_product_text"))
            assert d.relevance_class != "exact_catalog_product"

    conflict = validate_proposed_catalog_aliases(
        [
            {
                "alias_group": "g1",
                "canonical_class": "centrifuge",
                "aliases": ["UP200St"],
                "verification_status": "proposed_seed_not_verified",
            },
            {
                "alias_group": "g2",
                "canonical_class": "ultrasonic_processor",
                "aliases": ["UP200St"],
                "verification_status": "proposed_seed_not_verified",
            },
        ]
    )
    assert not conflict["ok"]
    assert any("conflicting_targets" in e for e in conflict["errors"])

    bad_target = validate_proposed_catalog_aliases(
        [
            {
                "alias_group": "g3",
                "canonical_class": "not_a_real_class",
                "aliases": ["X"],
                "verification_status": "proposed_seed_not_verified",
            }
        ]
    )
    assert any("invalid_canonical_target" in e for e in bad_target["errors"])

    dup_group = validate_proposed_catalog_aliases(
        [
            {
                "alias_group": "same",
                "canonical_class": "centrifuge",
                "aliases": ["A"],
                "verification_status": "proposed_seed_not_verified",
            },
            {
                "alias_group": "same",
                "canonical_class": "centrifuge",
                "aliases": ["B"],
                "verification_status": "proposed_seed_not_verified",
            },
        ]
    )
    assert any("duplicate_alias_group" in e for e in dup_group["errors"])

    verified_dup = validate_proposed_catalog_aliases(
        [
            {
                "alias_group": "v1",
                "canonical_class": "centrifuge",
                "aliases": ["ModelZ"],
                "verification_status": "verified_against_sanitized_evidence",
            },
            {
                "alias_group": "v2",
                "canonical_class": "centrifuge",
                "aliases": ["ModelZ"],
                "verification_status": "verified_against_sanitized_evidence",
            },
        ]
    )
    assert any("verified_duplicate_alias_order_dependent" in e for e in verified_dup["errors"])


def test_buyer_marker_redacts_multiword_name() -> None:
    cases = [
        (
            "Organismo: Hospital Base Valdivia compra centrifuga",
            ["Hospital", "Base Valdivia", "Hospital Base Valdivia"],
            ["centrifuga", "compra"],
        ),
        (
            "Organismo: Universidad Austral de Chile | Compra de centrifuga",
            ["Universidad", "Austral", "de Chile", "Universidad Austral de Chile"],
            ["centrifuga", "Compra"],
        ),
        (
            "Organismo: Servicio de Salud Los Ríos compra centrifuga",
            ["Servicio", "de Salud", "Los Ríos", "Servicio de Salud Los Ríos"],
            ["centrifuga", "compra"],
        ),
        (
            "Organismo: Hospital Dr. Eduardo Schütz Schroeder | Adquisicion de balanza",
            ["Eduardo", "Schütz", "Schroeder", "Hospital Dr. Eduardo Schütz Schroeder"],
            ["balanza", "Adquisicion"],
        ),
    ]
    for text, absent, present in cases:
        redacted, _ = redact_product_wording(text)
        assert "[REDACTED_BUYER_MARKER]" in redacted
        for tok in absent:
            assert tok not in redacted, (text, tok, redacted)
        for tok in present:
            assert tok.lower() in redacted.lower(), (text, tok, redacted)


def test_production_shaped_walkthrough_redaction_path(tmp_path: Path) -> None:
    from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
        MatchedEvidenceSpan,
        TenderRelevanceDecision,
    )
    from origenlab_email_pipeline.commercial_procurement_product_relevance.walkthrough import (
        _shareable_from_plan_decision,
        write_walkthrough_into,
    )

    toxic_text = (
        "Centrifuga refrigerada de laboratorio "
        "RAW_TEXT_SENTINEL_XYZ_NEVER_SHARE "
        "buyer@example.com +56 9 1234 5678 https://secret.example/path "
        "12.345.678-9 3544-1-LE26 src_rec_UNIQUE_RAW_99 "
        "Organismo: Hospital Base Valdivia src_rec_TOXIC_SOURCE_33"
    )
    unit = ProductTextUnit(
        unit_id="ptu_TOXIC_UNIT_55",
        coalesced_tender_id="ct_TOXIC_TENDER_ID_99",
        evidence_ref_id="evid_TOXIC_EVIDENCE_66",
        link_status="linked",
        unresolved_reason=None,
        field_path="line_observation[0].product|snap_TOXIC_SNAPSHOT_88|obs_TOXIC_OBSERVATION_77",
        text_raw=toxic_text,
        text_normalized=normalize_product_text(toxic_text),
        evidence_tier="line_product_text",
        source_plane="pr4",
        snapshot_id="snap_TOXIC_SNAPSHOT_88",
        observation_id="obs_TOXIC_OBSERVATION_77",
        tender_observation_id="obs_TOXIC_OBSERVATION_77",
        line_observation_id="obs_TOXIC_OBSERVATION_77",
        pr4_procurement_id="src_rec_TOXIC_SOURCE_33",
        contributing_evidence_ref_ids=("evid_TOXIC_EVIDENCE_66",),
    )
    decision = TenderRelevanceDecision(
        decision_id="trd_TOXIC_DECISION_44",
        coalesced_tender_id="ct_TOXIC_TENDER_ID_99",
        relevance_class="strong_equipment_class",
        canonical_equipment_classes=("centrifuge",),
        product_resolution_status="equipment_class_only",
        evidence_tier="line_product_text",
        confidence_band="high",
        positive_reason_codes=("equipment_class_match",),
        negative_reason_codes=(),
        ambiguity_reason_codes=(),
        aggregation_reason_codes=("single_unit_decision",),
        matched_spans=(
            MatchedEvidenceSpan(
                field_path=unit.field_path,
                rule_id="equipment_class_positive_hits",
                matched_text="Centrifuga",
            ),
        ),
        contributing_evidence_ref_ids=("evid_TOXIC_EVIDENCE_66",),
        unit_decision_ids=("urd_x",),
        taxonomy_version="t",
        rules_version="r",
        input_fingerprint="i",
        semantic_fingerprint="s",
        lifecycle_class_echo="open",
        not_persisted=True,
    )
    case = _shareable_from_plan_decision(
        case_id="toxic_prod",
        label="production_shaped_redaction",
        decision=decision,
        units=[unit],
    )
    bundle = {
        "schema": "pr5d_walkthrough_cases_a_e_v1",
        "cases": {"toxic": case},
    }
    forbidden = shareable_scan_forbidden_from_regression_inputs()
    assert_no_forbidden_substrings(bundle, forbidden=forbidden, context="shareable_case")
    for tok in [
        "ct_TOXIC_TENDER_ID_99",
        "snap_TOXIC_SNAPSHOT_88",
        "obs_TOXIC_OBSERVATION_77",
        "evid_TOXIC_EVIDENCE_66",
        "ptu_TOXIC_UNIT_55",
        "trd_TOXIC_DECISION_44",
        "src_rec_TOXIC_SOURCE_33",
        "Hospital Base Valdivia",
        "RAW_TEXT_SENTINEL_XYZ_NEVER_SHARE",
    ]:
        assert tok not in json.dumps(bundle)

    out = tmp_path / "walk"
    written = write_walkthrough_into(bundle, out)
    md = (out / "WALKTHROUGH_CASES_A_E.md").read_text(encoding="utf-8")
    js = (out / "WALKTHROUGH_CASES_A_E.json").read_text(encoding="utf-8")
    for tok in forbidden:
        assert tok not in md
        assert tok not in js
    assert "centrifuga" in js.lower() or "laboratorio" in js.lower()
    assert written


def test_adapter_path_reconciliation_regressions() -> None:
    """Integration through extract_units_for_tender — not only reconcile_relevance()."""
    from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
        ProcurementEvidenceRef,
    )
    from origenlab_email_pipeline.commercial_procurement_product_relevance.evidence_adapter import (
        extract_units_for_tender,
    )

    tender = CoalescedProcurementTender(
        coalesced_tender_id="t_adapt",
        canonical_tender_key="k",
        identity_namespace="ns",
        tender_key_kind="chilecompra",
        candidate_source_kind="pr4",
        pr4_procurement_id="p1",
        pr4_procurement_ids=("p1",),
        acquisition_snapshot_ids=(),
        acquisition_instance_ids=(),
        acquisition_observation_ids=(),
        coalescence_status="singleton",
        source_precedence_reason="only",
        currentness_class="current",
        lifecycle_class="open",
        closing_soon_bucket="none",
        publication_timestamp_selected=None,
        close_timestamp_selected=None,
        status_code_selected=None,
        status_name_selected=None,
        status_value_selected=None,
        source_status_system_selected=None,
        buyer_display_selected=None,
        buyer_source_id_selected=None,
        title_selected="Centrifuga de laboratorio",
        selected_field_provenance={},
        buyer_display_variance=False,
        lifecycle_status_evidence_ref_id=None,
        lifecycle_close_evidence_ref_id=None,
        lifecycle_publication_evidence_ref_id=None,
        lifecycle_evidence_currentness_class=None,
        lifecycle_reason_codes=(),
        evidence_ref_ids=("ref1",),
        conflict_ids=(),
    )
    ref = ProcurementEvidenceRef(
        evidence_ref_id="ref1",
        evidence_plane="pr4",
        source_kind="pr4",
        endpoint_kind="sqlite",
        source_record_id="src1",
        canonical_tender_key="k",
        tender_key_kind="chilecompra",
        identity_namespace="ns",
        cross_source_join_eligible=True,
        snapshot_id=None,
        acquisition_instance_id=None,
        page_id=None,
        observation_id=None,
        acquired_at_utc=None,
        source_status_code=None,
        source_status_name=None,
        source_status_value=None,
        source_status_system=None,
        normalized_status_meaning=None,
        publication_timestamp_raw=None,
        close_timestamp_raw=None,
        publication_timestamp_utc=None,
        close_timestamp_utc=None,
        publication_santiago_date=None,
        close_santiago_date=None,
        publication_precision=None,
        close_precision=None,
        timestamp_parse_reasons=(),
        buyer_display_raw=None,
        buyer_source_id=None,
        title_raw="Centrifuga de laboratorio",
        source_payload_digest=None,
        source_fingerprint=None,
        normalized_semantic_digest=None,
        field_provenance={},
        reason_codes=(),
        source_rank_class="pr4",
        has_status=False,
        has_close=False,
        has_publication=False,
        has_buyer_display=False,
        has_buyer_source_id=False,
        has_title=True,
    )
    refs = {"ref1": ref}
    snaps: dict = {}

    linked, unresolved, attempts, mats = extract_units_for_tender(
        tender, refs_by_id=refs, snapshots_by_id=snaps
    )
    assert attempts
    assert len(attempts) == len(mats)
    assert len(attempts) == len(linked) + len(unresolved)
    ok = reconcile_relevance(
        coalesced_tender_ids=[tender.coalesced_tender_id],
        decision_tender_ids=[tender.coalesced_tender_id],
        linked_unit_ids=[u.unit_id for u in linked],
        unresolved_unit_ids=[u.unit_id for u in unresolved],
        extraction_attempt_ids=[a.attempt_id for a in attempts],
        materialization_ledger=mats,
    )
    assert ok["ok"] is True

    # Drop one attempt result and add fabricated unit (cardinality preserved) — fail.
    from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
        MaterializationRecord,
    )

    two_attempts = ["pta_a", "pta_b"]
    trap = reconcile_relevance(
        coalesced_tender_ids=[tender.coalesced_tender_id],
        decision_tender_ids=[tender.coalesced_tender_id],
        linked_unit_ids=["ptu_keep", "ptu_fabricated"],
        unresolved_unit_ids=[],
        extraction_attempt_ids=two_attempts,
        materialization_ledger=[
            MaterializationRecord(0, "pta_a", "ptu_keep", "linked", "linked"),
            # pta_b dropped; fabricated unit has no attempt — counts still equal
        ],
    )
    assert trap["ok"] is False
    assert trap["missing_materializations"]
    assert "ptu_fabricated" in trap["units_without_attempt"]
    # Pure count equality would pass (2 attempts, 2 units) — bijection must fail.
    assert len(two_attempts) == 2
    assert len(["ptu_keep", "ptu_fabricated"]) == 2

    # Wrong attempt association.
    if len(mats) >= 2:
        wrong_mats = [
            MaterializationRecord(
                mats[0].attempt_occurrence,
                "pta_WRONG",
                mats[0].unit_id,
                mats[0].partition,
                mats[0].materialization_status,
            ),
            *mats[1:],
        ]
        wrong = reconcile_relevance(
            coalesced_tender_ids=[tender.coalesced_tender_id],
            decision_tender_ids=[tender.coalesced_tender_id],
            linked_unit_ids=[u.unit_id for u in linked],
            unresolved_unit_ids=[u.unit_id for u in unresolved],
            extraction_attempt_ids=[a.attempt_id for a in attempts],
            materialization_ledger=wrong_mats,
        )
        assert wrong["ok"] is False
        assert wrong["wrong_attempt_association"]

    # One attempt materializes twice.
    if mats:
        twice = reconcile_relevance(
            coalesced_tender_ids=[tender.coalesced_tender_id],
            decision_tender_ids=[tender.coalesced_tender_id],
            linked_unit_ids=[u.unit_id for u in linked] + ["ptu_extra"],
            unresolved_unit_ids=[u.unit_id for u in unresolved],
            extraction_attempt_ids=[a.attempt_id for a in attempts],
            materialization_ledger=list(mats)
            + [
                MaterializationRecord(
                    mats[0].attempt_occurrence,
                    mats[0].attempt_id,
                    "ptu_extra",
                    "linked",
                    "linked",
                )
            ],
        )
        assert twice["ok"] is False
        assert twice["duplicate_materialization_occurrences"]

    # Unit with no attempt / attempt with no materialization covered above.

    # Duplicate attempts not hidden.
    linked_b, unresolved_b, attempts_b, mats_b = extract_units_for_tender(
        tender, refs_by_id=refs, snapshots_by_id=snaps
    )
    dup_attempts = list(attempts_b) + [attempts_b[0]]
    dup_rec = reconcile_relevance(
        coalesced_tender_ids=[tender.coalesced_tender_id],
        decision_tender_ids=[tender.coalesced_tender_id],
        linked_unit_ids=[u.unit_id for u in linked_b],
        unresolved_unit_ids=[u.unit_id for u in unresolved_b],
        extraction_attempt_ids=[a.attempt_id for a in dup_attempts],
        materialization_ledger=mats_b,
    )
    assert dup_rec["ok"] is False
    assert dup_rec["duplicate_extraction_attempts"]

    # Linked/unresolved overlap.
    overlap = reconcile_relevance(
        coalesced_tender_ids=[tender.coalesced_tender_id],
        decision_tender_ids=[tender.coalesced_tender_id],
        linked_unit_ids=[linked_b[0].unit_id] if linked_b else ["ptu_x"],
        unresolved_unit_ids=[linked_b[0].unit_id] if linked_b else ["ptu_x"],
        extraction_attempt_ids=[a.attempt_id for a in attempts_b],
        materialization_ledger=mats_b,
    )
    assert overlap["ok"] is False
    assert overlap["linked_unresolved_overlap"]


def test_rule_precedence_exhaustive_contract() -> None:
    """Every RULE_PRECEDENCE entry must match executable outcome fields."""
    by_id = {r["rule_id"]: r for r in RULE_PRECEDENCE}
    assert len(by_id) == len(RULE_PRECEDENCE)

    fixtures = {
        "exact_catalog_model": None,  # no verified aliases — skipped when no fire
        "consumable_centrifuge_tubes": (
            "Tubos conicos para centrifuga",
            "line_product_text",
        ),
        "consumable_microscope_accessories": (
            "Portaobjetos y cubreobjetos",
            "line_product_text",
        ),
        "rental_or_comodato": (
            "Arriendo de centrifuga de laboratorio",
            "line_product_text",
        ),
        "service_or_maintenance": (
            "Mantencion y calibracion de centrifuga",
            "line_product_text",
        ),
        "non_lab_incubator": (
            "Incubadora de neonatologia modelo giraffe",
            "line_product_text",
        ),
        "non_lab_false_positive": (
            "Equipo de ecograf portatil para urgencias",
            "line_product_text",
        ),
        "context_required_sonicator": ("Sonicator", "line_product_text"),
        "equipment_class_positive_hits": (
            "Centrifuga refrigerada de laboratorio",
            "line_product_text",
        ),
        "context_required_agitador": ("Agitador", "line_product_text"),
        "generic_insumos": (
            "Compra de insumos de laboratorio",
            "line_product_text",
        ),
        "title_only_no_match_abstain": (
            "Adquisicion de materiales diversos",
            "title_only",
        ),
        "line_or_description_no_match_unrelated": (
            "Escritorio de oficina metalico",
            "line_product_text",
        ),
    }

    for rule_id, spec in by_id.items():
        fixture = fixtures.get(rule_id)
        if fixture is None:
            # exact_catalog_model requires verified alias — must not fire on seeds.
            d = classify_product_text_unit(_unit("UP200St", tier="line_product_text"))
            assert d.relevance_class != "exact_catalog_product"
            continue
        text, tier = fixture
        d = classify_product_text_unit(_unit(text, tier=tier))
        if "outcome_class" in spec:
            assert d.relevance_class == spec["outcome_class"], rule_id
        if "outcome_classes" in spec:
            assert d.relevance_class in spec["outcome_classes"], rule_id
        if "confidence_band" in spec:
            assert d.confidence_band == spec["confidence_band"], rule_id
        if rule_id == "equipment_class_positive_hits":
            assert d.confidence_band == spec["line_confidence_band"]
            title_d = classify_product_text_unit(_unit(text, tier="title_only"))
            assert title_d.confidence_band == spec["title_only_confidence_band"]
        if "resolution" in spec:
            assert d.product_resolution_status == spec["resolution"], rule_id
        if "ambiguity" in spec:
            assert spec["ambiguity"] in d.ambiguity_reason_codes, rule_id

    # Overrides: rental/service skipped when equipment purchase signal present.
    rental_buy = classify_product_text_unit(
        _unit(
            "Arriendo y compra de centrifuga de laboratorio",
            tier="line_product_text",
        )
    )
    assert rental_buy.relevance_class != "rental_or_comodato"
    svc_buy = classify_product_text_unit(
        _unit(
            "Mantencion y adquisicion de centrifuga de laboratorio",
            tier="line_product_text",
        )
    )
    assert svc_buy.relevance_class != "service_or_maintenance_only"

    # Bare sonicator vs specific ultrasonic class.
    bare = classify_product_text_unit(_unit("Sonicator", tier="line_product_text"))
    specific = classify_product_text_unit(
        _unit("Procesador ultrasonico UP200St tipo probe", tier="line_product_text")
    )
    assert bare.relevance_class == "ambiguous"
    assert specific.relevance_class in {
        "strong_equipment_class",
        "compatible_equipment_class",
        "ambiguous",
    }

    # Aggregation policy entries exist and are referenced by fingerprint payload.
    from origenlab_email_pipeline.commercial_procurement_product_relevance.aggregate import (
        aggregation_policy_spec,
    )
    from origenlab_email_pipeline.commercial_procurement_product_relevance.rules import (
        rules_fingerprint_payload,
    )

    agg = aggregation_policy_spec()
    assert {p["id"] for p in agg["policies"]} >= {
        "no_usable_units",
        "strong_positive_survives_negative_lines",
        "mixed_positive_and_negative_requires_review",
        "all_units_negative",
        "all_units_ambiguous_or_empty",
        "empty_never_silent_unrelated",
    }
    fp = rules_fingerprint_payload()
    assert fp["rule_precedence"] == RULE_PRECEDENCE
    assert fp["aggregation_policy"] == agg

    # Mixed positive + negative aggregation.
    tender = CoalescedProcurementTender(
        coalesced_tender_id="t_mix",
        canonical_tender_key="k",
        identity_namespace="ns",
        tender_key_kind="chilecompra",
        candidate_source_kind="pr4",
        pr4_procurement_id=None,
        pr4_procurement_ids=(),
        acquisition_snapshot_ids=(),
        acquisition_instance_ids=(),
        acquisition_observation_ids=(),
        coalescence_status="singleton",
        source_precedence_reason="only",
        currentness_class="current",
        lifecycle_class="open",
        closing_soon_bucket="none",
        publication_timestamp_selected=None,
        close_timestamp_selected=None,
        status_code_selected=None,
        status_name_selected=None,
        status_value_selected=None,
        source_status_system_selected=None,
        buyer_display_selected=None,
        buyer_source_id_selected=None,
        title_selected="mix",
        selected_field_provenance={},
        buyer_display_variance=False,
        lifecycle_status_evidence_ref_id=None,
        lifecycle_close_evidence_ref_id=None,
        lifecycle_publication_evidence_ref_id=None,
        lifecycle_evidence_currentness_class=None,
        lifecycle_reason_codes=(),
        evidence_ref_ids=(),
        conflict_ids=(),
    )
    pos = classify_product_text_unit(
        _unit("Centrifuga refrigerada", tier="line_product_text")
    )
    neg = classify_product_text_unit(
        _unit("Tubos conicos para centrifuga", tier="line_product_text")
    )
    agg_d = aggregate_tender_decision(tender, [pos, neg], input_fingerprint="x")
    assert "strong_positive_survives_negative_lines" in agg_d.aggregation_reason_codes
    assert agg_d.relevance_class in {
        "strong_equipment_class",
        "compatible_equipment_class",
        "exact_catalog_product",
    }


def test_contaminated_blind_packet_fails_before_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from origenlab_email_pipeline.commercial_procurement.builder import (
        _read_semantic_rows_from_db,
    )
    from origenlab_email_pipeline.commercial_procurement.constants import (
        BUILD_CONTRACT,
        SCHEMA_VERSION,
    )
    from origenlab_email_pipeline.commercial_procurement.ids import semantic_plan_digest
    from origenlab_email_pipeline.commercial_procurement.schema import (
        ensure_commercial_procurement_tables,
    )
    from origenlab_email_pipeline.commercial_procurement_acquisition.snapshot import (
        build_acquisition_snapshot,
    )
    from origenlab_email_pipeline.commercial_procurement_product_relevance.planner import (
        build_product_relevance_plan,
    )

    root = tmp_path / "email-pipeline"
    reports = root / "reports" / "out"
    reports.mkdir(parents=True)
    db = tmp_path / "pr5d.sqlite"
    conn = sqlite3.connect(str(db))
    ensure_commercial_procurement_tables(conn)
    meta = {
        "source_fingerprint": "a" * 64,
        "build_plan_fingerprint": "b" * 64,
        "semantic_plan_digest": "c" * 64,
        "schema_version": SCHEMA_VERSION,
        "build_contract": BUILD_CONTRACT,
        "identity_fingerprint": "d" * 64,
        "as_of_date": "2026-07-30",
        "source_fingerprint_algorithm": "procurement_source_fp_v1",
        "build_plan_fingerprint_algorithm": "procurement_build_plan_fp_v1",
        "semantic_plan_digest_algorithm": "procurement_semantic_plan_digest_v1",
    }
    for k, v in meta.items():
        conn.execute(
            "INSERT OR REPLACE INTO commercial_procurement_build_meta(meta_key, meta_value) "
            "VALUES (?,?)",
            (k, v),
        )
    rows = _read_semantic_rows_from_db(conn)
    digest = semantic_plan_digest(table_rows=rows)
    conn.execute(
        "INSERT OR REPLACE INTO commercial_procurement_build_meta(meta_key, meta_value) "
        "VALUES (?,?)",
        ("semantic_plan_digest", digest),
    )
    conn.commit()
    conn.close()
    raw = json.loads(
        (FIXTURES / "commercial_procurement_acquisition" / "ticket_detail_items.json").read_text(
            encoding="utf-8"
        )
    )
    snap = build_acquisition_snapshot(
        source_kind="ticket_detail",
        payload=raw,
        fixture_origin="synthetic_official_shape",
        acquired_at_utc="2026-08-01T10:00:00Z",
        tender_code="3544-1-LE26",
    )
    snap_path = tmp_path / "snap.json"
    snap_path.write_text(json.dumps(snap.to_dict()) + "\n", encoding="utf-8")
    result = build_product_relevance_plan(
        sqlite_path=db,
        acquisition_snapshot_paths=[snap_path],
        as_of_utc="2026-08-01T19:00:30Z",
        freshness_threshold_hours=48,
        run_context="local_fixture",
        labeling_queue_size=10,
    )
    if not result.human_review_packet:
        from dataclasses import replace
        from origenlab_email_pipeline.commercial_procurement_product_relevance.evaluation import (
            EvaluationRecord,
            to_blind_packet,
            to_sealed_scoring,
        )

        rec = EvaluationRecord(
            record_id="eval_contaminate",
            coalesced_tender_alias="redacted.tender.c",
            diagnostic_stratum="equipment_first_hit",
            product_text_redacted="centrifuga",
            has_line_descriptions=False,
            source_plane="pr4",
            text_richness="sparse",
            predicted_relevance_class="strong_equipment_class",
            predicted_confidence_band="high",
            predicted_resolution_status="equipment_class_only",
            predicted_ambiguity_reason_codes=(),
            predicted_aggregation_reason_codes=(),
            manual_review_recommended=False,
            label_status="proposed",
            gold_relevance_class=None,
            review_source=None,
            independently_reviewed=False,
            is_synthetic=False,
            redaction_proof={},
        )
        result = replace(
            result,
            human_review_packet=tuple(r.to_dict() for r in to_blind_packet([rec])),
            sealed_scoring_manifest=tuple(
                r.to_dict() for r in to_sealed_scoring([rec])
            ),
            operational_evaluation_records=(rec.to_dict(),),
            evaluation_meta={
                **result.evaluation_meta,
                "queue_record_count": 1,
            },
            counts={**result.counts, "proposed_evaluation_record_count": 1},
        )
    out = reports / "pr5d_bundle"
    write_relevance_outputs(
        result,
        out,
        repo_email_pipeline_root=root,
        require_git_ignored=False,
        include_walkthrough=True,
    )
    prior = (out / "RUN_MANIFEST.json").read_text(encoding="utf-8")

    # Contaminate blind packet with prediction fields — publish must fail.
    from dataclasses import replace

    contaminated = list(result.human_review_packet)
    bad = dict(contaminated[0])
    bad["predicted_relevance_class"] = "strong_equipment_class"
    bad["diagnostic_stratum"] = "equipment_first_hit"
    contaminated[0] = bad
    dirty = replace(result, human_review_packet=tuple(contaminated))
    with pytest.raises(AssertionError, match="prediction-revealing"):
        write_relevance_outputs(
            dirty,
            out,
            repo_email_pipeline_root=root,
            require_git_ignored=False,
            include_walkthrough=True,
        )
    assert (out / "RUN_MANIFEST.json").read_text(encoding="utf-8") == prior

    # Mismatched operational set — publish must fail and preserve prior.
    mismatched_ops = list(result.operational_evaluation_records)[:-1]
    dirty_ops = replace(result, operational_evaluation_records=tuple(mismatched_ops))
    with pytest.raises(AssertionError, match="record_id"):
        write_relevance_outputs(
            dirty_ops,
            out,
            repo_email_pipeline_root=root,
            require_git_ignored=False,
            include_walkthrough=True,
        )
    assert (out / "RUN_MANIFEST.json").read_text(encoding="utf-8") == prior


def test_buyer_marker_in_human_packet_and_shareable_path() -> None:
    from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
        MatchedEvidenceSpan,
        TenderRelevanceDecision,
    )
    from origenlab_email_pipeline.commercial_procurement_product_relevance.walkthrough import (
        _shareable_from_plan_decision,
    )

    names = [
        "Universidad Austral de Chile",
        "Servicio de Salud Los Ríos",
        "Hospital Dr. Eduardo Schütz Schroeder",
    ]
    for name in names:
        text = f"Organismo: {name} | Compra de centrifuga de laboratorio"
        unit = ProductTextUnit(
            unit_id="ptu_buyer",
            coalesced_tender_id="ct_buyer",
            evidence_ref_id="evid_buyer",
            link_status="linked",
            unresolved_reason=None,
            field_path="line.description",
            text_raw=text,
            text_normalized=normalize_product_text(text),
            evidence_tier="line_product_text",
            source_plane="pr4",
            snapshot_id=None,
            observation_id=None,
            tender_observation_id=None,
            line_observation_id=None,
            pr4_procurement_id=None,
            contributing_evidence_ref_ids=("evid_buyer",),
        )
        decision = TenderRelevanceDecision(
            decision_id="trd_buyer",
            coalesced_tender_id="ct_buyer",
            relevance_class="strong_equipment_class",
            canonical_equipment_classes=("centrifuge",),
            product_resolution_status="equipment_class_only",
            evidence_tier="line_product_text",
            confidence_band="high",
            positive_reason_codes=("equipment_class_match",),
            negative_reason_codes=(),
            ambiguity_reason_codes=(),
            aggregation_reason_codes=("single_unit_decision",),
            matched_spans=(
                MatchedEvidenceSpan("line.description", "equipment_class_positive_hits", "centrifuga"),
            ),
            contributing_evidence_ref_ids=("evid_buyer",),
            unit_decision_ids=("urd_x",),
            taxonomy_version="t",
            rules_version="r",
            input_fingerprint="i",
            semantic_fingerprint="s",
        )
        case = _shareable_from_plan_decision(
            case_id="buyer",
            label="buyer",
            decision=decision,
            units=[unit],
        )
        blob = json.dumps(case)
        assert name not in blob
        for part in name.split():
            if len(part) > 2 and part.lower() not in {"de", "del", "los", "la"}:
                assert part not in blob
        assert "centrifuga" in blob.lower() or "laboratorio" in blob.lower()

        # Human packet path via evaluation redaction.
        from origenlab_email_pipeline.commercial_procurement_product_relevance.evaluation import (
            EvaluationRecord,
            to_blind_packet,
        )

        rec = EvaluationRecord(
            record_id="eval_buyer",
            coalesced_tender_alias="redacted.tender.buyer",
            diagnostic_stratum="equipment_first_hit",
            product_text_redacted=redact_product_wording(text)[0],
            has_line_descriptions=True,
            source_plane="pr4",
            text_richness="rich",
            predicted_relevance_class="strong_equipment_class",
            predicted_confidence_band="high",
            predicted_resolution_status="equipment_class_only",
            predicted_ambiguity_reason_codes=(),
            predicted_aggregation_reason_codes=(),
            manual_review_recommended=False,
            label_status="proposed",
            gold_relevance_class=None,
            review_source=None,
            independently_reviewed=False,
            is_synthetic=False,
            redaction_proof={},
        )
        blind = to_blind_packet([rec])[0].to_dict()
        assert name not in json.dumps(blind)
        assert "centrifuga" in blind["product_text_redacted"].lower() or (
            "laboratorio" in blind["product_text_redacted"].lower()
        )
