"""The transactional half of the V2 human-review command boundary.

`repository.py` opens `begin read only` and cannot write. This module is its counterpart and
the **only** place in `apps/api` that writes to the V2 durable core. Everything it does
obeys four rules, and each is structural rather than remembered:

1. **One transaction per command.** The receipt, the `crm.*` change, the assertion's
   resolution and every event are the same transaction. A refusal at any point rolls the
   whole thing back, including the receipt — so a refused command leaves the idempotency key
   free and the database byte-identical. `test_a_refused_command_writes_nothing_at_all`
   proves it against a real database rather than asserting it here.
2. **Evidence is never destroyed.** `origenlab_api` holds no INSERT or DELETE on
   `evidence.source_record` or `evidence.assertion`, and UPDATE only on the resolution and
   review columns. A command therefore *cannot* rewrite an assertion's `value_norm` or a
   record's `payload` even if this file asked it to: what the mailbox said stays what the
   mailbox said, and resolving an assertion annotates it rather than replacing it.
3. **Every state change is compare-and-set.** An assertion resolves only `where resolution =
   'unresolved'`; a contact point is attached only `where person_id is null and
   organization_id is null`; an organization is confirmed only `where version = %s`. Two
   operators racing on the same record do not both win — the loser gets a 409 naming the
   conflict, which is the honest answer.
4. **Nothing is inferred.** No query here joins on a domain, scores a name or picks a
   candidate. Every subject is a UUID the operator sent, and the only comparison performed is
   equality between an asserted name and an organization's name.

The role is `origenlab_api`, with no membership in `origenlab_owner`, so RLS and the per-verb
grants constrain these writes exactly as they will in production.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from origenlab_api.v2.commands import (
    ATTACH_CONTACT_ADDRESS,
    CONFIRM_ORGANIZATION,
    CREATE_ORGANIZATION,
    KEEP_EVIDENCE_PENDING,
    CommandRefused,
    normalize_email,
    normalize_organization_name,
    validate_email_shape,
)
from origenlab_api.v2.identity import OperatorIdentity

#: Evidence review reads a handful of rows and writes a handful. A command that has not
#: finished in this long is stuck on a lock, and failing is better than holding one.
DEFAULT_COMMAND_TIMEOUT_MS = 15_000

#: Source-record states a command will act on. A `reviewed` or `promoted` record has already
#: been decided and a `rejected` one was thrown out; re-deciding either silently would bury
#: the earlier decision instead of contradicting it out loud.
ACTIONABLE_REVIEW_STATUSES: tuple[str, ...] = ("pending",)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class V2CommandRepository:
    """The four evidence-review commands, each in one transaction."""

    def __init__(
        self,
        connect: Any,
        dsn: str,
        statement_timeout_ms: int = DEFAULT_COMMAND_TIMEOUT_MS,
    ) -> None:
        self._connect = connect
        self._dsn = dsn
        self._statement_timeout_ms = statement_timeout_ms

    # ------------------------------------------------------------------ the transaction

    @contextmanager
    def _write(self) -> Iterator[Any]:
        """One read-write transaction, committed only if the whole command succeeded.

        The commit and the rollback are both explicit. Relying on the driver's context
        manager to commit on the way out would make "what happens when a handler raises"
        depend on which driver is installed, and that is precisely the behaviour rule 1
        above is about.
        """
        with self._connect(self._dsn, autocommit=False) as conn:
            with conn.cursor() as cur:
                cur.execute(f"set local statement_timeout = {int(self._statement_timeout_ms)}")
                try:
                    yield cur
                except BaseException:
                    conn.rollback()
                    raise
                conn.commit()

    def _row(self, cur: Any) -> dict[str, Any] | None:
        row = cur.fetchone()
        if row is None:
            return None
        return dict(zip([d[0] for d in cur.description], row, strict=True))

    # ------------------------------------------------------------------ idempotency

    def _claim_receipt(
        self,
        cur: Any,
        operator: OperatorIdentity,
        idempotency_key: str,
        command_name: str,
        digest: str,
    ) -> tuple[str, dict[str, Any] | None]:
        """Claim the key, or hand back what the first call already answered.

        `insert ... on conflict do nothing` is the claim, and it is atomic: two concurrent
        requests carrying one key cannot both proceed, because exactly one of them inserts.
        The loser reads the existing receipt and gets one of three answers — the stored
        response (a replay), a 409 because the key was reused for a *different* request, or
        a 409 because the first call is still running.
        """
        cur.execute(
            """
            insert into platform.command_receipt
                (operator_id, idempotency_key, command_name, request_digest, status)
            values (%s, %s, %s, %s, 'in_progress')
            on conflict (operator_id, idempotency_key) do nothing
            returning id::text as id
            """,
            (operator.operator_id, idempotency_key, command_name, digest),
        )
        claimed = self._row(cur)
        if claimed is not None:
            return claimed["id"], None

        cur.execute(
            """
            select id::text as id, command_name, request_digest, status, response_body
              from platform.command_receipt
             where operator_id = %s and idempotency_key = %s
            """,
            (operator.operator_id, idempotency_key),
        )
        existing = self._row(cur)
        if existing is None:  # pragma: no cover - the conflict says the row is there
            raise CommandRefused(409, "idempotency_conflict", "the idempotency key is in use")

        if existing["command_name"] != command_name or existing["request_digest"] != digest:
            raise CommandRefused(
                409,
                "idempotency_key_reused",
                "this Idempotency-Key was already used for a different request; "
                "a new decision needs a new key",
            )
        if existing["status"] == "completed":
            body = existing["response_body"]
            if isinstance(body, str):  # pragma: no cover - driver-dependent jsonb decoding
                body = json.loads(body)
            return existing["id"], {**body, "replayed": True}
        if existing["status"] == "in_progress":
            raise CommandRefused(
                409,
                "command_in_progress",
                "a command with this Idempotency-Key is still running",
            )
        raise CommandRefused(
            409,
            "command_already_failed",
            "a command with this Idempotency-Key failed; retry with a new key",
        )

    def _complete_receipt(self, cur: Any, receipt_id: str, response: dict[str, Any]) -> None:
        cur.execute(
            """
            update platform.command_receipt
               set status = 'completed', response_status = 200,
                   response_body = %s::jsonb, completed_at = now()
             where id = %s
            """,
            (_json(response), receipt_id),
        )

    # ------------------------------------------------------------------ shared reads

    def _actionable_source_record(self, cur: Any, source_record_id: str) -> dict[str, Any]:
        """The record, locked, and only if a decision about it is still open."""
        cur.execute(
            """
            select id::text as id, kind, review_status, is_quarantined
              from evidence.source_record
             where id = %s
               for update
            """,
            (source_record_id,),
        )
        record = self._row(cur)
        if record is None:
            raise CommandRefused(404, "source_record_not_found", "no such evidence record")
        if record["is_quarantined"]:
            raise CommandRefused(
                409,
                "source_record_quarantined",
                "this record is quarantined; its evidence contradicts itself and is not "
                "promotable until that is resolved",
            )
        if record["review_status"] not in ACTIONABLE_REVIEW_STATUSES:
            raise CommandRefused(
                409,
                "source_record_already_reviewed",
                f"this record is already '{record['review_status']}'",
            )
        return record

    def _unresolved_assertion(
        self, cur: Any, assertion_id: str, source_record_id: str, kind: str
    ) -> dict[str, Any]:
        """The assertion, locked, and only if it is the right one and still undecided.

        The `source_record_id` is checked rather than trusted. Without it an operator could
        resolve an assertion belonging to some *other* record by pairing two unrelated ids,
        and the event trail would record a decision about a message nobody read.
        """
        cur.execute(
            """
            select id::text as id, source_record_id::text as source_record_id,
                   kind, value_norm, resolution,
                   value->>'observed_value' as observed_value
              from evidence.assertion
             where id = %s
               for update
            """,
            (assertion_id,),
        )
        assertion = self._row(cur)
        if assertion is None:
            raise CommandRefused(404, "assertion_not_found", "no such assertion")
        if assertion["source_record_id"] != source_record_id:
            raise CommandRefused(
                422,
                "assertion_belongs_to_another_record",
                "that assertion does not belong to the evidence record in this request",
            )
        if assertion["kind"] != kind:
            raise CommandRefused(
                422,
                "assertion_wrong_kind",
                f"this command decides a '{kind}' assertion; that one is '{assertion['kind']}'",
            )
        if assertion["resolution"] != "unresolved":
            raise CommandRefused(
                409,
                "assertion_already_resolved",
                f"that assertion is already '{assertion['resolution']}'; "
                "the earlier decision stands until it is deliberately revisited",
            )
        return assertion

    def _live_organization(self, cur: Any, organization_id: str) -> dict[str, Any]:
        cur.execute(
            """
            select id::text as id, name, kind, confirmation, version,
                   merged_into_organization_id::text as merged_into_organization_id
              from crm.organization
             where id = %s
               for update
            """,
            (organization_id,),
        )
        organization = self._row(cur)
        if organization is None:
            raise CommandRefused(404, "organization_not_found", "no such organization")
        if organization["merged_into_organization_id"] is not None:
            raise CommandRefused(
                409,
                "organization_merged",
                "that organization has been merged into another; decide against the survivor",
            )
        return organization

    # ------------------------------------------------------------------ the audit stream

    def _append_event(
        self,
        cur: Any,
        *,
        aggregate_kind: str,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, Any],
        operator: OperatorIdentity,
        receipt_id: str,
    ) -> str:
        """One row on `crm.domain_event`, with the next sequence for its aggregate.

        `seq` is computed in the same statement that inserts it. If two transactions ever
        reach the same aggregate at once, the unique `(aggregate_kind, aggregate_id, seq)`
        makes one of them fail rather than letting both claim the same position in the
        stream — an audit trail with a duplicated position is not an audit trail.
        """
        cur.execute(
            """
            insert into crm.domain_event
                (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload,
                 actor_kind, actor_operator_id, command_receipt_id)
            values (
                %(aggregate_kind)s, %(aggregate_id)s,
                (select coalesce(max(seq), 0) + 1
                   from crm.domain_event
                  where aggregate_kind = %(aggregate_kind)s and aggregate_id = %(aggregate_id)s),
                %(event_type)s, 1, %(payload)s::jsonb,
                'operator', %(operator_id)s, %(receipt_id)s
            )
            returning id::text as id
            """,
            {
                "aggregate_kind": aggregate_kind,
                "aggregate_id": aggregate_id,
                "event_type": event_type,
                "payload": _json(payload),
                "operator_id": operator.operator_id,
                "receipt_id": receipt_id,
            },
        )
        row = self._row(cur)
        assert row is not None  # noqa: S101 - `returning` on a successful insert
        return row["id"]

    def _resolve_assertion(
        self,
        cur: Any,
        *,
        assertion_id: str,
        resolution: str,
        resolved_kind: str,
        resolved_id: str,
        operator: OperatorIdentity,
        receipt_id: str,
        note: str,
        source_record_id: str,
    ) -> None:
        """Annotate the assertion with what an operator decided it meant.

        The `where resolution = 'unresolved'` is the compare-and-set: the row was read and
        locked earlier in this transaction, and repeating the predicate here means a future
        refactor that drops the lock still cannot overwrite somebody else's decision.

        Only resolution columns move. `value_norm`, `value` and `kind` are not in this
        statement and could not be even if they were — `origenlab_api` has no grant on them.
        """
        cur.execute(
            """
            update evidence.assertion
               set resolution = %s, resolved_kind = %s, resolved_id = %s,
                   resolved_at = now(), resolved_by_operator_id = %s, updated_at = now()
             where id = %s and resolution = 'unresolved'
            """,
            (resolution, resolved_kind, resolved_id, operator.operator_id, assertion_id),
        )
        if cur.rowcount != 1:
            raise CommandRefused(
                409,
                "assertion_resolved_concurrently",
                "that assertion was decided by someone else while this command ran",
            )
        self._append_event(
            cur,
            aggregate_kind="assertion",
            aggregate_id=assertion_id,
            event_type="assertion.promoted",
            payload={
                "resolution": resolution,
                "resolved_kind": resolved_kind,
                "resolved_id": resolved_id,
                "source_record_id": source_record_id,
                "note": note,
            },
            operator=operator,
            receipt_id=receipt_id,
        )

    def _close_record_if_settled(
        self,
        cur: Any,
        *,
        source_record_id: str,
        operator: OperatorIdentity,
        receipt_id: str,
    ) -> bool:
        """Move the record to `reviewed` once nothing about it is unresolved any more.

        This is the only place a command touches `review_status`, and it is bookkeeping, not
        judgement: it fires when the last open question about the record has been answered,
        so the queue drains as decisions are made instead of growing forever. The record, its
        payload and every assertion stay exactly where they are.
        """
        cur.execute(
            "select count(*) as remaining from evidence.assertion "
            "where source_record_id = %s and resolution = 'unresolved'",
            (source_record_id,),
        )
        row = self._row(cur)
        assert row is not None  # noqa: S101 - count() always returns a row
        if int(row["remaining"]) > 0:
            return False

        cur.execute(
            "update evidence.source_record set review_status = 'reviewed', updated_at = now() "
            "where id = %s and review_status = 'pending'",
            (source_record_id,),
        )
        if cur.rowcount != 1:  # pragma: no cover - the record was locked at the top
            return False
        self._append_event(
            cur,
            aggregate_kind="source_record",
            aggregate_id=source_record_id,
            event_type="source_record.review_noted",
            payload={"decision": "all_assertions_resolved", "review_status": "reviewed"},
            operator=operator,
            receipt_id=receipt_id,
        )
        return True

    # ------------------------------------------------------------------ the commands

    def _keep_evidence_pending(
        self, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
    ) -> dict[str, Any]:
        record = self._actionable_source_record(cur, fields["source_record_id"])
        event_id = self._append_event(
            cur,
            aggregate_kind="source_record",
            aggregate_id=record["id"],
            event_type="source_record.review_noted",
            payload={
                "decision": "keep_pending",
                "review_status": "pending",
                "source_kind": record["kind"],
                "note": fields["note"],
            },
            operator=operator,
            receipt_id=receipt_id,
        )
        return {
            "command": KEEP_EVIDENCE_PENDING,
            "source_record_id": record["id"],
            "review_status": "pending",
            "created": [],
            "event_ids": [event_id],
        }

    def _confirm_organization(
        self, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
    ) -> dict[str, Any]:
        record = self._actionable_source_record(cur, fields["source_record_id"])
        assertion = self._unresolved_assertion(
            cur, fields["assertion_id"], record["id"], "organization_name"
        )
        organization = self._live_organization(cur, fields["organization_id"])

        # The only comparison in the whole boundary, and it is equality. A name that merely
        # resembles the asserted one is a different organization until a human says otherwise
        # somewhere that is not this command.
        if normalize_organization_name(organization["name"]) != normalize_organization_name(
            assertion["value_norm"]
        ):
            raise CommandRefused(
                422,
                "organization_name_is_not_an_exact_match",
                "that organization's name is not the name this message asserts; "
                "confirming it would be a guess, so it is refused",
            )
        if int(organization["version"]) != int(fields["organization_version"]):
            raise CommandRefused(
                409,
                "organization_version_conflict",
                "that organization changed since it was shown to you; re-read it and decide again",
            )

        events: list[str] = []
        if organization["confirmation"] != "confirmed":
            cur.execute(
                """
                update crm.organization
                   set confirmation = 'confirmed', confirmed_by_operator_id = %s,
                       version = version + 1, updated_at = now()
                 where id = %s and version = %s
                """,
                (operator.operator_id, organization["id"], int(fields["organization_version"])),
            )
            if cur.rowcount != 1:
                raise CommandRefused(
                    409,
                    "organization_version_conflict",
                    "that organization changed while this command ran",
                )
            events.append(
                self._append_event(
                    cur,
                    aggregate_kind="organization",
                    aggregate_id=organization["id"],
                    event_type="organization.confirmed",
                    payload={
                        "confirmed_from": "evidence_review",
                        "source_record_id": record["id"],
                        "assertion_id": assertion["id"],
                        "note": fields["note"],
                    },
                    operator=operator,
                    receipt_id=receipt_id,
                )
            )

        self._resolve_assertion(
            cur,
            assertion_id=assertion["id"],
            resolution="linked",
            resolved_kind="organization",
            resolved_id=organization["id"],
            operator=operator,
            receipt_id=receipt_id,
            note=fields["note"],
            source_record_id=record["id"],
        )
        reviewed = self._close_record_if_settled(
            cur, source_record_id=record["id"], operator=operator, receipt_id=receipt_id
        )
        return {
            "command": CONFIRM_ORGANIZATION,
            "source_record_id": record["id"],
            "assertion_id": assertion["id"],
            "organization_id": organization["id"],
            "review_status": "reviewed" if reviewed else "pending",
            "created": [],
            "event_ids": events,
        }

    def _create_organization(
        self, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
    ) -> dict[str, Any]:
        record = self._actionable_source_record(cur, fields["source_record_id"])
        assertion = self._unresolved_assertion(
            cur, fields["assertion_id"], record["id"], "organization_name"
        )
        name_norm = normalize_organization_name(assertion["value_norm"])

        # The name comes from the evidence. `name_display` may restore the capitalisation a
        # lower-cased `value_norm` lost and nothing else, which is why it is compared folded.
        display = fields["name_display"] or assertion["observed_value"] or assertion["value_norm"]
        if normalize_organization_name(display) != name_norm:
            raise CommandRefused(
                422,
                "name_display_does_not_match_the_assertion",
                "the display name must be the asserted name in different letter case; "
                "an organization may not be created under a name the message never stated",
            )

        cur.execute(
            "select id::text as id from crm.organization "
            "where lower(name) = %s and merged_into_organization_id is null",
            (name_norm,),
        )
        clash = self._row(cur)
        if clash is not None:
            raise CommandRefused(
                409,
                "organization_already_exists",
                "an organization with exactly this name already exists; confirm that one "
                "instead of creating a second row for the same name",
            )

        cur.execute(
            """
            insert into crm.organization
                (kind, name, confirmation, confirmed_by_operator_id, origin_source_record_id, note)
            values (%s, %s, 'confirmed', %s, %s, %s)
            returning id::text as id, version
            """,
            (fields["kind"], display, operator.operator_id, record["id"], fields["note"]),
        )
        created = self._row(cur)
        assert created is not None  # noqa: S101 - `returning` on a successful insert

        events = [
            self._append_event(
                cur,
                aggregate_kind="organization",
                aggregate_id=created["id"],
                event_type="organization.created",
                payload={
                    "name": display,
                    "kind": fields["kind"],
                    "created_from": "evidence_review",
                    "source_record_id": record["id"],
                    "assertion_id": assertion["id"],
                    "note": fields["note"],
                },
                operator=operator,
                receipt_id=receipt_id,
            )
        ]
        self._resolve_assertion(
            cur,
            assertion_id=assertion["id"],
            resolution="promoted",
            resolved_kind="organization",
            resolved_id=created["id"],
            operator=operator,
            receipt_id=receipt_id,
            note=fields["note"],
            source_record_id=record["id"],
        )
        reviewed = self._close_record_if_settled(
            cur, source_record_id=record["id"], operator=operator, receipt_id=receipt_id
        )
        return {
            "command": CREATE_ORGANIZATION,
            "source_record_id": record["id"],
            "assertion_id": assertion["id"],
            "organization_id": created["id"],
            "organization_version": int(created["version"]),
            "review_status": "reviewed" if reviewed else "pending",
            "created": ["organization"],
            "event_ids": events,
        }

    def _attach_contact_address(
        self, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
    ) -> dict[str, Any]:
        record = self._actionable_source_record(cur, fields["source_record_id"])
        assertion = self._unresolved_assertion(
            cur, fields["assertion_id"], record["id"], "contact_address"
        )
        organization = self._live_organization(cur, fields["organization_id"])
        value_norm = validate_email_shape(normalize_email(assertion["value_norm"]))
        display = assertion["observed_value"] or value_norm

        cur.execute(
            """
            select id::text as id, person_id::text as person_id,
                   organization_id::text as organization_id, usage, confirmation
              from crm.contact_point
             where kind = 'email' and value_norm = %s
               for update
            """,
            (value_norm,),
        )
        existing = self._row(cur)

        events: list[str] = []
        created: list[str] = []
        if existing is None:
            cur.execute(
                """
                insert into crm.contact_point
                    (kind, value_norm, value_display, organization_id, usage, confirmation,
                     origin_source_record_id)
                values ('email', %s, %s, %s, %s, 'confirmed', %s)
                returning id::text as id
                """,
                (value_norm, display, organization["id"], fields["usage"], record["id"]),
            )
            row = self._row(cur)
            assert row is not None  # noqa: S101 - `returning` on a successful insert
            contact_point_id = row["id"]
            created.append("contact_point")
            resolution = "promoted"
            events.append(
                self._append_event(
                    cur,
                    aggregate_kind="contact_point",
                    aggregate_id=contact_point_id,
                    event_type="contact_point.created",
                    payload={
                        "usage": fields["usage"],
                        "organization_id": organization["id"],
                        "created_from": "evidence_review",
                        "source_record_id": record["id"],
                        "assertion_id": assertion["id"],
                        "note": fields["note"],
                    },
                    operator=operator,
                    receipt_id=receipt_id,
                )
            )
        else:
            contact_point_id = existing["id"]
            resolution = "linked"
            if existing["person_id"] is not None:
                raise CommandRefused(
                    409,
                    "contact_point_belongs_to_a_person",
                    "that address is already attributed to a person; attaching it to an "
                    "organization as a shared mailbox would contradict a recorded identity",
                )
            if (
                existing["organization_id"] is not None
                and existing["organization_id"] != organization["id"]
            ):
                raise CommandRefused(
                    409,
                    "contact_point_attached_elsewhere",
                    "that address already belongs to a different organization",
                )
            if existing["organization_id"] is None:
                cur.execute(
                    """
                    update crm.contact_point
                       set organization_id = %s, usage = %s, confirmation = 'confirmed',
                           updated_at = now()
                     where id = %s and person_id is null and organization_id is null
                       and usage = 'unattributed'
                    """,
                    (organization["id"], fields["usage"], contact_point_id),
                )
                if cur.rowcount != 1:
                    raise CommandRefused(
                        409,
                        "contact_point_changed_concurrently",
                        "that address was attributed by someone else while this command ran",
                    )
                events.append(
                    self._append_event(
                        cur,
                        aggregate_kind="contact_point",
                        aggregate_id=contact_point_id,
                        event_type="contact_point.confirmed",
                        payload={
                            "usage": fields["usage"],
                            "organization_id": organization["id"],
                            "attached_from": "evidence_review",
                            "source_record_id": record["id"],
                            "assertion_id": assertion["id"],
                            "note": fields["note"],
                        },
                        operator=operator,
                        receipt_id=receipt_id,
                    )
                )

        self._resolve_assertion(
            cur,
            assertion_id=assertion["id"],
            resolution=resolution,
            resolved_kind="contact_point",
            resolved_id=contact_point_id,
            operator=operator,
            receipt_id=receipt_id,
            note=fields["note"],
            source_record_id=record["id"],
        )
        reviewed = self._close_record_if_settled(
            cur, source_record_id=record["id"], operator=operator, receipt_id=receipt_id
        )
        return {
            "command": ATTACH_CONTACT_ADDRESS,
            "source_record_id": record["id"],
            "assertion_id": assertion["id"],
            "organization_id": organization["id"],
            "contact_point_id": contact_point_id,
            "review_status": "reviewed" if reviewed else "pending",
            "created": created,
            "event_ids": events,
        }

    _HANDLERS = {
        KEEP_EVIDENCE_PENDING: _keep_evidence_pending,
        CONFIRM_ORGANIZATION: _confirm_organization,
        CREATE_ORGANIZATION: _create_organization,
        ATTACH_CONTACT_ADDRESS: _attach_contact_address,
    }

    # ------------------------------------------------------------------ the entry point

    def execute(
        self,
        *,
        command_name: str,
        operator: OperatorIdentity,
        fields: dict[str, Any],
        idempotency_key: str,
        digest: str,
    ) -> dict[str, Any]:
        """Run one command, or replay the one this key already ran."""
        handler = self._HANDLERS.get(command_name)
        if handler is None:  # pragma: no cover - the router names the command, not the client
            raise CommandRefused(404, "unknown_command", f"no such command '{command_name}'")

        with self._write() as cur:
            receipt_id, replay = self._claim_receipt(
                cur, operator, idempotency_key, command_name, digest
            )
            if replay is not None:
                return replay
            response = handler(self, cur, operator, fields, receipt_id)
            response["idempotency_key"] = idempotency_key
            response["command_receipt_id"] = receipt_id
            response["replayed"] = False
            self._complete_receipt(cur, receipt_id, response)
            return response
