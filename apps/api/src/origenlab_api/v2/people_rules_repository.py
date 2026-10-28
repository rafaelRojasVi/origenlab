"""Who wrote each email: read the snapshot, apply the strong people actions, undo one (people first, Task 5).

`people_rules.py` decides; this module reads what it needs from PostgreSQL and runs what it planned,
as `origenlab_api`, the same way `mail_rules_repository.py` runs the email → cases rules:

**One action, one transaction, one receipt.** Every strong action (P1–P5) runs inside one
transaction under one `platform.command_receipt` (`apply_people_rule`, key
`people-rule:<assertion id>:<rule id>`; a P1b adds its name's source, since one reading may teach
both the header name and the signature name). A refused step rolls the whole action back, receipt
included. A reading with any completed receipt is never planned again — an undone one included, so
a person's «Quitar» is not overridden by the next run.

**The machine writes, `machine_proposed`.** Every row an action creates (person, contact point,
affiliation, other name) is `confirmation = 'machine_proposed'` with the email it came from; every
event is `actor_kind = 'worker'` with `payload.attribution = {source: people_rules, rule_id, trigger,
assertion_id}`. The authoring handlers (`crm_authoring.py`) write `confirmed` rows for a person, so
the creating steps are written here; the undo steps reuse the handlers wherever they fit.

**Exact undo.** The receipt records every row the action created or moved, and the versions they
had once it was done. `undo` reverses exactly those — a merge moves back only the recorded rows, so
what the winner gained since stays with the winner — and refuses `changed_since` when a person has
edited a recorded row since (its `version` moved). Affiliations and other names carry no `version`;
for them undo acts only while the row is still as the action left it (open / not removed).

Two schema facts shape the merge (both hold for the operator `merge-people` too, see the report):
`origenlab_api` may not update `crm.affiliation.person_id` (the grant matrix lists the columns), so a
loser's affiliation is *closed* and the same affiliation *opened* for the winner; and a frozen
campaign recipient is never rewritten (`campaign_recipient_snapshot_guard`), so only unfrozen ones
move. A same-day affiliation cannot close on its first day (`valid_to > valid_from`), so a close
takes `greatest(today, valid_from + 1)`; «open» means `valid_to is null` everywhere here.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import Any

from origenlab_api.v2.case_command_repository import V2CaseCommandRepository
from origenlab_api.v2.command_core import DEFAULT_COMMAND_TIMEOUT_MS
from origenlab_api.v2.commands import CommandRefused, as_uuid
from origenlab_api.v2.crm_authoring import (
    V2CrmAuthoringRepository,
    _handle_archive_person,
    _handle_deactivate_contact_point,
    _live_organization_active,
    _live_person,
)
from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2.people_rules import (
    ContactPointRow,
    OrgRow,
    PeopleAction,
    PeopleSnapshot,
    PersonRow,
    Reading,
    is_free_mail,
    name_norm,
    org_key,
    plan_people,
)

APPLY_COMMAND = "apply_people_rule"
UNDO_COMMAND = "undo_people_rule"
STRONG_RULES = frozenset({"P1", "P1b", "P2", "P3", "P4", "P5"})
#: Readings older than this are not planned any more (the backfill reads the same 90 days).
READING_WINDOW_DAYS = 90
REMOVED_FROM_HOY = "Quitado de Hoy"
CHANGED_SINCE = "Alguien cambió este contacto después; deshazlo desde la ficha"
#: Rules that only resolve an email to a person already known: not listed on «Hoy».
QUIET_RULES = frozenset({"P1"})
_TITLE_MAX = 200


def _digest(value: dict[str, Any]) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def idempotency_key(action: PeopleAction) -> str:
    key = f"people-rule:{action.assertion_id}:{action.rule_id}"
    return f"{key}:{action.fields.get('source')}" if action.rule_id == "P1b" else key


# ─────────────────────────────────────────────────────────────── the snapshot ──

#: The `people:v1` readings no apply receipt names yet (undone ones included), with the `from`
#: participants of their message that carry the sender's address. The receipt anti-join is one
#: hash anti-join over the completed `apply_people_rule` receipts.
_READINGS_SQL = """
select a.id::text as id, a.source_record_id::text as source_record_id, a.value,
       coalesce(array(select p.id::text
                        from comms.message m
                        join comms.message_participant p on p.message_id = m.id
                       where m.provider_message_id = sr.payload ->> 'gmail_message_id'
                         and p.role = 'from' and p.address_norm = lower(btrim(a.value ->> 'sender_address'))
                       order by p.id), '{}') as participant_ids
  from evidence.assertion a
  join evidence.source_record sr on sr.id = a.source_record_id
 where a.kind = 'message_triage' and a.value_norm = 'people:v1'
   and a.created_at > now() - make_interval(days => %s)
   and not exists (select 1 from platform.command_receipt r
                    where r.command_name = %s and r.status = 'completed'
                      and r.response_body ->> 'assertion_id' = a.id::text)
 order by a.created_at, a.id
