"""Quote reads: the Cotizador workspace, the numbering box, the live preview, kit suggestions.

Read-only transactions as the runtime role, like `V2CatalogReads`. Nothing here knows about roles:
the routes refuse a viewer before calling in (`role_may_not_view_costs`), since every answer
carries costs and margins.

**Preview** (spec 2026-10-15 §5, R-A4) prices a draft exactly as `save-quote-draft` would —
`inputs.resolve_draft` + `quoting.engine.compute` + `inputs.result_json` — and writes nothing: no
receipt, no event, no row. Given a `revision_id` it prices with that revision's tax rate, as the
save does, so the preview's totals are the ones the save will store. One read transaction on one
connection, five round trips: the set-up, the parameters with the revision's tax rate (one
pipeline), and the four statements `resolve_draft` needs (products, observations, suppliers,
stored rates).

**Exchange rates.** `resolve_draft` reads the stored rates on this transaction's cursor. Only a
currency with nothing stored in the window reaches the injected resolver — here the full
`PricingFx.rate`, which asks a provider (HTTP, up to 5 s a provider) and stores its answer on its
own connection (once per currency and day). That never happens inside a read transaction: inside
one, a miss is only noted (`_FxPrefetch`) and the pass priced with a placeholder is thrown away;
the transaction closes, the resolver is asked for every noted currency with no connection held by
this request, and the read runs once more in a fresh transaction with those answers — at most one
retry; a currency still without a rate then is `fx_unavailable`. Opening a draft or previewing one
therefore warms today's rate before the operator saves; the commands, on a miss, refuse
(`inputs.no_provider`).

**Workspace** (`GET /v2/quotes/{id}`): the quote, its case (institution and RUT, the current
participants, `contacts` — whom the quote can go to: the participants, then the people on the
case's emails and forwarded requests, never OrigenLab's own addresses nor anything of Labdelivery
(`recipient_candidates`) —, «Lo que pidió» from the case memory), every revision with its printed label, and the
current one — a draft comes back as the `QuoteDraft` it was saved from plus its live computation
(today's rates and parameters), what was saved (`saved`: the stored totals and each item line's
stored prices — what approval freezes, since it prices the saved lines as they are) and
`needs_saving`, true when the live figures differ from the saved ones (or nothing was priced yet);
an issued revision as its stored totals, lines and frozen `party_snapshot`. Suggestions are only
offered, never added: the products the case's emails mention (`product_mention` assertions) and the
models that usually go with the principal (`kit_suggestions`, R-A5). The principal's quoted-price
history comes from the catalog reads. `params` is the company's preferences as the rail applies
them (`inputs.quote_preferences`: default markups, margin band, price-deviation warning).

**The module's list** (`GET /v2/quotes`): one row per quote at its current revision (`_current`'s
rule, applied to every quote in one statement), newest first by a keyset cursor, with the count of
each status under the same search — two statements in one pipeline, whatever the page holds.
Imported quotes are listed too (`origin` `historical_import`).

**«Rentabilidad»** (`GET /v2/quotes/economics?from=YYYY-MM&to=YYYY-MM`): every quote made in the
Cotizador at its current revision (the list's rule; void left out, a draft flagged), each with its
sale, product cost, import costs and gross margin priced from its stored lines as approval prices
them (`economics.py`), and the totals by month — three statements in one pipeline. The workspace's
`current.economics` is the same breakdown for one quote: a draft's from its live computation, an
issued revision's as frozen, none for an imported quote.

**Client suggestions** (`GET /v2/quotes/clients`, plan ruling R3): institutions and people a new
quote can be for, each with its open cases — never a supplier or manufacturer (a current
`organization_relationship` in those roles), an archived or merged institution, an inactive address,
nor OrigenLab's own or Labdelivery's addresses (`recipients.not_a_recipient`).
"""
from __future__ import annotations

import base64
import binascii
import datetime as dt
import json
import re
import uuid
from email.utils import getaddresses
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from origenlab_api.quoting.engine import EngineError, compute
from origenlab_api.v2.catalog.fx import FxUnavailable
from origenlab_api.v2.catalog import aliases
from origenlab_api.v2.catalog.keys import is_labdelivery, model_key
from origenlab_api.v2.case_commands import SUPPLIER_RELATIONSHIP_ROLES
from origenlab_api.v2.catalog.reads import V2CatalogReads, _like_escape, _plain
from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.quotes.draft import PreviewQuoteBody, QuoteDraft
from origenlab_api.v2.quotes.economics import frozen_economics, frozen_line_economics, month_range, report
from origenlab_api.v2.quotes.inputs import (
    PARAMETERS_SQL,
    FxResolver,
    draft_from_rows,
    parameters_from,
    quote_preferences,
    resolve_draft,
    result_json,
)
from origenlab_api.v2.mail_rules import FREE_MAIL_DOMAINS, WEB_FORM_REPLY_TO_SQL, mail_direction, web_form_enquirer
from origenlab_api.v2.quote_documents import QUOTE_DOCUMENT_COLUMNS, quote_contents, quote_document_join
from origenlab_api.v2.quotes.numbering import SANTIAGO, numbering_status, revision_label
from origenlab_api.v2.tenders.bidder import current_bidder_profile, format_rut
from origenlab_api.v2.quotes.recipients import PERSON_ADDRESS_ORDER, not_a_recipient

#: R-A5: a model is suggested beside the principal when it is on at least this share of the past
#: quote documents that carry the principal, and the principal was quoted at least `KIT_MIN_DOCUMENTS` times.
KIT_MIN_SHARE = Decimal("0.30")
KIT_MIN_DOCUMENTS = 3
KIT_LIMIT = 6
MENTION_LIMIT = 12
CONTACT_LIMIT = 20
PRICE_HISTORY_LIMIT = 20
#: The module's list: the statuses a row can show (`in_review` is listed as a draft), its page size
#: bound, and the item descriptions a row shows.
QUOTE_LIST_STATUSES = ("draft", "approved", "sent", "void")
QUOTE_LIST_MAX = 100
QUOTE_LIST_ITEMS = 3
#: Client suggestions: at most this many institutions and this many people, each with at most
#: `CLIENT_OPEN_CASES` open cases; the database is asked for `CLIENT_FETCH` of each so the address
#: rules applied here (Labdelivery, OrigenLab's own) still leave a full list.
CLIENT_LIMIT = 8
CLIENT_FETCH = 24
CLIENT_OPEN_CASES = 10

# The documents that quote the principal (under its model or an unambiguous alias), then every other
# model on them with the share of those documents it is on; each with the active catalog product of
# that key (the survivor of a merge) when one exists — by its model_key first, else the one product
# an active alias names (ambiguous: none). Optional lines count: an accessory offered as optional is
# exactly a kit item.
_KIT_SQL = """
with keys as ({line_keys}), docs as (
  select distinct l.source_record_id
    from evidence.document_line l
    join evidence.source_record s on s.id = l.source_record_id
   where l.model_key in (select k from keys) and s.kind = 'quote_document'
), total as (select count(*) as n from docs)
select k.model_key, k.n, total.n as total, k.description, p.id::text as product_id,
       coalesce(p.name_es, p.name, p.model_number) as product_name
  from (select l.model_key, count(distinct l.source_record_id) as n,
               min(coalesce(nullif(btrim(l.model), ''), l.description)) as description
          from evidence.document_line l join docs d on d.source_record_id = l.source_record_id
         where l.model_key is not null and not exists (select 1 from keys where keys.k = l.model_key)
         group by l.model_key) k
  cross join total
  left join lateral (
    select c.* from catalog.product c
     where c.active and c.merged_into_product_id is null
       and (c.model_key = k.model_key or c.id = {alias_root})
     order by (c.model_key = k.model_key) desc, c.created_at, c.id limit 1) p on true
 where total.n >= %(min_docs)s and k.n >= %(min_share)s * total.n
 order by k.n desc, k.model_key
 limit %(limit)s
""".format(line_keys=aliases.line_keys_of_key("%(key)s"), alias_root=aliases.unique_root_named_by("k.model_key"))

