"""Email → cases: read the snapshot, apply the planned actions, undo one. Runs as `origenlab_api`.

`mail_rules.py` decides; this module only reads what it needs and runs what it planned, through
the **existing** command handlers — `open_commercial_case`, `add_case_organization`,
`advance_case_stage`, `link_case_evidence` (`case_command_repository.py`),
`record_historical_quotation` / `void_historical_quote_revision` (`quote_import_repository.py`),
`archive-organization` (`crm_authoring.py`), `record_case_won` (`case_won.py`, the same writer
a person reaches through `POST /v2/commands/record-case-won`) — plus the three small handlers
below that had no equivalent: an institution «por confirmar» and the two inverses (unlink an
evidence link, correct a terminal stage).

**One action, one transaction, one receipt.** Every step of an action — a case, its institution,
three stage moves and a quote, say — runs inside one transaction under one
`platform.command_receipt` (`apply_mail_rule`, key `mail-rule:<evidence id>:<rule id>`). A step
that is refused rolls the whole action back, receipt included, and the action is reported as
refused with the step's code and sentence. A re-run replays the receipt (same operator) or plans
nothing at all (the planner skips every email any `apply_mail_rule` receipt names).

**System attribution.** The steps run under the applying admin's identity marked
`acts_as_system` with an `event_attribution` of the rule, its reasons and the email: every event
is `actor_kind = 'worker'` with no `actor_operator_id`, and its `command_receipt_id` leads to the
admin who pressed «Aplicar». Rows that require a named operator (`owner_operator_id`,
`linked_by_operator_id`, `confirmed_by_operator_id`, `created_by_operator_id`) name that admin.

**Undo is a person's act.** `undo` reads the receipt of one applied action and reverses it under
its own receipt (`undo_mail_rule_action`, key `mail-rule-undo:<receipt id>`), with ordinary
operator events whose `attribution.undoes_receipt_id` names the action. Nothing is deleted: a
link is unlinked, a created case is abandoned as `discarded_by_correction`, a quote revision is
voided, a stage is corrected back, an institution «por confirmar» is archived.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import Any, Callable

from origenlab_api.v2.case_command_repository import V2CaseCommandRepository
from origenlab_api.v2.case_kind import refuse_tender
from origenlab_api.v2.case_commands import (
    ADD_CASE_ORGANIZATION,
    ADVANCE_CASE_STAGE,
    LINK_CASE_EVIDENCE,
    OPEN_COMMERCIAL_CASE,
    RECORD_CASE_WON,
    AddCaseOrganizationBody,
    AdvanceCaseStageBody,
    LinkCaseEvidenceBody,
    OpenCommercialCaseBody,
    validated_case,
)
from origenlab_api.v2.case_won import record_case_won
from origenlab_api.v2.command_core import DEFAULT_COMMAND_TIMEOUT_MS
from origenlab_api.v2.commands import CommandRefused, as_uuid
from origenlab_api.v2.crm_authoring import V2CrmAuthoringRepository
from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2 import forwarded_requests
from origenlab_api.v2.mail_rules import (
    ADD_CASE_SENDER_PARTICIPANT,
    ATTACH_FORWARD,
    AUTO,
    CONFIRM_ORGANIZATION_RECORD,
    CREATE_REQUEST_FROM_FORWARD_STEP,
    LATE_QUOTE_WINDOW_DAYS,
    LINK_AFTER_CLOSE,
    OWN_DOMAINS,
    RECORD_AUTHORED_QUOTE_SENT,
    REPLY_WINDOW_DAYS,
    SETTLE_DAYS,
    SYSTEM_LABEL,
    UNCONFIRM_ORGANIZATION_RECORD,
    UPDATE_ORGANIZATION,
    WEB_QUOTE_ORIGIN,
    CaseQuote,
    CaseState,
    ForwardRequest,
    InstitutionFacts,
    MailDocument,
    MailEvidence,
    Organization,
    PlannedAction,
    QuoteRevision,
    Snapshot,
    addresses,
    domain_of,
    mail_direction,
    plan,
)
from origenlab_api.v2.quote_import_commands import (
    RECORD_HISTORICAL_QUOTATION,
    VOID_HISTORICAL_QUOTE_REVISION,
    RecordHistoricalQuotationBody,
    VoidHistoricalQuoteRevisionBody,
    validated_quote_import,
)
from origenlab_api.v2.quote_import_repository import V2QuoteImportRepository
from origenlab_api.v2.quote_sent import record_authored_quote_sent

APPLY_COMMAND = "apply_mail_rule"
UNDO_COMMAND = "undo_mail_rule_action"
DISCARDED_BY_CORRECTION = "discarded_by_correction"
APPLIED_LIST_LIMIT = 200

REGISTER_MAIL_ORGANIZATION = "register_mail_organization"
UNLINK_CASE_EVIDENCE = "unlink_case_evidence"
CORRECT_CASE_STAGE = "correct_case_stage"
ARCHIVE_ORGANIZATION = "archive-organization"
REMOVE_ORGANIZATION_DOMAIN = "remove-organization-domain"
#: «Aplicar» sends the previewed pairs in batches of this size; the dashboard drives the batches.
APPLY_BATCH = 10


def _digest(value: dict[str, Any]) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


# ─────────────────────────────────────────────────────────────── the snapshot ──

#: Live Phase 4a captures only — the records the worker writes: a `comms.message` in the same
#: transaction, no staging hash, no assertions other than the mail triage's own proposals. The
#: staged historical `gmail_message` records are never acted on (they are read only through
#: `_LINKS_SQL`, for the threads of their cases), and neither is a record a person rejected. The
#: triage (`apps/worker` triage.py) adds `message_triage` / `product_mention` rows to live captures;
#: they must not hide a live email from the rules.
_EVIDENCE_SQL = """
select sr.id::text as id, sr.payload, m.direction, m.provider_thread_id,
       requester.value as requester_match, quote_refs.value as quote_refs, ai.intent as model_intent,
       case_match.value as case_match, web_form.is_web_form, web_form.reply_to
  from evidence.source_record sr
  join lateral (select cm.id, cm.direction, cm.provider_thread_id from comms.message cm
                 where cm.provider_message_id = sr.payload ->> 'gmail_message_id'
                 order by cm.created_at limit 1) m on true
  left join lateral (
       select a.value
         from evidence.assertion a
        where a.source_record_id = sr.id
          and a.kind = 'message_triage'
          and a.value_norm = 'requester:v1'
        order by a.created_at desc
        limit 1
  ) requester on true
  left join lateral (
       select a.value from evidence.assertion a
        where a.source_record_id = sr.id and a.kind = 'message_triage' and a.value_norm = 'quote_refs:v1'
        limit 1
  ) quote_refs on true
  left join lateral (
       -- The newest triage reading's AI intent, when the model read the email.
       select a.value -> 'reading' ->> 'intent' as intent from evidence.assertion a
        where a.source_record_id = sr.id and a.kind = 'message_triage' and a.value_norm ~ '^triage:v[0-9]+$'
        order by substring(a.value_norm from 9)::int desc
        limit 1
  ) ai on true
  left join lateral (
       select a.value from evidence.assertion a
        where a.source_record_id = sr.id and a.kind = 'message_triage' and a.value_norm = 'case_match:v1'
        order by a.created_at desc
        limit 1
  ) case_match on true
  left join lateral (
       -- A web-quote-form submission: the triage worker saw the x-origenlab-origin marker (the raw
       -- headers are not stored, so its reason is the persisted proof) and the Reply-To address is
       -- the enquirer, kept in comms.message_participant.
       select exists (select 1 from evidence.assertion a
                       where a.source_record_id = sr.id and a.kind = 'message_triage'
                         and a.value_norm ~ '^triage:v[0-9]+$'
                         and a.value -> 'reasons' ? 'header:x-origenlab-origin') as is_web_form,
              (select p.address_norm from comms.message_participant p
                where p.message_id = m.id and p.role = 'reply_to'
                order by p.address_norm limit 1) as reply_to
  ) web_form on true
 where sr.kind = 'gmail_message' and not sr.is_quarantined
   and sr.review_status <> 'rejected'
   and sr.payload ->> 'staging_source_record_sha256' is null
   and not exists (select 1 from evidence.assertion a where a.source_record_id = sr.id
                     and a.kind not in ('message_triage', 'product_mention'))
 order by sr.id
