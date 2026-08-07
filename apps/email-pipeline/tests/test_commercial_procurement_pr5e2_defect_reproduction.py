"""Defect-reproduction tests for PR5E.2 recognition contract gaps.

These tests are expected to FAIL against commit 89b6a8a and PASS after hardening.
They must not weaken existing assertions merely to preserve the first implementation.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    CoalescedProcurementTender,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects import (
    adjudication as adjudication_mod,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects import (
    event_families as event_families_mod,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.adjudication import (
    adjudicate_public_evidence,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.catalog_scope import (
    VERIFIED_CATALOG_CLASSES,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.event_families import (
    FAMILY_REVIEW,
    normalize_procurement_object,
    resolve_procurement_event_families,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.lifecycle_precedence import (
    project_tender_lifecycle,
)
from origenlab_email_pipeline.commercial_procurement_live_feed_bridge.freshness import (
    resolve_acquired_at_utc,
)

SRC_ROOT = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "origenlab_email_pipeline"
    / "commercial_procurement_institution_prospects"
)
AS_OF = "2026-08-06T02:00:00Z"


def _tender(
    *,
    tid: str,
    buyer_name: str = "Buyer",
    buyer_id: str = "1",
    title: str = "Adquisicion de equipos",
    lifecycle: str = "closed",
    status_code: str | None = None,
    status_name: str | None = None,
    close_ts: str | None = None,
    pub: str | None = "2026-01-01T00:00:00Z",
) -> CoalescedProcurementTender:
    return CoalescedProcurementTender(
        coalesced_tender_id=tid,
        canonical_tender_key=f"chilecompra:{tid}",
        identity_namespace="chilecompra",
        tender_key_kind="codigo_externo",
        candidate_source_kind="live_snapshot",
        pr4_procurement_id=None,
        pr4_procurement_ids=(),
        acquisition_snapshot_ids=("snap-1",),
        acquisition_instance_ids=(),
        acquisition_observation_ids=(),
        coalescence_status="coalesced",
        source_precedence_reason="test",
        currentness_class="current_authoritative_snapshot",
        lifecycle_class=lifecycle,
        closing_soon_bucket="not_applicable",
        publication_timestamp_selected=pub,
        close_timestamp_selected=close_ts,
        status_code_selected=status_code,
        status_name_selected=status_name,
        status_value_selected=None,
        source_status_system_selected=None,
        buyer_display_selected=buyer_name,
        buyer_source_id_selected=buyer_id,
        title_selected=title,
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


# --- A. Temporal provenance must not be rewritten ----------------------------


def test_defect_acquired_at_must_not_be_clamped_to_as_of() -> None:
    """Acquisition timestamps are immutable observed facts."""
    manifest = {"generated_at_utc": "2026-08-06T20:12:01Z"}
    resolved = resolve_acquired_at_utc(
        manifest=manifest, refresh_state=None, as_of_utc=AS_OF
    )
    assert resolved == "2026-08-06T20:12:01Z", (
        "must not rewrite post-as_of acquisition stamps to evaluation as_of"
    )


def test_defect_freshness_source_must_not_contain_clamp_branch() -> None:
    src = inspect.getsource(resolve_acquired_at_utc)
    assert "dt > as_of" not in src and "clamped" not in src.lower()


# --- B. Retender review must be reachable ------------------------------------


def test_defect_near_match_retender_review_must_be_reachable() -> None:
    """Near-identical titles without exact fingerprint must yield review families."""
    tenders = (
        _tender(
            tid="A1",
            buyer_id="100",
            title=(
                "IMPLEMENTACION Y EQUIPAMIENTO CLINICA VETERINARIA ADQUISICION DE "
                "EQUIPOS DE LABORATORIO Y MONITOREO PARA ATENCION EN VETERINARIA "
                "MUNICIPAL"
            ),
        ),
        _tender(
            tid="A2",
            buyer_id="100",
            title=(
                "IMPLEMENTACION Y EQUIPAMIENTO CLINICA VETERINARIA ADQUISICION DE "
                "EQUIPOS DE LABORATORIO Y MONITOREO PARA ATENCION EN VETERINARIA "
                "MUNICIPAL SECTOR NORTE"
            ),
        ),
    )
    families, _ = resolve_procurement_event_families(tenders)
    assert any(f.family_resolution_status == FAMILY_REVIEW for f in families), (
        "near-match path is unreachable when singletons claim tender_to_family first"
    )


# --- C. Event-family over-aggression / institution-specific rules ------------


def test_defect_exact_title_one_year_apart_must_not_auto_confirm_same_event() -> None:
    tenders = (
        _tender(
            tid="Y1",
            buyer_id="50",
            title="Adquisicion de centrifuga de laboratorio",
            pub="2025-03-01T00:00:00Z",
        ),
        _tender(
            tid="Y2",
            buyer_id="50",
            title="Adquisicion de centrifuga de laboratorio",
            pub="2026-03-01T00:00:00Z",
        ),
    )
    families, _ = resolve_procurement_event_families(tenders)
    confirmed = [
        f
        for f in families
        if f.family_resolution_status == "confirmed_family" and f.raw_tender_count == 2
    ]
    assert not confirmed, (
        "exact buyer+title alone must not collapse annual procurements into one event"
    )


def test_defect_no_rengo_specific_normalization_in_production() -> None:
    assert "rengo" not in inspect.getsource(normalize_procurement_object).lower()
    src = (SRC_ROOT / "event_families.py").read_text(encoding="utf-8")
    assert re.search(r"\brengo\b", src, re.I) is None


def test_defect_production_src_has_no_benchmark_institution_or_tender_hardcodes() -> None:
    forbidden = (
        "Rengo",
        "Grant Benavente",
        "407-115-co26",
        "407-89-le26",
        "2775-73-le26",
        "2775-92-le26",
    )
    for path in SRC_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{path.name} contains forbidden token {token!r}"


# --- D/E. Line-level architecture & reconciliation ---------------------------


def test_defect_unit_text_must_use_full_product_text_not_matched_spans() -> None:
    from origenlab_email_pipeline.commercial_procurement_institution_prospects import (
        planner as planner_mod,
    )

    src = inspect.getsource(planner_mod._unit_text)
    assert "text_raw" in src or "text_normalized" in src
    assert "matched_spans" not in src


def test_defect_maintenance_without_equipment_class_is_assigned_not_review() -> None:
    """Valid rental/maintenance/consumable signals must not auto-enter line review."""
    from origenlab_email_pipeline.commercial_procurement_institution_prospects.planner import (
        _build_line_rows,
    )
    from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
        EvidenceUnitRelevanceDecision,
        ProductRelevancePlanResult,
    )

    # Prefer asserting via helper if signature stable; otherwise scan source contract.
    src = inspect.getsource(_build_line_rows)
    assert "or not unit.canonical_equipment_classes" not in src


# --- F. Catalog must come from seed ------------------------------------------


def test_defect_verified_catalog_classes_must_derive_from_catalog_seed() -> None:
    """Hand-maintained taxonomy list must not pretend to be verified catalog capability."""
    import json

    seed_path = (
        Path(__file__).resolve().parents[1]
        / "data"
        / "catalog"
        / "catalog_seed_v1.json"
    )
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    seed_classes = {
        str(c.get("equipment_class"))
        for c in seed.get("categories") or []
        if c.get("equipment_class")
    }
    # Taxonomy-only classes that are NOT seed equipment capabilities:
    assert "incubator" not in seed_classes
    assert "microscope" not in seed_classes
    # After hardening, verified capability must not include incubator/microscope
    # unless present as seed equipment_class.
    from origenlab_email_pipeline.commercial_procurement_institution_prospects import (
        catalog_scope as catalog_scope_mod,
    )

    verified = getattr(catalog_scope_mod, "verified_catalog_equipment_classes", None)
    if verified is None:
        verified = getattr(catalog_scope_mod, "VERIFIED_CATALOG_CLASSES", frozenset())
    assert "incubator" not in verified
    assert "microscope" not in verified
    assert "reactor" in verified or "lab_reactor" in verified or any(
        "reactor" in str(x) for x in verified
    )
    assert seed_path.is_file()



# --- G. Production must not load tests/fixtures ------------------------------


def test_defect_planner_must_not_call_fixture_evaluator() -> None:
    from origenlab_email_pipeline.commercial_procurement_institution_prospects import (
        planner as planner_mod,
    )

    src = inspect.getsource(planner_mod.build_institution_prospects_from_plans)
    assert "evaluate_reviewed_adjudication_fixture" not in src


def test_defect_adjudication_must_not_use_or_true_bypass() -> None:
    src = inspect.getsource(adjudicate_public_evidence)
    assert "or True" not in src


def test_defect_src_must_not_auto_load_tests_fixtures_path() -> None:
    for path in SRC_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "tests/fixtures" not in text, f"{path} references tests/fixtures"


# --- H. Lifecycle temporal order ---------------------------------------------


def test_defect_older_terminal_must_not_override_newer_open_without_chronology() -> None:
    """Terminal override requires actual newer observation, not status alone."""
    # Open lifecycle with future close, but selected status looks closed without
    # an observation timestamp proving the terminal is newer.
    tender = _tender(
        tid="T1",
        lifecycle="active_open",
        status_code="6",
        status_name="Cerrada",
        close_ts="2026-09-01T00:00:00Z",
        pub="2026-08-01T00:00:00Z",
    )
    # Without observation chronology, fail closed to review — not invent "newer".
    proj = project_tender_lifecycle(tender, as_of_utc=AS_OF)
    assert proj.projected_lifecycle_class != "closed" or (
        "observation" in " ".join(proj.reason_codes).lower()
        or "chronolog" in (proj.precedence_reason or "").lower()
        or "review" in (proj.precedence_reason or "").lower()
    ), "terminal override without observation chronology is unsafe"
    # Stronger contract after hardening:
    assert getattr(proj, "selected_observation_at_utc", None) is not None or (
        "missing_observation_chronology" in proj.reason_codes
        or "fail_closed" in (proj.precedence_reason or "")
    )


# --- I. Queue grain / naming -------------------------------------------------


def test_defect_retender_review_csv_must_not_be_zero_byte_when_empty() -> None:
    """Empty queues must still emit deterministic headers."""
    from origenlab_email_pipeline.commercial_procurement_institution_prospects.queues import (
        build_operator_queues,
    )

    queues = build_operator_queues(
        profiles=[],
        tender_rows=[],
        line_rows=[],
        families=[],
        clusters=[],
        match_review=[],
        contact_gaps=[],
    )
    # After hardening, empty CSV writers must have declared headers for each queue.
    assert "retender_review_queue" in queues
    # Contract marker expected on queue module after fix:
    from origenlab_email_pipeline.commercial_procurement_institution_prospects import (
        queues as queues_mod,
    )

    assert hasattr(queues_mod, "QUEUE_GRAINS") or hasattr(
        queues_mod, "EMPTY_QUEUE_HEADERS"
    ), "queue module must declare grains/headers explicitly"
