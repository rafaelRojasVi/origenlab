#!/usr/bin/env python3
"""Real-data shadow-CRM preview hydration (Phase 4 of the commercial audit).

READS real evidence from the read-only production SQLite (opened
mode=ro + PRAGMA query_only=ON; never mutated). WRITES only to an
explicitly-provided, localhost-only Postgres database — the script refuses
to run against any other host. It is deterministic and idempotent: re-running
it against the same real SQLite and the same preview database produces the
same rows (ON CONFLICT DO NOTHING / DO UPDATE, no random IDs on repeat runs).

Full rationale: docs/architecture/COMMERCIAL_OPERATING_SYSTEM_AUDIT.md.

Selection criterion (documented, not arbitrary): of the 9,577 real PR3
opportunity rows in SQLite `commercial_opportunity`, exactly one
(record_kind='explicit_opportunity', opportunity_id
o_254ee22e1f2e2c9ab7f7ef9706729d78) is backed by a genuine, title-bearing
`commercial_deal` record (CEAF / SERVA, OC 26172, deal_key
serva-ceaf-oc-26172-po-174-26). The other 9,576 rows are
record_kind='commercial_history' — bare contact-touch reconstructions with
no product/subject content. This script deliberately promotes only the one
real, title-bearing opportunity; promoting the others as "sales
opportunities" would require inventing a title/subject that does not exist
in the source data, which this task's data-safety rules forbid.

Two of this script's writes are explicitly scoped preview-only
demonstrations of a target shape that has NO production writer today
(see the audit doc's CRM-4A finding):

1. Seeding `commercial.opportunity` (normally written by the mirror-sync
   job, not this script) with just the one real row needed so the real
   `CommercialOperationsService.promote_sales_opportunity` write path has
   a source row to promote from (it looks the row up via
   `api.v_commercial_opportunity` and raises if missing).
2. Seeding `commercial.organization` / `commercial.contact` (CRM-4A) and
   then linking the newly promoted `sales_opportunity` to them via a direct
   UPDATE. Production has no reconciliation writer for this yet — this is a
   demonstration of the target shape, not a shipped feature.

Everything else (task/activity) is intentionally left empty: these are
pure human-authored CRM concepts with no machine source to backfill from,
and the task rules forbid inventing them.

Usage:
    cd apps/api
    uv run python scripts/hydrate_realdata_preview.py \\
        --postgres-write-url postgresql://origenlab:...@localhost:55432/origenlab_realdata_preview \\
        --sqlite-path "$HOME/data/origenlab-email/sqlite/emails.sqlite"
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid5, NAMESPACE_URL

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO / "src") not in sys.path:
    sys.path.insert(0, str(_REPO / "src"))

ALLOWED_HOSTS = {"localhost", "127.0.0.1"}

# The single real, title-bearing PR3 opportunity documented above.
CEAF_OPPORTUNITY_ID = "o_254ee22e1f2e2c9ab7f7ef9706729d78"

PREVIEW_OPERATOR = "realdata-preview-hydration@origenlab.cl"


def _require_local_dsn(dsn: str, *, label: str) -> None:
    host = urlsplit(dsn.replace("postgresql+psycopg://", "postgresql://")).hostname
    if host not in ALLOWED_HOSTS:
        raise SystemExit(
            f"Refusing to write: {label} host {host!r} is not in {ALLOWED_HOSTS}. "
            "This script only ever writes to a disposable local preview database."
        )


def _connect_sqlite_readonly(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def _deterministic_id(prefix: str, *parts: str) -> str:
    """Stable, non-random id derived from real identifying fields (re-runnable)."""
    return f"{prefix}_{uuid5(NAMESPACE_URL, '|'.join(parts)).hex}"


def fetch_ceaf_evidence(sqlite_path: Path) -> dict:
    conn = _connect_sqlite_readonly(sqlite_path)
    try:
        opp = conn.execute(
            "SELECT * FROM commercial_opportunity WHERE opportunity_id = ?",
            (CEAF_OPPORTUNITY_ID,),
        ).fetchone()
        if opp is None:
            raise SystemExit(
                f"Expected real PR3 opportunity {CEAF_OPPORTUNITY_ID} not found in "
                f"{sqlite_path} — real source data may have changed since this "
                "script's selection criterion was documented. Refusing to invent one."
            )
        deal = None
        if opp["deal_key"]:
            deal = conn.execute(
                "SELECT * FROM commercial_deal WHERE deal_key = ?", (opp["deal_key"],)
            ).fetchone()
        account = None
        if opp["account_id"]:
            account = conn.execute(
                "SELECT * FROM commercial_identity_account WHERE account_id = ?",
                (opp["account_id"],),
            ).fetchone()
        contact = None
        if opp["primary_contact_id"]:
            contact = conn.execute(
                "SELECT * FROM commercial_identity_contact WHERE contact_id = ?",
                (opp["primary_contact_id"],),
            ).fetchone()
        return {
            "opportunity": dict(opp),
            "deal": dict(deal) if deal else None,
            "account": dict(account) if account else None,
            "contact": dict(contact) if contact else None,
        }
    finally:
        conn.close()


def seed_opportunity_mirror(conn, evidence: dict) -> None:
    """Preview-only: normally written by the mirror-sync job, not a script."""
    opp = evidence["opportunity"]
    account = evidence["account"]
    contact = evidence["contact"]
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO commercial.opportunity (
              opportunity_id, record_kind, account_id, primary_contact_id,
              contact_display_email, account_display_domain,
              source_kind, source_key, deal_key,
              canonical_stage, source_stage, stage_reason_code, stage_confidence,
              stage_is_current, stage_is_terminal, stage_evidence_at, stage_evidence_id,
              first_activity_at, last_activity_at, identity_link_status, review_status,
              synced_at
            ) VALUES (
              %(opportunity_id)s, %(record_kind)s, %(account_id)s, %(primary_contact_id)s,
              %(contact_display_email)s, %(account_display_domain)s,
              %(source_kind)s, %(source_key)s, %(deal_key)s,
              %(canonical_stage)s, %(source_stage)s, %(stage_reason_code)s, %(stage_confidence)s,
              %(stage_is_current)s, %(stage_is_terminal)s, %(stage_evidence_at)s, %(stage_evidence_id)s,
              %(first_activity_at)s, %(last_activity_at)s, %(identity_link_status)s, %(review_status)s,
              now()
            )
            ON CONFLICT (opportunity_id) DO UPDATE SET
              synced_at = now(),
              canonical_stage = EXCLUDED.canonical_stage,
              last_activity_at = EXCLUDED.last_activity_at
            """,
            {
                "opportunity_id": opp["opportunity_id"],
                "record_kind": opp["record_kind"],
                "account_id": opp["account_id"],
                "primary_contact_id": opp["primary_contact_id"],
                "contact_display_email": contact["normalized_email"] if contact else None,
                "account_display_domain": account["primary_domain"] if account else None,
                "source_kind": opp["source_kind"],
                "source_key": opp["source_key"],
                "deal_key": opp["deal_key"],
                "canonical_stage": opp["canonical_stage"],
                "source_stage": opp["source_stage"],
                "stage_reason_code": opp["stage_reason_code"],
                "stage_confidence": opp["stage_confidence"],
                "stage_is_current": bool(opp["stage_is_current"]),
                "stage_is_terminal": bool(opp["stage_is_terminal"]),
                "stage_evidence_at": opp["stage_evidence_at"],
                "stage_evidence_id": opp["stage_evidence_id"],
                "first_activity_at": opp["first_activity_at"],
                "last_activity_at": opp["last_activity_at"],
                "identity_link_status": opp["identity_link_status"],
                "review_status": opp["review_status"],
            },
        )
    conn.commit()


