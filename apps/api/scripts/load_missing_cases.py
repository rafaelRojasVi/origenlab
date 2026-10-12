#!/usr/bin/env python3
"""Load the cases the history import held back (owner 2026-10-12: «yes do it»): for each case,
its organization, the case, its sent quotations and — when the emails show it — the win or the
loss, through the same commands a person runs from the dashboard.

    uv run python scripts/load_missing_cases.py --plan-b64 <gzip+base64 JSON> --operator <id or name>
    uv run python scripts/load_missing_cases.py --plan-b64 … --operator … --apply

The plan (kept private, never in the repository) is a JSON list of
  {"case": key, "title": str,
   "org": {"id": uuid} | {"name": str, "kind": str, "domain": str|null},
   "quotes": [{"q": printed number, "sha": sha256, "gid": gmail id, "sent": iso, "file": name, "sup": sha|null}],
   "outcome": {} | {"won": printed number} | {"lost": reason}}
Each quote's email must already be staged (a `gmail_message` record with a `document_reference`
for the PDF — owner-run SQL). Steps per case, each its own command with a key derived from the
case and the step, so a second --apply replays and decides nothing twice: organization (reuse,
confirm a machine proposal, or register), open the case from its first quote email, name the
requesting institution, walk to «quoting», record every quotation in send order (a later PDF of
the same number replaces the earlier), then «negotiating» + won, or lost with its reason.
Prints case keys, numbers and outcomes only.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json
import sys
from datetime import datetime
from typing import Any

from origenlab_api.v2.case_commands import (
    ADD_CASE_ORGANIZATION,
    ADVANCE_CASE_STAGE,
    OPEN_COMMERCIAL_CASE,
    RECORD_CASE_WON,
    AddCaseOrganizationBody,
    AdvanceCaseStageBody,
    OpenCommercialCaseBody,
    RecordCaseWonBody,
    validated_case,
)
from origenlab_api.v2.commands import CommandRefused, request_digest
from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2.quote_import_commands import (
    RECORD_HISTORICAL_QUOTATION,
    RecordHistoricalQuotationBody,
    validated_quote_import,
)

NOTE = "Caso del historial cargado tras revisión del dueño (2026-10-12): cotización enviada por correo"
BATCH = "missing-cases-20261012"


def _plan(args: argparse.Namespace) -> list[dict[str, Any]]:
    raw = gzip.decompress(base64.b64decode(args.plan_b64)) if args.plan_b64 else open(args.plan_file, "rb").read()
    data = json.loads(raw)
    if not isinstance(data, list) or not data:
        raise SystemExit("the plan must be a non-empty JSON list")
    return data


def _one(repo: Any, sql: str, params: Any) -> dict[str, Any] | None:
    with repo._write() as cur:
        cur.execute(sql, params)
        return repo._row(cur)


def _operator(repo: Any, who: str) -> OperatorIdentity:
    with repo._write() as cur:
        cur.execute(
            """select id::text as operator_id, coalesce(email_norm, '') as email_norm, display_name, role, status
                 from platform.operator
                where (id::text = %(w)s or display_name = %(w)s) and status = 'active' and role in ('sales', 'admin')""",
            {"w": who.strip()},
        )
        rows = cur.fetchall()
        names = [c.name for c in cur.description]
    if len(rows) != 1:
        raise SystemExit("--operator must name exactly one active sales or admin operator")
    return OperatorIdentity(**dict(zip(names, rows[0])))


class Loader:
    def __init__(self, cases: Any, quotes: Any, authoring: Any, operator: OperatorIdentity) -> None:
        self.cases, self.quotes, self.authoring, self.op = cases, quotes, authoring, operator
        self.orgs_by_name: dict[str, str] = {}

    def _key(self, case: str, step: str) -> str:
        return f"{BATCH}:{case}:{step}"[:200]

    def _case_cmd(self, case: str, step: str, name: str, body: Any) -> dict[str, Any]:
        return self.cases.execute(command_name=name, operator=self.op, fields=validated_case(name, body),
                                  idempotency_key=self._key(case, step), digest=request_digest(name, body))

    def staged(self, gid: str, sha: str) -> dict[str, Any] | None:
        return _one(self.cases, """
            select sr.id::text as id,
                   exists (select 1 from evidence.assertion a where a.source_record_id = sr.id
                             and a.kind = 'document_reference' and a.value_norm = 'sha256:' || %(sha)s) as referenced,
                   exists (select 1 from crm.quote_revision r where r.pdf_sha256 = %(sha)s) as recorded
              from evidence.source_record sr
             where sr.dedupe_key = 'gmail_message:' || %(gid)s and sr.kind = 'gmail_message'""", {"gid": gid, "sha": sha})

    def organization(self, case: str, org: dict[str, Any]) -> dict[str, Any]:
        from origenlab_api.v2.crm_authoring_routes import ConfirmOrganizationRecordBody, RegisterOrganizationBody

        if org.get("id"):
            row = _one(self.cases, "select id::text as id, version, confirmation from crm.organization where id = %s", (org["id"],))
            if row is None:
                raise CommandRefused(404, "organization_not_found", "the plan names an organization that does not exist")
            if row["confirmation"] != "confirmed":
                body = ConfirmOrganizationRecordBody(organization_id=row["id"], expected_version=row["version"], note=NOTE)
                fields = body.model_dump()
                fields["organization_id"] = str(fields["organization_id"])
                self.authoring.execute(command_name="confirm-organization-record", operator=self.op, fields=fields,
                                       idempotency_key=self._key(case, "org-confirm"),
                                       digest=request_digest("confirm-organization-record", body))
                row = _one(self.cases, "select id::text as id, version, confirmation from crm.organization where id = %s", (org["id"],))
            return row  # type: ignore[return-value]
        name = org["name"].strip()
        existing = self.orgs_by_name.get(name) or (_one(self.cases,
            "select id::text as id from crm.organization where name = %s and merged_into_organization_id is null "
            "order by created_at limit 1", (name,)) or {}).get("id")
        if not existing:
            body = RegisterOrganizationBody(name=name, kind=org["kind"], domain=org.get("domain") or None, note=NOTE)
            out = self.authoring.execute(command_name="register-organization", operator=self.op, fields=body.model_dump(),
                                         idempotency_key=f"{BATCH}:org:{name}"[:200],
                                         digest=request_digest("register-organization", body))
            existing = out.get("organization_id") or out.get("organization", {}).get("id")
            if not existing:
                existing = (_one(self.cases, "select id::text as id from crm.organization where name = %s "
                                 "order by created_at desc limit 1", (name,)) or {}).get("id")
        self.orgs_by_name[name] = existing
        return _one(self.cases, "select id::text as id, version, confirmation from crm.organization where id = %s",
                    (existing,))  # type: ignore[return-value]

    def load(self, p: dict[str, Any]) -> str:
        case = p["case"]
        quotes = sorted(p["quotes"], key=lambda q: q["sent"])
        first = self.staged(quotes[0]["gid"], quotes[0]["sha"])
        org = self.organization(case, p["org"])
        opened = self._case_cmd(case, "open", OPEN_COMMERCIAL_CASE, OpenCommercialCaseBody(
            title=p["title"][:200], origin_source_record_id=first["id"], note=NOTE))  # type: ignore[index]
        opp, version = opened["opportunity_id"], opened["opportunity_version"]
        now = _one(self.cases, "select stage from crm.opportunity where id = %s", (opp,))
        if now and now["stage"] in ("won", "lost"):
            return f"already {now['stage']}"  # a rerun: the case was finished by an earlier apply
        version = self._case_cmd(case, "requester", ADD_CASE_ORGANIZATION, AddCaseOrganizationBody(
            opportunity_id=opp, opportunity_version=version, organization_id=org["id"],
            organization_version=org["version"], role="requesting_institution", note=NOTE))["opportunity_version"]
        for stage in ("qualifying", "qualified", "quoting"):
            version = self._case_cmd(case, f"stage-{stage}", ADVANCE_CASE_STAGE, AdvanceCaseStageBody(
                opportunity_id=opp, opportunity_version=version, stage=stage, note=NOTE))["opportunity_version"]
        for q in quotes:
            rec = self.staged(q["gid"], q["sha"])
            body = RecordHistoricalQuotationBody(
                opportunity_id=opp, quote_number=q["q"], printed_quote_numbers=[q["q"]], document_sha256=q["sha"],
                sent_at=datetime.fromisoformat(q["sent"]), origin_source_record_id=rec["id"],  # type: ignore[index]
                ledger_decision_id=f"{BATCH}:{q['sha'][:16]}", filename=q.get("file") or None,
                supersedes_document_sha256=q.get("sup"), note=NOTE)
            self.quotes.execute(command_name=RECORD_HISTORICAL_QUOTATION, operator=self.op,
                                fields=validated_quote_import(RECORD_HISTORICAL_QUOTATION, body),
                                idempotency_key=self._key(case, f"quote-{q['sha'][:16]}"),
                                digest=request_digest(RECORD_HISTORICAL_QUOTATION, body))
        version = _one(self.cases, "select version from crm.opportunity where id = %s", (opp,))["version"]  # type: ignore[index]
        out = p.get("outcome") or {}
        if out.get("won"):
            version = self._case_cmd(case, "stage-negotiating", ADVANCE_CASE_STAGE, AdvanceCaseStageBody(
                opportunity_id=opp, opportunity_version=version, stage="negotiating", note=NOTE))["opportunity_version"]
            rev = _one(self.cases, """select q.id::text as quote_id, max(r.revision_no) as revision_no
                                        from crm.quote q join crm.quote_revision r on r.quote_id = q.id
                                       where q.opportunity_id = %s and q.quote_number = %s
                                         and r.status = 'sent' and r.superseded_by_revision_no is null
                                       group by q.id""", (opp, out["won"]))
            if rev is None:
                raise CommandRefused(409, "won_quote_not_on_case", f"the won quote {out['won']} is not on the case")
            self._case_cmd(case, "won", RECORD_CASE_WON, RecordCaseWonBody(
                opportunity_id=opp, opportunity_version=version, quote_id=rev["quote_id"],
                revision_no=int(rev["revision_no"]), note=NOTE))
            return f"won ({out['won']})"
        if out.get("lost"):
            self._case_cmd(case, "lost", ADVANCE_CASE_STAGE, AdvanceCaseStageBody(
                opportunity_id=opp, opportunity_version=version, stage="lost", close_reason=out["lost"][:2000], note=NOTE))
            return "lost"
        return "sent (quoting)"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--plan-b64")
    src.add_argument("--plan-file")
    p.add_argument("--operator", required=True)
    p.add_argument("--apply", action="store_true")
    args = p.parse_args(argv)
    plan = _plan(args)

    import psycopg

    from origenlab_api.settings import get_settings
    from origenlab_api.v2.case_command_repository import V2CaseCommandRepository
    from origenlab_api.v2.crm_authoring import V2CrmAuthoringRepository
    from origenlab_api.v2.quote_import_repository import V2QuoteImportRepository

    settings = get_settings()
    if not settings.v2_configured():
        raise SystemExit("the V2 database is not configured here")
    target = settings.v2_database_target()

    def connect(dsn: str, **kw: Any) -> Any:
        return psycopg.connect(dsn, **{**target.connect_options, **kw})

    t = settings.v2_statement_timeout_ms
    loader = Loader(V2CaseCommandRepository(connect, target.dsn, statement_timeout_ms=t),
                    V2QuoteImportRepository(connect, target.dsn, statement_timeout_ms=t),
                    V2CrmAuthoringRepository(connect, target.dsn, statement_timeout_ms=t), None)  # type: ignore[arg-type]
    loader.op = _operator(loader.cases, args.operator)

    failures = 0
    for case in plan:
        label = f"case {case['case'][:8]} · {', '.join(dict.fromkeys(q['q'] for q in case['quotes']))}"
        missing = [q["q"] for q in case["quotes"] if not (loader.staged(q["gid"], q["sha"]) or {}).get("referenced")]
        recorded = [q["q"] for q in case["quotes"] if (loader.staged(q["gid"], q["sha"]) or {}).get("recorded")]
        outcome = "won " + case["outcome"]["won"] if case.get("outcome", {}).get("won") else (
            "lost" if case.get("outcome", {}).get("lost") else "sent")
        if missing:
            print(f"SKIP {label}: not staged: {', '.join(missing)}"); failures += 1; continue
        if not args.apply:
            org = case["org"].get("id", "")[:8] or f"new {case['org'].get('kind')}"
            print(f"PLAN {label}: org {org}, {len(case['quotes'])} PDF, {outcome}"
                  + (f" (already recorded: {', '.join(recorded)})" if recorded else ""))
            continue
        try:
            print(f"OK   {label}: {loader.load(case)}")
        except CommandRefused as exc:
            print(f"FAIL {label}: {exc.code}"); failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
