#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# SAFETY: production SQLite opened mode=ro + query_only only when --sqlite-path set.
# Never --apply. Never prints ChileCompra API ticket values.
# Never makes authenticated ChileCompra API requests.
# -----------------------------------------------------------------------------
"""Emit corrected PR5A live-relevance design audit artifacts (read-only)."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from origenlab_email_pipeline.chilecompra_api import TICKET_ENV_VAR  # noqa: E402
from origenlab_email_pipeline.commercial_identity.builder import (  # noqa: E402
    require_explicit_sqlite_path,
)
from origenlab_email_pipeline.commercial_procurement_live_relevance.artifact_open import (  # noqa: E402
    inspect_artifact_provenance,
    pick_best_open_row,
    summarize_open_classes,
)
from origenlab_email_pipeline.commercial_procurement_live_relevance.paths import (  # noqa: E402
    existing_paths_document,
)
from origenlab_email_pipeline.commercial_procurement_live_relevance.constants import (  # noqa: E402
    CONTACT_RESOLUTION_STATUSES,
    CONTACT_STATUS_SEMANTICS,
)
from origenlab_email_pipeline.commercial_procurement_live_relevance.production_cases import (  # noqa: E402
    assert_no_forbidden_identifier_leaks,
    select_production_cases,
)
from origenlab_email_pipeline.commercial_procurement_live_relevance.schema_design import (  # noqa: E402
    proposed_schema_document,
)
from origenlab_email_pipeline.commercial_procurement_live_relevance.taxonomy import (  # noqa: E402
    taxonomy_mapping_document,
    validate_taxonomy_mapping_completeness,
)
from origenlab_email_pipeline.commercial_procurement_live_relevance.walkthrough import (  # noqa: E402
    build_pr5_walkthrough_bundle,
    render_walkthrough_markdown,
)

SANTIAGO = ZoneInfo("America/Santiago")


def _ticket_configured() -> bool:
    if (os.environ.get(TICKET_ENV_VAR) or "").strip():
        return True
    for p in (_ROOT / ".env", Path.home() / ".config/origenlab/.env"):
        if not p.is_file():
            continue
        for line in p.read_text(errors="ignore").splitlines():
            if line.strip().startswith("#"):
                continue
            if line.strip().startswith(f"{TICKET_ENV_VAR}="):
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                if val:
                    return True
    return False


def _open_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _acquisition_lanes_document() -> dict:
    return {
        "authenticated_requests_in_pr5a": False,
        "rate_limits": "not found in official documentation (do not invent)",
        "lanes": {
            "legacy_ticket_mercado_publico_api": {
                "base": "https://api.mercadopublico.cl/servicios/v1/publico/licitaciones.json",
                "auth": "ticket query parameter (CHILECOMPRA_API_TICKET)",
                "queries": [
                    "codigo",
                    "fecha (ddMMyyyy)",
                    "estado (e.g. activas)",
                    "codigo+detail for line items",
                ],
                "status_codes_documented": {
                    "5": "Publicada (active published)",
                    "6/7/8/18/19": "inactive family used in repo clients",
                },
                "role": "Active discovery + code detail",
            },
            "official_ocds_api": {
                "notes": (
                    "Official ChileCompra OCDS endpoints provide public tender "
                    "documents suitable for reconciliation snapshots. Public docs "
                    "describe monthly range queries and a 1,000-record maximum per "
                    "request; no-registration public access is advertised for OCDS."
                ),
                "role": "Reconciliation and durable source snapshots",
                "pagination": "Required for ranges exceeding 1,000 records",
                "authenticated_in_pr5a": False,
            },
            "bulk_official_downloads": {
                "example": "Licitacion_Publicada-style semicolon CSV exports",
                "grain": "Often line-level rows grouped by tender code",
                "role": "Historical backfill / reproducible corpus for PR4 rebuild",
                "update_cadence": "Treat as batch; not a substitute for live open discovery",
            },
        },
        "recommended_composition": {
            "primary_discovery": "ticket API (estado=activas + code detail)",
            "reconciliation": "OCDS snapshots",
            "historical_backfill": "bulk official downloads / file ingest",
            "do_not": "scrape Mercado Público HTML when official API/OCDS/files suffice",
        },
    }


def _funnel(conn: sqlite3.Connection, as_of_date: str, open_counts: dict[str, int]) -> dict:
    """Funnel grains. PR4 active uses persisted procurement_context (not date-only lexical)."""
    def q1(sql: str, args: tuple = ()) -> int:
        return int(conn.execute(sql, args).fetchone()[0])

    chilecompra_raw = q1(
        "SELECT COUNT(*) FROM external_leads_raw WHERE source_name = 'chilecompra'"
    )
    verified = q1("SELECT COUNT(*) FROM commercial_procurement_signal")
    # Persisted PR4 context is the active-status contract for this audit.
    # Do not use date-only lexical close_at comparisons as the future PR5 contract.
    active_pr4 = q1(
        """
        SELECT COUNT(*) FROM commercial_procurement_signal
        WHERE procurement_context = 'tender_active'
        """
    )
    historical_pr4 = q1(
        """
        SELECT COUNT(*) FROM commercial_procurement_signal
        WHERE procurement_context = 'historical_tender'
        """
    )
    linked_tenders = q1(
        "SELECT COUNT(*) FROM commercial_procurement_account_resolution WHERE resolution_status='linked'"
    )
    linked_accounts = q1(
        """
        SELECT COUNT(DISTINCT account_id)
        FROM commercial_procurement_account_resolution
        WHERE resolution_status='linked' AND account_id IS NOT NULL
        """
    )
    linked_tenders_with_any_contact = q1(
        """
        SELECT COUNT(*) FROM commercial_procurement_account_resolution r
        WHERE r.resolution_status='linked'
          AND EXISTS (
            SELECT 1 FROM commercial_identity_contact c WHERE c.account_id=r.account_id
          )
        """
    )
    linked_accounts_with_any_contact = q1(
        """
        SELECT COUNT(DISTINCT r.account_id)
        FROM commercial_procurement_account_resolution r
        WHERE r.resolution_status='linked'
          AND EXISTS (
            SELECT 1 FROM commercial_identity_contact c WHERE c.account_id=r.account_id
          )
        """
    )

    ccols = [r[1] for r in conn.execute("PRAGMA table_info(commercial_identity_contact)")]
    email_col = (
        "normalized_email"
        if "normalized_email" in ccols
        else ("email" if "email" in ccols else None)
    )
    role_col = "role" if "role" in ccols else None
    contacts_with_email = None
    contacts_with_role = None
    if email_col:
        contacts_with_email = q1(
            f"""
            SELECT COUNT(*) FROM commercial_identity_contact
            WHERE {email_col} IS NOT NULL AND TRIM({email_col}) != ''
            """
        )
    if role_col:
        contacts_with_role = q1(
            f"""
            SELECT COUNT(*) FROM commercial_identity_contact
            WHERE {role_col} IS NOT NULL AND TRIM({role_col}) != ''
            """
        )

    return {
        "chilecompra_raw_observation_rows": chilecompra_raw,
        "distinct_raw_canonical_tender_candidates": None,
        "distinct_raw_canonical_tender_candidates_note": (
            "not_evaluated in PR5A without a shared raw→canonical tender extractor "
            "over external_leads_raw; PR4 verified tenders used as verified grain"
        ),
        "pr4_verified_tenders": verified,
        "pr4_currently_active_positive_evidence": active_pr4,
        "pr4_active_status_method": "procurement_context=tender_active",
        "pr4_historical_tender_count": historical_pr4,
        "pr4_active_note": (
            "Date-only lexical close_at comparison is not the PR5 active contract; "
            "this audit uses persisted PR4 procurement_context. "
            f"as_of_date={as_of_date} recorded for operator context only."
        ),
        "artifact_declared_open_rows_any_validity_open": open_counts.get(
            "_validity_open_raw", None
        ),
        "strict_recent_artifact_declared_open": open_counts.get(
            "recent_artifact_declared_open", 0
        ),
        "stale_artifact_declared_open": open_counts.get("stale_artifact_declared_open", 0),
        "artifact_declared_open_unverified_provenance": open_counts.get(
            "artifact_declared_open_unverified_provenance", 0
        ),
        "live_verified_open_tenders": 0,
        "distinct_linked_tender_rows": linked_tenders,
        "distinct_linked_accounts": linked_accounts,
        "linked_tenders_whose_account_has_any_pr2_contact": linked_tenders_with_any_contact,
        "distinct_linked_accounts_with_any_pr2_contact": linked_accounts_with_any_contact,
        "contacts_with_known_relevant_roles": contacts_with_role,
        "contacts_with_known_relevant_roles_note": (
            "counts non-empty role field only; role suitability taxonomy not evaluated"
        ),
        "contacts_with_verified_email": contacts_with_email,
        "suppression_passing_contacts": None,
        "suppression_passing_contacts_note": "not_evaluated (would require full suppression join policy)",
        "outreach_review_candidates": 0 if active_pr4 == 0 else None,
        "open_class_counts": {
            k: v for k, v in open_counts.items() if not k.startswith("_")
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite-path", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--equipment-queue-csv", type=Path, default=None)
    parser.add_argument(
        "--fixture-seeds-json",
        type=Path,
        default=None,
        help="Fixture/test mode only — never describe as production-derived",
    )
    args = parser.parse_args(argv)

    db = require_explicit_sqlite_path(args.sqlite_path)
    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)

    now_utc = datetime.now(timezone.utc)
    now_scl = now_utc.astimezone(SANTIAGO)
    as_of_date = now_scl.date().isoformat()
    ticket_ok = _ticket_configured()

    manifest_path = _ROOT / "reports/out/active/current/manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.is_file()
        else None
    )

    queue_csv = args.equipment_queue_csv
    if queue_csv is None:
        current = _ROOT / "reports/out/active/current"
        # Prefer chilecompra_api dated queues, then published non-api basename
        api_cands = sorted(
            current.glob("equipment_first_operator_queue_chilecompra_api_*.csv")
        )
        pub_cands = sorted(current.glob("equipment_first_operator_queue_20*.csv"))
        queue_csv = (api_cands or pub_cands or [None])[-1]

    open_counts: dict[str, int] = {}
    validity_open_raw = 0
    if queue_csv and queue_csv.is_file():
        with queue_csv.open(newline="", encoding="utf-8", errors="replace") as fh:
            rows = list(csv.DictReader(fh))
        validity_open_raw = sum(
            1 for r in rows if str(r.get("validity_status") or "").lower() == "open"
        )
        provenance = inspect_artifact_provenance(
            queue_csv, as_of=now_scl, manifest=manifest
        )
        _, _, all_oc = pick_best_open_row(rows, as_of=now_scl, provenance=provenance)
        open_counts = summarize_open_classes(all_oc)
        open_counts["_validity_open_raw"] = validity_open_raw
        (out / "ARTIFACT_PROVENANCE.json").write_text(
            json.dumps(provenance.to_dict(), indent=2, sort_keys=True) + "\n"
        )

    fixture_mode = args.fixture_seeds_json is not None
    seeds: dict = {}
    forbidden: set[str] = set()
    selection_meta: dict = {}

    conn = _open_ro(db)
    conn.execute("BEGIN")
    meta = {
        str(r["meta_key"]): str(r["meta_value"])
        for r in conn.execute(
            "SELECT meta_key, meta_value FROM commercial_procurement_build_meta"
        )
    }
    pr2 = {
        str(r["meta_key"]): str(r["meta_value"])
        for r in conn.execute(
            "SELECT meta_key, meta_value FROM commercial_identity_build_meta"
        )
    }
    pr4_counts = {
        t: int(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
        for t in (
            "commercial_procurement_signal",
            "commercial_procurement_account_resolution",
            "commercial_procurement_evidence",
            "commercial_procurement_conflict",
            "commercial_procurement_enrichment_candidate",
            "commercial_procurement_build_meta",
        )
    }
    funnel = _funnel(conn, as_of_date, open_counts)

    if fixture_mode:
        seeds = json.loads(args.fixture_seeds_json.read_text(encoding="utf-8"))
        selection_meta = {
            "production_derived": False,
            "fixture_seeds_used": True,
            "fixture_path": str(args.fixture_seeds_json),
        }
    else:
        selected = select_production_cases(
            conn,
            pr4_semantic_digest=str(meta.get("semantic_plan_digest") or ""),
            pr2_identity_fingerprint=str(pr2.get("identity_fingerprint") or ""),
        )
        seeds = {
            "case_b": selected.case_b,
            "case_c": selected.case_c,
            "case_d": selected.case_d,
            "case_e": selected.case_e,
        }
        forbidden = selected.forbidden_values
        selection_meta = selected.selection_meta

    conn.execute("COMMIT")
    conn.close()

    bundle = build_pr5_walkthrough_bundle(
        seeds=seeds,
        open_queue_csv=queue_csv,
        as_of=now_scl,
        manifest=manifest,
        forbidden_values=forbidden,
        fixture_mode=fixture_mode,
    )
    md = render_walkthrough_markdown(bundle)

    (out / "CURRENT_FUNNEL.json").write_text(
        json.dumps(funnel, indent=2, sort_keys=True) + "\n"
    )
    (out / "EXISTING_PATHS.json").write_text(
        json.dumps(existing_paths_document(), indent=2, sort_keys=True) + "\n"
    )
    tax = taxonomy_mapping_document()
    tax["completeness"] = validate_taxonomy_mapping_completeness()
    (out / "TAXONOMY_MAPPING.json").write_text(
        json.dumps(tax, indent=2, sort_keys=True) + "\n"
    )
    (out / "PROPOSED_SCHEMA.json").write_text(
        json.dumps(proposed_schema_document(), indent=2, sort_keys=True) + "\n"
    )
    (out / "ACQUISITION_LANES.json").write_text(
        json.dumps(_acquisition_lanes_document(), indent=2, sort_keys=True) + "\n"
    )
    (out / "CASE_SELECTION_META.json").write_text(
        json.dumps(selection_meta, indent=2, sort_keys=True, default=str) + "\n"
    )
    (out / "DATA_WALKTHROUGH.json").write_text(
        json.dumps(
            {
                "summary": bundle.summary,
                "case_a": bundle.case_a,
                "case_b": bundle.case_b,
                "case_c": bundle.case_c,
                "case_d": bundle.case_d,
                "case_e": bundle.case_e,
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
        + "\n"
    )
    (out / "DATA_WALKTHROUGH.md").write_text(md)

    contact_audit = {
        "search_order": [
            "PR2 contacts linked to resolved account",
            "lead_master contact fields",
            "lead_outreach_enrichment",
            "email participants / business-mart contacts",
            "no external lookup in PR5A–PR5C",
        ],
        "statuses": list(CONTACT_RESOLUTION_STATUSES),
        "status_semantics": CONTACT_STATUS_SEMANTICS,
        "table_grain": {
            "commercial_procurement_contact_resolution": "exactly one summary row per candidate",
            "commercial_procurement_contact_candidate": "zero or more considered contacts",
        },
        "chilecompra_api_ticket_configured": ticket_ok,
        "account_resolution_required_before_contact_search": True,
    }
    (out / "CONTACT_RESOLUTION_AUDIT.json").write_text(
        json.dumps(contact_audit, indent=2, sort_keys=True) + "\n"
    )

    impl = """# PR5 implementation sequence (corrected)

