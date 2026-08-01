#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# SAFETY: production SQLite opened mode=ro + query_only only when --sqlite-path set.
# Never --apply. Never prints ChileCompra API ticket values.
# -----------------------------------------------------------------------------
"""Emit PR5A live-relevance design audit artifacts (read-only)."""

from __future__ import annotations

import argparse
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
from origenlab_email_pipeline.commercial_procurement_live_relevance.paths import (  # noqa: E402
    existing_paths_document,
)
from origenlab_email_pipeline.commercial_procurement_live_relevance.schema_design import (  # noqa: E402
    proposed_schema_document,
)
from origenlab_email_pipeline.commercial_procurement_live_relevance.taxonomy import (  # noqa: E402
    taxonomy_mapping_document,
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


def _funnel_from_sqlite(conn: sqlite3.Connection, as_of_date: str) -> dict:
    def q1(sql: str, args: tuple = ()) -> int:
        return int(conn.execute(sql, args).fetchone()[0])

    contexts = dict(
        conn.execute(
            "SELECT procurement_context, COUNT(*) FROM commercial_procurement_signal GROUP BY 1"
        )
    )
    verified = q1("SELECT COUNT(*) FROM commercial_procurement_signal")
    active_positive = q1(
        """
        SELECT COUNT(*) FROM commercial_procurement_signal
        WHERE (LOWER(COALESCE(status_name,'')) = 'publicada' OR status_code = '5')
          AND close_at IS NOT NULL AND close_at != ''
          AND close_at >= ?
        """,
        (as_of_date,),
    )
    linked = q1(
        "SELECT COUNT(*) FROM commercial_procurement_account_resolution WHERE resolution_status='linked'"
    )
    linked_with_contact = q1(
        """
        SELECT COUNT(*) FROM commercial_procurement_account_resolution r
        WHERE r.resolution_status='linked'
          AND EXISTS (
            SELECT 1 FROM commercial_identity_contact c WHERE c.account_id=r.account_id
          )
        """
    )
    return {
        "source_tender_observations_external_leads_raw": q1(
            "SELECT COUNT(*) FROM external_leads_raw"
        ),
        "verified_tenders_pr4_signals": verified,
        "pr4_context_distribution": contexts,
        "currently_active_tenders_pr4_positive_evidence": active_positive,
        "active_tenders_with_any_equipment_hit": 0 if active_positive == 0 else None,
        "active_tenders_passing_negative_exclusions": 0 if active_positive == 0 else None,
        "active_strongly_relevant_tenders": 0 if active_positive == 0 else None,
        "active_relevant_linked_to_pr2": 0 if active_positive == 0 else None,
        "linked_active_with_pr2_contacts": 0 if active_positive == 0 else None,
        "with_role_suitable_verified_contacts": 0 if active_positive == 0 else None,
        "requiring_contact_research": 0 if active_positive == 0 else None,
        "ambiguous_buyer_account": q1(
            "SELECT COUNT(*) FROM commercial_procurement_account_resolution WHERE resolution_status='ambiguous'"
        ),
        "blocked_by_suppression_or_outreach": 0 if active_positive == 0 else None,
        "proposed_outreach_review_candidates": 0,
        "historical_linked_signals": linked,
        "historical_linked_signals_with_pr2_contact": linked_with_contact,
        "note": (
            "Active* funnel stages are zero when PR4 SQLite corpus has no positive "
            "active evidence as of America/Santiago. Live open rows may still exist "
            "in equipment-first API artifacts — reported separately."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite-path", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument(
        "--equipment-queue-csv",
        type=Path,
        default=None,
        help="Optional equipment-first operator queue CSV for Case A",
    )
    parser.add_argument("--seeds-json", type=Path, default=None)
    args = parser.parse_args(argv)

    db = require_explicit_sqlite_path(args.sqlite_path)
    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)

    now_utc = datetime.now(timezone.utc)
    now_scl = now_utc.astimezone(SANTIAGO)
    as_of_date = now_scl.date().isoformat()

    ticket_ok = _ticket_configured()
    conn = _open_ro(db)
    conn.execute("BEGIN")
    funnel = _funnel_from_sqlite(conn, as_of_date)
    meta = {
        str(r["meta_key"]): str(r["meta_value"])
        for r in conn.execute(
            "SELECT meta_key, meta_value FROM commercial_procurement_build_meta"
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
    conn.execute("COMMIT")
    conn.close()

    seeds: dict = {}
    if args.seeds_json and args.seeds_json.is_file():
        seeds = json.loads(args.seeds_json.read_text())

    queue_csv = args.equipment_queue_csv
    if queue_csv is None:
        # newest chilecompra api operator queue under active/current
        current = _ROOT / "reports/out/active/current"
        cands = sorted(current.glob("equipment_first_operator_queue_chilecompra_api_*.csv"))
        queue_csv = cands[-1] if cands else None

    open_in_artifact = 0
    if queue_csv and queue_csv.is_file():
        import csv

        with queue_csv.open(newline="", encoding="utf-8", errors="replace") as fh:
            rows = list(csv.DictReader(fh))
        open_in_artifact = sum(1 for r in rows if r.get("validity_status") == "open")

    funnel["live_open_rows_in_equipment_first_artifact"] = open_in_artifact
    funnel["equipment_first_artifact"] = str(queue_csv) if queue_csv else None
    funnel["genuine_active_tenders_exist_anywhere_in_repo_sources"] = (
        funnel["currently_active_tenders_pr4_positive_evidence"] > 0 or open_in_artifact > 0
    )

    bundle = build_pr5_walkthrough_bundle(
        seeds=seeds, open_queue_csv=queue_csv, as_of=now_scl
    )
    md = render_walkthrough_markdown(bundle)

    (out / "CURRENT_FUNNEL.json").write_text(
        json.dumps(funnel, indent=2, sort_keys=True) + "\n"
    )
    (out / "EXISTING_PATHS.json").write_text(
        json.dumps(existing_paths_document(), indent=2, sort_keys=True) + "\n"
    )
    (out / "TAXONOMY_MAPPING.json").write_text(
        json.dumps(taxonomy_mapping_document(), indent=2, sort_keys=True) + "\n"
    )
    (out / "PROPOSED_SCHEMA.json").write_text(
        json.dumps(proposed_schema_document(), indent=2, sort_keys=True) + "\n"
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
        "statuses": list(
            __import__(
                "origenlab_email_pipeline.commercial_procurement_live_relevance.constants",
                fromlist=["CONTACT_RESOLUTION_STATUSES"],
            ).CONTACT_RESOLUTION_STATUSES
        ),
        "historical_linked_with_pr2_contact": funnel[
            "historical_linked_signals_with_pr2_contact"
        ],
        "live_outreach_review_candidates": 0,
        "chilecompra_api_ticket_configured": ticket_ok,
    }
    (out / "CONTACT_RESOLUTION_AUDIT.json").write_text(
        json.dumps(contact_audit, indent=2, sort_keys=True) + "\n"
    )

    impl = """# PR5 implementation sequence

1. **PR5A** (this PR) — design, audit, redacted walkthrough, no persistence
2. **PR5B** — deterministic planner + fixture validation (active/relevance/contact)
3. **PR5C** — additive SQLite persistence + gated production apply
4. **PR5D** — official live acquisition adapter + scheduling (API primary, file fallback)
5. **PR6** — targeted external contact enrichment + human review
6. **PR7** — API/dashboard read exposure

Do not start PR5B in this branch.
"""
    (out / "IMPLEMENTATION_SEQUENCE.md").write_text(impl)

    summary = {
        "checkpoint": "PR5A_DESIGN_AUDIT",
        "generated_at_utc": now_utc.isoformat(),
        "as_of_america_santiago": now_scl.isoformat(),
        "pr4_counts": pr4_counts,
        "pr4_semantic_plan_digest": meta.get("semantic_plan_digest"),
        "funnel_active_pr4": funnel["currently_active_tenders_pr4_positive_evidence"],
        "funnel_live_open_equipment_artifact": open_in_artifact,
        "genuine_active_tenders_exist": funnel[
            "genuine_active_tenders_exist_anywhere_in_repo_sources"
        ],
        "proposed_outreach_review_candidates": 0,
        "chilecompra_api_ticket_configured": ticket_ok,
        "canonical_acquisition_lane": "Mercado Público official API (estado/fecha/codigo)",
        "fallback_acquisition_lane": "Official Licitacion_Publicada / file bulk ingest",
        "no_production_mutation": True,
        "no_apply": True,
        "no_pr5b": True,
    }
    (out / "SUMMARY.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(f"wrote_out_dir={out}")
    print(f"genuine_active={summary['genuine_active_tenders_exist']}")
    print(f"ticket_configured={ticket_ok}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
