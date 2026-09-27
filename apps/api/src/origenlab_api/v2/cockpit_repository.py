"""Read-only repository for the operator cockpit endpoints.

Every query runs inside ``begin read only``, enforced by the ``_read()``
context manager inherited from ``V2Repository``.  This module adds no new
connection; it borrows the same ``V2Repository._read`` plumbing so the
isolation guarantee is provably the same for both read surfaces.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any

#: Maximum number of search hits returned; truncated flag set when reached.
MAX_SEARCH_HITS = 50

#: All stages in the canonical vocabulary.
_ALL_STAGES = (
    "lead",
    "qualifying",
    "qualified",
    "quoting",
    "negotiating",
    "won",
    "lost",
    "abandoned",
)


class CockpitRepository:
    """Read queries supporting ``/v2/cockpit/*``.

    Constructed with the same ``(connect, dsn)`` pair as ``V2Repository``
    so the app wires them from the same configured DSN.
    """

    def __init__(self, connect: Any, dsn: str, statement_timeout_ms: int = 30_000) -> None:
        self._connect = connect
        self._dsn = dsn
        self._statement_timeout_ms = statement_timeout_ms

    @contextmanager
    def _read(self):  # type: ignore[override]
        with self._connect(self._dsn, autocommit=False) as conn:
            with conn.cursor() as cur:
                cur.execute("set transaction read only")
                cur.execute(f"set local statement_timeout = {int(self._statement_timeout_ms)}")
                try:
                    yield cur
                finally:
                    conn.rollback()

    # ------------------------------------------------------------------ KPIs

    def kpis(self) -> dict[str, Any]:
        """All cockpit KPIs in one transaction."""
        with self._read() as cur:
            # -- quotation kpis
            cur.execute("""
                select
                  count(*) filter (
                    where qr.origin = 'historical_import'
                      and qr.status = 'sent'
                      and qr.superseded_by_revision_no is null
                  ) as sent_historical_revisions,
                  count(*) filter (
                    where qr.origin = 'historical_import'
                      and qr.status = 'void'
                  ) as void_historical_revisions
                from crm.quote_revision qr
            """)
            row = cur.fetchone()
            sent_historical, void_historical = int(row[0]), int(row[1])

            cur.execute("""
                select count(*) from (
                  select qr.quote_id
                  from crm.quote_revision qr
                  where qr.status <> 'void'
                    and qr.superseded_by_revision_no is null
                  group by qr.quote_id
                  having count(*) > 1
                ) sub
            """)
            undetermined = int(cur.fetchone()[0])

            cur.execute("""
                select count(*) from (
                  select q.quote_number
                  from crm.quote q
                  where q.number_origin = 'printed_historical'
                  group by q.quote_number
                  having count(distinct q.opportunity_id) > 1
                ) sub
            """)
            shared_printed = int(cur.fetchone()[0])

            # -- evidence kpis
            cur.execute("""
                select
                  -- distinct: the join repeats a record once per assertion
                  count(distinct sr.id) filter (
                    where sr.review_status = 'pending' and not sr.is_quarantined
                  ) as pending_records,
                  count(*) filter (
                    where a.kind = 'document_reference' and a.resolution = 'unresolved'
                  ) as unresolved_docs
                from evidence.source_record sr
                full outer join evidence.assertion a on a.source_record_id = sr.id
            """)
            row = cur.fetchone()
            pending_sr, unresolved_dr = int(row[0]), int(row[1])

            # revisions missing thread_id
            cur.execute("""
                select count(*)
                from crm.quote_revision qr
                join evidence.source_record sr on sr.id = qr.origin_source_record_id
                where qr.origin = 'historical_import'
                  and (sr.payload->>'gmail_thread_id') is null
            """)
            missing_thread = int(cur.fetchone()[0])

            # revisions missing document entry
            cur.execute("""
                select count(*)
                from crm.quote_revision qr
                join evidence.source_record sr on sr.id = qr.origin_source_record_id
                where qr.origin = 'historical_import'
                  and (
                    sr.payload->'documents' is null
                    or jsonb_array_length(sr.payload->'documents') = 0
                  )
            """)
            missing_doc = int(cur.fetchone()[0])

            # -- opportunity kpis
            cur.execute("""
                select stage, count(*) from crm.opportunity group by stage
            """)
            by_stage: dict[str, int] = {s: 0 for s in _ALL_STAGES}
            for stage, cnt in cur.fetchall():
                by_stage[stage] = int(cnt)

            cur.execute("""
                select count(*)
                from crm.opportunity op
                where op.stage = 'lead'
                  and op.organization_id is null
                  and not exists (
                    select 1 from crm.opportunity_organization oo
                     where oo.opportunity_id = op.id
                       and oo.role = 'requesting_institution'
                       and oo.valid_to is null
                  )
            """)
            leads_no_inst = int(cur.fetchone()[0])

        return {
            "quotations": {
                "sent_historical_revisions": sent_historical,
                "void_historical_revisions": void_historical,
                "quotes_with_undetermined_canonical": undetermined,
                "shared_printed_numbers": shared_printed,
            },
            "evidence": {
                "pending_source_records": pending_sr,
                "unresolved_document_references": unresolved_dr,
                "revisions_missing_thread_id": missing_thread,
                "revisions_missing_document_entry": missing_doc,
            },
            "opportunities": {
                "by_stage": by_stage,
                "leads_without_institution": leads_no_inst,
            },
        }

    # --------------------------------------------------------------- work queue

    def work_queue(self, limit: int, offset: int) -> dict[str, Any]:
        """Typed work items ordered by age (oldest first) then kind."""
        with self._read() as cur:
            cur.execute("""
                with items as (
                  -- pending_evidence
                  select
                    'pending_evidence'                    as kind,
                    'Source record awaiting review'       as reason,
                    'Review the evidence record'          as next_action,
                    jsonb_build_object('source_record_id', sr.id::text) as subject_ids,
                    extract(day from now() - sr.created_at)::int        as age_days,
                    sr.kind                                              as label,
                    sr.created_at                                        as sort_at
                  from evidence.source_record sr
                  where sr.review_status = 'pending' and not sr.is_quarantined

                  union all

                  -- unresolved_document
                  select
                    'unresolved_document',
                    'Document reference assertion unresolved',
                    'Resolve or reject the document assertion',
                    jsonb_build_object(
                      'assertion_id',      a.id::text,
                      'source_record_id',  a.source_record_id::text
                    ),
                    extract(day from now() - a.created_at)::int,
                    a.value_norm,
                    a.created_at
                  from evidence.assertion a
                  where a.kind = 'document_reference' and a.resolution = 'unresolved'

                  union all

                  -- canonical_undetermined
                  select
                    'canonical_undetermined',
                    'Quote has multiple active revisions — canonical is ambiguous',
                    'Void or supersede the duplicate revision',
                    jsonb_build_object(
                      'quote_id',         q.id::text,
                      'opportunity_id',   q.opportunity_id::text
                    ),
                    extract(day from now() - q.created_at)::int,
                    q.quote_number,
                    q.created_at
                  from crm.quote q
                  where (
                    select count(*)
                    from crm.quote_revision qr
                    where qr.quote_id = q.id
                      and qr.status <> 'void'
                      and qr.superseded_by_revision_no is null
                  ) > 1

                  union all

                  -- shared_printed_number
                  select
                    'shared_printed_number',
                    'Printed quote number appears on multiple opportunities',
                    'Confirm which opportunity each document belongs to',
                    jsonb_build_object(
                      'quote_number', q.quote_number,
                      'quote_id',     min(q.id::text)
                    ),
                    extract(day from now() - min(q.created_at))::int,
                    q.quote_number,
                    min(q.created_at)
                  from crm.quote q
                  where q.number_origin = 'printed_historical'
                  group by q.quote_number
                  having count(distinct q.opportunity_id) > 1

                  union all

                  -- case_without_institution
                  select
                    'case_without_institution',
                    'Lead-stage case has no requesting institution',
                    'Set or confirm the requesting institution',
                    jsonb_build_object('opportunity_id', op.id::text),
                    extract(day from now() - op.created_at)::int,
                    op.title,
                    op.created_at
                  from crm.opportunity op
                  where op.stage = 'lead'
                    and op.organization_id is null
                    and not exists (
                      select 1 from crm.opportunity_organization oo
                       where oo.opportunity_id = op.id
                         and oo.role = 'requesting_institution'
                         and oo.valid_to is null
                    )

                  union all

                  -- case_without_quote
                  select
                    'case_without_quote',
                    'Active quoting/negotiating case has no sent quote',
                    'Create or record a quotation',
                    jsonb_build_object('opportunity_id', op.id::text),
                    extract(day from now() - op.created_at)::int,
                    op.title,
                    op.created_at
                  from crm.opportunity op
                  where op.stage in ('quoting', 'negotiating')
                    and op.closed_at is null
                    and not exists (
                      select 1
                      from crm.quote q
                      join crm.quote_revision qr on qr.quote_id = q.id
                      where q.opportunity_id = op.id
                        and qr.status = 'sent'
                        and qr.superseded_by_revision_no is null
                    )
                )
                select kind, reason, next_action, subject_ids, age_days, label
                from items
                order by sort_at, kind
                limit %s offset %s
            """, (limit, offset))
            columns = [d[0] for d in cur.description]
            rows = cur.fetchall()
            items = [dict(zip(columns, r, strict=True)) for r in rows]

            # total (count all branches without limit/offset)
            cur.execute("""
                select
                  (select count(*) from evidence.source_record where review_status='pending' and not is_quarantined)
                + (select count(*) from evidence.assertion where kind='document_reference' and resolution='unresolved')
                + (select count(*) from (
                    select quote_id from crm.quote_revision
                    where status <> 'void' and superseded_by_revision_no is null
                    group by quote_id having count(*) > 1
                  ) sub)
                + (select count(*) from (
                    select quote_number from crm.quote where number_origin='printed_historical'
                    group by quote_number having count(distinct opportunity_id) > 1
                  ) sub2)
                + (select count(*) from crm.opportunity op
                    where op.stage='lead' and op.organization_id is null
                      and not exists (
                        select 1 from crm.opportunity_organization oo
                         where oo.opportunity_id=op.id and oo.role='requesting_institution' and oo.valid_to is null
                      ))
                + (select count(*) from crm.opportunity op
                    where op.stage in ('quoting','negotiating') and op.closed_at is null
                      and not exists (
                        select 1 from crm.quote q
                        join crm.quote_revision qr on qr.quote_id=q.id
                        where q.opportunity_id=op.id and qr.status='sent' and qr.superseded_by_revision_no is null
                      ))
                as total
            """)
            total = int(cur.fetchone()[0])

        # convert jsonb to dict
        for item in items:
            if isinstance(item["subject_ids"], str):
                item["subject_ids"] = json.loads(item["subject_ids"])
            if item["age_days"] is not None:
                item["age_days"] = int(item["age_days"])
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    # -------------------------------------------------------------- opportunities

    def opportunities(
        self,
        *,
        stage: str | None,
        organization_id: str | None,
        q: str | None,
        has_quote: bool | None,
        date_from: str | None,
        date_to: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        clauses: list[str] = []
        params: list[Any] = []

        if stage is not None:
            clauses.append("op.stage = %s")
            params.append(stage)
        if organization_id is not None:
            clauses.append("op.organization_id = %s::uuid")
            params.append(organization_id)
        if q:
            needle = f"%{q.strip().lower()}%"
            clauses.append("(lower(op.title) like %s or lower(o.name) like %s)")
            params.extend([needle, needle])
        if has_quote is True:
            clauses.append(
                "exists (select 1 from crm.quote q where q.opportunity_id = op.id)"
            )
        elif has_quote is False:
            clauses.append(
                "not exists (select 1 from crm.quote q where q.opportunity_id = op.id)"
            )
        if date_from:
            clauses.append("op.created_at >= %s::timestamptz")
            params.append(date_from)
        if date_to:
            clauses.append("op.created_at < %s::timestamptz")
            params.append(date_to)

        where = ("where " + " and ".join(clauses)) if clauses else ""

        with self._read() as cur:
            count_sql = f"""
                select count(*) from crm.opportunity op
                left join crm.organization o on o.id = op.organization_id
                {where}
            """
            cur.execute(count_sql, params)
            total = int(cur.fetchone()[0])

            sql = f"""
                select
                  op.id::text                                as opportunity_id,
                  op.title,
                  op.stage,
                  op.organization_id::text                   as organization_id,
                  o.name                                     as organization_name,
                  (select count(*) from crm.quote q
                    where q.opportunity_id = op.id)          as quote_count,
                  (select count(*) from crm.quote q
                   join crm.quote_revision qr on qr.quote_id = q.id
                   where q.opportunity_id = op.id
                     and qr.status = 'sent'
                     and qr.superseded_by_revision_no is null) as sent_revision_count,
                  (select count(*) from crm.opportunity_interest oi
                    where oi.opportunity_id = op.id
                      and oi.withdrawn_at is null)           as interest_count,
                  (op.organization_id is not null or exists (
                    select 1 from crm.opportunity_organization oo
                     where oo.opportunity_id = op.id
                       and oo.role = 'requesting_institution'
                       and oo.valid_to is null
                  ))                                         as has_requesting_institution,
                  op.closed_at::text,
                  op.close_reason,
                  op.created_at::text,
                  op.updated_at::text
                from crm.opportunity op
                left join crm.organization o on o.id = op.organization_id
                {where}
                order by op.updated_at desc, op.id
                limit %s offset %s
            """
            cur.execute(sql, (*params, limit, offset))
            cols = [d[0] for d in cur.description]
            items = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def opportunity_detail(self, opportunity_id: str) -> dict[str, Any] | None:
        with self._read() as cur:
            cur.execute("""
                select
                  op.id::text, op.title, op.stage,
                  op.organization_id::text, o.name,
                  op.close_reason, op.closed_at::text,
                  op.origin_source_record_id::text,
                  op.created_at::text, op.updated_at::text
                from crm.opportunity op
                left join crm.organization o on o.id = op.organization_id
                where op.id = %s::uuid
            """, (opportunity_id,))
            row = cur.fetchone()
            if row is None:
                return None
            (
                opp_id, title, stage, org_id, org_name,
                close_reason, closed_at, origin_sr_id, created_at, updated_at,
            ) = row

            # quotes + revisions
            cur.execute("""
                select
                  q.id::text, q.quote_number, q.number_origin,
                  qr.id::text, qr.revision_no, qr.status, qr.origin,
                  qr.pdf_sha256, qr.sent_at::text,
                  qr.grand_total::text, qr.quote_currency,
                  qr.superseded_by_revision_no,
                  qr.origin_source_record_id::text
                from crm.quote q
                join crm.quote_revision qr on qr.quote_id = q.id
                where q.opportunity_id = %s::uuid
                order by q.created_at, q.id, qr.revision_no
            """, (opportunity_id,))
            rev_rows = cur.fetchall()

            # group by quote
            quotes_map: dict[str, dict[str, Any]] = {}
            for (
                qid, qnum, num_origin,
                rid, rev_no, status, rev_origin,
                pdf, sent_at, grand_total, currency,
                superseded_by, origin_sr,
            ) in rev_rows:
                if qid not in quotes_map:
                    quotes_map[qid] = {
                        "quote_id": qid,
                        "quote_number": qnum,
                        "number_origin": num_origin,
                        "revisions": [],
                    }
                quotes_map[qid]["revisions"].append({
                    "revision_id": rid,
                    "revision_no": rev_no,
                    "status": status,
                    "origin": rev_origin,
                    "pdf_sha256": pdf,
                    "sent_at": sent_at,
                    "grand_total": grand_total,
                    "quote_currency": currency,
                    "superseded_by_revision_no": superseded_by,
                    "origin_source_record_id": origin_sr,
                    "is_canonical": False,  # filled below
                })

            # mark canonical
            quotes: list[dict[str, Any]] = []
            for qdata in quotes_map.values():
                active = [
                    r for r in qdata["revisions"]
                    if r["status"] != "void" and r["superseded_by_revision_no"] is None
                ]
                if len(active) == 1:
                    active[0]["is_canonical"] = True
                qdata["has_conflict"] = len(active) > 1
                quotes.append(qdata)

            # case organizations
            cur.execute("""
                select
                  oo.id::text, oo.organization_id::text,
                  org2.name, oo.role, oo.confirmation
                from crm.opportunity_organization oo
                join crm.organization org2 on org2.id = oo.organization_id
                where oo.opportunity_id = %s::uuid and oo.valid_to is null
                order by oo.created_at
            """, (opportunity_id,))
            case_orgs = [
                {
                    "id": r[0], "organization_id": r[1],
                    "organization_name": r[2], "role": r[3], "confirmation": r[4],
                }
                for r in cur.fetchall()
            ]

            # evidence links
            cur.execute("""
                select
                  oe.id::text, oe.relation,
                  oe.source_record_id::text, oe.assertion_id::text,
                  oe.linked_at::text, oe.unlinked_at::text
                from crm.opportunity_evidence oe
                where oe.opportunity_id = %s::uuid
                order by oe.linked_at
            """, (opportunity_id,))
            evidence_links = [
                {
                    "evidence_id": r[0], "relation": r[1],
                    "source_record_id": r[2], "assertion_id": r[3],
                    "linked_at": r[4], "unlinked_at": r[5],
                }
                for r in cur.fetchall()
            ]

            # evidence completeness
            cur.execute("""
                select exists (
                  select 1 from crm.opportunity_evidence oe
                  join evidence.source_record sr on sr.id = oe.source_record_id
                  where oe.opportunity_id = %s::uuid
                    and sr.kind = 'gmail_message'
                    and oe.unlinked_at is null
                )
            """, (opportunity_id,))
            has_gmail = bool(cur.fetchone()[0])

            cur.execute("""
                select not exists (
                  select 1 from crm.quote q
                  join crm.quote_revision qr on qr.quote_id = q.id
                  where q.opportunity_id = %s::uuid
                    and qr.status = 'sent'
                    and qr.pdf_sha256 is null
                )
            """, (opportunity_id,))
            all_revisions_have_pdf = bool(cur.fetchone()[0])

        return {
            "opportunity_id": opp_id,
            "title": title,
            "stage": stage,
            "organization_id": org_id,
            "organization_name": org_name,
            "close_reason": close_reason,
            "closed_at": closed_at,
            "origin_source_record_id": origin_sr_id,
            "created_at": created_at,
            "updated_at": updated_at,
            "quotes": quotes,
            "case_organizations": case_orgs,
            "evidence_links": evidence_links,
            "evidence_completeness": {
                "has_gmail_source": has_gmail,
                "all_revisions_have_pdf": all_revisions_have_pdf,
            },
        }

    # --------------------------------------------------------------- quotations

    def quotations(
        self,
        *,
        quote_number: str | None,
        organization_id: str | None,
        opportunity_id: str | None,
        gmail_message_id: str | None,
        status: str | None,
        number_origin: str | None,
        date_from: str | None,
        date_to: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        clauses: list[str] = []
        params: list[Any] = []

        if quote_number:
            if len(quote_number) > 0:
                clauses.append("q.quote_number like %s")
                params.append(quote_number + "%")
        if organization_id:
            clauses.append("op.organization_id = %s::uuid")
            params.append(organization_id)
        if opportunity_id:
            clauses.append("q.opportunity_id = %s::uuid")
            params.append(opportunity_id)
        if gmail_message_id:
            # match inside payload of linked source_record
            clauses.append("""
                exists (
                  select 1 from crm.quote_revision qr2
                  join evidence.source_record sr2 on sr2.id = qr2.origin_source_record_id
                  where qr2.quote_id = q.id
                    and sr2.payload->>'gmail_message_id' = %s
                )
            """)
            params.append(gmail_message_id)
        if status:
            clauses.append("""
                exists (
                  select 1 from crm.quote_revision qr3
                  where qr3.quote_id = q.id and qr3.status = %s
                )
            """)
            params.append(status)
        if number_origin:
            clauses.append("q.number_origin = %s")
            params.append(number_origin)
        if date_from:
            clauses.append("q.created_at >= %s::timestamptz")
            params.append(date_from)
        if date_to:
            clauses.append("q.created_at < %s::timestamptz")
            params.append(date_to)

        where = ("where " + " and ".join(clauses)) if clauses else ""

        with self._read() as cur:
            count_sql = f"""
                select count(*) from crm.quote q
                join crm.opportunity op on op.id = q.opportunity_id
                left join crm.organization o on o.id = op.organization_id
                {where}
            """
            cur.execute(count_sql, params)
            total = int(cur.fetchone()[0])

            sql = f"""
                select
                  q.id::text                                        as quote_id,
                  q.quote_number,
                  q.number_origin,
                  op.id::text                                       as opportunity_id,
                  op.title                                          as opportunity_title,
                  op.organization_id::text                          as organization_id,
                  o.name                                            as organization_name,
                  (select count(*) from crm.quote_revision qr
                    where qr.quote_id = q.id
                      and qr.status = 'sent'
                      and qr.superseded_by_revision_no is null)    as sent_revision_count,
                  (select max(qr.sent_at)::text from crm.quote_revision qr
                    where qr.quote_id = q.id and qr.status = 'sent') as latest_sent_at,
                  (select qr.pdf_sha256 from crm.quote_revision qr
                    where qr.quote_id = q.id and qr.status = 'sent'
                      and qr.superseded_by_revision_no is null
                    order by qr.revision_no desc limit 1)           as latest_pdf_sha256,
                  (select count(*) from crm.quote_revision qr
                    where qr.quote_id = q.id
                      and qr.status <> 'void'
                      and qr.superseded_by_revision_no is null) > 1 as has_conflict,
                  q.created_at::text                                as created_at
                from crm.quote q
                join crm.opportunity op on op.id = q.opportunity_id
                left join crm.organization o on o.id = op.organization_id
                {where}
                order by q.created_at desc, q.id
                limit %s offset %s
            """
            cur.execute(sql, (*params, limit, offset))
            cols = [d[0] for d in cur.description]
            items = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def quotation_detail(self, quote_id: str) -> dict[str, Any] | None:
        with self._read() as cur:
            cur.execute("""
                select
                  q.id::text, q.quote_number, q.number_origin,
                  op.id::text, op.title,
                  op.organization_id::text, o.name,
                  q.created_at::text
                from crm.quote q
                join crm.opportunity op on op.id = q.opportunity_id
                left join crm.organization o on o.id = op.organization_id
                where q.id = %s::uuid
            """, (quote_id,))
            row = cur.fetchone()
            if row is None:
                return None
            qid, qnum, num_origin, opp_id, opp_title, org_id, org_name, created_at = row

            cur.execute("""
                select
                  qr.id::text, qr.revision_no, qr.status, qr.origin,
                  qr.pdf_sha256, qr.sent_at::text,
                  qr.grand_total::text, qr.quote_currency,
                  qr.superseded_by_revision_no,
                  qr.origin_source_record_id::text
                from crm.quote_revision qr
                where qr.quote_id = %s::uuid
                order by qr.revision_no
            """, (quote_id,))
            revisions = []
            for r in cur.fetchall():
                revisions.append({
                    "revision_id": r[0], "revision_no": r[1], "status": r[2],
                    "origin": r[3], "pdf_sha256": r[4], "sent_at": r[5],
                    "grand_total": r[6], "quote_currency": r[7],
                    "superseded_by_revision_no": r[8],
                    "origin_source_record_id": r[9],
                    "is_canonical": False,
                })

            active = [
                rev for rev in revisions
                if rev["status"] != "void" and rev["superseded_by_revision_no"] is None
            ]
            if len(active) == 1:
                active[0]["is_canonical"] = True
            canonical = active[0] if len(active) == 1 else None

            # gmail sources from revisions
            source_record_ids = [
                r["origin_source_record_id"]
                for r in revisions
                if r["origin_source_record_id"]
            ]
            gmail_sources: list[dict[str, Any]] = []
            if source_record_ids:
                placeholders = ",".join(["%s::uuid"] * len(source_record_ids))
                cur.execute(f"""
                    select
                      sr.id::text,
                      sr.payload->>'gmail_message_id',
                      sr.payload->>'gmail_thread_id',
                      sr.payload->>'sender',
                      sr.payload->>'subject_raw',
                      sr.payload->>'sent_at'
                    from evidence.source_record sr
                    where sr.id in ({placeholders})
                      and sr.kind = 'gmail_message'
                """, source_record_ids)
                for r in cur.fetchall():
                    gmail_sources.append({
                        "source_record_id": r[0],
                        "gmail_message_id": r[1],
                        "gmail_thread_id": r[2],
                        "sender": r[3],
                        "subject_raw": r[4],
                        "sent_at": r[5],
                    })

            # conflicts
            conflicts: list[dict[str, Any]] = []
            if len(active) > 1:
                conflicts.append({
                    "kind": "canonical_undetermined",
                    "description": (
                        f"Quote {qnum!r} has {len(active)} active non-void revisions; "
                        "canonical cannot be determined."
                    ),
                    "related_opportunity_ids": [],
                })
            if num_origin == "printed_historical":
                cur.execute("""
                    select distinct q2.opportunity_id::text
                    from crm.quote q2
                    where q2.quote_number = %s and q2.id <> %s::uuid
                """, (qnum, quote_id))
                other_opps = [r[0] for r in cur.fetchall()]
                if other_opps:
                    conflicts.append({
                        "kind": "shared_printed_number",
                        "description": (
                            f"Printed number {qnum!r} also appears on "
                            f"{len(other_opps)} other opportunit"
                            + ("y." if len(other_opps) == 1 else "ies.")
                        ),
                        "related_opportunity_ids": other_opps,
                    })

        return {
            "quote_id": qid,
            "quote_number": qnum,
            "number_origin": num_origin,
            "opportunity_id": opp_id,
            "opportunity_title": opp_title,
            "organization_id": org_id,
            "organization_name": org_name,
            "revisions": revisions,
            "canonical_revision": canonical,
            "gmail_sources": gmail_sources,
            "conflicts": conflicts,
            "created_at": created_at,
        }

    # --------------------------------------------------------------- timeline

    def opportunity_timeline(self, opportunity_id: str) -> dict[str, Any] | None:
        with self._read() as cur:
            # Confirm opportunity exists
            cur.execute(
                "select exists (select 1 from crm.opportunity where id = %s::uuid)",
                (opportunity_id,),
            )
            if not cur.fetchone()[0]:
                return None

            # Collect aggregate_ids: the opportunity + its quotes + revisions
            cur.execute("""
                select
                  q.id::text   as quote_id,
                  qr.id::text  as revision_id
                from crm.quote q
                left join crm.quote_revision qr on qr.quote_id = q.id
                where q.opportunity_id = %s::uuid
            """, (opportunity_id,))
            agg_ids = {opportunity_id}
            for qid, rid in cur.fetchall():
                if qid:
                    agg_ids.add(qid)
                if rid:
                    agg_ids.add(rid)

            entries: list[dict[str, Any]] = []

            if agg_ids:
                placeholders = ",".join(["%s::uuid"] * len(agg_ids))
                agg_list = list(agg_ids)
                cur.execute(f"""
                    select
                      de.aggregate_kind,
                      de.aggregate_id::text,
                      de.event_type,
                      de.actor_operator_id::text,
                      de.payload,
                      de.recorded_at::text
                    from crm.domain_event de
                    where de.aggregate_id in ({placeholders})
                    order by de.recorded_at, de.stream_position
                """, agg_list)
                for r in cur.fetchall():
                    entries.append({
                        "entry_kind": "domain_event",
                        "occurred_at": r[5],
                        "event_type": r[2],
                        "aggregate_kind": r[0],
                        "aggregate_id": r[1],
                        "actor_operator_id": r[3],
                        "payload": r[4],
                        "quote_id": None,
                        "quote_number": None,
                        "revision_no": None,
                        "pdf_sha256": None,
                    })

            # add quotation_sent synthetic entries
            cur.execute("""
                select
                  q.id::text, q.quote_number,
                  qr.revision_no, qr.pdf_sha256, qr.sent_at::text
                from crm.quote q
                join crm.quote_revision qr on qr.quote_id = q.id
                where q.opportunity_id = %s::uuid
                  and qr.status = 'sent'
                  and qr.sent_at is not null
                order by qr.sent_at, q.id, qr.revision_no
            """, (opportunity_id,))
            for r in cur.fetchall():
                entries.append({
                    "entry_kind": "quotation_sent",
                    "occurred_at": r[4],
                    "event_type": None,
                    "aggregate_kind": None,
                    "aggregate_id": None,
                    "actor_operator_id": None,
                    "payload": None,
                    "quote_id": r[0],
                    "quote_number": r[1],
                    "revision_no": r[2],
                    "pdf_sha256": r[3],
                })

            entries.sort(key=lambda e: e["occurred_at"] or "")

        return {"opportunity_id": opportunity_id, "entries": entries}

    # ---------------------------------------------------------- evidence drawer

    def evidence_drawer(self, source_record_id: str) -> dict[str, Any] | None:
        with self._read() as cur:
            cur.execute("""
                select
                  sr.id::text, sr.kind, sr.review_status,
                  sr.is_quarantined, sr.acquired_at::text, sr.payload
                from evidence.source_record sr
                where sr.id = %s::uuid
            """, (source_record_id,))
            row = cur.fetchone()
            if row is None:
                return None
            sr_id, kind, review_status, is_quarantined, acquired_at, payload = row

            # Extract Gmail fields from payload — never the body
            gmail_message_id = payload.get("gmail_message_id") if payload else None
            gmail_thread_id = payload.get("gmail_thread_id") if payload else None
            sender = payload.get("sender") if payload else None
            recipients = payload.get("recipients") if payload else None
            subject_raw = payload.get("subject_raw") if payload else None
            sent_at = payload.get("sent_at") if payload else None

            raw_docs = (payload.get("documents") or []) if payload else []
            documents = [
                {
                    "sha256": d.get("sha256"),
                    "filename": d.get("filename"),
                    "bytes": d.get("bytes"),
                }
                for d in raw_docs
                if isinstance(d, dict)
            ]

            # assertions
            cur.execute("""
                select
                  a.id::text, a.kind, a.value_norm, a.resolution,
                  a.resolved_kind, a.resolved_id::text,
                  a.resolved_at::text, a.ambiguity_note
                from evidence.assertion a
                where a.source_record_id = %s::uuid
                order by a.created_at, a.id
            """, (source_record_id,))
            assertions = [
                {
                    "assertion_id": r[0], "kind": r[1], "value_norm": r[2],
                    "resolution": r[3], "resolved_kind": r[4], "resolved_id": r[5],
                    "resolved_at": r[6], "ambiguity_note": r[7],
                }
                for r in cur.fetchall()
            ]

            # audit events on the source record itself
            cur.execute("""
                select
                  de.event_type, de.aggregate_kind,
                  de.aggregate_id::text, de.payload, de.recorded_at::text
                from crm.domain_event de
                where de.aggregate_kind = 'source_record'
                  and de.aggregate_id = %s::uuid
                order by de.seq
            """, (source_record_id,))
            decision_events = [
                {
                    "event_type": r[0],
                    "aggregate_kind": r[1],
                    "aggregate_id": r[2],
                    "payload": r[3],
                    "recorded_at": r[4],
                }
                for r in cur.fetchall()
            ]

        return {
            "source_record_id": sr_id,
            "kind": kind,
            "review_status": review_status,
            "is_quarantined": is_quarantined,
            "acquired_at": acquired_at,
            "gmail_message_id": gmail_message_id,
            "gmail_thread_id": gmail_thread_id,
            "sender": sender,
            "recipients": recipients,
            "subject_raw": subject_raw,
            "sent_at": sent_at,
            "documents": documents,
            "assertions": assertions,
            "decision_events": decision_events,
        }

    # ------------------------------------------------------------------ search

    def search(self, q: str) -> dict[str, Any]:
        """Exact / prefix search across quote numbers, Gmail ids, sha256, orgs, titles."""
        q_stripped = q.strip()
        hits: list[dict[str, Any]] = []

        with self._read() as cur:
            # quote_number exact or prefix
            cur.execute("""
                select q.id::text, q.quote_number, q.opportunity_id::text
                from crm.quote q
                where q.quote_number like %s
                limit %s
            """, (q_stripped + "%", MAX_SEARCH_HITS))
            for r in cur.fetchall():
                hits.append({
                    "match_kind": "quote_number",
                    "subject_kind": "quote",
                    "subject_id": r[0],
                    "label": r[1],
                    "opportunity_id": r[2],
                })

            # gmail_message_id exact
            cur.execute("""
                select sr.id::text, sr.payload->>'gmail_message_id'
                from evidence.source_record sr
                where sr.kind = 'gmail_message'
                  and sr.payload->>'gmail_message_id' = %s
                limit %s
            """, (q_stripped, MAX_SEARCH_HITS))
            for r in cur.fetchall():
                hits.append({
                    "match_kind": "gmail_message_id",
                    "subject_kind": "source_record",
                    "subject_id": r[0],
                    "label": r[1] or q_stripped,
                    "opportunity_id": None,
                })

            # gmail_thread_id exact
            cur.execute("""
                select sr.id::text, sr.payload->>'gmail_thread_id'
                from evidence.source_record sr
                where sr.kind = 'gmail_message'
                  and sr.payload->>'gmail_thread_id' = %s
                limit %s
            """, (q_stripped, MAX_SEARCH_HITS))
            for r in cur.fetchall():
                hits.append({
                    "match_kind": "gmail_thread_id",
                    "subject_kind": "source_record",
                    "subject_id": r[0],
                    "label": r[1] or q_stripped,
                    "opportunity_id": None,
                })

            # sha256 prefix (≥12 hex chars)
            if len(q_stripped) >= 12 and all(c in "0123456789abcdefABCDEF" for c in q_stripped):
                prefix = q_stripped.lower()
                cur.execute("""
                    select qr.id::text, qr.pdf_sha256, q.id::text, q.quote_number
                    from crm.quote_revision qr
                    join crm.quote q on q.id = qr.quote_id
                    where qr.pdf_sha256 like %s
                    limit %s
                """, (prefix + "%", MAX_SEARCH_HITS))
                for r in cur.fetchall():
                    hits.append({
                        "match_kind": "sha256_prefix",
                        "subject_kind": "quote",
                        "subject_id": r[2],
                        "label": r[3],
                        "opportunity_id": None,
                    })

            # organization name (case-insensitive contains)
            cur.execute("""
                select o.id::text, o.name
                from crm.organization o
                where lower(o.name) like %s
                  and o.merged_into_organization_id is null
                limit %s
            """, (f"%{q_stripped.lower()}%", MAX_SEARCH_HITS))
            for r in cur.fetchall():
                hits.append({
                    "match_kind": "organization_name",
                    "subject_kind": "organization",
                    "subject_id": r[0],
                    "label": r[1],
                    "opportunity_id": None,
                })

            # opportunity title (case-insensitive contains)
            cur.execute("""
                select op.id::text, op.title
                from crm.opportunity op
                where lower(op.title) like %s
                limit %s
            """, (f"%{q_stripped.lower()}%", MAX_SEARCH_HITS))
            for r in cur.fetchall():
                hits.append({
                    "match_kind": "opportunity_title",
                    "subject_kind": "opportunity",
                    "subject_id": r[0],
                    "label": r[1],
                    "opportunity_id": r[0],
                })

        truncated = len(hits) >= MAX_SEARCH_HITS
        return {
            "query": q_stripped,
            "hits": hits[:MAX_SEARCH_HITS],
            "truncated": truncated,
        }
