#!/usr/bin/env python3
"""Plan — and, only into a disposable database, apply — the CRM import of confirmed quotations.

Dry run (the default; reads the target read-only when --target-dsn is given, writes only --out):

    uv run python scripts/quote_crm_import.py \
        --expect-ledger-hash da5069f7… --out ~/data/…/quote-crm-import-dryrun-<ts> \
        [--target-dsn postgresql://origenlab_api:…@127.0.0.1:54332/origenlab_clean] \
        [--confirmations organization_confirmations.jsonl]

Apply (refused unless every database named is a disposable `origenlab_test_<8 hex>`, or the
production guard below is given):

    uv run python scripts/quote_crm_import.py --apply \
        --expect-ledger-hash da5069f7… --expect-plan-sha256 <from the dry run> \
        --target-dsn <origenlab_api DSN of the disposable db> --admin-dsn <owner-capable DSN> \
        --operator-email <address> --out <new directory>

What an apply does, in order, each step idempotent:
  1. registers the ledger's operator in `platform.operator` if absent (with an `operator.changed`
     audit event, actor `migrator`);
  2. stages the plan's Gmail evidence — only the ready opportunities' — through the existing tool
     (`apps/email-pipeline/scripts/migration/stage_gmail_drive_evidence.py --apply`);
  3. for each opportunity whose organization an operator confirmed, runs the planned V2 commands
     as that operator, each with its idempotency key, re-hashing the ledger before each
     opportunity — a changed ledger aborts the run.

Production (`origenlab_clean`) — `--allow-cleanroom-production`, see
`scripts/quote_crm_import_production.py`. Without `--apply` it is a read-only preflight
report; with it, the same preflight runs again immediately before the first write, then a
verified `pg_dump` backup, then the apply under the import advisory lock, then a before/after
fingerprint. It needs every expected hash:

    uv run python scripts/quote_crm_import.py --allow-cleanroom-production [--apply --backup-dir <dir>] \
        --expect-ledger-hash <sha> --expect-plan-sha256 <sha> \
        --expect-input-sha256 decisions.jsonl=<sha> --expect-input-sha256 quote_document_identity.json=<sha> \
        --expect-input-sha256 evidence_source_records.json=<sha> --expect-input-sha256 blocking_report.csv=<sha> \
        --expect-input-sha256 organization_confirmations.jsonl=<sha> --expect-input-sha256 policy=<sha> \
        --confirmations <jsonl> --operator-email <registered admin> \
        --target-dsn <origenlab_api DSN of origenlab_clean> --admin-dsn <superuser DSN of origenlab_clean> --out <dir>

Production never registers an operator: the operator must already be active and admin.

It never writes a ledger, the staging, the identity report, emails.sqlite, Gmail, Drive or any
hosted database. Refuses to overwrite --out.

Before step 1 the intent is re-checked (`evidence_scope_violations`): evidence for an opportunity
that is not ready with an operator confirmation refuses the whole apply, before any write.

Exit codes: 2 bad arguments · 3 ledger hash mismatch or changed during the run · 4 plan hash
mismatch · 5 target refused · 6 evidence reconciliation not clean · 7 a command was refused ·
8 the plan carries evidence outside the ready opportunities · 9 the events the commands' receipts
own do not account for the growth of `crm.domain_event` during the command loop · 10 production:
the database after the apply is not what the plan predicted · 11 production: a preflight guard
refused (nothing written) · 12 production: the backup could not be taken (nothing written).

The events an apply reports are read back from `crm.domain_event` by the commands' receipt ids,
not summed from their responses: a response's `event_ids` does not list every event its command
appends (`create_organization` leaves out the `assertion.promoted` of the assertion it resolves).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from origenlab_api.v2 import quote_crm_import_plan as qp
from origenlab_api.v2 import quote_organization_confirmation as oc
from origenlab_api.v2.case_command_repository import V2CaseCommandRepository
from origenlab_api.v2.case_commands import CASE_BODY_BY_COMMAND, CASE_COMMAND_NAMES, validated_case
from origenlab_api.v2.command_repository import V2CommandRepository
from origenlab_api.v2.commands import BODY_BY_COMMAND, CommandRefused, request_digest, validated
from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2.quote_confirmation_rebase import rebase_problem
from origenlab_api.v2.quote_import_commands import (
    QUOTE_IMPORT_BODY_BY_COMMAND,
    QUOTE_IMPORT_COMMAND_NAMES,
    validated_quote_import,
)
from origenlab_api.v2.quote_import_repository import V2QuoteImportRepository
from origenlab_api.v2.quote_phase3_ledger_apply import EXCLUDED_DOCUMENTS

import quote_crm_import_production as prod  # the sibling module in scripts/

REPO = Path(__file__).resolve().parents[3]
AUDITS = Path.home() / "data/origenlab-v2-migration/audits"
DEFAULT_LEDGER = AUDITS / "quote-evidence-review-20260924/document_decisions.jsonl"
DEFAULT_EMAIL_LEDGER = AUDITS / "quote-evidence-review-20260924/decisions.jsonl"
DEFAULT_IDENTITY = AUDITS / "quote-document-identity-20260924/quote_document_identity.json"
DEFAULT_STAGING = AUDITS / "quote-evidence-staging-20260924/evidence_source_records.json"
DEFAULT_BLOCKING = AUDITS / "quote-phase3-worksheet-dryrun-20260925-frozen/blocking_report.csv"
DEFAULT_SQLITE = Path.home() / "data/origenlab-email/sqlite/emails.sqlite"
STAGE_TOOL = REPO / "apps/email-pipeline/scripts/migration/stage_gmail_drive_evidence.py"
LEDGER_OPERATOR = "Rafael"
DISPOSABLE = re.compile(r"^origenlab_test_[0-9a-f]{8}$")
NOTE = "Importación de cotización histórica confirmada por el operador en document_decisions.jsonl."


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dbname(dsn: str) -> str:
    return dsn.rsplit("/", 1)[-1].split("?", 1)[0]


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        if not rows:
            f.write("")
            return
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


# ─── inputs ────────────────────────────────────────────────────────────────────────────────


def read_confirmations(path: Path, target_dsn: str | None) -> dict[str, dict[str, Any]]:
    """Operator confirmations keyed by planned opportunity; a malformed or hand-written line refuses the file."""
    confirmations: dict[str, dict[str, Any]] = {}
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        c = json.loads(line)
        if c.get("action") not in {"confirm_existing", "create_from_evidence"}:
            raise SystemExit(f"confirmations line {n}: action must be confirm_existing or create_from_evidence")
        if c.get("simulation") is True:
            # A rehearsal file: never an owner decision, accepted only against a disposable db.
            if not (target_dsn and DISPOSABLE.match(dbname(target_dsn))):
                raise SystemExit(f"confirmations line {n}: a simulation file is accepted only "
                                 "against a disposable origenlab_test_<8 hex> target")
            if c.get("confirmed_by") != "SIMULATION":
                raise SystemExit(f"confirmations line {n}: a simulation says confirmed_by SIMULATION")
        else:
            # An owner decision exists only as the output of the confirmation loader
            # (`quote_organization_confirmation.py load`), which verified the operator and the
            # request it answers. A hand-written line is refused.
            who = c.get("confirmed_by")
            if c.get("loader_version") != oc.LOADER_VERSION or not isinstance(who, dict) \
                    or not {"email", "operator_id", "role"} <= set(who) or not (c.get("evidence") or {}).get("plan_sha256"):
                raise SystemExit(f"confirmations line {n}: not produced by {oc.LOADER_VERSION}; "
                                 "run scripts/quote_organization_confirmation.py load")
            # A rebased line is loader output whose plan reference was moved by
            # scripts/quote_confirmation_rebase.py after a per-opportunity check.
            problem = rebase_problem(c)
            if problem:
                raise SystemExit(f"confirmations line {n}: {problem}")
        if c["planned_opportunity_id"] in confirmations:
            raise SystemExit(f"confirmations line {n}: a second confirmation for {c['planned_opportunity_id']}")
        if not c.get("confirmed_at") or not c.get("note"):
            raise SystemExit(f"confirmations line {n}: confirmed_by, confirmed_at and note are required")
        if c["action"] == "confirm_existing" and not (c.get("organization_id") and c.get("organization_version")):
            raise SystemExit(f"confirmations line {n}: confirm_existing needs organization_id and organization_version")
        confirmations[c["planned_opportunity_id"]] = c
    return confirmations


def load_inputs(args: argparse.Namespace) -> dict[str, Any]:
    ledger_rows = [json.loads(line) for line in args.ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    identity = json.loads(args.identity.read_text(encoding="utf-8"))
    identity_docs = {d["sha256"]: d for d in identity["documents"]}
    staged = json.loads(args.staging.read_text(encoding="utf-8"))
    staged_by_email = {int(r["payload"]["source_email_id"]): r for r in staged}
    blocked_opps: dict[str, str] = {}
    reasons: dict[str, list[str]] = {sha: list(r) for sha, r in EXCLUDED_DOCUMENTS.items()}
    for row in csv.DictReader(args.blocking.open(encoding="utf-8")):
        full = [s for s in EXCLUDED_DOCUMENTS if s.startswith(row["documento"])]
        if len(full) != 1:
            raise SystemExit(f"blocking report row {row['documento']} matches {len(full)} excluded documents")
        blocked_opps[full[0]] = row["oportunidad"]
        reasons[full[0]].append(row["bloqueo"])
    if set(blocked_opps) != set(EXCLUDED_DOCUMENTS):
        raise SystemExit("the blocking report and EXCLUDED_DOCUMENTS disagree about which documents are blocked")
    confirmations = read_confirmations(args.confirmations, args.target_dsn) if args.confirmations else {}
    operators = {r.get("operator") for r in ledger_rows}
    if operators != {LEDGER_OPERATOR}:
        raise SystemExit(f"ledger operators {operators} — this load knows only {LEDGER_OPERATOR!r}")
    texts_dir = args.identity.parent / "texts"
    printed_in_text = {}
    for sha in identity_docs:
        path = texts_dir / f"{sha}.txt"
        if path.is_file():
            printed_in_text[sha] = sorted(qp.printed_numbers_in_text(path.read_text(encoding="utf-8", errors="replace")))
    return {"ledger_rows": ledger_rows, "identity_docs": identity_docs, "staged_by_email": staged_by_email,
            "printed_in_text": printed_in_text,
            "blocked": reasons, "blocked_opps": blocked_opps, "confirmations": confirmations}


# ─── Phase 3: evidence reconciliation (read-only) ──────────────────────────────────────────


def reconcile_evidence(intent: dict[str, Any], inputs: dict[str, Any], sqlite_path: Path) -> list[dict[str, Any]]:
    """Every promoted document's occurrences, checked against emails.sqlite read-only."""
    rows_by_sha = qp.effective_rows(inputs["ledger_rows"])
    conn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    try:
        out = []
        for opp in intent["opportunities"]:
            if opp["status"] == qp.S_HELD:
                continue
            for sha in opp["documents"]:
                row = rows_by_sha[sha]
                occ = sorted(row["occurrences"], key=lambda o: o["sent_at"])
                for i, o in enumerate(occ):
                    staged = inputs["staged_by_email"].get(int(o["email_id"]))
                    em = conn.execute("select message_id, folder, sender, recipients, date_iso from emails where id = ?",
                                      (int(o["email_id"]),)).fetchone()
                    att = conn.execute("select sha256, filename from attachments where id = ? and email_id = ?",
                                       (int(o["source_attachment_id"]), int(o["email_id"]))).fetchone()
                    payload = (staged or {}).get("payload", {})
                    checks = {
                        "staged": staged is not None,
                        "gmail_message_id_matches": payload.get("gmail_message_id") == o.get("gmail_message_id"),
                        "gmail_thread_id_matches": payload.get("gmail_thread_id") == o.get("gmail_thread_id"),
                        "sqlite_email_found": em is not None,
                        "rfc822_matches": em is not None and em[0] == o.get("rfc822_message_id"),
                        "sent_folder": em is not None and "Enviados" in (em[1] or ""),
                        "attachment_sha256_matches": att is not None and att[0] == sha,
                        "filename_matches": att is not None and att[1] == o.get("filename"),
                        "staged_payload_lists_document": any(d.get("sha256") == sha for d in payload.get("documents", [])),
                    }
                    out.append({
                        "planned_opportunity_id": opp["planned_opportunity_id"], "document_sha256": sha,
                        "quote_number": row["quote_number"], "role": "canonical" if i == 0 else "resend_or_copy",
                        "email_id": o["email_id"], "gmail_message_id": o.get("gmail_message_id"),
                        "gmail_thread_id": o.get("gmail_thread_id"), "rfc822_message_id": o.get("rfc822_message_id"),
                        "sent_at": o["sent_at"], "sender": payload.get("sender"), "recipients": payload.get("recipients"),
                        "filename": o.get("filename"), "drive_evidence": "unavailable: no exact Drive file id recorded",
                        "clean": all(checks.values()), **checks,
                    })
        return out
    finally:
        conn.close()


