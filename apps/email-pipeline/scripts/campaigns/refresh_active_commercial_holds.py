#!/usr/bin/env python3
"""Refresh derived outbound holds from durable CRM active commercial work.

The durable CRM in Postgres remains authoritative. This command reads active
sales opportunities and active customer-quote revisions, resolves the best
known contact email, and optionally replaces the derived SQLite
``outbound_commercial_hold`` projection used by outbound gates.

Dry-run is the default. Pass ``--apply`` to replace the SQLite projection and
write its successful-refresh marker. No Gmail call is made and no Postgres row
is mutated.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from origenlab_email_pipeline.business_mart import emails_in
from origenlab_email_pipeline.config import load_settings

ACTIVE_SALES_OPPORTUNITY_STAGES: tuple[str, ...] = (
    "new",
    "qualifying",
    "qualified",
    "quoting",
    "negotiating",
)

ACTIVE_QUOTE_REVISION_STATUSES: tuple[str, ...] = (
    "draft",
    "pending_approval",
    "adjustments_requested",
    "approved",
    "sent",
)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS outbound_commercial_hold (
  email_norm TEXT PRIMARY KEY,
  reasons_json TEXT NOT NULL,
  source_count INTEGER NOT NULL CHECK (source_count >= 1),
  refreshed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outbound_commercial_hold_meta (
  singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
  refreshed_at TEXT NOT NULL,
  active_source_rows INTEGER NOT NULL CHECK (active_source_rows >= 0),
  held_unique_emails INTEGER NOT NULL CHECK (held_unique_emails >= 0),
  unresolved_active_rows_without_email INTEGER NOT NULL CHECK (unresolved_active_rows_without_email >= 0)
);
"""

# The fallback to api.v_commercial_opportunity.contact_display_email is
# intentional: older promoted opportunities can lack primary_crm_contact_id,
# while the machine opportunity still carries the public/contact evidence used
# at promotion time. Durable CRM contact email wins when present.
_QUERY = """
WITH active_opportunities AS (
  SELECT
    COALESCE(
      NULLIF(lower(trim(crm_contact.primary_email)), ''),
      NULLIF(lower(trim(machine.contact_display_email)), '')
    ) AS email_norm,
    'sales_opportunity'::text AS source_kind,
    so.sales_opportunity_id::text AS source_id,
    so.stage::text AS state
  FROM api.v_commercial_sales_opportunity AS so
  LEFT JOIN api.v_commercial_opportunity AS machine
    ON machine.opportunity_id = so.source_opportunity_id
  LEFT JOIN api.v_commercial_contact AS crm_contact
    ON crm_contact.contact_id = so.primary_crm_contact_id
  WHERE so.stage = ANY(%(active_opportunity_stages)s::text[])
),
active_quotes AS (
  SELECT
    COALESCE(
      NULLIF(lower(trim(crm_contact.primary_email)), ''),
      NULLIF(lower(trim(machine.contact_display_email)), '')
    ) AS email_norm,
    'customer_quote'::text AS source_kind,
    q.quote_id::text AS source_id,
    latest.status::text AS state
  FROM api.v_commercial_customer_quote AS q
  JOIN api.v_commercial_sales_opportunity AS so
    ON so.sales_opportunity_id = q.sales_opportunity_id
  LEFT JOIN api.v_commercial_opportunity AS machine
    ON machine.opportunity_id = so.source_opportunity_id
  LEFT JOIN api.v_commercial_contact AS crm_contact
    ON crm_contact.contact_id = so.primary_crm_contact_id
  JOIN LATERAL (
    SELECT rev.status
    FROM api.v_commercial_customer_quote_revision AS rev
    WHERE rev.quote_id = q.quote_id
    ORDER BY rev.revision_number DESC
    LIMIT 1
  ) AS latest ON TRUE
  WHERE latest.status = ANY(%(active_quote_statuses)s::text[])
)
SELECT email_norm, source_kind, source_id, state
FROM active_opportunities
UNION ALL
SELECT email_norm, source_kind, source_id, state
FROM active_quotes
ORDER BY source_kind, source_id;
"""


def _resolve_postgres_url(explicit: str | None) -> str | None:
    if explicit and explicit.strip():
        return explicit.strip()
    for name in (
        "ORIGENLAB_POSTGRES_URL",
        "ALEMBIC_DATABASE_URL",
        "ORIGENLAB_CLOUD_POSTGRES_URL",
    ):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return None


def _normalize_postgres_url(url: str) -> str:
    u = url.strip()
    for prefix in ("postgresql+psycopg://", "postgresql+psycopg2://"):
        if u.startswith(prefix):
            return "postgresql://" + u[len(prefix):]
    return u


def _normalize_email(raw: object) -> str | None:
    value = str(raw or "").strip().lower()
    if not value:
        return None
    found = emails_in(value)
    return found[0] if found else None


