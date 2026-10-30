"""Marketing analytics — reach, blocks, responses, quote requests, cases and CRM coverage.

Read only, at read time, from the CRM's own facts: the send ledger (``outbound.send_attempt``,
every lane — the V1-lane imports included), the block list (``outbound.contact_control``), the
captured mail (``comms.message`` + participants) and its triage readings (``evidence.assertion``),
and the cases (``crm.opportunity`` and its links). Nothing is stored and nothing is estimated.

**One fact table, two readers.** :data:`FACTS_SQL` builds one row per counted thing — a
(metric, campaign, address, reference) — and both the figures (:func:`read_analytics`) and the
people behind each figure (:func:`read_people`) are that same CTE filtered, so a figure and the
list it opens agree by construction.

**The rules** (owner 2026-10-29, spec ``2026-10-29-marketing-analytics-design.md``):

* A *send* is a recipient's first accepted marketing attempt in a campaign.
* A *response* is an inbound message from a recipient's address within
  :data:`REPLY_WINDOW_DAYS` after a send, attributed to the most recent send that address had
  received before the message, so one reply never counts for two campaigns. Noise readings
  (bounce, auto-reply, unsubscribe, calendar, bulk, notification) are not responses.
* *Pidieron cotización* = responses the triage read as ``quote_request``.
* *Casos después* = cases linked to a recipient (by contact point, person, or the contact
  point's organization) created within the window after a send; each case counted once, for the
  latest send before it.
* A *bounce* / *baja* is an active block in the block list (address or domain), attributed to
  the latest send to that address at or before the block, else to the first send.
* Responses and quote requests are ``None`` — not 0 — for a campaign whose first send predates
  the earliest captured inbound mail: the mail of that period is not in the CRM to count.
"""

from __future__ import annotations

import re
from typing import Any, Literal

REPLY_WINDOW_DAYS = 14
#: Since when the CRM's mail capture is continuous (the catch-up from 25 Sep 2026; older mail in
#: ``comms.message`` is a scattered backfill). A mail figure for a campaign sent earlier would
#: count a handful of stray messages as if they were the replies: it is ``None`` instead.
MAIL_CAPTURE_FROM = "2026-09-25T00:00:00+00:00"
#: Triage classes that are never a person answering (``apps/worker`` ``NOISE_CLASSES`` plus the
#: routed tender alerts and our own outbound copies).
NOISE_CLASSES = ("bounce", "auto_reply", "calendar", "unsubscribe", "bulk", "notification", "empty",
                 "tender_notice", "outbound")
INTERNAL_DOMAINS = ("origenlab.cl",)

METRICS = ("sent", "new_people", "bounced", "baja", "responded", "quote_request", "cases_after", "ndr_unblocked")
#: Figures that need the captured mail to be measurable.
MAIL_METRICS = frozenset({"responded", "quote_request", "ndr_unblocked"})
SEGMENTS = ("sin_caso", "solicitada", "enviada", "conversacion", "pausa", "ganada", "perdida")
COVERAGE_COLUMNS = ("contacted", "not_contacted", "blocked")
OVERVIEW_METRICS = ("reached", "writable", "bounced", "baja", "with_open_case")

_BAJA = re.compile(r"unsubscribe|do_not_contact|baja|opt.?out", re.I)
_REBOTE = re.compile(r"bounce|invalid_address|no_such_user|access_denied|ndr|rebote", re.I)
#: The same two patterns, in PostgreSQL's dialect (`~*`), so the block category has one source.
_BAJA_SQL = "unsubscribe|do_not_contact|baja|opt.?out"
_REBOTE_SQL = "bounce|invalid_address|no_such_user|access_denied|ndr|rebote"


def block_category(source: str, reason: str) -> Literal["rebote", "baja", "otro"]:
    """What a block means for marketing: the address bounced, the person asked out, or other."""
    if source == "ndr_handler":
        return "rebote"
    if source == "unsubscribe_handler":
        return "baja"
    if _BAJA.search(reason or ""):
        return "baja"
    if _REBOTE.search(reason or ""):
        return "rebote"
    return "otro"


_CATEGORY_SQL = f"""
  case when cc.source = 'ndr_handler' then 'rebote'
       when cc.source = 'unsubscribe_handler' then 'baja'
       when cc.reason ~* '{_BAJA_SQL}' then 'baja'
       when cc.reason ~* '{_REBOTE_SQL}' then 'rebote'
       else 'otro' end"""