# The quote, its case, and the case's institution (through a merge) with its first RUT.
_QUOTE_SQL = """
select q.id::text as id, q.quote_number, q.opportunity_id::text as opportunity_id,
       o.title, o.stage, o.closed_at, o.version as case_version, o.kind as case_kind,
       (select n.codigo_externo from crm.opportunity_evidence e
          join procurement.notice n on n.id = e.notice_id
         where e.opportunity_id = o.id and e.relation = 'origin' and e.unlinked_at is null
         order by e.linked_at desc limit 1) as tender_code,
       inst.id::text as institution_id, inst.name as institution_name,
       (select e.value_norm from crm.external_identifier e
         where e.organization_id = inst.id and e.scheme = 'rut' order by e.created_at, e.id limit 1) as rut
  from crm.quote q
  join crm.opportunity o on o.id = q.opportunity_id
  left join crm.organization m on m.id = o.organization_id
  left join crm.organization inst on inst.id = coalesce(m.merged_into_organization_id, m.id)
 where q.id = %s::uuid
"""

_REVISIONS_SQL = """
select r.*
  from crm.quote_revision r
 where r.quote_id = %s::uuid
 order by r.revision_no
"""

# The current revision's lines with the model key of each line's product (for the principal's history).
_LINES_SQL = """
select l.*, p.model_key as product_model_key
  from crm.quote_line l
  left join catalog.product p on p.id = l.product_id
 where l.quote_revision_id = (
   select r.id from crm.quote_revision r where r.quote_id = %s::uuid
    order by (r.status in ('draft', 'in_review')) desc, (r.status <> 'void') desc, r.revision_no desc
    limit 1)
 order by l.line_no
"""

# The case's current participants: the participant's own email address, else the person's
# (`recipients.PERSON_ADDRESS_ORDER`) — the same address approval prints — and the same name
# (`commands._RECIPIENT_SQL`).
_PARTICIPANTS_SQL = f"""
select p.id::text as id, p.person_id::text as person_id, coalesce(p.contact_point_id, cp.id)::text as contact_point_id,
       coalesce(pe.display_name, cpe.display_name, case when p.person_id is null then p.note end,
                cp.value_display) as name,
       cp.value_norm as email, p.is_primary as primary, p.role
  from crm.opportunity_participant p
  left join crm.person pe on pe.id = p.person_id
  left join lateral (
    select c.id, c.value_norm, c.value_display, c.person_id from crm.contact_point c
     where c.kind = 'email'
       and (c.id = p.contact_point_id or (p.contact_point_id is null and c.person_id = p.person_id))
     order by {PERSON_ADDRESS_ORDER}
     limit 1) cp on true
  left join crm.person cpe on cpe.id = cp.person_id
 where p.opportunity_id = %s::uuid and p.valid_to is null
 order by p.is_primary desc, (p.role = 'quote_recipient') desc, p.valid_from, p.created_at, p.id
"""

# The people on the case's emails, for «¿A quién va dirigida?»: every live captured email linked to
# the case, of a quote revision recorded from one, or on the thread of either (the threads the
# pipeline's contact reads, crm_workspace._SQL_PIPELINE_CONTACT), with who sent it and to whom, and
# the customer of each forwarded request the case came from or holds (mail_rules_repository
# ._FORWARD_PEOPLE_SQL). Only a live email (neither quarantined nor rejected) names anyone or brings
# its thread in. `at` is an instant: the Date header when it is ISO 8601, else when it was captured.
# Python decides the direction and parses the headers (`email_people`); a web-quote-form submission
# (`web_form_reply_to`) names its Reply-To, the visitor.
_CASE_PEOPLE_SQL = r"""
with linked as (
    select oe.source_record_id as id from crm.opportunity_evidence oe
     where oe.opportunity_id = %(case)s::uuid and oe.unlinked_at is null and oe.source_record_id is not null
    union
    select qr.origin_source_record_id from crm.quote_revision qr join crm.quote q on q.id = qr.quote_id
     where q.opportunity_id = %(case)s::uuid and qr.origin_source_record_id is not null
), threads as (
    select distinct sr.payload->>'gmail_thread_id' as thread_id
      from linked l join evidence.source_record sr on sr.id = l.id
     where sr.kind = 'gmail_message' and sr.payload->>'gmail_thread_id' is not null
       and not sr.is_quarantined and sr.review_status <> 'rejected'
), emails as (
    select l.id from linked l
    union
    select sr.id from evidence.source_record sr join threads t on sr.payload->>'gmail_thread_id' = t.thread_id
     where sr.kind = 'gmail_message'
)
select 'email' as kind, sr.payload->>'sender' as sender, sr.payload->>'recipients' as recipients,
       sr.payload->>'direction_hint' as direction_hint,
       (select cm.direction from comms.message cm
         where cm.provider_message_id = sr.payload->>'gmail_message_id'
         order by cm.created_at limit 1) as comms_direction,
       """ + WEB_FORM_REPLY_TO_SQL + r""" as web_form_reply_to,
       null as name, null as origin,
       case when sr.payload->>'sent_at' ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}'
            then (sr.payload->>'sent_at')::timestamptz else sr.acquired_at end as at
  from emails e join evidence.source_record sr on sr.id = e.id
 where sr.kind = 'gmail_message' and not sr.is_quarantined and sr.review_status <> 'rejected'
union all
select 'forwarded_request', null, f.payload->>'original_email', null, null, null, f.payload->>'original_name',
       f.payload->>'origin',
       case when f.payload->>'sent_at' ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}'
            then (f.payload->>'sent_at')::timestamptz else f.acquired_at end
  from evidence.source_record f
  join crm.opportunity o on o.id = %(case)s::uuid
 where f.kind = 'forwarded_request' and f.review_status = 'promoted' and not f.is_quarantined
   and (o.origin_source_record_id = f.id
        or exists (select 1 from crm.opportunity_evidence oe
                    where oe.opportunity_id = o.id and oe.unlinked_at is null
                      and (oe.source_record_id = f.id
                           or oe.source_record_id::text = f.payload->>'forwarded_message_id')))
"""

# What the CRM already holds for those addresses: the contact point, whether it is active, and the
# person it belongs to. A deactivated address is not offered: the save would refuse it.
_KNOWN_ADDRESSES_SQL = """
select c.value_norm as email, c.id::text as contact_point_id, c.person_id::text as person_id,
       pe.display_name as name, c.status
  from crm.contact_point c
  left join crm.person pe on pe.id = c.person_id
 where c.kind = 'email' and c.value_norm = any(%s)
"""

_MEMORY_SQL = """
select sm.memory->>'summary' as summary
  from evidence.subject_memory sm
 where sm.subject_kind = 'opportunity' and sm.subject_id = %s::uuid
 order by sm.version desc
 limit 1
"""

# What the case's live emails name: one row per normalised model, with the catalog product it
# matched (the survivor of a merge, when active — as for kits) when there is one. A link a person removed, or a rejected or
# quarantined record, names nothing.
_MENTIONS_SQL = """
select distinct on (a.value_norm)
       a.value_norm, a.value->>'model' as model, a.value->>'description' as description,
       p.id::text as product_id, coalesce(p.name_es, p.name, p.model_number) as product_name,
       p.model_number as product_model
  from crm.opportunity_evidence oe
  join evidence.source_record s on s.id = oe.source_record_id
  join evidence.assertion a on a.source_record_id = s.id and a.kind = 'product_mention'
  left join catalog.product m
         on m.id = case when a.value->>'catalog_product_id'
                             ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
                        then (a.value->>'catalog_product_id')::uuid end
  left join catalog.product p on p.id = coalesce(m.merged_into_product_id, m.id)
        and p.active and p.merged_into_product_id is null
 where oe.opportunity_id = %s::uuid and oe.unlinked_at is null
   and not s.is_quarantined and s.review_status <> 'rejected'
 order by a.value_norm, (p.id is not null) desc, a.created_at
"""

