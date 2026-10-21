"""The procrastinate connection every queue process shares: the verified pool and the RLS-safe
connector. Apart from `task_queue` so the capture scheduler, which only needs a connection, does not
import triage and its models (2026-10-21, worker memory).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import procrastinate
import psycopg
from procrastinate.manager import QUEUEING_LOCK_CONSTRAINT

from origenlab_worker.database import (
    QUEUE_REQUIRED_POLICIES,
    QUEUE_REQUIRED_PRIVILEGES,
    WorkerTarget,
    averify_worker_connection,
)

APP_NAME_QUEUE = "origenlab-worker-queue"
SEARCH_PATH = "procrastinate, pg_catalog"


def pool_configure(target: WorkerTarget) -> Callable[[Any], Any]:
    async def configure(conn: Any) -> None:
        await averify_worker_connection(conn, target, privileges=QUEUE_REQUIRED_PRIVILEGES,
                                        policies=QUEUE_REQUIRED_POLICIES)
        await conn.execute(f"set search_path to {SEARCH_PATH}")
        if not conn.autocommit:
            await conn.commit()  # the pool wants the connection back idle

    return configure


class RlsQueueConnector(procrastinate.PsycopgConnector):
    """3.10 assumes UniqueViolation DETAIL is visible, but PostgreSQL RLS masks it.

    Recover only the known queueing-lock violation identified by PostgreSQL's
    constraint name. Never expose a value or broaden grants to obtain DETAIL.
    Other AssertionErrors/constraints retain their failure behavior.
    """
    @staticmethod
    def _masked_violation(exc):
        original = exc.__context__
        if (isinstance(original, psycopg.errors.UniqueViolation)
                and original.diag.constraint_name == QUEUEING_LOCK_CONSTRAINT
                and not original.diag.message_detail):
            raise procrastinate.exceptions.UniqueViolation(
                constraint_name=QUEUEING_LOCK_CONSTRAINT, queueing_lock=None,
            ) from None
        raise exc

    async def execute_query_one_async(self, query, **arguments):
        try:
            return await super().execute_query_one_async(query, **arguments)
        except AssertionError as exc:
            self._masked_violation(exc)

    async def execute_query_all_async(self, query, **arguments):
        try:
            return await super().execute_query_all_async(query, **arguments)
        except AssertionError as exc:
            self._masked_violation(exc)


def connector_for(target: WorkerTarget, *, max_size: int = 3) -> procrastinate.PsycopgConnector:
    return RlsQueueConnector(
        conninfo=target.dsn,
        kwargs={"application_name": APP_NAME_QUEUE, **target.connect_options},
        min_size=1,
        max_size=max_size,
        configure=pool_configure(target),
    )


__all__ = ["APP_NAME_QUEUE", "SEARCH_PATH", "RlsQueueConnector", "connector_for", "pool_configure"]