# ─── target state (read-only) ──────────────────────────────────────────────────────────────


def read_state(dsn: str, operator_email: str | None) -> tuple[qp.TargetState, dict[str, int]]:
    import psycopg

    state = qp.TargetState()
    with psycopg.connect(dsn, options="-c default_transaction_read_only=on") as conn:
        conn.read_only = True
        cur = conn.cursor()
        if cur.execute("show transaction_read_only").fetchone()[0] != "on":
            raise SystemExit("the state connection is not read-only; nothing read")
        for dk, sha in cur.execute("select dedupe_key, md5(payload::text) from evidence.source_record "
                                   "where kind = 'gmail_message'"):
            state.source_records[dk] = sha
        for dk, kind, vn in cur.execute(
                "select s.dedupe_key, a.kind, a.value_norm from evidence.assertion a "
                "join evidence.source_record s on s.id = a.source_record_id where s.kind = 'gmail_message'"):
            state.assertions.add((dk, kind, vn))
        op = None
        if operator_email:
            op = cur.execute("select id from platform.operator where email_norm = %s",
                             (operator_email.strip().lower(),)).fetchone()
        state.operator_registered = op is not None
        if op is not None:
            for k, st in cur.execute("select idempotency_key, status from platform.command_receipt "
                                     "where operator_id = %s and idempotency_key like 'qimport:%%'", (op[0],)):
                state.receipts[k] = st
        state.organizations = [(str(i), n, ln, c, v) for i, n, ln, c, v in cur.execute(
            "select id, name, legal_name, confirmation, version from crm.organization "
            "where merged_into_organization_id is null order by id")]
        state.contact_points = {v: str(i) for i, v in cur.execute("select id, value_norm from crm.contact_point")}
        state.quote_documents = {r[0] for r in cur.execute(
            "select pdf_sha256 from crm.quote_revision where pdf_sha256 is not null")}
        counts = {}
        for t in ("crm.opportunity", "crm.quote", "crm.quote_revision", "crm.organization", "crm.domain_event",
                  "crm.opportunity_evidence", "crm.opportunity_organization", "evidence.source_record",
                  "evidence.assertion", "platform.operator", "platform.command_receipt", "outbound.send_attempt",
                  "outbound.contact_control"):
            counts[t] = cur.execute(f"select count(*) from {t}").fetchone()[0]  # noqa: S608 - constant list
        conn.rollback()
    return state, counts


