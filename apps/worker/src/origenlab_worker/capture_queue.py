"""Pinned worker's one-minute Gmail check, then Drive → quote-read, on its own process/queue.

No new business flag is enabled. The existing commands retain their locks and
idempotency. A session lock covers the complete cycle (including rolling overlap).
No execution lock is attached to the queue job: an orphaned `doing` job must not
block the next periodic cycle forever. Historical jobs are never replayed.
"""
from __future__ import annotations

import asyncio
import functools
import json
import os
import re
import sys
import time
from collections.abc import Mapping
from contextlib import asynccontextmanager, suppress
from typing import Any

import procrastinate
import psycopg
from procrastinate import testing

from origenlab_worker.database import (
    QUEUE_REQUIRED_POLICIES, QUEUE_REQUIRED_PRIVILEGES, AsyncVerifiedConnectionCache, WorkerTarget,
    averify_worker_connection, remote_worker_target, write_ca_file,
)
from origenlab_worker.errors import ConfigRefused
from origenlab_worker.flags import ENV_CAPTURE_SCHEDULER, capture_enabled
from origenlab_worker.queue_connector import connector_for

QUEUE = "capture"
TASK = "capture_mail_cycle"
PERIODIC_ID = "gmail-drive-v1"
#: Gmail is checked every minute (was every ten, the legacy cron's): on normal days the poll interval
#: was nearly all of the arrival-to-Hoy delay (measured 2026-10-09..13: cycle ~15 s, fetch p50 3–7 min).
#: Drive filing and quote reading follow only when the check stored new mail, on the first cycle of
#: the process (so a release's postflight sees a complete cycle), or after FOLLOWUP_EVERY seconds —
#: a quiet minute costs one short command. The session lock and the one-waiting-job queueing lock
#: keep a long cycle from overlapping the next.
CAPTURE_CRON = "* * * * *"
FOLLOWUP_EVERY = 600


class Followups:
    """When Drive filing and quote reading last ran in this process (monotonic seconds)."""

    def __init__(self) -> None:
        self.last: float | None = None

    def due(self, stored: int, now: float) -> bool:
        return stored > 0 or self.last is None or now - self.last >= FOLLOWUP_EVERY


FOLLOWUPS = Followups()
CYCLE_LOCK = "origenlab-worker:capture-cycle:v1"
STAGE_TIMEOUT = 300
CYCLE_TIMEOUT = 540
RETRY_DELAYS = (30, 90)
COUNT_FIELDS = ("seen", "stored", "evidence", "bulk_sends", "duplicates", "skipped_draft",
                "skipped_spam", "skipped_trash", "gone", "too_large", "parse_failed",
                "candidates", "filed", "reused", "skipped", "refused", "elapsed_ms", "max_rss_mb",
                "read", "lines", "input_tokens", "output_tokens")
#: Each child command: the event its one report line must carry, and the modes that count as done.
STAGES = {
    "gmail-sync": ("gmail_sync", {"history", "resync"}),
    "drive-file": ("drive_file", {"file", "paused"}),
    "quote-read": ("quote_read", {"read", "paused", "locked"}),
}


class CaptureTransient(RuntimeError):
    """Sanitised transient failure; retry current cycle at most twice."""


class CaptureRefused(RuntimeError):
    """Auth/configuration failure; no immediate queue retry."""


enabled = capture_enabled


def target_from_env(env: Mapping[str, str]) -> WorkerTarget:
    if env.get("ORIGENLAB_WORKER_GMAIL_SYNC_ENABLED", "").strip().lower() != "true":
        raise ConfigRefused("capture_scheduler_requires_existing_gmail_flag")
    return remote_worker_target(
        env.get("ORIGENLAB_WORKER_DATABASE_URL", ""),
        expected_host=env.get("ORIGENLAB_WORKER_DATABASE_EXPECTED_HOST"),
        ca_file=write_ca_file(env.get("ORIGENLAB_WORKER_DATABASE_CA_PEM", "")),
    )