"""
_LINKS_SQL = """
select oe.source_record_id::text as source_record_id, oe.opportunity_id::text as opportunity_id,
       sr.payload ->> 'gmail_thread_id' as thread_id, sr.payload ->> 'sender' as sender
  from crm.opportunity_evidence oe
  join evidence.source_record sr on sr.id = oe.source_record_id
 where oe.unlinked_at is null and oe.source_record_id is not null
"""
#: Sales cases only (`case_kind.py`): R1–R15 never link, advance, reopen or close a tender. An email
#: already linked to a tender keeps that link in `linked_case_ids` (so no rule opens a second case
#: for it), but the tender is not a case the planner can act on.
#: `paused_until`: «En pausa hasta…» is an open «Retomar: …» task due later (`CaseMove`). A
#: «Recordarme…» reminder is a task too, but not a pause.
_CASES_SQL = """
select o.id::text as id, o.title, o.stage, o.version, o.organization_id::text as organization_id,
       o.closed_at is not null as closed,
       (select min(t.due_at) from crm.task t
         where t.opportunity_id = o.id and t.status = 'open' and t.due_at > now()
           and t.title like 'Retomar:%') as paused_until
  from crm.opportunity o
 where o.kind = 'sale'
"""
_QUOTES_SQL = """
select q.id::text as quote_id, q.opportunity_id::text as opportunity_id, q.quote_number,
       r.id::text as revision_id, r.revision_no, r.status, r.version,
       r.superseded_by_revision_no is not null as superseded, r.sent_at, r.origin, r.approved_at
  from crm.quote q
  join crm.opportunity o on o.id = q.opportunity_id and o.kind = 'sale'
  left join crm.quote_revision r on r.quote_id = q.id
 order by q.quote_number, r.revision_no
"""
_ORGANIZATIONS_SQL = """
select o.id::text as id, o.name, o.version, o.confirmation, o.kind,
       exists (select 1 from crm.organization_relationship rel
                where rel.organization_id = o.id and rel.role in ('supplier', 'manufacturer')
                  and (rel.valid_to is null or rel.valid_to > current_date)) as is_supplier,
       coalesce(array(select d.domain_norm from crm.organization_domain d
                       where d.organization_id = o.id and d.removed_at is null
                       order by d.domain_norm), '{}') as domains
  from crm.organization o
 where o.merged_into_organization_id is null and o.status = 'active'
"""
#: A thread holding an outbound message that has no evidence record is a campaign copy
#: (`capture.is_bulk_send`): the capture keeps the message and writes no evidence for it.
_CAMPAIGN_THREADS_SQL = """
select distinct m.provider_thread_id
  from comms.message m
 where m.direction = 'outbound' and m.parse_status = 'parsed' and m.provider_thread_id is not null
   and not exists (select 1 from evidence.source_record sr
                    where sr.dedupe_key = 'gmail_message:' || m.provider_message_id)
"""
#: Rules whose action opens a case; the rest file an email in one.
OPENING_RULES: frozenset[str] = frozenset({"R3", "R4", "R7", "R10"})
#: Rules that act on an institution, not on an email (R15).
INSTITUTION_RULES: frozenset[str] = frozenset({"R15"})
REMOVED_FROM_HOY = "Quitado desde Hoy: la acción automática no correspondía"
_RECENT_AUTOMATIC_SQL = """
select r.id::text as receipt_id, r.completed_at, r.response_body, sr.kind, sr.payload
  from platform.command_receipt r
  left join evidence.source_record sr on sr.id::text = r.response_body ->> 'evidence_id'
 where r.command_name = %s and r.status = 'completed'
   and r.response_body ->> 'trigger' = 'automatic'
   and r.completed_at > now() - make_interval(hours => %s)
   -- Never an action on a tender (`case_kind.py`), should an old one exist.
   and not exists (select 1 from crm.opportunity t
                    where t.id::text = r.response_body ->> 'case_id' and t.kind = 'tender')
   and not exists (select 1 from platform.command_receipt u
                    where u.command_name = %s and u.status = 'completed'
                      and u.response_body ->> 'undoes_receipt_id' = r.id::text)
 order by r.completed_at desc
 limit %s
"""
_FORWARD_PEOPLE_SQL = """
select distinct o.id::text, f.payload ->> 'original_email'
  from evidence.source_record f
  join crm.opportunity o
    on o.kind = 'sale'
   and (o.origin_source_record_id = f.id
        or exists (select 1 from crm.opportunity_evidence oe
                    where oe.opportunity_id = o.id and oe.unlinked_at is null
                      and oe.source_record_id::text = f.payload ->> 'forwarded_message_id'))
 where f.kind = 'forwarded_request' and f.review_status = 'promoted'
"""
_APPLIED_EVIDENCE_SQL = """
select distinct response_body ->> 'evidence_id', response_body ->> 'rule_id'
  from platform.command_receipt
 where command_name = %s and status = 'completed'
"""
#: Rules that act on an email another rule already acted on (R14, R16), each once: their apply receipt
#: is looked up by its own key, not by the email.
AFTER_APPLY_RULES: frozenset[str] = frozenset({"R14", "R16", "R17"})
#: R15: the institutions «por confirmar», what created them (the receipt of their
#: `organization.created` event), and the PDF file name of each quote on their cases that is still
#: sent and was recorded `%(days)s` days ago or more. A case an undo discarded does not count.
_INSTITUTIONS_SQL = """
select o.id::text,
       (select case when r.command_name = 'complete_case_from_sent_quote' then 'complete_case'
                    when r.command_name = 'apply_mail_rule' then r.response_body ->> 'rule_id' end
          from crm.domain_event ev
          join platform.command_receipt r on r.id = ev.command_receipt_id
         where ev.aggregate_kind = 'organization' and ev.aggregate_id = o.id
           and ev.event_type = 'organization.created'
         order by ev.seq limit 1) as created_by,
       coalesce(array(
         select coalesce((select d ->> 'filename'
                            from jsonb_array_elements(case when jsonb_typeof(sr.payload -> 'documents') = 'array'
                                                           then sr.payload -> 'documents' else '[]'::jsonb end) d
                           where lower(d ->> 'sha256') = lower(qr.pdf_sha256) limit 1), '')
           from crm.opportunity op
           join crm.quote q on q.opportunity_id = op.id
           join crm.quote_revision qr on qr.quote_id = q.id
           left join evidence.source_record sr on sr.id = qr.origin_source_record_id
          where op.organization_id = o.id and op.kind = 'sale' and qr.status = 'sent'
            -- A sent PDF recorded from an email; a Cotizador revision R16 marks sent is not read here.
            and qr.origin = 'historical_import'
            and qr.created_at <= now() - make_interval(days => %(days)s)
            and coalesce(op.close_reason, '') <> 'discarded_by_correction'
          order by qr.created_at, qr.id), '{}') as files
  from crm.organization o
 where o.confirmation = 'machine_proposed' and o.status = 'active' and o.merged_into_organization_id is null