_NOISE = ", ".join(f"'{c}'" for c in NOISE_CLASSES)
_INTERNAL = ", ".join(f"'{d}'" for d in INTERNAL_DOMAINS)
_WINDOW = f"interval '{REPLY_WINDOW_DAYS} days'"

FACTS_SQL = f"""
with sends as (
  select a.campaign_id, a.address_norm, min(a.accepted_at) as sent_at
    from outbound.send_attempt a
   where a.purpose = 'marketing' and a.campaign_id is not null and a.accepted_at is not null
   group by 1, 2
),
seq as (
  select s.*, row_number() over (partition by s.address_norm order by s.sent_at, s.campaign_id) as nth
    from sends s
),
reached as (select distinct address_norm from sends),
blocks as (
  select cc.id, cc.scope, cc.value_norm, cc.created_at, cc.source, {_CATEGORY_SQL} as category
    from outbound.contact_control cc
   where cc.kind = 'block' and (cc.until_at is null or cc.until_at > now())
),
crm_addresses as (
  select distinct cp.value_norm as address_norm from crm.contact_point cp where cp.kind = 'email'
),
known as (select address_norm from reached union select address_norm from crm_addresses),
address_blocks as (
  select k.address_norm, b.category, b.created_at
    from known k join blocks b on b.scope = 'address' and b.value_norm = k.address_norm
  union all
  select k.address_norm, b.category, b.created_at
    from known k join blocks b on b.scope = 'domain'
     and (b.value_norm = split_part(k.address_norm, '@', 2)
          or right(split_part(k.address_norm, '@', 2), length(b.value_norm) + 1) = '.' || b.value_norm)
),
blocked as (
  select address_norm,
         case when bool_or(category = 'baja') then 'baja'
              when bool_or(category = 'rebote') then 'rebote' else 'otro' end as category,
         min(created_at) as blocked_at
    from address_blocks group by 1
),
block_campaign as (
  select distinct on (b.address_norm) b.address_norm, b.category, s.campaign_id
    from blocked b join seq s on s.address_norm = b.address_norm
   order by b.address_norm, (s.sent_at <= b.blocked_at) desc,
            case when s.sent_at <= b.blocked_at then s.sent_at end desc nulls last, s.sent_at
),
triage as (
  select distinct on (sr.dedupe_key) substr(sr.dedupe_key, 15) as provider_message_id, a.value->>'class' as cls
    from evidence.assertion a join evidence.source_record sr on sr.id = a.source_record_id
   where a.kind = 'message_triage' and a.value_norm ~ '^triage:v[0-9]+$' and sr.dedupe_key like 'gmail_message:%%'
   order by sr.dedupe_key, substr(a.value_norm, 9)::int desc
),
inbound as (
  select m.id as message_id, m.internal_date, m.provider_message_id, p.address_norm
    from comms.message m
    join comms.message_participant p on p.message_id = m.id and p.role = 'from'
    join reached r on r.address_norm = p.address_norm
   where m.direction = 'inbound'
),
responses as (
  select distinct on (i.message_id) s.campaign_id, i.address_norm, i.provider_message_id, t.cls
    from inbound i
    join seq s on s.address_norm = i.address_norm and s.sent_at < i.internal_date
    left join triage t on t.provider_message_id = i.provider_message_id
   where i.internal_date <= s.sent_at + {_WINDOW} and coalesce(t.cls, '') not in ({_NOISE})
   order by i.message_id, s.sent_at desc
),
entity_points as (
  select cp.id as contact_point_id, cp.value_norm as address_norm, cp.person_id, cp.organization_id,
         cp.status = 'active' as active,
         coalesce(cp.person_id::text, 'cp:' || cp.id::text) as entity
    from crm.contact_point cp
    left join crm.person pe on pe.id = cp.person_id
   where cp.kind = 'email' and pe.merged_into_person_id is null
),
-- When a case was asked for: the date of the email it was opened from. A case a backfill or an
-- import created later from an older email is not one a campaign produced; a case with any other
-- origin (an import manifest) has no request date and is never «después».
opportunities as (
  select o.id, o.organization_id, o.stage,
         case when o.origin_source_record_id is null then o.created_at else m.internal_date end as requested_at
    from crm.opportunity o
    left join evidence.source_record sr on sr.id = o.origin_source_record_id
    left join comms.message m on sr.dedupe_key like 'gmail_message:%%'
                              and m.provider_message_id = substr(sr.dedupe_key, 15)
),
-- The email threads of each case (the messages and records linked to it, and the one it was
-- opened from), and every outside address that wrote or was written to in them.
case_threads as (
  select distinct oe.opportunity_id, m.provider_thread_id
    from crm.opportunity_evidence oe
    join comms.message m on m.id = oe.message_id
   where m.provider_thread_id is not null
  union
  select oe.opportunity_id, m.provider_thread_id
    from crm.opportunity_evidence oe
    join evidence.source_record sr on sr.id = oe.source_record_id and sr.dedupe_key like 'gmail_message:%%'
    join comms.message m on m.provider_message_id = substr(sr.dedupe_key, 15)
   where m.provider_thread_id is not null
  union
  select o.id, m.provider_thread_id
    from crm.opportunity o
    join evidence.source_record sr on sr.id = o.origin_source_record_id and sr.dedupe_key like 'gmail_message:%%'
    join comms.message m on m.provider_message_id = substr(sr.dedupe_key, 15)
   where m.provider_thread_id is not null
),
case_thread_people as (
  select distinct ct.opportunity_id, p.address_norm
    from case_threads ct
    join comms.message m on m.provider_thread_id = ct.provider_thread_id
    join comms.message_participant p on p.message_id = m.id and p.role in ('from', 'to', 'cc')
   where split_part(p.address_norm, '@', 2) not in ({_INTERNAL})
     and p.address_norm not like 'mailer-daemon@%%' and p.address_norm not like 'postmaster@%%'
),
-- A person «has» a case when they are on it or in its email threads (coverage, «Con caso abierto»).
person_case_links as (
  select o.id as opportunity_id, ep.entity, ep.address_norm, o.requested_at, o.stage
    from opportunities o
    join crm.opportunity_participant op on op.opportunity_id = o.id
    join entity_points ep on ep.contact_point_id = op.contact_point_id
  union
  select o.id, ep.entity, ep.address_norm, o.requested_at, o.stage
    from opportunities o
    join crm.opportunity_participant op on op.opportunity_id = o.id
    join entity_points ep on ep.person_id = op.person_id
  union
  select o.id, ep.entity, ep.address_norm, o.requested_at, o.stage
    from opportunities o
    join case_thread_people tp on tp.opportunity_id = o.id
    join entity_points ep on ep.address_norm = tp.address_norm
),
-- A campaign «produced» a case when anyone at the institution asked (owner decision 3, «Casos después»).
case_links as (
  select * from person_case_links
  union
  select o.id, ep.entity, ep.address_norm, o.requested_at, o.stage
    from opportunities o join entity_points ep on ep.organization_id = o.organization_id
  union
  select o.id, ep.entity, ep.address_norm, o.requested_at, o.stage
    from opportunities o
    join crm.affiliation af on af.organization_id = o.organization_id
    join entity_points ep on ep.person_id = af.person_id
  union
  select o.id, ep.entity, ep.address_norm, o.requested_at, o.stage
    from opportunities o
    join crm.opportunity_organization oo on oo.opportunity_id = o.id
         and oo.role not in ('supplier', 'manufacturer') and (oo.valid_to is null or oo.valid_to > current_date)
    join entity_points ep on ep.organization_id = oo.organization_id
),
cases_after as (
  select distinct on (cl.opportunity_id) s.campaign_id, cl.address_norm, cl.opportunity_id
    from case_links cl
    join seq s on s.address_norm = cl.address_norm and s.sent_at < cl.requested_at
                and cl.requested_at <= s.sent_at + {_WINDOW}
   order by cl.opportunity_id, s.sent_at desc
),
paused as (
  select t.opportunity_id from crm.task t where t.status = 'open'
   group by 1 having min(t.due_at) > now()
),
campaign_threads as (
  select distinct a.campaign_id, a.address_norm, m.provider_thread_id
    from outbound.send_attempt a
    join comms.message m on m.mailbox_id = a.mailbox_id and m.provider_message_id = a.provider_message_id
   where a.purpose = 'marketing' and a.accepted_at is not null and m.provider_thread_id is not null
),
delivery as (
  select substr(sr.dedupe_key, 15) as provider_message_id, d.value->>'block_reason' as block_reason
    from evidence.assertion d join evidence.source_record sr on sr.id = d.source_record_id
   where d.kind = 'delivery_failure' and d.value_norm = 'delivery:v2' and sr.dedupe_key like 'gmail_message:%%'
),
campaign_ndrs as (
  select distinct ct.campaign_id, ct.address_norm, (dl.block_reason is not null) as permanent
    from campaign_threads ct
    join comms.message m on m.provider_thread_id = ct.provider_thread_id and m.direction = 'inbound'
    join comms.message_participant p on p.message_id = m.id and p.role = 'from'
    left join triage t on t.provider_message_id = m.provider_message_id
    left join delivery dl on dl.provider_message_id = m.provider_message_id
   where (p.address_norm like 'mailer-daemon@%%' or p.address_norm like 'postmaster@%%' or t.cls = 'bounce')
     and not exists (select 1 from blocked b where b.address_norm = ct.address_norm)
),
-- A real miss: the worker read the notice as permanent and the address is still not blocked.
-- Temporary notices (full mailbox, delay, …) are never blocked on purpose and are counted apart.
ndr_unblocked as (select distinct campaign_id, address_norm from campaign_ndrs where permanent),
facts as (
  select 'sent' as metric, campaign_id, address_norm, null::uuid as opportunity_id, null::text as message_ref from sends
  union all select 'new_people', campaign_id, address_norm, null, null from seq where nth = 1
  union all select 'bounced', campaign_id, address_norm, null, null from block_campaign where category = 'rebote'
  union all select 'baja', campaign_id, address_norm, null, null from block_campaign where category = 'baja'
  union all select distinct 'responded', campaign_id, address_norm, null::uuid, null::text from responses
  union all select distinct 'quote_request', campaign_id, address_norm, null::uuid, null::text from responses
             where cls = 'quote_request'
  union all select 'cases_after', campaign_id, address_norm, opportunity_id, null from cases_after
  union all select 'ndr_unblocked', campaign_id, address_norm, null, null from ndr_unblocked
),
-- CRM coverage: every email-holding person (or person-less contact point), suppliers and our own
-- domain left out.
supplier_orgs as (
  select o.id from crm.organization o where o.kind in ('supplier', 'manufacturer')
  union select organization_id from crm.organization_relationship
         where role in ('supplier', 'manufacturer') and (valid_to is null or valid_to > current_date)
  union select organization_id from crm.opportunity_organization
         where role in ('supplier', 'manufacturer') and (valid_to is null or valid_to > current_date)
),
supplier_domains as (
  select d.domain_norm from crm.organization_domain d
   where d.scope = 'exclusive' and d.removed_at is null and d.organization_id in (select id from supplier_orgs)
),
coverage_points as (
  select ep.* from entity_points ep
   where ep.active and split_part(ep.address_norm, '@', 2) not in ({_INTERNAL})
     and (ep.organization_id is null or ep.organization_id not in (select id from supplier_orgs))
     and split_part(ep.address_norm, '@', 2) not in (select domain_norm from supplier_domains)
),
entity_state as (
  select cp.entity,
         bool_or(cp.address_norm in (select address_norm from reached)) as contacted,
         bool_and(cp.address_norm in (select address_norm from blocked)) as all_blocked,
         min(cp.address_norm) as address_norm
    from coverage_points cp group by 1
),
entity_cases as (
  select distinct on (cl.entity) cl.entity, cl.stage, (p.opportunity_id is not null) as paused
    from person_case_links cl left join paused p on p.opportunity_id = cl.opportunity_id
   order by cl.entity, (cl.stage not in ('won', 'lost', 'abandoned')) desc,
            case cl.stage when 'negotiating' then 4 when 'quoting' then 3 when 'won' then 2 else 1 end desc,
            cl.requested_at desc nulls last
),
coverage as (
  select es.entity, es.address_norm,
         case when ec.stage is null then 'sin_caso'
              when ec.stage in ('won') then 'ganada'
              when ec.stage in ('lost', 'abandoned') then 'perdida'
              when ec.paused then 'pausa'
              when ec.stage = 'negotiating' then 'conversacion'
              when ec.stage = 'quoting' then 'enviada'
              else 'solicitada' end as segment,
         case when es.contacted then 'contacted' when es.all_blocked then 'blocked' else 'not_contacted' end as col
    from entity_state es left join entity_cases ec on ec.entity = es.entity
)
"""