1. **PR5A** — corrected design/audit (this branch)
2. **PR5B** — acquisition source contract, ticket/OCDS parsers and captured fixtures
3. **PR5C** — deterministic candidate planner
4. **PR5D** — additive persistence and gated apply
5. **PR5E** — production acquisition scheduling and refresh operations
6. **PR6** — targeted external contact enrichment
7. **PR7** — API/dashboard exposure

Do not implement PR5B in this branch.
"""
    (out / "IMPLEMENTATION_SEQUENCE.md").write_text(impl)

    summary = {
        "checkpoint": "PR5A_DESIGN_AUDIT_CORRECTED",
        "generated_at_utc": now_utc.isoformat(),
        "as_of_america_santiago": now_scl.isoformat(),
        "pr4_counts": pr4_counts,
        "pr4_semantic_plan_digest": meta.get("semantic_plan_digest"),
        "pr4_active_tenders": funnel["pr4_currently_active_positive_evidence"],
        "recent_artifact_declared_open": funnel["strict_recent_artifact_declared_open"],
        "live_verified_open": 0,
        "current_status_independently_revalidated": False,
        "case_a_open_classification": bundle.summary.get("case_a_open_classification"),
        "outreach_review_candidates": funnel["outreach_review_candidates"],
        "chilecompra_api_ticket_configured": ticket_ok,
        "canonical_acquisition_lane": "ticket Mercado Público API for active discovery + code detail",
        "reconciliation_lane": "official OCDS snapshots",
        "fallback_acquisition_lane": "bulk official downloads / file ingest",
        "fixture_mode": fixture_mode,
        "no_production_mutation": True,
        "no_apply": True,
        "no_authenticated_api_request": True,
        "no_pr5b": True,
        "terminology_note": (
            "Do not call artifact-only rows live/genuine active; use open_classification."
        ),
    }
    (out / "SUMMARY.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")

    # Identifier + ticket leak gate on all local report text artifacts.
    report_blob = ""
    for name in (
        "SUMMARY.json",
        "CASE_SELECTION_META.json",
        "DATA_WALKTHROUGH.json",
        "DATA_WALKTHROUGH.md",
        "CURRENT_FUNNEL.json",
        "ARTIFACT_PROVENANCE.json",
        "CONTACT_RESOLUTION_AUDIT.json",
    ):
        p = out / name
        if p.is_file():
            report_blob += p.read_text(encoding="utf-8")
    assert_no_forbidden_identifier_leaks(report_blob, forbidden)
    for secret_token in ("ticket=", "Ticket=", "TICKET="):
        # Structural keys like chilecompra_api_ticket_configured are boolean-only.
        if "SECRET" in report_blob.upper() and "ticket=SECRET" in report_blob:
            raise AssertionError("ticket secret leaked into report artifacts")
        if f"{secret_token}SECRET" in report_blob:
            raise AssertionError("ticket secret leaked into report artifacts")

    print(f"wrote_out_dir={out}")
    print("live_verified_open=0")
    print(
        f"recent_artifact_declared_open={summary['recent_artifact_declared_open']}"
    )
    print(f"case_a_class={summary['case_a_open_classification']}")
    print(f"case_d_available={selection_meta.get('case_d_available', 'fixture')}")
    print(f"ticket_configured={ticket_ok}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
