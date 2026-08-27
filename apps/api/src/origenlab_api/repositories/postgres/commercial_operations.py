"""Durable Postgres commercial-operations command repository (ARCH-3B3)."""

from __future__ import annotations

import json
import uuid

from dataclasses import dataclass
from datetime import datetime, timezone

from origenlab_api.repositories.postgres.common import require_psycopg
from origenlab_api.repositories.postgres.write_common import (
    postgres_write_connection,
)
from origenlab_api.settings import Settings


class CommercialOperationNotFoundError(RuntimeError):
    """Requested durable commercial object does not exist."""


class CommercialOperationConflictError(RuntimeError):
    """Optimistic concurrency or state-transition conflict."""


SALES_OPPORTUNITY_STAGES = frozenset(
    {
        "new",
        "qualifying",
        "qualified",
        "quoting",
        "negotiating",
        "won",
        "lost",
        "dormant",
    }
)

SALES_OPPORTUNITY_TERMINAL_STAGES = frozenset(
    {
        "won",
        "lost",
    }
)


@dataclass(frozen=True)
class OperatorState:
    opportunity_id: str
    confirmation_status: str
    manual_stage: str | None
    owner_key: str | None
    version: int
    created_by: str
    updated_by: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class Activity:
    activity_id: str
    opportunity_id: str | None
    account_id: str | None
    contact_id: str | None
    activity_type: str
    occurred_at: datetime
    summary: str
    detail: str | None
    created_by: str
    created_at: datetime
    sales_opportunity_id: str | None = None


@dataclass(frozen=True)
class Task:
    task_id: str
    opportunity_id: str | None
    account_id: str | None
    contact_id: str | None
    title: str
    status: str
    priority: str
    due_at: datetime | None
    owner_key: str | None
    version: int
    created_by: str
    updated_by: str
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    sales_opportunity_id: str | None = None


@dataclass(frozen=True)
class SalesOpportunity:
    sales_opportunity_id: str
    source_kind: str
    source_opportunity_id: str
    account_id: str | None
    primary_contact_id: str | None
    title: str
    stage: str
    owner_key: str
    version: int
    created_by: str
    updated_by: str
    created_at: datetime
    updated_at: datetime
    # CRM-4A durable canonical CRM links. Appended and optional so existing
    # rows (and pre-CRM-4A callers) are unaffected; account_id /
    # primary_contact_id remain rebuildable PR2 identity provenance.
    organization_id: str | None = None
    primary_crm_contact_id: str | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _resolve_sales_opportunity_source(
    cur: object,
    *,
    sales_opportunity_id: str | None,
    opportunity_id: str | None,
) -> str | None:
    """Resolve a durable CRM anchor to its retained PR3 provenance.

    Legacy opportunity_id-only writes remain supported. For CRM-anchored work,
    the durable sales opportunity is authoritative; callers are never trusted
    to invent or mismatch its PR3 source ID.
    """

    if sales_opportunity_id is None:
        return opportunity_id

    cur.execute(
        """
        SELECT
          source_kind,
          source_opportunity_id
        FROM commercial.sales_opportunity
        WHERE sales_opportunity_id = %(sales_opportunity_id)s
        LIMIT 1
        """,
        {
            "sales_opportunity_id": sales_opportunity_id,
        },
    )

    row = cur.fetchone()

    if row is None:
        raise CommercialOperationNotFoundError(
            f"Sales opportunity not found: {sales_opportunity_id}"
        )

    if row["source_kind"] != "pr3":
        raise CommercialOperationConflictError(
            "Sales opportunity cannot currently anchor activity/task work "
            "without PR3 provenance"
        )

    source_opportunity_id = str(row["source_opportunity_id"])

    if (
        opportunity_id is not None
        and opportunity_id != source_opportunity_id
    ):
        raise CommercialOperationConflictError(
            "sales_opportunity_id and opportunity_id refer to different "
            "commercial pursuits"
        )

    return source_opportunity_id