def seed_organization_and_contact(conn, evidence: dict, *, operator: str) -> tuple[str, str | None]:
    """Preview-only demonstration of the target CRM-4A shape (no production writer exists).

    Returns (organization_id, contact_id | None).
    """
    deal = evidence["deal"]
    account = evidence["account"]
    contact = evidence["contact"]

    domain = (deal["client_domain"] if deal else None) or (account["primary_domain"] if account else None)
    display_name = (deal["client_org_name"] if deal else None) or (account["canonical_name"] if account else None)
    if not domain or not display_name:
        raise SystemExit("Real evidence missing organization domain/name — refusing to invent one.")

    organization_id = _deterministic_id("org", "realdata-preview", domain)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO commercial.organization (
              organization_id, display_name, primary_domain, version, created_by, updated_by
            ) VALUES (%(id)s, %(name)s, %(domain)s, 1, %(operator)s, %(operator)s)
            ON CONFLICT (organization_id) DO UPDATE SET
              display_name = EXCLUDED.display_name, updated_by = EXCLUDED.updated_by, updated_at = now()
            """,
            {"id": organization_id, "name": display_name, "domain": domain, "operator": operator},
        )

        contact_id = None
        email = (deal["client_contact_email"] if deal else None) or (contact["normalized_email"] if contact else None)
        if email:
            contact_id = _deterministic_id("contact", "realdata-preview", email)
            cur.execute(
                """
                INSERT INTO commercial.contact (
                  contact_id, organization_id, primary_email, display_name, version, created_by, updated_by
                ) VALUES (%(id)s, %(org_id)s, %(email)s, %(name)s, 1, %(operator)s, %(operator)s)
                ON CONFLICT (contact_id) DO UPDATE SET
                  organization_id = EXCLUDED.organization_id, updated_by = EXCLUDED.updated_by, updated_at = now()
                """,
                {
                    "id": contact_id,
                    "org_id": organization_id,
                    "email": email,
                    "name": (contact["display_name"] if contact and contact["display_name"] else email),
                    "operator": operator,
                },
            )
    conn.commit()
    return organization_id, contact_id


def promote_and_link(evidence: dict, *, organization_id: str, contact_id: str | None, operator: str) -> str:
    """Uses the REAL promotion write path, then a preview-only CRM-4A link demonstration."""
    from origenlab_api.services.commercial_operations_service import CommercialOperationsService
    from origenlab_api.repositories.postgres.commercial_operations import PostgresCommercialOperationsRepository
    from origenlab_api.settings import Settings
    from origenlab_api.repositories.postgres.write_common import postgres_write_connection

    deal = evidence["deal"]
    opportunity_id = evidence["opportunity"]["opportunity_id"]
    title = deal["title"] if deal else None
    if not title:
        raise SystemExit("Real evidence has no deal title — refusing to invent one.")

    settings = Settings()
    repository = PostgresCommercialOperationsRepository(settings)
    service = CommercialOperationsService(settings, repository=repository)

    sales_opportunity = service.promote_sales_opportunity(
        source_opportunity_id=opportunity_id,
        title=title,
        operator=operator,
        owner_key=operator,
        idempotency_key=f"realdata-preview-hydrate-{opportunity_id}-v1",
    )

    with postgres_write_connection(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                -- PREVIEW-ONLY: demonstrates the target CRM-4A reconciliation shape.
                -- No production writer sets these columns today; see
                -- docs/architecture/COMMERCIAL_OPERATING_SYSTEM_AUDIT.md.
                UPDATE commercial.sales_opportunity
                SET organization_id = %(org_id)s, primary_crm_contact_id = %(contact_id)s
                WHERE sales_opportunity_id = %(id)s
                """,
                {
                    "org_id": organization_id,
                    "contact_id": contact_id,
                    "id": sales_opportunity.sales_opportunity_id,
                },
            )
        conn.commit()

    return sales_opportunity.sales_opportunity_id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postgres-write-url", required=True)
    parser.add_argument("--sqlite-path", required=True, type=Path)
    args = parser.parse_args()

    _require_local_dsn(args.postgres_write_url, label="--postgres-write-url")

    import os

    os.environ["ORIGENLAB_POSTGRES_WRITE_URL"] = args.postgres_write_url
    os.environ["ORIGENLAB_COMMERCIAL_OPERATIONS_WRITES_ENABLED"] = "true"

    from origenlab_api.repositories.postgres.common import require_psycopg, normalize_postgres_url

    pg = require_psycopg()

    print(f"Reading real evidence (read-only) from {args.sqlite_path} ...")
    evidence = fetch_ceaf_evidence(args.sqlite_path)
    print(f"  real opportunity: {evidence['opportunity']['opportunity_id']}")
    print(f"  real deal title:  {evidence['deal']['title'] if evidence['deal'] else None!r}")
    print(f"  real org domain:  {evidence['deal']['client_domain'] if evidence['deal'] else None!r}")

    with pg.connect(normalize_postgres_url(args.postgres_write_url), connect_timeout=10) as conn:
        print("Seeding commercial.opportunity (preview-only mirror shape) ...")
        seed_opportunity_mirror(conn, evidence)
        print("Seeding commercial.organization / commercial.contact (preview-only CRM-4A demo) ...")
        organization_id, contact_id = seed_organization_and_contact(conn, evidence, operator=PREVIEW_OPERATOR)

    print("Promoting via the real durable write path (CommercialOperationsService.promote_sales_opportunity) ...")
    sales_opportunity_id = promote_and_link(
        evidence, organization_id=organization_id, contact_id=contact_id, operator=PREVIEW_OPERATOR
    )
    print(f"Done. sales_opportunity_id={sales_opportunity_id} organization_id={organization_id} contact_id={contact_id}")


if __name__ == "__main__":
    main()
