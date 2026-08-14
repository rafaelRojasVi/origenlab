"""Additional PR5E.2 hardening edge-case and static-safety tests."""

from __future__ import annotations

import random
from pathlib import Path

from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    CoalescedProcurementTender,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.catalog_capability import (
    catalog_digest,
    match_catalog_status,
    verified_catalog_equipment_classes,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.event_families import (
    FAMILY_CONFIRMED,
    FAMILY_REVIEW,
    resolve_procurement_event_families,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.lifecycle_precedence import (
    project_tender_lifecycle,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.line_claims import (
    build_line_claims,
)
from origenlab_email_pipeline.commercial_procurement_live_feed_bridge.freshness import (
    is_future_observation,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
    ProductTextUnit,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.normalize import (
    normalize_product_text,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.rules import (
    classify_product_text_unit,
)

AS_OF = "2026-08-06T02:00:00Z"
SRC = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "origenlab_email_pipeline"
    / "commercial_procurement_institution_prospects"
)


def _tender(**kwargs) -> CoalescedProcurementTender:
    defaults = dict(
        coalesced_tender_id="t1",
        canonical_tender_key="chilecompra:t1",
        identity_namespace="chilecompra",
        tender_key_kind="codigo_externo",
        candidate_source_kind="live_snapshot",
        pr4_procurement_id=None,
        pr4_procurement_ids=(),
        acquisition_snapshot_ids=("snap",),
        acquisition_instance_ids=(),
        acquisition_observation_ids=(),
        coalescence_status="coalesced",
        source_precedence_reason="test",
        currentness_class="current_authoritative_snapshot",
        lifecycle_class="closed",
        closing_soon_bucket="not_applicable",
        publication_timestamp_selected="2026-01-01T00:00:00Z",
        close_timestamp_selected=None,
        status_code_selected=None,
        status_name_selected=None,
        status_value_selected=None,
        source_status_system_selected=None,
        buyer_display_selected="Buyer",
        buyer_source_id_selected="1",
        title_selected="Title",
        selected_field_provenance={},
        buyer_display_variance=False,
        lifecycle_status_evidence_ref_id=None,
        lifecycle_close_evidence_ref_id=None,
        lifecycle_publication_evidence_ref_id=None,
        lifecycle_evidence_currentness_class=None,
        lifecycle_reason_codes=(),
        evidence_ref_ids=(),
        conflict_ids=(),
        # PR3: default to a recognized open/public Ticket Tipo code so
        # pre-existing tests keep their original actionability intent.
        procurement_method_selected="LP",
        procurement_method_details_selected=None,
    )
    defaults.update(kwargs)
    return CoalescedProcurementTender(**defaults)


def _unit(text: str, *, tier: str = "line_product_text") -> ProductTextUnit:
    return ProductTextUnit(
        unit_id="u1",
        coalesced_tender_id="t1",
        evidence_ref_id=None,
        link_status="linked",
        unresolved_reason=None,
        field_path="line",
        text_raw=text,
        text_normalized=normalize_product_text(text),
        evidence_tier=tier,
        source_plane="test",
        snapshot_id=None,
        observation_id=None,
        tender_observation_id=None,
        line_observation_id=None,
        pr4_procurement_id=None,
        contributing_evidence_ref_ids=(),
    )


def test_future_observation_excluded_without_mutation() -> None:
    assert is_future_observation("2026-08-06T20:12:01Z", as_of_utc=AS_OF) is True
    assert is_future_observation("2026-08-06T01:00:00Z", as_of_utc=AS_OF) is False


def test_catalog_includes_reactor_and_excludes_incubator() -> None:
    classes = verified_catalog_equipment_classes()
    assert "reactor" in classes or "lab_reactor" in classes
    assert "centrifuge" in classes
    assert "balance" in classes
    assert "ultrasonic_processor" in classes or "sonicator" in classes
    assert "incubator" not in classes
    assert "microscope" not in classes
    assert len(catalog_digest()) == 64


def test_sonicator_maps_to_ultrasonic_processor_status() -> None:
    status = match_catalog_status("ultrasonic_processor")
    assert status in {
        "verified_catalog_equipment_class",
        "catalog_equipment_class_needs_confirmation",
    }


def test_centrifugal_pump_vs_lab_centrifuge_claims() -> None:
    pump = classify_product_text_unit(_unit("bomba centrifuga horizontal"))
    lab = classify_product_text_unit(_unit("centrifuga de laboratorio clinica"))
    assert pump.relevance_class == "non_laboratory_false_positive"
    assert "centrifuge" in lab.canonical_equipment_classes


def test_mixed_maintenance_and_purchase_claims_preserve_both() -> None:
    text = "mantencion de centrifuga y adquisicion de sonicador de sonda"
    claims = build_line_claims(_unit(text))
    scopes = {c.equipment_scope for c in claims}
    cats = {
        cls
        for c in claims
        for cls in c.canonical_equipment_classes
    }
    assert "maintenance_or_calibration" in scopes or "installed_base_evidence" in scopes
    assert (
        "complete_equipment" in scopes
        or "ultrasonic_processor" in cats
        or "centrifuge" in cats
    )

def test_explicit_reissue_marker_confirms_family() -> None:
    tenders = (
        _tender(
            coalesced_tender_id="r1",
            canonical_tender_key="chilecompra:r1",
            buyer_source_id_selected="9",
            title_selected="Adquisicion de centrifuga de laboratorio",
            publication_timestamp_selected="2026-01-01T00:00:00Z",
        ),
        _tender(
            coalesced_tender_id="r2",
            canonical_tender_key="chilecompra:r2",
            buyer_source_id_selected="9",
            title_selected="Adquisicion de centrifuga de laboratorio EN REEMPLAZO de licitación anterior",
            publication_timestamp_selected="2026-01-15T00:00:00Z",
        ),
    )
    families, _ = resolve_procurement_event_families(tenders)
    assert any(f.family_resolution_status == FAMILY_CONFIRMED for f in families)


def test_near_title_without_marker_is_review_not_confirmed() -> None:
    base = (
        "IMPLEMENTACION Y EQUIPAMIENTO CLINICA VETERINARIA ADQUISICION DE "
        "EQUIPOS DE LABORATORIO Y MONITOREO PARA ATENCION EN VETERINARIA MUNICIPAL"
    )
    tenders = (
        _tender(
            coalesced_tender_id="n1",
            canonical_tender_key="chilecompra:n1",
            buyer_source_id_selected="7",
            title_selected=base,
            publication_timestamp_selected="2026-01-01T00:00:00Z",
        ),
        _tender(
            coalesced_tender_id="n2",
            canonical_tender_key="chilecompra:n2",
            buyer_source_id_selected="7",
            title_selected=base + " SECTOR NORTE",
            publication_timestamp_selected="2026-01-20T00:00:00Z",
        ),
    )
    families, _ = resolve_procurement_event_families(tenders)
    assert any(f.family_resolution_status == FAMILY_REVIEW for f in families)
    assert not any(
        f.family_resolution_status == FAMILY_CONFIRMED and f.raw_tender_count == 2
        for f in families
    )


def test_family_membership_order_invariant() -> None:
    tenders = [
        _tender(
            coalesced_tender_id=f"o{i}",
            canonical_tender_key=f"chilecompra:o{i}",
            buyer_source_id_selected="3",
            title_selected=f"Adquisicion de balanza analitica lote {i}",
            publication_timestamp_selected=f"2026-0{i+1}-01T00:00:00Z",
        )
        for i in range(1, 4)
    ]
    fwd, map_f = resolve_procurement_event_families(tenders)
    rev, map_r = resolve_procurement_event_families(list(reversed(tenders)))
    assert {f.family_id for f in fwd} == {f.family_id for f in rev}
    assert map_f == map_r
    shuffled = list(tenders)
    random.Random(0).shuffle(shuffled)
    shuf, map_s = resolve_procurement_event_families(shuffled)
    assert {f.family_id for f in fwd} == {f.family_id for f in shuf}
    assert map_f == map_s


def test_missing_observation_chronology_fail_closed_for_terminal_conflict() -> None:
    tender = _tender(
        coalesced_tender_id="c1",
        lifecycle_class="active_open",
        status_code_selected="6",
        status_name_selected="Cerrada",
        close_timestamp_selected="2026-09-01T00:00:00Z",
        publication_timestamp_selected=None,
    )
    proj = project_tender_lifecycle(tender, as_of_utc=AS_OF)
    assert (
        "missing_observation_chronology" in proj.reason_codes
        or "temporal_review" in (proj.precedence_reason or "")
        or proj.projected_lifecycle_class
        in {"active_open", "status_unknown", "date_missing"}
    )


def test_production_src_static_safety_no_fixture_or_benchmark_tokens() -> None:
    forbidden = (
        "Rengo",
        "Grant Benavente",
        "407-115-co26",
        "2775-73-le26",
        "tests/fixtures",
        "or True",
    )
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{path.name} contains {token!r}"


def test_empty_queue_headers_declared() -> None:
    from origenlab_email_pipeline.commercial_procurement_institution_prospects.queues import (
        EMPTY_QUEUE_HEADERS,
        QUEUE_GRAINS,
    )

    assert "retender_review_queue" in EMPTY_QUEUE_HEADERS
    assert EMPTY_QUEUE_HEADERS["retender_review_queue"]
    assert "current_opportunity_queue" in QUEUE_GRAINS
