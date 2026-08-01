"""Redacted PR5B data walkthrough builder (fixtures only)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.commercial_procurement_acquisition.normalize import (
    assert_compatible_with_existing_normalizer,
    tender_observation_to_normalized_row,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.ocds import (
    build_ocds_month_snapshot,
    parse_ocds_package,
    plan_ocds_ranges,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.redaction import (
    assert_no_ticket_leak,
    redact_token,
    ticket_safety_flags,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.snapshot import (
    build_acquisition_snapshot,
    build_partial_detail_run,
    run_manifest,
    snapshot_manifest,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.ticket_api import (
    parse_ticket_summary_payload,
)


def _stage(stage: str, source: Any, normalized: Any, provenance: str, result: str) -> dict[str, Any]:
    return {
        "stage": stage,
        "source_value": source,
        "normalized_value": normalized,
        "provenance": provenance,
        "result": result,
    }


def _artifact_row(model: str, redacted: dict[str, Any], fp_note: str) -> dict[str, Any]:
    return {
        "artifact_model": model,
        "redacted_representation": redacted,
        "fingerprint_contribution": fp_note,
    }


def _redact_tender_obs(t: dict[str, Any]) -> dict[str, Any]:
    return {
        "tender_observation_id": t.get("tender_observation_id"),
        "source_native_tender_key_redacted": redact_token(
            "native", t.get("source_native_tender_key")
        ),
        "canonical_tender_key_candidate": t.get("canonical_tender_key_candidate"),
        "canonical_candidate_kind": t.get("canonical_candidate_kind"),
        "title_redacted": redact_token("title", t.get("title")),
        "buyer_redacted": redact_token("org", t.get("buyer_display")),
        "source_status_code": t.get("source_status_code"),
        "source_status_name": t.get("source_status_name"),
        "close_timestamp_raw": t.get("close_timestamp_raw"),
        "procurement_method": t.get("procurement_method"),
    }


def build_walkthrough_cases(fixtures_dir: Path) -> dict[str, Any]:
    summary = json.loads((fixtures_dir / "ticket_summary_list.json").read_text())
    detail = json.loads((fixtures_dir / "ticket_detail_items.json").read_text())
    ocds = json.loads((fixtures_dir / "ocds_single_release.json").read_text())
    ocds_records = json.loads(
        (fixtures_dir / "ocds_records_with_compiled.json").read_text()
    )
    malformed = json.loads((fixtures_dir / "ticket_malformed_shape.json").read_text())
    origin = json.loads((fixtures_dir / "FIXTURE_ORIGIN.json").read_text())
    fixture_origin = origin["fixture_origin"]

    snap_a = build_acquisition_snapshot(
        source_kind="ticket_summary",
        payload=summary,
        fixture_origin=fixture_origin,
        acquired_at_utc="2026-08-01T00:12:00Z",
        materialized_at_utc="2026-08-01T00:12:00Z",
    )
    _mq, mal_page, *_rest = parse_ticket_summary_payload(malformed)
    snap_b = build_acquisition_snapshot(
        source_kind="ticket_detail",
        payload=detail,
        fixture_origin=fixture_origin,
        acquired_at_utc="2026-08-01T00:12:01Z",
        tender_code="9999-1-LE26",
        materialized_at_utc="2026-08-01T00:12:01Z",
    )
    snap_c_single = build_acquisition_snapshot(
        source_kind="ocds",
        payload=ocds,
        fixture_origin=fixture_origin,
        acquired_at_utc="2026-08-01T00:12:02Z",
        year=2026,
        month=8,
        range_start=1,
        range_end=1,
        materialized_at_utc="2026-08-01T00:12:02Z",
    )
    # Multi-page monthly provenance for Case C (two single-item pages).
    page1 = parse_ocds_package(
        ocds, year=2026, month=8, range_start=1, range_end=1
    )
    page2_payload = json.loads(json.dumps(ocds))
    page2_payload["releases"][0]["id"] = "ocds-synth-0001-02"
    page2_payload["releases"][0]["ocid"] = "ocds-synth-0002"
    page2_payload["releases"][0]["tender"]["id"] = "9999-9-LE26"
    page2 = parse_ocds_package(
        page2_payload, year=2026, month=8, range_start=2, range_end=2
    )
    planned = plan_ocds_ranges(
        year=2026, month=8, source_reported_total=2, page_size=1
    )
    snap_c = build_ocds_month_snapshot(
        planned_ranges=planned,
        parsed_pages=[page1, page2],
        source_reported_total=2,
        fixture_origin=fixture_origin,
        materialized_at_utc="2026-08-01T00:12:02Z",
    )
    snap_c_records = build_acquisition_snapshot(
        source_kind="ocds",
        payload=ocds_records,
        fixture_origin=fixture_origin,
        year=2026,
        month=8,
        range_start=1,
        range_end=2,
        materialized_at_utc="2026-08-01T00:12:02Z",
    )

    run_d, children_d = build_partial_detail_run(
        summary_payload=summary,
        detail_success_payload=detail,
        detail_failure_code="9999-2-LE26",
        detail_failure_error="fixture_simulated_detail_failure",
        fixture_origin=fixture_origin,
        acquired_at_utc="2026-08-01T00:12:03Z",
        materialized_at_utc="2026-08-01T00:12:03Z",
    )

    lic = detail["Listado"][0]
    existing_rows = assert_compatible_with_existing_normalizer(lic, detail=True)
    adapted = [
        tender_observation_to_normalized_row(
            snap_b.tender_observations[0],
            line=line,
            tender_code="9999-1-LE26",
        )
        for line in snap_b.line_observations
    ]
    compat = {
        "existing_row_count": len(existing_rows),
        "adapted_row_count": len(adapted),
        "codigo_match": all(
            existing_rows[i]["codigo"] == adapted[i]["codigo"] for i in range(len(adapted))
        ),
        "title_match": all(
            existing_rows[i]["title"] == adapted[i]["title"] for i in range(len(adapted))
        ),
        "close_date_match": all(
            existing_rows[i]["close_date"] == adapted[i]["close_date"]
            for i in range(len(adapted))
        ),
        "line_description_match": all(
            existing_rows[i]["line_description"] == adapted[i]["line_description"]
            for i in range(len(adapted))
        ),
    }

    t0 = snap_a.tender_observations[0]
    case_a = {
        "case_id": "A_ticket_summary",
        "fixture": "ticket_summary_list.json",
        "fixture_origin": fixture_origin,
        "stages": [
            _stage(
                "envelope",
                "Cantidad/Listado",
                snap_a.pages[0].response_item_count,
                "ticket Listado extract",
                snap_a.pages[0].completeness_status,
            ),
            _stage(
                "source_native_vs_canonical",
                t0.source_native_tender_key,
                t0.canonical_tender_key_candidate,
                t0.canonical_candidate_reason,
                t0.canonical_candidate_kind,
            ),
            _stage(
                "malformed_fingerprint",
                "ticket_malformed_shape.json",
                mal_page.raw_canonical_json_digest[:16] + "…",
                "raw_canonical_json_digest fingerprints actual payload",
                mal_page.completeness_status,
            ),
            _stage(
                "complete_fingerprint",
                snap_a.pages[0].raw_canonical_json_digest[:16] + "…",
                snap_a.source_fingerprint[:16] + "…",
                "complete summary page contributes to source fingerprint",
                snap_a.completeness_status,
            ),
        ],
        "artifacts": [
            _artifact_row(
                "AcquisitionSnapshot",
                {
                    "snapshot_id": snap_a.snapshot_id,
                    "fixture_origin": snap_a.fixture_origin,
                    "completeness_status": snap_a.completeness_status,
                    "source_fingerprint": snap_a.source_fingerprint,
                    "normalized_semantic_digest": snap_a.normalized_semantic_digest,
                },
                "fixture_origin separate from completeness_status",
            )
        ],
        "counts": {
            "pages": len(snap_a.pages),
            "source_observations": len(snap_a.source_observations),
            "tender_observations": len(snap_a.tender_observations),
            "line_observations": len(snap_a.line_observations),
        },
        "fingerprints": {
            "source": snap_a.source_fingerprint,
            "semantic": snap_a.normalized_semantic_digest,
            "malformed_raw": mal_page.raw_canonical_json_digest,
        },
    }

    case_b = {
        "case_id": "B_ticket_detail",
        "fixture": "ticket_detail_items.json",
        "fixture_origin": fixture_origin,
        "stages": [
            _stage(
                "query_identity",
                "tender_code=9999-1-LE26",
                snap_b.query.acquisition_query_id,
                "build_ticket_detail_query normalized code",
                snap_b.query.endpoint_kind,
            ),
            _stage(
                "canonical_candidate",
                "CodigoExterno",
                snap_b.tender_observations[0].canonical_tender_key_candidate,
                snap_b.tender_observations[0].canonical_candidate_reason,
                snap_b.tender_observations[0].canonical_candidate_kind,
            ),
            _stage(
                "line_observations",
                2,
                [line.ordinal for line in snap_b.line_observations],
                "Correlativo/CodigoProducto + ordinal",
                "stable line IDs across item shuffle",
            ),
            _stage(
                "compatibility_adapter",
                "CHILECOMPRA_NORMALIZED_FIELDS",
                compat["codigo_match"],
                "tender_observation_to_normalized_row",
                "equals existing normalizer on locked fields",
            ),
        ],
        "artifacts": [
            _artifact_row(
                "ProcurementLineObservation",
                {
                    "line_ids": [line.line_observation_id for line in snap_b.line_observations],
                    "native_ids": [
                        line.source_native_line_id for line in snap_b.line_observations
                    ],
                },
                "line rows contribute to semantic digest",
            )
        ],
        "compatibility": compat,
        "counts": {
            "pages": len(snap_b.pages),
            "source_observations": len(snap_b.source_observations),
            "tender_observations": len(snap_b.tender_observations),
            "line_observations": len(snap_b.line_observations),
        },
        "fingerprints": {
            "source": snap_b.source_fingerprint,
            "semantic": snap_b.normalized_semantic_digest,
        },
        "tender_redacted": _redact_tender_obs(snap_b.tender_observations[0].to_dict()),
    }

    rec_src = snap_c_records.source_observations
    case_c = {
        "case_id": "C_ocds_package",
        "fixture": "ocds_records_with_compiled.json + monthly pages",
        "fixture_origin": fixture_origin,
        "label": "synthetic_official_shape",
        "stages": [
            _stage(
                "records_policy_B",
                "compiledRelease + historical releases",
                [o.release_kind for o in rec_src],
                "historical preferred; compiled only if unique (ocid, release.id)",
                f"observations={len(rec_src)}",
            ),
            _stage(
                "record_id",
                "record-synth-0001",
                rec_src[0].record_id if rec_src else None,
                "record.id retained on observations",
                "not PR5 eligibility",
            ),
            _stage(
                "procurement_method",
                "tender.procurementMethod",
                snap_c_records.tender_observations[0].procurement_method
                if snap_c_records.tender_observations
                else None,
                "bounded typed evidence",
                "not PR5 eligibility",
            ),
            _stage(
                "additional_classifications",
                "item.additionalClassifications",
                [
                    list(line.additional_classifications)
                    for line in snap_c_records.line_observations
                ],
                "line-level bounded evidence",
                "retained",
            ),
            _stage(
                "related_processes",
                "tender.relatedProcesses",
                [
                    list(t.related_processes)
                    for t in snap_c_records.tender_observations
                ],
                "tender-level bounded evidence",
                "retained",
            ),
            _stage(
                "monthly_range_provenance",
                planned,
                snap_c.completeness_status,
                "build_ocds_month_snapshot",
                f"pages={len(snap_c.pages)}",
            ),
        ],
        "artifacts": [
            _artifact_row(
                "AcquisitionSnapshot(month)",
                {
                    "snapshot_id": snap_c.snapshot_id,
                    "completeness_status": snap_c.completeness_status,
                    "source_fingerprint": snap_c.source_fingerprint,
                },
                "monthly source fingerprint over sorted pages",
            ),
            _artifact_row(
                "AcquisitionSnapshot(records)",
                {
                    "snapshot_id": snap_c_records.snapshot_id,
                    "release_kinds": [o.release_kind for o in rec_src],
                    "canonical_candidates": [
                        o.canonical_tender_key_candidate for o in rec_src
                    ],
                },
                "records policy B prevents duplicate compiled emission",
            ),
        ],
        "counts": {
            "monthly_pages": len(snap_c.pages),
            "records_source_observations": len(rec_src),
            "single_page_source_observations": len(snap_c_single.source_observations),
        },
        "fingerprints": {
            "monthly_source": snap_c.source_fingerprint,
            "monthly_semantic": snap_c.normalized_semantic_digest,
            "records_source": snap_c_records.source_fingerprint,
            "records_semantic": snap_c_records.normalized_semantic_digest,
            "single_source": snap_c_single.source_fingerprint,
        },
    }

    case_d = {
        "case_id": "D_acquisition_run_partial_detail",
        "fixture_origin": fixture_origin,
        "stages": [
            _stage(
                "AcquisitionRun",
                run_d.acquisition_run_id,
                run_d.run_completeness,
                "composite of single-scope snapshots",
                run_d.run_kind,
            ),
            _stage(
                "summary_child",
                children_d["summary"].snapshot_id,
                children_d["summary"].query.acquisition_query_id,
                "one summary AcquisitionSnapshot",
                children_d["summary"].completeness_status,
            ),
            _stage(
                "detail_a_success",
                children_d["detail_a"].snapshot_id,
                children_d["detail_a"].query.acquisition_query_id,
                "own detail query ID for code A",
                "completed",
            ),
            _stage(
                "detail_b_failed",
                run_d.detail_attempts[1].query.acquisition_query_id,
                run_d.detail_attempts[1].page_id,
                "build_ticket_detail_query(tender_code=B) — never reuses A",
                run_d.detail_attempts[1].status,
            ),
        ],
        "artifacts": [
            _artifact_row(
                "AcquisitionRun",
                {
                    "acquisition_run_id": run_d.acquisition_run_id,
                    "child_query_ids": list(run_d.child_query_ids),
                    "requested_detail_count": run_d.requested_detail_count,
                    "completed_detail_count": run_d.completed_detail_count,
                    "failed_detail_count": run_d.failed_detail_count,
                    "run_source_fingerprint": run_d.run_source_fingerprint,
                },
                "run fingerprint excludes materialized_at",
            )
        ],
        "diagnostics": run_d.diagnostics,
        "fingerprints": {
            "run_source": run_d.run_source_fingerprint,
            "summary_source": children_d["summary"].source_fingerprint,
            "detail_a_source": children_d["detail_a"].source_fingerprint,
        },
    }

    ticket_native = snap_b.tender_observations[0].source_native_tender_key
    ocds_native = snap_c_single.tender_observations[0].source_native_tender_key
    ticket_cand = snap_b.tender_observations[0].canonical_tender_key_candidate
    ocds_cand = snap_c_single.tender_observations[0].canonical_tender_key_candidate
    case_e = {
        "case_id": "E_cross_source_same_logical_tender",
        "label": "synthetic_cross_source_example",
        "stages": [
            _stage(
                "ticket_source_native",
                "CodigoExterno=9999-1-LE26",
                ticket_native,
                "ticket_api:codigo_externo:<norm>",
                redact_token("native", ticket_native),
            ),
            _stage(
                "ocds_source_native",
                "ocid|release.id|tender.id|kind",
                ocds_native,
                "ocds:… distinct from ticket native",
                redact_token("native", ocds_native),
            ),
            _stage(
                "shared_canonical_candidate",
                ticket_cand,
                ocds_cand,
                "parser-emitted mercado_publico_codigo_externo (no ticket:/ocds-tender: prefix)",
                "equal" if ticket_cand == ocds_cand else "unequal",
            ),
            _stage(
                "no_coalescence",
                "source_native remain distinct",
                ticket_native != ocds_native,
                "PR5B emits candidates only",
                "coalesced=false",
            ),
        ],
        "ticket_canonical_candidate": ticket_cand,
        "ocds_canonical_candidate": ocds_cand,
        "candidates_equal": ticket_cand == ocds_cand,
        "ticket_source_fingerprint": snap_b.source_fingerprint,
        "ocds_source_fingerprint": snap_c_single.source_fingerprint,
        "coalesced": False,
        "artifacts": [
            _artifact_row(
                "canonical_tender_key_candidate",
                {
                    "ticket": ticket_cand,
                    "ocds": ocds_cand,
                    "kind_ticket": snap_b.tender_observations[0].canonical_candidate_kind,
                    "kind_ocds": snap_c_single.tender_observations[0].canonical_candidate_kind,
                },
                "source-neutral candidate equality without coalescence",
            )
        ],
    }

    safety = ticket_safety_flags(ticket_configured=False)
    bundle = {
        "fixture_origin": fixture_origin,
        **safety,
        "cases": {
            "A": case_a,
            "B": case_b,
            "C": case_c,
            "D": case_d,
            "E": case_e,
        },
        "manifests": {
            "A": snapshot_manifest(snap_a, ticket_configured=False),
            "B": snapshot_manifest(snap_b, ticket_configured=False),
            "C": snapshot_manifest(snap_c, ticket_configured=False),
            "C_records": snapshot_manifest(snap_c_records, ticket_configured=False),
            "D": run_manifest(run_d, ticket_configured=False),
        },
    }
    blob = json.dumps(bundle, default=str)
    assert_no_ticket_leak(blob)
    assert ticket_cand == ocds_cand == "9999-1-le26"
    assert ticket_native != ocds_native
    return bundle


def render_walkthrough_markdown(bundle: dict[str, Any]) -> str:
    lines = [
        "# Commercial procurement acquisition — PR5B data walkthrough",
        "",
        f"Fixture origin: `{bundle['fixture_origin']}`",
        f"Authenticated request performed: **{bundle['authenticated_request_performed']}**",
        f"Ticket used for request: **{bundle['ticket_used_for_request']}**",
        "",
        "Field flow:",
        "",
        "```",
        "official source payload (fixture/offline)",
        "    ↓",
        "sanitized AcquisitionQuery (one scope)",
        "    ↓",
        "AcquisitionPage",
        "    ↓",
        "source / tender / line observations",
        "    ↓",
        "source fingerprint + normalized semantic digest",
        "    ↓ (optional composite)",
        "AcquisitionRun (multiple single-scope snapshots/attempts)",
        "```",
        "",
    ]
    for key in ("A", "B", "C", "D", "E"):
        case = bundle["cases"][key]
        lines.append(f"## Case {key} — {case['case_id']}")
        lines.append("")
        if case.get("label"):
            lines.append(f"Label: `{case['label']}`")
            lines.append("")
        lines.append("| Stage | Source value | Normalized value | Provenance | Result |")
        lines.append("|-------|--------------|------------------|------------|--------|")
        for st in case.get("stages") or []:
            lines.append(
                f"| `{st['stage']}` | `{st['source_value']}` | `{st['normalized_value']}` | "
                f"{st['provenance']} | `{st['result']}` |"
            )
        lines.append("")
        if case.get("fingerprints"):
            for fk, fv in case["fingerprints"].items():
                lines.append(f"- {fk}: `{fv}`")
            lines.append("")
        if case.get("artifacts"):
            lines.append(
                "| Model/artifact | Redacted representation | Fingerprint contribution |"
            )
            lines.append(
                "|----------------|-------------------------|--------------------------|"
            )
            for art in case["artifacts"]:
                lines.append(
                    f"| `{art['artifact_model']}` | `{json.dumps(art['redacted_representation'], sort_keys=True)}` | "
                    f"{art['fingerprint_contribution']} |"
                )
            lines.append("")
    return "\n".join(lines) + "\n"