# ─── apply ─────────────────────────────────────────────────────────────────────────────────


def require_disposable(*dsns: str) -> None:
    names = {dbname(d) for d in dsns}
    if len(names) != 1:
        raise SystemExit(5)
    name = names.pop()
    if not DISPOSABLE.match(name):
        print(f"refused: --apply writes only into a disposable origenlab_test_<8 hex> database, not {name!r}. "
              "A production apply is not authorised by this build.", file=sys.stderr)
        raise SystemExit(5)


def domain_event_total(dsn: str) -> int:
    import psycopg

    with psycopg.connect(dsn, options="-c default_transaction_read_only=on") as conn:
        return int(conn.execute("select count(*) from crm.domain_event").fetchone()[0])


def receipt_events(dsn: str, receipt_ids: list[str]) -> list[dict[str, Any]]:
    """Every `crm.domain_event` these receipts own, with the command that owns it."""
    import psycopg

    if not receipt_ids:
        return []
    with psycopg.connect(dsn, options="-c default_transaction_read_only=on") as conn:
        rows = conn.execute(
            """
            select e.id::text, e.event_type, e.command_receipt_id::text, r.command_name
              from crm.domain_event e join platform.command_receipt r on r.id = e.command_receipt_id
             where e.command_receipt_id = any(%s::uuid[])
             order by e.command_receipt_id, e.id
            """,
            (receipt_ids,),
        ).fetchall()
    return [{"event_id": r[0], "event_type": r[1], "command_receipt_id": r[2], "command": r[3]} for r in rows]