# -- the Cotizador module's list (`GET /v2/quotes`)
#
# One row per quote at its current revision — the open draft, else the newest revision not
# voided, else the newest: `_current`'s order, chosen here for every quote at once with
# `distinct on`. Its institution through a merge, as the workspace. The search matches the
# number, the institution's name, the case's title and the current revision's line
# descriptions — its own, or those read from its PDF (`quote_documents`) — (ILIKE, wildcards
# escaped). `bucket` is the status a row is counted and filtered under: an `in_review` revision is
# still a draft.
_LIST_BASE_SQL = """
with cur as (
  select distinct on (r.quote_id) r.quote_id, r.id as revision_id, r.revision_no, r.status, r.origin,
         r.grand_total, r.quote_currency, r.pdf_sha256, r.updated_at
    from crm.quote_revision r
   order by r.quote_id, (r.status in ('draft', 'in_review')) desc, (r.status <> 'void') desc, r.revision_no desc
), listed as (
  select q.id, q.quote_number, c.revision_id, c.revision_no, c.status, c.origin, c.grand_total, c.quote_currency,
         c.pdf_sha256, greatest(q.updated_at, c.updated_at) as updated_at,
         case when c.status = 'in_review' then 'draft' else c.status end as bucket,
         o.id as opportunity_id, o.title, o.closed_at,
         inst.id as institution_id, inst.name as institution_name
    from crm.quote q
    join cur c on c.quote_id = q.id
    join crm.opportunity o on o.id = q.opportunity_id
    left join crm.organization m on m.id = o.organization_id
    left join crm.organization inst on inst.id = coalesce(m.merged_into_organization_id, m.id)
   -- Sales cases only: a tender's offer opens by id, never in this list (`case_kind.py`).
   where o.kind = 'sale'
     and (%(pattern)s::text is null
      or q.quote_number ilike %(pattern)s or inst.name ilike %(pattern)s or o.title ilike %(pattern)s
      or exists (select 1 from crm.quote_line l
                  where l.quote_revision_id = c.revision_id and l.description ilike %(pattern)s)
      or exists (select 1 from evidence.source_record d join evidence.document_line l on l.source_record_id = d.id
                  where d.dedupe_key = 'quote_document:' || lower(c.pdf_sha256) and d.kind = 'quote_document'
                    and l.description ilike %(pattern)s))
)
"""

# The page (newest first, after the cursor), then each row's item lines: the first descriptions
# and how many there are — the lateral runs for the page's rows only. A row with neither totals nor
# lines of its own (a historical import) also carries what its PDF said, when it was read
# (`quote_documents`: the case panel's columns), in the same statement.
_LIST_ROWS_SQL = _LIST_BASE_SQL + """
select p.*, coalesce(it.descriptions, '{}'::text[]) as descriptions, coalesce(it.n, 0) as item_count,
       """ + QUOTE_DOCUMENT_COLUMNS + """
  from (select l.* from listed l
         where (%(bucket)s::text is null or l.bucket = %(bucket)s)
           and (%(after_at)s::timestamptz is null
                or (l.updated_at, l.id) < (%(after_at)s::timestamptz, %(after_id)s::uuid))
         order by l.updated_at desc, l.id desc
         limit %(limit)s) p
  left join lateral (
    select (array_agg(x.description order by x.line_no))[1:%(items)s] as descriptions, count(*) as n
      from crm.quote_line x
     where x.quote_revision_id = p.revision_id and x.line_kind = 'item') it on true
  """ + quote_document_join("p.pdf_sha256") + """ and p.grand_total is null and it.n = 0
 order by p.updated_at desc, p.id desc
"""

_LIST_COUNTS_SQL = _LIST_BASE_SQL + """
select count(*) filter (where bucket = 'draft') as draft, count(*) filter (where bucket = 'approved') as approved,
       count(*) filter (where bucket = 'sent') as sent, count(*) filter (where bucket = 'void') as void,
       count(*) as "all"
  from listed
"""

# -- «Rentabilidad» (`GET /v2/quotes/economics`)
#
# Every quote made in the Cotizador at its current revision (`_current`'s rule, as the list), not
# voided, dated in Santiago by when it was approved (else sent; a draft by its last change) and
# inside the period; with its institution through a merge. The second statement is those revisions'
# stored lines, the third the parameters: one pipeline, one round trip, whatever the period holds.
_ECONOMICS_BASE_SQL = """
with cur as (
  select distinct on (r.quote_id) r.*
    from crm.quote_revision r
   order by r.quote_id, (r.status in ('draft', 'in_review')) desc, (r.status <> 'void') desc, r.revision_no desc
), picked as (
  select c.id as revision_id, c.quote_id, c.revision_no, c.status, c.quote_currency, c.tax_rate,
         c.expense_allocation_mode,
         case when c.status = 'in_review' then 'draft' else c.status end as bucket,
         case when c.status in ('draft', 'in_review') then c.updated_at
              else coalesce(c.approved_at, c.sent_at, c.updated_at) end as at
    from cur c
   where c.origin = 'authored' and c.status <> 'void'
     -- Sales cases only: a tender's offer is not in «Rentabilidad» (`case_kind.py`).
     and exists (select 1 from crm.quote q join crm.opportunity o on o.id = q.opportunity_id
                  where q.id = c.quote_id and o.kind = 'sale')
), dated as (
  select p.*, (p.at at time zone 'America/Santiago') as local_at
    from picked p
   where (p.at at time zone 'America/Santiago') >= %(start)s and (p.at at time zone 'America/Santiago') < %(end)s
)
"""

_ECONOMICS_ROWS_SQL = _ECONOMICS_BASE_SQL + """
select d.revision_id::text as revision_id, d.quote_id::text as quote_id, q.quote_number, d.revision_no, d.status,
       d.bucket, d.quote_currency, d.tax_rate, d.expense_allocation_mode, d.local_at::date as day,
       to_char(d.local_at, 'YYYY-MM') as month, inst.name as institution_name
  from dated d
  join crm.quote q on q.id = d.quote_id
  join crm.opportunity o on o.id = q.opportunity_id
  left join crm.organization m on m.id = o.organization_id
  left join crm.organization inst on inst.id = coalesce(m.merged_into_organization_id, m.id)
 order by d.at desc, d.quote_id desc
"""

_ECONOMICS_LINES_SQL = _ECONOMICS_BASE_SQL + """
select l.*
  from crm.quote_line l
  join dated d on d.revision_id = l.quote_revision_id
 order by l.quote_revision_id, l.line_no
"""

# -- client suggestions (`GET /v2/quotes/clients`, plan ruling R3)
#
# A supplier or manufacturer — a current `organization_relationship` row in those roles, the
# lookup `case_command_repository._is_one_of_our_suppliers` makes — is never a client; neither is
# an archived or merged institution.
_NOT_A_SUPPLIER = """not exists (select 1 from crm.organization_relationship r
                    where r.organization_id = {org} and r.role = any(%(supplier_roles)s)
                      and (r.valid_to is null or r.valid_to > current_date))"""

# Institutions by name, legal name or domain: a name starting with the text first, then those
# with an open case (their own or a merged twin's), then by name.
_CLIENT_ORGS_SQL = f"""
select o.id::text as id, o.name, o.kind,
       coalesce((select array_agg(d.domain_norm order by d.domain_norm)
                   from crm.organization_domain d where d.organization_id = o.id and d.removed_at is null),
                '{{}}'::text[]) as domains
  from crm.organization o
 where o.status = 'active' and o.merged_into_organization_id is null
   and (o.name ilike %(pattern)s or o.legal_name ilike %(pattern)s
        or exists (select 1 from crm.organization_domain d
                    where d.organization_id = o.id and d.removed_at is null and d.domain_norm ilike %(pattern)s))
   and {_NOT_A_SUPPLIER.format(org="o.id")}
 order by (o.name ilike %(prefix)s) desc,
          exists (select 1 from crm.opportunity op
                    left join crm.organization t on t.id = op.organization_id
                   where op.closed_at is null and op.kind = 'sale' and coalesce(t.merged_into_organization_id, t.id) = o.id) desc,
          lower(o.name), o.id
 limit %(limit)s
"""