@asynccontextmanager
async def cycle_lock(
    target: WorkerTarget,
    *,
    _cache: AsyncVerifiedConnectionCache | None = None,
    _process_lock: asyncio.Lock | None = None,
):
    """Acquire the per-process capture-cycle advisory lock.

    **With a cache and process lock** (production, set up by :func:`run_capture_worker`):
    the async connection is verified once and reused; an in-process ``asyncio.Lock``
    prevents re-entrancy within the same process (same session) while the PostgreSQL
    session-level advisory lock prevents concurrent cycles in *other* processes.  The
    advisory lock is explicitly released on exit so the connection stays alive for the
    next cycle.

    **Without a cache** (tests using the legacy interface): a fresh verified connection
    is opened for every call, exactly as before, so all existing tests remain valid.
    """
    if _cache is not None and _process_lock is not None:
        # New behaviour: persistent connection + asyncio.Lock for in-process exclusion.
        if _process_lock.locked():
            yield False
            return
        async with _process_lock:
            conn = await _cache.get()
            row = await (await conn.execute(
                "select pg_try_advisory_lock(hashtextextended(%s, 0))", (CYCLE_LOCK,),
            )).fetchone()
            acquired = bool(row[0])
            try:
                yield acquired
            finally:
                if acquired:
                    with suppress(Exception):
                        await conn.execute(
                            "select pg_advisory_unlock(hashtextextended(%s, 0))", (CYCLE_LOCK,),
                        )
                    if conn.closed or conn.broken:
                        _cache.invalidate()
    else:
        # Legacy behaviour: fresh connection per call (advisory lock released on connection close).
        async with await psycopg.AsyncConnection.connect(
            target.dsn, autocommit=True, application_name="origenlab-worker-capture-cycle",
            **target.connect_options,
        ) as conn:
            await averify_worker_connection(conn, target, privileges=QUEUE_REQUIRED_PRIVILEGES,
                                            policies=QUEUE_REQUIRED_POLICIES)
            row = await (await conn.execute(
                "select pg_try_advisory_lock(hashtextextended(%s, 0))", (CYCLE_LOCK,),
            )).fetchone()
            # Closing the connection releases the session lock, also after cancellation.
            yield bool(row[0])


async def run_stage(command: str, env: Mapping[str, str]) -> dict[str, Any]:
    """Run the existing CLI with a bound; never forward raw child output/errors."""
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "origenlab_worker.cli", command,
        env=dict(env), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=STAGE_TIMEOUT)
    except (TimeoutError, asyncio.CancelledError):
        if process.returncode is None:
            process.kill()
        await process.wait()
        if asyncio.current_task().cancelling():
            raise
        raise CaptureTransient("capture_stage_timeout") from None
    if process.returncode == 1:
        raise CaptureTransient("capture_stage_failed")
    if process.returncode != 0:
        raise CaptureRefused("capture_stage_auth_or_config_refused")
    try:
        report = json.loads(stdout.decode().strip())
        expected, allowed = STAGES[command]
        if report.get("event") != expected or report.get("exit") != 0 or report.get("mode") not in allowed:
            raise ValueError
    except (ValueError, UnicodeError, AttributeError):
        raise CaptureRefused("capture_stage_not_completed") from None
    # Only validated constant modes leave this subprocess boundary.
    return {"mode": report["mode"], "counts": {
        key: report[key] for key in COUNT_FIELDS
        if type(report.get(key)) is int and report[key] >= 0
    }}