def event_report(log: list[dict[str, Any]], events: list[dict[str, Any]], delta: int) -> dict[str, Any]:
    """What the new commands wrote to `crm.domain_event`, as the table has it."""
    new = [e for e in log if not e["replayed"]]
    by_type: dict[str, int] = {}
    by_command: dict[str, dict[str, int]] = {}
    for ev in events:
        by_type[ev["event_type"]] = by_type.get(ev["event_type"], 0) + 1
        per = by_command.setdefault(ev["command"], {})
        per[ev["event_type"]] = per.get(ev["event_type"], 0) + 1
    listed = {i for e in new for i in e["event_ids"]}
    return {
        "events_written": len(events),
        "domain_event_delta": delta,
        "events_match_delta": len(events) == delta,
        "events_listed_in_responses": sum(len(e["event_ids"]) for e in new),
        "events_not_listed_in_responses": sum(1 for ev in events if ev["event_id"] not in listed),
        "events_by_type": dict(sorted(by_type.items())),
        "events_by_command": {k: dict(sorted(v.items())) for k, v in sorted(by_command.items())},
        "new_receipts_without_events": sorted(
            {e["command_receipt_id"] for e in new} - {ev["command_receipt_id"] for ev in events}),
    }


def register_operator(admin_dsn: str, email: str) -> dict[str, Any]:
    import psycopg

    email = email.strip().lower()
    with psycopg.connect(admin_dsn) as conn, conn.cursor() as cur:
        cur.execute("set local role origenlab_owner")
        row = cur.execute("select id::text, display_name from platform.operator where email_norm = %s", (email,)).fetchone()
        if row is not None:
            if row[1] != LEDGER_OPERATOR:
                raise SystemExit(f"operator {email} exists with display name {row[1]!r}, not {LEDGER_OPERATOR!r}")
            return {"operator_id": row[0], "registered": False}
        op_id = cur.execute(
            "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
            "values (gen_random_uuid(), %s, %s, 'admin', 'active') returning id::text",
            (email, LEDGER_OPERATOR)).fetchone()[0]
        cur.execute(
            "insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, "
            "actor_kind) values ('operator', %s, 1, 'operator.changed', 1, %s::jsonb, 'migrator')",
            (op_id, json.dumps({"change": "registered", "display_name": LEDGER_OPERATOR, "role": "admin",
                                "reason": "operator named by document_decisions.jsonl", "plan_version": qp.PLAN_VERSION})))
        conn.commit()
        return {"operator_id": op_id, "registered": True}


def stage_evidence(manifest_path: Path, admin_dsn: str, *, apply: bool) -> dict[str, Any]:
    cmd = ["uv", "run", "--project", str(REPO / "apps/email-pipeline"), "python", str(STAGE_TOOL),
           "--manifest", str(manifest_path), "--json"]
    if apply:
        cmd += ["--database-url", admin_dsn, "--apply"]
    done = subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603 - fixed argv
    if done.returncode != 0:
        print(done.stderr.replace(admin_dsn, "<admin-dsn>") if admin_dsn else done.stderr, file=sys.stderr)
        raise SystemExit(6 if not apply else 7)
    return json.loads(done.stdout)


class Executor:
    """Runs one opportunity's steps, resolving `$ref` values from the database and from responses."""

    def __init__(self, dsn: str, operator: OperatorIdentity) -> None:
        import psycopg

        self.dsn = dsn
        self.operator = operator
        self.case_repo = V2CaseCommandRepository(psycopg.connect, dsn)
        self.evidence_repo = V2CommandRepository(psycopg.connect, dsn)
        self.quote_repo = V2QuoteImportRepository(psycopg.connect, dsn)

    def _lookup(self, sql: str, args: tuple[Any, ...]) -> str:
        import psycopg

        with psycopg.connect(self.dsn) as conn:
            row = conn.execute(sql, args).fetchone()
        if row is None:
            raise CommandRefused(409, "reference_not_found", f"no row for {args}")
        return str(row[0])

    def resolve(self, value: Any, ctx: dict[str, Any]) -> Any:
        if isinstance(value, dict) and "$ref" in value:
            ref = value["$ref"]
            if ref == "source":
                return self._lookup("select id from evidence.source_record where dedupe_key = %s", (value["dedupe_key"],))
            if ref == "assertion":
                return self._lookup(
                    "select a.id from evidence.assertion a join evidence.source_record s on s.id = a.source_record_id "
                    "where s.dedupe_key = %s and a.kind = %s and a.value_norm = %s",
                    (value["dedupe_key"], value["kind"], value["value_norm"]))
            if ref == "opportunity":
                return ctx["opportunity_id"]
            if ref == "opportunity_version":
                return ctx["opportunity_version"]
            if ref == "step":
                return ctx["responses"][value["step"]][value["field"]]
            raise CommandRefused(409, "unresolvable_reference", f"cannot resolve {ref}")
        if isinstance(value, dict):
            return {k: self.resolve(v, ctx) for k, v in value.items()}
        return value

    def preflight(self, opp: dict[str, Any]) -> None:
        """Refuse an opportunity before its first write when its confirmation cannot hold.

        G3 moves the CRM per whole opportunity; a confirmation that would fail halfway (a
        stale organization version, a name that already exists) is caught here instead.
        """
        import psycopg

        conf = opp["confirmation"] or {}
        done = self._lookup_optional(
            "select status from platform.command_receipt where operator_id = %s and idempotency_key = %s",
            (self.operator.operator_id, qp.key(opp["planned_opportunity_id"], "org:requesting")))
        if done == "completed":
            return
        with psycopg.connect(self.dsn) as conn:
            if conf.get("action") == "confirm_existing":
                row = conn.execute("select version, merged_into_organization_id from crm.organization where id = %s",
                                   (conf["organization_id"],)).fetchone()
                if row is None or row[1] is not None:
                    raise CommandRefused(409, "confirmed_organization_missing", "the confirmed organization is gone or merged")
                if int(row[0]) != int(conf["organization_version"]):
                    raise CommandRefused(409, "confirmed_organization_changed",
                                         f"organization is at version {row[0]}, the confirmation saw {conf['organization_version']}")
            elif conf.get("action") == "create_from_evidence":
                name = qp.normalize_organization_name(opp["printed_organization"] or "")
                clash = conn.execute(
                    "select id from crm.organization where lower(regexp_replace(btrim(name), '\\s+', ' ', 'g')) = %s "
                    "and merged_into_organization_id is null", (name,)).fetchone()
                done_create = self._lookup_optional(
                    "select status from platform.command_receipt where operator_id = %s and idempotency_key = %s",
                    (self.operator.operator_id, qp.key(opp["planned_opportunity_id"], "org:create")))
                if clash is not None and done_create != "completed":
                    raise CommandRefused(409, "organization_already_exists",
                                         "an organization with this name exists; confirm it instead of creating one")

    def _lookup_optional(self, sql: str, args: tuple[Any, ...]) -> str | None:
        import psycopg

        with psycopg.connect(self.dsn) as conn:
            row = conn.execute(sql, args).fetchone()
        return None if row is None else str(row[0])

    def run(self, opp: dict[str, Any], log: list[dict[str, Any]]) -> None:
        self.preflight(opp)
        ctx: dict[str, Any] = {"responses": {}}
        for step in opp["steps"]:
            body_raw = self.resolve(step["body"], ctx)
            name = step["command"]
            if name in CASE_COMMAND_NAMES:
                body = CASE_BODY_BY_COMMAND[name](**body_raw)
                repo, fields = self.case_repo, validated_case(name, body)
            elif name in QUOTE_IMPORT_COMMAND_NAMES:
                body = QUOTE_IMPORT_BODY_BY_COMMAND[name](**body_raw)
                repo, fields = self.quote_repo, validated_quote_import(name, body)
            else:
                body = BODY_BY_COMMAND[name](**body_raw)
                repo, fields = self.evidence_repo, validated(name, body)
            response = repo.execute(command_name=name, operator=self.operator, fields=fields,
                                    idempotency_key=step["key"], digest=request_digest(name, body))
            ctx["responses"][step["name"]] = response
            if "opportunity_id" in response and name != "record_historical_quotation":
                ctx["opportunity_id"] = response["opportunity_id"]
            if "opportunity_version" in response:
                ctx["opportunity_version"] = response["opportunity_version"]
            log.append({"planned_opportunity_id": opp["planned_opportunity_id"], "step": step["name"],
                        "command": name, "key": step["key"], "replayed": bool(response.get("replayed")),
                        "created": response.get("created", []), "event_ids": response.get("event_ids", []),
                        "command_receipt_id": response.get("command_receipt_id")})


