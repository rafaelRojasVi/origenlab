"""Redacted walkthrough cases A–E for PR5C coalescence / lifecycle."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.commercial_procurement_acquisition.snapshot import (
    build_acquisition_snapshot,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    CandidatePlanResult,
    CoalescedProcurementTender,
    UnresolvedProcurementEvidence,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.output_safety import (
    require_reports_out_dir,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.redaction import (
    assert_no_pii_leaks,
    redact_coalesced_tender,
    redact_evidence_ref,
    redact_unresolved,
)


def _case_shell(
    *,
    case_id: str,
    label: str,
    synthetic: bool,
    synthetic_label: str | None,
    steps: list[dict[str, Any]],
    planned_rows: dict[str, Any],
    reconciliation_impact: dict[str, Any],
    fingerprint_contribution: dict[str, Any],
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "label": label,
        "synthetic": synthetic,
        "synthetic_label": synthetic_label,
        "steps": steps,
        "planned_rows": planned_rows,
        "reconciliation_impact": reconciliation_impact,
        "fingerprint_contribution": fingerprint_contribution,
        "relevance_considered": False,
    }


def _identity_route(tender: CoalescedProcurementTender) -> dict[str, Any]:
    return {
        "accepted_identity_namespace": tender.identity_namespace,
        "accepted_tender_key_kind_provenance": tender.tender_key_kind,
        "rejected": [
            "ocid_alone",
            "source_native_alone",
            "title_similarity",
            "buyer_name_similarity",
            "cross_kind_text_match_without_namespace",
        ],
    }


def _tender_case(
    *,
    case_id: str,
    label: str,
    tender: CoalescedProcurementTender,
    refs: dict[str, Any],
    synthetic: bool,
    synthetic_label: str | None,
) -> dict[str, Any]:
    evidence = [
        redact_evidence_ref(refs[i].to_dict())
        for i in tender.evidence_ref_ids
        if i in refs
    ]
    red_t = redact_coalesced_tender(tender.to_dict())
    return _case_shell(
        case_id=case_id,
        label=label,
        synthetic=synthetic,
        synthetic_label=synthetic_label,
        steps=[
            {
                "stage": "source_planes",
                "planes": sorted({e["evidence_plane"] for e in evidence}),
            },
            {
                "stage": "field_provenance_matrix",
                "selected_field_provenance": red_t.get("selected_field_provenance"),
                "display_safe": {
                    "canonical_tender_key": red_t.get("canonical_tender_key"),
                    "buyer_display_selected": red_t.get("buyer_display_selected"),
                    "status_name_selected": red_t.get("status_name_selected"),
                },
                "resolver_safe": {
                    "identity_namespace": red_t.get("identity_namespace"),
                    "canonical_tender_key": red_t.get("canonical_tender_key"),
                    "tender_key_kind": red_t.get("tender_key_kind"),
                },
            },
            {
                "stage": "routes_considered_rejected",
                **_identity_route(tender),
            },
            {
                "stage": "coalescence_decision",
                "coalescence_status": tender.coalescence_status,
                "candidate_source_kind": tender.candidate_source_kind,
                "source_precedence_reason": tender.source_precedence_reason,
            },
            {
                "stage": "freshness_lifecycle",
                "currentness_class": tender.currentness_class,
                "lifecycle_class": tender.lifecycle_class,
                "closing_soon_bucket": tender.closing_soon_bucket,
                "lifecycle_reason_codes": list(tender.lifecycle_reason_codes),
                "lifecycle_status_evidence_ref_id": tender.lifecycle_status_evidence_ref_id,
                "lifecycle_close_evidence_ref_id": tender.lifecycle_close_evidence_ref_id,
            },
        ],
        planned_rows={
            "coalesced_tender": red_t,
            "evidence_refs": evidence,
        },
        reconciliation_impact={
            "counts_toward_coalesced_tenders": 1,
            "candidate_source_kind": tender.candidate_source_kind,
            "lifecycle_class": tender.lifecycle_class,
        },
        fingerprint_contribution={
            "canonical_tender_key_redacted": red_t.get("canonical_tender_key"),
            "coalesced_tender_id": tender.coalesced_tender_id,
            "in_semantic_digest": True,
        },
    )


def build_walkthrough_bundle(
    result: CandidatePlanResult,
    *,
    case_hints: dict[str, Any] | None = None,
    case_b_tender: CoalescedProcurementTender | None = None,
    case_b_refs: dict[str, Any] | None = None,
    case_c_tender: CoalescedProcurementTender | None = None,
    case_c_refs: dict[str, Any] | None = None,
    case_d_tender: CoalescedProcurementTender | None = None,
    case_d_refs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build machine-readable walkthrough from a completed plan result.

    Cases B–D must be real planned or explicitly constructed rows through the
    production code path — never ``case_unavailable``.
    """
    hints = case_hints or {}
    meta = dict(result.case_construction_meta or {})
    meta.update(hints)
    tenders = list(result.coalesced_tenders)
    refs = {e.evidence_ref_id: e for e in result.evidence_refs}
    unresolved = list(result.unresolved)

    case_a_tender = next(
        (
            t
            for t in tenders
            if t.coalescence_status == "pr4_only"
            and t.identity_namespace != "mercado_publico_codigo_externo"
        ),
        None,
    )
    if case_a_tender is None:
        case_a_tender = next(
            (t for t in tenders if t.coalescence_status == "pr4_only"),
            None,
        )
    if case_a_tender is None:
        raise ValueError("walkthrough Case A requires a pr4_only tender in the plan")

    if case_b_tender is None:
        case_b_tender = next(
            (t for t in tenders if t.candidate_source_kind == "live_snapshot"),
            None,
        )
    if case_b_tender is None:
        raise ValueError(
            "walkthrough Case B requires a live_snapshot tender "
            "(construct via sanitized Ticket fixture through production path)"
        )
    case_b_ref_map = case_b_refs or {
        i: refs[i] for i in case_b_tender.evidence_ref_ids if i in refs
    }

    if case_c_tender is None:
        case_c_tender = next(
            (
                t
                for t in tenders
                if t.coalescence_status in {"exact_agreement", "live_source_newer"}
                or t.candidate_source_kind == "both"
            ),
            None,
        )
    if case_d_tender is None:
        case_d_tender = next(
            (
                t
                for t in tenders
                if t.coalescence_status
                in {
                    "status_conflict",
                    "date_conflict",
                    "buyer_identity_conflict",
                    "multiple_live_sources_conflict",
                }
            ),
            None,
        )
    case_c_ref_map = case_c_refs or {
        i: refs[i] for i in (case_c_tender.evidence_ref_ids if case_c_tender else ()) if i in refs
    }
    case_d_ref_map = case_d_refs or {
        i: refs[i] for i in (case_d_tender.evidence_ref_ids if case_d_tender else ()) if i in refs
    }
    case_e_unresolved: UnresolvedProcurementEvidence | None = next(
        (
            u
            for u in unresolved
            if u.unresolved_reason
            in {
                "ocds_ocid_only_unresolved",
                "source_native_identity_not_canonical",
                "live_canonical_candidate_missing",
            }
        ),
        unresolved[0] if unresolved else None,
    )
    if case_e_unresolved is None:
        raise ValueError("walkthrough Case E requires unresolved evidence")

    def synth_flag(key: str, default: bool = False) -> bool:
        if key in meta:
            return bool(meta[key])
        return default

    def synth_label(key: str, default: str | None = None) -> str | None:
        if not synth_flag(key):
            return None
        return meta.get(f"{key}_label") or default

    if case_c_tender is None:
        raise ValueError(
            "walkthrough Case C requires an explicit both/agreement tender "
            "(construct via synthetic_overlap_through_production_code_path)"
        )
    if case_d_tender is None:
        raise ValueError(
            "walkthrough Case D requires an explicit conflict tender "
            "(construct via production code path)"
        )

    cases = [
        _tender_case(
            case_id="A",
            label="production_pr4_only_historical_tender",
            tender=case_a_tender,
            refs=refs,
            synthetic=synth_flag("case_a_synthetic", False),
            synthetic_label=synth_label("case_a_synthetic"),
        ),
        _tender_case(
            case_id="B",
            label="sanitized_live_only_ticket_tender",
            tender=case_b_tender,
            refs=case_b_ref_map if case_b_refs else refs,
            synthetic=True,
            synthetic_label="live_derived_sanitized_fixture_through_production_code_path",
        ),
        _tender_case(
            case_id="C",
            label="exact_two_plane_agreement",
            tender=case_c_tender,
            refs=case_c_ref_map if case_c_refs else refs,
            synthetic=synth_flag("case_c_synthetic", False),
            synthetic_label=synth_label(
                "case_c_synthetic",
                "synthetic_overlap_through_production_code_path",
            ),
        ),
        _tender_case(
            case_id="D",
            label="source_conflict",
            tender=case_d_tender,
            refs=case_d_ref_map if case_d_refs else refs,
            synthetic=synth_flag("case_d_synthetic", False),
            synthetic_label=synth_label(
                "case_d_synthetic",
                "synthetic_conflict_through_production_code_path",
            ),
        ),
    ]

    red_u = redact_unresolved(case_e_unresolved.to_dict())
    cases.append(
        _case_shell(
            case_id="E",
            label="unresolved_ocds_identity",
            synthetic=synth_flag("case_e_synthetic", False),
            synthetic_label=synth_label("case_e_synthetic"),
            steps=[
                {
                    "stage": "source_planes",
                    "planes": [case_e_unresolved.evidence_plane],
                },
                {
                    "stage": "canonical_rejection",
                    "unresolved_reason": case_e_unresolved.unresolved_reason,
                    "canonical_candidate_kind": case_e_unresolved.canonical_candidate_kind,
                    "becomes_candidate": False,
                    "becomes_conflict_with_unrelated_tender": False,
                },
            ],
            planned_rows={"unresolved_evidence": red_u},
            reconciliation_impact={
                "counts_toward_unresolved": 1,
                "counts_toward_coalesced_tenders": 0,
            },
            fingerprint_contribution={
                "unresolved_id": case_e_unresolved.unresolved_id,
                "in_input_fingerprint": True,
                "in_semantic_digest": True,
            },
        )
    )

    bundle = {
        "walkthrough_version": "pr5c_coalescence_lifecycle_v2",
        "planner_version": result.planner_version,
        "as_of_utc": result.as_of_utc,
        "fingerprints": {
            "candidate_input_source_fp_v1": result.input_source_fingerprint,
            "candidate_build_plan_fp_v1": result.build_plan_fingerprint,
            "candidate_semantic_digest_v1": result.semantic_digest,
        },
        "aggregate_reconciliation": result.aggregate_reconciliation,
        "cases": cases,
        "product_relevance_implemented": False,
    }
    blob = json.dumps(bundle, sort_keys=True, ensure_ascii=True)
    assert_no_pii_leaks(blob)
    return bundle


