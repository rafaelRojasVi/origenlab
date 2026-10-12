#!/usr/bin/env python3
"""Attach purchase orders that arrived by email to cases already won — the one-off backfill of
2026-10-12 (owner: «look for the OC for the ganadas and add them»).

    # 1. plan (read-only): what would be attached, and what blocks each entry
    uv run python scripts/attach_case_documents.py --entries '<json>' --operator <id or name>
    # 2. apply exactly that plan
    uv run python scripts/attach_case_documents.py --entries '<json>' --operator <id or name> --apply

`--entries` is a JSON list of {"opportunity_id", "source_record_id", "document_sha256",
"document_number"?, "note"?} (UUIDs and hashes only; never a name). Each OC is copied from its
email's stored `.eml` and recorded with `attach_case_document` — the code the dashboard's
«Adjuntar OC» runs — against the case's won revision, as the named operator (active, sales or
admin). Idempotency keys are derived from the entry, so a second `--apply` replays rather than
attaching twice. Runs where the API runs (its database and Storage environment); prints UUID
prefixes and outcomes only, never a key, a DSN or a file's content.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from origenlab_api.v2.case_document_routes import CopyRefused, copy_email_attachment
from origenlab_api.v2.case_documents import ATTACH_CASE_DOCUMENT
from origenlab_api.v2.commands import CommandRefused, as_uuid
from origenlab_api.v2.identity import OperatorIdentity

_PLAN = """
select o.id::text as opportunity_id, o.stage, r.id::text as won_revision_id, q.quote_number, r.revision_no,
       exists (select 1 from evidence.source_record sr
                 cross join lateral jsonb_array_elements(
                   case when jsonb_typeof(sr.payload -> 'documents') = 'array' then sr.payload -> 'documents'
                        else '[]'::jsonb end) d
                where sr.id = %(src)s and sr.kind = 'gmail_message' and lower(d ->> 'sha256') = %(sha)s) as carried,
       exists (select 1 from crm.opportunity_document x
                where x.opportunity_id = o.id and x.sha256 = %(sha)s and x.removed_at is null) as attached
  from crm.opportunity o
  left join crm.quote q on q.id = o.won_quote_id
  left join crm.quote_revision r on r.quote_id = q.id and r.revision_no = o.won_revision_no
 where o.id = %(opp)s
"""


def _entries(raw: str) -> list[dict[str, Any]]:
    data = json.loads(raw)
    if not isinstance(data, list) or not data:
        raise SystemExit("--entries must be a non-empty JSON list")
    out = []
    for e in data:
        sha = str(e["document_sha256"]).strip().lower()
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise SystemExit("document_sha256 must be a SHA-256 hex digest")
        out.append({
            "opportunity_id": as_uuid(e["opportunity_id"], "opportunity_id"),
            "source_record_id": as_uuid(e["source_record_id"], "source_record_id"),
            "document_sha256": sha,
            "document_number": (str(e.get("document_number") or "").strip() or None),
            "note": str(e.get("note") or "OC recibida por correo, adjuntada al caso ganado (backfill 2026-10-12)").strip(),
        })
    return out


def _operator(repo: Any, who: str) -> OperatorIdentity:
    """The operator by id, or by exact display name (profiles carry no email); active, sales or admin."""
    with repo._write() as cur:
        cur.execute(
            """select id::text as operator_id, coalesce(email_norm, '') as email_norm, display_name, role, status
                 from platform.operator
                where (id::text = %(who)s or display_name = %(who)s)
                  and status = 'active' and role in ('sales', 'admin')""",
            {"who": who.strip()},
        )
        rows = cur.fetchall()
        names = [c.name for c in cur.description]
    if len(rows) != 1:
        raise SystemExit("--operator must name exactly one active sales or admin operator (id or display name)")
    return OperatorIdentity(**dict(zip(names, rows[0])))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--entries", required=True, help="JSON list of entries (see above)")
    p.add_argument("--operator", required=True, help="the operator's id or exact display name")
    p.add_argument("--apply", action="store_true", help="attach; without it, only plan")
    args = p.parse_args(argv)
    entries = _entries(args.entries)

    import httpx
    import psycopg

    from origenlab_api.settings import get_settings
    from origenlab_api.v2.case_command_repository import V2CaseCommandRepository
    from origenlab_api.v2.catalog.storage import SupabaseStorage

    settings = get_settings()
    if not settings.v2_configured():
        raise SystemExit("the V2 database is not configured here")
    target = settings.v2_database_target()

    def connect(dsn: str, **kw: Any) -> Any:
        return psycopg.connect(dsn, **{**target.connect_options, **kw})

    repo = V2CaseCommandRepository(connect, target.dsn, statement_timeout_ms=settings.v2_statement_timeout_ms)
    operator = _operator(repo, args.operator)
    storages = None
    if args.apply:
        if not settings.catalog_storage_configured():
            raise SystemExit("Storage is not configured here (ORIGENLAB_V2_STORAGE_URL / _SECRET_KEY)")
        key = settings.v2_storage_secret_key.get_secret_value()  # type: ignore[union-attr]
        client = httpx.Client(timeout=httpx.Timeout(60.0, connect=5.0))
        storages = (SupabaseStorage(settings.v2_storage_url, key, client, bucket="crm-documents"),  # type: ignore[arg-type]
                    SupabaseStorage(settings.v2_storage_url, key, client, bucket="mail"))  # type: ignore[arg-type]

    failures = 0
    for e in entries:
        label = f"case {e['opportunity_id'][:8]} · doc {e['document_sha256'][:10]}"
        with repo._write() as cur:
            cur.execute(_PLAN, {"opp": e["opportunity_id"], "src": e["source_record_id"], "sha": e["document_sha256"]})
            row = repo._row(cur)
        if row is None:
            print(f"SKIP {label}: no such case"); failures += 1; continue
        if not row["carried"]:
            print(f"SKIP {label}: that message does not carry this document"); failures += 1; continue
        if row["attached"]:
            print(f"DONE {label}: already attached"); continue
        revision = row["won_revision_id"]
        where = f"{row['quote_number']} r{row['revision_no']}" if revision else "no won revision"
        if not args.apply:
            print(f"PLAN {label}: stage {row['stage']}, closes {where}, number {e['document_number'] or '(from the name)'}")
            continue
        assert storages is not None  # noqa: S101
        try:
            copied = copy_email_attachment(repo, storages[0], storages[1], e["opportunity_id"],
                                           e["source_record_id"], e["document_sha256"])
        except CopyRefused as exc:
            print(f"FAIL {label}: {exc.code}"); failures += 1; continue
        hint = copied.pop("number_hint")
        fields = {
            "opportunity_id": e["opportunity_id"], "kind": "purchase_order", "quote_revision_id": revision,
            "document_number": e["document_number"] or hint, "note": e["note"], **copied,
            "source": "email", "source_record_id": e["source_record_id"],
        }
        try:
            out = repo.execute(command_name=ATTACH_CASE_DOCUMENT, operator=operator, fields=fields,
                               idempotency_key=f"backfill-oc-{e['opportunity_id']}-{e['document_sha256'][:16]}",
                               digest=e["document_sha256"])
        except CommandRefused as exc:
            print(f"FAIL {label}: {exc.code}"); failures += 1; continue
        doc = out["document"]
        print(f"OK   {label}: attached {doc['document_id'][:8]} (OC {doc.get('document_number') or '-'}), closes {where}"
              + (" [replayed]" if out.get("replayed") else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