# ─── main ──────────────────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    ap.add_argument("--email-ledger", type=Path, default=DEFAULT_EMAIL_LEDGER)
    ap.add_argument("--identity", type=Path, default=DEFAULT_IDENTITY)
    ap.add_argument("--staging", type=Path, default=DEFAULT_STAGING)
    ap.add_argument("--blocking", type=Path, default=DEFAULT_BLOCKING)
    ap.add_argument("--sqlite", type=Path, default=DEFAULT_SQLITE)
    ap.add_argument("--confirmations", type=Path)
    ap.add_argument("--expect-ledger-hash", required=True)
    ap.add_argument("--expect-plan-sha256")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--target-dsn", help="origenlab_api DSN; read-only in a dry run")
    ap.add_argument("--admin-dsn", help="owner-capable DSN of the same database; --apply only")
    ap.add_argument("--operator-email")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True)
    mode.add_argument("--apply", action="store_true")
    ap.add_argument("--allow-cleanroom-production", action="store_true",
                    help=f"target {prod.PRODUCTION_DB} behind the production preflight")
    ap.add_argument("--expect-input-sha256", action="append", default=[], metavar="NAME=SHA256",
                    help=f"production: one per input, exactly {', '.join(prod.REQUIRED_INPUTS)}")
    ap.add_argument("--backup-dir", type=Path, help="production --apply: where the pre-write pg_dump goes")
    args = ap.parse_args(argv)

    if args.out.exists():
        print(f"refused: {args.out} exists; every run writes a new directory", file=sys.stderr)
        return 2
    if args.apply and not (args.expect_plan_sha256 and args.target_dsn and args.admin_dsn and args.operator_email):
        print("refused: --apply needs --expect-plan-sha256, --target-dsn, --admin-dsn and --operator-email", file=sys.stderr)
        return 2
    expected_inputs: dict[str, str] = {}
    if args.allow_cleanroom_production:
        needed = {"--target-dsn": args.target_dsn, "--admin-dsn": args.admin_dsn, "--operator-email": args.operator_email,
                  "--confirmations": args.confirmations, "--expect-plan-sha256": args.expect_plan_sha256}
        if args.apply:
            needed["--backup-dir"] = args.backup_dir
        missing = [k for k, v in needed.items() if not v]
        if missing:
            print(f"refused: --allow-cleanroom-production needs {', '.join(missing)}", file=sys.stderr)
            return 2
        try:
            expected_inputs = prod.parse_expected_inputs(args.expect_input_sha256)
        except prod.ProductionRefused as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
    elif args.expect_input_sha256 or args.backup_dir:
        print("refused: --expect-input-sha256 and --backup-dir belong to --allow-cleanroom-production", file=sys.stderr)
        return 2
    elif args.apply:
        require_disposable(args.target_dsn, args.admin_dsn)

    input_paths = {"document_decisions.jsonl": args.ledger, "decisions.jsonl": args.email_ledger,
                   "quote_document_identity.json": args.identity, "evidence_source_records.json": args.staging,
                   "blocking_report.csv": args.blocking}
    if args.confirmations:
        input_paths["organization_confirmations.jsonl"] = args.confirmations
    hashes_before = {k: sha256_file(p) for k, p in input_paths.items()}
    if hashes_before["document_decisions.jsonl"] != args.expect_ledger_hash:
        print(f"refused: document ledger is {hashes_before['document_decisions.jsonl']}, "
              f"expected {args.expect_ledger_hash}", file=sys.stderr)
        return 3
    ledger_prefix = args.ledger.read_bytes()

    inputs = load_inputs(args)
    acquired_at = datetime.fromtimestamp(args.staging.stat().st_mtime, UTC).isoformat()
    intent = qp.build_plan(inputs["ledger_rows"], inputs["identity_docs"], inputs["staged_by_email"],
                           inputs["blocked"], inputs["blocked_opps"], inputs["confirmations"],
                           inputs["printed_in_text"], acquired_at=acquired_at, note=NOTE)
    intent["inputs_sha256"] = hashes_before
    plan_sha = qp.plan_sha256(intent)

    # Operator confirmations answer one specific plan: the one computed from these same inputs
    # with no confirmation at all. If the ledger, identity, staging or blocking changed since the
    # requests were issued, the confirmations are stale and nothing moves.
    # Requests issued before evidence was scoped to ready opportunities answer the same inputs
    # under the `unheld` scope; both hashes are functions of the inputs alone.
    owner_confs = [c for c in inputs["confirmations"].values() if not c.get("simulation")]
    confirmation_basis: dict[str, str] = {}
    if owner_confs:
        for scope in (qp.EVIDENCE_SCOPE_READY, qp.EVIDENCE_SCOPE_UNHELD):
            base = qp.build_plan(inputs["ledger_rows"], inputs["identity_docs"], inputs["staged_by_email"],
                                 inputs["blocked"], inputs["blocked_opps"], {}, inputs["printed_in_text"],
                                 acquired_at=acquired_at, note=NOTE, evidence_scope=scope)
            base["inputs_sha256"] = {k: v for k, v in hashes_before.items() if k != "organization_confirmations.jsonl"}
            confirmation_basis[qp.plan_sha256(base)] = scope
        answered = {c["evidence"]["plan_sha256"] for c in owner_confs}
        stale = sorted(answered - set(confirmation_basis))
        if stale:
            print(f"refused: confirmations answer plan(s) {stale}, but these inputs give "
                  f"{sorted(confirmation_basis)}", file=sys.stderr)
            return 3
        confirmation_basis = {k: v for k, v in confirmation_basis.items() if k in answered}
        emails = {c["confirmed_by"]["email"] for c in owner_confs}
        if len(emails) != 1 or (args.operator_email and args.operator_email.strip().lower() not in emails):
            print(f"refused: confirmations are by {sorted(emails)}; --operator-email must be that one operator",
                  file=sys.stderr)
            return 2

    args.out.mkdir(parents=True)
    write_json(args.out / "intent.json", intent)
    (args.out / "intent.sha256").write_text(plan_sha + "\n")
    manifest_path = args.out / "evidence_manifest.json"
    write_json(manifest_path, intent["evidence_manifest"])
    write_csv(args.out / "idempotency_keys.csv", list(qp.idempotency_rows(intent)))
    write_csv(args.out / "blocked.csv", [
        {"document_sha256": d["document_sha256"], "planned_opportunity_id": inputs["blocked_opps"].get(d["document_sha256"]),
         "reasons": " ".join(d["reasons"])} for d in intent["blocked_documents"]]
        + [{"document_sha256": s, "planned_opportunity_id": o["planned_opportunity_id"], "reasons": " ".join(o["reasons"])}
           for o in intent["opportunities"] if o["status"] == qp.S_HELD for s in o["documents"]
           if s not in inputs["blocked"]])

    reconciliation = reconcile_evidence(intent, inputs, args.sqlite)
    write_csv(args.out / "evidence_reconciliation.csv", reconciliation)
    unclean = [r for r in reconciliation if not r["clean"]]
    staging_check = stage_evidence(manifest_path, "", apply=False)  # the tool's own validation, no DB
    violations = qp.evidence_scope_violations(intent)
    write_json(args.out / "evidence_scope_violations.json", violations)

    state_before = counts_before = None
    if args.target_dsn:
        st, counts_before = read_state(args.target_dsn, args.operator_email)
        state_before = qp.plan_state(intent, st)
        write_json(args.out / "state_before.json", state_before)
        # The operator's request sheet is issued by scripts/quote_organization_confirmation.py
        # from this directory, not here.

    run: dict[str, Any] = {
        "mode": "apply" if args.apply else "dry-run", "generated_at": datetime.now(UTC).isoformat(),
        "plan_version": qp.PLAN_VERSION, "plan_sha256": plan_sha, "summary": intent["summary"],
        "inputs_sha256_before": hashes_before, "evidence_reconciliation": {
            "rows": len(reconciliation), "clean": len(reconciliation) - len(unclean), "unclean": len(unclean)},
        "staging_tool_dry_run": {"mode": staging_check.get("mode"), **{
            k: staging_check.get("manifest", {}).get(k) for k in ("records", "observations", "observations_by_kind")}},
        "evidence_scope": intent.get("evidence_scope"), "evidence_scope_violations": len(violations),
        "confirmation_basis": confirmation_basis or None,
        "target": dbname(args.target_dsn) if args.target_dsn else None,
        "counts_before": counts_before, "state_before_totals": state_before and state_before["totals"],
    }

    exit_code = 0
    preflight: dict[str, Any] | None = None
    if args.allow_cleanroom_production:
        preflight = production_preflight(args, intent, plan_sha, hashes_before, inputs, len(unclean), len(violations),
                                         expected_inputs)
        run["production_preflight"] = {k: preflight[k] for k in ("passed", "refused", "fingerprint_sha256")}
    if preflight is not None and not preflight["passed"]:
        print(f"refused: production preflight: {', '.join(preflight['refused'])}", file=sys.stderr)
        exit_code = prod.EXIT_PREFLIGHT_REFUSED
    elif args.apply:
        if args.expect_plan_sha256 != plan_sha:
            print(f"refused: plan is {plan_sha}, expected {args.expect_plan_sha256}", file=sys.stderr)
            exit_code = 4
        elif unclean:
            print(f"refused: {len(unclean)} evidence reconciliation rows are not clean", file=sys.stderr)
            exit_code = 6
        elif preflight is not None:
            exit_code = apply_production(args, intent, manifest_path, ledger_prefix, run, preflight)
        else:
            exit_code = apply(args, intent, manifest_path, ledger_prefix, run)
    elif violations:
        exit_code = 8

    hashes_after = {k: sha256_file(p) for k, p in input_paths.items()}
    run["inputs_sha256_after"] = hashes_after
    run["inputs_unchanged"] = hashes_after == hashes_before
    run["ledger_prefix_unchanged"] = args.ledger.read_bytes()[: len(ledger_prefix)] == ledger_prefix
    if args.target_dsn:
        st_after, run["counts_after"] = read_state(args.target_dsn, args.operator_email)
        state_after = qp.plan_state(intent, st_after)
        write_json(args.out / "state_after.json", state_after)
        run["state_after_totals"] = state_after["totals"]
    write_json(args.out / "run.json", run)
    if not run["inputs_unchanged"] and exit_code == 0:
        exit_code = 3
    print(json.dumps({k: run[k] for k in ("mode", "plan_sha256", "summary", "evidence_reconciliation",
                                          "evidence_scope_violations", "inputs_unchanged") }, ensure_ascii=False, indent=1))
    return exit_code