# The one institution an email domain names (controller ruling 2026-10-16): active, not merged
# away, no supplier or manufacturer, holding the domain (not removed) — only when exactly one
# does, and never for a free-mail domain (`mail_rules.FREE_MAIL_DOMAINS`, `%(free_mail)s`).
# `{domain}` is the SQL naming the domain; `{guard}` a condition that must hold too (empty: none).
_DOMAIN_INSTITUTION_SQL = f"""
select (array_agg(distinct o.id))[1] as id
  from crm.organization_domain d join crm.organization o on o.id = d.organization_id
 where {{guard}} d.domain_norm = {{domain}} and d.removed_at is null
   and not ({{domain}} = any(%(free_mail)s::text[]))
   and o.status = 'active' and o.merged_into_organization_id is null
   and {_NOT_A_SUPPLIER.format(org="o.id")}
having count(distinct o.id) = 1
"""

# A person's institution, or an address's: the person's current affiliation (a confirmed one
# first, the newest), else the address's own institution, else — for an address with neither a
# person nor an institution — the one its domain names (`_DOMAIN_INSTITUTION_SQL`, the rule an
# address the CRM lacks follows); the survivor of a merge, and only while active. `{person}`,
# `{point_org}` and `{point_email}` are the column or parameter naming the person, the address's
# organization and the address. The suggestions (`_CLIENT_PEOPLE_SQL`) and `create-quote`
# (`_CLIENT_INSTITUTION_SQL`) read it here, so the dialog and the command agree on whose cases a
# person's (or an address's) are.
_CLIENT_INSTITUTION_JOINS = """
  left join lateral (
    select a.organization_id from crm.affiliation a
     where a.person_id = {person} and (a.valid_to is null or a.valid_to > current_date)
     order by (a.confirmation = 'confirmed') desc, a.valid_from desc, a.created_at desc, a.id
     limit 1) af on true
  left join lateral (""" + _DOMAIN_INSTITUTION_SQL.format(
    guard="{person} is null and {point_org} is null and",
    domain="substring({point_email} from '@([^@]*)$')") + """) dm on true
  left join crm.organization m on m.id = coalesce(af.organization_id, {point_org}, dm.id)
  left join crm.organization org on org.id = coalesce(m.merged_into_organization_id, m.id) and org.status = 'active'
"""


# People by name or by an active email address of theirs, and addresses with no person by the
# address. Each with the active email that stands for them (`recipients.PERSON_ADDRESS_ORDER`: one a
# quote may go to, then the oldest — the one `create-quote` and approval take too) and its
# institution (`_CLIENT_INSTITUTION_JOINS`), never a supplier's (a supplier's people are no client).
_CLIENT_PEOPLE_SQL = f"""
with found as (
  select p.id as person_id, null::uuid as contact_point_id, p.display_name as name
    from crm.person p
   where p.status = 'active' and p.merged_into_person_id is null
     and (p.display_name ilike %(pattern)s
          or exists (select 1 from crm.contact_point c
                      where c.person_id = p.id and c.kind = 'email' and c.status = 'active'
                        and c.value_norm ilike %(pattern)s))
  union all
  select null::uuid, c.id, null::text
    from crm.contact_point c
   where c.person_id is null and c.kind = 'email' and c.status = 'active' and c.value_norm ilike %(pattern)s
)
select f.person_id::text as person_id, e.id::text as contact_point_id, f.name, e.value_norm as email,
       org.id::text as organization_id, org.name as organization_name
  from found f
  left join lateral (
    select c.id, c.value_norm, c.organization_id from crm.contact_point c
     where c.kind = 'email' and c.status = 'active'
       and (c.id = f.contact_point_id or (f.contact_point_id is null and c.person_id = f.person_id))
     order by {PERSON_ADDRESS_ORDER}
     limit 1) e on true
{_CLIENT_INSTITUTION_JOINS.format(person="f.person_id", point_org="e.organization_id", point_email="e.value_norm")}
 where (org.id is null or {_NOT_A_SUPPLIER.format(org="org.id")})
 order by (coalesce(f.name, e.value_norm) ilike %(prefix)s) desc, lower(coalesce(f.name, e.value_norm)),
          f.person_id, e.id
 limit %(limit)s
"""

# The open cases of every suggestion at once: an institution's (the cases whose requesting
# institution is it or a twin merged into it), a person's (the cases they are a current participant
# of — the participant naming the person, or only an address of theirs) and an address's (the cases
# it is a current participant of). `requested_at` is when the case's own email was captured, else when
# the case was opened.
_CLIENT_CASES_SQL = """
with open_case as (
  select o.id, o.title, o.stage, o.organization_id, coalesce(sr.acquired_at, o.created_at) as requested_at
    from crm.opportunity o
    left join evidence.source_record sr on sr.id = o.origin_source_record_id
   where o.closed_at is null and o.kind = 'sale'  -- a tender is never a client's open case (case_kind.py)
)
select 'organization' as via, coalesce(m.merged_into_organization_id, m.id)::text as key,
       c.id::text as id, c.title, c.stage, c.requested_at
  from open_case c join crm.organization m on m.id = c.organization_id
 where coalesce(m.merged_into_organization_id, m.id) = any(%(organizations)s::uuid[])
union all
select 'person', p.person_id::text, c.id::text, c.title, c.stage, c.requested_at
  from crm.opportunity_participant p join open_case c on c.id = p.opportunity_id
 where p.valid_to is null and p.person_id = any(%(people)s::uuid[])
union all
select 'person', a.person_id::text, c.id::text, c.title, c.stage, c.requested_at
  from crm.opportunity_participant p join open_case c on c.id = p.opportunity_id
  join crm.contact_point a on a.id = p.contact_point_id
 where p.valid_to is null and a.person_id = any(%(people)s::uuid[])
union all
select 'contact_point', p.contact_point_id::text, c.id::text, c.title, c.stage, c.requested_at
  from crm.opportunity_participant p join open_case c on c.id = p.opportunity_id
 where p.valid_to is null and p.contact_point_id = any(%(points)s::uuid[])
"""

# One client's institution for `create-quote`: as the suggestions find it, with whether it is one
# of OrigenLab's suppliers or manufacturers (then it is no client).
_CLIENT_INSTITUTION_SQL = f"""
select org.id::text as organization_id, org.name as organization_name,
       (org.id is not null and not ({_NOT_A_SUPPLIER.format(org="org.id")})) as is_supplier
  from (select %(person)s::uuid as person_id, %(point_org)s::uuid as point_org, %(point_email)s::text as point_email) f
{_CLIENT_INSTITUTION_JOINS.format(person="f.person_id", point_org="f.point_org", point_email="f.point_email")}
"""

#: The revision columns `current` carries besides its draft or stored lines.
_REVISION_FIELDS = (
    "id", "revision_no", "status", "origin", "version", "quote_currency", "tax_rate", "subtotal", "tax_total",
    "grand_total", "totals_computed_at", "validity_days", "valid_until", "approved_at", "sent_at",
    "superseded_by_revision_no", "recipient_participant_id", "expense_method", "expense_allocation_mode",
    "principal_rule", "updated_at",
)
#: Stored line columns shown for an issued revision (the draft of a draft is rebuilt instead).
_LINE_FIELDS = (
    "line_no", "line_kind", "description", "product_id", "quantity", "item_type", "cost_currency", "unit_cost",
    "fx_rate", "fx_as_of", "fx_source", "margin_mode", "margin_pct", "unit_price_qc", "line_total_qc",
    "is_principal", "allocated_to_line_no", "expense_method", "supplier_organization_id", "cost_observation_id",
    "line_note_es",
)