"""


def _direction(payload: dict[str, Any], comms_direction: str | None) -> str:
    return mail_direction(payload.get("sender"), payload.get("direction_hint"), comms_direction)


def read_snapshot(cur: Any, *, settle_days: int = SETTLE_DAYS) -> tuple[Snapshot, dict[str, dict[str, Any]]]:
    """The snapshot the rules plan from, and per-email display facts for the preview."""
    def rows() -> list[dict[str, Any]]:
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    cur.execute(_LINKS_SQL)
    links = rows()
    linked_cases: dict[str, list[str]] = {}
    case_threads: dict[str, set[str]] = {}
    case_people: dict[str, set[str]] = {}
    for link in links:
        linked_cases.setdefault(link["source_record_id"], []).append(link["opportunity_id"])
        if link["thread_id"]:
            case_threads.setdefault(link["opportunity_id"], set()).add(link["thread_id"])
        for address in addresses([link.get("sender")]):
            if domain_of(address) not in OWN_DOMAINS:
                case_people.setdefault(link["opportunity_id"], set()).add(address)

    cur.execute(_CAMPAIGN_THREADS_SQL)
    campaign_threads = {r[0] for r in cur.fetchall()}

    cur.execute(_EVIDENCE_SQL)
    evidence: list[MailEvidence] = []
    display: dict[str, dict[str, Any]] = {}
    for r in rows():
        p = _json(r["payload"]) or {}
        thread = p.get("gmail_thread_id") or r["provider_thread_id"]
        direction = _direction(p, r["direction"])
        docs = tuple(
            MailDocument(filename=d.get("filename"), sha256=d.get("sha256"),
                         cn_tokens=tuple(d.get("cn_tokens") or ()))
            for d in (p.get("documents") or []) if isinstance(d, dict)
        )
        requester = _json(r.get("requester_match")) or {}
        requester_matched = requester.get("status") == "matched"
        evidence.append(MailEvidence(
            id=r["id"], thread_id=thread, direction=direction, sender=p.get("sender"),
            recipients=(p.get("recipients") or "",), subject=p.get("subject_raw"),
            sent_at=p.get("sent_at"), documents=docs,
            linked_case_ids=tuple(sorted(set(linked_cases.get(r["id"], [])))),
            campaign_thread=bool(thread and thread in campaign_threads),
            direction_hint=p.get("direction_hint"),
            requester_checked=bool(requester),
            requester_organization_id=(requester.get("organization_id") if requester_matched else None),
            requester_organization_name=(requester.get("organization_name") if requester_matched else None),
            body_quote_tokens=tuple(str(t) for t in ((_json(r.get("quote_refs")) or {}).get("tokens") or ())),
            model_intent=r.get("model_intent"),
            case_match=_json(r.get("case_match")) or None,
            extra_headers=({"x-origenlab-origin": WEB_QUOTE_ORIGIN} if r.get("is_web_form") else {}),
            reply_to=(r.get("reply_to") if r.get("is_web_form") else None),
        ))
        display[r["id"]] = {"subject": p.get("subject_raw"), "sent_at": p.get("sent_at"),
                            "direction": direction}

    cur.execute(_QUOTES_SQL)
    quotes: dict[str, dict[str, Any]] = {}
    for r in rows():
        q = quotes.setdefault(r["quote_id"], {"opportunity_id": r["opportunity_id"],
                                              "number": r["quote_number"], "revisions": []})
        if r["revision_id"]:
            q["revisions"].append(QuoteRevision(id=r["revision_id"], revision_no=int(r["revision_no"]),
                                                status=r["status"], version=int(r["version"]),
                                                superseded=bool(r["superseded"]),
                                                sent_at=r["sent_at"].isoformat() if r["sent_at"] else None,
                                                origin=r["origin"] or "",
                                                approved_at=r["approved_at"].isoformat() if r["approved_at"] else None))
    by_case: dict[str, list[CaseQuote]] = {}
    for qid, q in quotes.items():
        by_case.setdefault(q["opportunity_id"], []).append(
            CaseQuote(id=qid, quote_number=q["number"], revisions=tuple(q["revisions"])))

    cur.execute(_CASES_SQL)
    cases = tuple(
        CaseState(id=r["id"], title=r["title"], stage=r["stage"], version=int(r["version"]),
                  organization_id=r["organization_id"], closed=bool(r["closed"]),
                  thread_ids=tuple(sorted(case_threads.get(r["id"], ()))),
                  quotes=tuple(by_case.get(r["id"], ())),
                  people=tuple(sorted(case_people.get(r["id"], ()))),
                  paused_until=r["paused_until"].isoformat() if r["paused_until"] else None)
        for r in rows()
    )

    cur.execute(_ORGANIZATIONS_SQL)
    organizations = tuple(
        Organization(id=r["id"], name=r["name"], version=int(r["version"]), confirmation=r["confirmation"],
                     domains=tuple(r["domains"] or ()),
                     is_supplier=bool(r["is_supplier"]) or r["kind"] in ("supplier", "manufacturer"))
        for r in rows()
    )

    # The customer of a forwarded request is a person of the case it opened or joined (R13): the
    # request's own email came from Tatiana, so the sender of the linked copy is never the customer.
    cur.execute(_FORWARD_PEOPLE_SQL)
    by_case = {c.id: c for c in cases}
    extra: dict[str, set[str]] = {}
    for case_id, email in cur.fetchall():
        if case_id in by_case and email:
            extra.setdefault(case_id, set()).add(email.strip().lower())
    if extra:
        cases = tuple(replace(c, people=tuple(sorted(set(c.people) | extra.get(c.id, set())))) for c in cases)

    cur.execute(_APPLIED_EVIDENCE_SQL, (APPLY_COMMAND,))
    pairs = frozenset((r[0], r[1]) for r in cur.fetchall() if r[0])
    applied = frozenset(e for e, _ in pairs)

    # R14: the window of sent mail it may record (Operaciones lists the same 60 days).
    cur.execute("select now() - make_interval(days => %s)", (LATE_QUOTE_WINDOW_DAYS,))
    since = cur.fetchone()[0]
    # R17: the window of client replies it may act on.
    cur.execute("select now() - make_interval(days => %s)", (REPLY_WINDOW_DAYS,))
    reply_since = cur.fetchone()[0]

    # R15: institutions «por confirmar» and their settled quotes.
    cur.execute(_INSTITUTIONS_SQL, {"days": int(settle_days)})
    institutions = tuple(InstitutionFacts(organization_id=oid, created_by=created_by,
                                          settled_quote_files=tuple(f or None for f in (files or ())))
                         for oid, created_by, files in cur.fetchall())
    names = {o.id: o.name for o in organizations}
    for inst in institutions:
        display[inst.organization_id] = {"subject": f"Institución por confirmar: {names.get(inst.organization_id, '')}",
                                         "sent_at": None, "direction": None}

    # R11: the pending forwarded requests, their copies merged as «Hoy» shows them.
    cur.execute(forwarded_requests.READ_SQL)
    forwards = []
    for g in forwarded_requests.group_forwards(rows()):
        c = g["customer"]
        forwards.append(ForwardRequest(id=g["id"], ids=tuple(g["ids"]), customer_email=c.get("email"),
                                       customer_name=c.get("name"), company=c.get("company"),
                                       requested_item=g.get("requested_item"), origins=tuple(g.get("origins") or ())))
        display[g["id"]] = {"subject": f"Reenviado: {g.get('requested_item') or c.get('email') or ''}",
                            "sent_at": g.get("received_at"), "direction": "inbound"}
    return Snapshot(evidence=tuple(evidence), cases=cases, organizations=organizations,
                    applied_evidence_ids=applied, forwards=tuple(forwards), applied_pairs=pairs,
                    institutions=institutions, late_quote_since=since.isoformat() if since else None,
                    reply_since=reply_since.isoformat() if reply_since else None), display


# ─────────────────────────────────────────────────────────────── the repository ──


class MailRulesRepository(V2CaseCommandRepository):
    """Preview, apply and undo. The case handlers are inherited; the quote and authoring
    handlers are borrowed from their own repositories and run on this transaction's cursor."""

    def __init__(self, connect: Any, dsn: str, statement_timeout_ms: int = DEFAULT_COMMAND_TIMEOUT_MS,
                 *, commands_enabled: bool = False, settle_days: int = SETTLE_DAYS) -> None:
        super().__init__(connect, dsn, statement_timeout_ms)
        self._quotes = V2QuoteImportRepository(connect, dsn, statement_timeout_ms)
        self._authoring = V2CrmAuthoringRepository(connect, dsn, statement_timeout_ms)
        self.commands_enabled = commands_enabled
        #: R15's wait after a quote is recorded (`mail_rules.SETTLE_DAYS`); a test may shorten it.
        self.settle_days = int(settle_days)

    @contextmanager
    def _read(self) -> Iterator[Any]:
        with self._connect(self._dsn, autocommit=False) as conn:
            with conn.cursor() as cur:
                cur.execute("set transaction read only")
                cur.execute(f"set local statement_timeout = {int(self._statement_timeout_ms) * 4}")
                try:
                    yield cur
                finally:
                    conn.rollback()

    # ------------------------------------------------------------------ preview

    def plan(self) -> tuple[list[PlannedAction], dict[str, dict[str, Any]], Snapshot]:
        with self._read() as cur:
            snapshot, display = read_snapshot(cur, settle_days=self.settle_days)
        return plan(snapshot), display, snapshot

    def preview(self) -> dict[str, Any]:
        actions, display, snapshot = self.plan()
        orgs = {o.id: o.name for o in snapshot.organizations}
        items = []
        counts: dict[str, dict[str, int]] = {}
        for a in actions:
            row = {**a.as_dict(), **display.get(a.evidence_id, {})}
            if row["organization_id"] and not row["organization_name"]:
                row["organization_name"] = orgs.get(row["organization_id"])
            items.append(row)
            counts.setdefault(a.rule_id, {}).setdefault(a.mode, 0)
            counts[a.rule_id][a.mode] += 1
        return {
            "label": SYSTEM_LABEL,
            "commands_enabled": self.commands_enabled,
            "emails_considered": len(snapshot.evidence),
            "already_applied": len(snapshot.applied_evidence_ids),
            "counts": counts,
            "actions": items,
            "applied": self.applied(),
        }

    def applied(self) -> list[dict[str, Any]]:
        with self._read() as cur:
            cur.execute(
                """
                select r.id::text as receipt_id, r.completed_at, r.response_body,
                       o.display_name as applied_by,
                       u.id::text as undo_receipt_id, u.completed_at as undone_at,
                       uo.display_name as undone_by, u.response_body ->> 'note' as undo_note
                  from platform.command_receipt r
                  join platform.operator o on o.id = r.operator_id
                  left join platform.command_receipt u
                         on u.command_name = %s and u.status = 'completed'
                        and u.response_body ->> 'undoes_receipt_id' = r.id::text
                  left join platform.operator uo on uo.id = u.operator_id
                 where r.command_name = %s and r.status = 'completed'
                 order by r.completed_at desc
                 limit %s
                """,
                (UNDO_COMMAND, APPLY_COMMAND, APPLIED_LIST_LIMIT),
            )
            cols = [d[0] for d in cur.description]
            out = []
            for row in cur.fetchall():
                r = dict(zip(cols, row, strict=True))
                body = _json(r["response_body"]) or {}
                out.append({
                    "receipt_id": r["receipt_id"],
                    "applied_at": r["completed_at"].isoformat() if r["completed_at"] else None,
                    "applied_by": r["applied_by"],
                    "label": SYSTEM_LABEL,
                    **{k: body.get(k) for k in ("evidence_id", "rule_id", "reasons", "case_id", "case_title",
                                                "organization_id", "organization_name", "quote_number")},
                    "automatic": body.get("trigger") == "automatic",
                    "undone": r["undo_receipt_id"] is not None,
                    "undone_at": r["undone_at"].isoformat() if r["undone_at"] else None,
                    "undone_by": r["undone_by"],
                    "undo_note": r["undo_note"],
                })
            return out

    # ------------------------------------------------------------------ «Hoy»: what the system did by itself

    def recent_automatic(self, hours: int = 48, limit: int = 50) -> list[dict[str, Any]]:
        """The automatic run's actions of the last `hours`, not undone, newest first — «Hecho
        automáticamente» on Hoy, each with what it acted on (a subject or a forwarded request) and
        whether it opened a case or filed the email in one."""
        with self._read() as cur:
            cur.execute(_RECENT_AUTOMATIC_SQL, (APPLY_COMMAND, max(1, min(int(hours), 24 * 14)), UNDO_COMMAND,
                                                max(1, min(int(limit), 200))))
            cols = [d[0] for d in cur.description]
            out = []
            for row in cur.fetchall():
                r = dict(zip(cols, row, strict=True))
                body = _json(r["response_body"]) or {}
                if body.get("rule_id") in INSTITUTION_RULES:
                    continue  # an institution, not an email: Operaciones lists it, «Hoy» does not
                created = body.get("created") or {}
                opened = body.get("rule_id") in OPENING_RULES or bool(created.get("opportunity_id"))
                payload = _json(r["payload"]) or {}
                what = (payload.get("subject_raw") if r["kind"] == "gmail_message" else
                        " · ".join(x for x in (payload.get("requested_item"), payload.get("original_name")
                                               or payload.get("original_email")) if x))
                out.append({
                    "receipt_id": r["receipt_id"],
                    "applied_at": r["completed_at"].isoformat() if r["completed_at"] else None,
                    "rule_id": body.get("rule_id"), "reasons": body.get("reasons") or [],
                    "case_id": body.get("case_id"), "case_title": body.get("case_title"),
                    "quote_number": body.get("quote_number"), "kind": "opened" if opened else "filed",
                    "what": what or None, "source": "forward" if r["kind"] == "forwarded_request" else "email",
                })
            return out

    def remove_automatic(self, operator: OperatorIdentity, receipt_id: str) -> dict[str, Any]:
        """«Quitar» on Hoy (sales or admin): undo one action the automatic run took. A person's
        «Aplicar» stays an admin's to undo, in Historial."""
        receipt_id = as_uuid(receipt_id, "receipt_id")
        with self._read() as cur:
            cur.execute("select response_body ->> 'trigger' from platform.command_receipt "
                        "where id = %s and command_name = %s and status = 'completed'", (receipt_id, APPLY_COMMAND))
            row = cur.fetchone()
        if row is None:
            raise CommandRefused(404, "applied_action_not_found", "no applied email action with that receipt")
        if row[0] != "automatic":
            raise CommandRefused(403, "not_automatic", "only what the system did by itself is removed from Hoy")
        return self.undo(operator, receipt_id, REMOVED_FROM_HOY)

    # ------------------------------------------------------------------ apply

    def apply(self, operator: OperatorIdentity, pairs: list[dict[str, str]]) -> dict[str, Any]:
        """Apply the previewed `(evidence_id, rule_id)` pairs — at most `APPLY_BATCH` per call — and
        only those whose fresh re-plan still says the same rule in `auto` mode. Anything else is
        refused as `plan_changed`; an email that arrived after the preview is never applied."""
        if not pairs:
            raise CommandRefused(422, "nothing_to_apply", "name the previewed actions to apply")
        if len(pairs) > APPLY_BATCH:
            raise CommandRefused(422, "batch_too_large", f"at most {APPLY_BATCH} actions per call")
        actions, _display, _snapshot = self.plan()
        # One email may carry two actions (R16 beside a proposal): the pair names which.
        by_pair = {(a.evidence_id, a.rule_id): a for a in actions}
        by_evidence = {a.evidence_id: a for a in actions}
        applied: list[dict[str, Any]] = []
        refused: list[dict[str, Any]] = []
        for pair in pairs:
            evidence_id, rule_id = str(pair["evidence_id"]), str(pair["rule_id"])
            action = by_pair.get((evidence_id, rule_id)) or by_evidence.get(evidence_id)
            if action is None or action.rule_id != rule_id or action.mode != AUTO:
                now = "nada" if action is None else f"{action.rule_id} ({action.mode})"
                refused.append({"evidence_id": evidence_id, "rule_id": rule_id, "code": "plan_changed",
                                "message": f"las reglas ya no proponen {rule_id} automática para este correo; ahora: {now}"})
                continue
            outcome = self.apply_planned(operator, action)
            (refused if "code" in outcome else applied).append(outcome)
        return {"applied": applied, "refused": refused}

    def apply_planned(self, operator: OperatorIdentity, action: PlannedAction, *,
                      automatic: bool = False) -> dict[str, Any]:
        """Apply one action the rules just planned: its response, or a refusal carrying `code`.
        «Aplicar» and the automatic run (`mail_rules_auto.py`) both come through here."""
        try:
            return self._apply_action(operator, action, automatic=automatic)
        except CommandRefused as exc:
            return {"evidence_id": action.evidence_id, "rule_id": action.rule_id,
                    "code": exc.code, "message": exc.message}
        except Exception as exc:  # a constraint the handlers did not anticipate
            if not _is_database_error(exc):
                raise
            return {"evidence_id": action.evidence_id, "rule_id": action.rule_id,
                    "code": "database_refused",
                    "message": str(exc).splitlines()[0] if str(exc) else type(exc).__name__}

    def _apply_action(self, operator: OperatorIdentity, action: PlannedAction, *,
                      automatic: bool = False) -> dict[str, Any]:
        key = f"mail-rule:{action.evidence_id}:{action.rule_id}"
        digest = _digest({"evidence_id": action.evidence_id, "rule_id": action.rule_id})
        trigger = "automatic" if automatic else "manual"
        system = replace(operator, acts_as_system=True, event_attribution={
            "source": "mail_rules", "label": SYSTEM_LABEL, "rule_id": action.rule_id,
            "evidence_id": action.evidence_id, "reasons": list(action.reasons),
            "applied_by_operator_id": operator.operator_id, "trigger": trigger,
        })
        with self._write() as cur:
            # One apply per email, whoever presses: the lock serialises two concurrent runs, and the
            # second then sees the first's receipt.
            cur.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"mail-rule:{action.evidence_id}",))
            if action.rule_id in AFTER_APPLY_RULES:
                # R14 acts on an email another rule already acted on: only its own receipt counts.
                cur.execute(
                    "select operator_id::text, idempotency_key from platform.command_receipt "
                    "where command_name = %s and status = 'completed' and response_body ->> 'evidence_id' = %s "
                    "and response_body ->> 'rule_id' = %s",
                    (APPLY_COMMAND, action.evidence_id, action.rule_id),
                )
            else:
                # Any earlier receipt on this email (R14's included) refuses a stale «Aplicar».
                cur.execute(
                    "select operator_id::text, idempotency_key from platform.command_receipt "
                    "where command_name = %s and status = 'completed' and response_body ->> 'evidence_id' = %s",
                    (APPLY_COMMAND, action.evidence_id),
                )
            done = cur.fetchone()
            if done is not None and (done[0] != operator.operator_id or done[1] != key):
                raise CommandRefused(409, "already_applied", "another run already acted on this email")
            receipt_id, replay = self._claim_receipt(cur, operator, key, APPLY_COMMAND, digest)
            if replay is not None:
                return replay
            ctx: dict[str, Any] = dict(action.context)
            if "case" in ctx:
                # The snapshot holds sales cases only; a tender named here is refused all the same.
                refuse_tender(cur, ctx["case"])
                self._refresh_case(cur, ctx)
            results: list[dict[str, Any]] = []
            created: dict[str, Any] = {"opportunity_evidence_ids": []}
            for step in action.commands:
                inputs = {k: (ctx[v[1:]] if isinstance(v, str) and v.startswith("@") else v)
                          for k, v in step["inputs"].items()}
                result = self._run_step(cur, system, step["command"], inputs, receipt_id)
                _absorb(ctx, created, step["command"], result)
                if step["command"] == UPDATE_ORGANIZATION:
                    # R15 renamed it: undo gives the old name back while the new one still stands.
                    created["previous_name"] = ctx.get("previous_name")
                    created["renamed_to"] = inputs.get("name")
                results.append({"command": step["command"],
                                **{k: result.get(k) for k in _RESULT_KEYS if k in result}})
            response = {
                "evidence_id": action.evidence_id,
                "rule_id": action.rule_id,
                "reasons": list(action.reasons),
                "case_id": ctx.get("case"),
                "case_title": action.case_title,
                "organization_id": action.organization_id or ctx.get("organization"),
                "organization_name": action.organization_name,
                "quote_number": action.quote_number,
                "from_stage": ctx.get("from_stage"),
                "created": created,
                "steps": results,
                "trigger": trigger,
                "idempotency_key": key,
                "command_receipt_id": receipt_id,
                "replayed": False,
            }
            self._complete_receipt(cur, receipt_id, response)
            return response

    def _refresh_case(self, cur: Any, ctx: dict[str, Any]) -> None:
        """The case as it is now, locked. The rule read it a moment ago; if its stage changed since,
        the reason the rule acted no longer holds and the action is refused. Its version is
        taken as current: the compare-and-set that protects a person's decision protects
        nothing here, because the machine was shown nothing."""
        cur.execute("select stage, version from crm.opportunity where id = %s for update", (ctx["case"],))
        row = cur.fetchone()
        if row is None:
            raise CommandRefused(404, "case_not_found", "the case the rule matched no longer exists")
        stage, version = row
        if ctx.get("from_stage") is not None and stage != ctx["from_stage"]:
            raise CommandRefused(409, "case_moved", f"the case moved to '{stage}' since the rules read it")
        ctx["case_version"] = int(version)

    # ------------------------------------------------------------------ undo

    def undo(self, operator: OperatorIdentity, receipt_id: str, note: str) -> dict[str, Any]:
        receipt_id = as_uuid(receipt_id, "receipt_id")
        note = (note or "").strip()
        if not note:
            raise CommandRefused(422, "note_required", "say why the action is undone")
        key = f"mail-rule-undo:{receipt_id}"
        digest = _digest({"undoes_receipt_id": receipt_id})
        with self._write() as cur:
            cur.execute(
                "select response_body from platform.command_receipt "
                "where id = %s and command_name = %s and status = 'completed'",
                (receipt_id, APPLY_COMMAND),
            )
            row = cur.fetchone()
            if row is None:
                raise CommandRefused(404, "applied_action_not_found", "no applied email action with that receipt")
            action = _json(row[0]) or {}
            own, replay = self._claim_receipt(cur, operator, key, UNDO_COMMAND, digest)
            if replay is not None:
                return replay
            cur.execute(
                "select 1 from platform.command_receipt where command_name = %s and status = 'completed' "
                "and response_body ->> 'undoes_receipt_id' = %s",
                (UNDO_COMMAND, receipt_id),
            )
            if cur.fetchone() is not None:
                raise CommandRefused(409, "already_undone", "that action was already undone")
            person = replace(operator, event_attribution={
                "source": "mail_rules_undo", "undoes_receipt_id": receipt_id,
                "rule_id": action.get("rule_id"), "evidence_id": action.get("evidence_id"),
            })
            try:
                steps, kept = self._undo_steps(cur, person, action, note, own)
            except CommandRefused:
                raise
            except Exception as exc:
                if not _is_database_error(exc):
                    raise
                raise CommandRefused(409, "undo_refused_by_database",
                                     str(exc).splitlines()[0] if str(exc) else type(exc).__name__) from exc
            response = {
                "undoes_receipt_id": receipt_id,
                "rule_id": action.get("rule_id"),
                "evidence_id": action.get("evidence_id"),
                "case_id": action.get("case_id"),
                "note": note,
                "steps": steps,
                "organization_kept": kept,
                "idempotency_key": key,
                "command_receipt_id": own,
                "replayed": False,
            }
            self._complete_receipt(cur, own, response)
            return response

    def _undo_steps(self, cur: Any, operator: OperatorIdentity, action: dict[str, Any], note: str,
                    receipt_id: str) -> tuple[list[dict[str, Any]], str | None]:
        rule = action.get("rule_id")
        if rule == "R16":
            # An issued Cotizador revision never goes back to approved (`quote_revision_authored_frozen`),
            # and the email did carry its PDF. A quote that should not stand is voided in the Cotizador.
            raise CommandRefused(409, "sent_quote_is_final",
                                 "la cotización ya consta como enviada; si no correspondía, anúlala en el Cotizador")
        kept: str | None = None
        created = action.get("created") or {}
        case_id = action.get("case_id")
        done: list[dict[str, Any]] = []

        def run(command: str, inputs: dict[str, Any]) -> None:
            result = self._run_step(cur, operator, command, inputs, receipt_id)
            done.append({"command": command, **{k: result.get(k) for k in _RESULT_KEYS if k in result}})

        if rule == "R17":
            # Back to «Enviada» — unless a person moved the case since; then there is nothing of ours to undo.
            cur.execute("select stage, version from crm.opportunity where id = %s for update", (case_id,))
            row = cur.fetchone()
            if row is not None and row[0] == "negotiating":
                run(ADVANCE_CASE_STAGE, {"opportunity_id": case_id, "opportunity_version": int(row[1]),
                                         "stage": "quoting", "note": note})
        if rule in ("R5", "R6"):
            came_from = "negotiating" if rule == "R5" else action.get("from_stage")
            run(CORRECT_CASE_STAGE, {"opportunity_id": case_id, "to_stage": came_from, "note": note})
            if rule == "R5" and action.get("from_stage") == "quoting":
                run(ADVANCE_CASE_STAGE, {"opportunity_id": case_id, "opportunity_version": self._case_version(cur, case_id),
                                         "stage": "quoting", "note": note})
        if rule == "R15":
            # Back to «por confirmar», and to its old name unless a person renamed it since.
            org_id = action.get("organization_id")
            cur.execute("select version, name, confirmation from crm.organization where id = %s", (org_id,))
            org = cur.fetchone()
            if org is not None and org[2] == "confirmed":
                inputs: dict[str, Any] = {"organization_id": org_id, "expected_version": int(org[0]),
                                          "previous_name": None, "note": note}
                if created.get("previous_name") and org[1] == created.get("renamed_to"):
                    inputs["previous_name"] = created["previous_name"]
                run(UNCONFIRM_ORGANIZATION_RECORD, inputs)
        if rule in ("R1", "R9", "R13", "R14") and created.get("quote_revision_id"):
            # R1 can now recognize a new sent CN PDF on an existing thread. Undo the quotes first,
            # then walk the ordinary reversible stage edges back to where the case was before the
            # email action. No privileged stage correction is needed for these open stages.
            for revision_id in created.get("quote_revision_ids") or [created["quote_revision_id"]]:
                cur.execute("select version, status from crm.quote_revision where id = %s", (revision_id,))
                rev = cur.fetchone()
                if rev is not None and rev[1] == "sent":
                    run(VOID_HISTORICAL_QUOTE_REVISION, {"quote_revision_id": revision_id,
                                                        "quote_revision_version": int(rev[0]), "note": note})
            rollback = {
                "lead": ("qualified", "qualifying", "lead"),
                "qualifying": ("qualified", "qualifying"),
                "qualified": ("qualified",),
                "quoting": (),
                "negotiating": (),
            }.get(action.get("from_stage"), ())
            for stage in rollback:
                run(ADVANCE_CASE_STAGE, {
                    "opportunity_id": case_id,
                    "opportunity_version": self._case_version(cur, case_id),
                    "stage": stage,
                    "note": note,
                })
        if rule in ("R1", "R2", "R5", "R6", "R9", "R12", "R13"):
            for link_id in created.get("opportunity_evidence_ids") or []:
                run(UNLINK_CASE_EVIDENCE, {"opportunity_evidence_id": link_id, "reason": note, "note": note})
        if rule == "R11":
            forwards = created.get("forwarded_request_ids") or []
            if created.get("opportunity_id"):
                # A request R11 opened: it was not one. Closed as a correction, never reopened.
                run(ADVANCE_CASE_STAGE, {"opportunity_id": case_id, "opportunity_version": self._case_version(cur, case_id),
                                         "stage": "abandoned", "close_reason": DISCARDED_BY_CORRECTION, "note": note})
                if created.get("organization_id"):
                    kept = self._retire_mail_organization(cur, created["organization_id"], action, note, run)
                forwarded_requests.set_forward_status(cur, forwards, "rejected")
                done.append({"command": "forwarded_request_rejected", "forwarded_request_ids": forwards})
            else:
                # It joined the wrong case: unlinked, and back in «Solicitudes reenviadas» for a person.
                for link_id in created.get("opportunity_evidence_ids") or []:
                    run(UNLINK_CASE_EVIDENCE, {"opportunity_evidence_id": link_id, "reason": note, "note": note})
                forwarded_requests.set_forward_status(cur, forwards, "pending")
                done.append({"command": "forwarded_request_pending", "forwarded_request_ids": forwards})
        if rule in ("R3", "R4", "R7", "R10"):
            if rule in ("R3", "R4", "R10") and created.get("quote_revision_id"):
                cur.execute("select version, status from crm.quote_revision where id = %s",
                            (created["quote_revision_id"],))
                rev = cur.fetchone()
                if rev is not None and rev[1] == "sent":
                    run(VOID_HISTORICAL_QUOTE_REVISION, {"quote_revision_id": created["quote_revision_id"],
                                                        "quote_revision_version": int(rev[0]), "note": note})
            run(ADVANCE_CASE_STAGE, {"opportunity_id": case_id, "opportunity_version": self._case_version(cur, case_id),
                                     "stage": "abandoned", "close_reason": DISCARDED_BY_CORRECTION, "note": note})
            org_id = created.get("organization_id")
            if rule in ("R4", "R10") and org_id:
                kept = self._retire_mail_organization(cur, org_id, action, note, run)
        if not done:
            raise CommandRefused(409, "nothing_to_undo", "this action left nothing that can be reversed")
        return done, kept

    def _retire_mail_organization(self, cur: Any, org_id: str, action: dict[str, Any], note: str,
                                  run: Callable[[str, dict[str, Any]], None]) -> str | None:
        """Archive the institution «por confirmar» R4 created, and remove the domain it added — unless
        a person confirmed it or another open case uses it. Returns why it was kept, or None."""
        cur.execute("select version, confirmation, status from crm.organization where id = %s", (org_id,))
        org = cur.fetchone()
        if org is None or org[2] != "active":
            return None
        if org[1] != "machine_proposed":
            return "la institución ya fue confirmada por una persona: se conserva"
        cur.execute(
            """
            select count(distinct o.id) from crm.opportunity_organization oo
              join crm.opportunity o on o.id = oo.opportunity_id
             where oo.organization_id = %s and oo.valid_to is null and o.closed_at is null
            """,
            (org_id,),
        )
        others = int(cur.fetchone()[0])
        if others:
            return f"la institución está en {others} caso(s) abierto(s): se conserva, con su dominio"
        version = int(org[0])
        cur.execute(
            "select id::text from crm.organization_domain where organization_id = %s and removed_at is null "
            "and origin_source_record_id = %s",
            (org_id, action.get("evidence_id")),
        )
        for (domain_id,) in cur.fetchall():
            run(REMOVE_ORGANIZATION_DOMAIN, {"organization_id": org_id, "expected_version": version,
                                             "domain_id": domain_id, "note": note})
            version += 1
        run(ARCHIVE_ORGANIZATION, {"organization_id": org_id, "expected_version": version, "note": note})
        return None

    def _case_version(self, cur: Any, case_id: str) -> int:
        cur.execute("select version from crm.opportunity where id = %s", (case_id,))
        row = cur.fetchone()
        if row is None:
            raise CommandRefused(404, "case_not_found", "no such commercial case")
        return int(row[0])

    # ------------------------------------------------------------------ the step dispatch

    def _run_step(self, cur: Any, operator: OperatorIdentity, command: str, inputs: dict[str, Any],
                  receipt_id: str) -> dict[str, Any]:
        """One step through the handler that owns it, on this transaction's cursor."""
        if command in _CASE_BODIES:
            fields = validated_case(command, _CASE_BODIES[command](**inputs))
            return self._HANDLERS[command](self, cur, operator, fields, receipt_id)
        if command in _QUOTE_BODIES:
            fields = validated_quote_import(command, _QUOTE_BODIES[command](**inputs))
            return self._quotes._HANDLERS[command](self._quotes, cur, operator, fields, receipt_id)
        if command in (ARCHIVE_ORGANIZATION, REMOVE_ORGANIZATION_DOMAIN, UPDATE_ORGANIZATION,
                       CONFIRM_ORGANIZATION_RECORD, UNCONFIRM_ORGANIZATION_RECORD):
            return self._authoring._HANDLERS[command](self._authoring, cur, operator, dict(inputs), receipt_id)
        local: dict[str, Callable[..., dict[str, Any]]] = {
            REGISTER_MAIL_ORGANIZATION: MailRulesRepository._register_mail_organization,
            RECORD_CASE_WON: record_case_won,
            UNLINK_CASE_EVIDENCE: MailRulesRepository._unlink_case_evidence,
            CORRECT_CASE_STAGE: MailRulesRepository._correct_case_stage,
            LINK_AFTER_CLOSE: MailRulesRepository._link_after_close,
            ATTACH_FORWARD: forwarded_requests.attach_forward_to_case,
            CREATE_REQUEST_FROM_FORWARD_STEP: forwarded_requests.create_request_from_forward,
            RECORD_AUTHORED_QUOTE_SENT: record_authored_quote_sent,
            ADD_CASE_SENDER_PARTICIPANT: MailRulesRepository._add_case_sender_participant_step,
        }
        if command not in local:
            raise CommandRefused(500, "unknown_step", f"no handler for step '{command}'")
        return local[command](self, cur, operator, dict(inputs), receipt_id)

    # ------------------------------------------------------------------ the three new handlers

    def _add_case_sender_participant_step(self, cur: Any, operator: OperatorIdentity,
                                           fields: dict[str, Any], receipt_id: str) -> dict[str, Any]:
        """R7 step: wire the sender as a ``quote_recipient`` participant of the new request case.
        (owner, 2026-10-22)"""
        return self._add_case_sender_participant(
            cur, operator, receipt_id,
            email=str(fields.get("email") or ""),
            case_id=str(fields["opportunity_id"]),
            note=str(fields.get("note") or ""),
        )

    def _register_mail_organization(self, cur: Any, operator: OperatorIdentity, fields: dict[str, Any],
                                    receipt_id: str) -> dict[str, Any]:
        return register_machine_organization(self, cur, operator, fields, receipt_id)

    def _link_after_close(self, cur: Any, operator: OperatorIdentity, fields: dict[str, Any],
                          receipt_id: str) -> dict[str, Any]:
        """R2 on a **won** case: the email is filed and the case stays closed (owner, 2026-10-14 — a
        payment, an order or a delivery note is still that sale's). `link_case_evidence` refuses a
        closed case for a person; the machine may add this one kind of evidence, never edit."""
        case_id = as_uuid(fields["opportunity_id"], "opportunity_id")
        cur.execute("select stage, version from crm.opportunity where id = %s for update", (case_id,))
        row = cur.fetchone()
        if row is None:
            raise CommandRefused(404, "case_not_found", "no such commercial case")
        if row[0] != "won":
            raise CommandRefused(409, "case_not_won", "only a won case takes mail after it closed")
        evidence_id, event_id = self._link_evidence_row(
            cur, case_id=case_id, subject_column="source_record_id",
            subject_id=as_uuid(fields["source_record_id"], "source_record_id"), relation=fields["relation"],
            operator=operator, receipt_id=receipt_id, note=fields["note"])
        return {"command": LINK_AFTER_CLOSE, "opportunity_id": case_id, "opportunity_version": int(row[1]),
                "opportunity_evidence_id": evidence_id, "stage": row[0], "event_ids": [event_id]}

    def _unlink_case_evidence(self, cur: Any, operator: OperatorIdentity, fields: dict[str, Any],
                              receipt_id: str) -> dict[str, Any]:
        """The inverse of `link_case_evidence`: `unlinked_at` and its reason, once. The row stays."""
        link_id = as_uuid(fields["opportunity_evidence_id"], "opportunity_evidence_id")
        cur.execute(
            """
            update crm.opportunity_evidence
               set unlinked_at = now(), unlink_reason = %s
             where id = %s and unlinked_at is null
            returning opportunity_id::text as opportunity_id, relation,
                      source_record_id::text as source_record_id
            """,
            (fields["reason"], link_id),
        )
        row = self._row(cur)
        if row is None:
            raise CommandRefused(409, "evidence_link_not_current", "that evidence link is already unlinked or absent")
        event = self._append_event(
            cur, aggregate_kind="opportunity_evidence", aggregate_id=link_id, event_type="case_evidence.unlinked",
            payload={**row, "unlink_reason": fields["reason"], "note": fields["note"]},
            operator=operator, receipt_id=receipt_id,
        )
        return {"command": UNLINK_CASE_EVIDENCE, "opportunity_evidence_id": link_id,
                "opportunity_id": row["opportunity_id"], "event_ids": [event]}

    def _correct_case_stage(self, cur: Any, operator: OperatorIdentity, fields: dict[str, Any],
                            receipt_id: str) -> dict[str, Any]:
        """`won`/`lost` back to the stage it came from — the one exception the stage guard knows
        (migration 20261005120000). The won pair and the close facts are cleared with it."""
        case_id = as_uuid(fields["opportunity_id"], "opportunity_id")
        cur.execute("select stage, version from crm.opportunity where id = %s for update", (case_id,))
        row = cur.fetchone()
        if row is None:
            raise CommandRefused(404, "case_not_found", "no such commercial case")
        stage, version = row
        if stage not in ("won", "lost"):
            raise CommandRefused(409, "case_not_correctable",
                                 f"the case is at '{stage}'; only a won or lost stage is corrected back")
        # An undo takes back only what the rules did. Since 2026-10-10 the stage guard lets any lost
        # case reopen, so it no longer refuses this for us: if a person set the stage after the
        # rule did, that decision stands and the correction is refused here, by name.
        cur.execute(
            """
            select actor_kind from crm.domain_event
             where aggregate_kind = 'opportunity' and aggregate_id = %s
               and event_type = 'opportunity.staged' and payload ->> 'to_stage' = %s
             order by seq desc limit 1
            """,
            (case_id, stage),
        )
        latest = cur.fetchone()
        if latest is None or latest[0] != "worker":
            raise CommandRefused(409, "stage_correction_refused",
                                 "a person set this stage after the email rules did; their decision stands")
        cur.execute("select set_config('origenlab.case_stage_correction', %s, true)", (case_id,))
        try:
            cur.execute(
                """
                update crm.opportunity
                   set stage = %s, closed_at = null, close_reason = null,
                       won_quote_id = null, won_revision_no = null,
                       version = version + 1, updated_at = now()
                 where id = %s and version = %s
                """,
                (fields["to_stage"], case_id, int(version)),
            )
        except Exception as exc:
            # The transaction is aborted now: nothing more runs on it, the refusal rolls it back.
            if not _is_database_error(exc):
                raise
            raise CommandRefused(409, "stage_correction_refused",
                                 str(exc).splitlines()[0] if str(exc) else type(exc).__name__) from exc
        cur.execute("select set_config('origenlab.case_stage_correction', '', true)")
        event = self._append_event(
            cur, aggregate_kind="opportunity", aggregate_id=case_id, event_type="opportunity.stage_corrected",
            payload={"from_stage": stage, "to_stage": fields["to_stage"], "note": fields["note"]},
            operator=operator, receipt_id=receipt_id,
        )
        return {"command": CORRECT_CASE_STAGE, "opportunity_id": case_id,
                "opportunity_version": int(version) + 1, "stage": fields["to_stage"],
                "previous_stage": stage, "event_ids": [event]}


