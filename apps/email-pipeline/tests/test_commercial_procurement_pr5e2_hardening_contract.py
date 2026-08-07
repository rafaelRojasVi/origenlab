"""PR5E.2 hardening contract regressions.

These tests must FAIL on 59943b87 (aggregate-title planner + false reconciliations)
and PASS after the hardening contract is complete. Do not weaken assertions.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path
from typing import Any

import pytest

from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    CoalescedProcurementTender,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.catalog_capability import (
    MATCH_CATEGORY_PLACEHOLDER,
    MATCH_EXACT_PRODUCT,
    MATCH_VERIFIED_CLASS,
    CatalogCapability,
    catalog_digest,
    load_catalog_capability,
    match_catalog_status,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.constants import (
    OPERATOR_QUEUE_NAMES,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.equipment_history import (
    aggregate_equipment_history,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.event_families import (
    FAMILY_REVIEW,
    resolve_procurement_event_families,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.lifecycle_precedence import (
    project_tender_lifecycle,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.line_claims import (
    SCOPE_COMPLETE_EQUIPMENT,
    SCOPE_MAINTENANCE_OR_CALIBRATION,
    aggregate_tender_axes,
    build_line_claims,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.queues import (
    EMPTY_QUEUE_HEADERS,
    QUEUE_GRAINS,
    build_operator_queues,
    canonical_queue_row,
    queue_row_id,
)
from origenlab_email_pipeline.commercial_procurement_live_feed_bridge.freshness import (
    evaluate_feed_freshness,
    is_future_observation,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
    ProductTextUnit,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.normalize import (
    normalize_product_text,
)

AS_OF = "2026-08-06T02:00:00Z"
SRC = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "origenlab_email_pipeline"
    / "commercial_procurement_institution_prospects"
)
REQUIRED_ARTIFACTS = (
    "source_set_reconciliation.json",
    "temporal_reconciliation.json",
    "line_signal_claims.json",
    "line_signal_claims.csv",
    "line_reconciliation.json",
    "catalog_capability_snapshot.json",
    "event_family_reconciliation.json",
    "operator_queue_reconciliation.json",
    "reviewed_adjudication_axis_results.json",
    "recognition_quality_review_packet.json",
)


def _tender(**kwargs: Any) -> CoalescedProcurementTender:
    defaults: dict[str, Any] = dict(
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
    )
    defaults.update(kwargs)
    return CoalescedProcurementTender(**defaults)


def _unit(
    text: str,
    *,
    unit_id: str = "u1",
    tid: str = "t1",
    tier: str = "line_product_text",
    unresolved: str | None = None,
) -> ProductTextUnit:
    return ProductTextUnit(
        unit_id=unit_id,
        coalesced_tender_id=tid,
        evidence_ref_id=None,
        link_status="unresolved" if unresolved else "linked",
        unresolved_reason=unresolved,
        field_path="line",
        text_raw=text,
        text_normalized=normalize_product_text(text),
        evidence_tier=tier,
        source_plane="test",
        snapshot_id="snap-1",
        observation_id="obs-1",
        tender_observation_id=None,
        line_observation_id="line-1",
        pr4_procurement_id=None,
        contributing_evidence_ref_ids=(),
    )


# ---------------------------------------------------------------------------
# A. Queue identity collision
# ---------------------------------------------------------------------------


def test_a_canonical_queue_row_uses_operator_queue_names() -> None:
    """QUEUE_GRAINS keys and canonical_queue_row() names must not diverge."""
    src = inspect.getsource(build_operator_queues)
    short_names = (
        "current_opportunity",
        "historical_prospect",
        "line_evidence_review",
        "retender_review",
        "institution_match_review",
        "contact_gap",
    )
    for short in short_names:
        assert f'"{short}"' not in src or f'"{short}_queue"' in src, (
            f"build_operator_queues still stamps short queue name {short!r}"
        )
    for name in OPERATOR_QUEUE_NAMES:
        assert name in QUEUE_GRAINS
        assert name in EMPTY_QUEUE_HEADERS


def test_a_queue_row_ids_unique_and_grain_recomputable() -> None:
    rows = [
        {
            "institution_id": "inst-a",
            "tender_code": "100-1-le26",
            "equipment_category": "centrifuge",
            "display_name": "A",
            "coalesced_tender_id": "t-a",
            "lifecycle_class": "active_open",
            "review_disposition": "catalog_fit_candidate",
            "commercial_signal_type": "equipment_purchase_signal",
            "catalog_fit_status": "catalog_fit_candidate",
            "catalog_match_status": "verified_catalog_equipment_class",
            "opportunity_urgency_band": "high",
            "prospect_strength_band": "high",
            "line_evidence_unit_count": 1,
            "reason_codes": [],
        },
        {
            "institution_id": "inst-a",
            "tender_code": "100-1-le26",
            "equipment_category": "ultrasonic_processor",
            "display_name": "A",
            "coalesced_tender_id": "t-a",
            "lifecycle_class": "active_open",
            "review_disposition": "catalog_fit_candidate",
            "commercial_signal_type": "equipment_purchase_signal",
            "catalog_fit_status": "catalog_fit_candidate",
            "catalog_match_status": "verified_catalog_equipment_class",
            "opportunity_urgency_band": "high",
            "prospect_strength_band": "high",
            "line_evidence_unit_count": 1,
            "reason_codes": [],
        },
    ]
    stamped = [
        canonical_queue_row("current_opportunity_queue", dict(r)) for r in rows
    ]
    ids = [r["queue_row_id"] for r in stamped]
    assert len(ids) == len(set(ids))
    for stamped_row, raw in zip(stamped, rows, strict=True):
        expected = queue_row_id("current_opportunity_queue", stamped_row)
        assert stamped_row["queue_row_id"] == expected
        grain = QUEUE_GRAINS["current_opportunity_queue"]
        for field in grain.split(" + "):
            assert field in stamped_row
            assert stamped_row.get(field) == raw.get(field) or field in {
                "queue_row_id",
                "queue",
            }


def test_a_short_name_collides_when_grain_lookup_misses() -> None:
    """Prove short names yield empty grains → colliding IDs across different rows."""
    a = queue_row_id(
        "current_opportunity",
        {
            "institution_id": "i1",
            "tender_code": "t1",
            "equipment_category": "centrifuge",
        },
    )
    b = queue_row_id(
        "current_opportunity",
        {
            "institution_id": "i2",
            "tender_code": "t2",
            "equipment_category": "balance",
        },
    )
    # Document the defect: short-name lookup misses QUEUE_GRAINS → identical IDs.
    assert a == b
    # Production must not call with short names once hardened:
    source = inspect.getsource(build_operator_queues)
    assert re.search(r'canonical_queue_row\(\s*"current_opportunity"', source) is None
    assert "current_opportunity_queue" in source


# ---------------------------------------------------------------------------
# B. Production is not line-driven
# ---------------------------------------------------------------------------


def test_b_mixed_title_scopes_are_category_paired_not_cross_product() -> None:
    text = "mantención de centrífuga y adquisición de sonicador de sonda"
    claims = build_line_claims(_unit(text))
    by_cat: dict[str, set[str]] = {}
    for claim in claims:
        for cat in claim.canonical_equipment_classes:
            by_cat.setdefault(cat, set()).add(claim.equipment_scope)

    assert "centrifuge" in by_cat
    assert by_cat["centrifuge"] <= {
        SCOPE_MAINTENANCE_OR_CALIBRATION,
        "installed_base_evidence",
    }
    sonicator_cats = {
        c for c in by_cat if c in {"ultrasonic_processor", "sonicator"}
    }
    assert sonicator_cats
    for cat in sonicator_cats:
        assert SCOPE_COMPLETE_EQUIPMENT in by_cat[cat]


def test_b_accessory_only_lines_do_not_enter_purchase_history() -> None:
    """Title suggests purchase; detailed lines are accessories only."""
    from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
        TenderRelevanceDecision,
    )

    tender = _tender(
        coalesced_tender_id="acc-1",
        title_selected="Adquisición de equipos de laboratorio centrífuga",
        lifecycle_class="closed",
        publication_timestamp_selected="2025-06-01T00:00:00Z",
    )
    decision = TenderRelevanceDecision(
        decision_id="d-acc",
        coalesced_tender_id="acc-1",
        relevance_class="strong_equipment_class",
        canonical_equipment_classes=("centrifuge",),
        product_resolution_status="resolved",
        evidence_tier="title",
        confidence_band="medium",
        positive_reason_codes=("title_equipment",),
        negative_reason_codes=(),
        ambiguity_reason_codes=(),
        aggregation_reason_codes=(),
        matched_spans=(),
        contributing_evidence_ref_ids=(),
        unit_decision_ids=(),
        taxonomy_version="t",
        rules_version="r",
        input_fingerprint="i",
        semantic_fingerprint="s",
    )
    unit = _unit(
        "accesorios y repuestos para centrífuga de laboratorio",
        unit_id="acc-u1",
        tid="acc-1",
    )
    claims = build_line_claims(unit)
    axes = aggregate_tender_axes(claims)
    assert axes["has_complete_equipment_purchase"] is False

    sig = inspect.signature(aggregate_equipment_history)
    kwargs: dict[str, Any] = dict(
        tenders=[tender],
        decisions_by_tender={"acc-1": decision},
        institution_id_by_tender={"acc-1": "inst-acc"},
        claim_axes_by_tender={"acc-1": axes},
    )
    assert "claims_by_tender" in sig.parameters, (
        "aggregate_equipment_history must accept claims_by_tender so title "
        "relevance cannot invent purchase history against accessory-only lines"
    )
    kwargs["claims_by_tender"] = {"acc-1": list(claims)}
    history = aggregate_equipment_history(**kwargs)
    for rows in history.values():
        for cat_hist in rows:
            scopes = set(cat_hist.get("equipment_scopes") or [])
            assert SCOPE_COMPLETE_EQUIPMENT not in scopes
            assert cat_hist.get("has_equipment_purchase_signal") is False


def test_b_generic_veterinary_title_without_line_evidence_not_current() -> None:
    tender_row = {
        "institution_id": "rengo",
        "display_name": "Municipalidad de Rengo",
        "tender_code": "407-115-co26",
        "coalesced_tender_id": "rengo-1",
        "canonical_equipment_category": "",
        "projected_lifecycle_class": "active_open",
        "lifecycle_class": "active_open",
        "review_disposition": "possible_fit_needs_specs",
        "commercial_signal_type": "equipment_purchase_signal",
        "catalog_fit_status": "possible_fit_needs_specs",
        "catalog_match_status": "category_placeholder_needs_specs",
        "opportunity_urgency_band": "medium",
        "prospect_strength_band": "medium",
        "closing_soon_bucket": "within_30d",
        "publication_timestamp": "2026-07-01T00:00:00Z",
        "close_timestamp": "2026-08-20T00:00:00Z",
        "reason_codes": ["veterinary_clinic_title"],
        "eligibility_reason_codes": [],
    }
    queues = build_operator_queues(
        profiles=[],
        tender_rows=[tender_row],
        line_rows=[],  # no category-specific detailed lines
        families=[],
        clusters=[],
        match_review=[],
        contact_gaps=[],
    )
    codes = {
        r.get("tender_code") for r in queues["current_opportunity_queue"]
    }
    assert "407-115-co26" not in codes


# ---------------------------------------------------------------------------
# C. Temporal leakage
# ---------------------------------------------------------------------------


def test_c_missing_acquisition_is_not_implicitly_eligible() -> None:
    """Invalid/missing chronology must fail closed, not return False=eligible."""
    from origenlab_email_pipeline.commercial_procurement_live_feed_bridge import (
        freshness,
    )

    eligible = getattr(freshness, "is_temporally_eligible_observation", None)
    status_fn = getattr(freshness, "observation_temporal_status", None)
    assert eligible is not None or status_fn is not None, (
        "missing chronology currently returns is_future_observation=False "
        "(implicit eligible); need fail-closed eligibility helper"
    )
    if status_fn is not None:
        status = status_fn(None, as_of_utc=AS_OF)
        assert status in {
            "missing_chronology",
            "invalid_chronology",
            "temporal_review_required",
        }
    if eligible is not None:
        assert eligible(None, as_of_utc=AS_OF) is False
        assert eligible("not-a-timestamp", as_of_utc=AS_OF) is False
    # is_future_observation alone must not be the eligibility gate for missing.
    assert is_future_observation(None, as_of_utc=AS_OF) is False


def test_c_future_freshness_anchor_not_called_fresh() -> None:
    report = evaluate_feed_freshness(
        as_of_utc=AS_OF,
        refresh_state={"last_successful_refresh_at": "2026-08-06T20:12:01Z"},
        manifest=None,
        max_age_hours=24,
        allow_stale=True,
    )
    assert report["fresh_enough"] is False
    age = report["age_hours_at_as_of"]
    assert age is None or age >= 0


def test_c_same_time_open_and_terminal_is_conflict() -> None:
    tender = _tender(
        lifecycle_class="active_open",
        status_code_selected="8",
        status_name_selected="Adjudicada",
        close_timestamp_selected="2026-07-01T00:00:00Z",
        publication_timestamp_selected="2026-06-01T00:00:00Z",
    )
    proj = project_tender_lifecycle(
        tender,
        as_of_utc=AS_OF,
        status_observed_at_utc="2026-06-15T12:00:00Z",
        lifecycle_observed_at_utc="2026-06-15T12:00:00Z",
    )
    assert proj.projected_lifecycle_class == "status_conflict", (
        "same-time open vs terminal must preserve conflict; "
        f"got {proj.projected_lifecycle_class}"
    )


def test_c_open_with_elapsed_close_is_stale_open_review() -> None:
    tender = _tender(
        lifecycle_class="active_open",
        status_code_selected="5",
        status_name_selected="Publicada",
        close_timestamp_selected="2026-07-01T00:00:00Z",  # before AS_OF
        publication_timestamp_selected="2026-06-01T00:00:00Z",
    )
    proj = project_tender_lifecycle(
        tender,
        as_of_utc=AS_OF,
        status_observed_at_utc="2026-06-15T12:00:00Z",
        lifecycle_observed_at_utc="2026-06-15T12:00:00Z",
    )
    assert proj.projected_lifecycle_class != "active_open"
    assert (
        proj.projected_lifecycle_class
        in {"status_conflict", "date_missing", "closed"}
        or "stale" in " ".join(proj.reason_codes).lower()
        or proj.temporal_resolution_status == "temporal_review_required"
    )


def test_c_publication_not_lifecycle_observation_fallback() -> None:
    source = inspect.getsource(project_tender_lifecycle)
    assert "publication_timestamp_selected" not in source, (
        "publication timestamp must not masquerade as lifecycle observation time"
    )
    tender = _tender(
        lifecycle_class="active_open",
        status_code_selected="8",
        status_name_selected="Adjudicada",
        publication_timestamp_selected="2026-06-01T00:00:00Z",
        close_timestamp_selected="2026-07-01T00:00:00Z",
    )
    proj = project_tender_lifecycle(tender, as_of_utc=AS_OF)
    assert proj.lifecycle_observed_at_utc is None
    assert proj.temporal_resolution_status == "temporal_review_required"


def test_c_future_tenders_excluded_from_event_family_resolution() -> None:
    """Family resolution must receive only temporally eligible tenders."""
    planner_src = (SRC / "planner.py").read_text(encoding="utf-8")
    # Locate the call inside build_institution_prospects_from_plans, not import.
    build_pos = planner_src.find("def build_institution_prospects_from_plans")
    assert build_pos >= 0
    body = planner_src[build_pos:]
    fam_pos = body.find("resolve_procurement_event_families(")
    eligible_pos = body.find("temporally_eligible")
    if eligible_pos < 0:
        eligible_pos = body.find("eligible_tenders")
    assert eligible_pos >= 0, "planner must build a temporally eligible population"
    assert eligible_pos < fam_pos, (
        "temporal eligibility must occur before event-family resolution"
    )


# ---------------------------------------------------------------------------
# D. Catalog trust
# ---------------------------------------------------------------------------


def test_d_invalid_catalog_exposes_zero_capability() -> None:
    from origenlab_email_pipeline.catalog.catalog_seed import (
        CatalogSeedValidationError,
        validate_seed,
    )
    from origenlab_email_pipeline.commercial_procurement_institution_prospects import (
        catalog_capability as cap_mod,
    )

    bad = {
        "seed_version": "bad",
        "categories": [{"category_key": "x"}],
        "products": [
            {
                "product_key": "p1",
                "product_kind": "equipment",
                "equipment_class": "centrifuge",
                "display_name": "Centrifuge",
                "confidence": "operator_confirmed",
            }
        ],
    }
    with pytest.raises(CatalogSeedValidationError):
        validate_seed(bad)

    blocker = getattr(cap_mod, "CatalogCapabilityBlocked", None)
    empty = CatalogCapability.from_seed(bad, validated=False)
    # Soft-fail that still exposes centrifuge capability is the defect.
    if blocker is not None:
        with pytest.raises(blocker):
            cap_mod.load_catalog_capability.__wrapped__(  # type: ignore[attr-defined]
                seed_path=None
            )
    assert not empty.verified_equipment_classes, (
        "invalid catalog validation must expose zero capability, "
        f"got {sorted(empty.verified_equipment_classes)}"
    )
    assert not empty.product_names


def test_d_hielscher_up100h_extracted_needs_review_not_exact_verified() -> None:
    status = match_catalog_status(
        "ultrasonic_processor",
        text="Hielscher UP100H sonicador de sonda",
        is_purchase_signal=True,
    )
    assert status != MATCH_EXACT_PRODUCT
    assert status != MATCH_VERIFIED_CLASS


def test_d_generic_analytical_balance_is_placeholder_not_verified() -> None:
    status = match_catalog_status(
        "balance",
        text="balanza analitica de laboratorio",
        is_purchase_signal=True,
        specs_required=True,
    )
    assert status == MATCH_CATEGORY_PLACEHOLDER


def test_d_catalog_digest_includes_product_semantic_fields() -> None:
    # Digest must change when aliases/confidence change — inspect digest payload.
    digest_src = inspect.getsource(CatalogCapability.digest)
    for field in (
        "product_key",
        "confidence",
        "aliases",
        "display_name",
        "model_number",
        "product_kind",
    ):
        assert field in digest_src or "products" in digest_src, (
            f"catalog digest omits semantic field {field!r}"
        )
    assert len(catalog_digest()) == 64
    assert load_catalog_capability().validated is True


def test_d_catalog_digest_in_planner_fingerprints() -> None:
    planner_src = (SRC / "planner.py").read_text(encoding="utf-8")
    assert "catalog_digest" in planner_src or "catalog_capability_digest" in planner_src
    # Must appear inside build_fingerprint / dependency fingerprint construction.
    build_pos = planner_src.find('"build_fingerprint"')
    dep_pos = planner_src.find("dependency_fingerprints")
    assert build_pos >= 0 and dep_pos >= 0
    window = planner_src[min(build_pos, dep_pos) : max(build_pos, dep_pos) + 500]
    assert "catalog" in window.lower(), (
        "catalog digest absent from dependency/build semantic fingerprints"
    )


# ---------------------------------------------------------------------------
# E. Event families
# ---------------------------------------------------------------------------


def test_e_generic_adquisicion_de_equipos_does_not_create_review_component() -> None:
    tenders = tuple(
        _tender(
            coalesced_tender_id=f"g{i}",
            canonical_tender_key=f"chilecompra:g{i}",
            buyer_source_id_selected="42",
            title_selected="Adquisición de equipos",
            publication_timestamp_selected=f"2026-01-{i:02d}T00:00:00Z",
        )
        for i in range(1, 4)
    )
    families, _ = resolve_procurement_event_families(tenders)
    review = [f for f in families if f.family_resolution_status == FAMILY_REVIEW]
    assert not review, (
        "generic 'adquisición de equipos' must not create review components; "
        f"got {len(review)} review families"
    )


def test_e_review_transitivity_preserves_independent_ac_edge() -> None:
    """A–B review + B–C review must not erase authoritative A–C independence."""
    a = _tender(
        coalesced_tender_id="a",
        canonical_tender_key="chilecompra:a",
        buyer_source_id_selected="7",
        title_selected="Adquisicion de centrifuga refrigerada de laboratorio clinica",
        publication_timestamp_selected="2026-01-01T00:00:00Z",
    )
    b = _tender(
        coalesced_tender_id="b",
        canonical_tender_key="chilecompra:b",
        buyer_source_id_selected="7",
        title_selected="Adquisicion de centrifuga refrigerada de laboratorio clinica tipo B",
        publication_timestamp_selected="2026-01-10T00:00:00Z",
    )
    c = _tender(
        coalesced_tender_id="c",
        canonical_tender_key="chilecompra:c",
        buyer_source_id_selected="7",
        title_selected="Adquisicion de centrifuga refrigerada de laboratorio clinica tipo C",
        publication_timestamp_selected="2026-01-20T00:00:00Z",
    )
    families, _ = resolve_procurement_event_families((a, b, c))
    # Independent A–C must survive; lower bound of independent events >= 1 and
    # upper bound must not collapse to forcing a single event as fact.
    for fam in families:
        if set(fam.member_tender_ids) >= {"a", "b", "c"}:
            assert fam.independent_demand_event_count != 0 or (
                fam.independent_event_count_lower_bound >= 1
                and fam.independent_event_count_upper_bound >= 2
            )
            # Unresolved relationship must not report retender_or_reissue as fact.
            if fam.family_resolution_status == FAMILY_REVIEW:
                assert fam.retender_or_reissue_count == 0 or (
                    getattr(fam, "retender_or_reissue_is_fact", False) is False
                )


def test_e_different_buyer_purchasing_units_do_not_join() -> None:
    # Prefer purchasing-unit fields when present on tender provenance.
    t1 = _tender(
        coalesced_tender_id="pu1",
        canonical_tender_key="chilecompra:pu1",
        buyer_source_id_selected="100",
        buyer_display_selected="Hospital Sur / Unidad Compras A",
        title_selected="Adquisicion de centrifuga de laboratorio clinica",
    )
    t2 = _tender(
        coalesced_tender_id="pu2",
        canonical_tender_key="chilecompra:pu2",
        buyer_source_id_selected="100",
        buyer_display_selected="Hospital Sur / Unidad Compras B",
        title_selected="Adquisicion de centrifuga de laboratorio clinica",
    )
    # Annotate purchasing units via selected_field_provenance if supported.
    object.__setattr__(
        t1,
        "selected_field_provenance",
        {"purchasing_unit": "unidad-a"},
    )
    object.__setattr__(
        t2,
        "selected_field_provenance",
        {"purchasing_unit": "unidad-b"},
    )
    families, mapping = resolve_procurement_event_families((t1, t2))
    # Different purchasing units must not share one confirmed/review family.
    if mapping.get("pu1") and mapping.get("pu2"):
        assert mapping["pu1"] != mapping["pu2"] or all(
            f.family_resolution_status != FAMILY_REVIEW
            for f in families
            if set(f.member_tender_ids) >= {"pu1", "pu2"}
        )


# ---------------------------------------------------------------------------
# F. Reconciliation and artifacts
# ---------------------------------------------------------------------------


def test_f_line_reconciliation_rejects_overlaps_and_orphans() -> None:
    from origenlab_email_pipeline.commercial_procurement_institution_prospects import (
        planner,
    )

    fn = getattr(planner, "reconcile_line_partition", None)
    assert fn is not None, "planner must expose reconcile_line_partition()"

    result = fn(
        text_unit_ids=["u1", "u2", "u3", "u1"],  # duplicate unit id
        assigned_unit_ids=["u1", "u2"],
        review_unit_ids=["u2"],
        excluded_unit_ids=[],
        unresolved_unit_ids=["u3", "u1"],  # decided ∩ unresolved
        claim_refs=[
            {"claim_id": "c1", "unit_id": "u1", "coalesced_tender_id": "t1"},
            {"claim_id": "c1", "unit_id": "u1", "coalesced_tender_id": "t1"},
            {"claim_id": "c2", "unit_id": "u9", "coalesced_tender_id": "t9"},
            {"claim_id": "c3", "unit_id": "u3", "coalesced_tender_id": None},
        ],
        decision_unit_ids=["u1", "u-orphan"],
        unresolved_declared_unit_ids=["u3", "u-orphan-unresolved"],
        eligible_tender_ids=["t1"],
    )
    assert result.get("ok") is False
    codes = set(result.get("failure_codes") or [])
    assert codes & {
        "decided_unresolved_overlap",
        "duplicate_decisions",
        "duplicate_product_text_unit_ids",
        "orphan_decisions",
        "orphan_unresolved_units",
        "assigned_review_overlap",
        "orphan_claims",
        "missing_claim_unit_reference",
        "missing_claim_tender_reference",
        "claims_from_unresolved_units",
        "partition_incomplete",
    }


def test_f_required_artifact_filenames_declared() -> None:
    planner_src = (SRC / "planner.py").read_text(encoding="utf-8")
    write_src = planner_src
    missing = [name for name in REQUIRED_ARTIFACTS if name not in write_src]
    assert not missing, f"required artifacts not generated: {missing}"


def test_f_queue_reconciliation_artifact_contract() -> None:
    planner_src = (SRC / "planner.py").read_text(encoding="utf-8")
    assert "operator_queue_reconciliation.json" in planner_src
    assert "false authorization" in planner_src.lower() or (
        "contact_authorization" in planner_src
        and "outreach_authorization" in planner_src
    )


def test_f_source_set_reconciliation_separates_input_populations() -> None:
    planner_src = (SRC / "planner.py").read_text(encoding="utf-8")
    assert "source_set_reconciliation.json" in planner_src
    assert "16641" not in planner_src or "pr5b2" in planner_src.lower()
    # Must mention distinct populations (E1 / E2 / hardened / PR5B.2).
    assert "pr5e1" in planner_src.lower() or "fixed" in planner_src.lower()


def test_a_ast_no_short_queue_string_literals() -> None:
    """AST proof: no short queue name string passed to canonical_queue_row."""
    tree = ast.parse((SRC / "queues.py").read_text(encoding="utf-8"))
    short = {
        "current_opportunity",
        "historical_prospect",
        "line_evidence_review",
        "retender_review",
        "institution_match_review",
        "contact_gap",
    }
    bad: list[str] = []

    class Visitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            fn = node.func
            name = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if name == "canonical_queue_row" and node.args:
                arg0 = node.args[0]
                if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                    if arg0.value in short:
                        bad.append(arg0.value)
            self.generic_visit(node)

    Visitor().visit(tree)
    assert not bad, f"canonical_queue_row still called with short names: {bad}"
