"""Read-only review of a historical quotation import: the plan beside what the database holds.

A quotation import (``scripts/quote_crm_import.py``) is decided in a plan file — ``intent.json``,
pinned by ``intent.sha256`` — and only the plan's ``ready`` opportunities are ever written. The
waiting and held opportunities exist nowhere but in that plan, so a screen that read only the
database could not show them, and could not prove that none of them leaked in. This module reads
both and puts them side by side:

* the **plan** is loaded from a directory and refused unless its canonical hash equals the
  pinned one, so the screen always names the exact plan it is comparing against;
* the **database** is read in one ``read only`` transaction, through the idempotency keys the
  import wrote (``qimport:v1:<planned opportunity>:…``) — the planned id is the only join between
  the two, and nothing is matched by name;
* every opportunity is then labelled by the plan (ready / waiting / held) and checked against the
  database, and two invariants are computed on every read: no waiting or held opportunity is in
  the database, and no evidence outside the ready scope is attached to a ready one.

**No message body is ever returned.** A Gmail record is reduced to an explicit allowlist of
header fields (subject, sender, recipients, dates, ids); anything else in the payload — present
or added later — is dropped here rather than filtered by the page.

Writes nothing. The PDF lookup only resolves a file whose bytes hash to the requested SHA-256 and
whose SHA-256 is a revision document of an imported opportunity.
"""

from __future__ import annotations

import hashlib
import json
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote as urlquote

from origenlab_api.v2.quote_crm_import_plan import plan_sha256

REVIEW_VERSION = "qimport-review/2026-09-25.1"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_KEY = re.compile(r"^qimport:v1:([0-9a-f-]{36}):(.+)$")

#: Payload fields a Gmail source record may show. Everything else is dropped, so a body added
#: to the payload later can never reach the page by accident.
GMAIL_HEADER_FIELDS = (
    "subject_raw",
    "sender",
    "recipients",
    "sent_at",
    "gmail_message_id",
    "gmail_thread_id",
    "rfc822_message_id",
    "gmail_labels",
    "direction_hint",
    "intake_class",
    "tier",
)

PLAN_STATUS_LABELS = {
    "ready": "ready",
    "needs_organization_confirmation": "waiting",
    "held": "held",
}


class PlanRefused(ValueError):
    """The plan directory is missing, unreadable, or its hash does not match."""


@dataclass(frozen=True)
class LoadedPlan:
    directory: Path
    sha256: str
    intent: dict[str, Any]
    run: dict[str, Any]


def load_plan(directory: str | Path) -> LoadedPlan:
    root = Path(directory)
    intent_path = root / "intent.json"
    pin_path = root / "intent.sha256"
    try:
        raw = intent_path.read_bytes()
        pinned = pin_path.read_text(encoding="utf-8").split()[0].strip()
    except (OSError, IndexError) as exc:
        raise PlanRefused(f"plan directory unreadable: {root}") from exc
    intent = json.loads(raw)
    actual = plan_sha256(intent)
    if not _SHA256.match(pinned) or actual != pinned:
        raise PlanRefused(f"intent.json hashes to {actual}, intent.sha256 pins {pinned!r}")
    run: dict[str, Any] = {}
    run_path = root / "run.json"
    if run_path.exists():
        run = json.loads(run_path.read_text(encoding="utf-8"))
    return LoadedPlan(directory=root, sha256=actual, intent=intent, run=run)


def gmail_message_url(gmail_message_id: str | None) -> str | None:
    """The message in the operator's own Gmail; the id is the Gmail API id, not RFC 822."""
    if not gmail_message_id or not re.fullmatch(r"[0-9a-f]{8,32}", gmail_message_id):
        return None
    return f"https://mail.google.com/mail/u/0/#all/{gmail_message_id}"


def gmail_rfc822_search_url(rfc822_message_id: str | None) -> str | None:
    if not rfc822_message_id:
        return None
    term = f"rfc822msgid:{rfc822_message_id.strip('<>')}"
    return "https://mail.google.com/mail/u/0/#search/" + urlquote(term, safe="")


def _header_view(payload: dict[str, Any]) -> dict[str, Any]:
    return {field: payload.get(field) for field in GMAIL_HEADER_FIELDS}


