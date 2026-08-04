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
    compute_evaluation_metrics,
    redact_product_wording,
    stable_sample_key,
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
    classify_product_text_unit,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.taxonomy_extensions import (
    PROPOSED_CATALOG_ALIASES,
    validate_pr5d_taxonomy,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.walkthrough import (
    build_cases_a_e,
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
    assert result.reconciliation["equations"]["units_eq_linked_plus_unresolved"] is True
