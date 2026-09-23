"""The transactional half of the commercial-case command boundary.

`command_repository.py` settles what a staged message meant. This module runs the case that
follows, and the two share `CommandTransaction` rather than a copy of it: one transaction per
command, one receipt per `(operator, Idempotency-Key)`, one `crm.domain_event` per change,
and a refusal that rolls back all three.

Five rules hold here, each structural rather than remembered:

1. **One transaction per command.** Opening a case writes the case *and* its origin evidence
   link; making an institution the requesting one writes the case row *and* the role row.
   There is no half-open case and no institution that is the customer on one side of the pair
   and not on the other — a refusal anywhere undoes everything, the receipt included.
2. **Every change is compare-and-set.** A case moves only `where version = %s`; a role row is
   closed only `where valid_to is null`; the case's `version` advances on every write to it,
   and a trigger refuses an update that does not advance it. Two operators on one case do not
   both win.
3. **Nothing is rewritten.** `role` is not in `origenlab_api`'s UPDATE grant on
   `crm.opportunity_organization`, so changing what an institution is to a case closes one
   row and opens another; both readings stay visible forever. The supplier-exception triple
   is refused *any* later change by its own trigger.
4. **Nothing is inferred.** No query here scores a name, consults a brand list or picks a
   candidate. Every subject is a UUID an operator sent. The one lookup that could look like
   inference — does this institution hold a supplier relationship? — does not decide anything;
   it decides whether the operator must justify themselves.
5. **No machine writes.** Every row inserted here is `confirmation = 'confirmed'` with the
   deciding operator named, and `CommandTransaction._append_event` has no way to write an
   `actor_kind` other than `'operator'`. `machine_proposed` is a state this module can read
   and confirm, never one it can create.

**What it never touches.** `outbound.*` appears nowhere in this file — no campaign, no
recipient, no contact control, no send, no consent. Neither does `crm.person`,
`crm.organization_relationship`, `crm.quote`, `crm.task` or `crm.activity`. A
database-backed test counts all of them before and after every command and asserts they did
not move.

The role is `origenlab_api`, with no membership in `origenlab_owner`, so RLS and the per-verb
grants constrain these writes exactly as they will in production.
"""

from __future__ import annotations

from typing import Any

from origenlab_api.v2.case_commands import (
    ADD_CASE_ORGANIZATION,
    ADVANCE_CASE_STAGE,
    LINK_CASE_EVIDENCE,
    OPEN_COMMERCIAL_CASE,
    OPENING_STAGE,
    RECORD_CASE_INTEREST,
    REQUESTING_INSTITUTION,
    SET_CASE_ORGANIZATION_ROLE,
    STAGES_REQUIRING_A_REQUESTING_INSTITUTION,
    SUPPLIER_RELATIONSHIP_ROLES,
    TERMINAL_STAGES,
    stage_transition_allowed,
)
from origenlab_api.v2.command_core import CommandTransaction
from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.identity import OperatorIdentity

#: The evidence-link subject columns, paired with the table each one points at and the name
#: the refusal uses. Data rather than five branches, so a subject cannot be added to the
#: request shape and forgotten here.
EVIDENCE_SUBJECTS: tuple[tuple[str, str, str], ...] = (
    ("source_record_id", "evidence.source_record", "evidence record"),
    ("assertion_id", "evidence.assertion", "assertion"),
    ("message_id", "comms.message", "message"),
    ("notice_id", "procurement.notice", "procurement notice"),
)