def _doc_sha(value: str | None) -> str | None:
    if isinstance(value, str) and value.startswith("sha256:") and _SHA256.match(value[7:]):
        return value[7:]
    return None


# ───────────────────────────────────────────────────────────── database reads


class QuoteImportReviewRepository:
    """One ``read only`` transaction per snapshot; same (connect, dsn) pair as the cockpit."""

    def __init__(self, connect: Any, dsn: str, statement_timeout_ms: int = 30_000) -> None:
        self._connect = connect
        self._dsn = dsn
        self._statement_timeout_ms = statement_timeout_ms

    @contextmanager
    def _read(self):
        with self._connect(self._dsn, autocommit=False) as conn:
            with conn.cursor() as cur:
                cur.execute("set transaction read only")
                cur.execute(f"set local statement_timeout = {int(self._statement_timeout_ms)}")
                try:
                    yield cur
                finally:
                    conn.rollback()

    def snapshot(self, plan: LoadedPlan) -> dict[str, Any]:
        intent = plan.intent
        dedupe_keys = sorted(
            {k for o in intent["opportunities"] for k in o.get("source_dedupe_keys", [])}
            | {f"gmail_message:{r['external_id']}" for r in intent["evidence_manifest"]["records"]}
        )
        org_ids = sorted(
            {
                o["confirmation"]["organization_id"]
                for o in intent["opportunities"]
                if o.get("confirmation") and o["confirmation"].get("organization_id")
            }
        )
        with self._read() as cur:
            cur.execute("select current_database()")
            database = cur.fetchone()[0]
            cur.execute(
                """
                select idempotency_key, command_name, status, response_body
                  from platform.command_receipt
                 where idempotency_key like 'qimport:v1:%%'
                 order by idempotency_key
                """
            )
            receipts = [
                {"key": r[0], "command": r[1], "status": r[2], "response": r[3] or {}}
                for r in cur.fetchall()
            ]
            cur.execute(
                """
                select op.id::text, op.title, op.stage, op.version, op.organization_id::text,
                       op.created_at::text
                  from crm.opportunity op order by op.created_at, op.id
                """
            )
            opportunities = [
                dict(zip(("id", "title", "stage", "version", "organization_id", "created_at"), r))
                for r in cur.fetchall()
            ]
            cur.execute(
                """
                select oo.opportunity_id::text, oo.organization_id::text, o.name, oo.role,
                       oo.confirmation, oo.valid_to is null
                  from crm.opportunity_organization oo
                  join crm.organization o on o.id = oo.organization_id
                 order by oo.opportunity_id, oo.role, o.name
                """
            )
            links = [
                dict(zip(("opportunity_id", "organization_id", "name", "role", "confirmation", "current"), r))
                for r in cur.fetchall()
            ]
            cur.execute(
                """
                select oe.opportunity_id::text, sr.dedupe_key, oe.relation, oe.unlinked_at is null
                  from crm.opportunity_evidence oe
                  join evidence.source_record sr on sr.id = oe.source_record_id
                 order by oe.opportunity_id, sr.dedupe_key
                """
            )
            evidence_links = [
                dict(zip(("opportunity_id", "dedupe_key", "relation", "current"), r))
                for r in cur.fetchall()
            ]
            cur.execute(
                """
                select q.id::text, q.opportunity_id::text, q.quote_number, q.number_origin,
                       qr.id::text, qr.revision_no, qr.status, qr.origin, qr.pdf_sha256,
                       qr.sent_at::text, sr.dedupe_key, qr.superseded_by_revision_no
                  from crm.quote q
                  join crm.quote_revision qr on qr.quote_id = q.id
                  left join evidence.source_record sr on sr.id = qr.origin_source_record_id
                 order by q.opportunity_id, q.quote_number, qr.revision_no
                """
            )
            revisions = [
                dict(
                    zip(
                        (
                            "quote_id", "opportunity_id", "quote_number", "number_origin",
                            "revision_id", "revision_no", "status", "origin", "pdf_sha256",
                            "sent_at", "origin_dedupe_key", "superseded_by_revision_no",
                        ),
                        r,
                    )
                )
                for r in cur.fetchall()
            ]
            cur.execute(
                """
                select sr.id::text, sr.dedupe_key, sr.review_status, sr.is_quarantined,
                       sr.payload, sr.created_at::text
                  from evidence.source_record sr
                 where sr.dedupe_key = any(%s)
                 order by sr.dedupe_key
                """,
                (dedupe_keys,),
            )
            records = [
                {
                    "id": r[0], "dedupe_key": r[1], "review_status": r[2],
                    "is_quarantined": r[3], "headers": _header_view(r[4] or {}),
                    "created_at": r[5],
                }
                for r in cur.fetchall()
            ]
            cur.execute(
                """
                select sr.dedupe_key, a.id::text, a.kind, a.value_norm, a.value, a.resolution
                  from evidence.assertion a
                  join evidence.source_record sr on sr.id = a.source_record_id
                 where sr.dedupe_key = any(%s)
                 order by sr.dedupe_key, a.kind, a.value_norm
                """,
                (dedupe_keys,),
            )
            assertions = [
                dict(zip(("dedupe_key", "id", "kind", "value_norm", "value", "resolution"), r))
                for r in cur.fetchall()
            ]
            cur.execute(
                "select id::text, name from crm.organization where id = any(%s::uuid[])",
                (org_ids,),
            )
            organizations = {r[0]: r[1] for r in cur.fetchall()}
            cur.execute(
                "select id::text, email_norm, role, status from platform.operator order by email_norm"
            )
            operators = [dict(zip(("id", "email", "role", "status"), r)) for r in cur.fetchall()]
        return {
            "database": database,
            "receipts": receipts,
            "opportunities": opportunities,
            "organization_links": links,
            "evidence_links": evidence_links,
            "revisions": revisions,
            "records": records,
            "assertions": assertions,
            "organizations": organizations,
            "operators": operators,
        }


