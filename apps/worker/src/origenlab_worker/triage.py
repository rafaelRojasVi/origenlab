"""Mail triage of one captured message, end to end: read its `.eml` from Storage, run the rules,
look up product candidates in the catalog, ask the model only when the rules say a person wrote
something that matters, and record the reading as `evidence.assertion` proposals.

Writes, all `on conflict do nothing`, in one transaction per message:

* one `message_triage` assertion — `value_norm = triage:v<TRIAGE_VERSION>`, the whole reading in
  `value` (class, reasons, signals, candidates, the model's answer or why it did not run);
* one `product_mention` assertion per product named — by the model when it ran, otherwise by an
  exact model-number match in the catalog;
* for inbound mail from outside that a person wrote (not `people_reading.NO_PEOPLE_CLASSES`), one
  `message_triage` assertion `people:v1` (`people_reading.py`) — who wrote it: the sender's
  address, the name in its header, what its signature states (the model's `sender`, when the model
  ran), its delimited signature block and who else was on it. Isolated: a failure never breaks
  the triage;
* for mail a person wrote (`HUMAN_CLASSES`), one `message_triage` assertion
  `case_match:v<CASE_MATCH_VERSION>` — which case it probably belongs to (`case_match.py`), or that it
  looks like a deal with no case. A person decides; nothing is linked here.

The worker never writes `crm.*` or `outbound.*` directly. For a bounce it also records a versioned
`delivery_failure` assertion and only a conservative Batch-A single-recipient no-such-user result
may call the closed-list contact-control writer. Ordinary already-triaged mail is not re-read; old
bounce readings are backfilled independently.

The body is read for this one call and never stored. What is stored are short machine reasons,
the catalog ids, and the model's own summary and product descriptions.
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol

from origenlab_api.v2.mail_rules import FREE_MAIL_DOMAINS, PURCHASE_ORDER_FILENAME, PURCHASE_ORDER_NUMBER, fold, quote_number_tokens
from origenlab_worker.case_match import (
    CASE_MATCH_VALUE_NORM, CaseCard, MatchInput, MatchResult, match_case,
)
from origenlab_worker.case_match_model import PastDecision, build_pick_request, offered_cases, parse_pick
from origenlab_worker.bounce import BounceAnalysis, DELIVERY_FAILURE_VALUE_NORM, analyze_bounce
from origenlab_worker.catalog_match import ProductCandidate, match_products, model_key
from origenlab_worker.drive_filing import storage_key
from origenlab_worker.forwarded_request import FORWARDED_VALUE_NORM, is_candidate, is_forward_subject, read_forward
from origenlab_worker.people_reading import NO_PEOPLE_CLASSES, PEOPLE_VALUE_NORM, is_own_address, people_value
from origenlab_worker.mail_text import MailText, parse_mail
from origenlab_worker.quote_reader import pdf_text
from origenlab_worker.quote_refs import QUOTE_REFS_VALUE_NORM, read_quote_refs
from origenlab_worker.storage import StorageError
from origenlab_worker.triage_model import (
    DEFAULT_MODEL,
    LinkedCase,
    ModelReader,
    ModelReading,
    ModelRefused,
    ModelSender,
    build_people_request,
    build_request,
    parse_people,
    parse_reading,
    usage_tokens,
)
from origenlab_worker.triage_rules import TRIAGE_VERSION, RuleVerdict, TriageInput, classify

logger = logging.getLogger(__name__)
#: Extracts a PO number from full-phrase PDF body text: «ORDEN DE COMPRA N° 4500012345» or
#: «Purchase Order 4500012345» — as a complement to `PURCHASE_ORDER_NUMBER` which only handles
#: the compact OC-prefix form.
_PO_PHRASE_NUMBER = re.compile(
    r"(?:orden\s+de\s+compra|purchase\s+order)\s+(?:n[°ºo]?\.?\s*)?(\d{4,})",
)
VALUE_NORM = f"triage:v{TRIAGE_VERSION}"
REQUESTER_VALUE_NORM = "requester:v1"
PO_REFS_VALUE_NORM = "po_refs:v1"
#: Mail a person wrote about a deal: the context matcher places it on a case.
HUMAN_CLASSES: tuple[str, ...] = ("purchase_order", "lost", "quote_followup", "quote_request", "business_other")
#: The periodic sweep looks this far back; `triage-once --since-days` reaches further on purpose.
DEFAULT_SINCE_DAYS = 14

OUTCOMES: tuple[str, ...] = ("recorded", "already_triaged", "not_found", "no_eml", "storage_error",
                             "parse_error", "matcher_unavailable")


class EmlStore(Protocol):
    def get(self, key: str) -> bytes: ...


@dataclass(frozen=True)
class PendingMessage:
    source_record_id: str
    message_id: str
    direction: str
    labels: tuple[str, ...]
    eml_storage_path: str | None
    thread_id: str | None = None
    sent_at: str | None = None


@dataclass(frozen=True)
class ModelSettings:
    reader: ModelReader | None  # None: the model stage is off; the rules still run
    model: str


@dataclass
class TriageResult:
    outcome: str
    triage_class: str | None = None
    model: str | None = None  # 'ran' | 'off' | 'not_needed' | a ModelRefused code | 'api_error'
    mentions: int = 0
    case_match: str | None = None  # the matcher's decision, when it ran


_PENDING = """
select sr.id::text
  from comms.message m
  join evidence.source_record sr on sr.dedupe_key = 'gmail_message:' || m.provider_message_id
 where sr.kind = 'gmail_message'
   and m.parse_status = 'parsed'
   and m.eml_storage_path is not null
   and m.internal_date >= now() - make_interval(days => %s)
   and (
     not exists (select 1 from evidence.assertion a
                  where a.source_record_id = sr.id and a.kind = 'message_triage' and a.value_norm = %s)
     or (
       exists (select 1 from evidence.assertion a
                where a.source_record_id = sr.id and a.kind = 'message_triage' and a.value_norm = %s
                  and a.value->>'class' = 'bounce')
       and not exists (select 1 from evidence.assertion d
                        where d.source_record_id = sr.id and d.kind = 'delivery_failure'
                          and d.value_norm = %s)
     )
     or (
       exists (select 1 from evidence.assertion a
                where a.source_record_id = sr.id and a.kind = 'message_triage' and a.value_norm = %s
                  and a.value->>'class' = 'quote_request')
       and not exists (select 1 from evidence.assertion r
                        where r.source_record_id = sr.id and r.kind = 'message_triage'
                          and r.value_norm = %s)
     )
     or (
       exists (select 1 from evidence.assertion a
                where a.source_record_id = sr.id and a.kind = 'message_triage' and a.value_norm = %s
                  and a.value->>'class' = any(%s))
       and not exists (select 1 from evidence.assertion c
                        where c.source_record_id = sr.id and c.kind = 'message_triage'
                          and c.value_norm = %s)
     )
   )
 order by m.internal_date desc
 limit %s
"""
_ONE = """
select sr.id::text, m.id::text, m.direction, m.labels, m.eml_storage_path, m.provider_thread_id, m.internal_date,
       exists (select 1 from evidence.assertion a
                where a.source_record_id = sr.id and a.kind = 'message_triage' and a.value_norm = %s),
       (select a.value->>'class' from evidence.assertion a
         where a.source_record_id = sr.id and a.kind = 'message_triage' and a.value_norm = %s
         limit 1),
       exists (select 1 from evidence.assertion d
                where d.source_record_id = sr.id and d.kind = 'delivery_failure'
                  and d.value_norm = %s),
       exists (select 1 from evidence.assertion r
                where r.source_record_id = sr.id and r.kind = 'message_triage'
                  and r.value_norm = %s),
       exists (select 1 from evidence.assertion c
                where c.source_record_id = sr.id and c.kind = 'message_triage'
                  and c.value_norm = %s)
  from evidence.source_record sr
  join comms.message m on 'gmail_message:' || m.provider_message_id = sr.dedupe_key
 where sr.id = %s and sr.kind = 'gmail_message'
