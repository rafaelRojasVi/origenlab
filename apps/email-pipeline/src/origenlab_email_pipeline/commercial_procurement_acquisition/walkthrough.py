"""Redacted PR5B data walkthrough builder (fixtures only)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.commercial_procurement_acquisition.normalize import (
    assert_compatible_with_existing_normalizer,
    tender_observation_to_normalized_row,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.redaction import (
    assert_no_ticket_leak,
    redact_token,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.snapshot import (
    build_acquisition_snapshot,
    build_partial_detail_snapshot,
    snapshot_manifest,
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
        "normalized_tender_key_redacted": redact_token(
            "tender", t.get("normalized_tender_key")
        ),
        "title_redacted": redact_token("title", t.get("title")),
        "buyer_redacted": redact_token("org", t.get("buyer_display")),
        "source_status_code": t.get("source_status_code"),
        "source_status_name": t.get("source_status_name"),
        "close_timestamp_raw": t.get("close_timestamp_raw"),
    }


def build_walkthrough_cases(fixtures_dir: Path) -> dict[str, Any]:
    summary = json.loads((fixtures_dir / "ticket_summary_list.json").read_text())
    detail = json.loads((fixtures_dir / "ticket_detail_items.json").read_text())
    ocds = json.loads((fixtures_dir / "ocds_single_release.json").read_text())
    origin = json.loads((fixtures_dir / "FIXTURE_ORIGIN.json").read_text())

    snap_a = build_acquisition_snapshot(
        source_kind="ticket_summary",
        payload=summary,
        fixture_origin=origin["fixture_origin"],
        acquired_at_utc="2026-08-01T00:12:00Z",
    )
    snap_b = build_acquisition_snapshot(
        source_kind="ticket_detail",
        payload=detail,
        fixture_origin=origin["fixture_origin"],
        acquired_at_utc="2026-08-01T00:12:01Z",
        tender_code="9999-1-LE26",
    )
    snap_c = build_acquisition_snapshot(
        source_kind="ocds",
        payload=ocds,
        fixture_origin=origin["fixture_origin"],
        acquired_at_utc="2026-08-01T00:12:02Z",
        year=2026,
        month=8,
        range_start=1,
        range_end=1,
    )
    snap_d = build_partial_detail_snapshot(
        summary_payload=summary,
        detail_success_payload=detail,
        detail_failure_code="9999-2-LE26",
        detail_failure_error="fixture_simulated_detail_failure",
        fixture_origin=origin["fixture_origin"],
        acquired_at_utc="2026-08-01T00:12:03Z",
    )

    # Compatibility: existing normalizer vs adapter for detail licitacion
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

    case_a = {
        "case_id": "A_ticket_summary",
        "fixture": "ticket_summary_list.json",
        "fixture_origin": origin["fixture_origin"],
        "stages": [
            _stage(
                "envelope",
                "Cantidad/Listado",
                snap_a.pages[0].response_item_count,
                "ticket Listado extract",
                "summary page ok",
            ),
            _stage(
                "AcquisitionQuery",
                "estado=activas",
                snap_a.query.acquisition_query_id,
                "sanitized query identity (no ticket)",
                snap_a.query.endpoint_kind,
            ),
            _stage(
                "AcquisitionPage",
                snap_a.pages[0].raw_canonical_json_digest[:16] + "…",
                snap_a.pages[0].page_id,
                "page digest + range_position",
                snap_a.pages[0].completeness_status,
            ),
            _stage(
                "source_observation",
                "CodigoExterno",
                len(snap_a.source_observations),
                "one observation per Listado item",
                "all tenders captured (no keyword prefilter)",
            ),
            _stage(
                "tender_observation",
                snap_a.tender_observations[0].normalized_tender_key,
                redact_token(
                    "tender", snap_a.tender_observations[0].normalized_tender_key
                ),
                "ticket:codigo",
                "summary has zero lines",
            ),
            _stage(
                "fingerprint",
                "source+semantic",
                snap_a.source_fingerprint[:16] + "…",
                "acquisition_source_fingerprint_v1",
                snap_a.normalized_semantic_digest[:16] + "…",
            ),
        ],
        "artifacts": [
            _artifact_row(
                "AcquisitionSnapshot",
                {
                    "snapshot_id": snap_a.snapshot_id,
                    "source_fingerprint": snap_a.source_fingerprint,
                    "normalized_semantic_digest": snap_a.normalized_semantic_digest,
                    "tender_count": len(snap_a.tender_observations),
                    "line_count": len(snap_a.line_observations),
                },
                "full source + semantic digests",
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
        },
    }

    case_b = {
        "case_id": "B_ticket_detail",
        "fixture": "ticket_detail_items.json",
        "fixture_origin": origin["fixture_origin"],
        "stages": [
            _stage(
                "summary_vs_detail",
                "endpoint_kind",
                snap_b.query.endpoint_kind,
                "ticket_licitacion_detail distinct from summary",
                "detail provenance retained",
            ),
            _stage(
                "tender_observation",
                "9999-1-LE26",
                redact_token("tender", "ticket:9999-1-le26"),
                "CodigoExterno",
                snap_b.tender_observations[0].tender_observation_id,
            ),
            _stage(
                "line_observations",
                2,
                [line.ordinal for line in snap_b.line_observations],
                "Correlativo/CodigoProducto + ordinal",
                "stable line IDs",
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

    case_c = {
        "case_id": "C_ocds_package",
        "fixture": "ocds_single_release.json",
        "fixture_origin": origin["fixture_origin"],
        "label": "synthetic_official_shape",
        "stages": [
            _stage(
                "package",
                ocds.get("uri"),
                snap_c.pages[0].envelope_meta.get("uri"),
                "OCDS package metadata",
                "fixture_only",
            ),
            _stage(
                "release",
                "ocid+release.id",
                snap_c.source_observations[0].source_native_key,
                "do not collapse releases",
                snap_c.source_observations[0].ocid,
            ),
            _stage(
                "tender_status",
                "tender.status=active",
                snap_c.tender_observations[0].source_status_name,
                "source_status_system=ocds (not PR5 eligibility)",
                "status retained as evidence only",
            ),
            _stage(
                "lines",
                1,
                len(snap_c.line_observations),
                "tender.items",
                snap_c.line_observations[0].line_observation_id
                if snap_c.line_observations
                else None,
            ),
        ],
        "counts": {
            "pages": len(snap_c.pages),
            "source_observations": len(snap_c.source_observations),
            "tender_observations": len(snap_c.tender_observations),
            "line_observations": len(snap_c.line_observations),
        },
        "fingerprints": {
            "source": snap_c.source_fingerprint,
            "semantic": snap_c.normalized_semantic_digest,
        },
    }

    case_d = {
        "case_id": "D_partial_detail_failure",
        "fixture_origin": origin["fixture_origin"],
        "stages": [
            _stage(
                "completeness",
                "requested_detail_count=2",
                snap_d.diagnostics.get("completed_detail_count"),
                "partial_detail_failure",
                snap_d.completeness_status,
            ),
            _stage(
                "retained_evidence",
                True,
                len(snap_d.tender_observations),
                "successful summary+detail retained",
                "false_complete=false",
            ),
        ],
        "diagnostics": snap_d.diagnostics,
        "counts": {
            "pages": len(snap_d.pages),
            "source_observations": len(snap_d.source_observations),
            "tender_observations": len(snap_d.tender_observations),
            "line_observations": len(snap_d.line_observations),
        },
        "fingerprints": {
            "source": snap_d.source_fingerprint,
            "semantic": snap_d.normalized_semantic_digest,
        },
    }

    # Case E — cross-source keys without coalescence
    ticket_key = snap_b.tender_observations[0].normalized_tender_key
    ocds_key = snap_c.tender_observations[0].normalized_tender_key
    case_e = {
        "case_id": "E_cross_source_same_logical_tender",
        "label": "synthetic_cross_source_example",
        "stages": [
            _stage(
                "ticket_native",
                "CodigoExterno=9999-1-LE26",
                ticket_key,
                "ticket API",
                redact_token("tender", ticket_key),
            ),
            _stage(
                "ocds_native",
                "tender.id=9999-1-LE26 / ocid",
                ocds_key,
                "OCDS release",
                redact_token("tender", ocds_key),
            ),
            _stage(
                "shared_candidate",
                "9999-1-LE26",
                "codigo_externo_shared_candidate",
                "comparison evidence only",
                "no automatic coalescence in PR5B",
            ),
            _stage(
                "pr5c_boundary",
                "coalescence",
                "deferred",
                "PR5C will compare planes",
                "PR5B emits keys only",
            ),
        ],
        "ticket_source_fingerprint": snap_b.source_fingerprint,
        "ocds_source_fingerprint": snap_c.source_fingerprint,
        "coalesced": False,
    }

    bundle = {
        "fixture_origin": origin["fixture_origin"],
        "authenticated_request_performed": False,
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
            "D": snapshot_manifest(snap_d, ticket_configured=False),
        },
    }
    blob = json.dumps(bundle, default=str)
    assert_no_ticket_leak(blob)
    return bundle


def render_walkthrough_markdown(bundle: dict[str, Any]) -> str:
    lines = [
        "# Commercial procurement acquisition — PR5B data walkthrough",
        "",
        f"Fixture origin: `{bundle['fixture_origin']}`",
        f"Authenticated request performed: **{bundle['authenticated_request_performed']}**",
        "",
        "Field flow:",
        "",
        "```",
        "official source payload (fixture/offline)",
        "    ↓",
        "sanitized AcquisitionQuery",
        "    ↓",
        "AcquisitionPage",
        "    ↓",
        "source / tender / line observations",
        "    ↓",
        "source fingerprint + normalized semantic digest",
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
            lines.append(
                f"- source_fingerprint: `{case['fingerprints']['source']}`"
            )
            lines.append(
                f"- normalized_semantic_digest: `{case['fingerprints']['semantic']}`"
            )
            lines.append("")
        if case.get("artifacts"):
            lines.append(
                "| Artifact/model | Redacted representation | Fingerprint contribution |"
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
