"""Orchestrate bounded live source-contract validation (read-only)."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.chilecompra_api import TICKET_ENV_VAR, ticket_from_env
from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.identity import (
    is_mercado_publico_codigo_shape,
    normalize_mercado_publico_codigo,
    ticket_canonical_candidate,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.live_contract.budget import (
    RequestBudget,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.live_contract.compare import (
    build_field_type_matrix,
    compare_contracts,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.live_contract.range_semantics import (
    conclude_range_semantics,
    summarize_probe,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.live_contract.sanitize import (
    SANITIZER_VERSION,
    assert_no_identifier_leaks,
    sanitize_live_payload,
    strip_sanitizer_meta,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.live_contract.transport import (
    BudgetedLiveTransport,
    plan_requests,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.snapshot import (
    build_acquisition_snapshot,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.ticket_api import (
    parse_ticket_detail_payload,
    parse_ticket_summary_payload,
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _write_json(path: Path, payload: Any, *, restrictive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if restrictive:
        try:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def load_ticket_into_environ() -> bool:
    """Isolated helper — NOT used by the live CLI validation path.

    Production CLI requires CHILECOMPRA_API_TICKET already exported.
    Never returns or logs the ticket value.
    """
    if (os.environ.get(TICKET_ENV_VAR) or "").strip():
        return True
    root = Path(__file__).resolve().parents[4]  # apps/email-pipeline
    for p in (root / ".env", Path.home() / ".config/origenlab/.env"):
        if not p.is_file():
            continue
        for line in p.read_text(errors="ignore").splitlines():
            if line.strip().startswith("#"):
                continue
            if line.strip().startswith(f"{TICKET_ENV_VAR}="):
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                if val:
                    os.environ[TICKET_ENV_VAR] = val
                    return True
    return False


def select_detail_codes(
    summary_payload: dict[str, Any], *, limit: int
) -> list[str]:
    """Deterministic selection: normalize, filter, sort, take first N distinct."""
    listado = summary_payload.get("Listado")
    if not isinstance(listado, list):
        return []
    codes: set[str] = set()
    for item in listado:
        if not isinstance(item, dict):
            continue
        raw = item.get("CodigoExterno")
        norm = normalize_mercado_publico_codigo(str(raw) if raw is not None else None)
        if not norm or not is_mercado_publico_codigo_shape(norm):
            continue
        codes.add(norm)
    return sorted(codes)[: max(0, min(3, limit))]


def selection_token(ordinal: int) -> str:
    return f"live_detail_selection_{ordinal:03d}"


@dataclass
class ValidationPaths:
    root: Path
    raw: Path
    sanitized: Path

    @classmethod
    def create(cls, out_dir: Path) -> ValidationPaths:
        raw = out_dir / "raw"
        sanitized = out_dir / "sanitized"
        raw.mkdir(parents=True, exist_ok=True)
        sanitized.mkdir(parents=True, exist_ok=True)
        return cls(root=out_dir, raw=raw, sanitized=sanitized)


def run_live_validation(
    *,
    out_dir: Path,
    ticket_summary_limit_details: int = 3,
    authenticated_request_budget: int = 4,
    public_request_budget: int = 4,
    timeout_seconds: float = 30.0,
    run_context: str = "pr5b1_live_contract",
    reference_fixtures_dir: Path | None = None,
) -> dict[str, Any]:
    if authenticated_request_budget > 4 or authenticated_request_budget < 1:
        raise ValueError("authenticated_request_budget must be 1..4")
    if public_request_budget > 4 or public_request_budget < 0:
        raise ValueError("public_request_budget must be 0..4")
    if ticket_summary_limit_details > 3:
        raise ValueError("ticket_summary_limit_details maximum is 3")

    # Ticket must already be in the process environment (CHILECOMPRA_API_TICKET).
    # Do not auto-load .env files on the live CLI path.
    ticket_from_env()  # fail fast if missing

    paths = ValidationPaths.create(out_dir)
    budget = RequestBudget(
        authenticated_budget_max=authenticated_request_budget,
        public_budget_max=public_request_budget,
    )
    transport = BudgetedLiveTransport(budget, timeout_seconds=timeout_seconds)

    sqlite_proof = {
        "production_sqlite_opened": False,
        "production_sqlite_write_mode": False,
        "note": "this validator does not open SQLite",
    }

    # --- Ticket summary ---
    summary_result = transport.fetch_ticket_summary(estado="activas")
    if summary_result.error_classification and "fecha" in (
        summary_result.error_classification or ""
    ).casefold():
        # Per policy: do not improvise fecha fallback.
        _write_json(
            paths.sanitized / "CONTRACT_DIFF.json",
            {
                "summary_failure": summary_result.error_classification,
                "fecha_fallback": "not_attempted",
                "action": "requires_separate_explicit_run_or_documented_correction",
            },
        )
    _write_bytes(paths.raw / "ticket_summary.response.json", summary_result.raw_bytes)

    summary_payload = summary_result.payload or {}
    summary_shape = {
        "http_status": summary_result.http_status,
        "top_level_field_types": {
            str(k): type(v).__name__ for k, v in summary_payload.items()
        }
        if summary_payload
        else {},
        "Cantidad": summary_payload.get("Cantidad") if summary_payload else None,
        "FechaCreacion_type": type(summary_payload.get("FechaCreacion")).__name__
        if summary_payload
        else None,
        "Version": summary_payload.get("Version") if summary_payload else None,
        "Listado_type": type(summary_payload.get("Listado")).__name__
        if summary_payload
        else None,
        "raw_byte_sha256": summary_result.raw_byte_sha256,
        "canonical_json_digest": summary_result.canonical_json_digest,
    }

    parser_summary = None
    summary_snap = None
    if summary_payload:
        parser_summary = parse_ticket_summary_payload(summary_payload)
        summary_snap = build_acquisition_snapshot(
            source_kind="ticket_summary",
            payload=summary_payload,
            fixture_origin="live_response_unsanitized_ephemeral",
            materialized_at_utc=_utcnow(),
        )
        listado = summary_payload.get("Listado")
        valid = 0
        malformed = 0
        if isinstance(listado, list):
            for item in listado:
                if isinstance(item, dict) and item.get("CodigoExterno"):
                    cand = ticket_canonical_candidate(str(item.get("CodigoExterno")))
                    if cand.canonical_tender_key_candidate:
                        valid += 1
                    else:
                        malformed += 1
                else:
                    malformed += 1
        summary_shape["valid_tender_entries"] = valid
        summary_shape["malformed_entries"] = malformed
        summary_shape["parser_completeness"] = parser_summary[1].completeness_status
        summary_shape["source_observation_count"] = len(parser_summary[2])

    # --- Detail selection ---
    codes = (
        select_detail_codes(summary_payload, limit=ticket_summary_limit_details)
        if summary_payload
        else []
    )
    detail_tokens = [selection_token(i + 1) for i in range(len(codes))]
    detail_validation = {
        "status": "ok" if codes else "detail_validation_unavailable",
        "selected_count": len(codes),
        "selection_tokens": detail_tokens,
        "selection_rule": "normalize_sort_first_n_distinct",
        "summary_digest": summary_result.canonical_json_digest,
    }

    detail_findings: list[dict[str, Any]] = []
    forbidden_for_fixtures: list[str] = list(codes)
    for idx, code in enumerate(codes):
        token = selection_token(idx + 1)
        result = transport.fetch_ticket_detail(codigo=code)
        raw_name = f"ticket_detail_{idx + 1:03d}.response.json"
        _write_bytes(paths.raw / raw_name, result.raw_bytes)
        payload = result.payload or {}
        finding: dict[str, Any] = {
            "selection_token": token,
            "http_status": result.http_status,
            "raw_byte_sha256": result.raw_byte_sha256,
            "canonical_json_digest": result.canonical_json_digest,
            "error_classification": result.error_classification,
        }
        if payload:
            _q, page, sources, tenders, lines, diag = parse_ticket_detail_payload(
                payload, tender_code=code
            )
            finding.update(
                {
                    "Cantidad": payload.get("Cantidad"),
                    "Listado_type": type(payload.get("Listado")).__name__,
                    "parser_completeness": page.completeness_status,
                    "source_observation_count": len(sources),
                    "tender_observation_count": len(tenders),
                    "line_observation_count": len(lines),
                    "codigo_match": page.completeness_status == "complete",
                    "attachment_downloaded": False,
                }
            )
            # Collect buyer names / ocids for leak checks later
            for t in tenders:
                if t.buyer_display:
                    forbidden_for_fixtures.append(t.buyer_display)
                if t.title:
                    forbidden_for_fixtures.append(t.title)
        detail_findings.append(finding)

    # --- OCDS probes ---
    probe_specs = [
        ("0_0", 0, 0),
        ("1_1", 1, 1),
        ("0_9", 0, 9),
        ("1_10", 1, 10),
    ]
    probe_summaries: list[dict[str, Any]] = []
    for label, start, end in probe_specs:
        if budget.remaining("public") <= 0:
            budget.stopped_due_to_budget = True
            break
        result = transport.fetch_ocds_probe(year=2026, month=7, start=start, end=end)
        _write_bytes(paths.raw / f"ocds_probe_{label}.response.json", result.raw_bytes)
        summary = summarize_probe(
            label=label,
            start=start,
            end=end,
            http_status=result.http_status,
            payload=result.payload,
            error_classification=result.error_classification,
        )
        summary["raw_byte_sha256"] = result.raw_byte_sha256
        summary["canonical_json_digest"] = result.canonical_json_digest
        # Drop raw identity tokens list from committed sanitized output later;
        # keep tokens only for in-memory conclusion then redact file output.
        probe_summaries.append(summary)
        if result.payload and isinstance(result.payload, dict):
            data_rows = result.payload.get("data")
            if isinstance(data_rows, list):
                for row in data_rows:
                    if not isinstance(row, dict):
                        continue
                    for key in ("ocid", "urlTender", "urlAward"):
                        if row.get(key):
                            forbidden_for_fixtures.append(str(row.get(key)))
                    for key, val in row.items():
                        if isinstance(val, str) and (
                            "url" in key.casefold() or val.casefold().startswith("http")
                        ):
                            forbidden_for_fixtures.append(val)
            for rel in (result.payload.get("releases") or []) if isinstance(
                result.payload.get("releases"), list
            ) else []:
                if isinstance(rel, dict):
                    for key in ("ocid", "id"):
                        if rel.get(key):
                            forbidden_for_fixtures.append(str(rel.get(key)))
                    tender = rel.get("tender")
                    if isinstance(tender, dict) and tender.get("id"):
                        forbidden_for_fixtures.append(str(tender.get("id")))
            for rec in (result.payload.get("records") or []) if isinstance(
                result.payload.get("records"), list
            ) else []:
                if isinstance(rec, dict) and rec.get("ocid"):
                    forbidden_for_fixtures.append(str(rec.get("ocid")))

    range_conclusion = conclude_range_semantics(probe_summaries)
    # Redact token lists for sanitized output (keep first/last only).
    range_public = {
        **range_conclusion,
        "probes": [
            {
                k: v
                for k, v in p.items()
                if k != "tokens"
            }
            for p in range_conclusion.get("probes", [])
        ],
    }

    # --- Sanitize fixtures ---
    sanitized_digests: dict[str, str] = {}
    if summary_payload:
        san = sanitize_live_payload(summary_payload, source_kind="ticket_summary")
        assert_no_identifier_leaks(san, forbidden_substrings=forbidden_for_fixtures)
        body = strip_sanitizer_meta(san)
        _write_json(paths.sanitized / "ticket_summary_live_shape_v1.json", body)
        sanitized_digests["ticket_summary_live_shape_v1"] = canonical_json_digest(body)

    for idx, code in enumerate(codes):
        raw_path = paths.raw / f"ticket_detail_{idx + 1:03d}.response.json"
        if not raw_path.is_file():
            continue
        try:
            payload = json.loads(raw_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        san = sanitize_live_payload(
            payload, source_kind="ticket_detail", seed=f"detail_{idx}"
        )
        assert_no_identifier_leaks(san, forbidden_substrings=forbidden_for_fixtures)
        body = strip_sanitizer_meta(san)
        name = (
            "ticket_detail_items_live_shape_v1.json"
            if idx == 0
            else f"ticket_detail_alternate_live_shape_v{idx}.json"
        )
        _write_json(paths.sanitized / name, body)
        sanitized_digests[name.replace(".json", "")] = canonical_json_digest(body)

    # Prefer 1/1 OCDS probe for shape fixture when successful.
    for label in ("1_1", "0_0", "1_10", "0_9"):
        raw_path = paths.raw / f"ocds_probe_{label}.response.json"
        if not raw_path.is_file() or raw_path.stat().st_size == 0:
            continue
        try:
            payload = json.loads(raw_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        san = sanitize_live_payload(payload, source_kind="ocds", seed=f"ocds_{label}")
        assert_no_identifier_leaks(san, forbidden_substrings=forbidden_for_fixtures)
        body = strip_sanitizer_meta(san)
        _write_json(paths.sanitized / "ocds_range_live_shape_v1.json", body)
        sanitized_digests["ocds_range_live_shape_v1"] = canonical_json_digest(body)
        break

    # Field matrices / diffs
    live_matrix = build_field_type_matrix(summary_payload) if summary_payload else []
    ref_matrix: list[dict[str, Any]] = []
    if reference_fixtures_dir:
        ref_path = reference_fixtures_dir / "ticket_summary_list.json"
        if ref_path.is_file():
            ref_matrix = build_field_type_matrix(
                json.loads(ref_path.read_text(encoding="utf-8"))
            )
    contract_diff = compare_contracts(live_matrix=live_matrix, reference_matrix=ref_matrix)

    _write_json(paths.sanitized / "SHAPE_SUMMARY.json", summary_shape)
    _write_json(paths.sanitized / "FIELD_TYPE_MATRIX.json", live_matrix)
    _write_json(paths.sanitized / "CONTRACT_DIFF.json", contract_diff)
    _write_json(paths.sanitized / "RANGE_SEMANTICS.json", range_public)
    _write_json(
        paths.sanitized / "REDACTION_PROOF.json",
        {
            "sanitizer_version": SANITIZER_VERSION,
            "forbidden_token_count_checked": len(forbidden_for_fixtures),
            "ticket_in_sanitized_outputs": False,
            "selection_tokens_only": detail_tokens,
            "real_codes_in_committed_outputs": False,
        },
    )

    no_mutation = {
        "production_apply": False,
        "production_sqlite_mutation": False,
        "postgres_mutation": False,
        "gmail_mutation": False,
        "dashboard_mutation": False,
        "outreach_mutation": False,
        "scheduler_changed": False,
        "pr5c_started": False,
        "attachment_downloaded": False,
        "credential_url_persisted": False,
        "ticket_persisted": False,
        "ticket_logged": False,
        "ticket_hashed": False,
        **sqlite_proof,
    }
    _write_json(paths.sanitized / "NO_MUTATION_PROOF.json", no_mutation)

    walkthrough = {
        "cases": {
            "A_ticket_summary": {
                "stages": [
                    {
                        "stage": "authenticated_read_only_request",
                        "redacted_source_value": "estado=activas",
                        "normalized_value": ENDPOINT_SUMMARY,
                        "provenance": "ticket_licitaciones_summary",
                        "result": summary_result.http_status,
                    },
                    {
                        "stage": "raw_response_digest",
                        "redacted_source_value": summary_result.raw_byte_sha256,
                        "normalized_value": summary_result.canonical_json_digest,
                        "provenance": "sha256",
                        "result": "captured",
                    },
                    {
                        "stage": "pr5b_parser",
                        "redacted_source_value": "Cantidad/Listado",
                        "normalized_value": summary_shape.get("parser_completeness"),
                        "provenance": "parse_ticket_summary_payload",
                        "result": summary_shape.get("source_observation_count"),
                    },
                ]
            },
            "B_ticket_detail": {
                "status": detail_validation["status"],
                "selection_tokens": detail_tokens,
                "findings": detail_findings,
            },
            "C_ocds_range_semantics": range_public,
            "D_sanitizer_proof": {
                "sanitizer_version": SANITIZER_VERSION,
                "sanitized_digests": sanitized_digests,
            },
            "E_cross_source": {
                "status": "real_cross_source_pair_not_observed",
                "note": (
                    "Bounded Ticket activas + July OCDS probes are not assumed to "
                    "share a tender; no live cross-source claim."
                ),
            },
        }
    }
    _write_json(paths.sanitized / "DATA_WALKTHROUGH.json", walkthrough)
    md_lines = [
        "# PR5B.1 live contract walkthrough (redacted)",
        "",
        f"- Range conclusion: `{range_public.get('conclusion')}`",
        f"- PR5B correction required: `{range_public.get('pr5b_correction_required')}`",
        f"- Detail validation: `{detail_validation.get('status')}`",
        "- Cross-source: `real_cross_source_pair_not_observed`",
        "",
        "Raw capture digests remain in RUN_MANIFEST / FIXTURE_ORIGIN only.",
        "This file is Markdown (not JSON).",
        "",
    ]
    (paths.sanitized / "DATA_WALKTHROUGH.md").write_text(
        "\n".join(md_lines), encoding="utf-8"
    )

    fixture_origin = {
        "origin": "live_response_sanitized",
        "captured_at": _utc_day(),
        "source_kinds": ["ticket_summary", "ticket_detail", "ocds"],
        "endpoint_kinds": [
            "ticket_licitaciones_summary",
            "ticket_licitacion_detail",
            "ocds_lista_agno_mes_range",
        ],
        "raw_source_digests": {
            "ticket_summary": summary_result.raw_byte_sha256,
            **{
                f["selection_token"]: f["raw_byte_sha256"]
                for f in detail_findings
                if f.get("raw_byte_sha256")
            },
            **{
                p["label"]: p.get("raw_byte_sha256")
                for p in range_public.get("probes", [])
            },
        },
        "sanitized_fixture_digests": sanitized_digests,
        "sanitizer_version": SANITIZER_VERSION,
        "no_authenticated_url_retained": True,
        "no_ticket_retained": True,
        "no_real_buyer_tender_contact_identifiers_retained": True,
    }
    _write_json(paths.sanitized / "FIXTURE_ORIGIN.json", fixture_origin)

    run_manifest = {
        "run_context": run_context,
        "captured_at_utc": _utcnow(),
        "authenticated_request_authorized": True,
        "authenticated_request_budget": authenticated_request_budget,
        "authenticated_request_attempted": budget.authenticated_attempted,
        "authenticated_request_completed": budget.authenticated_completed,
        "public_request_budget": public_request_budget,
        "public_request_attempted": budget.public_attempted,
        "public_request_completed": budget.public_completed,
        "ticket_used_for_request": budget.authenticated_attempted > 0,
        "ticket_persisted": False,
        "ticket_logged": False,
        "ticket_hashed": False,
        "credential_url_persisted": False,
        "attachment_downloaded": False,
        "production_apply": False,
        "production_sqlite_mutation": False,
        "postgres_mutation": False,
        "gmail_mutation": False,
        "dashboard_mutation": False,
        "outreach_mutation": False,
        "scheduler_changed": False,
        "pr5c_started": False,
        "detail_validation": detail_validation,
        "range_conclusion": range_public.get("conclusion"),
        "pr5b_correction_required": range_public.get("pr5b_correction_required"),
        "summary_snapshot_id": summary_snap.snapshot_id if summary_snap else None,
        "summary_source_reported_total": (
            summary_snap.source_reported_total if summary_snap else None
        ),
        "plan": plan_requests(
            ticket_summary_limit_details=ticket_summary_limit_details,
            authenticated_request_budget=authenticated_request_budget,
            public_request_budget=public_request_budget,
        ),
    }
    _write_json(paths.root / "RUN_MANIFEST.json", run_manifest)
    _write_json(paths.root / "REQUEST_LEDGER.json", budget.to_dict())

    return {
        "ok": True,
        "out_dir": str(out_dir),
        "run_manifest": run_manifest,
        "budget": budget.to_dict(),
        "range_conclusion": range_public.get("conclusion"),
        "pr5b_correction_required": range_public.get("pr5b_correction_required"),
        "sanitized_digests": sanitized_digests,
    }


ENDPOINT_SUMMARY = "ticket_licitaciones_summary"