"""
# Every query below that reads cases keeps to sales cases, `o.kind = 'sale'`: a tender (kind
# 'tender') lives in «Licitaciones» — never a triage case, a matcher candidate or a case memory.
# The API's predicate is `origenlab_api.v2.case_kind.SALE_CASES`; the worker writes it inline.
_LINKED_CASES = """
select distinct o.id::text, o.title, o.stage
  from crm.opportunity_evidence oe
  join evidence.source_record sr on sr.id = oe.source_record_id
  join crm.opportunity o on o.id = oe.opportunity_id
 where oe.unlinked_at is null and sr.payload ->> 'gmail_thread_id' = %s
   and o.kind = 'sale'  -- case_kind.py
 order by 1
 limit 5
"""
#: Whether a tender holds this Gmail thread (any email of it linked to a `kind = 'tender'` case): then
#: the thread is the tender's, and the matcher suggests no sales case for it (`case_kind.py`).
_TENDER_THREAD = """
select exists (select 1 from crm.opportunity_evidence oe
                 join evidence.source_record sr on sr.id = oe.source_record_id
                 join crm.opportunity o on o.id = oe.opportunity_id and o.kind = 'tender'
                where oe.unlinked_at is null and sr.payload ->> 'gmail_thread_id' = %s)
"""
#: The cases an email could belong to: open, or closed in the last 120 days, never one an undo or a
#: person discarded. What a person would look at to place an email: who is asking (institution and
#: its domains), the quote numbers, every address seen on the case's Gmail threads, the products, and
#: the persons on the case (current participants, named directly or through their contact point; a merged
#: person walked up to the person it was merged into).
CASE_CARDS_SQL = """
with recursive threads as (
  select oe.opportunity_id, sr.payload ->> 'gmail_thread_id' as thread_id
    from crm.opportunity_evidence oe
    join evidence.source_record sr on sr.id = oe.source_record_id
   where oe.unlinked_at is null and sr.payload ->> 'gmail_thread_id' is not null
  union
  select q.opportunity_id, sr.payload ->> 'gmail_thread_id'
    from crm.quote_revision qr
    join crm.quote q on q.id = qr.quote_id
    join evidence.source_record sr on sr.id = qr.origin_source_record_id
   where sr.payload ->> 'gmail_thread_id' is not null
),
people (opportunity_id, person_id, depth) as (
  select pa.opportunity_id, coalesce(pa.person_id, cp.person_id), 0
    from crm.opportunity_participant pa
    left join crm.contact_point cp on cp.id = pa.contact_point_id
   where (pa.valid_to is null or pa.valid_to > current_date)
     and coalesce(pa.person_id, cp.person_id) is not null
  union all
  select pe.opportunity_id, p.merged_into_person_id, pe.depth + 1
    from people pe
    join crm.person p on p.id = pe.person_id
   where p.merged_into_person_id is not null and pe.depth < 20
)
select o.id::text, o.title, o.stage, o.created_at, o.closed_at, o.close_reason,
       org.name as institution,
       coalesce((select array_agg(d.domain_norm) from crm.organization_domain d
                  where d.organization_id = o.organization_id and d.removed_at is null), '{}') as domains,
       coalesce((select array_agg(q.quote_number order by q.quote_number) from crm.quote q
                  where q.opportunity_id = o.id), '{}') as quote_numbers,
       coalesce((select array_agg(distinct t.thread_id) from threads t where t.opportunity_id = o.id), '{}') as threads,
       coalesce(a.addresses, '{}') as addresses,
       coalesce((select array_agg(coalesce(i.model_text, i.description)) from crm.opportunity_interest i
                  where i.opportunity_id = o.id), '{}') as interests,
       coalesce((select array_agg(distinct pe.person_id::text) from people pe
                   join crm.person p on p.id = pe.person_id and p.merged_into_person_id is null
                  where pe.opportunity_id = o.id), '{}') as person_ids
  from crm.opportunity o
  left join crm.organization org on org.id = o.organization_id
  -- Every address seen on the case's threads, in one grouped join (not one scan of comms.message per case).
  left join (select t.opportunity_id, array_agg(distinct p.address_norm) as addresses
               from threads t
               join comms.message m on m.provider_thread_id = t.thread_id
               join comms.message_participant p on p.message_id = m.id
              where p.address_norm not like '%%@origenlab.cl'
              group by t.opportunity_id) a on a.opportunity_id = o.id
 where (o.closed_at is null or o.closed_at >= now() - interval '120 days')
   and o.close_reason is distinct from 'discarded_by_correction'
   and o.kind = 'sale'  -- case_kind.py: a tender is never a matcher candidate
 order by o.created_at desc
 limit 300
"""
_KNOWN_QUOTE_NUMBERS = "select quote_number from crm.quote"
#: The person behind a sender's address: an active email contact point a person owns (never a shared
#: mailbox or an address whose owner is unknown — those have no person), merges followed to the winner.
_SENDER_PERSON = """
with recursive chain (person_id, depth) as (
  select cp.person_id, 0
    from crm.contact_point cp
   where cp.kind = 'email' and cp.value_norm = %s and cp.status = 'active' and cp.person_id is not null
     and cp.usage not in ('shared_mailbox', 'individual_owner_unknown', 'unattributed')
  union all
  select p.merged_into_person_id, c.depth + 1
    from chain c
    join crm.person p on p.id = c.person_id
   where p.merged_into_person_id is not null and c.depth < 20
)
select c.person_id::text
  from chain c
  join crm.person p on p.id = c.person_id and p.merged_into_person_id is null
 limit 1
"""
_RECIPIENTS = """
select p.address_norm from comms.message_participant p
 where p.message_id = %s and p.role in ('to', 'cc') order by 1
"""
#: Who is on one message, for `people:v1`: the `from` address with its header display name, and the
#: `to`/`cc` addresses in header-capture order.
_PEOPLE_PARTICIPANTS = """
select p.role, p.address_norm, p.display_name from comms.message_participant p
 where p.message_id = %s and p.role in ('from', 'to', 'cc')
 order by case p.role when 'from' then 0 when 'to' then 1 else 2 end, p.created_at, p.address_norm
"""
#: Attachment names of the other emails on the same Gmail thread.
_THREAD_ATTACHMENTS = """
select a.filename
  from comms.attachment a
  join comms.message m on m.id = a.message_id
 where m.provider_thread_id = %s and m.id <> %s and a.filename is not null
 order by m.internal_date, a.part_index
 limit 200
"""
#: The latest decisions a person took on mail from this sender (its domain, unless free mail): emails
#: linked to a case, and case suggestions answered «no es de este caso». What the model learns from.
_PAST_DECISIONS = """
with sent as (
  select sr.id as source_record_id, m.subject, p.address_norm as sender
    from comms.message m
    join evidence.source_record sr on sr.dedupe_key = 'gmail_message:' || m.provider_message_id
    join comms.message_participant p on p.message_id = m.id and p.role = 'from'
   where case when %(by_domain)s then split_part(p.address_norm, '@', 2) = %(who)s else p.address_norm = %(who)s end
)
select * from (
  select s.sender, s.subject, o.id::text, o.title, 'linked' as verdict, oe.linked_at as at
    from sent s
    join crm.opportunity_evidence oe on oe.source_record_id = s.source_record_id and oe.unlinked_at is null
    join crm.opportunity o on o.id = oe.opportunity_id and o.kind = 'sale'  -- case_kind.py
  union all
  -- An email a person took off a case («Quitar» on Hoy, or unlinked by hand): not that case.
  select s.sender, s.subject, o.id::text, o.title, 'rejected', oe.unlinked_at
    from sent s
    join crm.opportunity_evidence oe on oe.source_record_id = s.source_record_id and oe.unlinked_at is not null
    join crm.opportunity o on o.id = oe.opportunity_id and o.kind = 'sale'  -- case_kind.py
   where not exists (select 1 from crm.opportunity_evidence again
                      where again.source_record_id = oe.source_record_id
                        and again.opportunity_id = oe.opportunity_id and again.unlinked_at is null)
  union all
  select s.sender, s.subject, o.id::text, o.title, 'rejected', r.reviewed_at
    from sent s
    join evidence.assertion a on a.source_record_id = s.source_record_id and a.kind = 'message_triage'
                             and a.value_norm = %(norm)s
    join evidence.triage_review r on r.assertion_id = a.id and r.verdict = 'rejected'
    -- The case the person was shown: named in the verdict's note by the dashboard, else the model's
    -- pick when it named a case, else layer 1's first candidate.
    left join crm.opportunity o on o.id::text = coalesce(
        substring(r.note from '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'),
        case when a.value -> 'pick' ->> 'choice' ~ '^[0-9a-f]{8}-' then a.value -> 'pick' ->> 'choice' end,
        a.value -> 'candidates' -> 0 ->> 'opportunity_id')
   where o.id is null or o.kind = 'sale'  -- case_kind.py
) d
order by at desc
limit %(limit)s
"""
_INSERT_ASSERTION = """
insert into evidence.assertion (source_record_id, kind, value_norm, value)
values (%s, %s, %s, %s)
on conflict (source_record_id, kind, value_norm) do nothing
"""

_REQUESTER_ORGANIZATIONS = """
select o.id::text, o.name
  from crm.organization o
 where o.status = 'active'
   and o.confirmation = 'confirmed'
   and o.merged_into_organization_id is null
   and not exists (
       select 1
         from crm.organization_relationship rel
        where rel.organization_id = o.id
          and rel.role in ('supplier', 'manufacturer')
          and (rel.valid_to is null or rel.valid_to > current_date)
   )
 order by length(o.name) desc, o.id