_ANALYTICS_SQL = FACTS_SQL + """
select json_build_object(
  'campaign_metrics', (select coalesce(json_agg(x), '[]'::json) from (
      select metric, campaign_id::text as campaign_id, count(*) as n from facts group by 1, 2) x),
  'first_sent', (select coalesce(json_object_agg(campaign_id::text, first), '{}'::json) from (
      select campaign_id, min(sent_at) as first from sends group by 1) f),
  'mail_from', (select min(internal_date) from comms.message where direction = 'inbound'),
  'reached', (select count(*) from reached),
  'blocked_reached', (select json_object_agg(category, n) from (
      select b.category, count(*) as n from blocked b join reached r using (address_norm) group by 1) z),
  'with_open_case', (select count(distinct cl.address_norm) from person_case_links cl join reached r using (address_norm)
                      where cl.stage not in ('won', 'lost', 'abandoned')),
  'soft_bounces', (select count(distinct address_norm) from campaign_ndrs where not permanent
                    and address_norm not in (select address_norm from ndr_unblocked)),
  'blocks_by_kind', (select coalesce(json_agg(k), '[]'::json) from (
      select case when b.source = 'ndr_handler' then coalesce(ev.block_reason, 'no_such_user')
                  when b.category = 'baja' then 'baja'
                  when b.category = 'rebote' and b.source in ('wave1a_suppression', 'wave1b_block') then 'rebote_historico'
                  when b.category = 'rebote' then 'rebote_revisado'
                  else 'otro' end as kind,
             count(*) as n
        from blocks b
        left join lateral (
          select e.payload->>'block_reason' as block_reason from crm.domain_event e
           where e.aggregate_kind = 'contact_control' and e.aggregate_id = b.id
             and e.event_type = 'contact_control.added'
           order by e.seq limit 1) ev on true
       group by 1) k),
  'blocks_by_week', (select coalesce(json_agg(w order by w.week), '[]'::json) from (
      select date_trunc('week', created_at)::date as week,
             count(*) filter (where category = 'rebote') as rebote,
             count(*) filter (where category = 'baja') as baja,
             count(*) filter (where category = 'otro') as otro
        from blocks group by 1) w),
  'coverage', (select coalesce(json_agg(c), '[]'::json) from (
      select segment, col, count(*) as n from coverage group by 1, 2) c)
)
"""