def walkthrough_markdown(bundle: dict[str, Any]) -> str:
    lines = [
        "# PR5C coalescence / lifecycle data walkthrough",
        "",
        f"- Planner: `{bundle.get('planner_version')}`",
        f"- As-of UTC: `{bundle.get('as_of_utc')}`",
        f"- Input FP: `{bundle['fingerprints']['candidate_input_source_fp_v1']}`",
        f"- Build-plan FP: `{bundle['fingerprints']['candidate_build_plan_fp_v1']}`",
        f"- Semantic digest: `{bundle['fingerprints']['candidate_semantic_digest_v1']}`",
        "",
        "Product relevance was **not** implemented in this slice.",
        "",
    ]
    for case in bundle.get("cases") or []:
        lines.append(f"## Case {case['case_id']} — {case['label']}")
        if case.get("synthetic"):
            lines.append("")
            lines.append(
                f"_Label: `{case.get('synthetic_label') or 'synthetic'}`_"
            )
        lines.append("")
        for step in case.get("steps") or []:
            lines.append(
                f"- **{step.get('stage', 'note')}**: "
                f"`{json.dumps(step, sort_keys=True)}`"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def write_walkthrough(
    bundle: dict[str, Any],
    out_dir: Path,
    *,
    repo_email_pipeline_root: Path | None = None,
    require_git_ignored: bool = True,
) -> dict[str, str]:
    root = repo_email_pipeline_root
    if root is None:
        root = Path(__file__).resolve().parents[3]

    def _write(safe: Path) -> dict[str, str]:
        json_path = safe / "DATA_WALKTHROUGH.json"
        md_path = safe / "DATA_WALKTHROUGH.md"
        proof_path = safe / "REDACTION_PROOF.json"
        json_path.write_text(
            json.dumps(bundle, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        md_path.write_text(walkthrough_markdown(bundle), encoding="utf-8")
        proof_path.write_text(
            json.dumps(
                {
                    "assert_no_pii_leaks": True,
                    "redaction_algorithm": "procurement_walkthrough_redact_v1",
                    "cases": [c["case_id"] for c in bundle.get("cases") or []],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return {
            "DATA_WALKTHROUGH.json": str(json_path),
            "DATA_WALKTHROUGH.md": str(md_path),
            "REDACTION_PROOF.json": str(proof_path),
        }

    # When writing into an already-populated plan dir (CLI), append without
    # replacing the entire directory — validate containment first via atomic
    # write into a nested walkthrough staging folder then move files.

    safe = require_reports_out_dir(
        out_dir,
        repo_email_pipeline_root=root,
        require_git_ignored=require_git_ignored,
    )
    safe.mkdir(parents=True, exist_ok=True)
    return _write(safe)


def build_case_b_live_only_from_ticket_snapshot(
    snapshot_payload: dict[str, Any],
    *,
    as_of_utc: str,
    freshness_threshold_hours: int = 48,
) -> tuple[CoalescedProcurementTender, dict[str, Any]]:
    """Construct Case B through real Plane B → coalesce → lifecycle (no PR4)."""
    from origenlab_email_pipeline.commercial_procurement_candidate_planner.acquisition_instance import (
        candidate_acquisition_instance_id,
    )
    from origenlab_email_pipeline.commercial_procurement_candidate_planner.coalescence import (
        coalesce_evidence_refs,
    )
    from origenlab_email_pipeline.commercial_procurement_candidate_planner.lifecycle import (
        apply_lifecycle,
    )
    from origenlab_email_pipeline.commercial_procurement_candidate_planner.normalize import (
        parse_as_of_utc,
    )
    from origenlab_email_pipeline.commercial_procurement_candidate_planner.plane_b_acquisition import (
        materialize_acquisition_snapshot,
        snapshot_to_evidence,
    )

    snap = materialize_acquisition_snapshot(snapshot_payload)
    inst_id = candidate_acquisition_instance_id(snap)
    refs, _unres = snapshot_to_evidence(snap, acquisition_instance_id=inst_id)
    if not refs:
        raise ValueError("Case B fixture produced no accepted live evidence refs")
    as_of = parse_as_of_utc(as_of_utc)
    tenders, conflicts = coalesce_evidence_refs(
        refs,
        as_of_utc=as_of,
        freshness_threshold_hours=freshness_threshold_hours,
    )
    refs_by_id = {r.evidence_ref_id: r for r in refs}
    tenders = apply_lifecycle(
        tenders,
        refs_by_id=refs_by_id,
        conflicts_by_id={c.conflict_id: c for c in conflicts},
        as_of_utc=as_of,
        freshness_threshold_hours=freshness_threshold_hours,
    )
    live_only = [t for t in tenders if t.candidate_source_kind == "live_snapshot"]
    if not live_only:
        raise ValueError("Case B construction did not yield live_snapshot tender")
    tender = live_only[0]
    return tender, refs_by_id


def build_ticket_detail_snapshot_from_fixture(fixture_path: Path) -> dict[str, Any]:
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    # Prefer CodigoExterno from sanitized listado when present.
    tender_code = None
    listado = payload.get("Listado") or payload.get("listado") or []
    if isinstance(listado, list) and listado:
        first = listado[0] if isinstance(listado[0], dict) else {}
        tender_code = first.get("CodigoExterno") or first.get("codigoExterno")
    snap = build_acquisition_snapshot(
        source_kind="ticket_detail",
        payload=payload,
        fixture_origin="live_response_sanitized",
        acquired_at_utc="2026-08-01T19:00:30Z",
        tender_code=tender_code or "3544-1-LE26",
    )
    return snap.to_dict()


def _coalesce_lifecycle_refs(
    refs: list[Any],
    *,
    as_of_utc: str,
    freshness_threshold_hours: int,
) -> tuple[list[CoalescedProcurementTender], dict[str, Any]]:
    from origenlab_email_pipeline.commercial_procurement_candidate_planner.coalescence import (
        coalesce_evidence_refs,
    )
    from origenlab_email_pipeline.commercial_procurement_candidate_planner.lifecycle import (
        apply_lifecycle,
    )
    from origenlab_email_pipeline.commercial_procurement_candidate_planner.normalize import (
        parse_as_of_utc,
    )

    as_of = parse_as_of_utc(as_of_utc)
    tenders, conflicts = coalesce_evidence_refs(
        refs,
        as_of_utc=as_of,
        freshness_threshold_hours=freshness_threshold_hours,
    )
    refs_by_id = {r.evidence_ref_id: r for r in refs}
    tenders = apply_lifecycle(
        tenders,
        refs_by_id=refs_by_id,
        conflicts_by_id={c.conflict_id: c for c in conflicts},
        as_of_utc=as_of,
        freshness_threshold_hours=freshness_threshold_hours,
    )
    return tenders, refs_by_id


def build_case_c_overlap_through_production_path(
    *,
    as_of_utc: str,
    freshness_threshold_hours: int = 48,
) -> tuple[CoalescedProcurementTender, dict[str, Any]]:
    """Synthetic both-plane agreement via real coalesce/lifecycle helpers."""
    from origenlab_email_pipeline.commercial_procurement_candidate_planner.normalize import (
        normalize_tender_timestamp,
        normalized_status_meaning,
        utc_iso,
    )
    from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
        ProcurementEvidenceRef,
    )
    from origenlab_email_pipeline.commercial_procurement_candidate_planner.constants import (
        IDENTITY_NS_MERCADO_PUBLICO,
    )

    key = "9001-1-le26"
    close_raw = "2026-08-10T15:00:00-04:00"
    close_nt = normalize_tender_timestamp(close_raw)
    meaning = normalized_status_meaning("5", "Publicada")

    def _mk(rid: str, plane: str, rank: str, *, pr4_id: str | None, acquired: str | None):
        return ProcurementEvidenceRef(
            evidence_ref_id=rid,
            evidence_plane=plane,
            source_kind="pr4" if plane == "pr4" else "mercado_publico_ticket_api",
            endpoint_kind=(
                "pr4_persisted_signal" if plane == "pr4" else "ticket_licitacion_detail"
            ),
            source_record_id=rid,
            canonical_tender_key=key,
            tender_key_kind=(
                "codigo_externo" if plane == "pr4" else "mercado_publico_codigo_externo"
            ),
            identity_namespace=IDENTITY_NS_MERCADO_PUBLICO,
            cross_source_join_eligible=True,
            snapshot_id=None if plane == "pr4" else "snap_case_c",
            acquisition_instance_id=None if plane == "pr4" else "inst_case_c",
            page_id=None if plane == "pr4" else "page_case_c",
            observation_id=None if plane == "pr4" else "obs_case_c",
            acquired_at_utc=acquired,
            source_status_code="5",
            source_status_name="Publicada",
            source_status_value="Publicada",
            source_status_system="chilecompra",
            normalized_status_meaning=meaning,
            publication_timestamp_raw=None,
            close_timestamp_raw=close_raw,
            publication_timestamp_utc=None,
            close_timestamp_utc=utc_iso(close_nt.utc_instant),
            publication_santiago_date=None,
            close_santiago_date=(
                close_nt.santiago_date.isoformat() if close_nt.santiago_date else None
            ),
            publication_precision=None,
            close_precision=close_nt.precision,
            timestamp_parse_reasons=(),
            buyer_display_raw="Org Case C",
            buyer_source_id="org-case-c",
            title_raw="Case C",
            source_payload_digest="case_c",
            source_fingerprint="case_c_fp",
            normalized_semantic_digest="case_c_sem",
            field_provenance={},
            reason_codes=(),
            source_rank_class=rank,
            has_status=True,
            has_close=True,
            has_publication=False,
            has_buyer_display=True,
            has_buyer_source_id=True,
            has_title=True,
            pr4_procurement_id=pr4_id,
        )

    refs = [
        _mk("pr4_case_c", "pr4", "pr4", pr4_id="p_case_c", acquired=None),
        _mk(
            "live_case_c",
            "acquisition",
            "ticket_detail",
            pr4_id=None,
            acquired="2026-08-01T10:00:00Z",
        ),
    ]
    tenders, refs_by_id = _coalesce_lifecycle_refs(
        refs,
        as_of_utc=as_of_utc,
        freshness_threshold_hours=freshness_threshold_hours,
    )
    both = [
        t
        for t in tenders
        if t.candidate_source_kind == "both"
        or t.coalescence_status in {"exact_agreement", "live_source_newer"}
    ]
    if not both:
        raise ValueError("Case C construction did not yield both/agreement tender")
    return both[0], refs_by_id


def build_case_d_conflict_through_production_path(
    *,
    as_of_utc: str,
    freshness_threshold_hours: int = 48,
) -> tuple[CoalescedProcurementTender, dict[str, Any]]:
    """Synthetic status conflict via real coalesce/lifecycle helpers."""
    from origenlab_email_pipeline.commercial_procurement_candidate_planner.normalize import (
        normalize_tender_timestamp,
        normalized_status_meaning,
        utc_iso,
    )
    from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
        ProcurementEvidenceRef,
    )
    from origenlab_email_pipeline.commercial_procurement_candidate_planner.constants import (
        IDENTITY_NS_MERCADO_PUBLICO,
    )

    key = "9002-1-le26"
    close_raw = "2026-08-10T15:00:00-04:00"
    close_nt = normalize_tender_timestamp(close_raw)

    def _mk(
        rid: str,
        plane: str,
        rank: str,
        *,
        code: str,
        name: str,
        pr4_id: str | None,
        acquired: str | None,
    ):
        return ProcurementEvidenceRef(
            evidence_ref_id=rid,
            evidence_plane=plane,
            source_kind="pr4" if plane == "pr4" else "mercado_publico_ticket_api",
            endpoint_kind=(
                "pr4_persisted_signal" if plane == "pr4" else "ticket_licitacion_detail"
            ),
            source_record_id=rid,
            canonical_tender_key=key,
            tender_key_kind=(
                "codigo_externo" if plane == "pr4" else "mercado_publico_codigo_externo"
            ),
            identity_namespace=IDENTITY_NS_MERCADO_PUBLICO,
            cross_source_join_eligible=True,
            snapshot_id=None if plane == "pr4" else "snap_case_d",
            acquisition_instance_id=None if plane == "pr4" else "inst_case_d",
            page_id=None if plane == "pr4" else "page_case_d",
            observation_id=None if plane == "pr4" else "obs_case_d",
            acquired_at_utc=acquired,
            source_status_code=code,
            source_status_name=name,
            source_status_value=name,
            source_status_system="chilecompra",
            normalized_status_meaning=normalized_status_meaning(code, name),
            publication_timestamp_raw=None,
            close_timestamp_raw=close_raw,
            publication_timestamp_utc=None,
            close_timestamp_utc=utc_iso(close_nt.utc_instant),
            publication_santiago_date=None,
            close_santiago_date=(
                close_nt.santiago_date.isoformat() if close_nt.santiago_date else None
            ),
            publication_precision=None,
            close_precision=close_nt.precision,
            timestamp_parse_reasons=(),
            buyer_display_raw="Org Case D",
            buyer_source_id="org-case-d",
            title_raw="Case D",
            source_payload_digest="case_d",
            source_fingerprint="case_d_fp",
            normalized_semantic_digest="case_d_sem",
            field_provenance={},
            reason_codes=(),
            source_rank_class=rank,
            has_status=True,
            has_close=True,
            has_publication=False,
            has_buyer_display=True,
            has_buyer_source_id=True,
            has_title=True,
            pr4_procurement_id=pr4_id,
        )

    refs = [
        _mk(
            "pr4_case_d",
            "pr4",
            "pr4",
            code="5",
            name="Publicada",
            pr4_id="p_case_d",
            acquired=None,
        ),
        _mk(
            "live_case_d",
            "acquisition",
            "ticket_detail",
            code="8",
            name="Adjudicada",
            pr4_id=None,
            acquired="2026-08-01T10:00:00Z",
        ),
    ]
    tenders, refs_by_id = _coalesce_lifecycle_refs(
        refs,
        as_of_utc=as_of_utc,
        freshness_threshold_hours=freshness_threshold_hours,
    )
    conflicts = [
        t
        for t in tenders
        if t.coalescence_status
        in {
            "status_conflict",
            "date_conflict",
            "buyer_identity_conflict",
            "multiple_live_sources_conflict",
        }
    ]
    if not conflicts:
        raise ValueError("Case D construction did not yield conflict tender")
    return conflicts[0], refs_by_id
