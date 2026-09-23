"""What every V2 command does identically, in one place.

`command_repository.py` and `case_command_repository.py` are two families of command — one
settles what a staged message meant, the other runs a commercial case — and they share four
mechanisms exactly, not approximately:

1. **One read-write transaction per command**, committed only if the whole command succeeded.
2. **One receipt per `(operator, Idempotency-Key)`**, claimed atomically, replayed verbatim.
3. **One `crm.domain_event` per change**, with a per-aggregate sequence computed in the same
   statement that inserts it.
4. **One dispatch**: a command name maps to a handler, and everything above happens around it
   whether the handler is `keep_evidence_pending` or `advance_case_stage`.

They were one class until the case commands arrived. Copying the four into a second class
would have meant two implementations of "a refused command writes nothing at all", and the
day they disagreed, only one of them would have been tested. So the mechanisms live here and
the two repositories are their handlers.

Nothing in this module knows what a command *means*. It opens no table by name except
`platform.command_receipt`, `crm.domain_event` and `crm.organization` — the receipt, the
audit stream, and the one read both families need.

The role is `origenlab_api`, with no membership in `origenlab_owner`, so RLS and the per-verb
grants constrain every write below exactly as they will in production.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Callable

from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.identity import OperatorIdentity

#: A command reads a handful of rows and writes a handful. One that has not finished in this
#: long is stuck on a lock, and failing is better than holding one.
DEFAULT_COMMAND_TIMEOUT_MS = 15_000


def json_payload(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class CommandTransaction:
    """The transaction, the receipt, the event stream and the dispatch.

    Subclasses supply `_HANDLERS`: a mapping from command name to a function taking
    `(self, cur, operator, fields, receipt_id)` and returning the response body.
    """

    #: Overridden by every subclass. Empty here so that a repository which forgets to
    #: declare one answers `unknown_command` rather than raising an AttributeError.
    _HANDLERS: dict[str, Callable[..., dict[str, Any]]] = {}

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
        depend on which driver is installed, and that is precisely the behaviour this is
        about.
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
            (json_payload(response), receipt_id),
        )

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

        `actor_kind` is `'operator'` and there is no parameter for anything else. No machine
        writes through this boundary, and a column that cannot say `'worker'` cannot be
        misread later as evidence that one did.
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
                "payload": json_payload(payload),
                "operator_id": operator.operator_id,
                "receipt_id": receipt_id,
            },
        )
        row = self._row(cur)
        assert row is not None  # noqa: S101 - `returning` on a successful insert
        return row["id"]

    # ------------------------------------------------------------------ the one shared read

    def _live_organization(self, cur: Any, organization_id: str) -> dict[str, Any]:
        """The organization, locked, and only if it is still the one to decide against."""
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