def _mail_measurable(first_sent: Any, mail_from: Any) -> bool:
    """Whether the captured mail covers a campaign's whole reply window."""
    from datetime import datetime

    if first_sent is None or mail_from is None:
        return False
    return mail_from <= first_sent and datetime.fromisoformat(MAIL_CAPTURE_FROM) <= first_sent


def read_analytics(cur: Any) -> dict[str, Any]:
    """Every figure the Marketing page shows. ``None`` = not measurable, never 0."""
    from datetime import datetime

    cur.execute(_ANALYTICS_SQL, {})  # params given so `%%` reads as `%` here as in read_people
    raw = cur.fetchone()[0]
    mail_from = raw["mail_from"]

    def _ts(v: Any) -> datetime | None:
        return None if v is None else datetime.fromisoformat(str(v))

    mf = _ts(mail_from)
    campaigns: dict[str, dict[str, int | None]] = {}
    for cid, first in (raw["first_sent"] or {}).items():
        measurable = _mail_measurable(_ts(first), mf)
        campaigns[cid] = {m: (0 if (m not in MAIL_METRICS or measurable) else None) for m in METRICS}
    for row in raw["campaign_metrics"]:
        figures = campaigns.setdefault(row["campaign_id"], {m: 0 for m in METRICS})
        if figures.get(row["metric"]) is not None:
            figures[row["metric"]] = int(row["n"])
    blocked = raw["blocked_reached"] or {}
    reached = int(raw["reached"])
    coverage: dict[str, dict[str, int]] = {s: {"people": 0, **{c: 0 for c in COVERAGE_COLUMNS}} for s in SEGMENTS}
    for row in raw["coverage"]:
        coverage[row["segment"]][row["col"]] += int(row["n"])
        coverage[row["segment"]]["people"] += int(row["n"])
    ndr = sum(f["ndr_unblocked"] or 0 for f in campaigns.values())
    return {
        "reply_window_days": REPLY_WINDOW_DAYS,
        "overview": {
            "reached": reached,
            "writable": reached - sum(int(n) for n in blocked.values()),
            "bounced": int(blocked.get("rebote", 0)),
            "baja": int(blocked.get("baja", 0)),
            "other_blocks": int(blocked.get("otro", 0)),
            "with_open_case": int(raw["with_open_case"]),
        },
        "campaigns": campaigns,
        "coverage": [{"segment": s, **coverage[s]} for s in SEGMENTS],
        "alerts": {"ndr_unblocked": ndr, "soft_bounces": int(raw["soft_bounces"])},
        "blocks": {
            "by_kind": [{"kind": k["kind"], "n": int(k["n"])} for k in raw["blocks_by_kind"]],
            "by_week": [{"week": w["week"], "rebote": int(w["rebote"]), "baja": int(w["baja"]), "otro": int(w["otro"])}
                        for w in raw["blocks_by_week"]],
        },
        "mail_captured_from": None if mf is None else mf.isoformat(),
    }