class _FxPrefetch:
    """The resolver a read transaction prices with: it never asks a provider.

    It answers from the rates fetched before this pass. Before any fetch, a currency it cannot
    answer is noted and given a placeholder rate, so one pass finds every missing currency; a pass
    that noted one is thrown away (`V2QuoteReads._fx_bounded`). On the last pass (`final`) a
    currency it cannot answer is unavailable.
    """

    _PLACEHOLDER = "1"

    def __init__(self) -> None:
        self.fetched: dict[tuple[str, dt.date], dict | FxUnavailable] = {}
        self.missed: set[tuple[str, dt.date]] = set()
        self.final = False

    def __call__(self, currency: str, day: dt.date) -> dict:
        found = self.fetched.get((currency, day))
        if isinstance(found, FxUnavailable):
            raise found
        if found is not None:
            return found
        if self.final:
            raise FxUnavailable(f"no {currency} rate for {day.isoformat()}")
        self.missed.add((currency, day))
        return {"clp_per_unit": self._PLACEHOLDER, "as_of": day, "source": "bcentral"}

    def fetch(self, resolver: FxResolver) -> None:
        """Ask `resolver` for every currency the last pass missed — with no transaction open."""
        for currency, day in sorted(self.missed):
            try:
                self.fetched[(currency, day)] = resolver(currency, day)
            except FxUnavailable as exc:
                self.fetched[(currency, day)] = exc
        self.missed.clear()
        self.final = True