_CASE_BODIES: dict[str, Any] = {
    OPEN_COMMERCIAL_CASE: OpenCommercialCaseBody,
    ADD_CASE_ORGANIZATION: AddCaseOrganizationBody,
    ADVANCE_CASE_STAGE: AdvanceCaseStageBody,
    LINK_CASE_EVIDENCE: LinkCaseEvidenceBody,
}
_QUOTE_BODIES: dict[str, Any] = {
    RECORD_HISTORICAL_QUOTATION: RecordHistoricalQuotationBody,
    VOID_HISTORICAL_QUOTE_REVISION: VoidHistoricalQuoteRevisionBody,
}
_RESULT_KEYS = ("opportunity_id", "opportunity_version", "opportunity_evidence_id", "organization_id",
                "organization_version", "quote_id", "quote_revision_id", "stage", "previous_stage")


def register_machine_organization(repo: Any, cur: Any, operator: OperatorIdentity, fields: dict[str, Any],
                                  receipt_id: str) -> dict[str, Any]:
    """An institution «por confirmar» (`machine_proposed`), named by its domain (R4) or by the
    name a quote PDF gives it (R10, no domain), with the email it came from. A person confirms,
    renames or archives it later."""
    domain = str(fields["domain"]).strip().lower() if fields.get("domain") else None
    if domain is not None:
        cur.execute(
            "select d.organization_id::text from crm.organization_domain d join crm.organization o on o.id = d.organization_id "
            "where d.domain_norm = %s and d.removed_at is null and o.status = 'active'",
            (domain,),
        )
        if cur.fetchone() is not None:
            raise CommandRefused(409, "domain_already_known", f"{domain} already belongs to an institution")
    else:
        cur.execute("select 1 from crm.organization where status = 'active' and lower(name) = lower(%s)",
                    (fields["name"],))
        if cur.fetchone() is not None:
            raise CommandRefused(409, "organization_name_already_known",
                                 f"{fields['name']} already names an institution")
    cur.execute(
        """
        insert into crm.organization (name, kind, confirmation, origin_source_record_id, note)
        values (%s, 'institution', 'machine_proposed', %s, %s)
        returning id::text as id, version
        """,
        (fields["name"], fields["origin_source_record_id"], fields["note"]),
    )
    org = repo._row(cur)
    assert org is not None  # noqa: S101
    events = [repo._append_event(
        cur, aggregate_kind="organization", aggregate_id=org["id"], event_type="organization.created",
        payload={"name": fields["name"], "kind": "institution", "confirmation": "machine_proposed",
                 "origin_source_record_id": fields["origin_source_record_id"], "note": fields["note"]},
        operator=operator, receipt_id=receipt_id,
    )]
    if domain is not None:
        cur.execute(
            """
            insert into crm.organization_domain (organization_id, domain_norm, scope, origin_source_record_id)
            values (%s, %s, 'shared', %s)
            """,
            (org["id"], domain, fields["origin_source_record_id"]),
        )
        events.append(repo._append_event(
            cur, aggregate_kind="organization", aggregate_id=org["id"], event_type="organization.domain_added",
            payload={"organization_id": org["id"], "domain_norm": domain, "scope": "shared"},
            operator=operator, receipt_id=receipt_id,
        ))
    return {"command": REGISTER_MAIL_ORGANIZATION, "organization_id": org["id"],
            "organization_version": int(org["version"]), "event_ids": events}