_PEOPLE_SELECT = """
, rows (address_norm, opportunity_id) as ({inner})
select r.address_norm, r.opportunity_id::text,
       pe.id::text as person_id, coalesce(pe.display_name, cp.value_display) as name, org.name as organization,
       o.stage as case_stage, o.title as case_title, count(*) over () as total
  from rows r
  left join crm.contact_point cp on cp.kind = 'email' and cp.value_norm = r.address_norm
  left join crm.person pe on pe.id = cp.person_id
  left join crm.organization org on org.id = coalesce(cp.organization_id, (select organization_id from crm.opportunity where id = r.opportunity_id))
  left join crm.opportunity o on o.id = r.opportunity_id
 order by name nulls last, r.opportunity_id
 limit %(limit)s offset %(offset)s
"""


def _campaign_mail_measurable(cur: Any, campaign_id: str) -> bool:
    cur.execute(
        "select (select min(accepted_at) from outbound.send_attempt where campaign_id = %s::uuid "
        "and purpose = 'marketing' and accepted_at is not null), "
        "(select min(internal_date) from comms.message where direction = 'inbound')",
        (campaign_id,),
    )
    first, mail_from = cur.fetchone()
    return _mail_measurable(first, mail_from)


class UnknownFigure(ValueError):
    """The metric, campaign or segment asked for is not one this module computes."""