"""


class TriageDb:
    """The triage's reads and writes on one verified `origenlab_worker` connection."""

    def __init__(self, conn: Any, statement_timeout_ms: int = 30_000) -> None:
        self._conn = conn
        self._timeout = int(statement_timeout_ms)

    @contextmanager
    def _tx(self) -> Iterator[Any]:
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(f"set local statement_timeout = {self._timeout}")
            yield cur

    def pending(self, *, since_days: int, limit: int) -> list[str]:
        with self._tx() as cur:
            cur.execute(_PENDING, (
                since_days, VALUE_NORM, VALUE_NORM, DELIVERY_FAILURE_VALUE_NORM,
                VALUE_NORM, REQUESTER_VALUE_NORM, VALUE_NORM, list(HUMAN_CLASSES), CASE_MATCH_VALUE_NORM, limit,
            ))
            return [row[0] for row in cur.fetchall()]

    def load(self, source_record_id: str) -> tuple[PendingMessage, bool, str | None, bool, bool, bool] | None:
        with self._tx() as cur:
            cur.execute(_ONE, (
                VALUE_NORM, VALUE_NORM, DELIVERY_FAILURE_VALUE_NORM, REQUESTER_VALUE_NORM, CASE_MATCH_VALUE_NORM,
                source_record_id,
            ))
            row = cur.fetchone()
        if row is None:
            return None
        (sr, mid, direction, labels, path, thread, sent, done, triage_class, delivery_done, requester_done,
         case_match_done) = row
        return (PendingMessage(sr, mid, direction, tuple(labels or ()), path, thread,
                               sent.isoformat() if sent is not None else None), bool(done),
                triage_class, bool(delivery_done), bool(requester_done), bool(case_match_done))

    def case_cards(self) -> list[CaseCard]:
        """The case snapshot the matcher scores against (`CASE_CARDS_SQL`), read-only."""
        with self._tx() as cur:
            cur.execute(CASE_CARDS_SQL)
            return [CaseCard.from_row(case_row(r)) for r in cur.fetchall()]

    def tender_thread(self, thread_id: str | None) -> bool:
        """Whether a tender holds this Gmail thread (`_TENDER_THREAD`), read-only."""
        if not thread_id:
            return False
        with self._tx() as cur:
            cur.execute(_TENDER_THREAD, (thread_id,))
            return bool(cur.fetchone()[0])

    def known_quote_numbers(self) -> list[str]:
        """Every quote number the CRM holds, on any case."""
        with self._tx() as cur:
            cur.execute(_KNOWN_QUOTE_NUMBERS)
            return [r[0] for r in cur.fetchall()]

    def sender_person(self, address: str | None) -> str | None:
        """The person behind the sender's address (`_SENDER_PERSON`), read-only; None when unknown."""
        if not address:
            return None
        with self._tx() as cur:
            cur.execute(_SENDER_PERSON, (address.strip().lower(),))
            row = cur.fetchone()
        return row[0] if row else None

    def case_context(self, message: PendingMessage) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """(recipients, attachment names of the thread's other emails) of one message."""
        with self._tx() as cur:
            cur.execute(_RECIPIENTS, (message.message_id,))
            recipients = tuple(r[0] for r in cur.fetchall())
            names: tuple[str, ...] = ()
            if message.thread_id:
                cur.execute(_THREAD_ATTACHMENTS, (message.thread_id, message.message_id))
                names = tuple(r[0] for r in cur.fetchall())
        return recipients, names

    def message_people(self, message_id: str) -> tuple[str | None, dict[str, str], tuple[str, ...]]:
        """(the `from` address, address → header display name, the `to`+`cc` addresses outside our
        domains (`people_reading.OWN_DOMAINS`) and other than the sender) of one message, read-only."""
        with self._tx() as cur:
            cur.execute(_PEOPLE_PARTICIPANTS, (message_id,))
            rows = cur.fetchall()
        sender = next((addr for role, addr, _ in rows if role == "from"), None)
        names = {addr: name.strip() for _, addr, name in rows if name and name.strip()}
        co: list[str] = []
        for role, addr, _ in rows:
            if role != "from" and addr != sender and addr not in co and not is_own_address(addr):
                co.append(addr)
        return sender, names, tuple(co)

    def record_people(self, source_record_id: str, value: dict[str, Any]) -> None:
        """`people:v1` — who wrote this email (`people_reading.py`). Once per email."""
        from psycopg.types.json import Jsonb

        with self._tx() as cur:
            cur.execute(_INSERT_ASSERTION, (source_record_id, "message_triage", PEOPLE_VALUE_NORM, Jsonb(value)))

    def pending_people(self, *, since_days: int, limit: int) -> list[str]:
        """Inbound emails of the last days whose latest triage reading (any `triage:v<N>`) is of a
        class a person writes, with no `people:v1` yet — what `people-backfill` reads, newest first.
        A forward is skipped in Python."""
        with self._tx() as cur:
            cur.execute(_PEOPLE_PENDING, (since_days, sorted(NO_PEOPLE_CLASSES), PEOPLE_VALUE_NORM, limit))
            return [row[0] for row in cur.fetchall()]

    def latest_triage_class(self, source_record_id: str) -> str | None:
        """The class of the email's latest triage reading of any version (`triage:v<N>`), or None
        when it has none."""
        with self._tx() as cur:
            cur.execute(_LATEST_TRIAGE_CLASS, (source_record_id,))
            row = cur.fetchone()
        return (row[0] or "") if row else None

    def past_decisions(self, sender: str | None, limit: int = 6) -> list[PastDecision]:
        if not sender or "@" not in sender:
            return []
        domain = sender.rsplit("@", 1)[1]
        by_domain = domain not in FREE_MAIL_DOMAINS
        with self._tx() as cur:
            cur.execute(_PAST_DECISIONS, {"by_domain": by_domain, "who": domain if by_domain else sender,
                                          "norm": CASE_MATCH_VALUE_NORM, "limit": limit})
            return [PastDecision(snd, subj or "", oid, title, verdict)
                    for snd, subj, oid, title, verdict, _at in cur.fetchall()]

    def record_case_match(self, source_record_id: str, value: dict[str, Any]) -> None:
        from psycopg.types.json import Jsonb

        with self._tx() as cur:
            cur.execute(_INSERT_ASSERTION, (source_record_id, "message_triage", CASE_MATCH_VALUE_NORM, Jsonb(value)))

    def linked_cases(self, thread_id: str | None) -> list[LinkedCase]:
        """The open and closed cases any email of this Gmail thread is linked to — the API's own
        thread rule (R1, `mail_rules_repository._LINKS_SQL`), read-only."""
        if not thread_id:
            return []
        with self._tx() as cur:
            cur.execute(_LINKED_CASES, (thread_id,))
            return [LinkedCase(oid, title, stage) for oid, title, stage in cur.fetchall()]

    def candidates(self, mail: MailText) -> list[ProductCandidate]:
        with self._tx() as cur:
            return match_products(cur, mail.subject, mail.body)

    def requester_match(self, body: str) -> dict[str, Any]:
        """Exact, unique CRM institution name in the cleaned body; no model and no fuzzy match."""
        with self._tx() as cur:
            cur.execute(_REQUESTER_ORGANIZATIONS)
            organizations = [(str(oid), str(name)) for oid, name in cur.fetchall()]
        return match_requester_organization(body, organizations)

    def record_requester(self, source_record_id: str, requester: dict[str, Any]) -> None:
        from psycopg.types.json import Jsonb

        with self._tx() as cur:
            cur.execute(
                _INSERT_ASSERTION,
                (source_record_id, "message_triage", REQUESTER_VALUE_NORM, Jsonb(requester)),
            )

    def pending_quote_refs(self, *, since_days: int, limit: int) -> list[str]:
        """Captured emails of the last days the quote-number reading has not seen yet."""
        with self._tx() as cur:
            cur.execute(_QUOTE_REFS_PENDING, (since_days, QUOTE_REFS_VALUE_NORM, limit))
            return [row[0] for row in cur.fetchall()]

    def record_quote_refs(self, source_record_id: str, tokens: Sequence[str]) -> None:
        """The quote tokens the body names — possibly none, so the email is not read again."""
        from psycopg.types.json import Jsonb

        with self._tx() as cur:
            cur.execute(_INSERT_ASSERTION, (source_record_id, "message_triage", QUOTE_REFS_VALUE_NORM,
                                            Jsonb({"version": 1, "tokens": list(tokens)})))

    def record_po_refs(self, source_record_id: str, tokens: Sequence[str],
                       purchase_order_numbers: Sequence[str]) -> None:
        """Quote tokens and PO numbers extracted from OC PDF attachments — possibly empty."""
        from psycopg.types.json import Jsonb

        with self._tx() as cur:
            cur.execute(_INSERT_ASSERTION, (source_record_id, "message_triage", PO_REFS_VALUE_NORM,
                                            Jsonb({"version": 1, "tokens": list(tokens),
                                                   "purchase_order_numbers": list(purchase_order_numbers)})))

    def pending_forwards(self, *, since_days: int, limit: int) -> list[str]:
        with self._tx() as cur:
            cur.execute(_FORWARD_PENDING, (since_days, limit))
            return [row[0] for row in cur.fetchall()]

    def record_forward(self, source_record_id: str, value: dict[str, Any]) -> None:
        """Who really asked, as an evidence record of its own (`forwarded_request`): the customer
        fields and the id of the email they were read from — never its text. Once per email."""
        from psycopg.types.json import Jsonb

        payload = {**value, "forwarded_message_id": source_record_id, "reading": FORWARDED_VALUE_NORM}
        with self._tx() as cur:
            cur.execute(_INSERT_FORWARD, (f"forwarded_request:{source_record_id}", Jsonb(payload)))

    def record(self, source_record_id: str, reading: dict[str, Any],
               mentions: Sequence[tuple[str, dict[str, Any]]],
               requester: dict[str, Any] | None = None, case_match: dict[str, Any] | None = None) -> int:
        from psycopg.types.json import Jsonb

        with self._tx() as cur:
            cur.execute(_INSERT_ASSERTION, (source_record_id, "message_triage", VALUE_NORM, Jsonb(reading)))
            if requester is not None:
                cur.execute(
                    _INSERT_ASSERTION,
                    (source_record_id, "message_triage", REQUESTER_VALUE_NORM, Jsonb(requester)),
                )
            if case_match is not None:
                cur.execute(
                    _INSERT_ASSERTION,
                    (source_record_id, "message_triage", CASE_MATCH_VALUE_NORM, Jsonb(case_match)),
                )
            for norm, value in mentions:
                cur.execute(_INSERT_ASSERTION, (source_record_id, "product_mention", norm, Jsonb(value)))
            return len(mentions)

    def record_delivery_failure(self, source_record_id: str, analysis: BounceAnalysis) -> int:
        """Record the versioned NDR observation and apply only its exact Batch-A hard block.

        The assertion and privileged call share one transaction. If the database boundary refuses
        the proposed block, the assertion rolls back too and the sweep retries instead of silently
        recording a half-applied safety decision.
        """
        from psycopg.types.json import Jsonb

        with self._tx() as cur:
            cur.execute(
                _INSERT_ASSERTION,
                (source_record_id, "delivery_failure", DELIVERY_FAILURE_VALUE_NORM, Jsonb(analysis.as_json())),
            )
            for address in analysis.auto_block_addresses:
                cur.execute(
                    """select outbound.add_contact_control(
                           'block', 'all', %s, 'invalid_address', null, null, %s
                       )""",
                    (
                        address,
                        Jsonb({
                            "source_record_id": source_record_id,
                            "triage_value_norm": VALUE_NORM,
                            "delivery_value_norm": DELIVERY_FAILURE_VALUE_NORM,
                            "reason_code": analysis.reason_code,
                        }),
                    ),
                )
                cur.fetchone()
        return len(analysis.auto_block_addresses)

    # Memory methods -------------------------------------------------------

    def latest_subject_memory(self, subject_kind: str, subject_id: str) -> dict[str, Any] | None:
        """The most recent memory for this subject, or None if none exists yet.

        Returns the stored `memory` jsonb plus the version number injected as ``_version``
        so the writer can compute the next version without a separate query.
        """
        with self._tx() as cur:
            cur.execute(_LATEST_SUBJECT_MEMORY, (subject_kind, subject_id))
            row = cur.fetchone()
        if row is None:
            return None
        memory, version = row
        return {**dict(memory), "_version": version}

    def insert_subject_memory(self, subject_kind: str, subject_id: str, version: int,
                               memory: dict[str, Any], *, source_record_id: str | None = None,
                               model: str | None = None) -> None:
        """Append a new memory version (idempotent on conflict)."""
        from psycopg.types.json import Jsonb

        with self._tx() as cur:
            cur.execute(_INSERT_SUBJECT_MEMORY, (
                subject_kind, subject_id, version, Jsonb(memory),
                source_record_id, model,
            ))

    def opportunity_org_and_person(self, opportunity_id: str) -> tuple[str | None, str | None]:
        """(organization_id, person_id) for the primary org and contact of one case, or (None, None)."""
        with self._tx() as cur:
            cur.execute(_OPPORTUNITY_ORG_AND_PERSON, (opportunity_id,))
            row = cur.fetchone()
        if row is None:
            return None, None
        return row[0], row[1]

    def opportunity_institution_name(self, opportunity_id: str) -> str | None:
        with self._tx() as cur:
            cur.execute(_OPPORTUNITY_INSTITUTION_NAME, (opportunity_id,))
            row = cur.fetchone()
        return row[0] if row else None

    def opportunity_quote_numbers(self, opportunity_id: str) -> list[str]:
        with self._tx() as cur:
            cur.execute(_OPPORTUNITY_QUOTE_NUMBERS, (opportunity_id,))
            return [r[0] for r in cur.fetchall()]

    def pending_memory_backfill(self, *, since_days: int, limit: int) -> list[str]:
        """Emails already matched to a case that have no case memory written yet."""
        with self._tx() as cur:
            cur.execute(_MEMORY_BACKFILL_PENDING,
                        {"human_classes": list(HUMAN_CLASSES), "days": since_days, "limit": limit})
            return [row[0] for row in cur.fetchall()]

    # End memory methods ---------------------------------------------------