def _read_active_rows(postgres_url: str) -> list[tuple[object, object, object, object]]:
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - operator environment guard
        raise RuntimeError(
            "psycopg is not installed; run the email-pipeline environment with the postgres dependency group"
        ) from exc

    with psycopg.connect(_normalize_postgres_url(postgres_url)) as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute(
                _QUERY,
                {
                    "active_opportunity_stages": list(ACTIVE_SALES_OPPORTUNITY_STAGES),
                    "active_quote_statuses": list(ACTIVE_QUOTE_REVISION_STATUSES),
                },
            )
            return list(cur.fetchall())


def _aggregate_rows(
    rows: list[tuple[object, object, object, object]],
) -> tuple[dict[str, list[dict[str, str]]], list[dict[str, str]]]:
    by_email: dict[str, list[dict[str, str]]] = defaultdict(list)
    unresolved: list[dict[str, str]] = []
    for raw_email, source_kind, source_id, state in rows:
        item = {
            "source_kind": str(source_kind or ""),
            "source_id": str(source_id or ""),
            "state": str(state or ""),
        }
        email = _normalize_email(raw_email)
        if not email:
            unresolved.append(item)
            continue
        by_email[email].append(item)
    return dict(by_email), unresolved


def _replace_sqlite_projection(
    db_path: Path,
    *,
    by_email: dict[str, list[dict[str, str]]],
    refreshed_at: str,
    active_source_rows: int | None = None,
    unresolved_active_rows_without_email: int = 0,
) -> None:
    if active_source_rows is None:
        active_source_rows = sum(len(v) for v in by_email.values()) + int(
            unresolved_active_rows_without_email
        )
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(_SCHEMA_SQL)
        conn.execute("BEGIN")
        conn.execute("DELETE FROM outbound_commercial_hold")
        for email in sorted(by_email):
            reasons = by_email[email]
            conn.execute(
                """
                INSERT INTO outbound_commercial_hold(
                  email_norm, reasons_json, source_count, refreshed_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    email,
                    json.dumps(reasons, ensure_ascii=False, sort_keys=True),
                    len(reasons),
                    refreshed_at,
                ),
            )
        conn.execute("DELETE FROM outbound_commercial_hold_meta")
        conn.execute(
            """
            INSERT INTO outbound_commercial_hold_meta(
              singleton_id, refreshed_at, active_source_rows,
              held_unique_emails, unresolved_active_rows_without_email
            ) VALUES (1, ?, ?, ?, ?)
            """,
            (
                refreshed_at,
                int(active_source_rows),
                len(by_email),
                int(unresolved_active_rows_without_email),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=None, help="SQLite path (default: settings)")
    ap.add_argument("--postgres-url", default=None, help="Explicit read-only-capable Postgres URL")
    ap.add_argument("--apply", action="store_true", help="Replace the SQLite derived hold projection")
    ap.add_argument("--json-out", type=Path, default=None, help="Optional audit JSON output")
    args = ap.parse_args(argv)

    settings = load_settings()
    db_path = (args.db or settings.resolved_sqlite_path()).expanduser().resolve()
    if not db_path.is_file():
        print(f"SQLite database not found: {db_path}", file=sys.stderr)
        return 2

    postgres_url = _resolve_postgres_url(args.postgres_url)
    if not postgres_url:
        print(
            "Missing Postgres URL: set ORIGENLAB_POSTGRES_URL, ALEMBIC_DATABASE_URL, "
            "ORIGENLAB_CLOUD_POSTGRES_URL, or pass --postgres-url.",
            file=sys.stderr,
        )
        return 2

    try:
        rows = _read_active_rows(postgres_url)
    except Exception as exc:
        print(f"Failed to read active commercial state: {exc}", file=sys.stderr)
        return 3

    by_email, unresolved = _aggregate_rows(rows)
    refreshed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    payload = {
        "mode": "apply" if args.apply else "dry_run",
        "sqlite_path": str(db_path),
        "active_source_rows": len(rows),
        "held_unique_emails": len(by_email),
        "unresolved_active_rows_without_email": len(unresolved),
        "active_sales_opportunity_stages": list(ACTIVE_SALES_OPPORTUNITY_STAGES),
        "active_quote_revision_statuses": list(ACTIVE_QUOTE_REVISION_STATUSES),
        "refreshed_at": refreshed_at,
        "held_emails": [
            {"email": email, "reasons": by_email[email]}
            for email in sorted(by_email)
        ],
        "unresolved": unresolved,
    }

    if args.apply:
        try:
            _replace_sqlite_projection(
                db_path,
                by_email=by_email,
                refreshed_at=refreshed_at,
                active_source_rows=len(rows),
                unresolved_active_rows_without_email=len(unresolved),
            )
        except Exception as exc:
            print(f"Failed to replace SQLite commercial hold projection: {exc}", file=sys.stderr)
            return 4

    if args.json_out:
        out = args.json_out.expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if unresolved:
        print(
            "WARNING: some active CRM rows have no resolvable email; review the unresolved list before bulk sending.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