async def run_cycle(target: WorkerTarget, env: Mapping[str, str], *,
                    stage=run_stage, lock=cycle_lock, log=None, followups: Followups | None = None,
                    clock=time.monotonic) -> None:
    followups = FOLLOWUPS if followups is None else followups
    emit = log or (lambda event: print(json.dumps(event, sort_keys=True), flush=True))
    async with lock(target) as acquired:
        if not acquired:
            emit({"event": "capture_cycle", "outcome": "locked"})
            return
        try:
            async with asyncio.timeout(CYCLE_TIMEOUT):
                for attempt in range(len(RETRY_DELAYS) + 1):
                    try:
                        gmail = await stage("gmail-sync", env)
                        due = followups.due(gmail.get("counts", {}).get("stored", 0), clock())
                        # The former && contract: never file after failed capture.
                        drive = await stage("drive-file", env) if due else {"mode": "skipped"}
                        break
                    except CaptureTransient:
                        if attempt == len(RETRY_DELAYS):
                            raise
                        await asyncio.sleep(RETRY_DELAYS[attempt])
        except TimeoutError:
            raise CaptureTransient("capture_cycle_timeout") from None
        if due:
            quote = await read_quotes(stage, env)
            followups.last = clock()
        else:
            quote = {"mode": "skipped"}
        revision = env.get("RENDER_GIT_COMMIT", "")
        emit({"event": "capture_cycle", "outcome": "success",
              "revision": revision if re.fullmatch(r"[0-9a-f]{40}", revision) else None,
              "gmail_mode": gmail["mode"], "drive_mode": drive["mode"], "quote_mode": quote["mode"],
              "gmail_counts": gmail.get("counts", {}), "drive_counts": drive.get("counts", {}),
              "quote_counts": quote.get("counts", {}), "quote_error": quote.get("error")})


async def read_quotes(stage, env: Mapping[str, str]) -> dict[str, Any]:
    """The legacy cron's third command (`... && quote-read`): only after capture and filing
    succeeded. Mail is already committed, so a failure here neither fails nor retries the
    cycle; it is reported as `quote_mode: failed` and the next cycle reads again."""
    try:
        return await stage("quote-read", env)
    except (CaptureTransient, CaptureRefused) as exc:
        return {"mode": "failed", "error": str(exc), "counts": {}}


def build_app(target: WorkerTarget | None = None, env: Mapping[str, str] | None = None,
              connector=None, *, cycle=run_cycle) -> procrastinate.App:
    app = procrastinate.App(connector=connector or testing.InMemoryConnector())

    @app.periodic(cron=CAPTURE_CRON, periodic_id=PERIODIC_ID)
    # Retry inside the lock instead of moving the job back to TODO: another
    # periodic TODO could otherwise collide with its queueing lock during retry.
    @app.task(name=TASK, queue=QUEUE, queueing_lock=CYCLE_LOCK, retry=False)
    async def capture_mail_cycle(timestamp: int) -> None:
        if target is None or env is None or not enabled(env):
            raise CaptureRefused("capture_context_not_enabled")
        await cycle(target, env)

    return app


def run_capture_worker(env: Mapping[str, str]) -> int:
    # The supervisor does not spawn this process while the new gate is OFF.
    if not enabled(env):
        raise ConfigRefused("capture_scheduler_disabled")
    target = target_from_env(env)
    # Configuration failures must stop the supervised child immediately, rather
    # than leave a healthy-looking scheduler failing every minute.
    from origenlab_worker.cli import config_from_env, drive_config_from_env
    config_from_env(env)
    if env.get("ORIGENLAB_WORKER_DRIVE_FILING_ENABLED", "").strip().lower() == "true":
        drive_config_from_env(env)
    # Build a persistent verified connection for cycle_lock so TLS + SCRAM + the
    # permission probe run once per process instead of once per minute.
    conn_cache = AsyncVerifiedConnectionCache(
        target,
        application_name="origenlab-worker-capture-cycle",
        privileges=QUEUE_REQUIRED_PRIVILEGES,
        policies=QUEUE_REQUIRED_POLICIES,
    )
    proc_lock = asyncio.Lock()
    lock_fn = functools.partial(cycle_lock, _cache=conn_cache, _process_lock=proc_lock)
    app = build_app(target, env, connector_for(target),
                    cycle=functools.partial(run_cycle, lock=lock_fn))

    async def serve():
        async with app.open_async():
            from origenlab_worker.task_queue import WORKER_POLLING
            await app.run_worker_async(queues=[QUEUE], concurrency=1, name="capture",
                                       install_signal_handlers=True, delete_jobs="successful",
                                       shutdown_graceful_timeout=15, **WORKER_POLLING)
    asyncio.run(serve())
    return 0
