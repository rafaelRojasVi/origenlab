"""The worker's job queue: Procrastinate on the V2 database, schema `procrastinate`
(supabase/migrations/20261006180000; docs/ARCHITECTURE.md §8, owner decision D2).

Two tasks on the `triage` queue, served by the triage child (`app`):

* `sweep_untriaged` — periodic, every minute: finds captured messages of the last
  `DEFAULT_SINCE_DAYS` days that have no reading of the current triage version and defers one
  `triage_message` job each. Capture itself never enqueues triage; the separate capture process writes mail and this sweep is
  how new mail reaches the queue, at most a minute late.
* `triage_message(source_record_id)` — one message, end to end (`triage.triage_one`). A queueing
  lock per source record keeps one job per message waiting at a time; a transient model failure
  is retried with backoff; a permanent one is recorded and not retried.

Two tasks on the `tenders` queue, served by the tender child (`tender_app`, ruling R4):

* `bid_preparation_sweep` — periodic, every minute: claims queued tender bid preparations and runs
  them one at a time (`tender.job.run_bid_sweep`, through `QueueContext.bid_sweep`). A queueing lock
  keeps at most one sweep waiting.
* `tender_discovery` — periodic, every 15 minutes: reads Mercado Público's open tenders into
  `procurement.notice` for «En Mercado Público» (`tender.discovery.run_discovery`, through
  `QueueContext.discovery`). No model is called; a queueing lock keeps at most one waiting.

Each child's app carries only its own queue's tasks: a worker defers every periodic task of its app,
so a shared app would fill a queue no process serves while the other feature is off.

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
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import procrastinate
from procrastinate import testing as procrastinate_testing

from origenlab_worker.database import (
    TRIAGE_REQUIRED_POLICIES,
    TRIAGE_REQUIRED_PRIVILEGES,
    VerifiedConnectionCache,
    WorkerTarget,
    open_verified_connection,
)
from origenlab_worker.queue_connector import (
    APP_NAME_QUEUE,
    SEARCH_PATH,
    RlsQueueConnector,
    connector_for,
    pool_configure,
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
TENDER_QUEUE = "tenders"
BID_SWEEP_LOCK = "bid-preparation-sweep"
DISCOVERY_LOCK = "tender-discovery"
APP_NAME_TRIAGE = "origenlab-worker-triage"
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
    #: The tender child's sweep (`triage_cli.run_tender_worker` sets it): runs, returns counts.
    bid_sweep: Callable[[], dict[str, Any]] | None = field(default=None, repr=False)
    #: The tender child's «En Mercado Público» run (`triage_cli.discovery_for`): returns counts.
    discovery: Callable[[], dict[str, Any]] | None = field(default=None, repr=False)
    #: The prospect search sweep (`triage_cli.run_tender_worker` sets it): claims and runs a queued
    #: prospect search run, returns counts. None means the feature is off.
    prospect_search: Callable[[], dict[str, Any]] | None = field(default=None, repr=False)
    #: Long-lived verified connection for sweep_untriaged (one at a time; set by run_triage_worker).
    sweep_conn: VerifiedConnectionCache | None = field(default=None, repr=False)
    log: Callable[[dict[str, Any]], None] = lambda event: print(json.dumps(event, sort_keys=True), file=sys.stdout,
                                                                  flush=True)


#: Thread-local storage for per-thread triage_message connections.
_triage_locals: threading.local = threading.local()


CONTEXT = QueueContext()


def build_app(connector: Any | None = None, *, queues: tuple[str, ...] = (QUEUE, TENDER_QUEUE)) -> procrastinate.App:
    """The app with the tasks of `queues` registered. `connector` defaults to an in-memory one
    (tests, and import time); the CLI replaces it with :func:`connector_for` via
    `App.replace_connector`."""
    app = procrastinate.App(connector=connector or procrastinate_testing.InMemoryConnector())
    if TENDER_QUEUE in queues:
        _register_tender_tasks(app)
    if QUEUE not in queues:
        return app

    @app.task(name="triage_message", queue=QUEUE, retry=RETRY, pass_context=False)
    def triage_message(source_record_id: str) -> None:
        ctx = CONTEXT
        if ctx.target is None or ctx.store_factory is None or ctx.model is None:
            raise RuntimeError("queue_context_not_configured")
        # Per-thread verified connection: triage runs at concurrency=2, so each thread needs its own.
        # The cache is initialised lazily and reused across jobs in the same thread.
        import psycopg
        cache: VerifiedConnectionCache | None = getattr(_triage_locals, "cache", None)
        if cache is None or getattr(_triage_locals, "cache_target", None) is not ctx.target:
            cache = VerifiedConnectionCache(
                ctx.target,
                application_name=APP_NAME_TRIAGE,
                privileges=TRIAGE_REQUIRED_PRIVILEGES,
                policies=TRIAGE_REQUIRED_POLICIES,
            )
            _triage_locals.cache = cache
            _triage_locals.cache_target = ctx.target
        try:
            conn = cache.get()
            result = triage_one(TriageDb(conn), ctx.store_factory(), source_record_id, ctx.model)
        except (psycopg.OperationalError, psycopg.InterfaceError):
            cache.invalidate()
            raise
        ctx.log({"event": "triage_message", "outcome": result.outcome, "class": result.triage_class,
                 "model": result.model, "mentions": result.mentions})

    @app.periodic(cron="* * * * *", periodic_id="sweep")
    @app.task(name="sweep_untriaged", queue=QUEUE, pass_context=True)
    def sweep_untriaged(context: procrastinate.JobContext, timestamp: int) -> None:
        ctx = CONTEXT
        if ctx.target is None:
            raise RuntimeError("queue_context_not_configured")
        # Reuse the long-lived sweep connection; fall back to a per-call connection only if
        # sweep_conn was not set up (e.g. unit tests that only exercise the queue schema).
        if ctx.sweep_conn is not None:
            with ctx.sweep_conn.connection() as conn:
                pending = TriageDb(conn).pending(since_days=DEFAULT_SINCE_DAYS, limit=SWEEP_LIMIT)
        else:
            with open_verified_connection(ctx.target, application_name=APP_NAME_TRIAGE,
                                          privileges=TRIAGE_REQUIRED_PRIVILEGES,
                                          policies=TRIAGE_REQUIRED_POLICIES) as conn:
                pending = TriageDb(conn).pending(since_days=DEFAULT_SINCE_DAYS, limit=SWEEP_LIMIT)
        # The app actually running this job (the CLI's, with the real connector), not this builder's.
        deferred = defer_triage(context.app, pending)
        ctx.log({"event": "triage_sweep", "found": len(pending), "deferred": deferred,
                 "revision": ctx.revision})

    return app


PROSPECT_SWEEP_LOCK = "prospect-search-sweep"
#: Mercado Público discovery: hourly (was every 15 min until 2026-10-30). Tenders stay open for days;
#: each run sends the whole open list (~4,800 codes) to the database, so the cadence is bandwidth.
DISCOVERY_CRON = "0 * * * *"
#: Every queue worker (capture, triage, tenders). Library defaults poll every 5 s and beat every 10 s;
#: LISTEN/NOTIFY still hands a new job over at once, so slower polling only catches a missed notify.
#: The stalled timeout stays 3× the heartbeat, or a live worker would look dead.
WORKER_POLLING: dict[str, float] = {
    "fetch_job_polling_interval": 30.0,
    "abort_job_polling_interval": 30.0,
    "update_heartbeat_interval": 60.0,
    "stalled_worker_timeout": 180.0,
}
PROSPECT_DAILY_LOCK = "prospect-daily-enqueue"


def _register_tender_tasks(app: procrastinate.App) -> None:
    @app.periodic(cron="* * * * *", periodic_id="bids")
    @app.task(name="bid_preparation_sweep", queue=TENDER_QUEUE, queueing_lock=BID_SWEEP_LOCK, retry=False,
              pass_context=False)
    def bid_preparation_sweep(timestamp: int) -> None:
        ctx = CONTEXT
        if ctx.bid_sweep is None:
            raise RuntimeError("queue_context_not_configured")
        ctx.log({"event": "bid_preparation_sweep", **ctx.bid_sweep()})

    @app.periodic(cron=DISCOVERY_CRON, periodic_id="discovery")
    @app.task(name="tender_discovery", queue=TENDER_QUEUE, queueing_lock=DISCOVERY_LOCK, retry=False,
              pass_context=False)
    def tender_discovery(timestamp: int) -> None:
        ctx = CONTEXT
        if ctx.discovery is None:
            raise RuntimeError("queue_context_not_configured")
        ctx.log({"event": "tender_discovery", **ctx.discovery()})

    # ── Prospect search tasks ──────────────────────────────────────────────────────────────────
    # Daily at 09:00 UTC ≈ 06:00 CLT/CLST (America/Santiago is UTC-3 or UTC-4 depending on DST).
    # A queueing lock prevents a second enqueue if the previous daily run is still pending.
    @app.periodic(cron="0 9 * * *", periodic_id="prospect_daily")
    @app.task(name="prospect_daily_enqueue", queue=TENDER_QUEUE, queueing_lock=PROSPECT_DAILY_LOCK,
              retry=False, pass_context=False)
    def prospect_daily_enqueue(timestamp: int) -> None:
        """Insert a 'daily' queued run row if one does not already exist for today."""
        ctx = CONTEXT
        if ctx.target is None:
            raise RuntimeError("queue_context_not_configured")
        import json as _json
        from datetime import UTC as _UTC
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo as _ZI
        with open_verified_connection(ctx.target, application_name="origenlab-worker-triage",
                                      privileges=(), policies=frozenset()) as conn:
            with conn.cursor() as cur:
                today = _dt.now(_ZI("America/Santiago")).date().isoformat()
                cur.execute(
                    "insert into evidence.prospect_search_run (trigger, status) "
                    "values ('daily', 'queued') "
                    "on conflict do nothing",
                )
                ctx.log({"event": "prospect_daily_enqueue", "date": today})

    @app.periodic(cron="* * * * *", periodic_id="prospect_sweep")
    @app.task(name="prospect_search_sweep", queue=TENDER_QUEUE, queueing_lock=PROSPECT_SWEEP_LOCK,
              retry=False, pass_context=False)
    def prospect_search_sweep(timestamp: int) -> None:
        """Claim the oldest queued prospect search run and execute it in a subprocess."""
        ctx = CONTEXT
        if ctx.prospect_search is None:
            return  # feature off; leave queued runs pending
        ctx.log({"event": "prospect_search_sweep", **ctx.prospect_search()})


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


app = build_app(queues=(QUEUE,))
tender_app = build_app(queues=(TENDER_QUEUE,))

__all__ = ["APP_NAME_QUEUE", "APP_NAME_TRIAGE", "BID_SWEEP_LOCK", "CONTEXT",
           "PROSPECT_DAILY_LOCK", "PROSPECT_SWEEP_LOCK", "QUEUE", "RETRY", "SEARCH_PATH",
           "TENDER_QUEUE", "QueueContext", "app", "build_app", "connector_for", "defer_triage",
           "pool_configure", "tender_app", "_triage_locals"]
