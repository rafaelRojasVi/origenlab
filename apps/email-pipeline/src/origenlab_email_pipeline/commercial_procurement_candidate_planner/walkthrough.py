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
    steps: list[dict[str, Any]],
    planned_rows: dict[str, Any],
    reconciliation_impact: dict[str, Any],
    fingerprint_contribution: dict[str, Any],
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "label": label,
        "synthetic": synthetic,
        "synthetic_label": (
            "synthetic_overlap_through_production_code_path" if synthetic else None
        ),
        "steps": steps,
        "planned_rows": planned_rows,
        "reconciliation_impact": reconciliation_impact,
        "fingerprint_contribution": fingerprint_contribution,
        "relevance_considered": False,
    }


def build_walkthrough_bundle(
    result: CandidatePlanResult,
    *,
    case_hints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build machine-readable walkthrough from a completed plan result."""
    hints = case_hints or {}
    tenders = list(result.coalesced_tenders)
    refs = {e.evidence_ref_id: e for e in result.evidence_refs}
    unresolved = list(result.unresolved)

    # Case A: first pr4_only historical.
    case_a_tender = next(
        (t for t in tenders if t.coalescence_status == "pr4_only"),
        None,
    )
    # Case B: first live_only.
    case_b_tender = next(
        (t for t in tenders if t.coalescence_status == "live_only"),
        None,
    )
    # Case C: exact agreement or both.
    case_c_tender = next(
        (
            t
            for t in tenders
            if t.coalescence_status
            in {"exact_agreement", "live_source_newer"}
            or t.candidate_source_kind == "both"
        ),
        None,
    )
    # Case D: conflict.
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
    # Case E: unresolved OCDS.
    case_e_unresolved = next(
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

    def tender_case(case_id: str, label: str, tender, synthetic: bool) -> dict[str, Any]:
        if tender is None:
            return _case_shell(
                case_id=case_id,
                label=label,
                synthetic=True,
                steps=[{"note": "case_unavailable_in_this_plan"}],
                planned_rows={},
                reconciliation_impact={},
                fingerprint_contribution={},
            )
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
                        "canonical_tender_key": red_t.get("canonical_tender_key"),
                        "tender_key_kind": red_t.get("tender_key_kind"),
                    },
                },
                {
                    "stage": "routes_considered_rejected",
                    "accepted": "mercado_publico_codigo_externo",
                    "rejected": [
                        "ocid_alone",
                        "source_native_alone",
                        "title_similarity",
                        "buyer_name_similarity",
                    ],
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

    cases = [
        tender_case(
            "A",
            "production_pr4_only_historical_tender",
            case_a_tender,
            synthetic=bool(hints.get("case_a_synthetic", False)),
        ),
        tender_case(
            "B",
            "sanitized_live_only_ticket_tender",
            case_b_tender,
            synthetic=bool(hints.get("case_b_synthetic", False)),
        ),
        tender_case(
            "C",
            "exact_two_plane_agreement",
            case_c_tender,
            synthetic=bool(
                hints.get("case_c_synthetic", True)
            ),  # expected synthetic unless real overlap found
        ),
        tender_case(
            "D",
            "source_conflict",
            case_d_tender,
            synthetic=bool(hints.get("case_d_synthetic", True)),
        ),
    ]

    if case_e_unresolved is None:
        cases.append(
            _case_shell(
                case_id="E",
                label="unresolved_ocds_identity",
                synthetic=True,
                steps=[{"note": "case_unavailable_in_this_plan"}],
                planned_rows={},
                reconciliation_impact={},
                fingerprint_contribution={},
            )
        )
    else:
        red_u = redact_unresolved(case_e_unresolved.to_dict())
        cases.append(
            _case_shell(
                case_id="E",
                label="unresolved_ocds_identity",
                synthetic=bool(hints.get("case_e_synthetic", False)),
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
        "walkthrough_version": "pr5c_coalescence_lifecycle_v1",
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
            lines.append(f"- **{step.get('stage', 'note')}**: `{json.dumps(step, sort_keys=True)}`")
        lines.append("")
    return "\n".join(lines) + "\n"


def write_walkthrough(bundle: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "DATA_WALKTHROUGH.json"
    md_path = out_dir / "DATA_WALKTHROUGH.md"
    proof_path = out_dir / "REDACTION_PROOF.json"
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


def build_ticket_detail_snapshot_from_fixture(fixture_path: Path) -> dict[str, Any]:
    """Helper for tests/walkthrough: real PR5B parser path → snapshot dict."""
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    snap = build_acquisition_snapshot(
        source_kind="ticket_detail",
        payload=payload,
        fixture_origin="live_response_sanitized",
        acquired_at_utc="2026-08-01T19:00:30Z",
    )
    return snap.to_dict()
