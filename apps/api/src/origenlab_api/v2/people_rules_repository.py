"""Who wrote each email: read the snapshot, apply the strong people actions, undo one (people first, Task 5).

`people_rules.py` decides; this module reads what it needs from PostgreSQL and runs what it planned,
as `origenlab_api`, the same way `mail_rules_repository.py` runs the email → cases rules:

**One action, one transaction, one receipt.** Every strong action (P1–P5) runs inside one
transaction under one `platform.command_receipt` (`apply_people_rule`, key
`people-rule:<assertion id>:<rule id>`; a P1b adds its name's source, since one reading may teach
both the header name and the signature name). A refused step rolls the whole action back, receipt
included, and the action stays planned. An action whose key has a completed receipt is never
planned again — an undone one included — while the reading's other actions still are; a reading
whose new address was attached or created (P2, P3) leaves the snapshot. And what a «Quitar» took
back is not learned again from the next email: an undone name, institution or merge is a block in
the snapshot (`blocked_names`, `blocked_affiliations`, `blocked_merges`; an undone merge is also a
«No» for its address).

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
    _winner,
    action_key,
    is_free_mail,
    name_norm,
    org_key,
    plan_people,
    readings_about,
)

APPLY_COMMAND = "apply_people_rule"
UNDO_COMMAND = "undo_people_rule"
#: A person's Sí/No to a W1 «¿Es …?» guess (Task 7). «No» is this receipt only; the planner reads
#: every answer back through `answered` and never asks the same (address, person) again.
ANSWER_COMMAND = "answer_person_guess"
#: The contact page's «Confirmar» on a learned name, address or institution (ruling R1).
CONFIRM_COMMAND = "confirm_person_fact"
ANSWERS = ("si", "no")
FACT_KINDS = ("name", "address", "affiliation")
STRONG_RULES = frozenset({"P1", "P1b", "P2", "P3", "P4", "P5"})
#: Readings older than this are not planned any more (the backfill reads the same 90 days).
READING_WINDOW_DAYS = 90
REMOVED_FROM_HOY = "Quitado de Hoy"
CHANGED_SINCE = "Alguien cambió este contacto después; revisa la ficha antes de quitarlo."
#: Rules that only resolve an email to a person already known: not listed on «Hoy».
QUIET_RULES = frozenset({"P1"})
_TITLE_MAX = 200


def _digest(value: dict[str, Any]) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def idempotency_key(action: PeopleAction) -> str:
    return action_key(action)


# ─────────────────────────────────────────────────────────────── the snapshot ──

#: The `people:v1` readings of the window, with their Gmail message id and the `from` participants
#: of their message that carry the sender's address — less the readings whose unknown address an
#: applied P2/P3 already attached or created (undone ones included): planned again they would read
#: as a known address. Every other reading stays; its applied actions drop out by key (`settled`).
_READINGS_SQL = """
select a.id::text as id, a.source_record_id::text as source_record_id, a.value,
       coalesce(array(select p.id::text
                        from comms.message m
                        join comms.message_participant p on p.message_id = m.id
                       where m.provider_message_id = sr.payload ->> 'gmail_message_id'
                         and p.role = 'from' and p.address_norm = lower(btrim(a.value ->> 'sender_address'))
                       order by p.id), '{}') as participant_ids,
       sr.payload ->> 'gmail_message_id' as message_id
  from evidence.assertion a
  join evidence.source_record sr on sr.id = a.source_record_id
 where a.kind = 'message_triage' and a.value_norm = 'people:v1'
   and a.created_at > now() - make_interval(days => %s)
   and not exists (select 1 from platform.command_receipt r
                    where r.command_name = %s and r.status = 'completed'
                      and r.response_body ->> 'assertion_id' = a.id::text
                      and r.response_body ->> 'rule_id' in ('P2', 'P3'))
 order by a.created_at, a.id
"""
#: The keys of the strong actions already applied in the window (a receipt is completed after its
#: reading was written, so the window bounds both).
_SETTLED_SQL = """
select idempotency_key from platform.command_receipt
 where command_name = %s and status = 'completed' and completed_at > now() - make_interval(days => %s)