class V2CaseCommandRepository(CommandTransaction):
    """The six commercial-case commands, each in one transaction."""

    # ------------------------------------------------------------------ shared reads

    def _live_case(
        self,
        cur: Any,
        opportunity_id: str,
        expected_version: int,
        *,
        allow_closed: bool = False,
    ) -> dict[str, Any]:
        """The case, locked, at the version the operator was shown.

        `allow_closed` exists for exactly one caller. `advance_case_stage` must be able to
        *read* a closed case in order to say "that stage is terminal and is never revived";
        every other command refuses a closed case outright, because adding an institution,
        an interest or an evidence link to a case that has ended is a decision about a
        conversation nobody is having.
        """
        cur.execute(
            """
            select id::text as id, title, stage, version,
                   organization_id::text as organization_id,
                   owner_operator_id::text as owner_operator_id,
                   closed_at, close_reason
              from crm.opportunity
             where id = %s
               for update
            """,
            (opportunity_id,),
        )
        case = self._row(cur)
        if case is None:
            raise CommandRefused(404, "case_not_found", "no such commercial case")
        if int(case["version"]) != int(expected_version):
            raise CommandRefused(
                409,
                "case_version_conflict",
                f"this case changed since it was shown to you (you saw version "
                f"{expected_version}, it is at {case['version']}); re-read it and decide again",
            )
        if not allow_closed and case["closed_at"] is not None:
            raise CommandRefused(
                409,
                "case_is_closed",
                f"this case ended at stage '{case['stage']}'; a closed case is not edited, "
                "and reopening one is a new case that references it",
            )
        return case

    def _current_requesting_institution(
        self, cur: Any, opportunity_id: str
    ) -> dict[str, Any] | None:
        """The one current `requesting_institution` row, if a human has named one."""
        cur.execute(
            """
            select id::text as id, organization_id::text as organization_id,
                   confirmation, confirmed_by_operator_id::text as confirmed_by_operator_id
              from crm.opportunity_organization
             where opportunity_id = %s
               and role = %s
               and valid_to is null
            """,
            (opportunity_id, REQUESTING_INSTITUTION),
        )
        return self._row(cur)

    def _is_one_of_our_suppliers(self, cur: Any, organization_id: str) -> bool:
        """Does this institution hold a current supplier or manufacturer relationship with us?

        This is a **lookup**, not an inference: it reads rows an operator opened on
        `crm.organization_relationship`, and it decides nothing about the case. All it
        decides is whether making this institution the one *asking* costs the operator a
        written justification (`DOMAIN.md` §3.6.1).
        """
        cur.execute(
            """
            select 1
              from crm.organization_relationship
             where organization_id = %s
               and role = any (%s)
               and (valid_to is null or valid_to > current_date)
             limit 1
            """,
            (organization_id, list(SUPPLIER_RELATIONSHIP_ROLES)),
        )
        return self._row(cur) is not None

    # ------------------------------------------------- the durable moves, reusable
    #
    # `add_case_organization` and `set_case_organization_role` both end in "this institution
    # now holds this part on this case", and for `requesting_institution` that sentence is
    # two writes that must not be separable. The move therefore lives here once, so the two
    # commands cannot drift apart: there is exactly one implementation of "make this
    # institution the one asking", and the difference between the commands is which steps
    # they run, never how.

    def _open_case_organization_row(
        self,
        cur: Any,
        *,
        case: dict[str, Any],
        organization_id: str,
        role: str,
        supplier_exception_reason: str | None,
        operator: OperatorIdentity,
        receipt_id: str,
        note: str,
    ) -> tuple[str, list[str], int]:
        """Open one confirmed role row, and the case row too when the role is the requester.

        Returns the new row's id, the events it produced, and the case's version afterwards
        — which the caller hands back to the operator, because it is the compare-and-set of
        their next decision.

        The order is the safe one and the constraint is indifferent to it: the agreement
        between `crm.opportunity.organization_id` and the current confirmed requesting
        institution is a DEFERRABLE INITIALLY DEFERRED constraint trigger, so both writes land
        before either is judged. `set constraints all immediate` then forces that judgement
        *here*, inside the handler, rather than at a `COMMIT` where a failure would surface
        as a driver error instead of a refusal with a name.
        """
        events: list[str] = []
        case_version = int(case["version"])
        is_requesting = role == REQUESTING_INSTITUTION

        # Order matters, and it is the order the operator's mistakes come in. "This case
        # already names who is asking" is about the case and answers first; asking someone to
        # justify a supplier exception they could not have used anyway would send them to
        # write a sentence that changes nothing.
        if is_requesting and self._current_requesting_institution(cur, case["id"]) is not None:
            raise CommandRefused(
                409,
                "case_already_has_a_requesting_institution",
                "this case already names who is asking. Change that institution's part "
                "first — the old reading is closed and stays readable — and then name "
                "the new requester",
            )

        needs_exception = is_requesting and self._is_one_of_our_suppliers(cur, organization_id)
        if needs_exception and supplier_exception_reason is None:
            raise CommandRefused(
                422,
                "supplier_exception_required",
                "OrigenLab buys from this institution — it holds a current supplier or "
                "manufacturer relationship — so making it the one asking on this case is an "
                "exception. Say in supplier_exception_reason why it is the requester here. "
                "The justification is written once, with your name and the moment, and is "
                "never rewritten; it changes nothing outside this case",
            )
        if supplier_exception_reason is not None and not needs_exception:
            raise CommandRefused(
                422,
                "supplier_exception_is_not_needed",
                "this institution holds no current supplier or manufacturer relationship "
                "with OrigenLab, so there is no refusal to override. Recording an exception "
                "nobody needed would read later as a supplier we sold to",
            )

        cur.execute(
            """
            insert into crm.opportunity_organization
                (opportunity_id, organization_id, role, valid_from, confirmation,
                 confirmed_by_operator_id, origin_source_record_id, note,
                 supplier_exception_by_operator_id, supplier_exception_reason,
                 supplier_exception_at)
            values (%(opportunity_id)s, %(organization_id)s, %(role)s, current_date,
                    'confirmed', %(operator_id)s, null, %(note)s,
                    %(exception_operator_id)s, %(exception_reason)s,
                    case when %(exception_reason)s::text is null then null else now() end)
            returning id::text as id
            """,
            {
                "opportunity_id": case["id"],
                "organization_id": organization_id,
                "role": role,
                "operator_id": operator.operator_id,
                "note": note,
                # The triple is all-or-none, and it is written in the one statement that
                # creates the row. `supplier_exception_at` is the database's clock, not this
                # process's, and its own trigger refuses every later change to all three —
                # so there is no second statement in which any of it could be adjusted.
                "exception_operator_id": operator.operator_id if needs_exception else None,
                "exception_reason": supplier_exception_reason if needs_exception else None,
            },
        )
        row = self._row(cur)
        assert row is not None  # noqa: S101 - `returning` on a successful insert
        case_organization_id = row["id"]

        events.append(
            self._append_event(
                cur,
                aggregate_kind="opportunity_organization",
                aggregate_id=case_organization_id,
                event_type="case_organization.added",
                payload={
                    "opportunity_id": case["id"],
                    "organization_id": organization_id,
                    "role": role,
                    "confirmation": "confirmed",
                    "supplier_exception_reason": (
                        supplier_exception_reason if needs_exception else None
                    ),
                    "note": note,
                },
                operator=operator,
                receipt_id=receipt_id,
            )
        )

        if is_requesting:
            case_version = self._set_case_organization_id(
                cur,
                case_id=case["id"],
                version=case_version,
                organization_id=organization_id,
            )
            events.append(
                self._append_event(
                    cur,
                    aggregate_kind="opportunity",
                    aggregate_id=case["id"],
                    event_type="opportunity.organization_set",
                    payload={
                        "organization_id": organization_id,
                        "opportunity_organization_id": case_organization_id,
                        "supplier_exception": needs_exception,
                        "note": note,
                    },
                    operator=operator,
                    receipt_id=receipt_id,
                )
            )
            cur.execute("set constraints all immediate")

        return case_organization_id, events, case_version

    def _set_case_organization_id(
        self, cur: Any, *, case_id: str, version: int, organization_id: str | None
    ) -> int:
        """Point `crm.opportunity.organization_id` at the requesting institution, or clear it.

        §3.6.1 defines this column as *the confirmed requesting institution*, so it is never
        written on its own: every caller has just opened or just closed the matching role row
        in this same transaction.
        """
        cur.execute(
            """
            update crm.opportunity
               set organization_id = %s, version = version + 1, updated_at = now()
             where id = %s and version = %s
            """,
            (organization_id, case_id, int(version)),
        )
        if cur.rowcount != 1:
            raise CommandRefused(
                409,
                "case_version_conflict",
                "this case changed while the command ran; re-read it and decide again",
            )
        return int(version) + 1

    def _link_evidence_row(
        self,
        cur: Any,
        *,
        case_id: str,
        subject_column: str,
        subject_id: str,
        relation: str,
        operator: OperatorIdentity,
        receipt_id: str,
        note: str,
    ) -> tuple[str, str]:
        """One evidence link, refused if this reading of this document is already recorded.

        The duplicate check is a select and the partial unique index is the real guarantee —
        the select exists so the answer is `evidence_already_linked` with a sentence rather
        than a unique-violation the operator has to interpret. Under a race the index wins and
        one of the two transactions fails; neither leaves a duplicate.
        """
        cur.execute(
            f"""
            select id::text as id from crm.opportunity_evidence
             where opportunity_id = %s and {subject_column} = %s and relation = %s
               and unlinked_at is null
            """,  # noqa: S608 - subject_column is one of four module constants, never input
            (case_id, subject_id, relation),
        )
        if self._row(cur) is not None:
            raise CommandRefused(
                409,
                "evidence_already_linked",
                f"this case already reads that document as '{relation}'; a second identical "
                "link would say nothing new",
            )

        cur.execute(
            f"""
            insert into crm.opportunity_evidence
                (opportunity_id, {subject_column}, relation, linked_by_operator_id, note)
            values (%s, %s, %s, %s, %s)
            returning id::text as id
            """,  # noqa: S608 - as above
            (case_id, subject_id, relation, operator.operator_id, note),
        )
        row = self._row(cur)
        assert row is not None  # noqa: S101 - `returning` on a successful insert

        event_id = self._append_event(
            cur,
            aggregate_kind="opportunity_evidence",
            aggregate_id=row["id"],
            event_type="case_evidence.linked",
            payload={
                "opportunity_id": case_id,
                "subject_kind": subject_column.removesuffix("_id"),
                "subject_id": subject_id,
                "relation": relation,
                "note": note,
            },
            operator=operator,
            receipt_id=receipt_id,
        )
        return row["id"], event_id

    # ------------------------------------------------------------------ the commands

    def _open_commercial_case(
        self, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
    ) -> dict[str, Any]:
        """A case, and the document that is the reason it exists. One or neither.

        The case opens at `lead` with no institution, which is the truthful state of an
        inbound enquiry from a name nobody has recorded (§3.6.5). What it is never opened
        without is a reason: the origin record becomes `crm.opportunity.origin_source_record_id`
        *and* a `crm.opportunity_evidence` row with relation `origin`, in this transaction.
        """
        cur.execute(
            """
            select id::text as id, kind, review_status, is_quarantined
              from evidence.source_record
             where id = %s
               for share
            """,
            (fields["origin_source_record_id"],),
        )
        record = self._row(cur)
        if record is None:
            raise CommandRefused(
                404,
                "source_record_not_found",
                "no such evidence record; a case is opened from a document that exists",
            )
        if record["is_quarantined"]:
            raise CommandRefused(
                409,
                "source_record_quarantined",
                "this record is quarantined because its evidence contradicts itself; a case "
                "opened on it would inherit the contradiction as its reason to exist",
            )

        cur.execute(
            """
            insert into crm.opportunity
                (title, stage, owner_operator_id, origin_source_record_id)
            values (%s, %s, %s, %s)
            returning id::text as id, version
            """,
            (fields["title"], OPENING_STAGE, operator.operator_id, record["id"]),
        )
        case = self._row(cur)
        assert case is not None  # noqa: S101 - `returning` on a successful insert

        events = [
            self._append_event(
                cur,
                aggregate_kind="opportunity",
                aggregate_id=case["id"],
                event_type="opportunity.created",
                payload={
                    "title": fields["title"],
                    "stage": OPENING_STAGE,
                    "owner_operator_id": operator.operator_id,
                    "origin_source_record_id": record["id"],
                    "source_kind": record["kind"],
                    "note": fields["note"],
                },
                operator=operator,
                receipt_id=receipt_id,
            )
        ]
        evidence_id, event_id = self._link_evidence_row(
            cur,
            case_id=case["id"],
            subject_column="source_record_id",
            subject_id=record["id"],
            relation="origin",
            operator=operator,
            receipt_id=receipt_id,
            note=fields["note"],
        )
        events.append(event_id)

        return {
            "command": OPEN_COMMERCIAL_CASE,
            "opportunity_id": case["id"],
            "opportunity_version": int(case["version"]),
            "stage": OPENING_STAGE,
            "organization_id": None,
            "origin_source_record_id": record["id"],
            "opportunity_evidence_id": evidence_id,
            "created": ["opportunity", "opportunity_evidence"],
            "event_ids": events,
        }

    def _link_case_evidence(
        self, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
    ) -> dict[str, Any]:
        """Attach a document that already exists. This command creates no evidence.

        The subject is checked for existence before the insert so that a wrong id is a 404
        naming what was looked for, rather than a foreign-key violation. The case's own row
        does not move, so its `version` does not advance — the version in the request is the
        operator's statement that this is the case they were looking at, and it is checked.
        """
        case = self._live_case(cur, fields["opportunity_id"], fields["opportunity_version"])

        named = [(column, fields[column]) for column, _, _ in EVIDENCE_SUBJECTS
                 if fields.get(column) is not None]
        assert len(named) == 1  # noqa: S101 - the request shape allows exactly one
        subject_column, subject_id = named[0]
        table, human = next(
            (t, h) for c, t, h in EVIDENCE_SUBJECTS if c == subject_column
        )

        cur.execute(
            f"select id::text as id from {table} where id = %s",  # noqa: S608 - module constant
            (subject_id,),
        )
        if self._row(cur) is None:
            raise CommandRefused(
                404,
                "evidence_subject_not_found",
                f"no such {human}; this command links evidence that already exists and "
                "creates none",
            )

        evidence_id, event_id = self._link_evidence_row(
            cur,
            case_id=case["id"],
            subject_column=subject_column,
            subject_id=subject_id,
            relation=fields["relation"],
            operator=operator,
            receipt_id=receipt_id,
            note=fields["note"],
        )
        return {
            "command": LINK_CASE_EVIDENCE,
            "opportunity_id": case["id"],
            "opportunity_version": int(case["version"]),
            "opportunity_evidence_id": evidence_id,
            "relation": fields["relation"],
            "subject_kind": subject_column.removesuffix("_id"),
            "subject_id": subject_id,
            "stage": case["stage"],
            "created": ["opportunity_evidence"],
            "event_ids": [event_id],
        }

    def _add_case_organization(
        self, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
    ) -> dict[str, Any]:
        """Put an institution on the case, in the part the operator named.

        For every part but `requesting_institution` this is one row and one event. For that
        one it is also `crm.opportunity.organization_id`, because §3.6.1 defines the column as
        that institution — and both move in this transaction or neither does.
        """
        case = self._live_case(cur, fields["opportunity_id"], fields["opportunity_version"])
        organization = self._live_organization(cur, fields["organization_id"])
        if int(organization["version"]) != int(fields["organization_version"]):
            raise CommandRefused(
                409,
                "organization_version_conflict",
                "that institution changed since it was shown to you; re-read it and decide again",
            )

        cur.execute(
            """
            select id::text as id from crm.opportunity_organization
             where opportunity_id = %s and organization_id = %s and role = %s
               and valid_to is null
            """,
            (case["id"], organization["id"], fields["role"]),
        )
        if self._row(cur) is not None:
            raise CommandRefused(
                409,
                "case_organization_role_already_current",
                f"this institution already holds the part '{fields['role']}' on this case. "
                "An institution may hold several parts at once, but not the same one twice",
            )

        case_organization_id, events, case_version = self._open_case_organization_row(
            cur,
            case=case,
            organization_id=organization["id"],
            role=fields["role"],
            supplier_exception_reason=fields["supplier_exception_reason"],
            operator=operator,
            receipt_id=receipt_id,
            note=fields["note"],
        )
        return {
            "command": ADD_CASE_ORGANIZATION,
            "opportunity_id": case["id"],
            "opportunity_version": case_version,
            "opportunity_organization_id": case_organization_id,
            "organization_id": organization["id"],
            "organization_version": int(organization["version"]),
            "role": fields["role"],
            "stage": case["stage"],
            "supplier_exception": fields["supplier_exception_reason"] is not None,
            "created": ["opportunity_organization"],
            "event_ids": events,
        }

    def _set_case_organization_role(
        self, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
    ) -> dict[str, Any]:
        """Vouch for a reading, or replace it with a different one. Never edit one.

        Two shapes, and the request does not choose between them — the row does:

        * the named row is `machine_proposed` and the operator names the **same** part: the
          reading did not change, only who vouches for it, so `confirmation` moves in place.
        * the operator names a **different** part: the current row is closed and a new one is
          opened, in this transaction. `valid_to` may equal `valid_from`, which is how a
          reading opened and withdrawn on the same day stays visible instead of vanishing.

        Confirming an already-`confirmed` row to the part it already holds is refused rather
        than treated as success: it would write an event saying a decision was taken when
        none was.
        """
        case = self._live_case(cur, fields["opportunity_id"], fields["opportunity_version"])

        cur.execute(
            """
            select id::text as id, opportunity_id::text as opportunity_id,
                   organization_id::text as organization_id, role, confirmation,
                   valid_from, valid_to,
                   supplier_exception_reason
              from crm.opportunity_organization
             where id = %s
               for update
            """,
            (fields["opportunity_organization_id"],),
        )
        row = self._row(cur)
        if row is None:
            raise CommandRefused(
                404, "case_organization_not_found", "no such institution row on any case"
            )
        if row["opportunity_id"] != case["id"]:
            raise CommandRefused(
                422,
                "case_organization_belongs_to_another_case",
                "that institution row belongs to a different case",
            )
        if row["valid_to"] is not None:
            raise CommandRefused(
                409,
                "case_organization_already_closed",
                "that reading was already closed; it stays readable, and a new part is "
                "added rather than reopened",
            )

        new_role = fields["role"]
        events: list[str] = []
        case_version = int(case["version"])

        if new_role == row["role"]:
            if row["confirmation"] == "confirmed":
                raise CommandRefused(
                    409,
                    "case_organization_role_unchanged",
                    f"this institution is already confirmed as '{new_role}' on this case; "
                    "there is nothing to confirm and nothing to change",
                )
            # A machine may only ever have proposed `mentioned` (a CHECK says so), so this
            # branch confirms exactly that: named in the evidence, part still not decided,
            # and now a human has read it and agrees.
            cur.execute(
                """
                update crm.opportunity_organization
                   set confirmation = 'confirmed', confirmed_by_operator_id = %s,
                       note = %s, updated_at = now()
                 where id = %s and confirmation = 'machine_proposed' and valid_to is null
                """,
                (operator.operator_id, fields["note"], row["id"]),
            )
            if cur.rowcount != 1:
                raise CommandRefused(
                    409,
                    "case_organization_changed_concurrently",
                    "that reading was decided by someone else while this command ran",
                )
            events.append(
                self._append_event(
                    cur,
                    aggregate_kind="opportunity_organization",
                    aggregate_id=row["id"],
                    event_type="case_organization.role_confirmed",
                    payload={
                        "opportunity_id": case["id"],
                        "organization_id": row["organization_id"],
                        "role": new_role,
                        "confirmed_from": "machine_proposed",
                        "note": fields["note"],
                    },
                    operator=operator,
                    receipt_id=receipt_id,
                )
            )
            return {
                "command": SET_CASE_ORGANIZATION_ROLE,
                "opportunity_id": case["id"],
                "opportunity_version": case_version,
                "opportunity_organization_id": row["id"],
                "organization_id": row["organization_id"],
                "role": new_role,
                "previous_role": row["role"],
                "stage": case["stage"],
                "supplier_exception": False,
                "created": [],
                "event_ids": events,
            }

        # A different part. Close the old reading and open the new one, in that order.
        if row["role"] == REQUESTING_INSTITUTION and case["stage"] in (
            STAGES_REQUIRING_A_REQUESTING_INSTITUTION
        ):
            raise CommandRefused(
                409,
                "case_needs_a_requesting_institution_at_this_stage",
                f"this case is at '{case['stage']}', which means an operator decided who is "
                "asking. Taking that away would leave the case claiming something it no "
                "longer records; step the case back first, or name the new requester on a "
                "case that is still open to the question",
            )

        cur.execute(
            """
            update crm.opportunity_organization
               set valid_to = greatest(valid_from, current_date), updated_at = now()
             where id = %s and valid_to is null
            """,
            (row["id"],),
        )
        if cur.rowcount != 1:
            raise CommandRefused(
                409,
                "case_organization_changed_concurrently",
                "that reading was closed by someone else while this command ran",
            )
        events.append(
            self._append_event(
                cur,
                aggregate_kind="opportunity_organization",
                aggregate_id=row["id"],
                event_type="case_organization.ended",
                payload={
                    "opportunity_id": case["id"],
                    "organization_id": row["organization_id"],
                    "role": row["role"],
                    "ended_because": f"replaced by '{new_role}'",
                    "note": fields["note"],
                },
                operator=operator,
                receipt_id=receipt_id,
            )
        )

        if row["role"] == REQUESTING_INSTITUTION:
            # The case no longer names who is asking, and the column that says so must let go
            # in the same transaction — otherwise `organization_id` would point at an
            # institution whose role row has been closed, which the deferred trigger judges
            # at commit anyway.
            case_version = self._set_case_organization_id(
                cur, case_id=case["id"], version=case_version, organization_id=None
            )
            events.append(
                self._append_event(
                    cur,
                    aggregate_kind="opportunity",
                    aggregate_id=case["id"],
                    event_type="opportunity.organization_set",
                    payload={
                        "organization_id": None,
                        "previous_organization_id": row["organization_id"],
                        "reason": f"the requester's part became '{new_role}'",
                        "note": fields["note"],
                    },
                    operator=operator,
                    receipt_id=receipt_id,
                )
            )
            cur.execute("set constraints all immediate")

        case = {**case, "version": case_version}
        new_id, open_events, case_version = self._open_case_organization_row(
            cur,
            case=case,
            organization_id=row["organization_id"],
            role=new_role,
            supplier_exception_reason=fields["supplier_exception_reason"],
            operator=operator,
            receipt_id=receipt_id,
            note=fields["note"],
        )
        events.extend(open_events)

        return {
            "command": SET_CASE_ORGANIZATION_ROLE,
            "opportunity_id": case["id"],
            "opportunity_version": case_version,
            "opportunity_organization_id": new_id,
            "closed_opportunity_organization_id": row["id"],
            "organization_id": row["organization_id"],
            "role": new_role,
            "previous_role": row["role"],
            "stage": case["stage"],
            "supplier_exception": fields["supplier_exception_reason"] is not None,
            "created": ["opportunity_organization"],
            "event_ids": events,
        }

    def _record_case_interest(
        self, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
    ) -> dict[str, Any]:
        """What the case is seeking — a quantity at most, never an amount.

        When both a catalogue product and a manufacturer are named they must agree. A trigger
        enforces it; this checks first so the refusal says which manufacturer the catalogue
        actually records, which is the thing the operator needs in order to fix it.
        """
        case = self._live_case(cur, fields["opportunity_id"], fields["opportunity_version"])

        manufacturer_id = fields["manufacturer_organization_id"]
        if fields["product_id"] is not None:
            cur.execute(
                "select id::text as id, "
                "manufacturer_organization_id::text as manufacturer_organization_id "
                "from catalog.product where id = %s",
                (fields["product_id"],),
            )
            product = self._row(cur)
            if product is None:
                raise CommandRefused(404, "product_not_found", "no such catalogue product")
            if (
                manufacturer_id is not None
                and product["manufacturer_organization_id"] != manufacturer_id
            ):
                raise CommandRefused(
                    422,
                    "interest_manufacturer_disagrees_with_the_product",
                    "the catalogue records a different manufacturer for that product; "
                    "naming another one here is a contradiction, not a refinement",
                )
        if manufacturer_id is not None:
            self._live_organization(cur, manufacturer_id)

        cur.execute(
            """
            insert into crm.opportunity_interest
                (opportunity_id, product_id, manufacturer_organization_id, model_text,
                 description, quantity, quantity_unit, confirmation,
                 confirmed_by_operator_id, note)
            values (%s, %s, %s, %s, %s, %s, %s, 'confirmed', %s, %s)
            returning id::text as id
            """,
            (
                case["id"],
                fields["product_id"],
                manufacturer_id,
                fields["model_text"],
                fields["description"],
                fields["quantity"],
                fields["quantity_unit"],
                operator.operator_id,
                fields["note"],
            ),
        )
        row = self._row(cur)
        assert row is not None  # noqa: S101 - `returning` on a successful insert

        event_id = self._append_event(
            cur,
            aggregate_kind="opportunity_interest",
            aggregate_id=row["id"],
            event_type="case_interest.added",
            payload={
                "opportunity_id": case["id"],
                "product_id": fields["product_id"],
                "manufacturer_organization_id": manufacturer_id,
                "model_text": fields["model_text"],
                "quantity": str(fields["quantity"]) if fields["quantity"] is not None else None,
                "quantity_unit": fields["quantity_unit"],
                "confirmation": "confirmed",
                "note": fields["note"],
            },
            operator=operator,
            receipt_id=receipt_id,
        )
        return {
            "command": RECORD_CASE_INTEREST,
            "opportunity_id": case["id"],
            "opportunity_version": int(case["version"]),
            "opportunity_interest_id": row["id"],
            "stage": case["stage"],
            "created": ["opportunity_interest"],
            "event_ids": [event_id],
        }

    def _advance_case_stage(
        self, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
    ) -> dict[str, Any]:
        """Move the case, when the rules for moving it are met — and name the rule when not.

        Three refusals, in the order an operator would meet them: this case has ended; that
        is not a move this stage allows; and from `qualified` onward, a case must record who
        is asking, decided by a human.

        The last one is not this function's invention and could not be worked around if it
        were. `opportunity_organization_required_from_qualified` refuses the stage without
        `organization_id`; the deferred agreement trigger makes that column *be* the current
        requesting institution; and `opportunity_organization_requesting_is_human` makes that
        row `confirmed` with a named operator. The check below only turns a constraint
        violation into a sentence.
        """
        case = self._live_case(
            cur, fields["opportunity_id"], fields["opportunity_version"], allow_closed=True
        )
        target = fields["stage"]

        if case["closed_at"] is not None:
            raise CommandRefused(
                409,
                "case_is_closed",
                f"this case ended at stage '{case['stage']}', which is terminal and is never "
                "revived; reopening is a new case that references this one",
            )
        if target == case["stage"]:
            raise CommandRefused(
                409,
                "case_is_already_at_that_stage",
                f"this case is already at '{target}'",
            )
        if not stage_transition_allowed(case["stage"], target):
            raise CommandRefused(
                422,
                "stage_transition_not_allowed",
                f"'{case['stage']}' → '{target}' is not a move this case may make "
                "(WORKFLOWS.md §1.1)",
            )

        if target in STAGES_REQUIRING_A_REQUESTING_INSTITUTION:
            requesting = self._current_requesting_institution(cur, case["id"])
            if requesting is None or requesting["confirmation"] != "confirmed":
                raise CommandRefused(
                    409,
                    "stage_requires_a_confirmed_requesting_institution",
                    f"'{target}' means an operator decided who is asking, and this case does "
                    "not record one. Name the requesting institution first; a case may sit "
                    "at 'lead' or 'qualifying' without one for as long as it takes",
                )

        is_terminal = target in TERMINAL_STAGES
        cur.execute(
            """
            update crm.opportunity
               set stage = %s,
                   close_reason = %s,
                   closed_at = case when %s then now() else null end,
                   version = version + 1,
                   updated_at = now()
             where id = %s and version = %s
            """,
            (target, fields["close_reason"], is_terminal, case["id"], int(case["version"])),
        )
        if cur.rowcount != 1:
            raise CommandRefused(
                409,
                "case_version_conflict",
                "this case changed while the command ran; re-read it and decide again",
            )
        case_version = int(case["version"]) + 1

        events = [
            self._append_event(
                cur,
                aggregate_kind="opportunity",
                aggregate_id=case["id"],
                event_type="opportunity.staged",
                payload={
                    "from_stage": case["stage"],
                    "to_stage": target,
                    "note": fields["note"],
                },
                operator=operator,
                receipt_id=receipt_id,
            )
        ]
        if is_terminal:
            events.append(
                self._append_event(
                    cur,
                    aggregate_kind="opportunity",
                    aggregate_id=case["id"],
                    event_type="opportunity.closed",
                    payload={
                        "stage": target,
                        "close_reason": fields["close_reason"],
                        "note": fields["note"],
                    },
                    operator=operator,
                    receipt_id=receipt_id,
                )
            )

        return {
            "command": ADVANCE_CASE_STAGE,
            "opportunity_id": case["id"],
            "opportunity_version": case_version,
            "stage": target,
            "previous_stage": case["stage"],
            "closed": is_terminal,
            "close_reason": fields["close_reason"],
            "created": [],
            "event_ids": events,
        }

    _HANDLERS = {
        OPEN_COMMERCIAL_CASE: _open_commercial_case,
        LINK_CASE_EVIDENCE: _link_case_evidence,
        ADD_CASE_ORGANIZATION: _add_case_organization,
        SET_CASE_ORGANIZATION_ROLE: _set_case_organization_role,
        RECORD_CASE_INTEREST: _record_case_interest,
        ADVANCE_CASE_STAGE: _advance_case_stage,
    }