"""
_PERSONS_SQL = """
select id::text, display_name, confirmation, status, merged_into_person_id::text, created_at from crm.person
"""
_NAMES_SQL = "select person_id::text, name_display from crm.person_name where removed_at is null"
_CONTACT_POINTS_SQL = """
select id::text, value_norm, person_id::text, organization_id::text, usage, status
  from crm.contact_point where kind = 'email'
"""
_AFFILIATIONS_SQL = "select person_id::text, organization_id::text from crm.affiliation where valid_to is null"
_ORGANIZATIONS_SQL = "select id::text, name, status, merged_into_organization_id::text from crm.organization"
_DOMAINS_SQL = """
select d.domain_norm, d.organization_id::text from crm.organization_domain d
 where d.scope = 'exclusive' and d.removed_at is null
"""
_RECENT_AUTOMATIC_SQL = """
select r.id::text as receipt_id, r.completed_at, r.response_body, p.display_name
  from platform.command_receipt r
  left join crm.person p on p.id::text = r.response_body ->> 'person_id'
 where r.command_name = %s and r.status = 'completed'
   and r.response_body ->> 'trigger' = 'automatic'
   and not (r.response_body ->> 'rule_id' = any(%s))
   and r.completed_at > now() - make_interval(hours => %s)
   and not exists (select 1 from platform.command_receipt u
                    where u.command_name = %s and u.status = 'completed'
                      and u.response_body ->> 'undoes_receipt_id' = r.id::text)
 order by r.completed_at desc
 limit %s
