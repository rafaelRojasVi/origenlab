#!/usr/bin/env python3
"""Read-only verification queries after sync_dashboard_postgres_mirror (Phase 0)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from origenlab_email_pipeline.mart_core_postgres_migrate import resolve_postgres_url  # noqa: E402


def evaluate_render_dashboard_assertions(
    out: dict[str, object],
    *,
    min_warm_cases: int = 1,
    # PHASE W1 (2026-08): the legacy equipment writer is frozen for observation
    # and no longer gates Render readiness by default. Pass an explicit count
    # for a manual/legacy diagnostic check only.
    expect_equipment_count: int | None = None,
    expect_archive_emails: int = 0,
) -> list[str]:
    """Return human-readable assertion failures for Render dashboard mirror checks."""
    errors: list[str] = []

    archive_emails = out.get("archive_emails")
    if archive_emails is None:
        errors.append("archive.emails table missing (expected schema with count 0)")
    elif archive_emails != expect_archive_emails:
        errors.append(
            f"archive.emails count={archive_emails!r}, expected {expect_archive_emails}"
        )

    warm = out.get("api_v_warm_case")
    if not isinstance(warm, int) or warm < min_warm_cases:
        errors.append(
            f"api.v_warm_case count={warm!r}, expected >= {min_warm_cases}"
        )

    if expect_equipment_count is not None:
        equipment = out.get("api_v_equipment_opportunity")
        if equipment != expect_equipment_count:
            errors.append(
                f"api.v_equipment_opportunity count={equipment!r}, "
                f"expected {expect_equipment_count}"
            )

    sync_row = out.get("dashboard_sync_run_latest")
    if not sync_row:
        errors.append("reporting.dashboard_sync_run has no rows")
    elif isinstance(sync_row, (list, tuple)):
        if len(sync_row) < 4:
            errors.append(f"dashboard_sync_run_latest malformed: {sync_row!r}")
        else:
            _run_id, status, _started, finished = sync_row[:4]
            if status != "success":
                errors.append(
                    f"latest dashboard_sync_run status={status!r}, expected 'success'"
                )
            if finished is None:
                errors.append("latest dashboard_sync_run finished_at is NULL")
    else:
        errors.append(f"dashboard_sync_run_latest unexpected shape: {sync_row!r}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify dashboard Postgres mirror row counts.")
    parser.add_argument("--postgres-url", default=None)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument(
        "--assert-render-dashboard",
        action="store_true",
        help="Exit 1 when Render dashboard mirror checks fail (warm cases, equipment, sync run).",
    )
    parser.add_argument(
        "--min-warm-cases",
        type=int,
        default=1,
        help="Minimum api.v_warm_case rows when --assert-render-dashboard (default 1).",
    )
    parser.add_argument(
        "--expect-equipment-count",
        type=int,
        default=None,
        help=(
            "[LEGACY] Expected api.v_equipment_opportunity count. Manual/legacy "
            "diagnostic only — omitted by default, so --assert-render-dashboard "
            "does not gate Render readiness on the frozen equipment writer."
        ),
    )
    parser.add_argument(
        "--expect-archive-emails",
        type=int,
        default=0,
        help="Expected archive.emails count when --assert-render-dashboard (default 0).",
    )
    args = parser.parse_args()

    try:
        import psycopg
    except ImportError:
        print("ERROR: psycopg missing — run: uv sync --group dev", file=sys.stderr)
        return 2

    pg_url = resolve_postgres_url(args.postgres_url or os.environ.get("ORIGENLAB_POSTGRES_URL"))
    out: dict[str, object] = {"postgres_url_redacted": pg_url.split("@")[-1] if "@" in pg_url else pg_url}

    queries: dict[str, str] = {
        "schemas": """
            SELECT schema_name
            FROM information_schema.schemata
            WHERE schema_name IN (
              'archive','ops','mart','leads','commercial','outbound','supplier','reporting'
            )
            ORDER BY 1
        """,
        "dashboard_sync_run_latest": """
            SELECT id, status, started_at, finished_at, postgres_url_redacted
            FROM reporting.dashboard_sync_run
            ORDER BY id DESC
            LIMIT 1
        """,
        "mart_contact_master": "SELECT COUNT(*)::bigint FROM mart.contact_master",
        "api_v_warm_case": "SELECT COUNT(*)::bigint FROM api.v_warm_case",
        "api_v_equipment_opportunity": "SELECT COUNT(*)::bigint FROM api.v_equipment_opportunity",
        "outbound_outreach_state": "SELECT COUNT(*)::bigint FROM outbound.outreach_contact_state",
        "commercial_warm_case": "SELECT COUNT(*)::bigint FROM commercial.warm_case",
        "equipment_opportunity": "SELECT COUNT(*)::bigint FROM commercial.equipment_opportunity",
        "equipment_source_canonical": """
            SELECT id, is_canonical FROM commercial.equipment_opportunity_source ORDER BY id DESC LIMIT 3
        """,
        "archive_emails": "SELECT COUNT(*)::bigint FROM archive.emails",
    }

    def _table_exists(cur, schema: str, table: str) -> bool:
        cur.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = %s AND table_name = %s
            """,
            (schema, table),
        )
        return cur.fetchone() is not None

    def _column_exists(cur, schema: str, table: str, column: str) -> bool:
        cur.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s AND column_name = %s
            """,
            (schema, table, column),
        )
        return cur.fetchone() is not None

    with psycopg.connect(pg_url) as conn:
        with conn.cursor() as cur:
            cur.execute(queries["schemas"])
            out["schemas_present"] = [r[0] for r in cur.fetchall()]

            for key in (
                "dashboard_sync_run_latest",
                "mart_contact_master",
                "outbound_outreach_state",
                "api_v_warm_case",
                "api_v_equipment_opportunity",
            ):
                cur.execute(queries[key])
                row = cur.fetchone()
                out[key] = row[0] if row and len(row) == 1 else list(row) if row else None

            for optional in ("commercial_warm_case", "equipment_opportunity"):
                try:
                    cur.execute(queries[optional])
                    out[optional] = cur.fetchone()[0]
                except Exception as exc:  # noqa: BLE001
                    conn.rollback()
                    out[optional] = {"error": str(exc)}

            try:
                cur.execute(queries["equipment_source_canonical"])
                out["equipment_source_canonical"] = cur.fetchall()
            except Exception as exc:  # noqa: BLE001
                conn.rollback()
                out["equipment_source_canonical"] = {"error": str(exc)}

            if _table_exists(cur, "archive", "emails"):
                cur.execute(queries["archive_emails"])
                out["archive_emails"] = cur.fetchone()[0]
                if _column_exists(cur, "archive", "emails", "body"):
                    cur.execute(
                        """
                        SELECT COUNT(*)::bigint FROM archive.emails
                        WHERE body IS NOT NULL AND length(trim(body)) > 0
                        """
                    )
                    out["archive_emails_with_body"] = cur.fetchone()[0]
                else:
                    out["archive_emails_with_body"] = "column_absent"
            else:
                out["archive_emails"] = None
                out["archive_emails_with_body"] = "schema_absent"

    if args.assert_render_dashboard:
        failures = evaluate_render_dashboard_assertions(
            out,
            min_warm_cases=args.min_warm_cases,
            expect_equipment_count=args.expect_equipment_count,
            expect_archive_emails=args.expect_archive_emails,
        )
        out["render_dashboard_assertions"] = {
            "passed": not failures,
            "failures": failures,
        }

    text = json.dumps(out, indent=2, default=str)
    print(text)
    if args.json_out:
        args.json_out.write_text(text + "\n", encoding="utf-8")

    if args.assert_render_dashboard:
        failures = out.get("render_dashboard_assertions", {}).get("failures", [])
        if failures:
            print("ASSERT FAILED:", file=sys.stderr)
            for msg in failures:
                print(f"  - {msg}", file=sys.stderr)
            return 1
        print("ASSERT OK: Render dashboard mirror checks passed.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
