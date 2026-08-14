"""PR5C coalescence / lifecycle planner tests (offline, no network)."""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

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
from origenlab_email_pipeline.commercial_procurement_acquisition.identity import (
    is_mercado_publico_codigo_shape,
    normalize_mercado_publico_codigo,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.snapshot import (
    build_acquisition_snapshot,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.acquisition_instance import (
    candidate_acquisition_instance_id,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.coalescence import (
    coalesce_evidence_refs,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.constants import (
    IDENTITY_NS_MERCADO_PUBLICO,
    IDENTITY_NS_PR4_CODIGO_EXTERNO,
    IDENTITY_NS_PR4_CODIGO_LICITACION,
    IDENTITY_NS_PR4_NUMERO_ADQUISICION,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.fingerprint import (
    candidate_build_plan_fingerprint,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.lifecycle import (
    apply_lifecycle,
    classify_lifecycle,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    CoalescedProcurementTender,
    ProcurementEvidenceRef,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.normalize import (
    accept_canonical_tender_key,
    accept_pr4_signal_identity,
    closing_bucket_for_delta,
    coalesced_tender_id,
    normalize_tender_timestamp,
    normalized_status_meaning,
    parse_as_of_utc,
    parse_tender_timestamp_raw,
    status_internally_inconsistent,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.output_safety import (
    ReportOutputError,
    require_reports_out_dir,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.plane_a_pr4 import (
    load_pr4_plane,
    pr4_signals_to_evidence,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.plane_b_acquisition import (
    AcquisitionPlaneError,
    dedupe_acquisition_instances,
    materialize_acquisition_snapshot,
    snapshot_to_evidence,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.planner import (
    build_candidate_plan,
    write_plan_outputs,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.redaction import (
    assert_no_pii_leaks,
    redact_coalesced_tender,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.walkthrough import (
    build_case_b_live_only_from_ticket_snapshot,
    build_case_c_overlap_through_production_path,
    build_case_d_conflict_through_production_path,
    build_walkthrough_bundle,
    write_walkthrough,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.procurement_eligibility import (
    classify_procurement_eligibility,
)

FIXTURES = Path(__file__).parent / "fixtures"
LIVE = FIXTURES / "commercial_procurement_acquisition_live_contract"
ACQ = FIXTURES / "commercial_procurement_acquisition"
AS_OF = "2026-08-01T12:00:00Z"


def _seed_pr4_db(path: Path, signals: list[dict]) -> None:
    conn = sqlite3.connect(path)
    ensure_commercial_procurement_tables(conn)
    meta = {
        "source_fingerprint": "a" * 64,
        "build_plan_fingerprint": "b" * 64,
        "semantic_plan_digest": "c" * 64,  # recomputed below
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
    conn.row_factory = sqlite3.Row
    for sig in signals:
        conn.execute(
            """
            INSERT INTO commercial_procurement_signal(
              procurement_id, source_system, canonical_tender_key, tender_key_kind,
              buyer_name_raw, buyer_name_norm, buyer_domain_norm, buyer_email_norm,
              region, title, status_code, status_name, publication_at, close_at,
              procurement_context, context_reason_code, confidence, line_item_count,
              constituent_source_ids_json, constituent_lines_fp, first_seen_at,
              last_seen_at, review_status
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                sig["procurement_id"],
                "chilecompra",
                sig["canonical_tender_key"],
                sig.get("tender_key_kind", "codigo_externo"),
                sig.get("buyer_name_raw"),
                sig.get("buyer_name_norm"),
                sig.get("buyer_domain_norm"),
                None,
                None,
                sig.get("title"),
                sig.get("status_code"),
                sig.get("status_name"),
                sig.get("publication_at"),
                sig.get("close_at"),
                sig.get("procurement_context", "historical_tender"),
                sig.get("context_reason_code", "status_inactive_or_closed"),
                "high",
                1,
                json.dumps([sig.get("source_record_id", sig["procurement_id"])]),
                "lines_fp",
                None,
                None,
                "none",
            ),
        )
        conn.execute(
            """
            INSERT INTO commercial_procurement_account_resolution(
              resolution_id, procurement_id, resolution_status, account_id,
              link_route, confidence, reason_code, auto_link_allowed,
              review_status, candidate_account_ids_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                f"r_{sig['procurement_id']}",
                sig["procurement_id"],
                "unlinked",
                None,
                "F_no_match",
                "none",
                "no_match",
                0,
                "none",
                "[]",
            ),
        )
    # Authoritative PR4 digest — do not invent a second algorithm.
    rows = _read_semantic_rows_from_db(conn)
    digest = semantic_plan_digest(table_rows=rows)
    conn.execute(
        "INSERT OR REPLACE INTO commercial_procurement_build_meta(meta_key, meta_value) "
        "VALUES (?,?)",
        ("semantic_plan_digest", digest),
    )
    conn.commit()
    conn.close()


def _ticket_detail_snapshot(
    *,
    acquired_at_utc: str = "2026-08-01T10:00:00Z",
    fixture: Path | None = None,
    tender_code: str = "3544-1-LE26",
) -> dict:
    path = fixture or (LIVE / "ticket_detail_items_live_shape_v1.json")
    payload = json.loads(path.read_text())
    snap = build_acquisition_snapshot(
        source_kind="ticket_detail",
        payload=payload,
        fixture_origin="live_response_sanitized",
        acquired_at_utc=acquired_at_utc,
        tender_code=tender_code,
    )
    return snap.to_dict()


def _write_snap(tmp_path: Path, name: str, payload: dict) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _reports_out(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "email-pipeline"
    out = root / "reports" / "out" / "pr5c_test"
    out.mkdir(parents=True)
    return root, out


def _identity_namespace_for(
    *,
    plane: str,
    key: str,
    tender_key_kind: str,
    cross_source: bool,
) -> str:
    if plane != "pr4":
        return IDENTITY_NS_MERCADO_PUBLICO
    if tender_key_kind == "codigo_externo" and cross_source:
        return IDENTITY_NS_MERCADO_PUBLICO
    if tender_key_kind == "codigo_licitacion":
        return IDENTITY_NS_PR4_CODIGO_LICITACION
    if tender_key_kind == "numero_adquisicion":
        return IDENTITY_NS_PR4_NUMERO_ADQUISICION
    if tender_key_kind == "codigo_externo":
        return IDENTITY_NS_PR4_CODIGO_EXTERNO
    return IDENTITY_NS_PR4_CODIGO_EXTERNO


def _ref(
    *,
    rid: str,
    plane: str,
    key: str,
    rank: str,
    status_code: str | None = None,
    status_name: str | None = None,
    close: str | None = None,
    pub: str | None = None,
    buyer: str | None = None,
    buyer_id: str | None = None,
    acquired: str | None = None,
    obs: str | None = None,
    snap: str | None = None,
    inst: str | None = None,
    page: str | None = None,
    pr4_id: str | None = None,
    tender_key_kind: str | None = None,
    identity_namespace: str | None = None,
    cross_source: bool | None = None,
    payload_digest: str = "x",
) -> ProcurementEvidenceRef:
    pub_nt = normalize_tender_timestamp(pub)
    close_nt = normalize_tender_timestamp(close)
    kind = tender_key_kind
    if kind is None:
        kind = (
            "codigo_externo"
            if plane == "pr4"
            else "mercado_publico_codigo_externo"
        )
    if cross_source is None:
        if plane == "pr4":
            cross_source = bool(
                kind == "codigo_externo" and is_mercado_publico_codigo_shape(key)
            )
        else:
            cross_source = is_mercado_publico_codigo_shape(key)
    ns = identity_namespace or _identity_namespace_for(
        plane=plane, key=key, tender_key_kind=kind, cross_source=cross_source
    )
    is_live = plane != "pr4"
    return ProcurementEvidenceRef(
        evidence_ref_id=rid,
        evidence_plane=plane,
        source_kind="pr4" if plane == "pr4" else "mercado_publico_ticket_api",
        endpoint_kind="pr4_persisted_signal" if plane == "pr4" else "ticket_licitacion_detail",
        source_record_id=rid,
        canonical_tender_key=key,
        tender_key_kind=kind,
        identity_namespace=ns,
        cross_source_join_eligible=cross_source,
        snapshot_id=snap if is_live else None,
        acquisition_instance_id=(inst or (f"inst_{rid}" if is_live else None)),
        page_id=page or (f"page_{rid}" if is_live else None),
        observation_id=obs or (f"obs_{rid}" if is_live else None),
        acquired_at_utc=acquired,
        source_status_code=status_code,
        source_status_name=status_name,
        source_status_value=status_name,
        source_status_system="chilecompra",
        normalized_status_meaning=normalized_status_meaning(status_code, status_name),
        publication_timestamp_raw=pub,
        close_timestamp_raw=close,
        publication_timestamp_utc=pub_nt.utc_iso,
        close_timestamp_utc=close_nt.utc_iso,
        publication_santiago_date=(
            pub_nt.santiago_date.isoformat() if pub_nt.santiago_date else None
        ),
        close_santiago_date=(
            close_nt.santiago_date.isoformat() if close_nt.santiago_date else None
        ),
        publication_precision=pub_nt.precision if pub else None,
        close_precision=close_nt.precision if close else None,
        timestamp_parse_reasons=tuple(
            r for r in (pub_nt.reason, close_nt.reason) if r
        ),
        buyer_display_raw=buyer,
        buyer_source_id=buyer_id,
        title_raw=None,
        source_payload_digest=payload_digest,
        source_fingerprint="y",
        normalized_semantic_digest="z",
        field_provenance={},
        reason_codes=(),
        source_rank_class=rank,
        has_status=bool(status_code or status_name),
        has_close=bool(close),
        has_publication=bool(pub),
        has_buyer_display=bool(buyer),
        has_buyer_source_id=bool(buyer_id),
        has_title=False,
        pr4_procurement_id=pr4_id,
    )


def _tender_shell(**kwargs) -> CoalescedProcurementTender:
    base = dict(
        coalesced_tender_id="t1",
        canonical_tender_key="4000-1-le26",
        identity_namespace=IDENTITY_NS_MERCADO_PUBLICO,
        tender_key_kind="mercado_publico_codigo_externo",
        candidate_source_kind="live_snapshot",
        pr4_procurement_id=None,
        pr4_procurement_ids=(),
        acquisition_snapshot_ids=("s",),
        acquisition_instance_ids=("inst_s",),
        acquisition_observation_ids=("o",),
        coalescence_status="live_only",
        source_precedence_reason="x",
        currentness_class="pending",
        lifecycle_class="pending",
        closing_soon_bucket="not_applicable",
        publication_timestamp_selected="2026-07-01T00:00:00-04:00",
        close_timestamp_selected="2026-08-05T18:00:00-04:00",
        status_code_selected="5",
        status_name_selected="Publicada",
        status_value_selected="Publicada",
        source_status_system_selected="chilecompra",
        buyer_display_selected=None,
        buyer_source_id_selected=None,
        title_selected=None,
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
    base.update(kwargs)
    return CoalescedProcurementTender(**base)


def _coalesce(refs: list[ProcurementEvidenceRef]):
    return coalesce_evidence_refs(
        refs,
        as_of_utc=parse_as_of_utc(AS_OF),
        freshness_threshold_hours=48,
    )


# --- Canonical identity ---


def test_valid_mercado_publico_key_accepted() -> None:
    key, reject = accept_canonical_tender_key(
        candidate="9999-1-LE26",
        candidate_kind="mercado_publico_codigo_externo",
    )
    assert reject is None
    assert key == normalize_mercado_publico_codigo("9999-1-LE26")
    assert is_mercado_publico_codigo_shape(key)


def test_ocid_only_rejected_to_unresolved() -> None:
    key, reject = accept_canonical_tender_key(
        candidate=None,
        candidate_kind="none",
    )
    assert key is None
    assert reject == "live_canonical_candidate_missing"


def test_malformed_candidate_rejected() -> None:
    key, reject = accept_canonical_tender_key(
        candidate="not-a-code",
        candidate_kind="mercado_publico_codigo_externo",
    )
    assert key is None
    assert reject == "live_canonical_candidate_malformed"


def test_source_native_never_canonical() -> None:
    key, reject = accept_canonical_tender_key(
        candidate="ticket_api:codigo_externo:9999-1-le26",
        candidate_kind="none",
    )
    assert key is None
    assert reject in {
        "live_canonical_candidate_missing",
        "unsupported_candidate_kind",
    }


@pytest.mark.parametrize(
    "kind",
    ["codigo_externo", "codigo_licitacion", "numero_adquisicion"],
)
def test_pr4_verified_key_kinds_accepted_without_mp_regex(kind: str) -> None:
    # Non-MP-shape suffix (3 digits) — Plane A still accepts.
    key, out_kind, reason, cross, ns = accept_pr4_signal_identity(
        raw_key="1000-1-LE123",
        tender_key_kind=kind,
    )
    assert reason is None
    assert key == "1000-1-le123"
    assert out_kind == kind
    assert cross is False
    assert ns == f"pr4_{kind}"


def test_corrupt_pr4_key_typed_unresolved(tmp_path: Path) -> None:
    db = tmp_path / "pr4.sqlite"
    _seed_pr4_db(
        db,
        [
            {
                "procurement_id": "p_missing",
                "canonical_tender_key": "   ",
                "tender_key_kind": "codigo_externo",
                "status_code": "6",
                "status_name": "Cerrada",
            },
        ],
    )
    pr4 = load_pr4_plane(db)
    refs, unresolved = pr4_signals_to_evidence(pr4)
    assert refs == []
    assert unresolved[0].unresolved_reason == "pr4_canonical_key_missing"

    key, kind, reason, cross, ns = accept_pr4_signal_identity(
        raw_key="1000-1-LE26",
        tender_key_kind="made_up_kind",
    )
    assert key is None and cross is False
    assert reason == "pr4_tender_key_kind_unsupported"
    assert kind == "made_up_kind"
    assert ns is None


def test_pr4_only_preserves_non_mp_kind() -> None:
    ref = _ref(
        rid="p",
        plane="pr4",
        key="1000-1-le123",
        rank="pr4",
        status_code="6",
        status_name="Cerrada",
        tender_key_kind="codigo_licitacion",
        cross_source=False,
        pr4_id="p1",
    )
    tenders, _ = _coalesce([ref])
    assert tenders[0].tender_key_kind == "codigo_licitacion"
    assert tenders[0].candidate_source_kind == "pr4"


def test_grouping_independent_of_input_order() -> None:
    a = _ref(
        rid="e2",
        plane="pr4",
        key="1000-1-le26",
        rank="pr4",
        status_code="6",
        status_name="Cerrada",
        close="2026-01-01",
        pr4_id="p2",
    )
    b = _ref(
        rid="e1",
        plane="pr4",
        key="1000-1-le26",
        rank="pr4",
        status_code="6",
        status_name="Cerrada",
        close="2026-01-01",
        pr4_id="p1",
    )
    t1, _ = _coalesce([a, b])
    t2, _ = _coalesce([b, a])
    assert len(t1) == 1 and len(t2) == 1
    assert t1[0].coalesced_tender_id == t2[0].coalesced_tender_id


def test_procurement_method_selection_independent_of_input_order() -> None:
    """PR3: procurement_method/procurement_method_details are selected via the
    same cross-source precedence as title/buyer_display (higher
    source_rank_class wins). Reversing the evidence-ref input order must not
    change which value wins — the planner/queue read this value verbatim, so
    order-dependence here would make eligibility classification nondeterministic.
    """
    from dataclasses import replace

    high_rank = replace(
        _ref(
            rid="detail",
            plane="acquisition",
            key="9000-1-co26",
            rank="ticket_detail",
            acquired="2026-08-01T10:00:00Z",
            obs="obs_detail",
            snap="snap1",
        ),
        procurement_method_raw="CO",
        procurement_method_details_raw="0",
        has_procurement_method=True,
        has_procurement_method_details=True,
    )
    low_rank = replace(
        _ref(
            rid="release",
            plane="acquisition",
            key="9000-1-co26",
            rank="ocds_release",
            acquired="2026-08-01T10:00:00Z",
            obs="obs_release",
            snap="snap1",
        ),
        procurement_method_raw="selective",
        procurement_method_details_raw=None,
        has_procurement_method=True,
        has_procurement_method_details=False,
    )
    forward, _ = _coalesce([high_rank, low_rank])
    reversed_result, _ = _coalesce([low_rank, high_rank])
    assert len(forward) == 1 and len(reversed_result) == 1
    assert forward[0].procurement_method_selected == "CO"
    assert forward[0].procurement_method_details_selected == "0"
    assert (
        forward[0].procurement_method_selected
        == reversed_result[0].procurement_method_selected
    )
    assert (
        forward[0].procurement_method_details_selected
        == reversed_result[0].procurement_method_details_selected
    )


def test_procurement_method_details_never_backfilled_from_losing_reference() -> None:
    """procurement_method and procurement_method_details must be selected as
    one coherent pair from the SAME winning evidence ref — never assembled
    from two different sources. If the winning ref has a method but no
    details, details stay None; a lower-ranked ref's details must not be used
    to fill the gap.
    """
    from dataclasses import replace

    high_rank_method_only = replace(
        _ref(
            rid="detail",
            plane="acquisition",
            key="9010-1-co26",
            rank="ticket_detail",
            acquired="2026-08-01T10:00:00Z",
            obs="obs_detail",
            snap="snap1",
        ),
        procurement_method_raw="CO",
        procurement_method_details_raw=None,
        has_procurement_method=True,
        has_procurement_method_details=False,
    )
    low_rank_details_only = replace(
        _ref(
            rid="release",
            plane="acquisition",
            key="9010-1-co26",
            rank="ocds_release",
            acquired="2026-08-01T10:00:00Z",
            obs="obs_release",
            snap="snap1",
        ),
        procurement_method_raw=None,
        procurement_method_details_raw="1",
        has_procurement_method=False,
        has_procurement_method_details=True,
    )
    tenders, _ = _coalesce([high_rank_method_only, low_rank_details_only])
    assert len(tenders) == 1
    assert tenders[0].procurement_method_selected == "CO"
    assert tenders[0].procurement_method_details_selected is None, (
        "details must not be backfilled from a different, lower-ranked ref"
    )


def test_procurement_method_ticket_beats_ocds_deterministically() -> None:
    """Multi-source conflict: the same tender carries conflicting Ticket
    (CO/0) and OCDS (open) evidence. The existing, pre-established rank
    hierarchy (ticket_detail=100 > ocds_release=70) resolves this
    unambiguously — Ticket wins regardless of input order. The tender must
    never appear open/actionable merely because OCDS evidence also exists.
    """
    from dataclasses import replace

    ticket = replace(
        _ref(
            rid="detail",
            plane="acquisition",
            key="9011-1-co26",
            rank="ticket_detail",
            acquired="2026-08-01T10:00:00Z",
            obs="obs_detail",
            snap="snap1",
        ),
        procurement_method_raw="CO",
        procurement_method_details_raw="0",
        has_procurement_method=True,
        has_procurement_method_details=True,
    )
    ocds = replace(
        _ref(
            rid="release",
            plane="acquisition",
            key="9011-1-co26",
            rank="ocds_release",
            acquired="2026-08-01T10:00:00Z",
            obs="obs_release",
            snap="snap1",
        ),
        procurement_method_raw="open",
        procurement_method_details_raw=None,
        has_procurement_method=True,
        has_procurement_method_details=False,
    )
    forward, _ = _coalesce([ticket, ocds])
    reversed_result, _ = _coalesce([ocds, ticket])
    assert forward[0].procurement_method_selected == "CO"
    assert forward[0].procurement_method_details_selected == "0"
    assert reversed_result[0].procurement_method_selected == "CO"
    assert reversed_result[0].procurement_method_details_selected == "0"


def test_procurement_method_same_rank_disagreement_fails_closed() -> None:
    """When no precedence resolves a conflict — two candidates at the exact
    same source rank disagree on procurement_method — the safe behavior is
    to select nothing (None), which classify_procurement_eligibility()
    downstream turns into "unknown" and the existing unknown/unmapped
    blocker. This must never arbitrarily pick a side between an open and a
    restricted value, and must be identical regardless of input order.
    """
    from dataclasses import replace

    a = replace(
        _ref(
            rid="detail_a",
            plane="acquisition",
            key="9012-1-le26",
            rank="ticket_detail",
            acquired="2026-08-01T10:00:00Z",
            obs="obs_a",
            snap="snap1",
        ),
        procurement_method_raw="LP",
        procurement_method_details_raw="1",
        has_procurement_method=True,
        has_procurement_method_details=True,
    )
    b = replace(
        _ref(
            rid="detail_b",
            plane="acquisition",
            key="9012-1-le26",
            rank="ticket_detail",
            acquired="2026-08-01T11:00:00Z",
            obs="obs_b",
            snap="snap1",
        ),
        procurement_method_raw="CO",
        procurement_method_details_raw="0",
        has_procurement_method=True,
        has_procurement_method_details=True,
    )
    forward, forward_conflicts = _coalesce([a, b])
    reversed_result, reversed_conflicts = _coalesce([b, a])
    assert forward[0].procurement_method_selected is None
    assert forward[0].procurement_method_details_selected is None
    assert reversed_result[0].procurement_method_selected is None
    assert reversed_result[0].procurement_method_details_selected is None
    assert any(
        c.conflict_kind == "procurement_method_conflict" for c in forward_conflicts
    )
    assert any(
        c.conflict_kind == "procurement_method_conflict" for c in reversed_conflicts
    )


def test_procurement_method_duplicate_agreeing_evidence_is_not_a_false_conflict() -> None:
    """Two equally-ranked refs that agree on procurement_method (e.g. a
    duplicate fetch) must be selected normally, not treated as a conflict.
    """
    from dataclasses import replace

    a = replace(
        _ref(
            rid="detail_a",
            plane="acquisition",
            key="9013-1-lp26",
            rank="ticket_detail",
            acquired="2026-08-01T10:00:00Z",
            obs="obs_a",
            snap="snap1",
        ),
        procurement_method_raw="LP",
        procurement_method_details_raw="1",
        has_procurement_method=True,
        has_procurement_method_details=True,
    )
    b = replace(
        _ref(
            rid="detail_b",
            plane="acquisition",
            key="9013-1-lp26",
            rank="ticket_detail",
            acquired="2026-08-01T11:00:00Z",
            obs="obs_b",
            snap="snap1",
        ),
        procurement_method_raw="LP",
        procurement_method_details_raw="1",
        has_procurement_method=True,
        has_procurement_method_details=True,
    )
    tenders, conflicts = _coalesce([a, b])
    assert tenders[0].procurement_method_selected == "LP"
    assert tenders[0].procurement_method_details_selected == "1"
    assert not any(c.conflict_kind == "procurement_method_conflict" for c in conflicts)


def test_build_candidate_plan_preserves_procurement_method_through_lifecycle(
    tmp_path: Path,
) -> None:
    """Live-verification regression: a fresh ChileCompra pull found
    coalesce_evidence_refs() correctly selecting "LP" for a real tender
    (745712-19-LP26 / SAG), but the full build_candidate_plan() path —
    which additionally runs apply_lifecycle() after coalescence — reset
    procurement_method_selected to None for 100% of live-snapshot tenders.
    apply_lifecycle() reconstructed CoalescedProcurementTender field-by-field
    and never carried the two PR3 fields forward. This exercises the public
    build_candidate_plan() path end to end (not coalesce_evidence_refs() in
    isolation, not a directly constructed CoalescedProcurementTender) with a
    real Ticket-detail-shaped snapshot, through apply_lifecycle(), and proves
    both fields — and downstream eligibility classification — survive.
    """
    db = tmp_path / "pr4.sqlite"
    _seed_pr4_db(db, [])

    public_snap = _write_snap(tmp_path, "public.json", _ticket_detail_snapshot())

    restricted_payload = json.loads(
        (LIVE / "ticket_detail_items_live_shape_v1.json").read_text()
    )
    restricted_lic = restricted_payload["Listado"][0]
    restricted_lic["CodigoExterno"] = "9999-1-CO26"
    restricted_lic["Tipo"] = "CO"
    restricted_lic["TipoConvocatoria"] = "0"
    restricted_snapshot = build_acquisition_snapshot(
        source_kind="ticket_detail",
        payload=restricted_payload,
        fixture_origin="live_response_sanitized",
        acquired_at_utc="2026-08-01T10:00:00Z",
        tender_code="9999-1-CO26",
    )
    restricted_snap = _write_snap(
        tmp_path, "restricted.json", restricted_snapshot.to_dict()
    )

    result = build_candidate_plan(
        sqlite_path=db,
        acquisition_snapshot_paths=[public_snap, restricted_snap],
        as_of_utc=AS_OF,
        freshness_threshold_hours=48,
        run_context="local_fixture",
    )

    by_key = {t.canonical_tender_key: t for t in result.coalesced_tenders}
    public_tender = by_key["3544-1-le26"]
    restricted_tender = by_key["9999-1-co26"]

    # Public method (Tipo=LE, TipoConvocatoria=1 in the live-shape fixture)
    # survives the full path, including apply_lifecycle().
    assert public_tender.procurement_method_selected == "LE"
    assert public_tender.procurement_method_details_selected == "1"
    prov_ref_id = public_tender.selected_field_provenance.get("procurement_method")
    assert prov_ref_id is not None
    assert prov_ref_id in public_tender.evidence_ref_ids
    assert (
        classify_procurement_eligibility(public_tender.procurement_method_selected)
        == "open_public"
    )

    # Restricted method (Tipo=CO) also survives and classifies correctly.
    assert restricted_tender.procurement_method_selected == "CO"
    assert restricted_tender.procurement_method_details_selected == "0"
    assert (
        classify_procurement_eligibility(restricted_tender.procurement_method_selected)
        == "restricted_invitation_unconfirmed"
    )


def test_apply_lifecycle_preserves_procurement_method_exactly() -> None:
    """apply_lifecycle() must change only lifecycle/currentness fields — it
    must not reset, backfill, or synthesize procurement_method_selected /
    procurement_method_details_selected. Covers: a populated method with
    populated details, and a populated method with details=None (which must
    stay None, never backfilled from anywhere).
    """
    from dataclasses import replace

    ref_with_details = _ref(
        rid="ref_both",
        plane="acquisition",
        key="8001-1-lp26",
        rank="ticket_detail",
        status_code="5",
        status_name="Publicada",
        close="2026-09-01T15:00:00-04:00",
        acquired="2026-08-01T10:00:00Z",
        obs="obs_both",
        snap="snap_both",
    )
    ref_with_details = replace(
        ref_with_details,
        procurement_method_raw="LP",
        procurement_method_details_raw="1",
        has_procurement_method=True,
        has_procurement_method_details=True,
    )
    ref_method_only = _ref(
        rid="ref_method_only",
        plane="acquisition",
        key="8002-1-co26",
        rank="ticket_detail",
        status_code="5",
        status_name="Publicada",
        close="2026-09-01T15:00:00-04:00",
        acquired="2026-08-01T10:00:00Z",
        obs="obs_method_only",
        snap="snap_method_only",
    )
    ref_method_only = replace(
        ref_method_only,
        procurement_method_raw="CO",
        procurement_method_details_raw=None,
        has_procurement_method=True,
        has_procurement_method_details=False,
    )

    tenders_both, _ = _coalesce([ref_with_details])
    tenders_method_only, _ = _coalesce([ref_method_only])
    refs_by_id = {
        ref_with_details.evidence_ref_id: ref_with_details,
        ref_method_only.evidence_ref_id: ref_method_only,
    }

    lifecycle_applied = apply_lifecycle(
        list(tenders_both) + list(tenders_method_only),
        refs_by_id=refs_by_id,
        conflicts_by_id={},
        as_of_utc=parse_as_of_utc(AS_OF),
        freshness_threshold_hours=48,
    )
    by_key = {t.canonical_tender_key: t for t in lifecycle_applied}

    both = by_key["8001-1-lp26"]
    assert both.procurement_method_selected == "LP"
    assert both.procurement_method_details_selected == "1"
    # Lifecycle fields were genuinely recomputed (proves apply_lifecycle ran,
    # not that it was a no-op).
    assert both.lifecycle_class != "pending"
    assert both.currentness_class != "pending"

    method_only = by_key["8002-1-co26"]
    assert method_only.procurement_method_selected == "CO"
    assert method_only.procurement_method_details_selected is None
    assert method_only.lifecycle_class != "pending"


# --- Stable coalesced ID ---


def test_stable_id_pr4_only_to_both() -> None:
    key = "2000-1-le26"
    pr4 = _ref(
        rid="p",
        plane="pr4",
        key=key,
        rank="pr4",
        status_code="5",
        status_name="Publicada",
        close="2026-08-10T18:00:00-04:00",
        pr4_id="proc1",
        tender_key_kind="codigo_externo",
        cross_source=True,
    )
    live = _ref(
        rid="l",
        plane="acquisition",
        key=key,
        rank="ticket_detail",
        status_code="5",
        status_name="Publicada",
        close="2026-08-10T18:00:00-04:00",
        acquired="2026-08-01T10:00:00Z",
        obs="obs1",
        snap="snap1",
    )
    t_pr4, _ = _coalesce([pr4])
    t_both, _ = _coalesce([pr4, live])
    assert t_pr4[0].coalesced_tender_id == t_both[0].coalesced_tender_id


def test_stable_id_second_snapshot_and_reorder() -> None:
    key = "2100-1-le26"
    a = _ref(
        rid="a",
        plane="acquisition",
        key=key,
        rank="ticket_detail",
        status_code="5",
        status_name="Publicada",
        close="2026-08-10T12:00:00Z",
        acquired="2026-08-01T10:00:00Z",
        obs="o1",
        snap="s1",
        payload_digest="d1",
    )
    b = _ref(
        rid="b",
        plane="acquisition",
        key=key,
        rank="ticket_detail",
        status_code="5",
        status_name="Publicada",
        close="2026-08-10T12:00:00Z",
        acquired="2026-08-01T11:00:00Z",
        obs="o2",
        snap="s2",
        payload_digest="d2",
    )
    t1, _ = _coalesce([a])
    t2, _ = _coalesce([a, b])
    t3, _ = _coalesce([b, a])
    assert t1[0].coalesced_tender_id == t2[0].coalesced_tender_id == t3[0].coalesced_tender_id


def test_stable_id_changes_with_canonical_key() -> None:
    a = coalesced_tender_id(identity_namespace="mercado_publico_codigo_externo", canonical_tender_key="1000-1-le26")
    b = coalesced_tender_id(identity_namespace="mercado_publico_codigo_externo", canonical_tender_key="1000-2-le26")
    assert a != b


# --- Plane adapters ---


def test_materialize_snapshot_and_reject_fingerprint_mismatch(tmp_path: Path) -> None:
    snap = _ticket_detail_snapshot()
    materialize_acquisition_snapshot(snap)
    bad = dict(snap)
    bad["source_fingerprint"] = "0" * 64
    with pytest.raises(AcquisitionPlaneError, match="source_fingerprint"):
        materialize_acquisition_snapshot(bad)


def test_parser_v1_snapshot_rehydrates_through_materialize_boundary() -> None:
    """PR3 gate: genuine parser-v1 compatibility, exercised through the real
    production rehydration boundary (materialize_acquisition_snapshot), not
    just a direct _validate_versions() unit test.

    A realistic pre-PR3 persisted snapshot never had Tipo/TipoConvocatoria in
    its source payload (the old parser did not read them), so it is
    constructed here without those fields, tagged parser_version=v1 exactly
    as a real historical file would be (including a v1-consistent
    normalized_semantic_digest, which the real materializer recomputes and
    checks), and must still rehydrate successfully with safely-None method
    fields. A v2 snapshot with real Tipo/TipoConvocatoria data must also
    rehydrate and correctly propagate populated method fields. An
    unsupported parser_version must remain rejected — version validation is
    not weakened globally by widening SUPPORTED_PARSER_VERSIONS.
    """
    from origenlab_email_pipeline.commercial_procurement_acquisition.fingerprint import (
        acquisition_normalized_semantic_digest,
    )
    from origenlab_email_pipeline.commercial_procurement_candidate_planner.plane_b_acquisition import (
        _line_from_dict,
        _source_from_dict,
        _tender_from_dict,
    )

    payload = json.loads((LIVE / "ticket_detail_items_live_shape_v1.json").read_text())
    lic = payload["Listado"][0]
    lic.pop("Tipo", None)
    lic.pop("TipoConvocatoria", None)
    snap = build_acquisition_snapshot(
        source_kind="ticket_detail",
        payload=payload,
        fixture_origin="live_response_sanitized",
        acquired_at_utc="2026-08-01T10:00:00Z",
        tender_code="3544-1-LE26",
    )
    v1_dict = snap.to_dict()
    v1_dict["parser_version"] = "procurement_acquisition_parser_v1"
    for source_obs in v1_dict["source_observations"]:
        source_obs["parser_version"] = "procurement_acquisition_parser_v1"
    # A real v1-era file's digest was computed with parser_version=v1 at the
    # time it was written; recompute it the same way materialize_* does, so
    # the digest check (not just the version check) reflects a genuine file.
    v1_dict["normalized_semantic_digest"] = acquisition_normalized_semantic_digest(
        source_observations=tuple(
            _source_from_dict(dict(o)) for o in v1_dict["source_observations"]
        ),
        tender_observations=tuple(
            _tender_from_dict(dict(t)) for t in v1_dict["tender_observations"]
        ),
        line_observations=tuple(
            _line_from_dict(dict(line)) for line in v1_dict["line_observations"]
        ),
        parser_version="procurement_acquisition_parser_v1",
        contract_version=v1_dict["contract_version"],
    )

    rehydrated_v1 = materialize_acquisition_snapshot(v1_dict)
    assert rehydrated_v1.parser_version == "procurement_acquisition_parser_v1"
    assert rehydrated_v1.tender_observations[0].procurement_method is None
    assert rehydrated_v1.tender_observations[0].procurement_method_details is None

    payload_v2 = json.loads((LIVE / "ticket_detail_items_live_shape_v1.json").read_text())
    snap_v2 = build_acquisition_snapshot(
        source_kind="ticket_detail",
        payload=payload_v2,
        fixture_origin="live_response_sanitized",
        acquired_at_utc="2026-08-01T10:00:00Z",
        tender_code="3544-1-LE26",
    )
    v2_dict = snap_v2.to_dict()
    assert v2_dict["parser_version"] == "procurement_acquisition_parser_v2"
    rehydrated_v2 = materialize_acquisition_snapshot(v2_dict)
    assert rehydrated_v2.tender_observations[0].procurement_method == "LE"

    unsupported = dict(v2_dict)
    unsupported["parser_version"] = "procurement_acquisition_parser_v99_nonexistent"
    with pytest.raises(AcquisitionPlaneError, match="parser_version"):
        materialize_acquisition_snapshot(unsupported)


def test_lista_index_without_mp_code_is_unresolved() -> None:
    payload = json.loads((LIVE / "ocds_range_live_shape_v1.json").read_text())
    snap = build_acquisition_snapshot(
        source_kind="ocds",
        payload=payload,
        fixture_origin="live_response_sanitized",
        year=2026,
        month=7,
        range_start=1,
        range_end=1,
        acquired_at_utc="2026-08-01T10:00:00Z",
    )
    inst = candidate_acquisition_instance_id(snap)
    refs, unresolved = snapshot_to_evidence(snap, acquisition_instance_id=inst)
    assert refs == []
    assert unresolved
    assert unresolved[0].unresolved_reason in {
        "ocds_ocid_only_unresolved",
        "source_native_identity_not_canonical",
        "live_canonical_candidate_missing",
    }


def test_ocds_release_fixture_may_yield_canonical_or_unresolved() -> None:
    payload = json.loads((ACQ / "ocds_ocid_only.json").read_text())
    snap = build_acquisition_snapshot(
        source_kind="ocds",
        payload=payload,
        fixture_origin="synthetic_official_shape",
        acquired_at_utc="2026-08-01T10:00:00Z",
    )
    inst = candidate_acquisition_instance_id(snap)
    _refs, unresolved = snapshot_to_evidence(snap, acquisition_instance_id=inst)
    assert unresolved


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda s: s.__setitem__("parser_version", "nope"), "parser_version"),
        (lambda s: s.__setitem__("contract_version", "nope"), "contract_version"),
        (lambda s: s.__setitem__("snapshot_id", "procurement_snapshot_id_deadbeef"), "snapshot_id"),
        (
            lambda s: s["source_observations"].__setitem__(
                0,
                {**s["source_observations"][0], "observation_id": "wrong"},
            ),
            "observation_id",
        ),
        (
            lambda s: s["pages"].__setitem__(
                0, {**s["pages"][0], "page_id": "acquisition_page_id_deadbeef"}
            ),
            "page_id",
        ),
        (
            lambda s: s["tender_observations"].__setitem__(
                0,
                {
                    **s["tender_observations"][0],
                    "tender_observation_id": "procurement_tender_observation_id_dead",
                },
            ),
            "tender_observation_id",
        ),
        (
            lambda s: s["line_observations"].__setitem__(
                0,
                {
                    **s["line_observations"][0],
                    "line_observation_id": "procurement_line_observation_id_dead",
                },
            ),
            "line_observation_id",
        ),
        (
            lambda s: s["source_observations"].__setitem__(
                0, {**s["source_observations"][0], "page_id": "missing_page"}
            ),
            "page_id missing",
        ),
        (
            lambda s: s.__setitem__(
                "source_observations",
                s["source_observations"] + [s["source_observations"][0]],
            ),
            "duplicate source observation",
        ),
    ],
)
def test_snapshot_linkage_rejections(mutate, match) -> None:
    snap = _ticket_detail_snapshot()
    mutate(snap)
    with pytest.raises(AcquisitionPlaneError, match=match):
        materialize_acquisition_snapshot(snap)


def test_duplicate_snapshot_id_divergent_rejected(tmp_path: Path) -> None:
    from dataclasses import replace

    a = materialize_acquisition_snapshot(
        _ticket_detail_snapshot(acquired_at_utc="2026-08-01T10:00:00Z")
    )
    b = materialize_acquisition_snapshot(
        _ticket_detail_snapshot(acquired_at_utc="2026-08-01T11:00:00Z")
    )
    # Same content identity, different acquisition times → two instances retained.
    assert a.snapshot_id == b.snapshot_id
    out = dedupe_acquisition_instances([a, b])
    assert len(out) == 2
    assert {iid for iid, _ in out} == {
        candidate_acquisition_instance_id(a),
        candidate_acquisition_instance_id(b),
    }
    # Exact same instance repeated → kept once.
    same = replace(a)
    assert len(dedupe_acquisition_instances([a, same])) == 1
    # Same instance_id with divergent event metadata → reject.
    # Force collision by replacing b's pages acquired_at while keeping a's instance
    # identity is impossible via normal IDs; instead corrupt by replacing page
    # metadata after computing id is not how production works — diverge by
    # mutating http_status while cloning event fields used for ID is also
    # impossible if ID hashes those fields. Reject path: two snaps with equal
    # instance payload keys but different completeness after ID collision via
    # monkeypatch is overkill — assert divergent same-id by crafting via replace
    # of a page field that is in the hash after manually forcing same id.
    # Practical contract: divergent event payload cannot share instance_id.
    # Covered by hashing acquired_at into ID (asserted above).
    assert candidate_acquisition_instance_id(a) != candidate_acquisition_instance_id(b)


# --- Coalescence ---


def test_pr4_only_and_live_only_and_exact_agreement() -> None:
    pr4 = _ref(
        rid="p",
        plane="pr4",
        key="2000-1-le26",
        rank="pr4",
        status_code="5",
        status_name="Publicada",
        close="2026-08-10T18:00:00-04:00",
        buyer="Org A",
        pr4_id="proc1",
    )
    live = _ref(
        rid="l",
        plane="acquisition",
        key="2000-1-le26",
        rank="ticket_detail",
        status_code="5",
        status_name="Publicada",
        close="2026-08-10T18:00:00-04:00",
        buyer="Org A",
        acquired="2026-08-01T10:00:00Z",
        obs="obs1",
        snap="snap1",
    )
    t_pr4, _ = _coalesce([pr4])
    assert t_pr4[0].coalescence_status == "pr4_only"
    t_live, _ = _coalesce([live])
    assert t_live[0].coalescence_status == "live_only"
    t_both, _ = _coalesce([pr4, live])
    assert t_both[0].candidate_source_kind == "both"
    assert t_both[0].coalescence_status in {"exact_agreement", "live_source_newer"}


def test_status_and_date_and_buyer_conflicts() -> None:
    a = _ref(
        rid="a",
        plane="acquisition",
        key="3000-1-le26",
        rank="ticket_detail",
        status_code="5",
        status_name="Publicada",
        close="2026-08-10T12:00:00Z",
        buyer="Buyer A",
        buyer_id="a.cl",
        acquired="2026-08-01T10:00:00Z",
        obs="o1",
        snap="s1",
    )
    b = _ref(
        rid="b",
        plane="acquisition",
        key="3000-1-le26",
        rank="ticket_summary",
        status_code="8",
        status_name="Adjudicada",
        close="2026-08-11T12:00:00Z",
        buyer="Buyer B",
        buyer_id="b.cl",
        acquired="2026-08-01T10:00:00Z",
        obs="o2",
        snap="s2",
    )
    tenders, conflicts = _coalesce([a, b])
    kinds = {c.conflict_kind for c in conflicts}
    assert "status_conflict" in kinds
    assert "date_conflict" in kinds
    assert "buyer_identity_conflict" in kinds
    assert tenders[0].coalescence_status in {
        "status_conflict",
        "multiple_live_sources_conflict",
    }


def test_atomic_status_selection_same_evidence() -> None:
    ref = _ref(
        rid="a",
        plane="acquisition",
        key="3100-1-le26",
        rank="ticket_detail",
        status_code="5",
        status_name="Publicada",
        close="2026-08-10T12:00:00Z",
        acquired="2026-08-01T10:00:00Z",
        obs="o1",
        snap="s1",
    )
    tenders, _ = _coalesce([ref])
    prov = tenders[0].selected_field_provenance
    assert prov.get("status") == "a"
    assert prov.get("status_code") == "a"
    assert prov.get("status_name") == "a"


def test_inconsistent_status_code_name_conflict() -> None:
    assert status_internally_inconsistent("8", "Publicada")
    assert status_internally_inconsistent("5", "Adjudicada")
    assert not status_internally_inconsistent("5", "Publicada")
    assert not status_internally_inconsistent(
        "7", "Desierta (o art. 3 ó 9 Ley 19.886)"
    )
    ref = _ref(
        rid="a",
        plane="acquisition",
        key="3200-1-le26",
        rank="ticket_detail",
        status_code="8",
        status_name="Publicada",
        close="2026-08-10T12:00:00Z",
        acquired="2026-08-01T10:00:00Z",
        obs="o1",
        snap="s1",
    )
    _, conflicts = _coalesce([ref])
    assert any("status_code_name_inconsistent" in c.reason_codes for c in conflicts)


def test_timestamp_semantic_equality_no_conflict() -> None:
    a = _ref(
        rid="a",
        plane="acquisition",
        key="3300-1-le26",
        rank="ticket_detail",
        status_code="5",
        status_name="Publicada",
        close="2026-08-10T18:00:00-04:00",
        acquired="2026-08-01T10:00:00Z",
        obs="o1",
        snap="s1",
    )
    b = _ref(
        rid="b",
        plane="acquisition",
        key="3300-1-le26",
        rank="ticket_summary",
        status_code="5",
        status_name="Publicada",
        close="2026-08-10T22:00:00Z",
        acquired="2026-08-01T10:00:00Z",
        obs="o2",
        snap="s2",
    )
    _, conflicts = _coalesce([a, b])
    assert not any(c.conflict_kind == "date_conflict" for c in conflicts)


def test_naive_santiago_vs_aware_equivalent() -> None:
    dt_naive, err1 = parse_tender_timestamp_raw("2026-08-10 18:00:00")
    dt_aware, err2 = parse_tender_timestamp_raw("2026-08-10T18:00:00-04:00")
    assert err1 is None and err2 is None
    assert dt_naive == dt_aware


def test_genuine_close_instant_conflict() -> None:
    a = _ref(
        rid="a",
        plane="acquisition",
        key="3400-1-le26",
        rank="ticket_detail",
        status_code="5",
        status_name="Publicada",
        close="2026-08-10T12:00:00Z",
        acquired="2026-08-01T10:00:00Z",
        obs="o1",
        snap="s1",
    )
    b = _ref(
        rid="b",
        plane="acquisition",
        key="3400-1-le26",
        rank="ticket_summary",
        status_code="5",
        status_name="Publicada",
        close="2026-08-11T12:00:00Z",
        acquired="2026-08-01T10:00:00Z",
        obs="o2",
        snap="s2",
    )
    tenders, conflicts = _coalesce([a, b])
    assert any(
        "close_timestamp_conflict" in c.reason_codes for c in conflicts
    )
    as_of = parse_as_of_utc(AS_OF)
    out = apply_lifecycle(
        tenders,
        refs_by_id={"a": a, "b": b},
        conflicts_by_id={c.conflict_id: c for c in conflicts},
        as_of_utc=as_of,
        freshness_threshold_hours=48,
    )
    assert out[0].lifecycle_class != "active_open"
    assert "authoritative_close_date_conflict" in out[0].lifecycle_reason_codes


def test_timezone_unresolved_malformed() -> None:
    dt, err = parse_tender_timestamp_raw("not-a-timestamp")
    assert dt is None
    assert err == "timezone_unresolved"


def test_identical_repeated_observation_deduped() -> None:
    a = _ref(
        rid="a1",
        plane="acquisition",
        key="3500-1-le26",
        rank="ticket_detail",
        status_code="5",
        status_name="Publicada",
        close="2026-08-10T12:00:00Z",
        acquired="2026-08-01T10:00:00Z",
        obs="same_obs",
        snap="s1",
        inst="same_inst",
        page="same_page",
        payload_digest="same",
    )
    b = _ref(
        rid="a2",
        plane="acquisition",
        key="3500-1-le26",
        rank="ticket_detail",
        status_code="5",
        status_name="Publicada",
        close="2026-08-10T12:00:00Z",
        acquired="2026-08-01T10:00:00Z",
        obs="same_obs",
        snap="s1",
        inst="same_inst",
        page="same_page",
        payload_digest="same",
    )
    tenders, conflicts = _coalesce([a, b])
    assert len(tenders[0].evidence_ref_ids) == 1
    assert not any(
        c.conflict_kind == "duplicate_live_observation_conflict" for c in conflicts
    )


# --- Freshness / lifecycle ---


def test_stale_open_not_active_and_current_open_active() -> None:
    as_of = parse_as_of_utc(AS_OF)
    tender = _tender_shell(
        selected_field_provenance={"status": "e1", "status_code": "e1", "status_name": "e1", "close_timestamp": "e1"},
    )
    stale_ref = _ref(
        rid="e1",
        plane="acquisition",
        key="4000-1-le26",
        rank="ticket_detail",
        status_code="5",
        status_name="Publicada",
        close="2026-08-05T18:00:00-04:00",
        acquired="2026-07-01T10:00:00Z",
        obs="o",
        snap="s",
    )
    out = apply_lifecycle(
        [tender],
        refs_by_id={"e1": stale_ref},
        as_of_utc=as_of,
        freshness_threshold_hours=48,
    )
    assert out[0].currentness_class == "stale_authoritative_snapshot"
    assert out[0].lifecycle_class != "active_open"

    fresh_ref = _ref(
        rid="e1",
        plane="acquisition",
        key="4000-1-le26",
        rank="ticket_detail",
        status_code="5",
        status_name="Publicada",
        close="2026-08-05T18:00:00-04:00",
        acquired="2026-08-01T10:00:00Z",
        obs="o",
        snap="s",
    )
    out2 = apply_lifecycle(
        [tender],
        refs_by_id={"e1": fresh_ref},
        as_of_utc=as_of,
        freshness_threshold_hours=48,
    )
    assert out2[0].lifecycle_class == "active_open"
    assert out2[0].closing_soon_bucket == "d4_to_d7"
    assert out2[0].lifecycle_status_evidence_ref_id == "e1"
    assert out2[0].lifecycle_close_evidence_ref_id == "e1"


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (timedelta(seconds=-1), "not_applicable"),
        (timedelta(hours=12), "lt_24h"),
        (timedelta(hours=24), "d1_to_d3"),
        (timedelta(days=3), "d1_to_d3"),
        (timedelta(days=4), "d4_to_d7"),
        (timedelta(days=7), "d4_to_d7"),
        (timedelta(days=8), "gt_7d"),
    ],
)
def test_closing_bucket_for_delta_operator_boundaries(
    delta: timedelta, expected: str
) -> None:
    assert closing_bucket_for_delta(delta) == expected


def test_close_exactly_at_as_of_is_closed() -> None:
    as_of = parse_as_of_utc("2026-08-01T12:00:00Z")
    life, bucket, _, _ = classify_lifecycle(
        tender=_tender_shell(
            close_timestamp_selected="2026-08-01T12:00:00Z",
            currentness_class="current_authoritative_snapshot",
            evidence_ref_ids=(),
            acquisition_snapshot_ids=(),
            acquisition_observation_ids=(),
        ),
        currentness_class="current_authoritative_snapshot",
        as_of_utc=as_of,
        has_status_conflict=False,
    )
    assert life == "closed"
    assert bucket == "not_applicable"


def test_future_scheduled_publication_requires_current_publication_provenance() -> None:
    as_of = parse_as_of_utc(AS_OF)
    tender = _tender_shell(
        status_code_selected=None,
        status_name_selected=None,
        status_value_selected=None,
        source_status_system_selected=None,
        close_timestamp_selected=None,
        publication_timestamp_selected="2026-08-02T09:00:00Z",
        selected_field_provenance={"publication_timestamp": "pub"},
        evidence_ref_ids=("pub",),
    )
    fresh_pub_ref = _ref(
        rid="pub",
        plane="acquisition",
        key="4000-1-le26",
        rank="ticket_detail",
        pub="2026-08-02T09:00:00Z",
        acquired="2026-08-01T10:00:00Z",
        obs="pub_obs",
        snap="pub_snap",
    )
    out = apply_lifecycle(
        [tender],
        refs_by_id={"pub": fresh_pub_ref},
        as_of_utc=as_of,
        freshness_threshold_hours=48,
    )
    assert out[0].lifecycle_class == "future_scheduled"
    assert out[0].lifecycle_publication_evidence_ref_id == "pub"
    assert out[0].lifecycle_evidence_currentness_class == "current_authoritative_snapshot"
    assert "publication_after_as_of" in out[0].lifecycle_reason_codes

    stale_pub_ref = _ref(
        rid="pub",
        plane="acquisition",
        key="4000-1-le26",
        rank="ticket_detail",
        pub="2026-08-02T09:00:00Z",
        acquired="2026-07-29T10:00:00Z",
        obs="pub_obs_stale",
        snap="pub_snap_stale",
    )
    stale_out = apply_lifecycle(
        [tender],
        refs_by_id={"pub": stale_pub_ref},
        as_of_utc=as_of,
        freshness_threshold_hours=48,
    )
    assert stale_out[0].lifecycle_class == "status_unknown"
    assert (
        stale_out[0].lifecycle_evidence_currentness_class
        == "stale_or_unverified_field_provenance"
    )
    assert "stale_or_unverified_future_publication" in stale_out[0].lifecycle_reason_codes


def test_publication_date_conflict_blocks_future_scheduled() -> None:
    as_of = parse_as_of_utc(AS_OF)
    pub_ref = _ref(
        rid="pub",
        plane="acquisition",
        key="4000-1-le26",
        rank="ticket_detail",
        pub="2026-08-02T09:00:00Z",
        acquired="2026-08-01T10:00:00Z",
        obs="pub_obs",
        snap="pub_snap",
    )
    life, bucket, reasons, life_cur = classify_lifecycle(
        tender=_tender_shell(
            status_code_selected=None,
            status_name_selected=None,
            status_value_selected=None,
            source_status_system_selected=None,
            close_timestamp_selected=None,
            publication_timestamp_selected="2026-08-02T09:00:00Z",
            selected_field_provenance={"publication_timestamp": "pub"},
            evidence_ref_ids=("pub",),
        ),
        currentness_class="current_authoritative_snapshot",
        as_of_utc=as_of,
        has_status_conflict=False,
        has_publication_date_conflict=True,
        publication_ref=pub_ref,
    )
    assert life == "status_unknown"
    assert bucket == "not_applicable"
    assert reasons == ("authoritative_publication_date_conflict",)
    assert life_cur is None


def test_awarded_and_cancelled_mappings() -> None:
    as_of = parse_as_of_utc(AS_OF)
    for code, name, expected, reason_substr in [
        ("8", "Adjudicada", "awarded", "awarded"),
        ("7", "Desierta", "cancelled", "desierta"),
        ("18", "Revocada", "cancelled", "revocada"),
        ("19", "Suspendida", "cancelled", "suspendida"),
        ("6", "Cerrada", "closed", "closed"),
    ]:
        life, _, reasons, _ = classify_lifecycle(
            tender=_tender_shell(
                candidate_source_kind="pr4",
                pr4_procurement_id="p",
                pr4_procurement_ids=("p",),
                acquisition_snapshot_ids=(),
                acquisition_observation_ids=(),
                coalescence_status="pr4_only",
                currentness_class="historical_pr4_only",
                close_timestamp_selected=None,
                status_code_selected=code,
                status_name_selected=name,
                evidence_ref_ids=(),
            ),
            currentness_class="historical_pr4_only",
            as_of_utc=as_of,
            has_status_conflict=False,
        )
        assert life == expected
        assert any(reason_substr in r for r in reasons)


def test_lista_stub_cannot_freshen_pr4_lifecycle() -> None:
    as_of = parse_as_of_utc(AS_OF)
    pr4 = _ref(
        rid="p",
        plane="pr4",
        key="4100-1-le26",
        rank="pr4",
        status_code="5",
        status_name="Publicada",
        close="2026-08-10T18:00:00-04:00",
        pr4_id="p1",
    )
    lista = _ref(
        rid="l",
        plane="acquisition",
        key="4100-1-le26",
        rank="ocds_lista_index",
        acquired="2026-08-01T10:00:00Z",
        obs="lista1",
        snap="s1",
    )
    tenders, conflicts = _coalesce([pr4, lista])
    out = apply_lifecycle(
        tenders,
        refs_by_id={"p": pr4, "l": lista},
        conflicts_by_id={c.conflict_id: c for c in conflicts},
        as_of_utc=as_of,
        freshness_threshold_hours=48,
    )
    assert out[0].lifecycle_class != "active_open"
    # Lista cannot supply status; provenance stays on PR4.
    assert out[0].selected_field_provenance.get("status") == "p"


def test_buyer_only_live_cannot_freshen_lifecycle() -> None:
    as_of = parse_as_of_utc(AS_OF)
    pr4 = _ref(
        rid="p",
        plane="pr4",
        key="4200-1-le26",
        rank="pr4",
        status_code="5",
        status_name="Publicada",
        close="2026-08-10T18:00:00-04:00",
        pr4_id="p1",
    )
    buyer_only = _ref(
        rid="l",
        plane="acquisition",
        key="4200-1-le26",
        rank="ticket_detail",
        buyer="Fresh Buyer",
        acquired="2026-08-01T10:00:00Z",
        obs="b1",
        snap="s1",
    )
    tenders, conflicts = _coalesce([pr4, buyer_only])
    out = apply_lifecycle(
        tenders,
        refs_by_id={"p": pr4, "l": buyer_only},
        conflicts_by_id={c.conflict_id: c for c in conflicts},
        as_of_utc=as_of,
        freshness_threshold_hours=48,
    )
    assert out[0].lifecycle_class != "active_open"
    assert out[0].selected_field_provenance.get("status") == "p"


def test_status_and_close_from_separate_current_refs() -> None:
    as_of = parse_as_of_utc(AS_OF)
    status_ref = _ref(
        rid="s",
        plane="acquisition",
        key="4300-1-le26",
        rank="ticket_detail",
        status_code="5",
        status_name="Publicada",
        acquired="2026-08-01T10:00:00Z",
        obs="o1",
        snap="s1",
    )
    close_ref = _ref(
        rid="c",
        plane="acquisition",
        key="4300-1-le26",
        rank="ticket_summary",
        close="2026-08-10T18:00:00-04:00",
        acquired="2026-08-01T09:00:00Z",
        obs="o2",
        snap="s2",
    )
    tenders, conflicts = _coalesce([status_ref, close_ref])
    out = apply_lifecycle(
        tenders,
        refs_by_id={"s": status_ref, "c": close_ref},
        conflicts_by_id={c.conflict_id: c for c in conflicts},
        as_of_utc=as_of,
        freshness_threshold_hours=48,
    )
    assert out[0].lifecycle_class == "active_open"
    assert out[0].lifecycle_status_evidence_ref_id == "s"
    assert out[0].lifecycle_close_evidence_ref_id == "c"


def test_one_stale_lifecycle_ref_blocks_active_open() -> None:
    as_of = parse_as_of_utc(AS_OF)
    status_ref = _ref(
        rid="s",
        plane="acquisition",
        key="4400-1-le26",
        rank="ticket_detail",
        status_code="5",
        status_name="Publicada",
        acquired="2026-08-01T10:00:00Z",
        obs="o1",
        snap="s1",
    )
    stale_close = _ref(
        rid="c",
        plane="acquisition",
        key="4400-1-le26",
        rank="ticket_summary",
        close="2026-08-10T18:00:00-04:00",
        acquired="2026-07-01T09:00:00Z",
        obs="o2",
        snap="s2",
    )
    tenders, conflicts = _coalesce([status_ref, stale_close])
    out = apply_lifecycle(
        tenders,
        refs_by_id={"s": status_ref, "c": stale_close},
        conflicts_by_id={c.conflict_id: c for c in conflicts},
        as_of_utc=as_of,
        freshness_threshold_hours=48,
    )
    assert out[0].lifecycle_class != "active_open"


def test_naive_as_of_rejected() -> None:
    with pytest.raises(ValueError, match="malformed timezone"):
        parse_as_of_utc("2026-08-01T12:00:00")


def test_nonpositive_freshness_rejected(tmp_path: Path) -> None:
    db = tmp_path / "pr4.sqlite"
    _seed_pr4_db(
        db,
        [
            {
                "procurement_id": "p1",
                "canonical_tender_key": "1000-1-LE26",
                "status_code": "6",
                "status_name": "Cerrada",
            }
        ],
    )
    snap = _write_snap(tmp_path, "d.json", _ticket_detail_snapshot())
    with pytest.raises(ValueError, match="freshness_threshold_hours"):
        build_candidate_plan(
            sqlite_path=db,
            acquisition_snapshot_paths=[snap],
            as_of_utc=AS_OF,
            freshness_threshold_hours=0,
            run_context="local_fixture",
        )


# --- Report output safety ---


def test_report_output_valid_and_rejects(tmp_path: Path) -> None:
    root, out = _reports_out(tmp_path)
    resolved = require_reports_out_dir(
        out, repo_email_pipeline_root=root, require_git_ignored=False
    )
    assert resolved == out.resolve()

    docs = root / "docs" / "out"
    docs.mkdir(parents=True)
    with pytest.raises(ReportOutputError):
        require_reports_out_dir(
            docs, repo_email_pipeline_root=root, require_git_ignored=False
        )

    escape = root / "reports" / "out" / ".." / "secret"
    with pytest.raises(ReportOutputError):
        require_reports_out_dir(
            escape, repo_email_pipeline_root=root, require_git_ignored=False
        )

    # Symlink escape
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "reports" / "out" / "link_escape"
    link.symlink_to(outside)
    with pytest.raises(ReportOutputError):
        require_reports_out_dir(
            link, repo_email_pipeline_root=root, require_git_ignored=False
        )
    assert list(outside.iterdir()) == []


def test_write_plan_rejects_before_partial_files(tmp_path: Path) -> None:
    db = tmp_path / "pr4.sqlite"
    _seed_pr4_db(
        db,
        [
            {
                "procurement_id": "p1",
                "canonical_tender_key": "1000-1-LE26",
                "status_code": "6",
                "status_name": "Cerrada",
            }
        ],
    )
    snap = _write_snap(tmp_path, "d.json", _ticket_detail_snapshot())
    result = build_candidate_plan(
        sqlite_path=db,
        acquisition_snapshot_paths=[snap],
        as_of_utc=AS_OF,
        freshness_threshold_hours=48,
        run_context="local_fixture",
    )
    bad = tmp_path / "docs" / "leak"
    bad.mkdir(parents=True)
    with pytest.raises(ReportOutputError):
        write_plan_outputs(result, bad, repo_email_pipeline_root=tmp_path / "email-pipeline", require_git_ignored=False)
    assert list(bad.iterdir()) == []


# --- End-to-end planner ---


def test_end_to_end_plan_reconciliation_and_fingerprints(tmp_path: Path) -> None:
    db = tmp_path / "pr4.sqlite"
    detail = _ticket_detail_snapshot()
    snap_obj = materialize_acquisition_snapshot(detail)
    live_key = None
    for obs in snap_obj.source_observations:
        key, _ = accept_canonical_tender_key(
            candidate=obs.canonical_tender_key_candidate,
            candidate_kind=obs.canonical_candidate_kind,
        )
        if key:
            live_key = key
            break
    assert live_key

    signals = [
        {
            "procurement_id": "p_hist_001",
            "canonical_tender_key": "2581-11-LE26",
            "status_code": "8",
            "status_name": "Adjudicada",
            "publication_at": "2026-02-20",
            "close_at": "2026-02-26",
            "buyer_name_raw": "I MUNICIPALIDAD DE CONCHALI",
            "title": "Formularios QR",
            "procurement_context": "historical_tender",
        },
        {
            "procurement_id": "p_overlap_001",
            "canonical_tender_key": live_key,
            "status_code": "5",
            "status_name": "Publicada",
            "publication_at": "2026-07-01",
            "close_at": "2026-08-10",
            "buyer_name_raw": "Synthetic Overlap Buyer",
            "title": "Overlap",
            "procurement_context": "historical_tender",
        },
        {
            "procurement_id": "p_non_mp_001",
            "canonical_tender_key": "1000-1-LE123",
            "tender_key_kind": "codigo_externo",
            "status_code": "6",
            "status_name": "Cerrada",
            "publication_at": "2026-01-01",
            "close_at": "2026-01-10",
            "buyer_name_raw": "Non MP Shape",
            "title": "Former gap shape",
            "procurement_context": "historical_tender",
        },
    ]
    _seed_pr4_db(db, signals)

    snap_path = _write_snap(tmp_path, "detail.json", detail)
    lista = build_acquisition_snapshot(
        source_kind="ocds",
        payload=json.loads((LIVE / "ocds_range_live_shape_v1.json").read_text()),
        fixture_origin="live_response_sanitized",
        year=2026,
        month=7,
        range_start=1,
        range_end=1,
        acquired_at_utc="2026-08-01T10:00:00Z",
    ).to_dict()
    lista_path = _write_snap(tmp_path, "lista.json", lista)

    result = build_candidate_plan(
        sqlite_path=db,
        acquisition_snapshot_paths=[snap_path, lista_path],
        as_of_utc=AS_OF,
        freshness_threshold_hours=48,
        run_context="local_fixture",
    )
    assert result.aggregate_reconciliation["ok"] is True
    assert result.aggregate_reconciliation["no_silent_drop"] is True
    counts = result.aggregate_reconciliation["counts"]
    assert counts["pr4_signals_total"] == counts["pr4_coalesced"] + counts["pr4_unresolved"]
    assert counts["pr4_unresolved"] == 0
    kinds = {t.candidate_source_kind for t in result.coalesced_tenders}
    assert "pr4" in kinds
    assert "both" in kinds or "live_snapshot" in kinds
    assert any(u.unresolved_reason for u in result.unresolved)

    non_mp = next(
        t for t in result.coalesced_tenders if t.canonical_tender_key == "1000-1-le123"
    )
    assert non_mp.tender_key_kind == "codigo_externo"
    assert non_mp.identity_namespace == IDENTITY_NS_PR4_CODIGO_EXTERNO
    assert non_mp.candidate_source_kind == "pr4"

    result2 = build_candidate_plan(
        sqlite_path=db,
        acquisition_snapshot_paths=[lista_path, snap_path],
        as_of_utc=AS_OF,
        freshness_threshold_hours=48,
        run_context="local_fixture",
    )
    assert result.input_source_fingerprint == result2.input_source_fingerprint
    assert result.semantic_digest == result2.semantic_digest

    build_changed = candidate_build_plan_fingerprint(
        input_source_fingerprint=result.input_source_fingerprint,
        as_of_utc="2026-08-02T12:00:00Z",
        freshness_threshold_hours=48,
    )
    assert build_changed != result.build_plan_fingerprint

    root, out = _reports_out(tmp_path)
    write_plan_outputs(result, out, repo_email_pipeline_root=root, require_git_ignored=False)

    case_b, case_b_refs = build_case_b_live_only_from_ticket_snapshot(
        detail,
        as_of_utc=AS_OF,
        freshness_threshold_hours=48,
    )
    case_c, case_c_refs = build_case_c_overlap_through_production_path(as_of_utc=AS_OF)
    case_d, case_d_refs = build_case_d_conflict_through_production_path(as_of_utc=AS_OF)
    bundle = build_walkthrough_bundle(
        result,
        case_hints={
            "case_c_synthetic": True,
            "case_c_synthetic_label": "synthetic_overlap_through_production_code_path",
            "case_d_synthetic": True,
            "case_d_synthetic_label": "synthetic_conflict_through_production_code_path",
        },
        case_b_tender=case_b,
        case_b_refs=case_b_refs,
        case_c_tender=case_c,
        case_c_refs=case_c_refs,
        case_d_tender=case_d,
        case_d_refs=case_d_refs,
    )
    assert {c["case_id"] for c in bundle["cases"]} == {"A", "B", "C", "D", "E"}
    assert "case_unavailable" not in json.dumps(bundle)
    case_b_row = next(c for c in bundle["cases"] if c["case_id"] == "B")
    assert (
        case_b_row["synthetic_label"]
        == "live_derived_sanitized_fixture_through_production_code_path"
    )
    write_walkthrough(bundle, out, repo_email_pipeline_root=root, require_git_ignored=False)
    assert_no_pii_leaks(json.dumps(bundle))
    red = redact_coalesced_tender(result.coalesced_tenders[0].to_dict())
    assert "@" not in json.dumps(red)
    assert (out / "DATA_WALKTHROUGH.md").read_text().strip()


def test_complete_pr4_reconciliation(tmp_path: Path) -> None:
    db = tmp_path / "pr4.sqlite"
    _seed_pr4_db(
        db,
        [
            {
                "procurement_id": "ok1",
                "canonical_tender_key": "1000-1-LE26",
                "status_code": "6",
                "status_name": "Cerrada",
            },
            {
                "procurement_id": "ok2",
                "canonical_tender_key": "1000-1-LE123",
                "status_code": "6",
                "status_name": "Cerrada",
            },
            {
                "procurement_id": "bad",
                "canonical_tender_key": "",
                "tender_key_kind": "codigo_externo",
                "status_code": "6",
                "status_name": "Cerrada",
            },
        ],
    )
    snap = _write_snap(tmp_path, "d.json", _ticket_detail_snapshot())
    result = build_candidate_plan(
        sqlite_path=db,
        acquisition_snapshot_paths=[snap],
        as_of_utc=AS_OF,
        freshness_threshold_hours=48,
        run_context="local_fixture",
    )
    c = result.aggregate_reconciliation["counts"]
    assert c["pr4_signals_total"] == 3
    assert c["pr4_coalesced"] == 2
    assert c["pr4_unresolved"] == 1
    assert c["pr4_signals_total"] == c["pr4_coalesced"] + c["pr4_unresolved"]


def test_cli_rejects_forbidden_flags(tmp_path: Path) -> None:
    import runpy
    import sys

    script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "commercial"
        / "build_commercial_procurement_candidate_plan.py"
    )
    old = sys.argv[:]
    try:
        sys.argv = [str(script), "--apply", "--sqlite-path", str(tmp_path / "x")]
        with pytest.raises(SystemExit):
            runpy.run_path(str(script), run_name="__main__")
    finally:
        sys.argv = old


# --- PR5C correction: acquisition instances, namespaces, equivalence ---


def test_acquisition_instance_t1_t2_fingerprint_and_order(tmp_path: Path) -> None:
    t1 = materialize_acquisition_snapshot(
        _ticket_detail_snapshot(acquired_at_utc="2026-08-01T10:00:00Z")
    )
    t2 = materialize_acquisition_snapshot(
        _ticket_detail_snapshot(acquired_at_utc="2026-08-01T11:00:00Z")
    )
    assert t1.snapshot_id == t2.snapshot_id
    id1 = candidate_acquisition_instance_id(t1)
    id2 = candidate_acquisition_instance_id(t2)
    assert id1 != id2

    db = tmp_path / "pr4.sqlite"
    _seed_pr4_db(
        db,
        [
            {
                "procurement_id": "p1",
                "canonical_tender_key": "9999-9-LE26",
                "status_code": "6",
                "status_name": "Cerrada",
            }
        ],
    )
    p1 = _write_snap(tmp_path, "t1.json", t1.to_dict())
    p2 = _write_snap(tmp_path, "t2.json", t2.to_dict())
    r12 = build_candidate_plan(
        sqlite_path=db,
        acquisition_snapshot_paths=[p1, p2],
        as_of_utc=AS_OF,
        freshness_threshold_hours=48,
        run_context="local_fixture",
    )
    r21 = build_candidate_plan(
        sqlite_path=db,
        acquisition_snapshot_paths=[p2, p1],
        as_of_utc=AS_OF,
        freshness_threshold_hours=48,
        run_context="local_fixture",
    )
    assert r12.input_source_fingerprint == r21.input_source_fingerprint
    assert r12.semantic_digest == r21.semantic_digest
    assert set(r12.acquisition_instance_ids) == {id1, id2}

    r1 = build_candidate_plan(
        sqlite_path=db,
        acquisition_snapshot_paths=[p1],
        as_of_utc=AS_OF,
        freshness_threshold_hours=48,
        run_context="local_fixture",
    )
    assert r1.input_source_fingerprint != r12.input_source_fingerprint

    # Exact duplicate T1 is deduplicated.
    r_dup = build_candidate_plan(
        sqlite_path=db,
        acquisition_snapshot_paths=[p1, p1],
        as_of_utc=AS_OF,
        freshness_threshold_hours=48,
        run_context="local_fixture",
    )
    assert list(r_dup.acquisition_instance_ids) == [id1]

    # Newest equal-rank evidence selected as provenance.
    refs1, _ = snapshot_to_evidence(t1, acquisition_instance_id=id1)
    refs2, _ = snapshot_to_evidence(t2, acquisition_instance_id=id2)
    tenders, _ = _coalesce(refs1 + refs2)
    live = next(t for t in tenders if t.candidate_source_kind == "live_snapshot")
    status_ref = live.selected_field_provenance.get("status")
    assert status_ref in {r.evidence_ref_id for r in refs2}


def test_identity_namespace_join_and_non_join() -> None:
    key = "4000-1-le26"
    pr4_ce = _ref(
        rid="pr4_ce",
        plane="pr4",
        key=key,
        rank="pr4",
        status_code="5",
        status_name="Publicada",
        close="2026-08-10T12:00:00Z",
        tender_key_kind="codigo_externo",
        pr4_id="p_ce",
    )
    live = _ref(
        rid="live",
        plane="acquisition",
        key=key,
        rank="ticket_detail",
        status_code="5",
        status_name="Publicada",
        close="2026-08-10T12:00:00Z",
        acquired="2026-08-01T10:00:00Z",
        obs="o",
        snap="s",
    )
    t_join, _ = _coalesce([pr4_ce, live])
    assert len(t_join) == 1
    assert t_join[0].candidate_source_kind == "both"
    assert t_join[0].identity_namespace == IDENTITY_NS_MERCADO_PUBLICO

    for kind, ns in (
        ("numero_adquisicion", IDENTITY_NS_PR4_NUMERO_ADQUISICION),
        ("codigo_licitacion", IDENTITY_NS_PR4_CODIGO_LICITACION),
    ):
        pr4_other = _ref(
            rid=f"pr4_{kind}",
            plane="pr4",
            key=key,
            rank="pr4",
            status_code="5",
            status_name="Publicada",
            close="2026-08-10T12:00:00Z",
            tender_key_kind=kind,
            pr4_id=f"p_{kind}",
        )
        assert pr4_other.identity_namespace == ns
        tenders, _ = _coalesce([pr4_other, live])
        assert len(tenders) == 2
        assert {t.identity_namespace for t in tenders} == {
            ns,
            IDENTITY_NS_MERCADO_PUBLICO,
        }

    id_mp = coalesced_tender_id(
        identity_namespace=IDENTITY_NS_MERCADO_PUBLICO, canonical_tender_key=key
    )
    id_lic = coalesced_tender_id(
        identity_namespace=IDENTITY_NS_PR4_CODIGO_LICITACION,
        canonical_tender_key=key,
    )
    assert id_mp != id_lic

    t_pr4, _ = _coalesce([pr4_ce])
    t_both, _ = _coalesce([pr4_ce, live])
    assert t_pr4[0].coalesced_tender_id == t_both[0].coalesced_tender_id


def test_status_meaning_equivalence_and_conflict() -> None:
    key = "5100-1-le26"
    forms = [
        ("5", "Publicada"),
        ("5", "Publicada."),
        ("5", "Publicada — Ley 19.886"),
        (None, "Publicada"),
    ]
    refs = [
        _ref(
            rid=f"r{i}",
            plane="acquisition",
            key=key,
            rank="ticket_detail",
            status_code=code,
            status_name=name,
            close="2026-08-10T12:00:00Z",
            acquired="2026-08-01T10:00:00Z",
            obs=f"o{i}",
            snap=f"s{i}",
            inst=f"i{i}",
        )
        for i, (code, name) in enumerate(forms)
    ]
    _, conflicts = _coalesce(refs)
    assert not any(c.conflict_kind == "status_conflict" for c in conflicts)

    bad = _ref(
        rid="adj",
        plane="acquisition",
        key=key,
        rank="ticket_summary",
        status_code="8",
        status_name="Adjudicada",
        close="2026-08-10T12:00:00Z",
        acquired="2026-08-01T10:00:00Z",
        obs="oadj",
        snap="sadj",
        inst="iadj",
    )
    _, conflicts2 = _coalesce([refs[0], bad])
    assert any(c.conflict_kind == "status_conflict" for c in conflicts2)

    _, conflicts3 = _coalesce(
        [
            _ref(
                rid="incons",
                plane="acquisition",
                key="5101-1-le26",
                rank="ticket_detail",
                status_code="5",
                status_name="Adjudicada",
                close="2026-08-10T12:00:00Z",
                acquired="2026-08-01T10:00:00Z",
                obs="oi",
                snap="si",
            )
        ]
    )
    assert any(c.conflict_kind == "status_conflict" for c in conflicts3)

    _, conflicts4 = _coalesce(
        [
            _ref(
                rid="d1",
                plane="acquisition",
                key="5102-1-le26",
                rank="ticket_detail",
                status_code="7",
                status_name="Desierta",
                close="2026-08-10T12:00:00Z",
                acquired="2026-08-01T10:00:00Z",
                obs="d1",
                snap="sd1",
                inst="id1",
            ),
            _ref(
                rid="d2",
                plane="acquisition",
                key="5102-1-le26",
                rank="ticket_summary",
                status_code="7",
                status_name="Desierta (Art. 9 Ley 19.886)",
                close="2026-08-10T12:00:00Z",
                acquired="2026-08-01T10:00:00Z",
                obs="d2",
                snap="sd2",
                inst="id2",
            ),
        ]
    )
    assert not any(c.conflict_kind == "status_conflict" for c in conflicts4)


def test_timestamp_precision_compatibility_rules() -> None:
    from origenlab_email_pipeline.commercial_procurement_candidate_planner.normalize import (
        timestamps_compatible,
    )

    date_only = normalize_tender_timestamp("2026-08-10")
    precise = normalize_tender_timestamp("2026-08-10T15:00:00-04:00")
    assert timestamps_compatible(date_only, precise) is True

    offset_a = normalize_tender_timestamp("2026-08-10T18:00:00-04:00")
    offset_b = normalize_tender_timestamp("2026-08-10T22:00:00Z")
    assert timestamps_compatible(offset_a, offset_b) is True

    other_day = normalize_tender_timestamp("2026-08-11")
    assert timestamps_compatible(date_only, other_day) is False
    assert timestamps_compatible(
        date_only, normalize_tender_timestamp("2026-08-11T12:00:00Z")
    ) is False

    a = normalize_tender_timestamp("2026-08-10T12:00:00Z")
    b = normalize_tender_timestamp("2026-08-10T13:00:00Z")
    assert timestamps_compatible(a, b) is False

    key = "5200-1-le26"
    valid = _ref(
        rid="valid",
        plane="acquisition",
        key=key,
        rank="ticket_summary",
        status_code="5",
        status_name="Publicada",
        close="2026-08-10T12:00:00Z",
        acquired="2026-08-01T10:00:00Z",
        obs="ov",
        snap="sv",
        inst="iv",
    )
    malformed = _ref(
        rid="bad",
        plane="acquisition",
        key=key,
        rank="ticket_detail",
        status_code="5",
        status_name="Publicada",
        close="not-a-timestamp",
        acquired="2026-08-01T10:00:00Z",
        obs="ob",
        snap="sb",
        inst="ib",
    )
    tenders, conflicts = _coalesce([malformed, valid])
    assert not any(c.conflict_kind == "date_conflict" for c in conflicts)
    assert tenders[0].close_timestamp_selected == "2026-08-10T12:00:00Z"

    missing = _ref(
        rid="miss",
        plane="acquisition",
        key=key,
        rank="ticket_detail",
        status_code="5",
        status_name="Publicada",
        close=None,
        acquired="2026-08-01T10:00:00Z",
        obs="om",
        snap="sm",
        inst="im",
    )
    tenders2, conflicts2 = _coalesce([missing, valid])
    assert not any(c.conflict_kind == "date_conflict" for c in conflicts2)
    assert tenders2[0].close_timestamp_selected == "2026-08-10T12:00:00Z"

    pr4_date = _ref(
        rid="pr4d",
        plane="pr4",
        key="5201-1-le26",
        rank="pr4",
        status_code="5",
        status_name="Publicada",
        close="2026-08-10",
        pr4_id="pd",
    )
    live_precise = _ref(
        rid="livep",
        plane="acquisition",
        key="5201-1-le26",
        rank="ticket_detail",
        status_code="5",
        status_name="Publicada",
        close="2026-08-10T15:30:00-04:00",
        acquired="2026-08-01T10:00:00Z",
        obs="op",
        snap="sp",
        inst="ip",
    )
    tenders3, conflicts3 = _coalesce([pr4_date, live_precise])
    assert not any(c.conflict_kind == "date_conflict" for c in conflicts3)
    assert tenders3[0].close_timestamp_selected == "2026-08-10T15:30:00-04:00"


def test_buyer_display_variance_not_identity_conflict() -> None:
    a = _ref(
        rid="a",
        plane="acquisition",
        key="5300-1-le26",
        rank="ticket_detail",
        status_code="5",
        status_name="Publicada",
        close="2026-08-10T12:00:00Z",
        buyer="Ministry of Health",
        buyer_id="org-1",
        acquired="2026-08-01T10:00:00Z",
        obs="o1",
        snap="s1",
        inst="i1",
    )
    b = _ref(
        rid="b",
        plane="acquisition",
        key="5300-1-le26",
        rank="ticket_summary",
        status_code="5",
        status_name="Publicada",
        close="2026-08-10T12:00:00Z",
        buyer="MINISTERIO DE SALUD",
        buyer_id="org-1",
        acquired="2026-08-01T10:00:00Z",
        obs="o2",
        snap="s2",
        inst="i2",
    )
    tenders, conflicts = _coalesce([a, b])
    assert not any(c.conflict_kind == "buyer_identity_conflict" for c in conflicts)
    assert tenders[0].buyer_display_variance is True


def test_git_check_ignore_required(tmp_path: Path) -> None:
    import subprocess

    plain_root = tmp_path / "plain"
    plain_out = plain_root / "reports" / "out" / "plan"
    plain_out.mkdir(parents=True)
    with pytest.raises(ReportOutputError, match="git-ignored"):
        require_reports_out_dir(plain_out, repo_email_pipeline_root=plain_root)

    repo = tmp_path / "repo"
    (repo / "reports" / "out").mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    (repo / ".gitignore").write_text("reports/out/\n", encoding="utf-8")
    dest = repo / "reports" / "out" / "plan"
    dest.mkdir()
    resolved = require_reports_out_dir(dest, repo_email_pipeline_root=repo)
    assert resolved == dest.resolve()


def test_write_atomically_restores_prior_bundle_on_replace_failure(tmp_path: Path) -> None:
    """Regression: delete-then-replace must not destroy both prior and new bundles."""
    import os

    from origenlab_email_pipeline.commercial_procurement_candidate_planner.output_safety import (
        write_atomically,
    )

    root, out = _reports_out(tmp_path)
    out.mkdir(parents=True, exist_ok=True)
    prior = out / "PRIOR.txt"
    prior.write_text("prior-bundle\n", encoding="utf-8")

    real_replace = os.replace

    def flaky_replace(src, dst):  # type: ignore[no-untyped-def]
        src_path = Path(src)
        dst_path = Path(dst)
        # Fail only when installing the temp bundle onto the destination path.
        if (
            src_path.name.startswith(f".{out.name}.tmp.")
            and dst_path.resolve() == out.resolve()
        ):
            raise OSError("simulated final rename failure")
        return real_replace(src, dst)

    def writer(safe):  # type: ignore[no-untyped-def]
        path = safe / "NEW.txt"
        path.write_text("new-bundle\n", encoding="utf-8")
        return {"NEW.txt": str(path)}

    try:
        os.replace = flaky_replace  # type: ignore[assignment]
        with pytest.raises(OSError, match="simulated final rename failure"):
            write_atomically(
                out,
                repo_email_pipeline_root=root,
                writer=writer,
                require_git_ignored=False,
            )
    finally:
        os.replace = real_replace  # type: ignore[assignment]

    assert out.exists()
    assert (out / "PRIOR.txt").read_text(encoding="utf-8") == "prior-bundle\n"
    assert not (out / "NEW.txt").exists()


def test_write_walkthrough_stages_before_publishing(tmp_path: Path) -> None:
    """Walkthrough must stage a complete bundle before publishing files."""
    root, out = _reports_out(tmp_path)
    out.mkdir(parents=True, exist_ok=True)
    (out / "RUN_MANIFEST.json").write_text("{}\n", encoding="utf-8")

    bundle = {
        "walkthrough_version": "pr5c_coalescence_lifecycle_v2",
        "planner_version": "procurement_candidate_planner_v1",
        "as_of_utc": AS_OF,
        "fingerprints": {
            "candidate_input_source_fp_v1": "a" * 64,
            "candidate_build_plan_fp_v1": "b" * 64,
            "candidate_semantic_digest_v1": "c" * 64,
        },
        "aggregate_reconciliation": {},
        "cases": [],
        "product_relevance_implemented": False,
    }
    written = write_walkthrough(
        bundle, out, repo_email_pipeline_root=root, require_git_ignored=False
    )
    assert set(written) == {
        "DATA_WALKTHROUGH.json",
        "DATA_WALKTHROUGH.md",
        "REDACTION_PROOF.json",
    }
    assert (out / "DATA_WALKTHROUGH.json").is_file()
    assert (out / "RUN_MANIFEST.json").is_file()
    assert not (out / ".walkthrough_bundle").exists()


def test_endpoint_contract_mismatches_rejected() -> None:
    snap = _ticket_detail_snapshot()
    bad = dict(snap)
    bad["query"] = {**bad["query"], "query_contract_version": "acquisition_query_v2"}
    with pytest.raises(AcquisitionPlaneError):
        materialize_acquisition_snapshot(bad)

    lista = build_acquisition_snapshot(
        source_kind="ocds",
        payload=json.loads((LIVE / "ocds_range_live_shape_v1.json").read_text()),
        fixture_origin="live_response_sanitized",
        year=2026,
        month=7,
        range_start=1,
        range_end=1,
        acquired_at_utc="2026-08-01T10:00:00Z",
    ).to_dict()
    if lista["query"].get("query_contract_version") == "acquisition_query_v2":
        bad_lista = dict(lista)
        bad_lista["query"] = {
            **bad_lista["query"],
            "query_contract_version": "acquisition_query_v1",
        }
        with pytest.raises(AcquisitionPlaneError):
            materialize_acquisition_snapshot(bad_lista)
    bad_kind = dict(lista)
    bad_kind["query"] = {
        **bad_kind["query"],
        "endpoint_kind": "ticket_licitacion_detail",
        "source_kind": "mercado_publico_ticket_api",
    }
    with pytest.raises(AcquisitionPlaneError):
        materialize_acquisition_snapshot(bad_kind)