def _claim_idempotency(
    cur: object,
    *,
    operator: str,
    idempotency_key: str,
    command_kind: str,
    request_fingerprint: str,
) -> str | None:
    """Reserve a command key or return its existing result ID."""

    cur.execute(
        """
        INSERT INTO commercial.command_idempotency (
          operator_key,
          idempotency_key,
          command_kind,
          request_fingerprint,
          result_id
        )
        VALUES (
          %(operator)s,
          %(idempotency_key)s,
          %(command_kind)s,
          %(request_fingerprint)s,
          NULL
        )
        ON CONFLICT (
          operator_key,
          idempotency_key
        )
        DO NOTHING
        RETURNING idempotency_key
        """,
        {
            "operator": operator,
            "idempotency_key": idempotency_key,
            "command_kind": command_kind,
            "request_fingerprint": request_fingerprint,
        },
    )

    claimed = cur.fetchone()

    if claimed is not None:
        return None

    # INSERT ... ON CONFLICT waits for an in-flight conflicting
    # transaction. This SELECT therefore observes the committed winner.
    cur.execute(
        """
        SELECT
          command_kind,
          request_fingerprint,
          result_id
        FROM commercial.command_idempotency
        WHERE operator_key = %(operator)s
          AND idempotency_key = %(idempotency_key)s
        FOR UPDATE
        """,
        {
            "operator": operator,
            "idempotency_key": idempotency_key,
        },
    )

    existing = cur.fetchone()

    if existing is None:
        raise RuntimeError("Idempotency reservation disappeared")

    if (
        existing["command_kind"] != command_kind
        or existing["request_fingerprint"] != request_fingerprint
    ):
        raise CommercialOperationConflictError(
            "Idempotency key reused with different request"
        )

    result_id = existing["result_id"]

    if not result_id:
        raise CommercialOperationConflictError("Idempotency result is incomplete")

    return str(result_id)


def _store_idempotency_result(
    cur: object,
    *,
    operator: str,
    idempotency_key: str,
    result_id: str,
) -> None:
    cur.execute(
        """
        UPDATE commercial.command_idempotency
        SET result_id = %(result_id)s
        WHERE operator_key = %(operator)s
          AND idempotency_key = %(idempotency_key)s
          AND result_id IS NULL
        """,
        {
            "operator": operator,
            "idempotency_key": idempotency_key,
            "result_id": result_id,
        },
    )


