"""The worker's job queue: Procrastinate on the V2 database, schema `procrastinate`
(supabase/migrations/20261006180000; docs/ARCHITECTURE.md §8, owner decision D2).

Two tasks, both on the `triage` queue:

* `sweep_untriaged` — periodic, every minute: finds captured messages of the last
  `DEFAULT_SINCE_DAYS` days that have no reading of the current triage version and defers one
  `triage_message` job each. Capture itself never enqueues triage; the separate capture process writes mail and this sweep is
  how new mail reaches the queue, at most a minute late.
* `triage_message(source_record_id)` — one message, end to end (`triage.triage_one`). A queueing
  lock per source record keeps one job per message waiting at a time; a transient model failure
  is retried with backoff; a permanent one is recorded and not retried.

Every connection the queue opens — pooled, and the standalone LISTEN connection — runs the
worker's own probe first (`database.averify_worker_connection`) for the queue's grants and
policies, then pins `search_path = procrastinate, pg_catalog` for Procrastinate's unqualified SQL.
A session that is not exactly `origenlab_worker`, or that could write crm/outbound, is refused
before Procrastinate issues a statement. The triage's own reads and writes use a separate,
synchronously verified connection per job (`database.open_verified_connection`).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import procrastinate
import psycopg
from procrastinate import testing as procrastinate_testing
from procrastinate.manager import QUEUEING_LOCK_CONSTRAINT

from origenlab_worker.database import (
    QUEUE_REQUIRED_POLICIES,
    QUEUE_REQUIRED_PRIVILEGES,
    TRIAGE_REQUIRED_POLICIES,
    TRIAGE_REQUIRED_PRIVILEGES,
    WorkerTarget,
    averify_worker_connection,
    open_verified_connection,
)
from origenlab_worker.triage import (
    DEFAULT_SINCE_DAYS,
    EmlStore,
    ModelSettings,
    ModelUnavailable,
    TriageDb,
    triage_one,
)

QUEUE = "triage"
APP_NAME_QUEUE = "origenlab-worker-queue"
APP_NAME_TRIAGE = "origenlab-worker-triage"
SEARCH_PATH = "procrastinate, pg_catalog"
SWEEP_LIMIT = 200
#: A transient model failure (network, 429, 5xx) is retried 5 times, 30 s + 90 s per attempt
#: before it: 30, 120, 210, 300, 390 s — about 17 minutes in all. (Procrastinate's
#: `exponential_wait` is `base ** (attempts + 1)`: 30 would mean 7.5 hours by the third try.)
#: Anything else fails the job at once; the sweep re-enqueues the message a minute later.
RETRY = procrastinate.RetryStrategy(max_attempts=6, wait=30, linear_wait=90, retry_exceptions={ModelUnavailable})


@dataclass
class QueueContext:
    """What the tasks need at run time, set once by the CLI before the worker starts."""

    target: WorkerTarget | None = field(default=None, repr=False)
    store_factory: Callable[[], EmlStore] | None = field(default=None, repr=False)
    model: ModelSettings | None = field(default=None, repr=False)
    revision: str | None = None
    log: Callable[[dict[str, Any]], None] = lambda event: print(json.dumps(event, sort_keys=True), file=sys.stdout,
                                                                  flush=True)


CONTEXT = QueueContext()


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


def build_app(connector: Any | None = None) -> procrastinate.App:
    """The app with both tasks registered. `connector` defaults to an in-memory one (tests, and
    import time); the CLI replaces it with :func:`connector_for` via `App.replace_connector`."""
    app = procrastinate.App(connector=connector or procrastinate_testing.InMemoryConnector())

    @app.task(name="triage_message", queue=QUEUE, retry=RETRY, pass_context=False)
    def triage_message(source_record_id: str) -> None:
        ctx = CONTEXT
        if ctx.target is None or ctx.store_factory is None or ctx.model is None:
            raise RuntimeError("queue_context_not_configured")
        with open_verified_connection(ctx.target, application_name=APP_NAME_TRIAGE,
                                      privileges=TRIAGE_REQUIRED_PRIVILEGES,
                                      policies=TRIAGE_REQUIRED_POLICIES) as conn:
            result = triage_one(TriageDb(conn), ctx.store_factory(), source_record_id, ctx.model)
        ctx.log({"event": "triage_message", "outcome": result.outcome, "class": result.triage_class,
                 "model": result.model, "mentions": result.mentions})

    @app.periodic(cron="* * * * *", periodic_id="sweep")
    @app.task(name="sweep_untriaged", queue=QUEUE, pass_context=True)
    def sweep_untriaged(context: procrastinate.JobContext, timestamp: int) -> None:
        ctx = CONTEXT
        if ctx.target is None:
            raise RuntimeError("queue_context_not_configured")
        with open_verified_connection(ctx.target, application_name=APP_NAME_TRIAGE,
                                      privileges=TRIAGE_REQUIRED_PRIVILEGES,
                                      policies=TRIAGE_REQUIRED_POLICIES) as conn:
            pending = TriageDb(conn).pending(since_days=DEFAULT_SINCE_DAYS, limit=SWEEP_LIMIT)
        # The app actually running this job (the CLI's, with the real connector), not this builder's.
        deferred = defer_triage(context.app, pending)
        ctx.log({"event": "triage_sweep", "found": len(pending), "deferred": deferred,
                 "revision": ctx.revision})

    return app


def defer_triage(app: procrastinate.App, source_record_ids: list[str]) -> int:
    """One `triage_message` job per id; an id whose job is already waiting is skipped."""
    task = app.tasks["triage_message"]
    deferred = 0
    for sid in source_record_ids:
        try:
            task.configure(queueing_lock=f"triage:{sid}").defer(source_record_id=sid)
        except procrastinate.exceptions.AlreadyEnqueued:
            continue
        deferred += 1
    return deferred


app = build_app()

__all__ = ["APP_NAME_QUEUE", "APP_NAME_TRIAGE", "CONTEXT", "QUEUE", "RETRY", "SEARCH_PATH", "QueueContext", "app",
           "build_app", "connector_for", "defer_triage", "pool_configure"]