"""
#: Every applied P1b, P4 or P5 a person undid with «Quitar», with the name / institution it added.
_UNDONE_SQL = """
select r.response_body, pn.person_id::text, pn.name_display, af.person_id::text, af.organization_id::text
  from platform.command_receipt r
  join platform.command_receipt u on u.command_name = %s and u.status = 'completed'
                                 and u.response_body ->> 'undoes_receipt_id' = r.id::text
  left join crm.person_name pn on pn.id::text = r.response_body -> 'created' ->> 'person_name_id'
  left join crm.affiliation af on af.id::text = r.response_body -> 'created' ->> 'affiliation_id'
 where r.command_name = %s and r.status = 'completed' and r.response_body ->> 'rule_id' in ('P1b', 'P4', 'P5')
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


def describe(row: dict[str, Any], names: dict[str, str]) -> str:
    """The short Spanish line «Hecho automáticamente» shows for one people action."""
    who = row.get("person_name") or "Una persona"
    rule = row.get("rule_id")
    if rule == "P5" and row.get("merge"):
        merge = row["merge"]
        winner = names.get(merge.get("winner_person_id")) or who
        loser = names.get(merge.get("loser_person_id")) or "otra ficha"
        return f"{winner} ↔ {loser}: fusionados"
    if rule == "P2":
        return f"{who}: nuevo correo {row.get('address')}"
    org = row.get("organization_name")
    if rule == "P3":
        return f"{who}: persona nueva en {org} ({row.get('address')})" if org else \
            f"{who}: persona nueva ({row.get('address')})"
    if rule == "P1b":
        also = row.get("name")
        return f"{who}: también se llama {also}" if also else f"{who}: nombre nuevo"
    if rule == "P4":
        return f"{who}: trabaja en {org}" if org else f"{who}: institución nueva"
    return f"{who}: {rule}"


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
                                 message_participant_ids=tuple(pids or ()), value=_json(value) or {},
                                 message_id=message_id)
                         for aid, srid, value, pids, message_id in cur.fetchall())

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

        cur.execute(_SETTLED_SQL, (APPLY_COMMAND, READING_WINDOW_DAYS + 1))
        settled = frozenset(r[0] for r in cur.fetchall())

        answered = self.answered(cur)
        blocked_names: set[tuple[str, str]] = set()
        blocked_affiliations: set[tuple[str, str]] = set()
        blocked_merges: set[frozenset[str]] = set()
        cur.execute(_UNDONE_SQL, (UNDO_COMMAND, APPLY_COMMAND))
        for body, name_person, name_display, aff_person, aff_org in cur.fetchall():
            body = _json(body) or {}
            rule = body.get("rule_id")
            if rule == "P1b":
                pid = _final(name_person or body.get("person_id"), merged)
                norm = name_norm(name_display or body.get("name") or "")
                if pid and norm:
                    blocked_names.add((pid, norm))
            elif rule == "P4":
                pid = _final(aff_person or body.get("person_id"), merged)
                if pid and aff_org:
                    blocked_affiliations.add((pid, aff_org))
            elif rule == "P5":
                merge = body.get("merge") or {}
                pair = [merge.get("winner_person_id"), merge.get("loser_person_id")]
                if all(pair):
                    blocked_merges.add(frozenset(pair))
                    # An undone merge is a «No» for its address and both persons.
                    address = (body.get("address") or "").strip().lower()
                    for pid in pair:
                        if address:
                            answered[(address, pid)] = False

        return PeopleSnapshot(readings=readings, persons=persons, names=names, contact_points=contact_points,
                              affiliations=affiliations, organizations=organizations, org_by_name=org_by_name,
                              org_by_domain=org_by_domain, answered=answered, settled=settled,
                              blocked_names=frozenset(blocked_names),
                              blocked_affiliations=frozenset(blocked_affiliations),
                              blocked_merges=frozenset(blocked_merges))

    def answered(self, cur: Any) -> dict[tuple[str, str], bool]:
        """(address, person id) → a person's Sí (True) or No (False) to a «¿Es …?» guess."""
        cur.execute(
            "select response_body ->> 'address', response_body ->> 'person_id', response_body ->> 'answer' "
            "from platform.command_receipt where command_name = %s and status = 'completed' "
            "order by completed_at, id",
            (ANSWER_COMMAND,),
        )
        return {(address, pid): answer == "si" for address, pid, answer in cur.fetchall() if address and pid}

    def plan(self) -> list[PeopleAction]:
        with self._read() as cur:
            snapshot = self.read_snapshot(cur)
        return plan_people(snapshot)

    # ------------------------------------------------------------------ weak guesses (W1)

    def _guesses(self, about: Any, keep: Any) -> list[dict[str, Any]]:
        """The W1 guesses the rules plan right now, as the contact page and the case show them.
        Planned at read time from the same snapshot the timer uses, so an answered pair, or a reading
        already handled, is never shown — but only over the readings `about(snapshot)` keeps (a
        person's or a case's, `readings_about`), and `keep(action)` choosing among their guesses."""
        with self._read() as cur:
            snapshot = about(self.read_snapshot(cur))
        if not snapshot.readings:
            return []
        weak = [a for a in plan_people(snapshot) if a.rule_id == "W1" and a.person_id and keep(a)]
        messages = {r.assertion_id: r.message_id for r in snapshot.readings}
        out = []
        for a in weak:
            pid = str(a.person_id)
            person = snapshot.persons.get(pid)
            name = person.display_name if person is not None else ""
            orgs = sorted(snapshot.organizations[o].name for o in snapshot.affiliations.get(pid, set())
                          if o in snapshot.organizations and snapshot.organizations[o].status == "active")
            org = orgs[0] if orgs else None
            out.append({
                "guess_id": f"{a.assertion_id}:{pid}", "address": a.address, "person_id": pid,
                "person_name": name, "organization_name": org, "reasons": list(a.reasons),
                "question_es": f"¿Es {name} ({org})?" if org else f"¿Es {name}?",
                "message_id": messages.get(a.assertion_id),
            })
        return out

    def guesses_for_person(self, person_id: str) -> list[dict[str, Any]]:
        """The «¿Es …?» guesses that name this person (contact page): planned only over the
        addresses that wrote a name sharing a word with one of the person's names."""
        def about(snap: PeopleSnapshot) -> PeopleSnapshot:
            person = snap.persons.get(person_id)
            if person is None:
                return readings_about(snap)
            names = {person.display_name} | {n for n, ids in snap.names.items() if person_id in ids}
            return readings_about(snap, names=names)
        return self._guesses(about, lambda a: a.person_id == person_id)

    def guesses_for_case(self, message_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        """Gmail message id → the guesses about who wrote that email (the case's «Correos»)."""
        wanted = {m for m in message_ids if m}
        if not wanted:
            return {}
        def about(snap: PeopleSnapshot) -> PeopleSnapshot:
            senders = {r.sender for r in snap.readings if r.message_id in wanted}
            return readings_about(snap, addresses=senders)

        out: dict[str, list[dict[str, Any]]] = {}
        for guess in self._guesses(about, lambda a: True):
            if guess["message_id"] in wanted:
                out.setdefault(guess["message_id"], []).append(guess)
        return out

    def answer_guess(self, operator: OperatorIdentity, guess_id: str, answer: str,
                     idempotency_key: str) -> dict[str, Any]:
        """One Sí/No (sales or admin, checked by the route). «Sí» attaches the address to the person
        as a person's act — P2's step, `confirmed`, events `actor_kind = 'operator'` — and points the
        email's sender at it; «No» records the answer only. Either way the planner never asks again,
        and a «No» also keeps P2/P5 from joining that pair by themselves."""
        assertion_id, _, person_id = (guess_id or "").partition(":")
        assertion_id = as_uuid(assertion_id, "guess_id")
        person_id = as_uuid(person_id, "guess_id")
        if answer not in ANSWERS:
            raise CommandRefused(422, "unknown_answer", "answer is si or no")
        digest = _digest({"guess_id": f"{assertion_id}:{person_id}", "answer": answer})
        with self._write() as cur:
            receipt_id, replay = self._claim_receipt(cur, operator, idempotency_key, ANSWER_COMMAND, digest)
            if replay is not None:
                return replay
            cur.execute(
                "select lower(btrim(a.value ->> 'sender_address')), a.source_record_id::text "
                "from evidence.assertion a where a.id = %s and a.kind = 'message_triage' and a.value_norm = 'people:v1'",
                (assertion_id,),
            )
            row = cur.fetchone()
            if row is None or not row[0]:
                raise CommandRefused(404, "guess_not_found", "no people reading with that id")
            address, source_record_id = row
            _live_person(cur, person_id)
            # One answer per (address, person): concurrent answers wait here, then see the first.
            cur.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))",
                        (f"people-guess:{address}:{person_id}",))
            cur.execute("select 1 from platform.command_receipt where command_name = %s and status = 'completed' "
                        "and response_body ->> 'address' = %s and response_body ->> 'person_id' = %s",
                        (ANSWER_COMMAND, address, person_id))
            if cur.fetchone() is not None:
                raise CommandRefused(409, "already_answered", "someone already answered that guess")
            response: dict[str, Any] = {
                "guess_id": f"{assertion_id}:{person_id}", "assertion_id": assertion_id, "address": address,
                "person_id": person_id, "answer": answer, "created": {}, "resolved_participant_ids": [],
                "idempotency_key": idempotency_key, "command_receipt_id": receipt_id, "replayed": False,
            }
            if answer == "si":
                person = replace(operator, acts_as_system=False, event_attribution={
                    "source": "people_guess", "guess_id": response["guess_id"], "assertion_id": assertion_id,
                })
                ctx = _Ctx(self, cur, person, receipt_id, source_record_id, confirmed=True)
                cur.execute("select person_id::text from crm.contact_point where kind = 'email' and value_norm = %s",
                            (address,))
                owner = cur.fetchone()
                if owner is not None:
                    # The address is someone's already: joining two persons is the ficha's «Unir».
                    raise CommandRefused(409, "contact_point_taken",
                                         "Esa dirección ya es de otra persona; únelas desde la ficha"
                                         if owner[0] != person_id else "Esa dirección ya es de esta persona")
                usage, organization_id = self._guess_usage(cur, address, person_id)
                try:
                    self._attach_address(ctx, address, person_id, usage, organization_id)
                except CommandRefused as exc:
                    if exc.code != "contact_point_taken":
                        raise
                    raise CommandRefused(409, "contact_point_taken",
                                         "Esa dirección ya es de otra persona; únelas desde la ficha") from exc
                except Exception as exc:  # a writer the lock does not cover: the unique index decides
                    if not _is_unique_violation(exc):
                        raise
                    raise CommandRefused(409, "contact_point_taken",
                                         "Esa dirección ya es de otra persona; únelas desde la ficha") from exc
                cur.execute(
                    "select coalesce(array(select p.id::text from evidence.source_record sr "
                    "join comms.message m on m.provider_message_id = sr.payload ->> 'gmail_message_id' "
                    "join comms.message_participant p on p.message_id = m.id "
                    "where sr.id = %s and p.role = 'from' and p.address_norm = %s order by p.id), '{}')",
                    (source_record_id, address),
                )
                participants = list(cur.fetchone()[0] or [])
                resolved_cp, resolved = self._resolve_participants(cur, address, participants)
                response.update(created=ctx.created, versions=ctx.versions, resolved_participant_ids=resolved,
                                resolved_contact_point_id=resolved_cp, usage=usage, organization_id=organization_id)
            self._complete_receipt(cur, receipt_id, response)
            return response

    def _guess_usage(self, cur: Any, address: str, person_id: str) -> tuple[str, str | None]:
        """How a «Sí» address is filed: free mail is personal; an institution's exclusive domain is
        work at it; otherwise work at the person's one open institution, else personal."""
        domain = address.rsplit("@", 1)[-1]
        if is_free_mail(domain):
            return "personal", None
        cur.execute(
            "select d.organization_id::text from crm.organization_domain d join crm.organization o "
            "on o.id = d.organization_id where d.domain_norm = %s and d.scope = 'exclusive' "
            "and d.removed_at is null and o.status = 'active' and o.merged_into_organization_id is null",
            (domain,),
        )
        rows = cur.fetchall()
        if len(rows) == 1:
            return "work", rows[0][0]
        cur.execute(
            "select a.organization_id::text from crm.affiliation a join crm.organization o on o.id = a.organization_id "
            "where a.person_id = %s and a.valid_to is null and o.status = 'active'", (person_id,))
        orgs = sorted({r[0] for r in cur.fetchall()})
        return ("work", orgs[0]) if len(orgs) == 1 else ("personal", None)

    # ------------------------------------------------------------------ «Confirmar» (ruling R1)

    def confirm_fact(self, operator: OperatorIdentity, kind: str, fact_id: str,
                     idempotency_key: str) -> dict[str, Any]:
        """A person confirms a learned name, address or institution: `confirmation = 'confirmed'`
        (and `confirmed_by_operator_id` on an affiliation, the one table of the three with it), one
        operator event, one receipt. «Quitar» then no longer undoes the action that learned it."""
        if kind not in FACT_KINDS:
            raise CommandRefused(422, "unknown_kind", "kind is name, address or affiliation")
        fact_id = as_uuid(fact_id, "id")
        digest = _digest({"kind": kind, "id": fact_id})
        with self._write() as cur:
            receipt_id, replay = self._claim_receipt(cur, operator, idempotency_key, CONFIRM_COMMAND, digest)
            if replay is not None:
                return replay
            if kind == "name":
                cur.execute("select person_id::text, confirmation from crm.person_name "
                            "where id = %s and removed_at is null for update", (fact_id,))
            elif kind == "address":
                cur.execute("select person_id::text, confirmation from crm.contact_point "
                            "where id = %s and status = 'active' and person_id is not null for update", (fact_id,))
            else:
                cur.execute("select person_id::text, confirmation from crm.affiliation "
                            "where id = %s and valid_to is null for update", (fact_id,))
            row = cur.fetchone()
            if row is None:
                raise CommandRefused(404, "fact_not_found", f"no live {kind} with that id")
            person_id, confirmation = row
            if confirmation == "confirmed":
                raise CommandRefused(409, "already_confirmed", "a person already confirmed it")
            ctx = _Ctx(self, cur, replace(operator, acts_as_system=False, event_attribution=None), receipt_id, None)
            if kind == "name":
                cur.execute("update crm.person_name set confirmation = 'confirmed' where id = %s", (fact_id,))
                self._bump_person(ctx, person_id)
                ctx.event("person", person_id, "person.updated", {"person_name_confirmed": fact_id})
            elif kind == "address":
                cur.execute("update crm.contact_point set confirmation = 'confirmed', version = version + 1, "
                            "updated_at = now() where id = %s", (fact_id,))
                ctx.event("contact_point", fact_id, "contact_point.confirmed",
                          {"contact_point_id": fact_id, "person_id": person_id})
            else:
                cur.execute("update crm.affiliation set confirmation = 'confirmed', confirmed_by_operator_id = %s, "
                            "updated_at = now() where id = %s", (operator.operator_id, fact_id))
                self._bump_person(ctx, person_id)
                ctx.event("person", person_id, "person.updated", {"affiliation_confirmed": fact_id})
            response = {"kind": kind, "id": fact_id, "person_id": person_id, "confirmation": "confirmed",
                        "idempotency_key": idempotency_key, "command_receipt_id": receipt_id, "replayed": False}
            self._complete_receipt(cur, receipt_id, response)
            return response

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
                    "name": body.get("name"), "organization_name": body.get("organization_name"),
                })
            # Receipts written before the texts were stored: read them from the rows they created.
            for row in out:
                created = row["created"]
                if row["rule_id"] == "P1b" and not row["name"] and created.get("person_name_id"):
                    cur.execute("select name_display from crm.person_name where id = %s",
                                (created["person_name_id"],))
                    found = cur.fetchone()
                    row["name"] = found[0] if found else None
                if row["rule_id"] in ("P3", "P4") and not row["organization_name"] and created.get("affiliation_id"):
                    cur.execute("select o.name from crm.affiliation a join crm.organization o "
                                "on o.id = a.organization_id where a.id = %s", (created["affiliation_id"],))
                    found = cur.fetchone()
                    row["organization_name"] = found[0] if found else None
            merged = [m for row in out if (m := row["merge"])]
            names: dict[str, str] = {}
            if merged:
                ids = sorted({i for m in merged for i in (m.get("winner_person_id"), m.get("loser_person_id")) if i})
                cur.execute("select id::text, display_name from crm.person where id = any(%s::uuid[])", (ids,))
                names = dict(cur.fetchall())
        for row in out:
            row["what"] = describe(row, names)
        return out

    def has_receipt(self, receipt_id: str) -> bool:
        """Whether `receipt_id` is a completed people-rule application (not a mail rule's)."""
        receipt_id = as_uuid(receipt_id, "receipt_id")
        with self._read() as cur:
            cur.execute("select 1 from platform.command_receipt where id = %s and command_name = %s "
                        "and status = 'completed'", (receipt_id, APPLY_COMMAND))
            return cur.fetchone() is not None

    def remove_automatic(self, operator: OperatorIdentity, receipt_id: str) -> dict[str, Any]:
        """«Quitar» on Hoy (sales or admin): undo one people action the automatic run took. A
        person's own «Aplicar» is not removed here."""
        receipt_id = as_uuid(receipt_id, "receipt_id")
        with self._read() as cur:
            cur.execute("select response_body ->> 'trigger' from platform.command_receipt "
                        "where id = %s and command_name = %s and status = 'completed'", (receipt_id, APPLY_COMMAND))
            row = cur.fetchone()
        if row is None:
            raise CommandRefused(404, "applied_action_not_found", "no applied people action with that receipt")
        if row[0] != "automatic":
            raise CommandRefused(403, "not_automatic", "only what the system did by itself is removed from Hoy")
        return self.undo(operator, receipt_id, REMOVED_FROM_HOY)

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
            if action.rule_id == "P1b":
                response["name"] = fields["name"].strip()
            elif action.rule_id in ("P3", "P4"):
                cur.execute("select name from crm.organization where id = %s", (fields["organization_id"],))
                found = cur.fetchone()
                if found is not None:
                    response["organization_name"] = found[0]
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
        # One claim on an address at a time (the timer's P2/P3 and a person's «Sí»): the second
        # waits here, then sees the first's row and is refused by name instead of by the unique index.
        ctx.cur.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"people-address:{address}",))
        ctx.cur.execute("select 1 from crm.contact_point where kind = 'email' and value_norm = %s for update",
                        (address,))
        if ctx.cur.fetchone() is not None:
            raise CommandRefused(409, "contact_point_taken", "the address is already in the CRM")
        ctx.cur.execute(
            """
            insert into crm.contact_point (kind, value_norm, value_display, person_id, organization_id, usage,
                                           confirmation, origin_source_record_id)
            values ('email', %s, %s, %s, %s, %s, %s, %s)
            returning id::text, version
            """,
            (address, address, person_id, organization_id, usage,
             "confirmed" if ctx.confirmed else "machine_proposed", ctx.source_record_id),
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
            "archived_by_operator_id::text, archive_reason, display_name, confirmation, created_at "
            "from crm.person where id = any(%s::uuid[]) order by id for update",
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
        # Re-checked under the row locks: a «Confirmar» between the plan and now changes the answer.
        if winner[8] == "confirmed" and loser[8] == "confirmed":
            raise CommandRefused(409, "plan_changed", "both persons are confirmed now; only a person merges them")
        as_row = {i: PersonRow(id=i, display_name=r[7], confirmation=r[8], status=r[1], merged_into=r[3],
                               created_at=r[9]) for i, r in rows.items()}
        if _winner(as_row[winner_id], as_row[loser_id])[0].id != winner_id:
            raise CommandRefused(409, "plan_changed", "the person to keep is not the planned one any more")
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
        # A learned name or institution a person confirmed since is theirs now (`confirm-person-fact`).
        for table, key in (("crm.person_name", "person_name_id"), ("crm.affiliation", "affiliation_id")):
            if created.get(key):
                cur.execute(f"select confirmation from {table} where id = %s", (created[key],))  # noqa: S608
                row = cur.fetchone()
                if row is not None and row[0] == "confirmed":
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
                 source_record_id: str | None, *, confirmed: bool = False) -> None:
        self.repo, self.cur, self.operator, self.receipt_id = repo, cur, operator, receipt_id
        self.source_record_id = source_record_id
        #: A person's act (a «Sí»): the rows it creates are `confirmed`, not `machine_proposed`.
        self.confirmed = confirmed
        self.created: dict[str, Any] = {}
        self.versions: dict[str, int] = {}

    def event(self, kind: str, aggregate_id: str, event_type: str, payload: dict[str, Any]) -> str:
        return self.repo._append_event(self.cur, aggregate_kind=kind, aggregate_id=aggregate_id,
                                       event_type=event_type, payload=payload, operator=self.operator,
                                       receipt_id=self.receipt_id)


def _is_unique_violation(exc: Exception) -> bool:
    try:
        from psycopg import errors
    except ImportError:  # pragma: no cover
        return False
    return isinstance(exc, errors.UniqueViolation)


def _is_database_error(exc: Exception) -> bool:
    try:
        import psycopg
    except ImportError:  # pragma: no cover
        return False
    return isinstance(exc, psycopg.Error)


__all__ = ["ANSWER_COMMAND", "APPLY_COMMAND", "CHANGED_SINCE", "CONFIRM_COMMAND", "PeopleRulesRepository",
           "READING_WINDOW_DAYS", "UNDO_COMMAND", "idempotency_key"]