def case_row(c: tuple) -> dict[str, Any]:
    """One `CASE_CARDS_SQL` row as a plain dict (the shape of the eval's `cases.jsonl`)."""
    (oid, title, stage, created, closed, reason, institution, domains, quotes, threads, addresses, interests,
     people) = c
    iso = lambda v: v.isoformat() if v is not None else None  # noqa: E731
    return {"opportunity_id": oid, "title": title, "stage": stage, "created_at": iso(created), "closed_at": iso(closed),
            "close_reason": reason, "institution": institution, "domains": list(domains or []),
            "quote_numbers": list(quotes or []), "threads": list(threads or []), "addresses": list(addresses or []),
            "interests": [i for i in (interests or []) if i], "person_ids": sorted(people or [])}


def match_message(db: Any, message: PendingMessage, mail: MailText,
                  settings: ModelSettings | None = None) -> tuple[MatchResult, dict[str, Any]] | None:
    """The context matcher for one message: layer 1 always; layer 2 (Claude) when the model is on and
    layer 1 is not sure. Returns the decision and the `case_match:v1` value, or None when the matcher's
    own reads failed. Neither that nor a failed or unusable pick ever blocks the triage: a pick is
    recorded as `pick_state` and layer 1 stands; a failed read leaves the case match to the backfill."""
    try:
        held_by_tender = db.tender_thread(message.thread_id)
        if not held_by_tender:
            recipients, thread_names = db.case_context(message)
            cards = db.case_cards()
            known = db.known_quote_numbers()
            # Only a client's email is placed by its person: our own outbound sender is no client.
            person = db.sender_person(mail.sender) if message.direction == "inbound" else None
    except Exception:  # noqa: BLE001 - a suggestion never takes the triage down with it
        return None
    if held_by_tender:
        # The thread is a tender's («Licitaciones»): no sales case and no new case is suggested.
        none = MatchResult("none", (), (), None)
        return none, {**none.as_json(), "pick_state": "not_needed", "held_by": "tender"}
    inp = MatchInput(mail.sender, recipients, message.thread_id, message.direction, mail.subject, mail.body,
                     mail.attachment_names, message.sent_at, thread_names, sender_person_id=person)
    result = match_case(inp, cards, known)
    value: dict[str, Any] = result.as_json()
    if settings is None or settings.reader is None:
        value["pick_state"] = "off" if settings is not None else "not_needed"
        return result, value
    if result.decision == "exact":
        value["pick_state"] = "not_needed"
        return result, value
    offered = {c.opportunity_id for c in offered_cases(result, cards)}
    try:
        answer = settings.reader(build_pick_request(inp, result, cards, db.past_decisions(mail.sender),
                                                    model=settings.model))
        value["pick"] = parse_pick(answer, offered=offered, model=settings.model)
        value["pick_state"] = "ran"
    except ModelRefused as exc:
        value["pick_state"] = str(exc)
    except Exception:  # noqa: BLE001 - a suggestion is never worth blocking the triage; reported by state only
        value["pick_state"] = "unavailable"
    return result, value


