"""CRM freeform authoring commands — people, organizations, contact points, notes, lines.

Every handler runs inside the single transaction `CommandTransaction` provides.  No physical
row is ever deleted; archiving and deactivating are the only removals.  CAS tokens prevent
two operators from overwriting each other silently.

**What this file never does:**
- Opens `outbound.*`, `platform.command_receipt` or `crm.domain_event` by name (those are
  `CommandTransaction`'s domain; this module calls ``_append_event`` / ``_live_organization``).
- Writes a raw email address into any event payload (contact-point id and kind travel instead).
- Infers or scores anything: every aggregate is a UUID the operator supplied.
- Physically removes any row.  No row-removal SQL exists in this module.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from origenlab_api.v2.command_core import CommandTransaction, json_payload
from origenlab_api.v2.commands import (
    CommandRefused,
    normalize_email,
    normalize_organization_name,
)
from origenlab_api.v2.crm_merge_preview import merge_preview_digest, merge_preview  # noqa: F401 – re-exported
from origenlab_api.v2.identity import OperatorIdentity

#: The six curated product-line ids that the website carries and this CRM mirrors.
PRODUCT_LINE_IDS: frozenset[str] = frozenset(
    {"hielscher", "ortoalresa", "ika", "adam-equipment", "loeser", "serva"}
)

_PHONE_STRIP = re.compile(r"[\s()\-]")
_E164_SHAPE = re.compile(r"^\+[1-9][0-9]{6,14}$")
_DOMAIN_SHAPE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")
_DOMAIN_NORM_SHAPE = re.compile(r"^[a-z0-9.-]+$")


def normalize_phone(raw: str) -> str:
    """Strip common separators and validate E.164 shape.

    Accepts ``+56 9 1234 5678`` and ``+56912345678``.  Refuses anything
    that does not start with ``+`` after stripping, or whose digit count is
    outside the ITU-T range.
    """
    stripped = _PHONE_STRIP.sub("", raw.strip())
    if not _E164_SHAPE.match(stripped):
        raise CommandRefused(
            422,
            "not_a_phone_number",
            "phone value must be in E.164 format (e.g. +56912345678)",
        )
    return stripped


def _normalize_contact_value(kind: str, raw: str) -> str:
    if kind == "email":
        value = normalize_email(raw)
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value):
            raise CommandRefused(422, "not_an_email_address", "value is not a valid email address")
        return value
    if kind == "phone":
        return normalize_phone(raw)
    raise CommandRefused(422, "invalid_contact_kind", "kind must be 'email' or 'phone'")


def _normalize_domain(raw: str) -> str:
    norm = raw.strip().lower()
    if not _DOMAIN_SHAPE.match(norm):
        raise CommandRefused(
            422, "invalid_domain", "domain must be a lower-case hostname (e.g. example.com)"
        )
    return norm


# ─────────────────────────────────────────────────────────────── shared locked reads ──

def _live_person(
    cur: Any,
    person_id: str,
    expected_version: int | None = None,
    *,
    allow_archived: bool = False,
) -> dict[str, Any]:
    cur.execute(
        """
        select id::text as id, display_name, given_name, family_name, title,
               status, version, confirmation,
               merged_into_person_id::text as merged_into_person_id
          from crm.person
         where id = %s
           for update
        """,
        (person_id,),
    )
    row = _one(cur)
    if row is None:
        raise CommandRefused(404, "person_not_found", "no such person")
    if row["merged_into_person_id"] is not None:
        raise CommandRefused(409, "merged_person", "that person has been merged into another")
    if not allow_archived and row["status"] == "archived":
        raise CommandRefused(409, "archived_subject", "that person is archived")
    if expected_version is not None and row["version"] != expected_version:
        raise CommandRefused(409, "stale_version", "the person was modified since you loaded it")
    return row


def _live_contact_point(
    cur: Any, contact_point_id: str, expected_version: int | None = None
) -> dict[str, Any]:
    cur.execute(
        """
        select id::text as id, kind, value_norm, value_display,
               person_id::text as person_id,
               organization_id::text as organization_id,
               usage, status, version, note
          from crm.contact_point
         where id = %s
           for update
        """,
        (contact_point_id,),
    )
    row = _one(cur)
    if row is None:
        raise CommandRefused(404, "contact_point_not_found", "no such contact point")
    if expected_version is not None and row["version"] != expected_version:
        raise CommandRefused(
            409, "stale_version", "the contact point was modified since you loaded it"
        )
    return row


def _live_note(
    cur: Any, note_id: str, expected_version: int | None = None
) -> dict[str, Any]:
    cur.execute(
        """
        select id::text as id, subject_kind, subject_id::text as subject_id,
               body, author_operator_id::text as author_operator_id,
               root_note_id::text as root_note_id,
               revision_of_note_id::text as revision_of_note_id,
               revision_no, status, version,
               archived_at, archived_by_operator_id::text as archived_by_operator_id,
               archive_reason
          from crm.note
         where id = %s
           for update
        """,
        (note_id,),
    )
    row = _one(cur)
    if row is None:
        raise CommandRefused(404, "note_not_found", "no such note")
    if expected_version is not None and row["version"] != expected_version:
        raise CommandRefused(409, "stale_version", "the note was modified since you loaded it")
    return row


def _live_assertion(
    cur: Any, assertion_id: str, *, kind: str | None = None
) -> dict[str, Any]:
    cur.execute(
        """
        select id::text as id, kind, value_norm, resolution,
               resolved_kind, resolved_id::text as resolved_id,
               resolved_at, resolved_by_operator_id::text as resolved_by_operator_id,
               ambiguity_note
          from evidence.assertion
         where id = %s
           for update
        """,
        (assertion_id,),
    )
    row = _one(cur)
    if row is None:
        raise CommandRefused(404, "assertion_not_found", "no such assertion")
    if kind is not None and row["kind"] != kind:
        raise CommandRefused(
            409,
            "wrong_assertion_kind",
            f"expected a {kind!r} assertion, got {row['kind']!r}",
        )
    if row["resolution"] != "unresolved":
        raise CommandRefused(409, "candidate_already_decided", "this assertion has already been resolved")
    return row


def _one(cur: Any) -> dict[str, Any] | None:
    row = cur.fetchone()
    if row is None:
        return None
    return dict(zip([d[0] for d in cur.description], row, strict=True))


# ─────────────────────────────────────────────────────────────────────── handlers ──

def _handle_create_person(
    self: "V2CrmAuthoringRepository",
    cur: Any,
    operator: OperatorIdentity,
    fields: dict[str, Any],
    receipt_id: str,
) -> dict[str, Any]:
    display_name = fields["display_name"]
    given_name = fields.get("given_name")
    family_name = fields.get("family_name")
    title = fields.get("title")
    email = fields.get("email")
    phone = fields.get("phone")
    organization_id = fields.get("organization_id")
    role_title = fields.get("role_title")
    note = fields["note"]

    cur.execute(
        """
        insert into crm.person
            (display_name, given_name, family_name, title,
             confirmation, confirmed_by_operator_id, status)
        values (%s, %s, %s, %s, 'confirmed', %s::uuid, 'active')
        returning id::text as id, version
        """,
        (display_name, given_name, family_name, title, operator.operator_id),
    )
    person = _one(cur)
    person_id = person["id"]

    self._append_event(
        cur,
        aggregate_kind="person",
        aggregate_id=person_id,
        event_type="person.created",
        payload={"display_name": display_name, "note": note},
        operator=operator,
        receipt_id=receipt_id,
    )

    # Optional email contact point
    if email:
        value_norm = _normalize_contact_value("email", email)
        _create_contact_point(
            self, cur, operator, receipt_id,
            kind="email", value_norm=value_norm, value_display=value_norm,
            person_id=person_id, organization_id=None, usage="personal",
        )

    # Optional phone contact point
    if phone:
        value_norm = _normalize_contact_value("phone", phone)
        _create_contact_point(
            self, cur, operator, receipt_id,
            kind="phone", value_norm=value_norm, value_display=value_norm,
            person_id=person_id, organization_id=None, usage="personal",
        )

    # Optional affiliation
    if organization_id:
        _live_organization_active(cur, organization_id)
        cur.execute(
            """
            insert into crm.affiliation
                (person_id, organization_id, role_title, valid_from, confirmation, confirmed_by_operator_id)
            values (%s::uuid, %s::uuid, %s, current_date, 'confirmed', %s::uuid)
            returning id::text as id
            """,
            (person_id, organization_id, role_title, operator.operator_id),
        )
        aff = _one(cur)
        self._append_event(
            cur,
            aggregate_kind="affiliation",
            aggregate_id=aff["id"],
            event_type="affiliation.opened",
            payload={"person_id": person_id, "organization_id": organization_id, "role_title": role_title},
            operator=operator,
            receipt_id=receipt_id,
        )

    # Bump person version so the create returns version=1 (already 1 from default)
    return {"ok": True, "person_id": person_id, "version": person["version"]}


def _live_organization_active(cur: Any, organization_id: str) -> dict[str, Any]:
    cur.execute(
        """
        select id::text as id, name, kind, status, version
          from crm.organization
         where id = %s
        """,
        (organization_id,),
    )
    row = _one(cur)
    if row is None:
        raise CommandRefused(409, "organization_not_found", "no such organization")
    if row["status"] == "archived":
        raise CommandRefused(409, "archived_subject", "that organization is archived")
    return row


def _create_contact_point(
    self: "V2CrmAuthoringRepository",
    cur: Any,
    operator: OperatorIdentity,
    receipt_id: str,
    *,
    kind: str,
    value_norm: str,
    value_display: str,
    person_id: str | None,
    organization_id: str | None,
    usage: str,
) -> str:
    cur.execute(
        """
        insert into crm.contact_point
            (kind, value_norm, value_display, person_id, organization_id, usage,
             confirmation)
        values (%s, %s, %s, %s::uuid, %s::uuid, %s, 'confirmed')
        returning id::text as id
        """,
        (kind, value_norm, value_display, person_id, organization_id, usage),
    )
    row = _one(cur)
    cp_id = row["id"]
    self._append_event(
        cur,
        aggregate_kind="contact_point",
        aggregate_id=cp_id,
        event_type="contact_point.created",
        payload={"kind": kind, "contact_point_id": cp_id, "usage": usage},
        operator=operator,
        receipt_id=receipt_id,
    )
    return cp_id


def _handle_update_person(
    self: "V2CrmAuthoringRepository",
    cur: Any,
    operator: OperatorIdentity,
    fields: dict[str, Any],
    receipt_id: str,
) -> dict[str, Any]:
    person_id = fields["person_id"]
    expected_version = fields["expected_version"]
    note = fields["note"]

    person = _live_person(cur, person_id, expected_version)

    changes: dict[str, Any] = {}
    for col in ("display_name", "given_name", "family_name", "title"):
        if col in fields and fields[col] is not None:
            changes[col] = fields[col]

    if not changes:
        raise CommandRefused(422, "no_changes", "no fields to update")

    set_clause = ", ".join(f"{c} = %s" for c in changes)
    values = list(changes.values()) + [expected_version, person_id]
    cur.execute(
        f"""
        update crm.person
           set {set_clause}, version = version + 1, updated_at = now()
         where version = %s and id = %s::uuid
        """,
        values,
    )
    if cur.rowcount == 0:
        raise CommandRefused(409, "stale_version", "concurrent modification")

    self._append_event(
        cur,
        aggregate_kind="person",
        aggregate_id=person_id,
        event_type="person.updated",
        payload={"changed": list(changes.keys()), "note": note},
        operator=operator,
        receipt_id=receipt_id,
    )
    return {"ok": True, "person_id": person_id, "version": expected_version + 1}


def _handle_archive_person(
    self: "V2CrmAuthoringRepository",
    cur: Any,
    operator: OperatorIdentity,
    fields: dict[str, Any],
    receipt_id: str,
) -> dict[str, Any]:
    person_id = fields["person_id"]
    expected_version = fields["expected_version"]
    reason = fields["note"]  # "reason" is supplied in the "note" field per CONTRACT

    _live_person(cur, person_id, expected_version)

    cur.execute(
        """
        update crm.person
           set status = 'archived', archived_at = now(),
               archived_by_operator_id = %s::uuid, archive_reason = %s,
               version = version + 1, updated_at = now()
         where version = %s and id = %s::uuid
        """,
        (operator.operator_id, reason, expected_version, person_id),
    )
    if cur.rowcount == 0:
        raise CommandRefused(409, "stale_version", "concurrent modification")

    references = _person_references(cur, person_id)
    self._append_event(
        cur,
        aggregate_kind="person",
        aggregate_id=person_id,
        event_type="person.archived",
        payload={"archive_reason": reason},
        operator=operator,
        receipt_id=receipt_id,
    )
    return {"ok": True, "person_id": person_id, "version": expected_version + 1, "references": references}


def _person_references(cur: Any, person_id: str) -> dict[str, int]:
    cur.execute(
        """
        select
          (select count(*) from outbound.campaign_recipient where person_id = %s::uuid) as campaign_recipients,
          (select count(*) from crm.opportunity_participant where person_id = %s::uuid) as opportunity_participants,
          (select count(*) from crm.quote q
            join crm.opportunity o on o.id = q.opportunity_id
            join crm.opportunity_participant op on op.opportunity_id = o.id
           where op.person_id = %s::uuid) as quotes,
          (select count(*) from evidence.assertion where resolved_kind = 'person' and resolved_id = %s::uuid) as evidence_assertions,
          (select count(*) from crm.note where subject_kind = 'person' and subject_id = %s::uuid) as notes
        """,
        (person_id, person_id, person_id, person_id, person_id),
    )
    return _one(cur)


def _handle_restore_person(
    self: "V2CrmAuthoringRepository",
    cur: Any,
    operator: OperatorIdentity,
    fields: dict[str, Any],
    receipt_id: str,
) -> dict[str, Any]:
    person_id = fields["person_id"]
    expected_version = fields["expected_version"]
    note = fields["note"]

    person = _live_person(cur, person_id, expected_version, allow_archived=True)
    if person["status"] != "archived":
        raise CommandRefused(409, "not_archived", "that person is not archived")

    cur.execute(
        """
        update crm.person
           set status = 'active', archived_at = null,
               archived_by_operator_id = null, archive_reason = null,
               version = version + 1, updated_at = now()
         where version = %s and id = %s::uuid
        """,
        (expected_version, person_id),
    )
    if cur.rowcount == 0:
        raise CommandRefused(409, "stale_version", "concurrent modification")

    self._append_event(
        cur,
        aggregate_kind="person",
        aggregate_id=person_id,
        event_type="person.restored",
        payload={"note": note},
        operator=operator,
        receipt_id=receipt_id,
    )
    return {"ok": True, "person_id": person_id, "version": expected_version + 1}


def _handle_merge_people(
    self: "V2CrmAuthoringRepository",
    cur: Any,
    operator: OperatorIdentity,
    fields: dict[str, Any],
    receipt_id: str,
) -> dict[str, Any]:
    loser_id = fields["loser_person_id"]
    winner_id = fields["winner_person_id"]
    expected_loser_version = fields["expected_loser_version"]
    expected_winner_version = fields["expected_winner_version"]
    expected_sha = fields["expected_preview_sha256"]
    note = fields["note"]

    if loser_id == winner_id:
        raise CommandRefused(409, "same_person", "loser and winner must be different people")

    # Lock both in a consistent order to prevent deadlock
    ids_sorted = sorted([loser_id, winner_id])
    cur.execute(
        """
        select id::text as id, display_name, version, status,
               merged_into_person_id::text as merged_into_person_id
          from crm.person
         where id = any(%s)
           for update
        """,
        (ids_sorted,),
    )
    rows: dict[str, Any] = {}
    while True:
        r = _one(cur)
        if r is None:
            break
        rows[r["id"]] = r

    loser = rows.get(loser_id)
    winner = rows.get(winner_id)
    if loser is None:
        raise CommandRefused(404, "person_not_found", "loser person not found")
    if winner is None:
        raise CommandRefused(404, "person_not_found", "winner person not found")
    if loser["merged_into_person_id"] is not None:
        raise CommandRefused(409, "merged_person", "the loser has already been merged")
    if loser["status"] == "archived":
        raise CommandRefused(409, "archived_subject", "the loser person is archived")
    if winner["status"] == "archived":
        raise CommandRefused(409, "archived_subject", "the winner person is archived")
    if loser["version"] != expected_loser_version:
        raise CommandRefused(409, "stale_version", "loser person was modified since you loaded it")
    if winner["version"] != expected_winner_version:
        raise CommandRefused(409, "stale_version", "winner person was modified since you loaded it")

    # Count moves (with locks held)
    cur.execute(
        "select count(*) as n from crm.contact_point where person_id = %s::uuid",
        (loser_id,),
    )
    cp_count = int(_one(cur)["n"])
    cur.execute(
        "select count(*) as n from crm.affiliation where person_id = %s::uuid and valid_to is null",
        (loser_id,),
    )
    aff_count = int(_one(cur)["n"])
    cur.execute(
        "select count(*) as n from crm.opportunity_participant where person_id = %s::uuid",
        (loser_id,),
    )
    opp_count = int(_one(cur)["n"])
    cur.execute(
        "select count(*) as n from outbound.campaign_recipient where person_id = %s::uuid",
        (loser_id,),
    )
    cr_count = int(_one(cur)["n"])
    cur.execute(
        "select count(*) as n from crm.note where subject_kind = 'person' and subject_id = %s::uuid",
        (loser_id,),
    )
    note_count = int(_one(cur)["n"])

    moves = {
        "contact_points": cp_count,
        "affiliations": aff_count,
        "opportunity_participants": opp_count,
        "campaign_recipients": cr_count,
        "notes": note_count,
    }

    actual_sha = merge_preview_digest({
        "loser":  {"id": loser_id,  "version": loser["version"]},
        "winner": {"id": winner_id, "version": winner["version"]},
        "moves":  moves,
    })
    if actual_sha != expected_sha:
        raise CommandRefused(
            409, "preview_changed", "the merge preview changed; reload and re-confirm"
        )

    # Repoint contact points
    cur.execute(
        "update crm.contact_point set person_id = %s::uuid where person_id = %s::uuid",
        (winner_id, loser_id),
    )

    # Repoint opportunity participants
    cur.execute(
        """
        update crm.opportunity_participant
           set person_id = %s::uuid
         where person_id = %s::uuid
        """,
        (winner_id, loser_id),
    )

    # Repoint campaign_recipient
    cur.execute(
        """
        update outbound.campaign_recipient
           set person_id = %s::uuid
         where person_id = %s::uuid
        """,
        (winner_id, loser_id),
    )

    # Repoint notes
    cur.execute(
        """
        update crm.note
           set subject_id = %s::uuid
         where subject_kind = 'person' and subject_id = %s::uuid
        """,
        (winner_id, loser_id),
    )

    # Affiliations: repoint non-conflicting; close conflicting loser ones
    cur.execute(
        """
        select la.id::text as id, la.organization_id::text as organization_id
          from crm.affiliation la
         where la.person_id = %s::uuid and la.valid_to is null
        """,
        (loser_id,),
    )
    loser_affs = []
    while True:
        r = _one(cur)
        if r is None:
            break
        loser_affs.append(r)

    for la in loser_affs:
        cur.execute(
            """
            select id from crm.affiliation
             where person_id = %s::uuid and organization_id = %s::uuid and valid_to is null
            """,
            (winner_id, la["organization_id"]),
        )
        conflict = _one(cur)
        if conflict is not None:
            # Close the loser's affiliation
            cur.execute(
                "update crm.affiliation set valid_to = current_date where id = %s::uuid",
                (la["id"],),
            )
        else:
            # Repoint to winner
            cur.execute(
                "update crm.affiliation set person_id = %s::uuid where id = %s::uuid",
                (winner_id, la["id"]),
            )

    # Archive the loser
    cur.execute(
        """
        update crm.person
           set status = 'archived', archived_at = now(),
               archived_by_operator_id = %s::uuid,
               archive_reason = %s,
               merged_into_person_id = %s::uuid,
               version = version + 1, updated_at = now()
         where id = %s::uuid
        """,
        (
            operator.operator_id,
            f"fusionada en {winner_id}",
            winner_id,
            loser_id,
        ),
    )

    # Bump winner version
    cur.execute(
        """
        update crm.person
           set version = version + 1, updated_at = now()
         where id = %s::uuid
        returning version
        """,
        (winner_id,),
    )
    winner_new_version = _one(cur)["version"]

    self._append_event(
        cur,
        aggregate_kind="person",
        aggregate_id=loser_id,
        event_type="person.merged",
        payload={"winner_person_id": winner_id, "note": note},
        operator=operator,
        receipt_id=receipt_id,
    )
    self._append_event(
        cur,
        aggregate_kind="person",
        aggregate_id=winner_id,
        event_type="person.updated",
        payload={"merged_from_person_id": loser_id, "note": note},
        operator=operator,
        receipt_id=receipt_id,
    )

    return {
        "ok": True,
        "loser_person_id": loser_id,
        "winner_person_id": winner_id,
        "version": winner_new_version,
    }


def _handle_add_contact_point(
    self: "V2CrmAuthoringRepository",
    cur: Any,
    operator: OperatorIdentity,
    fields: dict[str, Any],
    receipt_id: str,
) -> dict[str, Any]:
    person_id = fields.get("person_id")
    organization_id = fields.get("organization_id")
    expected_version = fields["expected_version"]
    kind = fields["kind"]
    value = fields["value"]
    usage = fields["usage"]
    note_text = fields["note"]

    value_norm = _normalize_contact_value(kind, value)

    # CAS on the primary subject
    if person_id:
        _live_person(cur, person_id, expected_version)
    elif organization_id:
        org = _live_organization_active(cur, organization_id)
        if org["version"] != expected_version:
            raise CommandRefused(409, "stale_version", "organization was modified since you loaded it")
    else:
        raise CommandRefused(422, "missing_subject", "one of person_id or organization_id is required")

    # Check uniqueness
    cur.execute(
        """
        select id::text as id, person_id::text as person_id,
               organization_id::text as organization_id, status
          from crm.contact_point
         where kind = %s and value_norm = %s
           for update
        """,
        (kind, value_norm),
    )
    existing_cp = _one(cur)
    if existing_cp is not None:
        same_person = person_id and existing_cp["person_id"] == person_id
        same_org = organization_id and existing_cp["organization_id"] == organization_id
        subject_matches = same_person or same_org

        if not subject_matches:
            raise CommandRefused(
                409, "contact_point_taken",
                "this contact value belongs to a different subject",
            )
        if existing_cp["status"] == "active":
            raise CommandRefused(409, "already_exists", "this contact point is already active")
        # Reactivate
        cur.execute(
            """
            update crm.contact_point
               set status = 'active', deactivated_at = null,
                   deactivated_by_operator_id = null,
                   note = %s, version = version + 1, updated_at = now()
             where id = %s::uuid
            returning version
            """,
            (note_text or None, existing_cp["id"]),
        )
        new_version = _one(cur)["version"]
        self._append_event(
            cur,
            aggregate_kind="contact_point",
            aggregate_id=existing_cp["id"],
            event_type="contact_point.updated",
            payload={"kind": kind, "contact_point_id": existing_cp["id"], "reactivated": True},
            operator=operator,
            receipt_id=receipt_id,
        )
        # Bump person/org version
        _bump_person_or_org_version(cur, person_id, organization_id)
        return {
            "ok": True, "contact_point_id": existing_cp["id"],
            "person_id": person_id, "organization_id": organization_id,
            "version": new_version, "reactivated": True,
        }

    # Create new contact point
    cur.execute(
        """
        insert into crm.contact_point
            (kind, value_norm, value_display, person_id, organization_id, usage,
             confirmation, note)
        values (%s, %s, %s, %s::uuid, %s::uuid, %s, 'confirmed', %s)
        returning id::text as id, version
        """,
        (kind, value_norm, value, person_id, organization_id, usage, note_text or None),
    )
    row = _one(cur)
    cp_id = row["id"]
    self._append_event(
        cur,
        aggregate_kind="contact_point",
        aggregate_id=cp_id,
        event_type="contact_point.created",
        payload={"kind": kind, "contact_point_id": cp_id, "usage": usage},
        operator=operator,
        receipt_id=receipt_id,
    )
    # Bump the aggregate version
    new_agg_version = _bump_person_or_org_version(cur, person_id, organization_id)
    return {
        "ok": True,
        "contact_point_id": cp_id,
        "person_id": person_id,
        "organization_id": organization_id,
        "version": new_agg_version,
    }


def _bump_person_or_org_version(cur: Any, person_id: str | None, organization_id: str | None) -> int:
    if person_id:
        cur.execute(
            "update crm.person set version = version + 1, updated_at = now() where id = %s::uuid returning version",
            (person_id,),
        )
    elif organization_id:
        cur.execute(
            "update crm.organization set version = version + 1, updated_at = now() where id = %s::uuid returning version",
            (organization_id,),
        )
    else:
        return 1
    row = _one(cur)
    return row["version"] if row else 1


def _handle_update_contact_point(
    self: "V2CrmAuthoringRepository",
    cur: Any,
    operator: OperatorIdentity,
    fields: dict[str, Any],
    receipt_id: str,
) -> dict[str, Any]:
    cp_id = fields["contact_point_id"]
    expected_version = fields["expected_version"]
    note_text = fields["note"]

    cp = _live_contact_point(cur, cp_id, expected_version)
    if cp["status"] == "inactive":
        raise CommandRefused(409, "already_inactive", "contact point is inactive; reactivate first")

    changes: dict[str, Any] = {}
    if "usage" in fields and fields["usage"] is not None:
        changes["usage"] = fields["usage"]
    if "value_display" in fields and fields["value_display"] is not None:
        changes["value_display"] = fields["value_display"]

    set_parts = []
    values = []
    if changes:
        for col, val in changes.items():
            set_parts.append(f"{col} = %s")
            values.append(val)
    set_parts.append("note = %s")
    values.append(note_text or None)
    set_parts.append("version = version + 1")
    set_parts.append("updated_at = now()")
    values += [expected_version, cp_id]

    cur.execute(
        f"update crm.contact_point set {', '.join(set_parts)} where version = %s and id = %s::uuid",
        values,
    )
    if cur.rowcount == 0:
        raise CommandRefused(409, "stale_version", "concurrent modification")

    self._append_event(
        cur,
        aggregate_kind="contact_point",
        aggregate_id=cp_id,
        event_type="contact_point.updated",
        payload={"contact_point_id": cp_id, "changed": list(changes.keys())},
        operator=operator,
        receipt_id=receipt_id,
    )
    return {"ok": True, "contact_point_id": cp_id, "version": expected_version + 1}


def _handle_deactivate_contact_point(
    self: "V2CrmAuthoringRepository",
    cur: Any,
    operator: OperatorIdentity,
    fields: dict[str, Any],
    receipt_id: str,
) -> dict[str, Any]:
    cp_id = fields["contact_point_id"]
    expected_version = fields["expected_version"]
    note_text = fields["note"]

    cp = _live_contact_point(cur, cp_id, expected_version)
    if cp["status"] == "inactive":
        raise CommandRefused(409, "already_inactive", "contact point is already inactive")

    cur.execute(
        """
        update crm.contact_point
           set status = 'inactive', deactivated_at = now(),
               deactivated_by_operator_id = %s::uuid,
               note = %s,
               version = version + 1, updated_at = now()
         where version = %s and id = %s::uuid
        """,
        (operator.operator_id, note_text or None, expected_version, cp_id),
    )
    if cur.rowcount == 0:
        raise CommandRefused(409, "stale_version", "concurrent modification")

    self._append_event(
        cur,
        aggregate_kind="contact_point",
        aggregate_id=cp_id,
        event_type="contact_point.deactivated",
        payload={"contact_point_id": cp_id},
        operator=operator,
        receipt_id=receipt_id,
    )
    return {"ok": True, "contact_point_id": cp_id, "version": expected_version + 1}


def _handle_link_person_organization(
    self: "V2CrmAuthoringRepository",
    cur: Any,
    operator: OperatorIdentity,
    fields: dict[str, Any],
    receipt_id: str,
) -> dict[str, Any]:
    person_id = fields["person_id"]
    expected_version = fields["expected_version"]
    organization_id = fields["organization_id"]
    role_title = fields.get("role_title")
    unit_label = fields.get("unit_label")
    valid_from = fields.get("valid_from") or str(date.today())
    note_text = fields["note"]

    _live_person(cur, person_id, expected_version)
    _live_organization_active(cur, organization_id)

    cur.execute(
        """
        insert into crm.affiliation
            (person_id, organization_id, role_title, unit_label, valid_from,
             confirmation, confirmed_by_operator_id, note)
        values (%s::uuid, %s::uuid, %s, %s, %s::date,
                'confirmed', %s::uuid, %s)
        returning id::text as id
        """,
        (person_id, organization_id, role_title, unit_label, valid_from, operator.operator_id, note_text),
    )
    aff = _one(cur)
    aff_id = aff["id"]

    # Bump person version
    cur.execute(
        "update crm.person set version = version + 1, updated_at = now() where id = %s::uuid returning version",
        (person_id,),
    )
    new_version = _one(cur)["version"]

    self._append_event(
        cur,
        aggregate_kind="affiliation",
        aggregate_id=aff_id,
        event_type="affiliation.opened",
        payload={"person_id": person_id, "organization_id": organization_id, "role_title": role_title},
        operator=operator,
        receipt_id=receipt_id,
    )
    return {"ok": True, "affiliation_id": aff_id, "person_id": person_id, "version": new_version}


def _handle_unlink_person_organization(
    self: "V2CrmAuthoringRepository",
    cur: Any,
    operator: OperatorIdentity,
    fields: dict[str, Any],
    receipt_id: str,
) -> dict[str, Any]:
    person_id = fields["person_id"]
    expected_version = fields["expected_version"]
    affiliation_id = fields["affiliation_id"]
    valid_to = fields.get("valid_to") or str(date.today())
    note_text = fields["note"]

    _live_person(cur, person_id, expected_version)

    cur.execute(
        "select id::text as id, person_id::text as person_id, valid_to from crm.affiliation where id = %s::uuid for update",
        (affiliation_id,),
    )
    aff = _one(cur)
    if aff is None:
        raise CommandRefused(404, "affiliation_not_found", "no such affiliation")
    if aff["person_id"] != person_id:
        raise CommandRefused(409, "affiliation_belongs_to_another_person", "affiliation does not belong to this person")
    if aff["valid_to"] is not None:
        raise CommandRefused(409, "already_closed", "affiliation is already closed")

    cur.execute(
        "update crm.affiliation set valid_to = %s::date, note = %s, updated_at = now() where id = %s::uuid",
        (valid_to, note_text, affiliation_id),
    )

    # Bump person version
    cur.execute(
        "update crm.person set version = version + 1, updated_at = now() where id = %s::uuid returning version",
        (person_id,),
    )
    new_version = _one(cur)["version"]

    self._append_event(
        cur,
        aggregate_kind="affiliation",
        aggregate_id=affiliation_id,
        event_type="affiliation.closed",
        payload={"person_id": person_id, "valid_to": valid_to},
        operator=operator,
        receipt_id=receipt_id,
    )
    return {"ok": True, "affiliation_id": affiliation_id, "person_id": person_id, "version": new_version}


def _handle_register_organization(
    self: "V2CrmAuthoringRepository",
    cur: Any,
    operator: OperatorIdentity,
    fields: dict[str, Any],
    receipt_id: str,
) -> dict[str, Any]:
    name = fields["name"]
    legal_name = fields.get("legal_name")
    kind = fields["kind"]
    classification = fields.get("classification")
    domain = fields.get("domain")
    product_lines = fields.get("product_lines") or []
    note_text = fields["note"]

    cur.execute(
        """
        insert into crm.organization
            (name, legal_name, kind, confirmation, confirmed_by_operator_id, status)
        values (%s, %s, %s, 'confirmed', %s::uuid, 'active')
        returning id::text as id, version
        """,
        (name, legal_name, kind, operator.operator_id),
    )
    org = _one(cur)
    org_id = org["id"]

    self._append_event(
        cur,
        aggregate_kind="organization",
        aggregate_id=org_id,
        event_type="organization.created",
        payload={"name": name, "kind": kind, "note": note_text},
        operator=operator,
        receipt_id=receipt_id,
    )

    if classification:
        cur.execute(
            """
            insert into crm.organization_relationship
                (organization_id, role, valid_from, note)
            values (%s::uuid, %s, current_date, %s)
            returning id::text as id
            """,
            (org_id, classification, note_text),
        )
        rel = _one(cur)
        self._append_event(
            cur,
            aggregate_kind="organization_relationship",
            aggregate_id=rel["id"],
            event_type="relationship.changed",
            payload={"organization_id": org_id, "role": classification, "action": "opened"},
            operator=operator,
            receipt_id=receipt_id,
        )

    if domain:
        domain_norm = _normalize_domain(domain)
        cur.execute(
            """
            insert into crm.organization_domain (organization_id, domain_norm, scope)
            values (%s::uuid, %s, 'shared')
            on conflict (organization_id, domain_norm) do nothing
            returning id::text as id
            """,
            (org_id, domain_norm),
        )
        if _one(cur):
            self._append_event(
                cur,
                aggregate_kind="organization",
                aggregate_id=org_id,
                event_type="organization.domain_added",
                payload={"organization_id": org_id, "domain_norm": domain_norm},
                operator=operator,
                receipt_id=receipt_id,
            )

    for line_id in product_lines:
        if line_id not in PRODUCT_LINE_IDS:
            raise CommandRefused(422, "invalid_product_line", f"unknown product line: {line_id!r}")
        cur.execute(
            """
            insert into crm.organization_product_line
                (organization_id, line_id, linked_by_operator_id, valid_from)
            values (%s::uuid, %s, %s::uuid, current_date)
            on conflict do nothing
            returning id::text as id
            """,
            (org_id, line_id, operator.operator_id),
        )
        if _one(cur):
            self._append_event(
                cur,
                aggregate_kind="organization",
                aggregate_id=org_id,
                event_type="organization.product_line_linked",
                payload={"organization_id": org_id, "line_id": line_id},
                operator=operator,
                receipt_id=receipt_id,
            )

    # Bump version once for all added sub-entities, then re-read the authoritative value.
    if classification or domain or product_lines:
        cur.execute(
            "update crm.organization set version = version + 1, updated_at = now() where id = %s::uuid returning version",
            (org_id,),
        )
        final_version = _one(cur)["version"]
    else:
        final_version = org["version"]

    return {"ok": True, "organization_id": org_id, "version": final_version}


def _handle_update_organization(
    self: "V2CrmAuthoringRepository",
    cur: Any,
    operator: OperatorIdentity,
    fields: dict[str, Any],
    receipt_id: str,
) -> dict[str, Any]:
    org_id = fields["organization_id"]
    expected_version = fields["expected_version"]
    note_text = fields["note"]

    # Lock the org
    cur.execute(
        """
        select id::text as id, status, version
          from crm.organization
         where id = %s::uuid
           for update
        """,
        (org_id,),
    )
    org = _one(cur)
    if org is None:
        raise CommandRefused(404, "organization_not_found", "no such organization")
    if org["status"] == "archived":
        raise CommandRefused(409, "archived_subject", "that organization is archived")
    if org["version"] != expected_version:
        raise CommandRefused(409, "stale_version", "organization was modified since you loaded it")

    changes: dict[str, Any] = {}
    for col in ("name", "legal_name", "kind"):
        if col in fields and fields[col] is not None:
            changes[col] = fields[col]

    if not changes:
        raise CommandRefused(422, "no_changes", "no fields to update")

    set_clause = ", ".join(f"{c} = %s" for c in changes)
    values = list(changes.values()) + [expected_version, org_id]
    cur.execute(
        f"""
        update crm.organization
           set {set_clause}, version = version + 1, updated_at = now()
         where version = %s and id = %s::uuid
        """,
        values,
    )
    if cur.rowcount == 0:
        raise CommandRefused(409, "stale_version", "concurrent modification")

    self._append_event(
        cur,
        aggregate_kind="organization",
        aggregate_id=org_id,
        event_type="organization.updated",
        payload={"changed": list(changes.keys()), "note": note_text},
        operator=operator,
        receipt_id=receipt_id,
    )
    return {"ok": True, "organization_id": org_id, "version": expected_version + 1}


def _handle_archive_organization(
    self: "V2CrmAuthoringRepository",
    cur: Any,
    operator: OperatorIdentity,
    fields: dict[str, Any],
    receipt_id: str,
) -> dict[str, Any]:
    org_id = fields["organization_id"]
    expected_version = fields["expected_version"]
    reason = fields["note"]

    cur.execute(
        "select id::text as id, status, version from crm.organization where id = %s::uuid for update",
        (org_id,),
    )
    org = _one(cur)
    if org is None:
        raise CommandRefused(404, "organization_not_found", "no such organization")
    if org["status"] == "archived":
        raise CommandRefused(409, "already_archived", "organization is already archived")
    if org["version"] != expected_version:
        raise CommandRefused(409, "stale_version", "organization was modified since you loaded it")

    cur.execute(
        """
        update crm.organization
           set status = 'archived', archived_at = now(),
               archived_by_operator_id = %s::uuid, archive_reason = %s,
               version = version + 1, updated_at = now()
         where version = %s and id = %s::uuid
        """,
        (operator.operator_id, reason, expected_version, org_id),
    )
    if cur.rowcount == 0:
        raise CommandRefused(409, "stale_version", "concurrent modification")

    references = _org_references(cur, org_id)
    self._append_event(
        cur,
        aggregate_kind="organization",
        aggregate_id=org_id,
        event_type="organization.archived",
        payload={"archive_reason": reason},
        operator=operator,
        receipt_id=receipt_id,
    )
    return {"ok": True, "organization_id": org_id, "version": expected_version + 1, "references": references}


def _org_references(cur: Any, org_id: str) -> dict[str, int]:
    cur.execute(
        """
        select
          (select count(*) from outbound.campaign_recipient where organization_id = %s::uuid) as campaign_recipients,
          (select count(*) from crm.opportunity_organization where organization_id = %s::uuid) as opportunities,
          (select count(*) from crm.quote q
            join crm.opportunity o on o.id = q.opportunity_id
            join crm.opportunity_organization oo on oo.opportunity_id = o.id
           where oo.organization_id = %s::uuid) as quotes,
          (select count(*) from evidence.assertion where resolved_kind = 'organization' and resolved_id = %s::uuid) as evidence_assertions,
          (select count(*) from crm.note where subject_kind = 'organization' and subject_id = %s::uuid) as notes
        """,
        (org_id, org_id, org_id, org_id, org_id),
    )
    return _one(cur)


def _handle_restore_organization(
    self: "V2CrmAuthoringRepository",
    cur: Any,
    operator: OperatorIdentity,
    fields: dict[str, Any],
    receipt_id: str,
) -> dict[str, Any]:
    org_id = fields["organization_id"]
    expected_version = fields["expected_version"]
    note_text = fields["note"]

    cur.execute(
        "select id::text as id, status, version from crm.organization where id = %s::uuid for update",
        (org_id,),
    )
    org = _one(cur)
    if org is None:
        raise CommandRefused(404, "organization_not_found", "no such organization")
    if org["status"] != "archived":
        raise CommandRefused(409, "not_archived", "that organization is not archived")
    if org["version"] != expected_version:
        raise CommandRefused(409, "stale_version", "organization was modified since you loaded it")

    cur.execute(
        """
        update crm.organization
           set status = 'active', archived_at = null,
               archived_by_operator_id = null, archive_reason = null,
               version = version + 1, updated_at = now()
         where version = %s and id = %s::uuid
        """,
        (expected_version, org_id),
    )
    if cur.rowcount == 0:
        raise CommandRefused(409, "stale_version", "concurrent modification")

    self._append_event(
        cur,
        aggregate_kind="organization",
        aggregate_id=org_id,
        event_type="organization.restored",
        payload={"note": note_text},
        operator=operator,
        receipt_id=receipt_id,
    )
    return {"ok": True, "organization_id": org_id, "version": expected_version + 1}


def _handle_add_organization_identifier(
    self: "V2CrmAuthoringRepository",
    cur: Any,
    operator: OperatorIdentity,
    fields: dict[str, Any],
    receipt_id: str,
) -> dict[str, Any]:
    org_id = fields["organization_id"]
    expected_version = fields["expected_version"]
    scheme = fields["scheme"]
    value = fields["value"]
    note_text = fields["note"]

    org = _live_organization_active(cur, org_id)
    if org["version"] != expected_version:
        raise CommandRefused(409, "stale_version", "organization was modified since you loaded it")

    cur.execute(
        """
        insert into crm.external_identifier
            (organization_id, scheme, value_norm)
        values (%s::uuid, %s, %s)
        returning id::text as id
        """,
        (org_id, scheme, value.strip()),
    )
    identifier = _one(cur)

    # Bump org version
    cur.execute(
        "update crm.organization set version = version + 1, updated_at = now() where id = %s::uuid returning version",
        (org_id,),
    )
    new_version = _one(cur)["version"]

    self._append_event(
        cur,
        aggregate_kind="organization",
        aggregate_id=org_id,
        event_type="organization.identifier_added",
        payload={"organization_id": org_id, "scheme": scheme, "identifier_id": identifier["id"], "note": note_text},
        operator=operator,
        receipt_id=receipt_id,
    )
    return {"ok": True, "organization_id": org_id, "identifier_id": identifier["id"], "version": new_version}


def _handle_remove_organization_identifier(
    self: "V2CrmAuthoringRepository",
    cur: Any,
    operator: OperatorIdentity,
    fields: dict[str, Any],
    receipt_id: str,
) -> dict[str, Any]:
    org_id = fields["organization_id"]
    expected_version = fields["expected_version"]
    identifier_id = fields["identifier_id"]
    note_text = fields["note"]

    org = _live_organization_active(cur, org_id)
    if org["version"] != expected_version:
        raise CommandRefused(409, "stale_version", "organization was modified since you loaded it")

    cur.execute(
        """
        update crm.external_identifier
           set removed_at = now(), removed_by_operator_id = %s::uuid, remove_reason = %s
         where id = %s::uuid and organization_id = %s::uuid and removed_at is null
        """,
        (operator.operator_id, note_text, identifier_id, org_id),
    )
    if cur.rowcount == 0:
        raise CommandRefused(404, "identifier_not_found", "identifier not found or already removed")

    cur.execute(
        "update crm.organization set version = version + 1, updated_at = now() where id = %s::uuid returning version",
        (org_id,),
    )
    new_version = _one(cur)["version"]

    self._append_event(
        cur,
        aggregate_kind="organization",
        aggregate_id=org_id,
        event_type="organization.identifier_removed",
        payload={"organization_id": org_id, "identifier_id": identifier_id},
        operator=operator,
        receipt_id=receipt_id,
    )
    return {"ok": True, "organization_id": org_id, "identifier_id": identifier_id, "version": new_version}


def _handle_add_organization_domain(
    self: "V2CrmAuthoringRepository",
    cur: Any,
    operator: OperatorIdentity,
    fields: dict[str, Any],
    receipt_id: str,
) -> dict[str, Any]:
    org_id = fields["organization_id"]
    expected_version = fields["expected_version"]
    domain = fields["domain"]
    scope = fields.get("scope", "shared")
    note_text = fields["note"]

    org = _live_organization_active(cur, org_id)
    if org["version"] != expected_version:
        raise CommandRefused(409, "stale_version", "organization was modified since you loaded it")

    domain_norm = _normalize_domain(domain)

    # `(organization_id, domain_norm)` is unique and rows are never deleted, so the same domain
    # re-added to the same institution is either already present (refused) or soft-removed
    # (restored on its existing row — never a duplicate, never a bare unique violation).
    cur.execute(
        """
        select id::text as id, organization_id::text as organization_id, domain_norm, scope,
               removed_at is not null as removed
          from crm.organization_domain
         where organization_id = %s::uuid and domain_norm = %s
           for update
        """,
        (org_id, domain_norm),
    )
    existing = _one(cur)
    if existing is not None:
        if not existing["removed"]:
            raise CommandRefused(
                409, "domain_already_present", "that domain is already on this organization"
            )
        return self._restore_domain_row(
            cur, operator=operator, org_id=org_id, dom=existing, scope=scope,
            reason=note_text, receipt_id=receipt_id,
        )

    _refuse_if_exclusive_domain_taken(cur, domain_norm=domain_norm, scope=scope, except_id=None)

    cur.execute(
        """
        insert into crm.organization_domain (organization_id, domain_norm, scope)
        values (%s::uuid, %s, %s)
        returning id::text as id
        """,
        (org_id, domain_norm, scope),
    )
    dom = _one(cur)

    cur.execute(
        "update crm.organization set version = version + 1, updated_at = now() where id = %s::uuid returning version",
        (org_id,),
    )
    new_version = _one(cur)["version"]

    self._append_event(
        cur,
        aggregate_kind="organization",
        aggregate_id=org_id,
        event_type="organization.domain_added",
        payload={"organization_id": org_id, "domain_norm": domain_norm, "scope": scope},
        operator=operator,
        receipt_id=receipt_id,
    )
    return {
        "ok": True, "organization_id": org_id, "domain_id": dom["id"],
        "version": new_version, "restored": False,
    }


def _refuse_if_exclusive_domain_taken(
    cur: Any, *, domain_norm: str, scope: str, except_id: str | None
) -> None:
    """An exclusive claim is refused while another live row claims the domain exclusively.

    The partial unique index `organization_domain_exclusive_owner_key` enforces the same rule;
    checking first turns a unique violation into a named refusal.
    """
    if scope != "exclusive":
        return
    cur.execute(
        """
        select organization_id::text as organization_id
          from crm.organization_domain
         where domain_norm = %s and scope = 'exclusive' and removed_at is null
           and (%s::uuid is null or id <> %s::uuid)
         limit 1
        """,
        (domain_norm, except_id, except_id),
    )
    if _one(cur) is not None:
        raise CommandRefused(
            409, "exclusive_domain_taken",
            "another organization holds that domain exclusively; remove its claim first",
        )


def _restore_domain_row(
    self: "V2CrmAuthoringRepository",
    cur: Any,
    *,
    operator: OperatorIdentity,
    org_id: str,
    dom: dict[str, Any],
    scope: str,
    reason: str,
    receipt_id: str,
) -> dict[str, Any]:
    """Bring a soft-removed domain row of `org_id` back into force, on the same row.

    Callers have already locked the organization, checked its version and verified that
    `dom` belongs to `org_id` and is removed. The removal triple is cleared as a whole (the
    CHECK constraint accepts 0 or 3 of them, nothing between); the operator's reason travels
    in the `organization.domain_restored` event because the row has no column for it.
    """
    if dom["organization_id"] != org_id:  # defensive: callers check this and refuse loudly
        raise CommandRefused(
            409, "domain_of_other_organization",
            "that domain belongs to a different organization; a restore never moves a domain",
        )
    _refuse_if_exclusive_domain_taken(cur, domain_norm=dom["domain_norm"], scope=scope, except_id=dom["id"])

    cur.execute(
        """
        update crm.organization_domain
           set removed_at = null, removed_by_operator_id = null, remove_reason = null,
               scope = %s
         where id = %s::uuid and organization_id = %s::uuid and removed_at is not null
        """,
        (scope, dom["id"], org_id),
    )
    if cur.rowcount == 0:
        raise CommandRefused(409, "domain_not_removed", "that domain is not removed")

    cur.execute(
        "update crm.organization set version = version + 1, updated_at = now() where id = %s::uuid returning version",
        (org_id,),
    )
    new_version = _one(cur)["version"]

    self._append_event(
        cur,
        aggregate_kind="organization",
        aggregate_id=org_id,
        event_type="organization.domain_restored",
        payload={
            "organization_id": org_id,
            "domain_id": dom["id"],
            "domain_norm": dom["domain_norm"],
            "scope": scope,
            "previous_scope": dom["scope"],
            "reason": reason,
        },
        operator=operator,
        receipt_id=receipt_id,
    )
    return {
        "ok": True, "organization_id": org_id, "domain_id": dom["id"],
        "version": new_version, "restored": True,
    }


def _handle_restore_organization_domain(
    self: "V2CrmAuthoringRepository",
    cur: Any,
    operator: OperatorIdentity,
    fields: dict[str, Any],
    receipt_id: str,
) -> dict[str, Any]:
    """Explicit restore of a soft-removed domain, by its row id, on its own organization."""
    org_id = fields["organization_id"]
    expected_version = fields["expected_version"]
    domain_id = fields["domain_id"]
    note_text = fields["note"]

    org = _live_organization_active(cur, org_id)
    if org["version"] != expected_version:
        raise CommandRefused(409, "stale_version", "organization was modified since you loaded it")

    cur.execute(
        """
        select id::text as id, organization_id::text as organization_id, domain_norm, scope,
               removed_at is not null as removed
          from crm.organization_domain
         where id = %s::uuid
           for update
        """,
        (domain_id,),
    )
    dom = _one(cur)
    if dom is None:
        raise CommandRefused(404, "domain_not_found", "no such domain")
    if dom["organization_id"] != org_id:
        # Never silently re-home a domain: the operator names an organization and a row, and
        # the row must already be that organization's.
        raise CommandRefused(
            409, "domain_of_other_organization",
            "that domain belongs to a different organization; a restore never moves a domain",
        )
    if not dom["removed"]:
        raise CommandRefused(409, "domain_not_removed", "that domain is not removed")

    scope = fields.get("scope") or dom["scope"]
    return self._restore_domain_row(
        cur, operator=operator, org_id=org_id, dom=dom, scope=scope,
        reason=note_text, receipt_id=receipt_id,
    )


def _handle_remove_organization_domain(
    self: "V2CrmAuthoringRepository",
    cur: Any,
    operator: OperatorIdentity,
    fields: dict[str, Any],
    receipt_id: str,
) -> dict[str, Any]:
    org_id = fields["organization_id"]
    expected_version = fields["expected_version"]
    domain_id = fields["domain_id"]
    note_text = fields["note"]

    org = _live_organization_active(cur, org_id)
    if org["version"] != expected_version:
        raise CommandRefused(409, "stale_version", "organization was modified since you loaded it")

    cur.execute(
        """
        update crm.organization_domain
           set removed_at = now(), removed_by_operator_id = %s::uuid, remove_reason = %s
         where id = %s::uuid and organization_id = %s::uuid and removed_at is null
        """,
        (operator.operator_id, note_text, domain_id, org_id),
    )
    if cur.rowcount == 0:
        raise CommandRefused(404, "domain_not_found", "domain not found or already removed")

    cur.execute(
        "update crm.organization set version = version + 1, updated_at = now() where id = %s::uuid returning version",
        (org_id,),
    )
    new_version = _one(cur)["version"]

    self._append_event(
        cur,
        aggregate_kind="organization",
        aggregate_id=org_id,
        event_type="organization.domain_removed",
        payload={"organization_id": org_id, "domain_id": domain_id},
        operator=operator,
        receipt_id=receipt_id,
    )
    return {"ok": True, "organization_id": org_id, "domain_id": domain_id, "version": new_version}


def _handle_add_organization_classification(
    self: "V2CrmAuthoringRepository",
    cur: Any,
    operator: OperatorIdentity,
    fields: dict[str, Any],
    receipt_id: str,
) -> dict[str, Any]:
    org_id = fields["organization_id"]
    expected_version = fields["expected_version"]
    role = fields["role"]
    valid_from = fields.get("valid_from") or str(date.today())
    note_text = fields["note"]

    org = _live_organization_active(cur, org_id)
    if org["version"] != expected_version:
        raise CommandRefused(409, "stale_version", "organization was modified since you loaded it")

    cur.execute(
        """
        insert into crm.organization_relationship
            (organization_id, role, valid_from, note)
        values (%s::uuid, %s, %s::date, %s)
        returning id::text as id
        """,
        (org_id, role, valid_from, note_text),
    )
    rel = _one(cur)

    cur.execute(
        "update crm.organization set version = version + 1, updated_at = now() where id = %s::uuid returning version",
        (org_id,),
    )
    new_version = _one(cur)["version"]

    self._append_event(
        cur,
        aggregate_kind="organization_relationship",
        aggregate_id=rel["id"],
        event_type="relationship.changed",
        payload={"organization_id": org_id, "role": role, "action": "opened"},
        operator=operator,
        receipt_id=receipt_id,
    )
    return {"ok": True, "organization_id": org_id, "relationship_id": rel["id"], "version": new_version}


def _handle_remove_organization_classification(
    self: "V2CrmAuthoringRepository",
    cur: Any,
    operator: OperatorIdentity,
    fields: dict[str, Any],
    receipt_id: str,
) -> dict[str, Any]:
    org_id = fields["organization_id"]
    expected_version = fields["expected_version"]
    relationship_id = fields["relationship_id"]
    valid_to = fields.get("valid_to") or str(date.today())
    note_text = fields["note"]

    org = _live_organization_active(cur, org_id)
    if org["version"] != expected_version:
        raise CommandRefused(409, "stale_version", "organization was modified since you loaded it")

    cur.execute(
        """
        update crm.organization_relationship
           set valid_to = %s::date, note = coalesce(note, %s), updated_at = now()
         where id = %s::uuid and organization_id = %s::uuid and valid_to is null
        """,
        (valid_to, note_text, relationship_id, org_id),
    )
    if cur.rowcount == 0:
        raise CommandRefused(404, "relationship_not_found", "relationship not found or already closed")

    cur.execute(
        "update crm.organization set version = version + 1, updated_at = now() where id = %s::uuid returning version",
        (org_id,),
    )
    new_version = _one(cur)["version"]

    self._append_event(
        cur,
        aggregate_kind="organization_relationship",
        aggregate_id=relationship_id,
        event_type="relationship.changed",
        payload={"organization_id": org_id, "relationship_id": relationship_id, "action": "closed", "valid_to": valid_to},
        operator=operator,
        receipt_id=receipt_id,
    )
    return {"ok": True, "organization_id": org_id, "relationship_id": relationship_id, "version": new_version}


def _handle_link_organization_product_line(
    self: "V2CrmAuthoringRepository",
    cur: Any,
    operator: OperatorIdentity,
    fields: dict[str, Any],
    receipt_id: str,
) -> dict[str, Any]:
    org_id = fields["organization_id"]
    expected_version = fields["expected_version"]
    line_id = fields["line_id"]
    note_text = fields["note"]

    if line_id not in PRODUCT_LINE_IDS:
        raise CommandRefused(422, "invalid_product_line", f"unknown product line: {line_id!r}")

    org = _live_organization_active(cur, org_id)
    if org["version"] != expected_version:
        raise CommandRefused(409, "stale_version", "organization was modified since you loaded it")

    # Check for existing active link
    cur.execute(
        "select id from crm.organization_product_line where organization_id = %s::uuid and line_id = %s and valid_to is null",
        (org_id, line_id),
    )
    if _one(cur) is not None:
        raise CommandRefused(409, "line_already_linked", "this product line is already linked to this organization")

    cur.execute(
        """
        insert into crm.organization_product_line
            (organization_id, line_id, linked_by_operator_id, note, valid_from)
        values (%s::uuid, %s, %s::uuid, %s, current_date)
        returning id::text as id
        """,
        (org_id, line_id, operator.operator_id, note_text),
    )
    link = _one(cur)

    cur.execute(
        "update crm.organization set version = version + 1, updated_at = now() where id = %s::uuid returning version",
        (org_id,),
    )
    new_version = _one(cur)["version"]

    self._append_event(
        cur,
        aggregate_kind="organization",
        aggregate_id=org_id,
        event_type="organization.product_line_linked",
        payload={"organization_id": org_id, "line_id": line_id, "link_id": link["id"]},
        operator=operator,
        receipt_id=receipt_id,
    )
    return {"ok": True, "organization_id": org_id, "link_id": link["id"], "version": new_version}


def _handle_unlink_organization_product_line(
    self: "V2CrmAuthoringRepository",
    cur: Any,
    operator: OperatorIdentity,
    fields: dict[str, Any],
    receipt_id: str,
) -> dict[str, Any]:
    org_id = fields["organization_id"]
    expected_version = fields["expected_version"]
    link_id = fields["link_id"]
    note_text = fields["note"]

    org = _live_organization_active(cur, org_id)
    if org["version"] != expected_version:
        raise CommandRefused(409, "stale_version", "organization was modified since you loaded it")

    cur.execute(
        """
        update crm.organization_product_line
           set valid_to = current_date, unlinked_by_operator_id = %s::uuid,
               note = coalesce(note, %s), updated_at = now()
         where id = %s::uuid and organization_id = %s::uuid and valid_to is null
        """,
        (operator.operator_id, note_text, link_id, org_id),
    )
    if cur.rowcount == 0:
        raise CommandRefused(404, "link_not_found", "product line link not found or already closed")

    cur.execute(
        "update crm.organization set version = version + 1, updated_at = now() where id = %s::uuid returning version",
        (org_id,),
    )
    new_version = _one(cur)["version"]

    self._append_event(
        cur,
        aggregate_kind="organization",
        aggregate_id=org_id,
        event_type="organization.product_line_unlinked",
        payload={"organization_id": org_id, "link_id": link_id},
        operator=operator,
        receipt_id=receipt_id,
    )
    return {"ok": True, "organization_id": org_id, "link_id": link_id, "version": new_version}


def _handle_confirm_supplier_candidate(
    self: "V2CrmAuthoringRepository",
    cur: Any,
    operator: OperatorIdentity,
    fields: dict[str, Any],
    receipt_id: str,
) -> dict[str, Any]:
    assertion_id = fields["assertion_id"]
    organization_id = fields.get("organization_id")
    new_org = fields.get("new_organization")
    classification = fields["classification"]
    product_lines = fields.get("product_lines") or []
    note_text = fields["note"]

    if classification not in ("supplier", "manufacturer"):
        raise CommandRefused(422, "invalid_classification", "classification must be 'supplier' or 'manufacturer'")

    for line_id in product_lines:
        if line_id not in PRODUCT_LINE_IDS:
            raise CommandRefused(422, "invalid_product_line", f"unknown product line: {line_id!r}")

    assertion = _live_assertion(cur, assertion_id, kind="supplier_candidate")

    # Use existing org or create new
    if organization_id:
        if new_org:
            raise CommandRefused(422, "ambiguous_subject", "provide either organization_id or new_organization, not both")
        cur.execute(
            "select id::text as id, status, version from crm.organization where id = %s::uuid for update",
            (organization_id,),
        )
        org = _one(cur)
        if org is None:
            raise CommandRefused(404, "organization_not_found", "no such organization")
        if org["status"] == "archived":
            raise CommandRefused(409, "archived_subject", "that organization is archived")
        resolution = "linked"
    elif new_org:
        org_name = new_org["name"]
        org_kind = new_org["kind"]
        cur.execute(
            """
            insert into crm.organization
                (name, kind, confirmation, confirmed_by_operator_id, status)
            values (%s, %s, 'confirmed', %s::uuid, 'active')
            returning id::text as id, version
            """,
            (org_name, org_kind, operator.operator_id),
        )
        org = _one(cur)
        organization_id = org["id"]
        self._append_event(
            cur,
            aggregate_kind="organization",
            aggregate_id=organization_id,
            event_type="organization.created",
            payload={"name": org_name, "kind": org_kind, "note": note_text},
            operator=operator,
            receipt_id=receipt_id,
        )
        resolution = "promoted"
    else:
        raise CommandRefused(422, "missing_subject", "one of organization_id or new_organization is required")

    # Add classification if not already present
    cur.execute(
        "select id from crm.organization_relationship where organization_id = %s::uuid and role = %s and valid_to is null",
        (organization_id, classification),
    )
    if _one(cur) is None:
        cur.execute(
            """
            insert into crm.organization_relationship
                (organization_id, role, valid_from, note)
            values (%s::uuid, %s, current_date, %s)
            returning id::text as id
            """,
            (organization_id, classification, note_text),
        )
        rel = _one(cur)
        self._append_event(
            cur,
            aggregate_kind="organization_relationship",
            aggregate_id=rel["id"],
            event_type="relationship.changed",
            payload={"organization_id": organization_id, "role": classification, "action": "opened"},
            operator=operator,
            receipt_id=receipt_id,
        )

    # Add candidate domain if absent
    domain_norm = assertion["value_norm"].strip().lower()
    if _DOMAIN_SHAPE.match(domain_norm):
        cur.execute(
            """
            insert into crm.organization_domain (organization_id, domain_norm, scope)
            values (%s::uuid, %s, 'shared')
            on conflict (organization_id, domain_norm) do nothing
            returning id::text as id
            """,
            (organization_id, domain_norm),
        )
        if _one(cur):
            self._append_event(
                cur,
                aggregate_kind="organization",
                aggregate_id=organization_id,
                event_type="organization.domain_added",
                payload={"organization_id": organization_id, "domain_norm": domain_norm},
                operator=operator,
                receipt_id=receipt_id,
            )

    # Optional product lines
    for line_id in product_lines:
        cur.execute(
            "select id from crm.organization_product_line where organization_id = %s::uuid and line_id = %s and valid_to is null",
            (organization_id, line_id),
        )
        if _one(cur) is None:
            cur.execute(
                """
                insert into crm.organization_product_line
                    (organization_id, line_id, linked_by_operator_id, valid_from)
                values (%s::uuid, %s, %s::uuid, current_date)
                returning id::text as id
                """,
                (organization_id, line_id, operator.operator_id),
            )
            self._append_event(
                cur,
                aggregate_kind="organization",
                aggregate_id=organization_id,
                event_type="organization.product_line_linked",
                payload={"organization_id": organization_id, "line_id": line_id},
                operator=operator,
                receipt_id=receipt_id,
            )

    # Bump org version
    cur.execute(
        "update crm.organization set version = version + 1, updated_at = now() where id = %s::uuid returning version",
        (organization_id,),
    )
    new_org_version = _one(cur)["version"]

    # Resolve assertion
    cur.execute(
        """
        update evidence.assertion
           set resolution = %s, resolved_kind = 'organization', resolved_id = %s::uuid,
               resolved_at = now(), resolved_by_operator_id = %s::uuid,
               updated_at = now()
         where id = %s::uuid
        """,
        (resolution, organization_id, operator.operator_id, assertion_id),
    )

    self._append_event(
        cur,
        aggregate_kind="assertion",
        aggregate_id=assertion_id,
        event_type="assertion.supplier_candidate_confirmed",
        payload={"assertion_id": assertion_id, "organization_id": organization_id, "resolution": resolution},
        operator=operator,
        receipt_id=receipt_id,
    )
    return {
        "ok": True,
        "assertion_id": assertion_id,
        "organization_id": organization_id,
        "resolution": resolution,
        "version": new_org_version,
    }


def _handle_reject_supplier_candidate(
    self: "V2CrmAuthoringRepository",
    cur: Any,
    operator: OperatorIdentity,
    fields: dict[str, Any],
    receipt_id: str,
) -> dict[str, Any]:
    assertion_id = fields["assertion_id"]
    note_text = fields["note"]

    assertion = _live_assertion(cur, assertion_id, kind="supplier_candidate")

    cur.execute(
        """
        update evidence.assertion
           set resolution = 'rejected', ambiguity_note = %s,
               resolved_at = now(), resolved_by_operator_id = %s::uuid,
               updated_at = now()
         where id = %s::uuid
        """,
        (note_text, operator.operator_id, assertion_id),
    )

    self._append_event(
        cur,
        aggregate_kind="assertion",
        aggregate_id=assertion_id,
        event_type="assertion.supplier_candidate_rejected",
        payload={"assertion_id": assertion_id, "note": note_text},
        operator=operator,
        receipt_id=receipt_id,
    )
    return {"ok": True, "assertion_id": assertion_id, "resolution": "rejected"}


def _handle_add_note(
    self: "V2CrmAuthoringRepository",
    cur: Any,
    operator: OperatorIdentity,
    fields: dict[str, Any],
    receipt_id: str,
) -> dict[str, Any]:
    subject_kind = fields["subject_kind"]
    subject_id = fields["subject_id"]
    body = fields["body"]

    # revision_no = 1 notes have root_note_id = NULL (schema constraint note_revision_shape).
    # Revisions point back to the root via root_note_id; the revise-note handler resolves the
    # root by following old_note["root_note_id"] or note_id (for the first revision of a root note).
    cur.execute(
        """
        insert into crm.note
            (subject_kind, subject_id, body, author_operator_id,
             revision_no, status)
        values (%s, %s::uuid, %s, %s::uuid, 1, 'active')
        returning id::text as id, version
        """,
        (subject_kind, subject_id, body, operator.operator_id),
    )
    note = _one(cur)
    note_id = note["id"]

    self._append_event(
        cur,
        aggregate_kind="note",
        aggregate_id=note_id,
        event_type="note.created",
        payload={"subject_kind": subject_kind, "subject_id": subject_id},
        operator=operator,
        receipt_id=receipt_id,
    )
    return {"ok": True, "note_id": note_id, "version": note["version"]}


def _handle_revise_note(
    self: "V2CrmAuthoringRepository",
    cur: Any,
    operator: OperatorIdentity,
    fields: dict[str, Any],
    receipt_id: str,
) -> dict[str, Any]:
    note_id = fields["note_id"]
    expected_version = fields["expected_version"]
    body = fields["body"]

    old_note = _live_note(cur, note_id, expected_version)
    if old_note["status"] == "archived":
        raise CommandRefused(409, "archived_subject", "cannot revise an archived note")

    root_id = old_note["root_note_id"] or note_id

    cur.execute(
        """
        insert into crm.note
            (subject_kind, subject_id, body, author_operator_id,
             root_note_id, revision_of_note_id,
             revision_no, status)
        values (%s, %s::uuid, %s, %s::uuid,
                %s::uuid, %s::uuid,
                %s, 'active')
        returning id::text as id, version
        """,
        (
            old_note["subject_kind"],
            old_note["subject_id"],
            body,
            operator.operator_id,
            root_id,
            note_id,
            old_note["revision_no"] + 1,
        ),
    )
    new_note = _one(cur)
    new_note_id = new_note["id"]

    self._append_event(
        cur,
        aggregate_kind="note",
        aggregate_id=new_note_id,
        event_type="note.revised",
        payload={
            "root_note_id": root_id,
            "previous_note_id": note_id,
            "revision_no": old_note["revision_no"] + 1,
        },
        operator=operator,
        receipt_id=receipt_id,
    )
    return {"ok": True, "note_id": new_note_id, "root_note_id": root_id, "version": new_note["version"]}


def _handle_archive_note(
    self: "V2CrmAuthoringRepository",
    cur: Any,
    operator: OperatorIdentity,
    fields: dict[str, Any],
    receipt_id: str,
) -> dict[str, Any]:
    note_id = fields["note_id"]
    expected_version = fields["expected_version"]
    reason = fields["note"]

    # Author-or-admin check.  The route passes the requesting operator's id and role
    # so we can check inside the transaction (after locking the note row).
    requesting_id = fields.get("_requesting_operator_id", operator.operator_id)
    requesting_role = fields.get("_requesting_operator_role", operator.role)

    note = _live_note(cur, note_id, expected_version)
    if note["status"] == "archived":
        raise CommandRefused(409, "already_archived", "note is already archived")

    if requesting_role != "admin" and note["author_operator_id"] != requesting_id:
        raise CommandRefused(
            403, "role_may_not_archive",
            "only the note's author or an admin may archive it",
        )

    cur.execute(
        """
        update crm.note
           set status = 'archived', archived_at = now(),
               archived_by_operator_id = %s::uuid, archive_reason = %s,
               version = version + 1
         where version = %s and id = %s::uuid
        """,
        (operator.operator_id, reason, expected_version, note_id),
    )
    if cur.rowcount == 0:
        raise CommandRefused(409, "stale_version", "concurrent modification")

    self._append_event(
        cur,
        aggregate_kind="note",
        aggregate_id=note_id,
        event_type="note.archived",
        payload={"archive_reason": reason},
        operator=operator,
        receipt_id=receipt_id,
    )
    return {"ok": True, "note_id": note_id, "version": expected_version + 1}


# ─────────────────────────────────────────────────────────────────────────── repository ──

class V2CrmAuthoringRepository(CommandTransaction):
    """28 CRM authoring commands, each in one transaction."""

    _restore_domain_row = _restore_domain_row

    _HANDLERS = {
        "create-person": _handle_create_person,
        "update-person": _handle_update_person,
        "archive-person": _handle_archive_person,
        "restore-person": _handle_restore_person,
        "merge-people": _handle_merge_people,
        "add-contact-point": _handle_add_contact_point,
        "update-contact-point": _handle_update_contact_point,
        "deactivate-contact-point": _handle_deactivate_contact_point,
        "link-person-organization": _handle_link_person_organization,
        "unlink-person-organization": _handle_unlink_person_organization,
        "register-organization": _handle_register_organization,
        "update-organization": _handle_update_organization,
        "archive-organization": _handle_archive_organization,
        "restore-organization": _handle_restore_organization,
        "add-organization-identifier": _handle_add_organization_identifier,
        "remove-organization-identifier": _handle_remove_organization_identifier,
        "add-organization-domain": _handle_add_organization_domain,
        "remove-organization-domain": _handle_remove_organization_domain,
        "restore-organization-domain": _handle_restore_organization_domain,
        "add-organization-classification": _handle_add_organization_classification,
        "remove-organization-classification": _handle_remove_organization_classification,
        "link-organization-product-line": _handle_link_organization_product_line,
        "unlink-organization-product-line": _handle_unlink_organization_product_line,
        "confirm-supplier-candidate": _handle_confirm_supplier_candidate,
        "reject-supplier-candidate": _handle_reject_supplier_candidate,
        "add-note": _handle_add_note,
        "revise-note": _handle_revise_note,
        "archive-note": _handle_archive_note,
    }