def _decimal_or_none(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _saved_view(revision: dict[str, Any], lines: list[dict[str, Any]]) -> dict[str, Any]:
    """What the draft's last save stored, which approval freezes: its totals and each item line's prices."""
    return {"totals": {"net": revision["subtotal"], "iva": revision["tax_total"], "total": revision["grand_total"]},
            "lines": [{"line_no": r["line_no"], "unit_price_qc": r["unit_price_qc"],
                       "line_total_qc": r["line_total_qc"]}
                      for r in sorted(lines, key=lambda r: r["line_no"]) if r["line_kind"] == "item"]}


def _differs(computed: dict[str, Any] | None, saved: dict[str, Any]) -> bool:
    """Whether the live computation differs from the save, figure by figure (a draft never priced differs)."""
    if computed is None:
        return True
    totals = computed["totals"]
    live_totals = {k: _decimal_or_none(totals[k]) for k in ("net", "iva", "total")}
    if live_totals != {k: _decimal_or_none(v) for k, v in saved["totals"].items()}:
        return True
    live = [(r["line_no"], _decimal_or_none(r["unit_price"]), _decimal_or_none(r["line_total"]))
            for r in computed["lines"]]
    stored = [(r["line_no"], _decimal_or_none(r["unit_price_qc"]), _decimal_or_none(r["line_total_qc"]))
              for r in saved["lines"]]
    return live != stored


def _label(quote_number: str, revision_no: int) -> str | None:
    """The printed label, or None for a historical number that does not follow the series' form."""
    try:
        return revision_label(quote_number, revision_no)
    except ValueError:
        return None


def _santiago_today(now: datetime) -> dt.date:
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now.astimezone(SANTIAGO).date()


#: A machine that sends mail, by its address's local part: no-reply in any spelling (alone or as
#: a part, `alertas-noreply`, `noreply+tag`), bounces and notification senders. Deliberately narrow:
#: a mailbox a person reads (`ventas@`, `contacto@`, `info@`) is still someone to quote to.
_AUTOMATIC_LOCAL = re.compile(
    r"(?:^|[-_.])(?:no-?reply|do-?not-?reply)(?:[-_.+]|$)|^(?:mailer-daemon|postmaster|notifications?)(?:\+|$)"
)
#: Google's own notification senders (Gemini notes, Calendar) and every `*.google.com` sender
#: (Docs comments, Calendar invitations): a person at google.com itself is still offered.
_AUTOMATIC_ADDRESSES = frozenset({"gemini-notes@google.com", "calendar-notification@google.com"})


def _automatic_sender(email: str) -> bool:
    """A notification or no-reply address on a case's emails: nobody a quote goes to."""
    local, _, domain = email.strip().lower().rpartition("@")
    return (email.strip().lower() in _AUTOMATIC_ADDRESSES or domain.endswith(".google.com")
            or bool(_AUTOMATIC_LOCAL.search(local)))


def _offerable(email: str | None, name: str | None) -> bool:
    """An address a quote may go to: well formed, not an automatic sender, and allowed by the
    save's own rule (`recipients`)."""
    return bool(email) and "@" in email and not _automatic_sender(email) and not_a_recipient(email, name) is None


def _header_pairs(raw: str | None) -> list[tuple[str | None, str]]:
    """(display name, lower-cased address) for each address in a header; a name that is an address is no name."""
    out = []
    for name, address in getaddresses([(raw or "").replace(";", ",")]):
        address = address.strip().lower()
        if "@" not in address:
            continue
        name = name.strip().strip('"').strip()
        out.append((name if name and "@" not in name else None, address))
    return out


def email_people(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The addresses on a case's emails, newest first, once each: who wrote to OrigenLab (an
    inbound email's sender), whom OrigenLab wrote to (an outbound email's recipients), and the
    customer of a forwarded request (not one forwarded from the Labdelivery mailbox). Pure."""
    seen: dict[str, dict[str, Any]] = {}
    oldest = datetime.min.replace(tzinfo=UTC)
    for r in sorted(rows, key=lambda r: r.get("at") or oldest, reverse=True):
        if r["kind"] == "forwarded_request":
            if r.get("origin") == "labdelivery":
                continue
            pairs, source = [(r.get("name") or None, str(r.get("recipients") or "").strip().lower())], "forwarded_request"
        elif enquirer := web_form_enquirer(r.get("sender"), r.get("web_form_reply_to")):
            # A web-quote-form submission: contacto@ mailed itself; the visitor is its Reply-To.
            pairs, source = [(None, enquirer)], "email_from"
        elif mail_direction(r.get("sender"), r.get("direction_hint"), r.get("comms_direction")) == "inbound":
            pairs, source = _header_pairs(r.get("sender")), "email_from"
        else:
            pairs, source = _header_pairs(r.get("recipients")), "email_to"
        for name, address in pairs:
            if not _offerable(address, name):
                continue
            if address in seen:
                seen[address]["name"] = seen[address]["name"] or name
                continue
            seen[address] = {"email": address, "name": name, "source": source}
    return list(seen.values())


def recipient_candidates(participants: list[dict[str, Any]], people: list[dict[str, Any]],
                         known: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Whom the quote can go to: the case's current participants first, then the people on its
    emails (`email_people`) not already among them — each with the contact point and person the
    CRM holds for its address, if any. Pure."""
    by_address = {k["email"]: k for k in known}
    inactive = {k["email"] for k in known if k.get("status") not in (None, "active")}
    out: list[dict[str, Any]] = []
    taken_addresses: set[str] = set()
    taken_people: set[str] = set()
    for p in participants:
        if not_a_recipient(p["email"], p["name"]) is not None:
            continue
        out.append({"participant_id": p["id"], "person_id": p["person_id"], "contact_point_id": p["contact_point_id"],
                    "name": p["name"], "email": p["email"], "source": "participant"})
        if p["email"]:
            taken_addresses.add(p["email"])
        if p["person_id"]:
            taken_people.add(p["person_id"])
    for person in people:
        crm = by_address.get(person["email"]) or {}
        if person["email"] in inactive or person["email"] in taken_addresses or (crm.get("person_id") and crm["person_id"] in taken_people):
            continue
        out.append({"participant_id": None, "person_id": crm.get("person_id"),
                    "contact_point_id": crm.get("contact_point_id"), "name": crm.get("name") or person["name"],
                    "email": person["email"], "source": person["source"]})
        taken_addresses.add(person["email"])
        if len(out) >= CONTACT_LIMIT:
            break
    return out


def _encode_cursor(updated_at: datetime, quote_id: str) -> str:
    raw = json.dumps([updated_at.isoformat(), str(quote_id)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    """The (updated_at, quote_id) a page ended at; anything this list did not issue is a 422."""
    try:
        decoded = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if not (isinstance(decoded, list) and len(decoded) == 2 and all(isinstance(v, str) for v in decoded)):
            raise ValueError("not a cursor")
        at, quote_id = decoded
        moment = datetime.fromisoformat(at)
        if moment.tzinfo is None:
            raise ValueError("naive")
        return moment, str(uuid.UUID(quote_id))
    except (binascii.Error, UnicodeDecodeError, ValueError, TypeError, AttributeError, OverflowError,
            RecursionError) as exc:
        raise CommandRefused(422, "bad_cursor", "that cursor is not one this list gave") from exc


def _pattern(text: str | None) -> str | None:
    r"""`%text%` for ILIKE with the text's own `%`, `_` and `\` taken literally; None for no search."""
    text = (text or "").strip()
    return f"%{_like_escape(text)}%" if text else None


def _list_amounts(row: dict[str, Any]) -> dict[str, Any]:
    """A list row's products and amount: the revision's own (`amount_source: revision`) when it has
    totals or lines; else what its PDF said when it was read (`pdf_read`: the read lines' first
    descriptions, expenses left out, and the neto — never IVA); else nothing (None)."""
    own = {"first": list(row["descriptions"]), "count": int(row["item_count"])}
    if row["grand_total"] is not None or own["count"]:
        return {"items": own, "grand_total": row["grand_total"], "net_total": None,
                "currency": row["quote_currency"], "amount_source": "revision"}
    read = quote_contents(row)
    if read is None:
        return {"items": own, "grand_total": None, "net_total": None, "currency": row["quote_currency"],
                "amount_source": None}
    products = [line["description"].strip() for line in read["lines"]
                if line.get("kind") != "freight" and (line.get("description") or "").strip()]
    return {"items": {"first": products[:QUOTE_LIST_ITEMS], "count": len(products)}, "grand_total": None,
            "net_total": read["net_total"], "currency": read["currency"] or row["quote_currency"],
            "amount_source": "pdf_read"}


def _offerable_domain(domain: str) -> bool:
    return not_a_recipient(f"x@{domain}") is None


def _cases_by_key(rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    out: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault((r["via"], r["key"]), []).append(
            {"id": r["id"], "title": r["title"], "stage": r["stage"], "requested_at": r["requested_at"]})
    return out


def _open_cases(by_key: dict[tuple[str, str], list[dict[str, Any]]], *keys: tuple[str, str | None],
                limit: int | None = CLIENT_OPEN_CASES) -> list[dict[str, Any]]:
    """The open cases under any of these keys, once each, the newest request first."""
    seen: dict[str, dict[str, Any]] = {}
    for via, key in keys:
        if key is None:
            continue
        for case in by_key.get((via, key), []):
            seen.setdefault(case["id"], case)
    oldest = datetime.min.replace(tzinfo=UTC)
    ordered = sorted(seen.values(), key=lambda c: (c["requested_at"] or oldest, c["id"]), reverse=True)
    return ordered if limit is None else ordered[:limit]


def _client_params() -> dict[str, Any]:
    return {"supplier_roles": list(SUPPLIER_RELATIONSHIP_ROLES), "free_mail": sorted(FREE_MAIL_DOMAINS)}


def client_institution(cur: Any, *, person_id: str | None, point_organization_id: str | None,
                       point_email: str | None = None) -> dict[str, Any]:
    """The institution a person (or an address) belongs to, as the suggestions show it:
    `{organization_id, organization_name, is_supplier}`, all None/False when there is none. An
    address (`point_email`) with no person and no institution of its own is its domain's."""
    cur.execute(_CLIENT_INSTITUTION_SQL, {**_client_params(), "person": person_id, "point_org": point_organization_id,
                                          "point_email": point_email})
    names = [d[0] for d in cur.description]
    return dict(zip(names, cur.fetchone(), strict=True))


# The institution an address the CRM lacks names by its domain (`_DOMAIN_INSTITUTION_SQL`).
_CLIENT_DOMAIN_SQL = ("select o.id::text as id, o.name from crm.organization o where o.id = ("
                      + _DOMAIN_INSTITUTION_SQL.format(guard="", domain="%(domain)s") + ")")


def client_institution_by_domain(cur: Any, email: str | None) -> dict[str, Any] | None:
    """The one institution whose domain is this address's (controller ruling 2026-10-16): only
    active, non-merged institutions that are no supplier or manufacturer count, and never for a
    free-mail domain (`mail_rules.FREE_MAIL_DOMAINS`, the list `register-organization`'s callers
    use). `{id, name}` when exactly one matches, else None. The same rule as a held address's
    (`_CLIENT_INSTITUTION_JOINS`)."""
    domain = (email or "").rpartition("@")[2].strip().lower()
    if not domain:
        return None
    cur.execute(_CLIENT_DOMAIN_SQL, {**_client_params(), "domain": domain})
    names = [d[0] for d in cur.description]
    found = [dict(zip(names, r, strict=True)) for r in cur.fetchall()]
    return found[0] if found else None


def client_open_cases(cur: Any, *, organization_id: str | None = None, person_id: str | None = None,
                      contact_point_id: str | None = None) -> list[dict[str, Any]]:
    """Every open case of one client (plan R3), the newest request first: the cases whose
    requesting institution is the institution (or a twin merged into it), and those the person or
    the address is a current participant of. The same statement and the same merge the
    suggestions run (`_CLIENT_CASES_SQL`, `_open_cases`), without their cap, so `create-quote`
    decides on what the dialog showed."""
    cur.execute(_CLIENT_CASES_SQL, {"organizations": [organization_id] if organization_id else [],
                                    "people": [person_id] if person_id else [],
                                    "points": [contact_point_id] if contact_point_id else []})
    names = [d[0] for d in cur.description]
    by_key = _cases_by_key([dict(zip(names, r, strict=True)) for r in cur.fetchall()])
    return _open_cases(by_key, ("person", person_id), ("contact_point", contact_point_id),
                       ("organization", organization_id), limit=None)


class V2QuoteReads:
    def __init__(self, connect: Any, dsn: str, *, fx_resolver: FxResolver, statement_timeout_ms: int = 30_000,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._connect = connect
        self._dsn = dsn
        self._fx_resolver = fx_resolver
        self._statement_timeout_ms = statement_timeout_ms
        self._clock = clock
        # Its price-history SQL and shaping; it is never asked to open a transaction of its own here.
        self._catalog = V2CatalogReads(connect, dsn, statement_timeout_ms)

    @contextmanager
    def _read(self):  # type: ignore[no-untyped-def]
        with self._connect(self._dsn, autocommit=False) as conn:
            with conn.cursor() as cur:
                cur.execute(f"set transaction read only; set local statement_timeout = {int(self._statement_timeout_ms)}")
                try:
                    yield cur
                finally:
                    conn.rollback()

    @staticmethod
    def _rows(cur: Any) -> list[dict[str, Any]]:
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    def _today(self) -> dt.date:
        return _santiago_today(self._clock())

    # -- numbering

    def numbering(self) -> dict[str, Any]:
        """What Hoy's number box shows: the next number, without reserving it. A bad seed is a 409."""
        with self._read() as cur:
            return numbering_status(cur, self._clock())

    # -- preview

    @staticmethod
    def _params(rows: list[dict[str, Any]]) -> dict[str, Decimal | str]:
        return parameters_from((r["key"], r["value_numeric"], r["value_json"]) for r in rows)

    def _fx_bounded(self, once: Callable[[_FxPrefetch], Any]) -> Any:
        """`once(fx)` reads in its own transaction; on a rate miss it runs once more after the fetch.

        A pass that missed a rate is discarded whatever it returned or refused: it priced with a
        placeholder. The fetch runs between the two transactions, so the provider's HTTP call and
        its write of the answer never wait while this request holds a pooled connection.
        """
        fx = _FxPrefetch()
        try:
            out = once(fx)
        except CommandRefused:
            if not fx.missed:
                raise
        else:
            if not fx.missed:
                return out
        fx.fetch(self._fx_resolver)
        return once(fx)

    def _priced(self, cur: Any, draft: QuoteDraft, tax_rate: Decimal | None,
                params: dict[str, Decimal | str], fx: FxResolver) -> dict[str, Any]:
        params = dict(params)
        if tax_rate is not None:
            params["iva_rate"] = Decimal(tax_rate)
        try:
            resolved = resolve_draft(cur, draft, params, fx, self._today())
            result = compute(resolved.engine_input)
        except EngineError as exc:
            raise CommandRefused(422, exc.code, f"the draft cannot be priced ({exc.code})") from exc
        return result_json(resolved, result)

    def preview(self, body: PreviewQuoteBody) -> dict[str, Any]:
        """The draft priced as a save would price it; nothing is written."""
        return self._fx_bounded(lambda fx: self._preview_once(body, fx))

    def _preview_once(self, body: PreviewQuoteBody, fx: FxResolver) -> dict[str, Any]:
        revision_id = str(body.revision_id) if body.revision_id else None
        with self._read() as cur:
            queries: dict[str, tuple[str, tuple[Any, ...]]] = {"params": (PARAMETERS_SQL, ())}
            if revision_id is not None:
                queries["revision"] = ("select tax_rate from crm.quote_revision where id = %s::uuid", (revision_id,))
            rows = self._catalog._batch(cur, queries)
            tax_rate = None
            if revision_id is not None:
                if not rows["revision"]:
                    raise CommandRefused(404, "revision_not_found", "no such quote revision")
                tax_rate = rows["revision"][0]["tax_rate"]
            return {"revision_id": revision_id,
                    "computed": self._priced(cur, body.draft, tax_rate, self._params(rows["params"]), fx)}

    # -- kit suggestions (R-A5)

    def kit_suggestions(self, cur: Any, key: str | None) -> list[dict[str, Any]]:
        key = model_key(key)
        if key is None:
            return []
        cur.execute(_KIT_SQL, self._kit_params(key))
        return self._kits_from(self._rows(cur))

    @staticmethod
    def _kit_params(key: str) -> dict[str, Any]:
        return {"key": key, "min_docs": KIT_MIN_DOCUMENTS, "min_share": KIT_MIN_SHARE, "limit": KIT_LIMIT}

    @staticmethod
    def _kits_from(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for r in rows:
            name = r["product_name"] or r["description"] or r["model_key"]
            if is_labdelivery(name):
                continue
            out.append({"model_key": r["model_key"], "product_id": r["product_id"], "name": name,
                        "share": str((Decimal(r["n"]) / Decimal(r["total"])).quantize(Decimal("0.0001"))),
                        "documents": r["n"]})
        return out

    # -- the module's list

    def quote_list(self, *, status: str, q: str | None, limit: int, cursor: str | None) -> dict[str, Any]:
        """One page of quotes, each at its current revision, newest first, with the count of each
        status under the same search. Two statements in one pipeline, whatever the page holds."""
        if status != "all" and status not in QUOTE_LIST_STATUSES:
            raise CommandRefused(422, "bad_status", "no such quote status")
        limit = max(1, min(int(limit), QUOTE_LIST_MAX))
        after_at, after_id = _decode_cursor(cursor) if cursor else (None, None)
        params = {"pattern": _pattern(q), "bucket": None if status == "all" else status,
                  "after_at": after_at, "after_id": after_id, "limit": limit + 1, "items": QUOTE_LIST_ITEMS}
        with self._read() as cur:
            rows = self._catalog._batch(cur, {"rows": (_LIST_ROWS_SQL, params),
                                              "counts": (_LIST_COUNTS_SQL, {"pattern": params["pattern"]})})
        page = rows["rows"][:limit]
        more = len(rows["rows"]) > limit
        counts = rows["counts"][0]
        return _plain({
            "rows": [{
                "quote_id": str(r["id"]), "quote_number": r["quote_number"],
                "label": _label(r["quote_number"], r["revision_no"]), "status": r["bucket"], "origin": r["origin"],
                "opportunity": {"id": str(r["opportunity_id"]), "title": r["title"], "closed": r["closed_at"] is not None},
                "client": ({"organization_id": str(r["institution_id"]), "name": r["institution_name"]}
                           if r["institution_id"] else None),
                **_list_amounts(r), "updated_at": r["updated_at"],
            } for r in page],
            "counts": {k: int(counts[k]) for k in (*QUOTE_LIST_STATUSES, "all")},
            "next_cursor": _encode_cursor(page[-1]["updated_at"], page[-1]["id"]) if more else None,
        })

    # -- «Rentabilidad»

    def economics_report(self, *, from_month: str | None, to_month: str | None) -> dict[str, Any]:
        """Each authored quote of the period with its sale, product cost, import costs and gross
        margin, and the totals by month and for the range (`economics.report`). One pipeline of
        three statements, whatever the period holds. A bad period is a 422 `bad_period`."""
        try:
            start, end = month_range(from_month, to_month, self._today())
        except ValueError as exc:
            raise CommandRefused(422, "bad_period", str(exc)) from exc
        period = {"start": start, "end": end}
        with self._read() as cur:
            rows = self._catalog._batch(cur, {"rows": (_ECONOMICS_ROWS_SQL, period),
                                              "lines": (_ECONOMICS_LINES_SQL, period),
                                              "params": (PARAMETERS_SQL, ())})
        params = self._params(rows["params"])
        revisions = [{**r, "label": _label(r["quote_number"], r["revision_no"])} for r in rows["rows"]]
        return {**report(revisions, rows["lines"], params, start, end), "params": _plain(quote_preferences(params))}

    # -- client suggestions (R3)

    def client_suggestions(self, q: str) -> dict[str, Any]:
        """Institutions and people a new quote can be for, each with its open cases. Never a
        supplier or manufacturer, an archived or merged institution, an inactive address, nor
        anything of OrigenLab's own or Labdelivery's (`recipients.not_a_recipient`). Two
        round trips: the two searches in one pipeline, then every suggestion's open cases."""
        text = q.strip()
        params = {"pattern": _pattern(text), "prefix": f"{_like_escape(text)}%", "limit": CLIENT_FETCH,
                  **_client_params()}
        with self._read() as cur:
            found = self._catalog._batch(cur, {"organizations": (_CLIENT_ORGS_SQL, params),
                                               "people": (_CLIENT_PEOPLE_SQL, params)})
            organizations = [o for o in found["organizations"]
                             if not is_labdelivery(o["name"]) and all(_offerable_domain(d) for d in o["domains"])
                             ][:CLIENT_LIMIT]
            people = [p for p in found["people"]
                      if p["email"] and not_a_recipient(p["email"], p["name"]) is None
                      and not is_labdelivery(p["organization_name"])][:CLIENT_LIMIT]
            cases: list[dict[str, Any]] = []
            org_ids = {o["id"] for o in organizations} | {p["organization_id"] for p in people if p["organization_id"]}
            person_ids = {p["person_id"] for p in people if p["person_id"]}
            point_ids = {p["contact_point_id"] for p in people if p["contact_point_id"]}
            if org_ids or person_ids or point_ids:
                cur.execute(_CLIENT_CASES_SQL, {"organizations": sorted(org_ids), "people": sorted(person_ids),
                                                "points": sorted(point_ids)})
                cases = self._rows(cur)
        by_key = _cases_by_key(cases)
        return _plain({
            "organizations": [{"id": o["id"], "name": o["name"], "kind": o["kind"], "domains": list(o["domains"]),
                               "open_cases": _open_cases(by_key, ("organization", o["id"]))}
                              for o in organizations],
            "people": [{"person_id": p["person_id"], "contact_point_id": p["contact_point_id"], "name": p["name"],
                        "email": p["email"],
                        "organization": ({"id": p["organization_id"], "name": p["organization_name"]}
                                         if p["organization_id"] else None),
                        "open_cases": _open_cases(by_key, ("person", p["person_id"]),
                                                  ("contact_point", p["contact_point_id"]),
                                                  ("organization", p["organization_id"]))}
                       for p in people],
        })

    # -- workspace

    def workspace(self, quote_id: uuid.UUID | str, role: str | None = None) -> dict[str, Any] | None:
        """Everything the Cotizador page opens with, or None when the quote does not exist.

        Round trips after the set-up, all on this one connection: the quote with its case; one
        pipeline (revisions, current lines, participants, the people on the case's emails, memory,
        mentions, parameters); when its emails name anyone, what the CRM holds for those addresses;
        for a draft, `resolve_draft`'s four statements; then one pipeline for the principal's
        history and kits. Addresses are shown in full: only sales and admin reach this read.
        """
        return self._fx_bounded(lambda fx: self._workspace_once(quote_id, fx, role))

    def _workspace_once(self, quote_id: uuid.UUID | str, fx: FxResolver,
                        role: str | None = None) -> dict[str, Any] | None:
        qid = str(quote_id)
        with self._read() as cur:
            cur.execute(_QUOTE_SQL, (qid,))
            found = self._rows(cur)
            if not found:
                return None
            quote = found[0]
            case_id = quote["opportunity_id"]
            rows = self._catalog._batch(cur, {
                "revisions": (_REVISIONS_SQL, (qid,)),
                "lines": (_LINES_SQL, (qid,)),
                "participants": (_PARTICIPANTS_SQL, (case_id,)),
                "people": (_CASE_PEOPLE_SQL, {"case": case_id}),
                "memory": (_MEMORY_SQL, (case_id,)),
                "mentions": (_MENTIONS_SQL, (case_id,)),
                "params": (PARAMETERS_SQL, ()),
            })
            people = email_people(rows["people"])
            known: list[dict[str, Any]] = []
            if people:
                cur.execute(_KNOWN_ADDRESSES_SQL, ([p["email"] for p in people],))
                known = self._rows(cur)
            revisions = rows["revisions"]
            current_row = self._current(revisions)
            lines = rows["lines"]
            params = self._params(rows["params"])
            current = self._current_json(cur, quote, current_row, lines, params, fx) if current_row else None

            principal_key = self._principal_key(current, lines)
            history, kits = None, []
            if principal_key is not None:
                # The principal's product: its history includes the lines that name it by an alias.
                principal_product = self._principal_line(current, lines)["product_id"]
                extra = self._catalog._batch(cur, {
                    **self._catalog._price_history_queries(principal_key, principal_product),
                    "kits": (_KIT_SQL, self._kit_params(principal_key)),
                })
                history = self._catalog._price_history_from(principal_key, extra, PRICE_HISTORY_LIMIT)
                kits = self._kits_from(extra["kits"])

            # A tender's offer: the tender's code, and who bids (sales and admin only).
            tender = None
            if quote["case_kind"] == "tender":
                profile = current_bidder_profile(cur) if role in ("sales", "admin") else None
                tender = {"code": quote["tender_code"],
                          "bidder": ({"legal_name": profile["legal_name"], "rut": format_rut(profile["rut"])}
                                     if profile else None)}

        memory = rows["memory"][0]["summary"] if rows["memory"] else None
        return _plain({
            "quote": {"id": quote["id"], "quote_number": quote["quote_number"], "opportunity_id": case_id},
            "case": {
                "id": case_id, "title": quote["title"], "stage": quote["stage"], "closed": quote["closed_at"] is not None,
                "version": quote["case_version"],
                "institution": ({"id": quote["institution_id"], "name": quote["institution_name"], "rut": quote["rut"]}
                                if quote["institution_id"] else None),
                "participants": [{k: p[k] for k in ("id", "name", "email", "primary", "role")}
                                 for p in rows["participants"]],
                "contacts": recipient_candidates(rows["participants"], people, known),
                "lo_que_pidio": memory,
            },
            "revisions": [{"id": r["id"], "revision_no": r["revision_no"],
                           "label": _label(quote["quote_number"], r["revision_no"]), "status": r["status"],
                           "origin": r["origin"], "version": r["version"], "grand_total": r["grand_total"],
                           "approved_at": r["approved_at"], "sent_at": r["sent_at"],
                           "superseded_by_revision_no": r["superseded_by_revision_no"]}
                          for r in revisions],
            "current": current,
            "suggestions": {"mentioned": self._mentions_from(rows["mentions"]), "kits": kits},
            "price_history": history,
            "params": quote_preferences(params),
            "tender": tender,
        })

    @staticmethod
    def _current(revisions: list[dict[str, Any]]) -> dict[str, Any] | None:
        """The open draft; else the newest revision not voided; else the newest. Same order as `_LINES_SQL`."""
        if not revisions:
            return None
        return max(revisions, key=lambda r: (r["status"] in ("draft", "in_review"), r["status"] != "void",
                                             r["revision_no"]))

    def _current_json(self, cur: Any, quote: dict[str, Any], revision: dict[str, Any],
                      lines: list[dict[str, Any]], params: dict[str, Decimal | str],
                      fx: FxResolver) -> dict[str, Any]:
        out: dict[str, Any] = {k: revision.get(k) for k in _REVISION_FIELDS}
        out["label"] = _label(quote["quote_number"], revision["revision_no"])
        if revision["origin"] == "authored" and revision["status"] == "draft":
            draft = draft_from_rows(revision, lines)
            out["draft"] = draft.model_dump(mode="json")
            out["computed"] = None
            out["computed_error"] = None
            out["economics"] = None
            try:
                out["computed"] = self._priced(cur, draft, revision["tax_rate"], params, fx)
                # «Economía de la cotización»: the same live computation the rail shows.
                out["economics"] = out["computed"]["economics"]
            except CommandRefused as exc:
                # The page still opens: the draft is shown with why it cannot be priced right now.
                out["computed_error"] = {"code": exc.code, "message": exc.message}
            out["saved"] = _saved_view(revision, lines)
            out["needs_saving"] = _differs(out["computed"], out["saved"])
        else:
            out["lines"] = [{k: line.get(k) for k in _LINE_FIELDS} for line in lines]
            out["party_snapshot"] = revision.get("party_snapshot")
            # An issued revision's economics are what approval froze; an imported quote has none
            # (its import costs were never recorded).
            authored = revision["origin"] == "authored"
            out["economics"] = frozen_economics(revision, lines, params) if authored else None
            out["line_economics"] = frozen_line_economics(revision, lines, params) if authored else None
        return out

    @classmethod
    def _principal_key(cls, current: dict[str, Any] | None, lines: list[dict[str, Any]]) -> str | None:
        """The model key of the principal line's product: the computed principal of a draft, the stored one otherwise."""
        line = cls._principal_line(current, lines)
        return model_key(line["product_model_key"]) if line and line.get("product_model_key") else None

    @staticmethod
    def _principal_line(current: dict[str, Any] | None, lines: list[dict[str, Any]]) -> dict[str, Any] | None:
        """The principal item line: the computed principal of a draft, the stored one otherwise."""
        if current is None:
            return None
        principal = None
        computed = current.get("computed")
        if computed is not None:
            principal = computed.get("principal_line_no")
        elif "lines" in current:
            principal = next((r["line_no"] for r in lines if r["line_kind"] == "item" and r["is_principal"]), None)
        if principal is None:
            return None
        return next((r for r in lines if r["line_no"] == principal and r["line_kind"] == "item"), None)

    @staticmethod
    def _mentions_from(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for r in rows:
            name = r["product_name"] or r["description"] or r["model"] or r["value_norm"]
            model = r["product_model"] or r["model"]
            if is_labdelivery(name) or is_labdelivery(model) or is_labdelivery(r["description"]):
                continue
            out.append({"product_id": r["product_id"], "model": model, "name": name, "model_key": r["value_norm"]})
            if len(out) == MENTION_LIMIT:
                break
        return out