def production_preflight(args: argparse.Namespace, intent: dict[str, Any], plan_sha: str,
                         hashes_before: dict[str, str], inputs: dict[str, Any], unclean: int, violations: int,
                         expected_inputs: dict[str, str]) -> dict[str, Any]:
    """Every production guard, read-only; writes preflight.json and fingerprint_before.json into --out."""
    _, port, _ = prod.dsn_parts(args.target_dsn)
    db = prod.read_database_facts(args.admin_dsn, args.operator_email)
    input_hashes = {k: v for k, v in hashes_before.items() if k != "document_decisions.jsonl"}
    input_hashes["policy"] = prod.policy_fingerprint(EXCLUDED_DOCUMENTS)
    confirming = {c["confirmed_by"]["operator_id"] for c in inputs["confirmations"].values()
                  if isinstance(c.get("confirmed_by"), dict)}
    checks = prod.evaluate(
        target_dsn=args.target_dsn, admin_dsn=args.admin_dsn, plan_sha=plan_sha, expected_plan=args.expect_plan_sha256,
        ledger_sha=hashes_before["document_decisions.jsonl"], expected_ledger=args.expect_ledger_hash,
        input_hashes=input_hashes, expected_inputs=expected_inputs, db=db, container=prod.container_facts(port),
        processes=prod.scan_processes(), crontab=prod.read_crontab(),
        plan_keys=(r["idempotency_key"] for r in qp.idempotency_rows(intent)),
        confirmation_operator_ids=confirming, evidence_unclean=unclean, scope_violations=violations)
    fp = prod.fingerprint(args.admin_dsn)
    prod.write_report(args.out / "fingerprint_before.json", fp)
    report = {"generated_at": datetime.now(UTC).isoformat(), "database": db.get("current_database"),
              "passed": not prod.refused(checks), "refused": prod.refused(checks), "checks": checks,
              "input_sha256": input_hashes, "plan_sha256": plan_sha,
              "operator": db.get("operator"), "migration_head": (db.get("migrations") or [None])[-1],
              "domain_event_max_position": db.get("domain_event_max_position"),
              "qimport_receipts": len(db.get("qimport_receipts") or {}), "fingerprint_sha256": fp["sha256"],
              "fingerprint": fp}
    prod.write_report(args.out / "preflight.json", {k: v for k, v in report.items() if k != "fingerprint"})
    return report


