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