def _identity_text(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", fold(text)).split())


def match_requester_organization(body: str, organizations: Sequence[tuple[str, str]]) -> dict[str, Any]:
    """Unique exact organization-name phrase in the cleaned body.

    Short/generic names are intentionally ignored. A second match makes the result ambiguous.
    Only canonical ids/names are stored; the body itself is never persisted.
    """
    haystack = f" {_identity_text(body)} "
    matches: list[tuple[str, str]] = []
    for organization_id, name in organizations:
        needle = _identity_text(name)
        if len(needle) < 10 or len(needle.split()) < 2:
            continue
        if f" {needle} " in haystack:
            matches.append((organization_id, name))
    if len(matches) == 1:
        oid, name = matches[0]
        return {
            "requester_version": 1,
            "status": "matched",
            "organization_id": oid,
            "organization_name": name,
            "how": "exact_name_in_body",
        }
    if not matches:
        return {"requester_version": 1, "status": "none", "how": "exact_name_in_body"}
    return {
        "requester_version": 1,
        "status": "ambiguous",
        "how": "exact_name_in_body",
        "candidate_ids": sorted(oid for oid, _name in matches),
    }


def _candidate_json(c: ProductCandidate) -> dict[str, Any]:
    return {"product_id": c.product_id, "model_number": c.model_number, "model_key": c.model_key,
            "how": c.how, "evidence": c.evidence}


def mentions_for(reading: ModelReading | None, candidates: Sequence[ProductCandidate]) -> list[tuple[str, dict[str, Any]]]:
    """`(value_norm, value)` for each product the message names, deduplicated by value_norm."""
    out: dict[str, dict[str, Any]] = {}
    base = {"triage_version": TRIAGE_VERSION}
    if reading is not None:
        by_id = {c.product_id: c for c in candidates}
        for p in reading.products:
            matched = by_id.get(p.catalog_product_id or "")
            norm = (matched.model_key if matched and matched.model_key
                    else model_key(p.model) if p.model else fold(p.description))[:200]
            if norm:
                out.setdefault(norm, {**base, "source": "model", "description": p.description, "brand": p.brand,
                                      "model": p.model, "quantity": p.quantity,
                                      "catalog_product_id": p.catalog_product_id})
    else:
        for c in candidates:
            if c.how == "model_key" and c.model_key:
                out.setdefault(c.model_key, {**base, "source": "catalog_model_key", "description": c.name,
                                             "model": c.model_number, "quantity": None,
                                             "catalog_product_id": c.product_id})
    return list(out.items())


def reading_value(verdict: RuleVerdict, mail: MailText, candidates: Sequence[ProductCandidate],
                  model_state: str, reading: ModelReading | None, elapsed_ms: int,
                  cases: Sequence[LinkedCase] = ()) -> dict[str, Any]:
    value: dict[str, Any] = {
        "triage_version": TRIAGE_VERSION,
        "class": verdict.triage_class,
        "needs_model": verdict.needs_model,
        "reasons": list(verdict.reasons),
        "signals": verdict.signals,
        "quote_numbers": list(verdict.quote_numbers),
        "purchase_order_numbers": list(verdict.purchase_order_numbers),
        "sender_domain": verdict.sender_domain,
        "free_mail": verdict.free_mail,
        "attachment_count": len(mail.attachment_names),
        "body_chars": len(mail.body),
        "candidates": [_candidate_json(c) for c in candidates],
        "linked_cases": [{"opportunity_id": c.opportunity_id, "stage": c.stage} for c in cases],
        "model_state": model_state,
        "elapsed_ms": elapsed_ms,
    }
    if reading is not None:
        value["reading"] = reading.as_json()
    return value


def triage_one(db: TriageDb, store: EmlStore, source_record_id: str, settings: ModelSettings, *,
               clock: Callable[[], float] = time.monotonic) -> TriageResult:
    started = clock()
    loaded = db.load(source_record_id)
    if loaded is None:
        return TriageResult("not_found")
    message, done, existing_class, delivery_done, requester_done, case_match_done = loaded
    needs_delivery_backfill = done and existing_class == "bounce" and not delivery_done
    needs_requester_backfill = done and existing_class == "quote_request" and not requester_done
    needs_case_match_backfill = done and existing_class in HUMAN_CLASSES and not case_match_done
    if done and not needs_delivery_backfill and not needs_requester_backfill and not needs_case_match_backfill:
        return TriageResult("already_triaged")
    if not message.eml_storage_path:
        return TriageResult("no_eml")
    try:
        raw = store.get(storage_key(message.eml_storage_path))
    except StorageError:
        return TriageResult("storage_error")
    try:
        mail = parse_mail(raw)
    except Exception:  # noqa: BLE001 - a malformed message is recorded as such by the capture; skip it
        return TriageResult("parse_error")

    # Independent backfills never re-run the model. Hard-bounce evidence keeps triage:v1 exactly;
    # requester:v1 only reads the cleaned body and a read-only CRM organization list.
    if needs_delivery_backfill:
        analysis = analyze_bounce(raw)
        db.record_delivery_failure(message.source_record_id, analysis)
        return TriageResult("recorded", "bounce", "not_needed", 0)
    if needs_requester_backfill:
        db.record_requester(message.source_record_id, db.requester_match(mail.body))
        return TriageResult("recorded", "quote_request", "not_needed", 0)
    if needs_case_match_backfill:
        matched = match_message(db, message, mail)
        if matched is None:
            return TriageResult("matcher_unavailable", existing_class, "not_needed", 0, "unavailable")
        match, match_value = matched
        db.record_case_match(message.source_record_id, match_value)
        return TriageResult("recorded", existing_class, "not_needed", 0, match.decision)

    verdict = classify(TriageInput(direction=message.direction, labels=message.labels, mail=mail))
    candidates = db.candidates(mail) if verdict.needs_model or verdict.signals.get("quote_word") else []
    cases = db.linked_cases(message.thread_id) if verdict.needs_model else []

    reading: ModelReading | None = None
    if not verdict.needs_model:
        state = "not_needed"
    elif settings.reader is None:
        state = "off"
    else:
        request = build_request(mail, verdict, candidates, model=settings.model, cases=cases)
        try:
            answer = settings.reader(request)
        except Exception as exc:  # noqa: BLE001 - reported by class and status only, never message text
            if not is_permanent_api_error(exc):
                raise ModelUnavailable(type(exc).__name__) from None
            answer, state = None, f"api_error_{getattr(exc, 'status_code', 0)}"
        if answer is not None:
            try:
                reading = parse_reading(answer, model=settings.model, candidates=candidates, cases=cases)
                state = "ran"
            except ModelRefused as exc:
                state = str(exc)

    mentions = mentions_for(reading, candidates)
    value = reading_value(verdict, mail, candidates, state, reading, int((clock() - started) * 1000), cases)
    requester = db.requester_match(mail.body) if verdict.triage_class == "quote_request" else None
    matched = match_message(db, message, mail, settings) if verdict.triage_class in HUMAN_CLASSES else None
    match, match_value = matched if matched is not None else (None, None)
    written = db.record(message.source_record_id, value, mentions, requester, match_value)
    if verdict.triage_class == "bounce":
        db.record_delivery_failure(message.source_record_id, analyze_bounce(raw))
    record_people_reading(db, message, mail, reading, verdict.triage_class)
    record_forward_reading(db, message.source_record_id, raw, mail.sender, mail.subject)
    record_quote_refs(db, message.source_record_id, mail)
    if message.direction == "inbound":
        record_po_refs(db, message.source_record_id, raw)
    if verdict.triage_class in HUMAN_CLASSES and (match is None or match.decision != "rejected"):
        record_memory_reading(db, message, match, mail.body, cases)
    decision = match.decision if match else ("unavailable" if verdict.triage_class in HUMAN_CLASSES else None)
    return TriageResult("recorded", verdict.triage_class, state, written, decision)


def record_people_reading(db: Any, message: PendingMessage, mail: MailText, reading: ModelReading | None,
                          triage_class: str | None) -> bool:
    """`people:v1` (`people_reading.py`): who wrote an inbound email a person wrote — never machine
    mail (`NO_PEOPLE_CLASSES`). The model's `sender` when it ran, otherwise only the header name and
    the delimited signature. Isolated like the quote-number reading: a failure is logged by class
    and swallowed, never the triage's. True when written."""
    if message.direction != "inbound" or triage_class in NO_PEOPLE_CLASSES:
        return False
    try:
        value = _people_value(message, mail, db.message_people(message.message_id),
                              reading.sender if reading is not None else None)
        if value is None:
            return False
        db.record_people(message.source_record_id, value)
        return True
    except Exception as exc:  # noqa: BLE001 - never let this optional reading break the triage
        logger.warning("people reading skipped: %s", type(exc).__name__)
        return False


def _people_value(message: PendingMessage, mail: MailText, people: tuple[str | None, dict[str, str], tuple[str, ...]],
                  sender: ModelSender | None, *, empty_ok: bool = False) -> dict[str, Any] | None:
    """The one place a `people:v1` value is built — the triage and the backfill write the same.
    ``empty_ok`` (the backfill, after a paid model call) keeps a reading that knows only the address."""
    address, names, co = people
    return people_value(direction=message.direction, sender_address=address, header_names=names,
                        reading_sender=sender, signature_text=mail.signature_text, co_addresses=co,
                        forwarded=is_forward_subject(mail.subject), empty_ok=empty_ok)


def backfill_people(db: Any, store: EmlStore, reader: ModelReader | None, source_record_ids: Sequence[str], *,
                    apply: bool, sample: int | None, model: str = DEFAULT_MODEL) -> dict[str, int]:
    """`people:v1` for emails triaged before the reading existed (`TriageDb.pending_people`): one
    people-only model call each (`build_people_request`), then the very value the triage writes.

    Never re-runs the triage. Skips mail the triage would skip (not inbound, no triage reading, a
    class no person writes, a forward, our own sender) without calling the model. A plain dry run
    reads and counts and calls no model; ``sample`` reads only the first N emails and calls the model
    for them, so their token totals price the whole run; only ``apply`` writes. Resumable: a written
    email leaves `pending_people`. An email the model was asked about is always written — when
    nothing is known of the writer, as a reading with only the address (the people rules plan
    nothing for it) — so a rerun never pays for it twice. A refused answer or a permanent API error
    is written without a signature, as the triage does; a passing outage raises `ModelUnavailable`
    carrying the counts so far (`.counts`; run it again later).

    The triage reading may be of any version (`triage:v<N>`, its latest): mail triaged before the
    current `TRIAGE_VERSION` is in the 90 days too.
    """
    counts = {"found": len(source_record_ids), "read": 0, "model_calls": 0, "written": 0, "skipped": 0,
              "input_tokens": 0, "output_tokens": 0}
    ids = list(source_record_ids)[:sample] if sample is not None else list(source_record_ids)
    ask = reader is not None and (apply or sample is not None)
    for sid in ids:
        loaded = db.load(sid)
        if loaded is None:
            counts["skipped"] += 1
            continue
        message = loaded[0]
        triage_class = db.latest_triage_class(sid) if message.direction == "inbound" else None
        if (triage_class is None or message.direction != "inbound" or triage_class in NO_PEOPLE_CLASSES
                or not message.eml_storage_path):
            counts["skipped"] += 1
            continue
        try:
            mail = parse_mail(store.get(storage_key(message.eml_storage_path)))
        except Exception:  # noqa: BLE001 - one unreadable message is skipped, never fatal
            counts["skipped"] += 1
            continue
        people = db.message_people(message.message_id)
        if is_forward_subject(mail.subject) or not people[0] or is_own_address(people[0]):
            counts["skipped"] += 1
            continue
        counts["read"] += 1
        sender: ModelSender | None = None
        if ask:
            assert reader is not None
            counts["model_calls"] += 1
            try:
                answer = reader(build_people_request(mail, model=model))
            except Exception as exc:  # noqa: BLE001 - reported by class only, never message text
                if not is_permanent_api_error(exc):
                    counts["model_calls"] -= 1
                    counts["read"] -= 1
                    unavailable = ModelUnavailable(type(exc).__name__)
                    unavailable.counts = counts  # type: ignore[attr-defined]
                    raise unavailable from None
                answer = None
            if answer is not None:
                tokens_in, tokens_out = usage_tokens(answer)
                counts["input_tokens"] += tokens_in
                counts["output_tokens"] += tokens_out
                try:
                    sender, _ = parse_people(answer)
                except ModelRefused:
                    sender = None
        if not apply:
            continue
        value = _people_value(message, mail, people, sender, empty_ok=ask)
        if value is None:
            counts["skipped"] += 1
            continue
        db.record_people(sid, value)
        counts["written"] += 1
    return counts


def record_quote_refs(db: Any, source_record_id: str, mail: MailText) -> bool:
    """`quote_refs:v1` (`quote_refs.py`), isolated like the forward reading: a failure here is
    logged by class and swallowed, never the triage's."""
    try:
        db.record_quote_refs(source_record_id, read_quote_refs(mail.subject, mail.body))
        return True
    except Exception as exc:  # noqa: BLE001 - never let this optional reading break the triage
        logger.warning("quote reference reading skipped: %s", type(exc).__name__)
        return False


def _is_oc_pdf(filename: str) -> bool:
    """True when an attachment looks like a purchase-order PDF.

    Accepts OC/O.C. and PO/P.O. prefixes, and Spanish/English long-form names anywhere in the
    stem.  Only `.pdf` files — never Word documents, spreadsheets, etc.  All matching is done on
    the folded (lowercase) name, matching `PURCHASE_ORDER_FILENAME`'s convention.

    This is intentionally broader than the API's `PURCHASE_ORDER_FILENAME` (which is used for the
    R5 «purchase_order» triage-class signal and must stay narrow).  Real-world OCs seen so far:
    «OC 4500000123.pdf», «4521-2026 Orden de compra Proveedor Ficticio.pdf», «PO_4500012345.pdf»,
    «Purchase Order 123.pdf».
    """
    import re as _re
    name = fold(filename)
    if not name.endswith(".pdf"):
        return False
    return bool(
        PURCHASE_ORDER_FILENAME.match(name)               # ^oc / ^o.c. (existing)
        or _re.match(r"^p\.?o(?![a-z])", name)            # PO_ / PO4500
        or "orden de compra" in name
        or "orden_de_compra" in name
        or _re.search(r"purchase[\s_]order", name)        # "purchase order" or "purchase_order"
    )


def _po_refs_from_text(text: str) -> tuple[list[str], list[str]]:
    """Extract quote tokens and PO numbers from plain text (already extracted from a PDF).

    Applies `fold()` so uppercase PDF text («OC 4500123», «CN09876») is matched correctly by the
    lowercase-only regexes.  Returns (tokens, po_numbers).
    """
    folded = fold(text)
    tokens: list[str] = []
    po_numbers: list[str] = []
    for t in quote_number_tokens(folded):
        if t not in tokens:
            tokens.append(t)
    for m in PURCHASE_ORDER_NUMBER.finditer(folded):
        n = m.group(1)
        if n not in po_numbers:
            po_numbers.append(n)
    # Also capture PO numbers from full-phrase form: «ORDEN DE COMPRA N° 4500012345»
    for m in _PO_PHRASE_NUMBER.finditer(folded):
        n = m.group(1)
        if n not in po_numbers:
            po_numbers.append(n)
    return tokens, po_numbers


def _po_refs_from_raw(raw: bytes) -> tuple[list[str], list[str]]:
    """Extract quote tokens and PO numbers from OC PDF attachments in a raw EML.

    Parses the EML for parts whose filename passes `_is_oc_pdf`, extracts the PDF text layer, and
    returns (tokens, po_numbers).  Empty lists when nothing is found or pdftotext is not available
    (non-OCR; scanned OC PDFs yield no tokens).  Logs a warning on unexpected errors.
    """
    from email import policy
    from email.message import EmailMessage
    from email.parser import BytesParser

    tokens: list[str] = []
    po_numbers: list[str] = []
    try:
        msg = BytesParser(policy=policy.default).parsebytes(raw)
        if not isinstance(msg, EmailMessage):
            raise TypeError(f"unexpected parser result: {type(msg).__name__}")
        if msg.defects:
            raise ValueError(f"malformed EML: {type(msg.defects[0]).__name__}")
        for part in msg.walk():
            filename = part.get_filename()
            if not filename or not _is_oc_pdf(filename):
                continue
            data = part.get_payload(decode=True)
            if not isinstance(data, bytes) or not data:
                continue
            text = pdf_text(data)
            if not text:
                continue
            part_tokens, part_pos = _po_refs_from_text(text)
            for t in part_tokens:
                if t not in tokens:
                    tokens.append(t)
            for n in part_pos:
                if n not in po_numbers:
                    po_numbers.append(n)
    except Exception as exc:  # noqa: BLE001
        logger.warning("po reference reading skipped: %s", type(exc).__name__)
    return tokens, po_numbers


def record_po_refs(db: Any, source_record_id: str, raw: bytes) -> bool:
    """`po_refs:v1`: quote tokens and PO numbers from OC PDF attachments. Isolated — a failure is
    logged by class and swallowed, never the triage's.  Only written when at least one OC PDF
    attachment yielded a token or PO number — ordinary mail (no OC PDF) produces no row."""
    try:
        tokens, po_numbers = _po_refs_from_raw(raw)
        if not tokens and not po_numbers:
            return False
        db.record_po_refs(source_record_id, tokens, po_numbers)
        return True
    except Exception as exc:  # noqa: BLE001 - never let this optional reading break the triage
        logger.warning("po reference reading skipped: %s", type(exc).__name__)
        return False


def backfill_quote_refs(db: Any, store: EmlStore, source_record_ids: Sequence[str], *, apply: bool) -> dict[str, int]:
    """Emails captured before `quote_refs:v1` existed, read for the quote numbers their body names —
    nothing else: no triage, no model. A dry run reads and counts, writes nothing."""
    counts = {"found": len(source_record_ids), "with_numbers": 0, "recorded": 0, "skipped": 0}
    for sid in source_record_ids:
        loaded = db.load(sid)
        if loaded is None or not loaded[0].eml_storage_path:
            counts["skipped"] += 1
            continue
        try:
            mail = parse_mail(store.get(storage_key(loaded[0].eml_storage_path)))
        except Exception:  # noqa: BLE001 - one unreadable message is skipped, never fatal
            counts["skipped"] += 1
            continue
        tokens = read_quote_refs(mail.subject, mail.body)
        counts["with_numbers"] += 1 if tokens else 0
        if apply:
            db.record_quote_refs(sid, tokens)
            counts["recorded"] += 1
    return counts


_INSERT_FORWARD = """
insert into evidence.source_record (kind, dedupe_key, payload)
values ('forwarded_request', %s, %s)
on conflict (dedupe_key) do nothing
"""

#: `people-backfill`'s list: inbound, parsed, stored, of the last days; its latest triage reading
#: (any `triage:v<N>`, ruling R11) of a class a person writes; no `people:v1` yet.
_PEOPLE_PENDING = """
select sr.id::text
  from comms.message m
  join evidence.source_record sr on sr.dedupe_key = 'gmail_message:' || m.provider_message_id
 where sr.kind = 'gmail_message'
   and m.direction = 'inbound'
   and m.parse_status = 'parsed'
   and m.eml_storage_path is not null
   and m.internal_date >= now() - make_interval(days => %s)
   and exists (select 1 from (select a.value->>'class' as c from evidence.assertion a
                               where a.source_record_id = sr.id and a.kind = 'message_triage'
                                 and a.value_norm ~ '^triage:v[0-9]+$'
                               order by substring(a.value_norm from 9)::int desc
                               limit 1) latest
                where not (coalesce(latest.c, '') = any(%s)))
   and not exists (select 1 from evidence.assertion p
                    where p.source_record_id = sr.id and p.kind = 'message_triage' and p.value_norm = %s)
 order by m.internal_date desc
 limit %s
"""

_LATEST_TRIAGE_CLASS = """
select a.value->>'class'
  from evidence.assertion a
 where a.source_record_id = %s and a.kind = 'message_triage' and a.value_norm ~ '^triage:v[0-9]+$'
 order by substring(a.value_norm from 9)::int desc
 limit 1
"""

_QUOTE_REFS_PENDING = """
select sr.id::text
  from evidence.source_record sr
 where sr.kind = 'gmail_message'
   and sr.created_at > now() - make_interval(days => %s)
   and not exists (select 1 from evidence.assertion a
                    where a.source_record_id = sr.id and a.kind = 'message_triage' and a.value_norm = %s)
 order by sr.created_at desc
 limit %s
"""

_FORWARD_PENDING = """
select sr.id::text
  from evidence.source_record sr
 where sr.kind = 'gmail_message'
   and sr.created_at > now() - make_interval(days => %s)
   and coalesce(sr.payload->>'subject_raw', '') ~* '^\\s*((re|aw)\\s*:\\s*)*(rv|fw|fwd|tr|reenv(iado)?)\\s*:'
   and not exists (select 1 from evidence.source_record f
                    where f.dedupe_key = 'forwarded_request:' || sr.id::text)
 order by sr.created_at desc
 limit %s
"""

# Memory SQL ---------------------------------------------------------------

_LATEST_SUBJECT_MEMORY = """
select memory, version
  from evidence.subject_memory
 where subject_kind = %s and subject_id = %s::uuid
 order by version desc
 limit 1
"""

_INSERT_SUBJECT_MEMORY = """
insert into evidence.subject_memory
  (subject_kind, subject_id, version, memory, source_record_id, model)
values (%s, %s::uuid, %s, %s, %s, %s)
on conflict (subject_kind, subject_id, version) do nothing
"""

#: The case's requesting institution, and the person behind an address that wrote on the case (an
#: email contact point with a person, matched on the sender of a linked email). (2026-10-15: the first
#: version read a `contact_point.email_norm` column that does not exist.)
_OPPORTUNITY_ORG_AND_PERSON = """
select o.organization_id::text,
       (select cp.person_id::text
          from crm.contact_point cp
         where cp.kind = 'email' and cp.person_id is not null
           and cp.value_norm in (
               select lower(substring(sr.payload ->> 'sender' from '[^<>[:space:]]+@[^<>[:space:]]+'))
                 from crm.opportunity_evidence oe
                 join evidence.source_record sr on sr.id = oe.source_record_id
                where oe.opportunity_id = o.id and oe.unlinked_at is null)
         order by cp.created_at
         limit 1)
  from crm.opportunity o
 where o.id = %s::uuid
"""

_OPPORTUNITY_INSTITUTION_NAME = """
select org.name
  from crm.opportunity o
  join crm.organization org on org.id = o.organization_id
 where o.id = %s::uuid
"""

_OPPORTUNITY_QUOTE_NUMBERS = """
select q.quote_number
  from crm.quote q
  join crm.opportunity opp on opp.id = q.opportunity_id
 where q.opportunity_id = %s::uuid
"""

#: Business mail (a HUMAN_CLASSES triage reading) filed on an open case that has no memory yet, oldest
#: first, so each case's memory is written in the order its conversation happened. (2026-10-15: the
#: first version filtered on case_match decisions the matcher never writes, and its `like 'triage:v%'`
#: broke the placeholders — it never returned a row.)
_MEMORY_BACKFILL_PENDING = """
select sr.id::text
  from evidence.source_record sr
 where sr.kind = 'gmail_message'
   and sr.created_at > now() - make_interval(days => %(days)s)
   and exists (select 1 from evidence.assertion ao
                where ao.source_record_id = sr.id and ao.kind = 'message_triage'
                  and ao.value_norm ~ '^triage:v[0-9]+$' and ao.value->>'class' = any(%(human_classes)s))
   and exists (select 1 from crm.opportunity_evidence oe
                 join crm.opportunity o on o.id = oe.opportunity_id and o.closed_at is null
                                        and o.kind = 'sale'  -- case_kind.py: tenders are not summarised
                where oe.source_record_id = sr.id and oe.unlinked_at is null
                  and not exists (select 1 from evidence.subject_memory sm
                                   where sm.subject_kind = 'opportunity' and sm.subject_id = o.id))
 order by coalesce(sr.payload->>'sent_at', sr.created_at::text), sr.id
 limit %(limit)s
"""

# End memory SQL -----------------------------------------------------------


def backfill_forwards(db: Any, store: EmlStore, source_record_ids: Sequence[str], *, apply: bool) -> dict[str, int]:
    """The forwards of the last days, read again for who really asked — and nothing else: the
    triage is never re-run, no model is called. A dry run reads and counts, writes nothing."""
    counts = {"found": len(source_record_ids), "readable": 0, "recorded": 0, "skipped": 0}
    for sid in source_record_ids:
        loaded = db.load(sid)
        if loaded is None or not loaded[0].eml_storage_path:
            counts["skipped"] += 1
            continue
        try:
            raw = store.get(storage_key(loaded[0].eml_storage_path))
            mail = parse_mail(raw)
        except Exception:  # noqa: BLE001 - one unreadable message is skipped, never fatal
            counts["skipped"] += 1
            continue
        if not is_candidate(mail.sender, mail.subject, extra_senders=forward_extra_senders()):
            counts["skipped"] += 1
            continue
        if read_forward(raw) is None:
            counts["skipped"] += 1
            continue
        counts["readable"] += 1
        if apply and record_forward_reading(db, sid, raw, mail.sender, mail.subject):
            counts["recorded"] += 1
    return counts


def forward_reading_enabled() -> bool:
    """`ORIGENLAB_WORKER_FORWARD_READING` (default on): «0», «false» or «off» turns it off without a deploy."""
    return os.environ.get("ORIGENLAB_WORKER_FORWARD_READING", "on").strip().lower() not in ("0", "false", "off", "no")


def forward_extra_senders() -> set[str]:
    """`ORIGENLAB_WORKER_FORWARD_SENDERS`: Tatiana's other addresses, comma-separated (never in the repo)."""
    return {a.strip().lower() for a in os.environ.get("ORIGENLAB_WORKER_FORWARD_SENDERS", "").split(",") if a.strip()}


def record_forward_reading(db: Any, source_record_id: str, raw: bytes, sender: str | None, subject: str | None) -> bool:
    """Who really asked, for a request we forwarded to contacto@ (spec 2026-10-14). Runs after the
    triage has recorded its own reading and is isolated from it: whatever fails here — the reader,
    the write — is logged by class and swallowed, so capture, triage, quotes and Drive never wait on it."""
    if not forward_reading_enabled() or not is_candidate(sender, subject, extra_senders=forward_extra_senders()):
        return False
    try:
        reading = read_forward(raw)
        if reading is None:
            return False
        db.record_forward(source_record_id, reading.as_json())
        return True
    except Exception as exc:  # noqa: BLE001 - never let this optional reading break the triage
        logger.warning("forward reading skipped: %s", type(exc).__name__)
        return False


def record_memory_reading(
    db: Any,
    message: PendingMessage,
    match: Any | None,
    email_body: str,
    linked_cases: list[Any],
) -> None:
    """Update the memory for any case this email belongs to, plus its institution and person.

    Runs after the main triage recording, fully isolated: whatever fails here is logged by class
    and swallowed, so no downstream step (Drive, queue, worker loop) ever waits on it.
    The switch `ORIGENLAB_WORKER_MEMORY_ENABLED` is checked first; the default is off.
    """
    from origenlab_worker.memory import memory_enabled, memory_model, record_memory
    from origenlab_worker.triage_model import anthropic_reader

    if not memory_enabled():
        return
    try:
        model = memory_model()
        api_key = os.environ.get("ORIGENLAB_WORKER_ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            return
        reader = anthropic_reader(api_key)
        # Which cases to update: the one the matcher placed the email on, or all linked ones.
        if match is not None and getattr(match, "opportunity_id", None):
            case_ids = [match.opportunity_id]
        else:
            case_ids = [c.opportunity_id for c in linked_cases]
        for oid in case_ids:
            org_id, person_id = db.opportunity_org_and_person(oid)
            inst_name = db.opportunity_institution_name(oid)
            quote_nums = db.opportunity_quote_numbers(oid)
            stage = next((c.stage for c in linked_cases if c.opportunity_id == oid), None)
            record_memory(
                db, reader, message.source_record_id, email_body,
                opportunity_id=oid, organization_id=org_id, person_id=person_id,
                stage=stage, quote_numbers=quote_nums, institution_name=inst_name,
                model=model,
            )
    except Exception as exc:  # noqa: BLE001 - never let memory break the triage
        logger.warning("memory reading skipped: %s", type(exc).__name__)


class ModelUnavailable(RuntimeError):
    """The model call failed (network, rate limit, 5xx): nothing was recorded; the job retries."""


def is_permanent_api_error(exc: BaseException) -> bool:
    """A 4xx the same request will always get again (bad request, auth, not found): recorded as the
    reading's `model_state` so the sweep does not re-enqueue it forever. 408, 409 and 429 are
    transient, like 5xx and connection errors, and retried by the queue."""
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and 400 <= status < 500 and status not in (408, 409, 429)


@dataclass
class SweepCounts:
    found: int = 0
    outcomes: dict[str, int] = field(default_factory=dict)
    classes: dict[str, int] = field(default_factory=dict)
    model: dict[str, int] = field(default_factory=dict)

    def add(self, result: TriageResult) -> None:
        self.outcomes[result.outcome] = self.outcomes.get(result.outcome, 0) + 1
        if result.triage_class:
            self.classes[result.triage_class] = self.classes.get(result.triage_class, 0) + 1
        if result.model:
            self.model[result.model] = self.model.get(result.model, 0) + 1


__all__ = ["CASE_CARDS_SQL", "HUMAN_CLASSES", "case_row", "match_message", "DEFAULT_SINCE_DAYS", "OUTCOMES", "VALUE_NORM", "REQUESTER_VALUE_NORM", "PO_REFS_VALUE_NORM", "ModelSettings",
           "backfill_quote_refs", "record_quote_refs", "record_po_refs",
           "backfill_forwards", "record_forward_reading",
           "record_memory_reading",
           "ModelUnavailable", "is_permanent_api_error", "PendingMessage", "SweepCounts", "TriageDb",
           "TriageResult", "match_requester_organization", "mentions_for", "reading_value", "triage_one"]