class PostgresCommercialOperationsRepository:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def opportunity_exists(self, opportunity_id: str) -> bool:
        pg = require_psycopg()

        with postgres_write_connection(self._settings) as conn:
            with conn.cursor(row_factory=pg.rows.dict_row) as cur:
                cur.execute(
                    """
                    SELECT 1
                    FROM api.v_commercial_opportunity
                    WHERE opportunity_id = %(opportunity_id)s
                    LIMIT 1
                    """,
                    {"opportunity_id": opportunity_id},
                )
                return cur.fetchone() is not None

    def upsert_operator_state(
        self,
        *,
        opportunity_id: str,
        confirmation_status: str,
        manual_stage: str | None,
        owner_key: str | None,
        operator: str,
        expected_version: int,
    ) -> OperatorState:
        pg = require_psycopg()
        now = _utcnow()

        with postgres_write_connection(self._settings) as conn:
            with conn.cursor(row_factory=pg.rows.dict_row) as cur:
                cur.execute(
                    """
                    SELECT opportunity_id
                    FROM api.v_commercial_opportunity
                    WHERE opportunity_id = %(opportunity_id)s
                    LIMIT 1
                    """,
                    {"opportunity_id": opportunity_id},
                )
                if cur.fetchone() is None:
                    raise CommercialOperationNotFoundError(
                        f"Commercial opportunity not found: {opportunity_id}"
                    )

                cur.execute(
                    """
                    SELECT *
                    FROM commercial.opportunity_operator_state
                    WHERE opportunity_id = %(opportunity_id)s
                    FOR UPDATE
                    """,
                    {"opportunity_id": opportunity_id},
                )
                existing = cur.fetchone()

                previous_state = (
                    None
                    if existing is None
                    else {
                        "confirmation_status": existing["confirmation_status"],
                        "manual_stage": existing["manual_stage"],
                        "owner_key": existing["owner_key"],
                        "version": int(existing["version"]),
                    }
                )

                if existing is None:
                    if expected_version != 0:
                        raise CommercialOperationConflictError(
                            "Operator state version conflict"
                        )

                    cur.execute(
                        """
                        INSERT INTO commercial.opportunity_operator_state (
                          opportunity_id,
                          confirmation_status,
                          manual_stage,
                          owner_key,
                          version,
                          created_by,
                          updated_by,
                          created_at,
                          updated_at
                        )
                        VALUES (
                          %(opportunity_id)s,
                          %(confirmation_status)s,
                          %(manual_stage)s,
                          %(owner_key)s,
                          1,
                          %(operator)s,
                          %(operator)s,
                          %(now)s,
                          %(now)s
                        )
                        RETURNING *
                        """,
                        {
                            "opportunity_id": opportunity_id,
                            "confirmation_status": confirmation_status,
                            "manual_stage": manual_stage,
                            "owner_key": owner_key,
                            "operator": operator,
                            "now": now,
                        },
                    )
                else:
                    current_version = int(existing["version"])

                    if expected_version != current_version:
                        raise CommercialOperationConflictError(
                            "Operator state version conflict"
                        )

                    cur.execute(
                        """
                        UPDATE commercial.opportunity_operator_state
                        SET
                          confirmation_status = %(confirmation_status)s,
                          manual_stage = %(manual_stage)s,
                          owner_key = %(owner_key)s,
                          version = version + 1,
                          updated_by = %(operator)s,
                          updated_at = %(now)s
                        WHERE opportunity_id = %(opportunity_id)s
                          AND version = %(current_version)s
                        RETURNING *
                        """,
                        {
                            "opportunity_id": opportunity_id,
                            "confirmation_status": confirmation_status,
                            "manual_stage": manual_stage,
                            "owner_key": owner_key,
                            "operator": operator,
                            "now": now,
                            "current_version": current_version,
                        },
                    )

                row = cur.fetchone()
                if row is None:
                    raise CommercialOperationConflictError(
                        "Operator state concurrent update conflict"
                    )

                state = OperatorState(**dict(row))

                cur.execute(
                    """
                    INSERT INTO commercial.opportunity_operator_event (
                        event_id,
                        opportunity_id,
                        event_type,
                        actor_key,
                        payload,
                        created_at
                    )
                    VALUES (
                        %(event_id)s,
                        %(opportunity_id)s,
                        %(event_type)s,
                        %(actor_key)s,
                        %(payload)s,
                        %(created_at)s
                    )
                    """,
                    {
                        "event_id": str(uuid.uuid4()),
                        "opportunity_id": opportunity_id,
                        "event_type": "operator_state_changed",
                        "actor_key": operator,
                        "payload": json.dumps(
                            {
                                "from": previous_state,
                                "to": {
                                    "confirmation_status": (state.confirmation_status),
                                    "manual_stage": state.manual_stage,
                                    "owner_key": state.owner_key,
                                    "version": state.version,
                                },
                            }
                        ),
                        "created_at": now,
                    },
                )

                return state

    def promote_sales_opportunity(
        self,
        *,
        sales_opportunity_id: str,
        source_opportunity_id: str,
        title: str,
        owner_key: str,
        operator: str,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> SalesOpportunity:
        pg = require_psycopg()
        now = _utcnow()

        with postgres_write_connection(self._settings) as conn:
            with conn.cursor(row_factory=pg.rows.dict_row) as cur:
                replay_result_id = _claim_idempotency(
                    cur,
                    operator=operator,
                    idempotency_key=idempotency_key,
                    command_kind="sales_opportunity_promote",
                    request_fingerprint=request_fingerprint,
                )

                if replay_result_id is not None:
                    cur.execute(
                        """
                        SELECT *
                        FROM commercial.sales_opportunity
                        WHERE sales_opportunity_id =
                          %(sales_opportunity_id)s
                        LIMIT 1
                        """,
                        {
                            "sales_opportunity_id": replay_result_id,
                        },
                    )

                    replay = cur.fetchone()

                    if replay is None:
                        raise CommercialOperationConflictError(
                            "Idempotency result sales opportunity is missing"
                        )

                    return SalesOpportunity(**dict(replay))

                # PR3 is consulted only as the machine/evidence source.
                # Its identity references are snapshotted into durable CRM
                # state; no FK is created back to this replaceable view.
                cur.execute(
                    """
                    SELECT
                      account_id,
                      primary_contact_id
                    FROM api.v_commercial_opportunity
                    WHERE opportunity_id =
                      %(source_opportunity_id)s
                    LIMIT 1
                    """,
                    {
                        "source_opportunity_id": (source_opportunity_id),
                    },
                )

                source = cur.fetchone()

                if source is None:
                    raise CommercialOperationNotFoundError(
                        f"Commercial opportunity not found: {source_opportunity_id}"
                    )

                cur.execute(
                    """
                    INSERT INTO commercial.sales_opportunity (
                      sales_opportunity_id,
                      source_kind,
                      source_opportunity_id,
                      account_id,
                      primary_contact_id,
                      title,
                      stage,
                      owner_key,
                      version,
                      created_by,
                      updated_by,
                      created_at,
                      updated_at
                    )
                    VALUES (
                      %(sales_opportunity_id)s,
                      'pr3',
                      %(source_opportunity_id)s,
                      %(account_id)s,
                      %(primary_contact_id)s,
                      %(title)s,
                      'new',
                      %(owner_key)s,
                      1,
                      %(operator)s,
                      %(operator)s,
                      %(created_at)s,
                      %(created_at)s
                    )
                    ON CONFLICT (
                      source_kind,
                      source_opportunity_id
                    )
                    DO NOTHING
                    RETURNING *
                    """,
                    {
                        "sales_opportunity_id": (sales_opportunity_id),
                        "source_opportunity_id": (source_opportunity_id),
                        "account_id": source["account_id"],
                        "primary_contact_id": (source["primary_contact_id"]),
                        "title": title,
                        "owner_key": owner_key,
                        "operator": operator,
                        "created_at": now,
                    },
                )

                row = cur.fetchone()

                if row is None:
                    cur.execute(
                        """
                        SELECT sales_opportunity_id
                        FROM commercial.sales_opportunity
                        WHERE source_kind = 'pr3'
                          AND source_opportunity_id =
                            %(source_opportunity_id)s
                        LIMIT 1
                        """,
                        {
                            "source_opportunity_id": (source_opportunity_id),
                        },
                    )

                    existing = cur.fetchone()

                    if existing is not None:
                        raise CommercialOperationConflictError(
                            "Commercial opportunity already promoted: "
                            f"{source_opportunity_id}"
                        )

                    raise RuntimeError("Sales opportunity insert returned no row")

                result = SalesOpportunity(**dict(row))

                cur.execute(
                    """
                    INSERT INTO commercial.sales_opportunity_event (
                      event_id,
                      sales_opportunity_id,
                      event_type,
                      actor_key,
                      payload,
                      created_at
                    )
                    VALUES (
                      %(event_id)s,
                      %(sales_opportunity_id)s,
                      'created',
                      %(actor_key)s,
                      %(payload)s,
                      %(created_at)s
                    )
                    """,
                    {
                        "event_id": str(uuid.uuid4()),
                        "sales_opportunity_id": (sales_opportunity_id),
                        "actor_key": operator,
                        "payload": json.dumps(
                            {
                                "source": {
                                    "kind": "pr3",
                                    "opportunity_id": (source_opportunity_id),
                                },
                                "snapshot": {
                                    "account_id": result.account_id,
                                    "primary_contact_id": (result.primary_contact_id),
                                },
                                "opportunity": {
                                    "title": result.title,
                                    "stage": result.stage,
                                    "owner_key": (result.owner_key),
                                },
                            }
                        ),
                        "created_at": now,
                    },
                )

                _store_idempotency_result(
                    cur,
                    operator=operator,
                    idempotency_key=idempotency_key,
                    result_id=sales_opportunity_id,
                )

                return result

    def transition_sales_opportunity_stage(
        self,
        *,
        sales_opportunity_id: str,
        stage: str,
        operator: str,
        expected_version: int,
    ) -> SalesOpportunity:
        if stage not in SALES_OPPORTUNITY_STAGES:
            raise ValueError(f"Unsupported sales opportunity stage: {stage!r}")

        pg = require_psycopg()
        now = _utcnow()

        with postgres_write_connection(self._settings) as conn:
            with conn.cursor(row_factory=pg.rows.dict_row) as cur:
                # Serialize lifecycle mutation for this durable CRM row.
                # The version check protects clients from silently writing
                # over a state they did not read.
                cur.execute(
                    """
                    SELECT *
                    FROM commercial.sales_opportunity
                    WHERE sales_opportunity_id =
                      %(sales_opportunity_id)s
                    FOR UPDATE
                    """,
                    {
                        "sales_opportunity_id": sales_opportunity_id,
                    },
                )

                existing = cur.fetchone()

                if existing is None:
                    raise CommercialOperationNotFoundError(
                        f"Sales opportunity not found: {sales_opportunity_id}"
                    )

                current_stage = str(existing["stage"])
                current_version = int(existing["version"])

                if expected_version != current_version:
                    raise CommercialOperationConflictError(
                        "Sales opportunity version conflict"
                    )

                if current_stage in SALES_OPPORTUNITY_TERMINAL_STAGES:
                    raise CommercialOperationConflictError(
                        "Sales opportunity is terminal and cannot change stage"
                    )

                if stage == current_stage:
                    raise CommercialOperationConflictError(
                        "Sales opportunity is already in the requested stage"
                    )

                cur.execute(
                    """
                    UPDATE commercial.sales_opportunity
                    SET
                      stage = %(stage)s,
                      version = version + 1,
                      updated_by = %(operator)s,
                      updated_at = %(now)s
                    WHERE sales_opportunity_id =
                      %(sales_opportunity_id)s
                      AND version = %(current_version)s
                    RETURNING *
                    """,
                    {
                        "sales_opportunity_id": sales_opportunity_id,
                        "stage": stage,
                        "operator": operator,
                        "now": now,
                        "current_version": current_version,
                    },
                )

                row = cur.fetchone()

                if row is None:
                    raise CommercialOperationConflictError(
                        "Sales opportunity concurrent update conflict"
                    )

                result = SalesOpportunity(**dict(row))

                cur.execute(
                    """
                    INSERT INTO commercial.sales_opportunity_event (
                      event_id,
                      sales_opportunity_id,
                      event_type,
                      actor_key,
                      payload,
                      created_at
                    )
                    VALUES (
                      %(event_id)s,
                      %(sales_opportunity_id)s,
                      'stage_changed',
                      %(actor_key)s,
                      %(payload)s,
                      %(created_at)s
                    )
                    """,
                    {
                        "event_id": str(uuid.uuid4()),
                        "sales_opportunity_id": sales_opportunity_id,
                        "actor_key": operator,
                        "payload": json.dumps(
                            {
                                "from": {
                                    "stage": current_stage,
                                    "version": current_version,
                                },
                                "to": {
                                    "stage": result.stage,
                                    "version": result.version,
                                },
                            }
                        ),
                        "created_at": now,
                    },
                )

                return result

    def create_activity(
        self,
        *,
        activity_id: str,
        opportunity_id: str | None,
        sales_opportunity_id: str | None = None,
        account_id: str | None = None,
        contact_id: str | None,
        activity_type: str,
        occurred_at: datetime,
        summary: str,
        detail: str | None,
        operator: str,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> Activity:
        pg = require_psycopg()

        with postgres_write_connection(self._settings) as conn:
            with conn.cursor(row_factory=pg.rows.dict_row) as cur:
                replay_result_id = _claim_idempotency(
                    cur,
                    operator=operator,
                    idempotency_key=idempotency_key,
                    command_kind="activity_create",
                    request_fingerprint=request_fingerprint,
                )

                if replay_result_id is not None:
                    cur.execute(
                        """
                        SELECT *
                        FROM commercial.activity
                        WHERE activity_id = %(activity_id)s
                        LIMIT 1
                        """,
                        {
                            "activity_id": replay_result_id,
                        },
                    )

                    replay = cur.fetchone()

                    if replay is None:
                        raise CommercialOperationConflictError(
                            "Idempotency result activity is missing"
                        )

                    return Activity(**dict(replay))

                resolved_opportunity_id = _resolve_sales_opportunity_source(
                    cur,
                    sales_opportunity_id=sales_opportunity_id,
                    opportunity_id=opportunity_id,
                )

                cur.execute(
                    """
                    INSERT INTO commercial.activity (
                      activity_id,
                      opportunity_id,
                      sales_opportunity_id,
                      account_id,
                      contact_id,
                      activity_type,
                      occurred_at,
                      summary,
                      detail,
                      created_by
                    )
                    SELECT
                      %(activity_id)s,
                      %(opportunity_id)s,
                      %(sales_opportunity_id)s,
                      %(account_id)s,
                      %(contact_id)s,
                      %(activity_type)s,
                      %(occurred_at)s,
                      %(summary)s,
                      %(detail)s,
                      %(operator)s
                    WHERE
                      CAST(%(sales_opportunity_id)s AS text) IS NOT NULL
                      OR CAST(%(opportunity_id)s AS text) IS NULL
                      OR EXISTS (
                        SELECT 1
                        FROM api.v_commercial_opportunity
                        WHERE opportunity_id = %(opportunity_id)s
                      )
                    RETURNING *
                    """,
                    {
                        "activity_id": activity_id,
                        "opportunity_id": resolved_opportunity_id,
                        "sales_opportunity_id": sales_opportunity_id,
                        "account_id": account_id,
                        "contact_id": contact_id,
                        "activity_type": activity_type,
                        "occurred_at": occurred_at,
                        "summary": summary,
                        "detail": detail,
                        "operator": operator,
                    },
                )

                row = cur.fetchone()

                if row is None:
                    if opportunity_id is not None:
                        raise CommercialOperationNotFoundError(
                            f"Commercial opportunity not found: {opportunity_id}"
                        )

                    raise RuntimeError("Activity insert returned no row")

                _store_idempotency_result(
                    cur,
                    operator=operator,
                    idempotency_key=idempotency_key,
                    result_id=activity_id,
                )

                return Activity(**dict(row))

    def create_task(
        self,
        *,
        task_id: str,
        opportunity_id: str | None,
        sales_opportunity_id: str | None = None,
        account_id: str | None = None,
        contact_id: str | None,
        title: str,
        priority: str,
        due_at: datetime | None,
        owner_key: str | None,
        operator: str,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> Task:
        pg = require_psycopg()

        with postgres_write_connection(self._settings) as conn:
            with conn.cursor(row_factory=pg.rows.dict_row) as cur:
                replay_result_id = _claim_idempotency(
                    cur,
                    operator=operator,
                    idempotency_key=idempotency_key,
                    command_kind="task_create",
                    request_fingerprint=request_fingerprint,
                )

                if replay_result_id is not None:
                    cur.execute(
                        """
                        SELECT *
                        FROM commercial.task
                        WHERE task_id = %(task_id)s
                        LIMIT 1
                        """,
                        {
                            "task_id": replay_result_id,
                        },
                    )

                    replay = cur.fetchone()

                    if replay is None:
                        raise CommercialOperationConflictError(
                            "Idempotency result task is missing"
                        )

                    return Task(**dict(replay))

                resolved_opportunity_id = _resolve_sales_opportunity_source(
                    cur,
                    sales_opportunity_id=sales_opportunity_id,
                    opportunity_id=opportunity_id,
                )

                cur.execute(
                    """
                    INSERT INTO commercial.task (
                      task_id,
                      opportunity_id,
                      sales_opportunity_id,
                      account_id,
                      contact_id,
                      title,
                      status,
                      priority,
                      due_at,
                      owner_key,
                      version,
                      created_by,
                      updated_by
                    )
                    SELECT
                      %(task_id)s,
                      %(opportunity_id)s,
                      %(sales_opportunity_id)s,
                      %(account_id)s,
                      %(contact_id)s,
                      %(title)s,
                      'open',
                      %(priority)s,
                      %(due_at)s,
                      %(owner_key)s,
                      1,
                      %(operator)s,
                      %(operator)s
                    WHERE
                      CAST(%(sales_opportunity_id)s AS text) IS NOT NULL
                      OR CAST(%(opportunity_id)s AS text) IS NULL
                      OR EXISTS (
                        SELECT 1
                        FROM api.v_commercial_opportunity
                        WHERE opportunity_id = %(opportunity_id)s
                      )
                    RETURNING *
                    """,
                    {
                        "task_id": task_id,
                        "opportunity_id": resolved_opportunity_id,
                        "sales_opportunity_id": sales_opportunity_id,
                        "account_id": account_id,
                        "contact_id": contact_id,
                        "title": title,
                        "priority": priority,
                        "due_at": due_at,
                        "owner_key": owner_key,
                        "operator": operator,
                    },
                )

                row = cur.fetchone()

                if row is None:
                    if opportunity_id is not None:
                        raise CommercialOperationNotFoundError(
                            f"Commercial opportunity not found: {opportunity_id}"
                        )

                    raise RuntimeError("Task insert returned no row")

                _store_idempotency_result(
                    cur,
                    operator=operator,
                    idempotency_key=idempotency_key,
                    result_id=task_id,
                )

                return Task(**dict(row))

    def transition_task(
        self,
        *,
        task_id: str,
        status: str,
        operator: str,
        expected_version: int,
    ) -> Task:
        if status not in {"done", "cancelled"}:
            raise ValueError("Task transition status must be 'done' or 'cancelled'")

        pg = require_psycopg()
        now = _utcnow()
        completed_at = now if status == "done" else None

        with postgres_write_connection(self._settings) as conn:
            with conn.cursor(row_factory=pg.rows.dict_row) as cur:
                cur.execute(
                    """
                    UPDATE commercial.task
                    SET
                      status = %(status)s,
                      completed_at = %(completed_at)s,
                      version = version + 1,
                      updated_by = %(operator)s,
                      updated_at = %(now)s
                    WHERE task_id = %(task_id)s
                      AND status = 'open'
                      AND version = %(expected_version)s
                    RETURNING *
                    """,
                    {
                        "task_id": task_id,
                        "status": status,
                        "completed_at": completed_at,
                        "operator": operator,
                        "now": now,
                        "expected_version": expected_version,
                    },
                )

                row = cur.fetchone()
                if row is not None:
                    return Task(**dict(row))

                cur.execute(
                    """
                    SELECT task_id, status, version
                    FROM commercial.task
                    WHERE task_id = %(task_id)s
                    LIMIT 1
                    """,
                    {"task_id": task_id},
                )
                existing = cur.fetchone()

                if existing is None:
                    raise CommercialOperationNotFoundError(
                        f"Commercial task not found: {task_id}"
                    )

                raise CommercialOperationConflictError(
                    "Task is no longer open or its version changed"
                )
