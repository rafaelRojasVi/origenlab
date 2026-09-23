"""Read-only repository over the V2 durable core.

Every query here runs inside `begin read only`, so this module cannot write to `crm.*` even
by accident. That is not decoration: `docs/CLAUDE.md` and `docs/ARCHITECTURE.md` route every
human CRM mutation through one path — route → service → repository → transaction →
append-only event — and a read boundary that *could* write is a second writer waiting to
happen.

The connection role is `origenlab_api`, which holds no membership in `origenlab_owner` and
cannot `SET ROLE` to it. RLS therefore constrains these reads exactly as it will in
production, rather than being silently bypassed by a privileged local login.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from origenlab_api.v2.identity import OperatorIdentity, OperatorLookup

#: Every listing is bounded. An unbounded operator list is a way to turn one careless request
#: into a full export of the contact database.
MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_PAGE_SIZE
    return max(1, min(int(limit), MAX_PAGE_SIZE))


@dataclass(frozen=True)
class Page:
    items: list[dict[str, Any]]
    total: int
    limit: int
    offset: int


class V2Repository(OperatorLookup):
    """Read queries over `crm.*`, `evidence.*` and `platform.*`."""

    def __init__(self, connect: Any, dsn: str, statement_timeout_ms: int = 30_000) -> None:
        self._connect = connect
        self._dsn = dsn
        self._statement_timeout_ms = statement_timeout_ms

    @contextmanager
    def _read(self) -> Iterator[Any]:
        """One read-only transaction, proven read-only by the server.

        `set transaction read only` comes first and `set local statement_timeout` second, so
        both are unambiguously inside the transaction the driver has already opened. The
        order matters: `set local` outside a transaction is silently a no-op, and a timeout
        that quietly did not apply is worse than no timeout at all.

        A write attempted in here fails with SQLSTATE 25006, which
        `test_a_write_is_refused_by_the_server` proves against a real database rather than
        assuming.
        """
        with self._connect(self._dsn, autocommit=False) as conn:
            with conn.cursor() as cur:
                cur.execute("set transaction read only")
                cur.execute(f"set local statement_timeout = {int(self._statement_timeout_ms)}")
                try:
                    yield cur
                finally:
                    conn.rollback()

    def _page(self, cur: Any, sql: str, count_sql: str, params: tuple[Any, ...],
              limit: int, offset: int) -> Page:
        cur.execute(count_sql, params)
        total = int(cur.fetchone()[0])
        cur.execute(sql, (*params, limit, offset))
        columns = [d[0] for d in cur.description]
        items = [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]
        return Page(items=items, total=total, limit=limit, offset=offset)

    # ------------------------------------------------------------------ identity

    def by_email(self, email_norm: str) -> OperatorIdentity | None:
        with self._read() as cur:
            cur.execute(
                "select id::text, email_norm, display_name, role, status "
                "from platform.operator where email_norm = %s",
                (email_norm,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return OperatorIdentity(
            operator_id=row[0],
            email_norm=row[1],
            display_name=row[2],
            role=row[3],
            status=row[4],
        )

    # ------------------------------------------------------------------ contacts

    def contacts(self, *, q: str | None, limit: int, offset: int) -> Page:
        """Contacts, which in V2 means contact points and the persons they resolve to.

        A contact point with no person is not an error and not a gap to be filled in: it is
        the honest state of a channel whose owner is unknown. The response says so with
        `usage` and `confirmation` rather than hiding it.
        """
        where = "where cp.kind = 'email'"
        params: tuple[Any, ...] = ()
        if q:
            where += " and cp.value_norm like %s"
            params = (f"%{q.strip().lower()}%",)
        sql = f"""
            select cp.id::text            as contact_point_id,
                   cp.value_display       as address,
                   cp.usage               as usage,
                   cp.confirmation        as confirmation,
                   p.id::text             as person_id,
                   p.display_name         as person_display_name,
                   o.id::text             as organization_id,
                   o.name                 as organization_name,
                   cp.created_at          as created_at
              from crm.contact_point cp
              left join crm.person p       on p.id = cp.person_id
              left join crm.organization o on o.id = cp.organization_id
              {where}
             order by cp.value_norm
             limit %s offset %s
        """
        count_sql = f"select count(*) from crm.contact_point cp {where}"
        with self._read() as cur:
            return self._page(cur, sql, count_sql, params, limit, offset)

    # ------------------------------------------------------------- organizations

    def organizations(self, *, q: str | None, limit: int, offset: int) -> Page:
        where = "where o.merged_into_organization_id is null"
        params: tuple[Any, ...] = ()
        if q:
            where += " and lower(o.name) like %s"
            params = (f"%{q.strip().lower()}%",)
        sql = f"""
            select o.id::text       as organization_id,
                   o.name           as name,
                   o.kind           as kind,
                   o.confirmation   as confirmation,
                   o.parent_organization_id::text as parent_organization_id,
                   (select count(*) from crm.contact_point cp
                     where cp.organization_id = o.id) as contact_point_count,
                   o.created_at     as created_at
              from crm.organization o
              {where}
             order by lower(o.name)
             limit %s offset %s
        """
        count_sql = f"select count(*) from crm.organization o {where}"
        with self._read() as cur:
            return self._page(cur, sql, count_sql, params, limit, offset)

    # ------------------------------------------------- prospects, leads, opportunities

    #: `lead` is a stage, not an entity (`docs/DOMAIN.md` §3.2). A prospect or lead is an
    #: opportunity that has not yet been qualified, so both are read from `crm.opportunity`
    #: by stage — there is no prospect table and there must not be one.
    PROSPECT_STAGES: tuple[str, ...] = ("lead", "qualifying")
    CLOSED_STAGES: tuple[str, ...] = ("won", "lost", "abandoned")

    def prospects(self, *, limit: int, offset: int) -> Page:
        return self._opportunities_by_stage(
            stages=self.PROSPECT_STAGES, include=True, limit=limit, offset=offset
        )

    def active_opportunities(self, *, limit: int, offset: int) -> Page:
        return self._opportunities_by_stage(
            stages=self.CLOSED_STAGES, include=False, limit=limit, offset=offset
        )

    def _opportunities_by_stage(
        self, *, stages: tuple[str, ...], include: bool, limit: int, offset: int
    ) -> Page:
        operator = "=" if include else "<>"
        joiner = " or " if include else " and "
        predicate = joiner.join(f"op.stage {operator} %s" for _ in stages)
        where = f"where ({predicate})"
        params: tuple[Any, ...] = tuple(stages)
        sql = f"""
            select op.id::text     as opportunity_id,
                   op.title        as title,
                   op.stage        as stage,
                   o.id::text      as organization_id,
                   o.name          as organization_name,
                   op.created_at   as created_at,
                   op.updated_at   as updated_at
              from crm.opportunity op
              left join crm.organization o on o.id = op.organization_id
              {where}
             order by op.updated_at desc
             limit %s offset %s
        """
        count_sql = f"select count(*) from crm.opportunity op {where}"
        with self._read() as cur:
            return self._page(cur, sql, count_sql, params, limit, offset)

    # ----------------------------------------------------------------- follow-ups

    def tasks_due(self, *, horizon_days: int, limit: int, offset: int) -> Page:
        """Follow-ups that are open and due on or before the horizon.

        `status = 'done' ⇔ completed_at IS NOT NULL` is the table's own invariant
        (`task_done_shape`), and `cancelled` is a third state that is neither. Open-ness is
        therefore `status = 'open'`, which is the one reading that agrees with both.
        """
        where = (
            "where t.status = 'open' "
            "and t.due_at is not null "
            "and t.due_at < (now() + make_interval(days => %s))"
        )
        params: tuple[Any, ...] = (int(horizon_days),)
        sql = f"""
            select t.id::text    as task_id,
                   t.title       as title,
                   t.due_at      as due_at,
                   (t.due_at < now()) as overdue,
                   op.id::text   as opportunity_id,
                   op.title      as opportunity_title,
                   o.name        as organization_name
              from crm.task t
              left join crm.opportunity op  on op.id = t.opportunity_id
              left join crm.organization o  on o.id = op.organization_id
              {where}
             order by t.due_at
             limit %s offset %s
        """
        count_sql = f"select count(*) from crm.task t {where}"
        with self._read() as cur:
            return self._page(cur, sql, count_sql, params, limit, offset)

    # --------------------------------------------------------------- review queue

    def review_summary(self) -> dict[str, int]:
        """What a human still has to decide.

        Three populations, counted separately because they need different decisions:
        ambiguous assertions (the migration stopped and asked), machine-proposed identities
        (the migration transcribed an observation and wants it confirmed), and unresolved
        assertions (nothing has looked at them yet).
        """
        with self._read() as cur:
            cur.execute(
                """
                select
                  (select count(*) from evidence.assertion where resolution = 'ambiguous')
                      as ambiguous_assertions,
                  (select count(*) from evidence.assertion where resolution = 'unresolved')
                      as unresolved_assertions,
                  (select count(*) from crm.organization
                    where confirmation = 'machine_proposed'
                      and merged_into_organization_id is null)
                      as machine_proposed_organizations,
                  (select count(*) from crm.contact_point where confirmation = 'machine_proposed')
                      as machine_proposed_contact_points,
                  (select count(*) from crm.contact_point where usage = 'unattributed')
                      as unattributed_contact_points
                """
            )
            columns = [d[0] for d in cur.description]
            return dict(zip(columns, (int(v) for v in cur.fetchone()), strict=True))

    # -------------------------------------------------------------------- quotes

    def quotes_to_follow_up(self, *, limit: int, offset: int) -> Page:
        """Quotes whose latest revision was sent and which are not yet resolved.

        `crm.quote` owns the number; `crm.quote_revision` owns the status, so "sent and still
        open" is a fact about the newest revision, not about the quote. The newest revision
        is found by `revision_no`, not by `superseded_by_revision_no`: a revision can be the
        latest without anything having superseded it, which is precisely the case here.
        """
        where = """
            where r.status = 'sent'
              and not exists (
                select 1 from crm.quote_revision later
                 where later.quote_id = q.id and later.revision_no > r.revision_no
              )
        """
        params: tuple[Any, ...] = ()
        sql = f"""
            select q.id::text     as quote_id,
                   q.quote_number as quote_number,
                   r.revision_no  as revision_no,
                   r.status       as status,
                   r.sent_at      as sent_at,
                   op.id::text    as opportunity_id,
                   o.name         as organization_name,
                   r.updated_at   as updated_at
              from crm.quote q
              join crm.quote_revision r     on r.quote_id = q.id
              left join crm.opportunity op  on op.id = q.opportunity_id
              left join crm.organization o  on o.id = op.organization_id
              {where}
             order by r.updated_at desc
             limit %s offset %s
        """
        count_sql = f"""
            select count(*) from crm.quote q
              join crm.quote_revision r on r.quote_id = q.id
              {where}
        """
        with self._read() as cur:
            return self._page(cur, sql, count_sql, params, limit, offset)

    # ---------------------------------------------------------------- the cards

    #: Every child list on a card is bounded. A card is a summary an operator reads, not an
    #: export: an organization with ten thousand channels must render, and the count beside
    #: the list is what tells the truth about how many there are.
    CARD_CHILD_LIMIT = 100

    def _rows(self, cur: Any, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        cur.execute(sql, params)
        columns = [d[0] for d in cur.description]
        return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]

    def _scalar(self, cur: Any, sql: str, params: tuple[Any, ...]) -> int:
        cur.execute(sql, params)
        return int(cur.fetchone()[0])

    def contact_card(self, contact_point_id: str) -> dict[str, Any] | None:
        """One channel, everything known about it, and where each fact came from.

        The card is deliberately assembled from separate bounded queries rather than one
        wide join: a channel with no person, no organization, no evidence and no marketing
        history is the *common* case in the migrated data, and a join would render that as a
        row of nulls instead of four empty lists that each say what is missing.

        `evidence` is the provenance the operator needs to act. Every fact on this card
        traces to an `evidence.source_record`, and the card reports that record's kind, URI
        and review status rather than asserting the fact on its own authority.
        """
        with self._read() as cur:
            head = self._rows(
                cur,
                """
                select cp.id::text          as contact_point_id,
                       cp.kind              as channel_kind,
                       cp.value_display     as address,
                       cp.value_norm        as address_norm,
                       cp.usage             as usage,
                       cp.confirmation      as confirmation,
                       cp.created_at        as created_at,
                       cp.updated_at        as updated_at,
                       p.id::text           as person_id,
                       p.display_name       as person_display_name,
                       o.id::text           as organization_id,
                       o.name               as organization_name,
                       o.kind               as organization_kind,
                       sr.kind              as origin_source_kind,
                       sr.source_uri        as origin_source_uri,
                       sr.review_status     as origin_review_status
                  from crm.contact_point cp
                  left join crm.person p          on p.id = cp.person_id
                  left join crm.organization o    on o.id = cp.organization_id
                  left join evidence.source_record sr on sr.id = cp.origin_source_record_id
                 where cp.id = %s::uuid
                """,
                (contact_point_id,),
            )
            if not head:
                return None
            row = head[0]
            address_norm = row.pop("address_norm")
            person_id = row["person_id"]

            siblings = (
                self._rows(
                    cur,
                    """
                    select cp.id::text      as contact_point_id,
                           cp.value_display as address,
                           cp.kind          as channel_kind,
                           cp.usage         as usage,
                           cp.confirmation  as confirmation
                      from crm.contact_point cp
                     where cp.person_id = %s::uuid and cp.id <> %s::uuid
                     order by cp.value_norm
                     limit %s
                    """,
                    (person_id, contact_point_id, self.CARD_CHILD_LIMIT),
                )
                if person_id
                else []
            )

            affiliations = (
                self._rows(
                    cur,
                    """
                    select a.id::text        as affiliation_id,
                           o.id::text        as organization_id,
                           o.name            as organization_name,
                           a.role_title      as role_title,
                           a.unit_label      as unit_label,
                           a.confirmation    as confirmation,
                           a.valid_from      as valid_from,
                           a.valid_to        as valid_to
                      from crm.affiliation a
                      join crm.organization o on o.id = a.organization_id
                     where a.person_id = %s::uuid
                     order by a.valid_from desc nulls last
                     limit %s
                    """,
                    (person_id, self.CARD_CHILD_LIMIT),
                )
                if person_id
                else []
            )

            evidence = self._rows(
                cur,
                """
                select a.id::text        as assertion_id,
                       a.kind            as kind,
                       a.value_norm      as value_norm,
                       a.resolution      as resolution,
                       a.resolved_kind   as resolved_kind,
                       a.ambiguity_note  as ambiguity_note,
                       a.created_at      as observed_at,
                       sr.kind           as source_kind,
                       sr.source_uri     as source_uri,
                       sr.review_status  as source_review_status,
                       sr.is_quarantined as source_is_quarantined
                  from evidence.assertion a
                  join evidence.source_record sr on sr.id = a.source_record_id
                 where (a.resolved_kind = 'contact_point' and a.resolved_id = %s::uuid)
                    or a.value_norm = %s
                 order by a.created_at desc
                 limit %s
                """,
                (contact_point_id, address_norm, self.CARD_CHILD_LIMIT),
            )

            marketing = self._rows(
                cur,
                """
                select c.name        as campaign_name,
                       c.status      as campaign_status,
                       r.state       as recipient_state,
                       r.attempt_count as attempt_count,
                       r.created_at  as created_at
                  from outbound.campaign_recipient r
                  join outbound.campaign c on c.id = r.campaign_id
                 where r.contact_point_id = %s::uuid
                 order by r.created_at desc
                 limit %s
                """,
                (contact_point_id, self.CARD_CHILD_LIMIT),
            )

            #: A suppression is a fact about an *address*, never about an identity
            #: (`docs/STATUS.md` §2.7.4), so it is matched by `value_norm` and reported
            #: beside the card rather than attached to it.
            suppressions = self._rows(
                cur,
                """
                select cc.kind       as control_kind,
                       cc.purpose    as purpose,
                       cc.scope      as scope,
                       cc.reason     as reason,
                       cc.source     as source,
                       cc.until_at   as until_at,
                       cc.needs_review as needs_review,
                       cc.created_at as created_at
                  from outbound.contact_control cc
                 where cc.scope = 'address' and cc.value_norm = %s
                 order by cc.created_at desc
                 limit %s
                """,
                (address_norm, self.CARD_CHILD_LIMIT),
            )

            return {
                **row,
                "sibling_contact_points": siblings,
                "affiliations": affiliations,
                "evidence": evidence,
                "marketing": marketing,
                "address_controls": suppressions,
                "counts": {
                    "sibling_contact_points": self._scalar(
                        cur,
                        "select count(*) from crm.contact_point where person_id = %s::uuid and id <> %s::uuid",
                        (person_id, contact_point_id),
                    )
                    if person_id
                    else 0,
                    "affiliations": self._scalar(
                        cur,
                        "select count(*) from crm.affiliation where person_id = %s::uuid",
                        (person_id,),
                    )
                    if person_id
                    else 0,
                    "evidence": self._scalar(
                        cur,
                        "select count(*) from evidence.assertion a "
                        "where (a.resolved_kind = 'contact_point' and a.resolved_id = %s::uuid) "
                        "or a.value_norm = %s",
                        (contact_point_id, address_norm),
                    ),
                    "marketing": self._scalar(
                        cur,
                        "select count(*) from outbound.campaign_recipient where contact_point_id = %s::uuid",
                        (contact_point_id,),
                    ),
                    "address_controls": self._scalar(
                        cur,
                        "select count(*) from outbound.contact_control "
                        "where scope = 'address' and value_norm = %s",
                        (address_norm,),
                    ),
                },
            }

    def organization_card(self, organization_id: str) -> dict[str, Any] | None:
        """One organization, its channels, its people, its domains and its provenance.

        A domain is a routing hint and never an identity key (`docs/DOMAIN.md` §2.2), so the
        domains listed here are the ones somebody recorded against this organization — not
        every address that happens to share a mail domain with it.
        """
        with self._read() as cur:
            head = self._rows(
                cur,
                """
                select o.id::text        as organization_id,
                       o.name            as name,
                       o.legal_name      as legal_name,
                       o.kind            as kind,
                       o.confirmation    as confirmation,
                       o.note            as note,
                       o.version         as version,
                       o.created_at      as created_at,
                       o.updated_at      as updated_at,
                       parent.id::text   as parent_organization_id,
                       parent.name       as parent_organization_name,
                       merged.id::text   as merged_into_organization_id,
                       merged.name       as merged_into_organization_name,
                       sr.kind           as origin_source_kind,
                       sr.source_uri     as origin_source_uri,
                       sr.review_status  as origin_review_status
                  from crm.organization o
                  left join crm.organization parent on parent.id = o.parent_organization_id
                  left join crm.organization merged on merged.id = o.merged_into_organization_id
                  left join evidence.source_record sr on sr.id = o.origin_source_record_id
                 where o.id = %s::uuid
                """,
                (organization_id,),
            )
            if not head:
                return None
            row = head[0]

            contact_points = self._rows(
                cur,
                """
                select cp.id::text      as contact_point_id,
                       cp.value_display as address,
                       cp.kind          as channel_kind,
                       cp.usage         as usage,
                       cp.confirmation  as confirmation,
                       p.display_name   as person_display_name
                  from crm.contact_point cp
                  left join crm.person p on p.id = cp.person_id
                 where cp.organization_id = %s::uuid
                 order by cp.value_norm
                 limit %s
                """,
                (organization_id, self.CARD_CHILD_LIMIT),
            )

            people = self._rows(
                cur,
                """
                select p.id::text     as person_id,
                       p.display_name as display_name,
                       a.role_title   as role_title,
                       a.unit_label   as unit_label,
                       a.confirmation as confirmation,
                       a.valid_from   as valid_from,
                       a.valid_to     as valid_to
                  from crm.affiliation a
                  join crm.person p on p.id = a.person_id
                 where a.organization_id = %s::uuid
                 order by p.display_name
                 limit %s
                """,
                (organization_id, self.CARD_CHILD_LIMIT),
            )

            domains = self._rows(
                cur,
                """
                select d.id::text    as organization_domain_id,
                       d.domain_norm as domain,
                       d.scope       as scope,
                       d.created_at  as created_at
                  from crm.organization_domain d
                 where d.organization_id = %s::uuid
                 order by d.domain_norm
                 limit %s
                """,
                (organization_id, self.CARD_CHILD_LIMIT),
            )

            relationships = self._rows(
                cur,
                """
                select r.id::text   as organization_relationship_id,
                       r.role       as role,
                       r.valid_from as valid_from,
                       r.valid_to   as valid_to,
                       r.note       as note
                  from crm.organization_relationship r
                 where r.organization_id = %s::uuid
                 order by r.valid_from desc nulls last
                 limit %s
                """,
                (organization_id, self.CARD_CHILD_LIMIT),
            )

            children = self._rows(
                cur,
                """
                select c.id::text     as organization_id,
                       c.name         as name,
                       c.kind         as kind,
                       c.confirmation as confirmation
                  from crm.organization c
                 where c.parent_organization_id = %s::uuid
                   and c.merged_into_organization_id is null
                 order by lower(c.name)
                 limit %s
                """,
                (organization_id, self.CARD_CHILD_LIMIT),
            )

            evidence = self._rows(
                cur,
                """
                select a.id::text        as assertion_id,
                       a.kind            as kind,
                       a.value_norm      as value_norm,
                       a.resolution      as resolution,
                       a.resolved_kind   as resolved_kind,
                       a.ambiguity_note  as ambiguity_note,
                       a.created_at      as observed_at,
                       sr.kind           as source_kind,
                       sr.source_uri     as source_uri,
                       sr.review_status  as source_review_status,
                       sr.is_quarantined as source_is_quarantined
                  from evidence.assertion a
                  join evidence.source_record sr on sr.id = a.source_record_id
                 where a.resolved_kind = 'organization' and a.resolved_id = %s::uuid
                 order by a.created_at desc
                 limit %s
                """,
                (organization_id, self.CARD_CHILD_LIMIT),
            )

            return {
                **row,
                "contact_points": contact_points,
                "people": people,
                "domains": domains,
                "relationships": relationships,
                "child_organizations": children,
                "evidence": evidence,
                "counts": {
                    "contact_points": self._scalar(
                        cur,
                        "select count(*) from crm.contact_point where organization_id = %s::uuid",
                        (organization_id,),
                    ),
                    "people": self._scalar(
                        cur,
                        "select count(*) from crm.affiliation where organization_id = %s::uuid",
                        (organization_id,),
                    ),
                    "domains": self._scalar(
                        cur,
                        "select count(*) from crm.organization_domain where organization_id = %s::uuid",
                        (organization_id,),
                    ),
                    "relationships": self._scalar(
                        cur,
                        "select count(*) from crm.organization_relationship where organization_id = %s::uuid",
                        (organization_id,),
                    ),
                    "child_organizations": self._scalar(
                        cur,
                        "select count(*) from crm.organization where parent_organization_id = %s::uuid "
                        "and merged_into_organization_id is null",
                        (organization_id,),
                    ),
                    "evidence": self._scalar(
                        cur,
                        "select count(*) from evidence.assertion "
                        "where resolved_kind = 'organization' and resolved_id = %s::uuid",
                        (organization_id,),
                    ),
                },
            }

    # ------------------------------------------------------------------ evidence

    #: The vocabularies are closed in the database (`evidence.assertion.kind`,
    #: `evidence.source_record.kind`, `evidence.assertion.resolution`). Repeating them here
    #: is what lets a bad filter be a 422 instead of a query that silently matches nothing.
    EVIDENCE_RESOLUTIONS: tuple[str, ...] = (
        "unresolved", "promoted", "linked", "rejected", "ambiguous",
    )
    EVIDENCE_SOURCE_KINDS: tuple[str, ...] = (
        "workbook_import", "chilecompra_notice", "migration_manifest",
        "v1_parse_failure", "v1_evidence_edge", "v1_supplier_candidate",
        "v1_historical_quote_candidate", "gmail_message", "drive_file",
    )

    def evidence(
        self,
        *,
        q: str | None,
        resolution: str | None,
        source_kind: str | None,
        limit: int,
        offset: int,
    ) -> Page:
        """The evidence trail, newest first, each row carrying its own provenance.

        This is the review queue in list form: `/v2/review/summary` says how many decisions
        are waiting and this says which. Unresolved and ambiguous rows are the actionable
        ones; promoted rows are kept visible because "where did this contact come from" is
        the question the whole evidence schema exists to answer.
        """
        where = "where true"
        params: tuple[Any, ...] = ()
        if q:
            where += " and a.value_norm like %s"
            params = (*params, f"%{q.strip().lower()}%")
        if resolution:
            where += " and a.resolution = %s"
            params = (*params, resolution)
        if source_kind:
            where += " and sr.kind = %s"
            params = (*params, source_kind)
        sql = f"""
            select a.id::text        as assertion_id,
                   a.kind            as kind,
                   a.value_norm      as value_norm,
                   a.resolution      as resolution,
                   a.resolved_kind   as resolved_kind,
                   a.resolved_id::text as resolved_id,
                   a.ambiguity_note  as ambiguity_note,
                   a.created_at      as observed_at,
                   sr.id::text       as source_record_id,
                   sr.kind           as source_kind,
                   sr.source_uri     as source_uri,
                   sr.review_status  as source_review_status,
                   sr.is_quarantined as source_is_quarantined,
                   sr.acquired_at    as acquired_at
              from evidence.assertion a
              join evidence.source_record sr on sr.id = a.source_record_id
              {where}
             order by a.created_at desc, a.id
             limit %s offset %s
        """
        count_sql = f"""
            select count(*) from evidence.assertion a
              join evidence.source_record sr on sr.id = a.source_record_id
              {where}
        """
        with self._read() as cur:
            return self._page(cur, sql, count_sql, params, limit, offset)

    # ----------------------------------------------- the review queue, record by record

    #: `evidence.source_record.review_status` — closed in the database, repeated here so a
    #: bad filter is a 422 rather than a page that silently matches nothing.
    EVIDENCE_REVIEW_STATUSES: tuple[str, ...] = ("pending", "reviewed", "promoted", "rejected")

    #: Payload keys the staging manifest writes for a Gmail record. They are read
    #: defensively with `->>` so a record of any other kind simply returns nulls rather than
    #: failing the query: the review queue is a queue of *records*, not of Gmail.
    RECORD_PAYLOAD_KEYS: tuple[str, ...] = (
        "subject", "from", "from_domain", "message_date", "thread_id",
    )

    def evidence_records(
        self,
        *,
        source_kind: str | None,
        review_status: str | None,
        limit: int,
        offset: int,
    ) -> Page:
        """The review queue grouped the way a human reviews it: one row per source record.

        `/v2/evidence` lists assertions, which is the right shape for "where did this fact
        come from" and the wrong shape for "what am I being asked to decide". A reviewer
        decides about a *message*: its sender, its subject, what it asserts, and whether any
        of that already exists in `crm.*`. So this returns the record with its assertions
        attached, plus the three matches that decide the answer:

        * the `crm.contact_point` whose folded address equals an asserted `contact_address`
          — which proves only that **the address exists**, never that a person is confirmed;
        * the `crm.organization` whose folded name equals an asserted `organization_name`;
        * the `crm.organization` reached through `crm.organization_domain` for the sender's
          domain — the only domain-to-organization link that is *evidence* rather than a
          guess.

        Everything here is a fact read from the database. It proposes no promotion, scores
        no candidate and matches no organization by resemblance: a domain that merely looks
        like an organization's name is an interpretation, and interpretation belongs to the
        human at the other end of this queue.
        """
        where = "where true"
        params: tuple[Any, ...] = ()
        if source_kind:
            where += " and sr.kind = %s"
            params = (*params, source_kind)
        if review_status:
            where += " and sr.review_status = %s"
            params = (*params, review_status)
        sql = f"""
            select sr.id::text        as source_record_id,
                   sr.kind            as source_kind,
                   sr.dedupe_key      as dedupe_key,
                   sr.source_uri      as source_uri,
                   sr.acquired_at     as acquired_at,
                   sr.review_status   as review_status,
                   sr.is_quarantined  as is_quarantined,
                   sr.payload->>'subject'      as subject,
                   sr.payload->>'from'         as from_address,
                   sr.payload->>'from_domain'  as from_domain,
                   sr.payload->>'message_date' as message_date,
                   sr.payload->>'thread_id'    as thread_id
              from evidence.source_record sr
              {where}
             order by sr.acquired_at desc, sr.id
             limit %s offset %s
        """
        count_sql = f"select count(*) from evidence.source_record sr {where}"
        with self._read() as cur:
            page = self._page(cur, sql, count_sql, params, limit, offset)
            ids = [row["source_record_id"] for row in page.items]
            if not ids:
                return page

            # Bounded per record, like every child list on a card. One of these records is
            # a bulk migration manifest carrying eleven thousand assertions; returning it
            # whole would be a response nobody asked for and a page nobody can render. The
            # true count travels beside the capped list so the surface can say so.
            assertions = self._rows(
                cur,
                f"""
                select source_record_id, assertion_id, kind, value_norm, resolution,
                       resolved_kind, resolved_id, ambiguity_note, assertion_total
                  from (
                    select a.source_record_id::text as source_record_id,
                           a.id::text               as assertion_id,
                           a.kind                   as kind,
                           a.value_norm             as value_norm,
                           a.resolution             as resolution,
                           a.resolved_kind          as resolved_kind,
                           a.resolved_id::text      as resolved_id,
                           a.ambiguity_note         as ambiguity_note,
                           count(*) over (partition by a.source_record_id) as assertion_total,
                           row_number() over (
                             partition by a.source_record_id order by a.kind, a.value_norm, a.id
                           ) as rn
                      from evidence.assertion a
                     where a.source_record_id = any(%s::uuid[])
                  ) ranked
                 where rn <= {self.CARD_CHILD_LIMIT}
                 order by kind, value_norm
                """,
                (ids,),
            )
            contact_matches = self._rows(
                cur,
                """
                select a.source_record_id::text as source_record_id,
                       a.value_norm             as value_norm,
                       cp.id::text              as contact_point_id,
                       cp.usage                 as usage,
                       cp.confirmation          as confirmation,
                       p.id::text               as person_id,
                       p.display_name           as person_display_name,
                       o.id::text               as organization_id,
                       o.name                   as organization_name
                  from evidence.assertion a
                  join crm.contact_point cp     on cp.value_norm = a.value_norm
                  left join crm.person p        on p.id = cp.person_id
                  left join crm.organization o  on o.id = cp.organization_id
                 where a.source_record_id = any(%s::uuid[])
                   and a.kind = 'contact_address'
                 order by a.value_norm
                 limit %s
                """,
                (ids, self.CARD_CHILD_LIMIT * len(ids)),
            )
            organization_matches = self._rows(
                cur,
                """
                select a.source_record_id::text as source_record_id,
                       a.value_norm             as value_norm,
                       o.id::text               as organization_id,
                       o.name                   as name,
                       o.confirmation           as confirmation
                  from evidence.assertion a
                  join crm.organization o on lower(o.name) = a.value_norm
                                         and o.merged_into_organization_id is null
                 where a.source_record_id = any(%s::uuid[])
                   and a.kind = 'organization_name'
                 order by a.value_norm
                 limit %s
                """,
                (ids, self.CARD_CHILD_LIMIT * len(ids)),
            )
            domain_matches = self._rows(
                cur,
                """
                select sr.id::text  as source_record_id,
                       o.id::text   as organization_id,
                       o.name       as name,
                       od.scope     as scope
                  from evidence.source_record sr
                  join crm.organization_domain od
                    on od.domain_norm = lower(sr.payload->>'from_domain')
                  join crm.organization o on o.id = od.organization_id
                 where sr.id = any(%s::uuid[])
                """,
                (ids,),
            )

        by_record: dict[str, dict[str, list[dict[str, Any]]]] = {
            record_id: {"assertions": [], "contact_matches": [], "organization_matches": []}
            for record_id in ids
        }
        totals: dict[str, int] = {}
        for bucket, rows in (
            ("assertions", assertions),
            ("contact_matches", contact_matches),
            ("organization_matches", organization_matches),
        ):
            for row in rows:
                record_id = row.pop("source_record_id")
                if bucket == "assertions":
                    totals[record_id] = int(row.pop("assertion_total"))
                if record_id in by_record:
                    by_record[record_id][bucket].append(row)
        domain_by_record = {row.pop("source_record_id"): row for row in domain_matches}

        for row in page.items:
            record_id = row["source_record_id"]
            row.update(by_record[record_id])
            row["assertion_total"] = totals.get(record_id, 0)
            row["domain_organization"] = domain_by_record.get(record_id)
        return page

    # --------------------------------------------------------------- commercial cases

    #: The parts an institution may hold on a case, in the order a card reads them:
    #: who asks first, who ends up using it second, and the rest afterwards. Sorting by
    #: this rather than alphabetically is what makes `mentioned` — the only part a machine
    #: may propose — sit last instead of in the middle of the human decisions.
    CASE_ROLE_ORDER: tuple[str, ...] = (
        "requesting_institution",
        "end_user_institution",
        "purchasing_agent",
        "funder",
        "supplier",
        "manufacturer",
        "mentioned",
    )

    def cases(self, *, stage: str | None, open_only: bool, limit: int, offset: int) -> Page:
        """Commercial cases, each with the institution that is asking and nothing inferred.

        The requesting institution is read from the **current** `crm.opportunity_organization`
        row — `valid_to is null` — and not from `crm.opportunity.organization_id`, even
        though §3.6.1 keeps the two equal. They are equal because a command writes both in
        one transaction, and a list that reads the denormalised column would be unable to
        show that a case has parts at all. Where the row is absent the answer is null, which
        for a case at `lead` is the honest state and not a gap.

        `counts` are computed with the same predicates the card uses, so a case that reads
        "1 institución" here does not open into two.
        """
        clauses = ["true"]
        params: list[Any] = []
        if stage is not None:
            clauses.append("op.stage = %s")
            params.append(stage)
        if open_only:
            clauses.append("op.closed_at is null")
        where = "where " + " and ".join(clauses)

        sql = f"""
            select op.id::text        as opportunity_id,
                   op.title           as title,
                   op.stage           as stage,
                   op.version         as version,
                   op.closed_at       as closed_at,
                   op.close_reason    as close_reason,
                   op.created_at      as created_at,
                   op.updated_at      as updated_at,
                   owner.id::text     as owner_operator_id,
                   owner.display_name as owner_display_name,
                   sr.id::text        as origin_source_record_id,
                   sr.kind            as origin_source_kind,
                   sr.source_uri      as origin_source_uri,
                   req_org.id::text   as requesting_organization_id,
                   req_org.name       as requesting_organization_name,
                   req.confirmation   as requesting_confirmation,
                   (select count(*) from crm.opportunity_organization oo
                     where oo.opportunity_id = op.id and oo.valid_to is null)
                                      as organization_count,
                   (select count(*) from crm.opportunity_interest oi
                     where oi.opportunity_id = op.id and oi.withdrawn_at is null)
                                      as interest_count,
                   (select count(*) from crm.opportunity_evidence oe
                     where oe.opportunity_id = op.id and oe.unlinked_at is null)
                                      as evidence_count
              from crm.opportunity op
              join platform.operator owner on owner.id = op.owner_operator_id
              left join evidence.source_record sr on sr.id = op.origin_source_record_id
              left join crm.opportunity_organization req
                     on req.opportunity_id = op.id
                    and req.role = 'requesting_institution'
                    and req.valid_to is null
              left join crm.organization req_org on req_org.id = req.organization_id
              {where}
             order by op.updated_at desc, op.id
             limit %s offset %s
        """
        count_sql = f"select count(*) from crm.opportunity op {where}"
        with self._read() as cur:
            return self._page(cur, sql, count_sql, tuple(params), limit, offset)

    def organization_cases(
        self, organization_id: str, *, limit: int, offset: int
    ) -> Page | None:
        """Every case an institution is part of, and what it is on each one.

        `/v2/cases` answers "what is in play". This answers the question an institution
        screen actually asks — *where does this organization appear at all* — and the two
        are not the same list. A case carries up to seven parts (`CASE_ROLE_ORDER`), so
        reading only `requesting_institution`, as a browser-side join over one page of
        `/v2/cases` must, hides every case where an institution supplies, manufactures,
        pays or is merely named. A manufacturer would read as uninvolved in its own deals.

        The join is `crm.opportunity_organization.organization_id` — a recorded part row
        and nothing else. No name match, no mail domain, no inference: a domain is a
        routing hint and never an identity key (`docs/DOMAIN.md` §2.2).

        **Every part comes back, current and closed, as a list.** One institution routinely
        holds more than one part on one case — supplier *and* manufacturer is the ordinary
        shape, not an edge case — so a single label per case would have to pick one and be
        wrong. A closed row (`valid_to` set) is history rather than a mistake and is
        returned carrying `is_current: false`, on the same principle as `case_card`.

        Returns `None` when no such organization exists, so the route answers 404 rather
        than an empty page that would read as "this institution is in no cases".
        """
        with self._read() as cur:
            cur.execute(
                "select 1 from crm.organization where id = %s::uuid", (organization_id,)
            )
            if cur.fetchone() is None:
                return None

            sql = """
                select op.id::text        as opportunity_id,
                       op.title           as title,
                       op.stage           as stage,
                       op.version         as version,
                       op.closed_at       as closed_at,
                       op.close_reason    as close_reason,
                       op.created_at      as created_at,
                       op.updated_at      as updated_at,
                       owner.id::text     as owner_operator_id,
                       owner.display_name as owner_display_name,
                       sr.id::text        as origin_source_record_id,
                       sr.kind            as origin_source_kind,
                       sr.source_uri      as origin_source_uri,
                       req_org.id::text   as requesting_organization_id,
                       req_org.name       as requesting_organization_name,
                       req.confirmation   as requesting_confirmation,
                       (select count(*) from crm.opportunity_organization oo
                         where oo.opportunity_id = op.id and oo.valid_to is null)
                                          as organization_count,
                       (select count(*) from crm.opportunity_interest oi
                         where oi.opportunity_id = op.id and oi.withdrawn_at is null)
                                          as interest_count,
                       (select count(*) from crm.opportunity_evidence oe
                         where oe.opportunity_id = op.id and oe.unlinked_at is null)
                                          as evidence_count,
                       (select jsonb_agg(
                                 jsonb_build_object(
                                   'opportunity_organization_id', mine.id::text,
                                   'role', mine.role,
                                   'confirmation', mine.confirmation,
                                   'valid_from', mine.valid_from,
                                   'valid_to', mine.valid_to,
                                   'is_current', mine.valid_to is null,
                                   'note', mine.note,
                                   'supplier_exception_reason',
                                     mine.supplier_exception_reason)
                                 order by (mine.valid_to is null) desc,
                                          array_position(%s::text[], mine.role),
                                          mine.valid_from desc)
                          from crm.opportunity_organization mine
                         where mine.opportunity_id = op.id
                           and mine.organization_id = %s::uuid)
                                          as roles
                  from crm.opportunity op
                  join platform.operator owner on owner.id = op.owner_operator_id
                  left join evidence.source_record sr on sr.id = op.origin_source_record_id
                  left join crm.opportunity_organization req
                         on req.opportunity_id = op.id
                        and req.role = 'requesting_institution'
                        and req.valid_to is null
                  left join crm.organization req_org on req_org.id = req.organization_id
                 where exists (
                         select 1 from crm.opportunity_organization part
                          where part.opportunity_id = op.id
                            and part.organization_id = %s::uuid)
                 order by op.updated_at desc, op.id
                 limit %s offset %s
            """
            count_sql = """
                select count(*)
                  from crm.opportunity op
                 where exists (
                         select 1 from crm.opportunity_organization part
                          where part.opportunity_id = op.id
                            and part.organization_id = %s::uuid)
            """
            total = self._scalar(cur, count_sql, (organization_id,))
            items = self._rows(
                cur,
                sql,
                (
                    list(self.CASE_ROLE_ORDER),
                    organization_id,
                    organization_id,
                    limit,
                    offset,
                ),
            )
        return Page(items=items, total=total, limit=limit, offset=offset)

    def case_card(self, opportunity_id: str) -> dict[str, Any] | None:
        """One commercial case: who asks, what it seeks, and why it believes any of it.

        Assembled from four bounded queries rather than one join, for the reason the other
        cards are: a case at `lead` legitimately has no institution, no interest and one
        evidence link, and a join renders that as a row of nulls instead of three lists that
        each say what is missing.

        **Closed rows are returned, not filtered.** `crm.opportunity_organization` never
        rewrites a part — changing one closes a row and opens another (§3.6.1) — so a card
        that showed only the current rows would show a reading with no history and make the
        audit trail invisible exactly where it matters. `is_current` carries the
        distinction; withdrawn interests and unlinked evidence are returned on the same
        principle.
        """
        with self._read() as cur:
            head = self._rows(
                cur,
                """
                select op.id::text        as opportunity_id,
                       op.title           as title,
                       op.stage           as stage,
                       op.version         as version,
                       op.closed_at       as closed_at,
                       op.close_reason    as close_reason,
                       op.created_at      as created_at,
                       op.updated_at      as updated_at,
                       owner.id::text     as owner_operator_id,
                       owner.display_name as owner_display_name,
                       op.organization_id::text as organization_id,
                       org.name           as organization_name,
                       sr.id::text        as origin_source_record_id,
                       sr.kind            as origin_source_kind,
                       sr.source_uri      as origin_source_uri,
                       sr.review_status   as origin_review_status,
                       reopened.id::text  as reopened_from_opportunity_id,
                       reopened.title     as reopened_from_title
                  from crm.opportunity op
                  join platform.operator owner on owner.id = op.owner_operator_id
                  left join crm.organization org on org.id = op.organization_id
                  left join evidence.source_record sr on sr.id = op.origin_source_record_id
                  left join crm.opportunity reopened
                         on reopened.id = op.reopened_from_opportunity_id
                 where op.id = %s::uuid
                """,
                (opportunity_id,),
            )
            if not head:
                return None
            row = head[0]

            organizations = self._rows(
                cur,
                """
                select oo.id::text          as opportunity_organization_id,
                       o.id::text           as organization_id,
                       o.name               as name,
                       o.kind               as organization_kind,
                       oo.role              as role,
                       oo.confirmation      as confirmation,
                       oo.valid_from        as valid_from,
                       oo.valid_to          as valid_to,
                       (oo.valid_to is null) as is_current,
                       confirmer.display_name as confirmed_by_display_name,
                       oo.note              as note,
                       sr.kind              as origin_source_kind,
                       sr.source_uri        as origin_source_uri,
                       oo.supplier_exception_reason as supplier_exception_reason,
                       oo.supplier_exception_at     as supplier_exception_at,
                       excepter.display_name        as supplier_exception_by_display_name
                  from crm.opportunity_organization oo
                  join crm.organization o on o.id = oo.organization_id
                  left join platform.operator confirmer
                         on confirmer.id = oo.confirmed_by_operator_id
                  left join platform.operator excepter
                         on excepter.id = oo.supplier_exception_by_operator_id
                  left join evidence.source_record sr on sr.id = oo.origin_source_record_id
                 where oo.opportunity_id = %s::uuid
                 order by (oo.valid_to is null) desc,
                          array_position(%s::text[], oo.role),
                          oo.valid_from desc,
                          o.name
                 limit %s
                """,
                (opportunity_id, list(self.CASE_ROLE_ORDER), self.CARD_CHILD_LIMIT),
            )

            interests = self._rows(
                cur,
                """
                select oi.id::text     as opportunity_interest_id,
                       oi.product_id::text as product_id,
                       -- `catalog.product.name` is nullable; the model number is what
                       -- identifies a product and is never blank. Falling back to it means a
                       -- catalogued interest always reads as something rather than as a gap.
                       coalesce(p.name, p.model_number) as product_name,
                       oi.manufacturer_organization_id::text as manufacturer_organization_id,
                       m.name          as manufacturer_organization_name,
                       oi.model_text   as model_text,
                       oi.description  as description,
                       oi.quantity     as quantity,
                       oi.quantity_unit as quantity_unit,
                       oi.confirmation as confirmation,
                       confirmer.display_name as confirmed_by_display_name,
                       oi.withdrawn_at as withdrawn_at,
                       oi.withdraw_reason as withdraw_reason,
                       oi.note         as note,
                       sr.kind         as origin_source_kind,
                       sr.source_uri   as origin_source_uri,
                       oi.created_at   as created_at
                  from crm.opportunity_interest oi
                  left join catalog.product p on p.id = oi.product_id
                  left join crm.organization m on m.id = oi.manufacturer_organization_id
                  left join platform.operator confirmer
                         on confirmer.id = oi.confirmed_by_operator_id
                  left join evidence.source_record sr on sr.id = oi.origin_source_record_id
                 where oi.opportunity_id = %s::uuid
                 order by (oi.withdrawn_at is null) desc, oi.created_at
                 limit %s
                """,
                (opportunity_id, self.CARD_CHILD_LIMIT),
            )

            # One link points at exactly one of four typed columns, so the subject is
            # flattened here into the pair a card can render — `subject_kind` and the words
            # of the thing itself — rather than four mostly-null columns the UI must
            # re-derive. Which column was filled is a fact about the row, not a guess.
            evidence = self._rows(
                cur,
                """
                select oe.id::text        as opportunity_evidence_id,
                       oe.relation        as relation,
                       case
                         when oe.source_record_id is not null then 'source_record'
                         when oe.assertion_id     is not null then 'assertion'
                         when oe.message_id       is not null then 'message'
                         else 'notice'
                       end                as subject_kind,
                       coalesce(oe.source_record_id, oe.assertion_id,
                                oe.message_id, oe.notice_id)::text as subject_id,
                       coalesce(sr.kind, asr.kind)       as source_kind,
                       coalesce(sr.source_uri, asr.source_uri) as source_uri,
                       coalesce(sr.review_status, asr.review_status) as source_review_status,
                       a.kind             as assertion_kind,
                       a.value_norm       as assertion_value,
                       linker.display_name as linked_by_display_name,
                       oe.linked_at       as linked_at,
                       oe.unlinked_at     as unlinked_at,
                       oe.unlink_reason   as unlink_reason,
                       oe.note            as note
                  from crm.opportunity_evidence oe
                  join platform.operator linker on linker.id = oe.linked_by_operator_id
                  left join evidence.source_record sr on sr.id = oe.source_record_id
                  left join evidence.assertion a on a.id = oe.assertion_id
                  left join evidence.source_record asr on asr.id = a.source_record_id
                 where oe.opportunity_id = %s::uuid
                 order by (oe.unlinked_at is null) desc, oe.linked_at
                 limit %s
                """,
                (opportunity_id, self.CARD_CHILD_LIMIT),
            )

            counts = {
                "organizations_current": self._scalar(
                    cur,
                    "select count(*) from crm.opportunity_organization "
                    "where opportunity_id = %s::uuid and valid_to is null",
                    (opportunity_id,),
                ),
                "organizations_total": self._scalar(
                    cur,
                    "select count(*) from crm.opportunity_organization "
                    "where opportunity_id = %s::uuid",
                    (opportunity_id,),
                ),
                "interests_open": self._scalar(
                    cur,
                    "select count(*) from crm.opportunity_interest "
                    "where opportunity_id = %s::uuid and withdrawn_at is null",
                    (opportunity_id,),
                ),
                "interests_total": self._scalar(
                    cur,
                    "select count(*) from crm.opportunity_interest where opportunity_id = %s::uuid",
                    (opportunity_id,),
                ),
                "evidence_linked": self._scalar(
                    cur,
                    "select count(*) from crm.opportunity_evidence "
                    "where opportunity_id = %s::uuid and unlinked_at is null",
                    (opportunity_id,),
                ),
                "evidence_total": self._scalar(
                    cur,
                    "select count(*) from crm.opportunity_evidence where opportunity_id = %s::uuid",
                    (opportunity_id,),
                ),
            }

        row["organizations"] = organizations
        row["interests"] = interests
        row["evidence"] = evidence
        row["counts"] = counts
        return row