def _absorb(ctx: dict[str, Any], created: dict[str, Any], command: str, result: dict[str, Any]) -> None:
    if command in (UPDATE_ORGANIZATION, CONFIRM_ORGANIZATION_RECORD):
        # The authoring handlers answer `version`; the next step expects the institution's new one.
        ctx["organization_version"] = int(result["version"])
        if command == CONFIRM_ORGANIZATION_RECORD and not result.get("already_confirmed"):
            created["confirmed_organization_id"] = result["organization_id"]
        return
    if result.get("opportunity_id"):
        ctx["case"] = result["opportunity_id"]
    if result.get("opportunity_version") is not None:
        ctx["case_version"] = int(result["opportunity_version"])
    if command == REGISTER_MAIL_ORGANIZATION:
        ctx["organization"] = result["organization_id"]
        ctx["organization_version"] = result["organization_version"]
        created["organization_id"] = result["organization_id"]
    if command == OPEN_COMMERCIAL_CASE:
        created["opportunity_id"] = result["opportunity_id"]
    elif command in (LINK_CASE_EVIDENCE, LINK_AFTER_CLOSE):
        created["opportunity_evidence_ids"].append(result["opportunity_evidence_id"])
    if command in (ATTACH_FORWARD, CREATE_REQUEST_FROM_FORWARD_STEP):
        created["opportunity_evidence_ids"].extend(result.get("opportunity_evidence_ids") or [])
        created["forwarded_request_ids"] = list(result.get("forwarded_request_ids") or [])
    if command == CREATE_REQUEST_FROM_FORWARD_STEP:
        created["opportunity_id"] = result["opportunity_id"]
        if result.get("organization_created"):
            created["organization_id"] = result["organization_id"]
    if command == RECORD_AUTHORED_QUOTE_SENT:
        created.setdefault("sent_revision_ids", []).append(result["quote_revision_id"])
    if command == RECORD_HISTORICAL_QUOTATION:
        # One email may send several alternatives (R1): the first stays in the single keys older
        # receipts carry; every one is listed, so undo voids them all.
        created.setdefault("quote_revision_id", result["quote_revision_id"])
        created.setdefault("quote_id", result["quote_id"])
        created.setdefault("quote_revision_ids", []).append(result["quote_revision_id"])


def _is_database_error(exc: Exception) -> bool:
    try:
        import psycopg
    except ImportError:  # pragma: no cover
        return False
    return isinstance(exc, psycopg.Error)


__all__ = ["APPLY_COMMAND", "DISCARDED_BY_CORRECTION", "MailRulesRepository", "UNDO_COMMAND", "read_snapshot",
           "register_machine_organization"]