#: Tables an import may change; any other table changing is a post-apply mismatch.
IMPORT_MAY_CHANGE = ("crm.", "evidence.", "platform.command_receipt")


def post_apply_problems(run: dict[str, Any], diff: dict[str, dict[str, Any]], events: dict[str, int]) -> list[str]:
    """What the database shows after a production apply, against what the plan predicted."""
    problems = []
    predicted = run.get("state_before_totals") or {}
    applied = run.get("apply") or {}
    if applied.get("new_writes") != predicted.get("commands_would_write"):
        problems.append(f"new command writes {applied.get('new_writes')} ≠ predicted {predicted.get('commands_would_write')}")
    for table, key in (("evidence.source_record", "source_records_would_write"),
                       ("evidence.assertion", "assertions_would_write")):
        delta = (diff.get(table) or {}).get("delta", 0)
        if delta != predicted.get(key):
            problems.append(f"{table} grew by {delta} ≠ predicted {predicted.get(key)}")
    outside = sorted(t for t in diff if not t.startswith(IMPORT_MAY_CHANGE))
    if outside:
        problems.append(f"tables outside the import changed: {outside}")
    if events["not_owned_by_run"]:
        problems.append(f"{events['not_owned_by_run']} domain events appeared that no receipt of this run owns")
    if events["owned_by_run"] != applied.get("events_written"):
        problems.append(f"events owned by the run {events['owned_by_run']} ≠ reported {applied.get('events_written')}")
    if (diff.get("crm.domain_event") or {}).get("delta", 0) != events["owned_by_run"]:
        problems.append("crm.domain_event growth ≠ events owned by the run")
    if applied.get("refused_opportunities"):
        problems.append(f"{applied['refused_opportunities']} opportunities refused")
    return problems