def read_people(cur: Any, *, metric: str | None = None, campaign_id: str | None = None,
                segment: str | None = None, column: str | None = None,
                limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """The people behind one figure — the same rows the figure counts.

    Either ``metric`` (optionally per ``campaign_id``; an overview metric without it) or a
    coverage ``segment`` (optionally one ``column``).
    """
    params: dict[str, Any] = {"limit": max(1, min(int(limit), 200)), "offset": max(0, int(offset))}
    if segment is not None:
        if segment not in SEGMENTS and segment != "total":
            raise UnknownFigure(f"unknown segment {segment!r}")
        if column is not None and column not in COVERAGE_COLUMNS:
            raise UnknownFigure(f"unknown column {column!r}")
        where = []
        if segment != "total":
            where.append("segment = %(segment)s")
            params["segment"] = segment
        if column is not None:
            where.append("col = %(column)s")
            params["column"] = column
        inner = ("select address_norm, null::uuid as opportunity_id from coverage"
                 + (" where " + " and ".join(where) if where else ""))
    elif metric in METRICS and campaign_id is not None:
        if metric in MAIL_METRICS and not _campaign_mail_measurable(cur, campaign_id):
            return {"total": None, "rows": [], "limit": params["limit"], "offset": params["offset"]}
        params.update(metric=metric, campaign_id=campaign_id)
        inner = ("select address_norm, opportunity_id from facts "
                 "where metric = %(metric)s and campaign_id = %(campaign_id)s::uuid")
    elif metric in OVERVIEW_METRICS and campaign_id is None:
        inner = {
            "reached": "select address_norm, null::uuid as opportunity_id from reached",
            "writable": "select address_norm, null::uuid from reached where address_norm not in (select address_norm from blocked)",
            "bounced": "select address_norm, null::uuid from blocked join reached using (address_norm) where category = 'rebote'",
            "baja": "select address_norm, null::uuid from blocked join reached using (address_norm) where category = 'baja'",
            "with_open_case": "select distinct cl.address_norm, null::uuid from person_case_links cl join reached using (address_norm) "
                              "where cl.stage not in ('won', 'lost', 'abandoned')",
        }[metric]
    else:
        raise UnknownFigure(f"unknown figure metric={metric!r} campaign_id={campaign_id!r}")
    cur.execute(FACTS_SQL + _PEOPLE_SELECT.format(inner=inner), params)
    rows = cur.fetchall()
    cols = [d.name for d in cur.description]
    out = [dict(zip(cols, r, strict=True)) for r in rows]
    total = int(out[0]["total"]) if out else 0
    for r in out:
        r.pop("total")
    return {"total": total, "rows": out, "limit": params["limit"], "offset": params["offset"]}