# ─────────────────────────────────────────────────────────────── the read model


def _plan_status(raw: str) -> str:
    return PLAN_STATUS_LABELS.get(raw, raw)


def build_review(plan: LoadedPlan, db: dict[str, Any]) -> dict[str, Any]:
    """Pure: the plan and a database snapshot in, the page's read model out."""
    intent = plan.intent
    ready_record_keys = {f"gmail_message:{r['external_id']}" for r in intent["evidence_manifest"]["records"]}
    ready_docs = {
        d
        for o in intent["opportunities"]
        if o["status"] == "ready"
        for d in o.get("documents", [])
    }

    # planned id -> database opportunity id, from the `:open` receipt only.
    planned_to_db: dict[str, str] = {}
    receipts_by_planned: dict[str, list[dict[str, Any]]] = {}
    for r in db["receipts"]:
        m = _KEY.match(r["key"])
        if not m:
            continue
        receipts_by_planned.setdefault(m.group(1), []).append(r)
        if m.group(2) == "open" and r["response"].get("opportunity_id"):
            planned_to_db[m.group(1)] = r["response"]["opportunity_id"]
    db_to_planned = {v: k for k, v in planned_to_db.items()}

    ops_by_id = {o["id"]: o for o in db["opportunities"]}
    links_by_op: dict[str, list[dict[str, Any]]] = {}
    for link in db["organization_links"]:
        links_by_op.setdefault(link["opportunity_id"], []).append(link)
    ev_by_op: dict[str, list[dict[str, Any]]] = {}
    for link in db["evidence_links"]:
        ev_by_op.setdefault(link["opportunity_id"], []).append(link)
    revs_by_op: dict[str, list[dict[str, Any]]] = {}
    revs_by_sha: dict[str, list[dict[str, Any]]] = {}
    for rev in db["revisions"]:
        revs_by_op.setdefault(rev["opportunity_id"], []).append(rev)
        if rev["pdf_sha256"]:
            revs_by_sha.setdefault(rev["pdf_sha256"], []).append(rev)
    records_by_key = {r["dedupe_key"]: r for r in db["records"]}
    assertions_by_key: dict[str, list[dict[str, Any]]] = {}
    for a in db["assertions"]:
        assertions_by_key.setdefault(a["dedupe_key"], []).append(a)

    # document sha -> manifest records that carry it (as staged in the plan)
    carriers: dict[str, list[str]] = {}
    for r in intent["evidence_manifest"]["records"]:
        for obs in r.get("observations", []):
            sha = _doc_sha(obs.get("value")) if obs.get("kind") == "document_reference" else None
            if sha:
                carriers.setdefault(sha, []).append(f"gmail_message:{r['external_id']}")
    filenames: dict[str, str] = {}
    stored_paths: dict[str, str] = {}
    for r in intent["evidence_manifest"]["records"]:
        for doc in r.get("payload", {}).get("documents", []):
            if doc.get("sha256") and doc.get("filename"):
                filenames.setdefault(doc["sha256"], doc["filename"])
            if doc.get("sha256") and doc.get("stored_path"):
                stored_paths.setdefault(doc["sha256"], doc["stored_path"])

    def evidence_view(key: str) -> dict[str, Any]:
        rec = records_by_key.get(key)
        headers = rec["headers"] if rec else {}
        return {
            "dedupe_key": key,
            "in_database": rec is not None,
            "in_ready_scope": key in ready_record_keys,
            "source_record_id": rec["id"] if rec else None,
            "review_status": rec["review_status"] if rec else None,
            "is_quarantined": rec["is_quarantined"] if rec else None,
            "subject": headers.get("subject_raw"),
            "sender": headers.get("sender"),
            "recipients": headers.get("recipients"),
            "sent_at": headers.get("sent_at"),
            "gmail_thread_id": headers.get("gmail_thread_id"),
            "gmail_url": gmail_message_url(headers.get("gmail_message_id") or key.split(":", 1)[-1]),
            "gmail_search_url": gmail_rfc822_search_url(headers.get("rfc822_message_id")),
            "assertions": [
                {
                    "kind": a["kind"],
                    "value": a["value_norm"],
                    "resolution": a["resolution"],
                    "document_sha256": (
                        _doc_sha(a["value_norm"])
                        if a["kind"] == "document_reference"
                        else ((a["value"] or {}).get("document_sha256") if isinstance(a["value"], dict) else None)
                    ),
                }
                for a in assertions_by_key.get(key, [])
            ]
            if rec
            else [],
        }

    rows: list[dict[str, Any]] = []
    violations: dict[str, list[dict[str, Any]]] = {
        "waiting_or_held_in_database": [],
        "unready_evidence_in_ready_opportunity": [],
        "ready_opportunity_incomplete": [],
    }

    for o in intent["opportunities"]:
        planned = o["planned_opportunity_id"]
        status = _plan_status(o["status"])
        db_id = planned_to_db.get(planned)
        op = ops_by_id.get(db_id) if db_id else None
        conf = o.get("confirmation")
        if conf:
            conf_view = {
                "state": "confirmed",
                "action": conf.get("action"),
                "organization_id": conf.get("organization_id"),
                "organization_name": db["organizations"].get(conf.get("organization_id") or ""),
                "confirmed_by": (conf.get("confirmed_by") or {}).get("email"),
                "operator_id": (conf.get("confirmed_by") or {}).get("operator_id"),
                "role": (conf.get("confirmed_by") or {}).get("role"),
                "confirmed_at": conf.get("confirmed_at"),
                "category": (conf.get("evidence") or {}).get("category"),
                "note": conf.get("note"),
            }
        else:
            conf_view = {"state": "held" if status == "held" else "pending"}

        db_revs = revs_by_op.get(db_id, []) if db_id else []
        db_rev_by_sha = {r["pdf_sha256"]: r for r in db_revs if r["pdf_sha256"]}
        quotes = []
        for q in o.get("quotes", []):
            revisions = []
            for rev in q.get("revisions", []):
                sha = rev["document_sha256"]
                dbr = db_rev_by_sha.get(sha)
                carrying = carriers.get(sha, [])
                canonical = rev.get("origin_dedupe_key")
                revisions.append(
                    {
                        "document_sha256": sha,
                        "filename": filenames.get(sha),
                        "sent_at": rev.get("sent_at"),
                        "supersedes_document_sha256": rev.get("supersedes_document_sha256"),
                        "canonical_message": canonical,
                        "duplicate_messages": sorted(k for k in carrying if k != canonical),
                        "in_database": dbr is not None,
                        "quote_id": dbr["quote_id"] if dbr else None,
                        "revision_id": dbr["revision_id"] if dbr else None,
                        "revision_no": dbr["revision_no"] if dbr else None,
                        "status": dbr["status"] if dbr else None,
                        "origin": dbr["origin"] if dbr else None,
                        "number_origin": dbr["number_origin"] if dbr else None,
                        "db_origin_message": dbr["origin_dedupe_key"] if dbr else None,
                        "superseded_by_revision_no": dbr["superseded_by_revision_no"] if dbr else None,
                        "pdf_available": bool(dbr) and sha in stored_paths,
                    }
                )
            quotes.append({"quote_number": q["quote_number"], "revisions": revisions})

        keys = sorted(set(o.get("source_dedupe_keys", [])))
        evidence = [evidence_view(k) for k in keys] if status == "ready" else [
            {"dedupe_key": k, "in_database": k in records_by_key, "in_ready_scope": k in ready_record_keys,
             "gmail_url": gmail_message_url(k.split(":", 1)[-1])}
            for k in keys
        ]

        checks: list[dict[str, Any]] = []
        if status == "ready":
            current_orgs = [link for link in links_by_op.get(db_id, []) if link["current"]] if db_id else []
            linked_keys = {e["dedupe_key"] for e in ev_by_op.get(db_id, []) if e["current"]} if db_id else set()
            planned_shas = {r["document_sha256"] for q in quotes for r in q["revisions"]}
            db_shas = set(db_rev_by_sha)
            asserted_docs = {
                a["document_sha256"]
                for e in evidence
                for a in e.get("assertions", [])
                if a["kind"] == "document_reference" and a["document_sha256"]
            }
            checks = [
                {"code": "opportunity_in_database", "ok": op is not None},
                {
                    "code": "confirmed_organization_linked",
                    "ok": bool(conf)
                    and any(
                        link["organization_id"] == conf.get("organization_id")
                        and link["role"] == "requesting_institution"
                        for link in current_orgs
                    ),
                },
                {"code": "every_revision_in_database", "ok": planned_shas <= db_shas},
                {"code": "no_extra_revision_in_database", "ok": db_shas <= planned_shas},
                {"code": "every_message_in_database", "ok": all(e["in_database"] for e in evidence)},
                {"code": "every_message_linked", "ok": set(keys) <= linked_keys},
                {"code": "every_document_asserted", "ok": planned_shas <= asserted_docs},
                {
                    "code": "revision_origin_is_canonical",
                    "ok": all(
                        r["db_origin_message"] == r["canonical_message"]
                        for q in quotes
                        for r in q["revisions"]
                        if r["in_database"]
                    ),
                },
            ]
            # Invariant 10: everything attached to a ready opportunity is in the ready scope.
            attached = linked_keys | {r["origin_dedupe_key"] for r in db_revs if r["origin_dedupe_key"]}
            for key in sorted(attached):
                if key not in ready_record_keys:
                    violations["unready_evidence_in_ready_opportunity"].append(
                        {"planned_opportunity_id": planned, "dedupe_key": key, "why": "record outside ready scope"}
                    )
            for e in evidence:
                for a in e.get("assertions", []):
                    sha = a.get("document_sha256")
                    if sha and sha not in ready_docs:
                        violations["unready_evidence_in_ready_opportunity"].append(
                            {"planned_opportunity_id": planned, "dedupe_key": e["dedupe_key"],
                             "why": f"assertion {a['kind']} about unready document {sha}"}
                        )
            for sha in db_shas - ready_docs:
                violations["unready_evidence_in_ready_opportunity"].append(
                    {"planned_opportunity_id": planned, "document_sha256": sha, "why": "revision of unready document"}
                )
            if not all(c["ok"] for c in checks):
                violations["ready_opportunity_incomplete"].append(
                    {"planned_opportunity_id": planned, "failed": [c["code"] for c in checks if not c["ok"]]}
                )
            completeness = "complete" if all(c["ok"] for c in checks) else "incomplete"
        else:
            # Invariant 9: nothing of a waiting or held opportunity reached the database.
            leaks = []
            if receipts_by_planned.get(planned):
                leaks.append(f"{len(receipts_by_planned[planned])} import receipts")
            for d in o.get("documents", []):
                if d in revs_by_sha:
                    leaks.append(f"revision of document {d}")
            if leaks:
                violations["waiting_or_held_in_database"].append(
                    {"planned_opportunity_id": planned, "status": status, "leaks": leaks}
                )
            completeness = "not_imported" if not leaks else "leaked"

        orgs = [
            {"organization_id": link["organization_id"], "name": link["name"], "role": link["role"],
             "confirmation": link["confirmation"]}
            for link in links_by_op.get(db_id, [])
            if link["current"]
        ] if db_id else []
        rows.append(
            {
                "planned_opportunity_id": planned,
                "plan_status": status,
                "plan_status_raw": o["status"],
                "reasons": o.get("reasons", []),
                "warnings": o.get("warnings", []),
                "printed_organization": o.get("printed_organization"),
                "printed_addressee": o.get("printed_addressee"),
                "recipient_addresses": o.get("recipient_addresses", []),
                "confirmation": conf_view,
                "opportunity": (
                    {"id": op["id"], "title": op["title"], "stage": op["stage"], "version": op["version"]}
                    if op
                    else None
                ),
                "organizations": orgs,
                "quotes": quotes,
                "evidence": evidence,
                "checks": checks,
                "completeness": completeness,
                "commands_planned": len(o.get("steps", [])),
                "receipts_in_database": len(receipts_by_planned.get(planned, [])),
            }
        )

    # Anything in the database that the plan does not account for.
    unplanned_ops = [
        {"id": op["id"], "title": op["title"], "stage": op["stage"]}
        for op in db["opportunities"]
        if op["id"] not in db_to_planned
    ]
    unplanned_revisions = [
        {"quote_number": r["quote_number"], "opportunity_id": r["opportunity_id"], "pdf_sha256": r["pdf_sha256"]}
        for r in db["revisions"]
        if r["opportunity_id"] not in db_to_planned
    ]
    imported_keys = {k for k in ready_record_keys if k in records_by_key}
    ready_rows = [r for r in rows if r["plan_status"] == "ready"]
    counts = {
        "plan_opportunities": len(rows),
        "ready": len(ready_rows),
        "waiting": sum(r["plan_status"] == "waiting" for r in rows),
        "held": sum(r["plan_status"] == "held" for r in rows),
        "ready_in_database": sum(r["opportunity"] is not None for r in ready_rows),
        "quotes_in_database_for_ready": len(
            {rv["quote_id"] for r in ready_rows for q in r["quotes"] for rv in q["revisions"] if rv["quote_id"]}
        ),
        "revisions_in_database_for_ready": sum(
            rv["in_database"] for r in ready_rows for q in r["quotes"] for rv in q["revisions"]
        ),
        "gmail_records_in_ready_scope": len(ready_record_keys),
        "gmail_records_in_database": len(imported_keys),
        "assertions_on_ready_records": sum(len(assertions_by_key.get(k, [])) for k in imported_keys),
        "organization_confirmations": sum(r["confirmation"]["state"] == "confirmed" for r in rows),
        "unplanned_opportunities_in_database": len(unplanned_ops),
        "unplanned_revisions_in_database": len(unplanned_revisions),
    }
    return {
        "review_version": REVIEW_VERSION,
        "plan": {
            "sha256": plan.sha256,
            "version": intent.get("plan_version"),
            "evidence_scope": intent.get("evidence_scope"),
            "generated_at": plan.run.get("generated_at"),
            "dry_run_target": plan.run.get("target"),
            "summary": intent.get("summary", {}),
        },
        "database": db["database"],
        "operators": db["operators"],
        "counts": counts,
        "invariants": {
            name: {"ok": not items, "violations": items} for name, items in violations.items()
        },
        "unplanned": {"opportunities": unplanned_ops, "revisions": unplanned_revisions},
        "opportunities": rows,
    }


def revision_document_path(
    review: dict[str, Any], plan: LoadedPlan, documents_root: str | Path, sha: str
) -> Path | None:
    """A PDF on disk, only for a revision document that is in the database and hashes right."""
    if not _SHA256.match(sha):
        return None
    allowed = any(
        rv["document_sha256"] == sha and rv["in_database"]
        for r in review["opportunities"]
        if r["plan_status"] == "ready"
        for q in r["quotes"]
        for rv in q["revisions"]
    )
    if not allowed:
        return None
    stored = None
    for rec in plan.intent["evidence_manifest"]["records"]:
        for doc in rec.get("payload", {}).get("documents", []):
            if doc.get("sha256") == sha and doc.get("stored_path"):
                stored = doc["stored_path"]
                break
        if stored:
            break
    if not stored:
        return None
    root = Path(documents_root).resolve()
    path = (root / stored).resolve()
    if root not in path.parents or not path.is_file():
        return None
    if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
        return None
    return path