"""


def _final(pid: str | None, merged: dict[str, str | None]) -> str | None:
    """The person a merge chain ends at."""
    seen: set[str] = set()
    while pid is not None and merged.get(pid) and pid not in seen:
        seen.add(pid)
        pid = merged[pid]
    return pid


class PeopleRulesRepository(V2CaseCommandRepository):
    """Snapshot, plan, apply, undo and the «Hoy» list of the people rules."""

    def __init__(self, connect: Any, dsn: str, statement_timeout_ms: int = DEFAULT_COMMAND_TIMEOUT_MS) -> None:
        super().__init__(connect, dsn, statement_timeout_ms)
        self._authoring = V2CrmAuthoringRepository(connect, dsn, statement_timeout_ms)

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

    # ------------------------------------------------------------------ snapshot and plan

    def read_snapshot(self, cur: Any) -> PeopleSnapshot:
        cur.execute(_READINGS_SQL, (READING_WINDOW_DAYS, APPLY_COMMAND))
        readings = tuple(Reading(assertion_id=aid, source_record_id=srid,
                                 message_participant_ids=tuple(pids or ()), value=_json(value) or {})
                         for aid, srid, value, pids in cur.fetchall())

        cur.execute(_PERSONS_SQL)
        persons = {pid: PersonRow(id=pid, display_name=name, confirmation=conf, status=status,
                                  merged_into=merged, created_at=created)
                   for pid, name, conf, status, merged, created in cur.fetchall()}
        merged = {pid: p.merged_into for pid, p in persons.items()}

        names: dict[str, set[str]] = {}

        def add_name(pid: str | None, name: str) -> None:
            norm = name_norm(name or "")
            if pid is not None and norm:
                names.setdefault(norm, set()).add(pid)

        for p in persons.values():
            add_name(_final(p.id, merged), p.display_name)
        cur.execute(_NAMES_SQL)
        for pid, name in cur.fetchall():
            add_name(_final(pid, merged), name)

        cur.execute(_CONTACT_POINTS_SQL)
        contact_points = {value: ContactPointRow(id=cid, value_norm=value, person_id=_final(pid, merged),
                                                 organization_id=oid, usage=usage, status=status)
                          for cid, value, pid, oid, usage, status in cur.fetchall()}

        cur.execute(_AFFILIATIONS_SQL)
        affiliations: dict[str, set[str]] = {}
        for pid, oid in cur.fetchall():
            final = _final(pid, merged)
            if final is not None:
                affiliations.setdefault(final, set()).add(oid)

        cur.execute(_ORGANIZATIONS_SQL)
        organizations: dict[str, OrgRow] = {}
        org_by_name: dict[str, set[str]] = {}
        for oid, name, status, merged_into in cur.fetchall():
            organizations[oid] = OrgRow(id=oid, name=name, status=status if merged_into is None else "merged")
            if status == "active" and merged_into is None and org_key(name):
                org_by_name.setdefault(org_key(name), set()).add(oid)

        cur.execute(_DOMAINS_SQL)
        org_by_domain = {domain: oid for domain, oid in cur.fetchall()
                         if not is_free_mail(domain) and oid in organizations
                         and organizations[oid].status == "active"}

        return PeopleSnapshot(readings=readings, persons=persons, names=names, contact_points=contact_points,
                              affiliations=affiliations, organizations=organizations, org_by_name=org_by_name,
                              org_by_domain=org_by_domain, answered=self.answered(cur))

    def answered(self, cur: Any) -> dict[tuple[str, str], bool]:
        """(address, person id) → a person's Sí/No to a «¿Es …?» guess. Task 7 fills it."""
        return {}

    def plan(self) -> list[PeopleAction]:
        with self._read() as cur:
            snapshot = self.read_snapshot(cur)
        return plan_people(snapshot)

    # ------------------------------------------------------------------ «Hoy»

    def recent_automatic(self, hours: int = 48, limit: int = 50) -> list[dict[str, Any]]:
        """What the automatic run learned in the last `hours`, not undone, newest first. A bare P1
        (an email resolved to a person already known) is not listed."""
        with self._read() as cur:
            cur.execute(_RECENT_AUTOMATIC_SQL, (APPLY_COMMAND, sorted(QUIET_RULES), max(1, min(int(hours), 24 * 14)),
                                                UNDO_COMMAND, max(1, min(int(limit), 200))))
            out = []
            for receipt_id, completed_at, body, person_name in cur.fetchall():
                body = _json(body) or {}
                out.append({
                    "receipt_id": receipt_id,
                    "applied_at": completed_at.isoformat() if completed_at else None,
                    "rule_id": body.get("rule_id"), "reasons": body.get("reasons") or [],
                    "assertion_id": body.get("assertion_id"), "address": body.get("address"),
                    "person_id": body.get("person_id"), "person_name": person_name,
                    "created": body.get("created") or {}, "merge": body.get("merge"),
                })
            return out

    # ------------------------------------------------------------------ apply

    def apply_planned(self, operator: OperatorIdentity, action: PeopleAction, *,
                      automatic: bool) -> dict[str, Any]:
        """Apply one strong action the rules just planned: its response, or a refusal with `code`."""
        try:
            if action.tier != "strong" or action.rule_id not in STRONG_RULES:
                raise CommandRefused(422, "not_strong", "only a strong action is applied by itself")
            return self._apply(operator, action, automatic=automatic)
        except CommandRefused as exc:
            return {"assertion_id": action.assertion_id, "rule_id": action.rule_id,
                    "code": exc.code, "message": exc.message}
        except Exception as exc:  # a constraint the checks did not anticipate
            if not _is_database_error(exc):
                raise
            return {"assertion_id": action.assertion_id, "rule_id": action.rule_id, "code": "database_refused",
                    "message": str(exc).splitlines()[0] if str(exc) else type(exc).__name__}

    def _apply(self, operator: OperatorIdentity, action: PeopleAction, *, automatic: bool) -> dict[str, Any]:
        key = idempotency_key(action)
        digest = _digest({"assertion_id": action.assertion_id, "rule_id": action.rule_id,
                          "address": action.address, "person_id": action.person_id, "fields": action.fields})
        trigger = "automatic" if automatic else "manual"
        system = replace(operator, acts_as_system=True, event_attribution={
            "source": "people_rules", "rule_id": action.rule_id, "trigger": trigger,
            "assertion_id": action.assertion_id,
        })
        with self._write() as cur:
            # One run at a time per reading; the second then sees the first's receipt.
            cur.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"people-rule:{action.assertion_id}",))
            cur.execute("select 1 from platform.command_receipt where command_name = %s and idempotency_key = %s "
                        "and status = 'completed' and operator_id <> %s", (APPLY_COMMAND, key, operator.operator_id))
            if cur.fetchone() is not None:
                raise CommandRefused(409, "already_applied", "another run already applied this action")
            receipt_id, replay = self._claim_receipt(cur, operator, key, APPLY_COMMAND, digest)
            if replay is not None:
                return replay
            source_record_id, participant_ids = self._reading(cur, action)
            ctx = _Ctx(self, cur, system, receipt_id, source_record_id)
            merge: dict[str, Any] | None = None
            fields = action.fields
            if action.rule_id == "P1":
                self._check_known_address(cur, action)
            elif action.rule_id == "P1b":
                self._add_name(ctx, str(action.person_id), fields["name"], fields["source"])
            elif action.rule_id == "P2":
                self._attach_address(ctx, action.address, fields["person_id"], fields["usage"],
                                     fields.get("organization_id"))
            elif action.rule_id == "P3":
                self._create_person(ctx, action)
            elif action.rule_id == "P4":
                self._affiliate(ctx, str(action.person_id), fields["organization_id"])
            elif action.rule_id == "P5":
                merge = self._merge_people_recorded(cur, system, fields["winner_person_id"],
                                                    fields["loser_person_id"], receipt_id)
                ctx.versions.update(merge.pop("versions"))
            resolved_cp, resolved = self._resolve_participants(cur, action.address, participant_ids)
            response: dict[str, Any] = {
                "rule_id": action.rule_id, "assertion_id": action.assertion_id, "address": action.address,
                "person_id": action.person_id or ctx.created.get("person_id"), "trigger": trigger,
                "reasons": list(action.reasons), "created": ctx.created,
                "resolved_participant_ids": resolved, "resolved_contact_point_id": resolved_cp,
                "versions": ctx.versions, "idempotency_key": key, "command_receipt_id": receipt_id,
                "replayed": False,
            }
            if merge is not None:
                response["merge"] = merge
            self._complete_receipt(cur, receipt_id, response)
            return response

    def _reading(self, cur: Any, action: PeopleAction) -> tuple[str, list[str]]:
        cur.execute(
            """
            select a.source_record_id::text,
                   coalesce(array(select p.id::text from comms.message m
                                    join comms.message_participant p on p.message_id = m.id
                                   where m.provider_message_id = sr.payload ->> 'gmail_message_id'
                                     and p.role = 'from' and p.address_norm = %s order by p.id), '{}')
              from evidence.assertion a join evidence.source_record sr on sr.id = a.source_record_id
             where a.id = %s and a.kind = 'message_triage' and a.value_norm = 'people:v1'
            """,
            (action.address, as_uuid(action.assertion_id, "assertion_id")),
        )
        row = cur.fetchone()
        if row is None:
            raise CommandRefused(404, "reading_not_found", "no people reading with that id")
        return row[0], list(row[1] or [])

    def _resolve_participants(self, cur: Any, address: str, participant_ids: list[str]) -> tuple[str | None, list[str]]:
        """Point the email's `from` participants at the address's contact point, where still unset."""
        cur.execute("select id::text from crm.contact_point where kind = 'email' and value_norm = %s "
                    "and status = 'active' and person_id is not null", (address,))
        row = cur.fetchone()
        if row is None or not participant_ids:
            return None, []
        cur.execute(
            "update comms.message_participant set resolved_contact_point_id = %s "
            "where id = any(%s::uuid[]) and resolved_contact_point_id is null returning id::text",
            (row[0], participant_ids),
        )
        return row[0], sorted(r[0] for r in cur.fetchall())

    # ------------------------------------------------------------------ the creating steps (machine_proposed)

    def _check_known_address(self, cur: Any, action: PeopleAction) -> None:
        cur.execute(
            "select cp.status, p.status, p.merged_into_person_id from crm.contact_point cp "
            "join crm.person p on p.id = cp.person_id where cp.id = %s",
            (action.fields["contact_point_id"],),
        )
        row = cur.fetchone()
        if row is None or row[0] != "active" or row[1] != "active" or row[2] is not None:
            raise CommandRefused(409, "plan_changed", "the address no longer belongs to a live person")

    def _add_name(self, ctx: "_Ctx", person_id: str, name: str, source: str) -> None:
        _live_person(ctx.cur, person_id)
        ctx.cur.execute(
            """
            insert into crm.person_name (person_id, name_display, name_norm, source, confirmation,
                                         origin_source_record_id)
            values (%s, %s, %s, %s, 'machine_proposed', %s)
            on conflict (person_id, name_norm) where removed_at is null do nothing
            returning id::text
            """,
            (person_id, name.strip(), name_norm(name), source, ctx.source_record_id),
        )
        row = ctx.cur.fetchone()
        if row is None:
            raise CommandRefused(409, "name_already_known", "the person already carries that name")
        ctx.created["person_name_id"] = row[0]
        self._bump_person(ctx, person_id)
        ctx.event("person", person_id, "person.updated", {"person_name_added": row[0], "source": source})

    def _attach_address(self, ctx: "_Ctx", address: str, person_id: str, usage: str,
                        organization_id: str | None) -> None:
        _live_person(ctx.cur, person_id)
        if usage == "work":
            if not organization_id:
                raise CommandRefused(422, "organization_required", "a work address names its institution")
            _live_organization_active(ctx.cur, organization_id)
        self._new_contact_point(ctx, address, person_id, organization_id if usage == "work" else None, usage)
        self._bump_person(ctx, person_id)

    def _new_contact_point(self, ctx: "_Ctx", address: str, person_id: str, organization_id: str | None,
                           usage: str) -> None:
        ctx.cur.execute("select 1 from crm.contact_point where kind = 'email' and value_norm = %s for update",
                        (address,))
        if ctx.cur.fetchone() is not None:
            raise CommandRefused(409, "contact_point_taken", "the address is already in the CRM")
        ctx.cur.execute(
            """
            insert into crm.contact_point (kind, value_norm, value_display, person_id, organization_id, usage,
                                           confirmation, origin_source_record_id)
            values ('email', %s, %s, %s, %s, %s, 'machine_proposed', %s)
            returning id::text, version
            """,
            (address, address, person_id, organization_id, usage, ctx.source_record_id),
        )
        cp_id, version = ctx.cur.fetchone()
        ctx.created["contact_point_id"] = cp_id
        ctx.versions[f"contact_point:{cp_id}"] = int(version)
        ctx.event("contact_point", cp_id, "contact_point.created",
                  {"kind": "email", "contact_point_id": cp_id, "usage": usage, "person_id": person_id})

    def _create_person(self, ctx: "_Ctx", action: PeopleAction) -> None:
        f = action.fields
        _live_organization_active(ctx.cur, f["organization_id"])
        title = (f.get("title") or "").strip() or None
        if title is not None and len(title) > _TITLE_MAX:
            title = None
        ctx.cur.execute(
            """
            insert into crm.person (display_name, title, confirmation, origin_source_record_id, status)
            values (%s, %s, 'machine_proposed', %s, 'active')
            returning id::text
            """,
            (f["display_name"].strip(), title, ctx.source_record_id),
        )
        person_id = ctx.cur.fetchone()[0]
        ctx.created["person_id"] = person_id
        ctx.event("person", person_id, "person.created", {"display_name": f["display_name"].strip()})
        work = f.get("usage") == "work"
        self._new_contact_point(ctx, action.address, person_id, f["organization_id"] if work else None,
                                "work" if work else "personal")
        self._open_affiliation(ctx, person_id, f["organization_id"])
        ctx.cur.execute("select version from crm.person where id = %s", (person_id,))
        ctx.versions[f"person:{person_id}"] = int(ctx.cur.fetchone()[0])

    def _affiliate(self, ctx: "_Ctx", person_id: str, organization_id: str) -> None:
        _live_person(ctx.cur, person_id)
        _live_organization_active(ctx.cur, organization_id)
        ctx.cur.execute("select 1 from crm.affiliation where person_id = %s and organization_id = %s "
                        "and valid_to is null", (person_id, organization_id))
        if ctx.cur.fetchone() is not None:
            raise CommandRefused(409, "already_affiliated", "the person already belongs to that institution")
        self._open_affiliation(ctx, person_id, organization_id)
        self._bump_person(ctx, person_id)

    def _bump_person(self, ctx: "_Ctx", person_id: str) -> None:
        """A row added under a person moves its version, as the authoring handlers do
        (`_bump_person_or_org_version`): an operator's open ficha then gets `stale_version`. It is
        not recorded for undo — removing the added row does not touch the person's own fields."""
        ctx.cur.execute("update crm.person set version = version + 1, updated_at = now() where id = %s", (person_id,))

    def _open_affiliation(self, ctx: "_Ctx", person_id: str, organization_id: str, *,
                          role_title: str | None = None, unit_label: str | None = None,
                          record: bool = True) -> str:
        ctx.cur.execute(
            """
            insert into crm.affiliation (person_id, organization_id, role_title, unit_label, valid_from,
                                         confirmation, origin_source_record_id)
            values (%s, %s, %s, %s, current_date, 'machine_proposed', %s)
            returning id::text
            """,
            (person_id, organization_id, role_title, unit_label, ctx.source_record_id),
        )
        aff_id = ctx.cur.fetchone()[0]
        if record:
            ctx.created["affiliation_id"] = aff_id
        ctx.event("affiliation", aff_id, "affiliation.opened",
                  {"person_id": person_id, "organization_id": organization_id, "role_title": role_title})
        return aff_id

    # ------------------------------------------------------------------ the recorded merge

    def _merge_people_recorded(self, cur: Any, operator: OperatorIdentity, winner_id: str, loser_id: str,
                               receipt_id: str) -> dict[str, Any]:
        """What `_handle_merge_people` does, selecting every row's id before it moves: the loser's
        contact points, open affiliations, case participants, unfrozen campaign recipients and notes
        go to the winner; an affiliation the winner already holds is closed on the loser."""
        if winner_id == loser_id:
            raise CommandRefused(409, "same_person", "loser and winner must be different people")
        cur.execute(
            "select id::text, status, version, merged_into_person_id::text, archived_at, "
            "archived_by_operator_id::text, archive_reason from crm.person where id = any(%s::uuid[]) "
            "order by id for update",
            (sorted([winner_id, loser_id]),),
        )
        rows = {r[0]: r for r in cur.fetchall()}
        winner, loser = rows.get(winner_id), rows.get(loser_id)
        if winner is None or loser is None:
            raise CommandRefused(404, "person_not_found", "a person of the merge no longer exists")
        if winner[3] is not None or loser[3] is not None:
            raise CommandRefused(409, "merged_person", "a person of the merge was already merged")
        if winner[1] != "active" or loser[1] != "active":
            raise CommandRefused(409, "archived_subject", "a person of the merge is archived")
        loser_prior = {"status": loser[1], "version": int(loser[2]), "merged_into_person_id": loser[3],
                       "archived_at": loser[4].isoformat() if loser[4] else None,
                       "archived_by_operator_id": loser[5], "archive_reason": loser[6]}

        def ids(sql: str) -> list[str]:
            cur.execute(sql, (loser_id,))
            return sorted(r[0] for r in cur.fetchall())

        versions: dict[str, int] = {}
        contact_points = ids("select id::text from crm.contact_point where person_id = %s order by id for update")
        if contact_points:
            cur.execute("update crm.contact_point set person_id = %s, version = version + 1, updated_at = now() "
                        "where id = any(%s::uuid[]) returning id::text, version", (winner_id, contact_points))
            versions.update({f"contact_point:{i}": int(v) for i, v in cur.fetchall()})
        participants = ids("select id::text from crm.opportunity_participant where person_id = %s for update")
        if participants:
            cur.execute("update crm.opportunity_participant set person_id = %s, updated_at = now() "
                        "where id = any(%s::uuid[])", (winner_id, participants))
        recipients = ids("select id::text from outbound.campaign_recipient where person_id = %s "
                         "and frozen_at is null for update")
        if recipients:
            cur.execute("update outbound.campaign_recipient set person_id = %s where id = any(%s::uuid[])",
                        (winner_id, recipients))
        notes = ids("select id::text from crm.note where subject_kind = 'person' and subject_id = %s for update")
        if notes:
            cur.execute("update crm.note set subject_id = %s where id = any(%s::uuid[]) returning id::text, version",
                        (winner_id, notes))
            versions.update({f"note:{i}": int(v) for i, v in cur.fetchall()})

        ctx = _Ctx(self, cur, operator, receipt_id, None)
        cur.execute(
            "select id::text, organization_id::text, role_title, unit_label from crm.affiliation "
            "where person_id = %s and valid_to is null order by id for update", (loser_id,))
        moved_affs, opened_affs, closed_affs = [], [], []
        for aff_id, org_id, role_title, unit_label in cur.fetchall():
            cur.execute("select 1 from crm.affiliation where person_id = %s and organization_id = %s "
                        "and valid_to is null", (winner_id, org_id))
            duplicate = cur.fetchone() is not None
            self._close_affiliation(ctx, aff_id, loser_id)
            if duplicate:
                closed_affs.append(aff_id)
            else:
                moved_affs.append(aff_id)
                opened_affs.append(self._open_affiliation(ctx, winner_id, org_id, role_title=role_title,
                                                          unit_label=unit_label, record=False))

        cur.execute(
            """
            update crm.person
               set status = 'archived', archived_at = now(), archived_by_operator_id = %s,
                   archive_reason = %s, merged_into_person_id = %s, version = version + 1, updated_at = now()
             where id = %s
            returning version
            """,
            (operator.operator_id, f"fusionada en {winner_id}", winner_id, loser_id),
        )
        versions[f"person:{loser_id}"] = int(cur.fetchone()[0])
        cur.execute("update crm.person set version = version + 1, updated_at = now() where id = %s", (winner_id,))
        ctx.event("person", loser_id, "person.merged", {"winner_person_id": winner_id})
        ctx.event("person", winner_id, "person.updated", {"merged_from_person_id": loser_id})
        return {
            "winner_person_id": winner_id, "loser_person_id": loser_id, "loser_prior": loser_prior,
            "moved": {"contact_point_ids": contact_points, "affiliation_ids": moved_affs,
                      "opened_affiliation_ids": opened_affs, "closed_affiliation_ids": closed_affs,
                      "opportunity_participant_ids": participants, "campaign_recipient_ids": recipients,
                      "note_ids": notes},
            "versions": versions,
        }

    def _close_affiliation(self, ctx: "_Ctx", aff_id: str, person_id: str) -> bool:
        ctx.cur.execute(
            "update crm.affiliation set valid_to = greatest(current_date, valid_from + 1), updated_at = now() "
            "where id = %s and valid_to is null returning valid_to",
            (aff_id,),
        )
        row = ctx.cur.fetchone()
        if row is None:
            return False
        ctx.event("affiliation", aff_id, "affiliation.closed", {"person_id": person_id, "valid_to": str(row[0])})
        return True

    # ------------------------------------------------------------------ undo

    def undo(self, operator: OperatorIdentity, receipt_id: str, note: str) -> dict[str, Any]:
        """«Quitar»: reverse one applied action exactly, under its own receipt, as the person's act."""
        receipt_id = as_uuid(receipt_id, "receipt_id")
        note = (note or "").strip()
        if not note:
            raise CommandRefused(422, "note_required", "say why the action is undone")
        key = f"people-rule-undo:{receipt_id}"
        digest = _digest({"undoes_receipt_id": receipt_id})
        with self._write() as cur:
            cur.execute("select response_body from platform.command_receipt "
                        "where id = %s and command_name = %s and status = 'completed'", (receipt_id, APPLY_COMMAND))
            row = cur.fetchone()
            if row is None:
                raise CommandRefused(404, "applied_action_not_found", "no applied people action with that receipt")
            action = _json(row[0]) or {}
            own, replay = self._claim_receipt(cur, operator, key, UNDO_COMMAND, digest)
            if replay is not None:
                return replay
            cur.execute("select 1 from platform.command_receipt where command_name = %s and status = 'completed' "
                        "and response_body ->> 'undoes_receipt_id' = %s", (UNDO_COMMAND, receipt_id))
            if cur.fetchone() is not None:
                raise CommandRefused(409, "already_undone", "that action was already undone")
            person = replace(operator, event_attribution={
                "source": "people_rules_undo", "undoes_receipt_id": receipt_id,
                "rule_id": action.get("rule_id"), "assertion_id": action.get("assertion_id"),
            })
            try:
                self._refuse_if_changed(cur, action)
                steps = self._undo_steps(_Ctx(self, cur, person, own, None), action, note)
            except CommandRefused:
                raise
            except Exception as exc:
                if not _is_database_error(exc):
                    raise
                raise CommandRefused(409, "undo_refused_by_database",
                                     str(exc).splitlines()[0] if str(exc) else type(exc).__name__) from exc
            response = {
                "undoes_receipt_id": receipt_id, "rule_id": action.get("rule_id"),
                "assertion_id": action.get("assertion_id"), "note": note, "steps": steps,
                "idempotency_key": key, "command_receipt_id": own, "replayed": False,
            }
            self._complete_receipt(cur, own, response)
            return response

    def _refuse_if_changed(self, cur: Any, action: dict[str, Any]) -> None:
        created = action.get("created") or {}
        if action.get("rule_id") == "P3" and created.get("person_id"):
            # The person this action created gained an address, a name or an institution since: archiving
            # it would strand them. A person undoes that from the ficha.
            cur.execute(
                """
                select (select count(*) from crm.contact_point where person_id = %(p)s and status = 'active'
                          and id is distinct from %(cp)s::uuid)
                     + (select count(*) from crm.person_name where person_id = %(p)s and removed_at is null)
                     + (select count(*) from crm.affiliation where person_id = %(p)s and valid_to is null
                          and id is distinct from %(aff)s::uuid)
                """,
                {"p": created["person_id"], "cp": created.get("contact_point_id"), "aff": created.get("affiliation_id")},
            )
            if int(cur.fetchone()[0]):
                raise CommandRefused(409, "changed_since", CHANGED_SINCE)
        tables = {"person": "crm.person", "contact_point": "crm.contact_point", "note": "crm.note"}
        for ref, version in sorted((action.get("versions") or {}).items()):
            kind, _, row_id = ref.partition(":")
            cur.execute(f"select version from {tables[kind]} where id = %s for update", (row_id,))  # noqa: S608
            row = cur.fetchone()
            if row is None or int(row[0]) != int(version):
                raise CommandRefused(409, "changed_since", CHANGED_SINCE)

    def _undo_steps(self, ctx: "_Ctx", action: dict[str, Any], note: str) -> list[str]:
        """Reverse of apply: the resolved participants, then the merge, then the created rows."""
        cur, steps = ctx.cur, []
        created = action.get("created") or {}
        if action.get("resolved_participant_ids"):
            cur.execute("update comms.message_participant set resolved_contact_point_id = null "
                        "where id = any(%s::uuid[]) and resolved_contact_point_id = %s",
                        (action["resolved_participant_ids"], action.get("resolved_contact_point_id")))
            steps.append("participants_unresolved")
        if action.get("merge"):
            self._unmerge(ctx, action["merge"])
            steps.append("unmerged")
        if created.get("person_name_id"):
            cur.execute("update crm.person_name set removed_at = now(), removed_by_operator_id = %s "
                        "where id = %s and removed_at is null returning person_id::text",
                        (ctx.operator.operator_id, created["person_name_id"]))
            row = cur.fetchone()
            if row is not None:
                ctx.event("person", row[0], "person.updated", {"person_name_removed": created["person_name_id"]})
                steps.append("name_removed")
        if created.get("contact_point_id"):
            cp_id = created["contact_point_id"]
            cur.execute("select version, status from crm.contact_point where id = %s", (cp_id,))
            row = cur.fetchone()
            if row is not None and row[1] == "active":
                _handle_deactivate_contact_point(self._authoring, cur, ctx.operator, {
                    "contact_point_id": cp_id, "expected_version": int(row[0]), "note": note}, ctx.receipt_id)
                steps.append("contact_point_deactivated")
        if created.get("affiliation_id"):
            cur.execute("select person_id::text from crm.affiliation where id = %s", (created["affiliation_id"],))
            row = cur.fetchone()
            if row is not None and self._close_affiliation(ctx, created["affiliation_id"], row[0]):
                steps.append("affiliation_closed")
        if created.get("person_id"):
            cur.execute("select version, status from crm.person where id = %s", (created["person_id"],))
            row = cur.fetchone()
            if row is not None and row[1] == "active":
                _handle_archive_person(self._authoring, cur, ctx.operator, {
                    "person_id": created["person_id"], "expected_version": int(row[0]),
                    "note": REMOVED_FROM_HOY}, ctx.receipt_id)
                steps.append("person_archived")
        return steps

    def _unmerge(self, ctx: "_Ctx", merge: dict[str, Any]) -> None:
        cur = ctx.cur
        winner_id, loser_id = merge["winner_person_id"], merge["loser_person_id"]
        moved, prior = merge.get("moved") or {}, merge.get("loser_prior") or {}
        cur.execute(
            """
            update crm.person
               set status = %s, archived_at = %s::timestamptz, archived_by_operator_id = %s, archive_reason = %s,
                   merged_into_person_id = %s, version = version + 1, updated_at = now()
             where id = %s and merged_into_person_id = %s
            """,
            (prior.get("status", "active"), prior.get("archived_at"), prior.get("archived_by_operator_id"),
             prior.get("archive_reason"), prior.get("merged_into_person_id"), loser_id, winner_id),
        )
        if cur.rowcount == 0:
            raise CommandRefused(409, "changed_since", CHANGED_SINCE)
        # Every recorded row goes back, or none: a row a person moved since refuses the whole undo.
        for ids, sql in (
            (moved.get("contact_point_ids") or [],
             "update crm.contact_point set person_id = %s, version = version + 1, updated_at = now() "
             "where id = any(%s::uuid[]) and person_id = %s"),
            (moved.get("opportunity_participant_ids") or [],
             "update crm.opportunity_participant set person_id = %s, updated_at = now() "
             "where id = any(%s::uuid[]) and person_id = %s"),
            (moved.get("campaign_recipient_ids") or [],
             "update outbound.campaign_recipient set person_id = %s where id = any(%s::uuid[]) and person_id = %s"),
            (moved.get("note_ids") or [],
             "update crm.note set subject_id = %s where id = any(%s::uuid[]) and subject_id = %s"),
        ):
            if ids:
                cur.execute(sql, (loser_id, ids, winner_id))
                if cur.rowcount != len(ids):
                    raise CommandRefused(409, "changed_since", CHANGED_SINCE)
        for aff_id in moved.get("opened_affiliation_ids") or []:
            self._close_affiliation(ctx, aff_id, winner_id)
        for aff_id in (moved.get("affiliation_ids") or []) + (moved.get("closed_affiliation_ids") or []):
            cur.execute("update crm.affiliation set valid_to = null, updated_at = now() "
                        "where id = %s and valid_to is not null returning organization_id::text", (aff_id,))
            row = cur.fetchone()
            if row is not None:
                ctx.event("affiliation", aff_id, "affiliation.opened",
                          {"person_id": loser_id, "organization_id": row[0], "reopened": True})
        cur.execute("update crm.person set version = version + 1, updated_at = now() where id = %s", (winner_id,))
        ctx.event("person", loser_id, "person.restored", {"unmerged_from_person_id": winner_id})
        ctx.event("person", winner_id, "person.updated", {"unmerged_person_id": loser_id})


class _Ctx:
    """One action's cursor, acting identity, receipt and what it created."""

    def __init__(self, repo: PeopleRulesRepository, cur: Any, operator: OperatorIdentity, receipt_id: str,
                 source_record_id: str | None) -> None:
        self.repo, self.cur, self.operator, self.receipt_id = repo, cur, operator, receipt_id
        self.source_record_id = source_record_id
        self.created: dict[str, Any] = {}
        self.versions: dict[str, int] = {}

    def event(self, kind: str, aggregate_id: str, event_type: str, payload: dict[str, Any]) -> str:
        return self.repo._append_event(self.cur, aggregate_kind=kind, aggregate_id=aggregate_id,
                                       event_type=event_type, payload=payload, operator=self.operator,
                                       receipt_id=self.receipt_id)


def _is_database_error(exc: Exception) -> bool:
    try:
        import psycopg
    except ImportError:  # pragma: no cover
        return False
    return isinstance(exc, psycopg.Error)


__all__ = ["APPLY_COMMAND", "CHANGED_SINCE", "PeopleRulesRepository", "READING_WINDOW_DAYS", "UNDO_COMMAND",
           "idempotency_key"]