def apply_production(args: argparse.Namespace, intent: dict[str, Any], manifest_path: Path, ledger_prefix: bytes,
                     run: dict[str, Any], preflight: dict[str, Any]) -> int:
    """Backup, re-check under the import lock, apply, and account for every change."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    try:
        backup = prod.take_backup(args.backup_dir, REPO, stamp)
    except prod.ProductionRefused as exc:
        run["aborted"] = f"backup: {exc}"
        print(f"refused: {run['aborted']}", file=sys.stderr)
        return prod.EXIT_BACKUP_FAILED
    run["backup"] = backup
    try:
        with prod.ImportLock(args.admin_dsn) as lock:
            # Immediately before the first write: nobody arrived, nothing moved since the preflight.
            others, procs = lock.other_sessions(), prod.scan_processes()
            fp_before = prod.fingerprint(args.admin_dsn)
            late = []
            if others:
                late.append(f"database sessions appeared: {others}")
            if procs["processes"]:
                late.append(f"processes naming the database appeared: {procs['processes']}")
            if fp_before["sha256"] != preflight["fingerprint_sha256"]:
                late.append("the database changed between the preflight and the write")
            if late:
                run["aborted"] = "; ".join(late)
                print(f"refused: {run['aborted']}", file=sys.stderr)
                return prod.EXIT_PREFLIGHT_REFUSED
            position = preflight["domain_event_max_position"]
            code = apply(args, intent, manifest_path, ledger_prefix, run,
                         production_operator_id=preflight["operator"]["id"])
            fp_after = prod.fingerprint(args.admin_dsn)
    except prod.ProductionRefused as exc:
        run["aborted"] = str(exc)
        print(f"refused: {exc}", file=sys.stderr)
        return prod.EXIT_PREFLIGHT_REFUSED
    diff = prod.fingerprint_diff(fp_before, fp_after)
    receipts = [e["command_receipt_id"] for e in read_apply_log(args.out)]
    events = prod.events_after(args.admin_dsn, position, receipts)
    problems = post_apply_problems(run, diff, events)
    prod.write_report(args.out / "fingerprint_after.json", fp_after)
    prod.write_report(args.out / "production_report.json", {
        "backup": backup, "fingerprint_before_sha256": fp_before["sha256"], "fingerprint_after_sha256": fp_after["sha256"],
        "tables_changed": diff, "events_after_preflight_position": {"position": position, **events},
        "apply": run.get("apply"), "post_apply_problems": problems, "apply_exit_code": code})
    run["post_apply_problems"] = problems
    if code == 0 and problems:
        print(f"post-apply mismatch: {problems}", file=sys.stderr)
        return prod.EXIT_POST_APPLY_MISMATCH
    return code


def read_apply_log(out: Path) -> list[dict[str, Any]]:
    """The new (not replayed) receipts of this run, from apply_log.csv."""
    path = out / "apply_log.csv"
    if not path.is_file() or not path.stat().st_size:
        return []
    return [r for r in csv.DictReader(path.open(encoding="utf-8")) if r["replayed"] == "False"]


def apply(args: argparse.Namespace, intent: dict[str, Any], manifest_path: Path, ledger_prefix: bytes,
          run: dict[str, Any], *, production_operator_id: str | None = None) -> int:
    def ledger_intact() -> bool:
        return sha256_file(args.ledger) == args.expect_ledger_hash

    # Checked here, before the first write, whatever the caller already checked.
    violations = qp.evidence_scope_violations(intent)
    if violations:
        run["aborted"] = f"{len(violations)} evidence scope violation(s): evidence outside the ready opportunities"
        run["evidence_scope_violations_detail"] = violations
        print(f"refused: {run['aborted']}", file=sys.stderr)
        return 8
    # Production never registers an operator; the preflight proved this one active and admin.
    reg = ({"operator_id": production_operator_id, "registered": False} if production_operator_id
           else register_operator(args.admin_dsn, args.operator_email))
    run["operator"] = {"registered_now": reg["registered"]}
    if intent["evidence_manifest"]["records"]:
        staged = stage_evidence(manifest_path, args.admin_dsn, apply=True)
        run["evidence_staging"] = {k: v for k, v in staged.items() if k != "target"}
    operator = OperatorIdentity(operator_id=reg["operator_id"], email_norm=args.operator_email.strip().lower(),
                                display_name=LEDGER_OPERATOR, role="admin", status="active")
    executor = Executor(args.target_dsn, operator)
    events_before = domain_event_total(args.target_dsn)
    log: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    for opp in intent["opportunities"]:
        if opp["status"] != qp.S_READY:
            continue
        if not ledger_intact():
            run["aborted"] = "document ledger changed during the run"
            write_csv(args.out / "apply_log.csv", log)
            return 3
        try:
            executor.run(opp, log)
        except CommandRefused as exc:
            refused.append({"planned_opportunity_id": opp["planned_opportunity_id"], "code": exc.code,
                            "detail": str(exc)})
    write_csv(args.out / "apply_log.csv", [{**e, "created": " ".join(e["created"]),
                                            "event_ids": len(e["event_ids"])} for e in log])
    write_json(args.out / "apply_refusals.json", refused)
    new_receipts = [e["command_receipt_id"] for e in log if not e["replayed"]]
    events = receipt_events(args.target_dsn, new_receipts)
    write_csv(args.out / "apply_events.csv", events)
    report = event_report(log, events, domain_event_total(args.target_dsn) - events_before)
    run["apply"] = {"commands": len(log), "replayed": sum(1 for e in log if e["replayed"]),
                    "new_writes": len(new_receipts), **report,
                    "refused_opportunities": len(refused)}
    if not ledger_intact():
        run["aborted"] = "document ledger changed during the run"
        return 3
    if not report["events_match_delta"]:
        print(f"events owned by the new receipts ({report['events_written']}) differ from the growth of "
              f"crm.domain_event ({report['domain_event_delta']})", file=sys.stderr)
        return 9
    return 7 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
