"""Pinned worker's ten-minute Gmail → Drive cycle, on its own process/queue.

No new business flag is enabled. The existing commands retain their locks and
idempotency. A session lock covers the complete cycle (including rolling overlap).
No execution lock is attached to the queue job: an orphaned `doing` job must not
block the next periodic cycle forever. Historical jobs are never replayed.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from collections.abc import Mapping
from contextlib import asynccontextmanager
from typing import Any

import procrastinate
import psycopg
from procrastinate import testing

from origenlab_worker.database import (
    QUEUE_REQUIRED_POLICIES, QUEUE_REQUIRED_PRIVILEGES, WorkerTarget,
    averify_worker_connection, remote_worker_target, write_ca_file,
)
from origenlab_worker.errors import ConfigRefused
from origenlab_worker.task_queue import connector_for

ENV_CAPTURE_SCHEDULER = "ORIGENLAB_WORKER_CAPTURE_SCHEDULER_ENABLED"
QUEUE = "capture"
TASK = "capture_mail_cycle"
PERIODIC_ID = "gmail-drive-v1"
CYCLE_LOCK = "origenlab-worker:capture-cycle:v1"
STAGE_TIMEOUT = 300
CYCLE_TIMEOUT = 540
RETRY_DELAYS = (30, 90)
COUNT_FIELDS = ("seen", "stored", "evidence", "bulk_sends", "duplicates", "skipped_draft",
                "skipped_spam", "skipped_trash", "gone", "too_large", "parse_failed",
                "candidates", "filed", "reused", "skipped", "refused", "elapsed_ms", "max_rss_mb")


class CaptureTransient(RuntimeError):
    """Sanitised transient failure; retry current cycle at most twice."""


class CaptureRefused(RuntimeError):
    """Auth/configuration failure; no immediate queue retry."""


def enabled(env: Mapping[str, str]) -> bool:
    return env.get(ENV_CAPTURE_SCHEDULER, "").strip().lower() == "true"


def target_from_env(env: Mapping[str, str]) -> WorkerTarget:
    if env.get("ORIGENLAB_WORKER_GMAIL_SYNC_ENABLED", "").strip().lower() != "true":
        raise ConfigRefused("capture_scheduler_requires_existing_gmail_flag")
    return remote_worker_target(
        env.get("ORIGENLAB_WORKER_DATABASE_URL", ""),
        expected_host=env.get("ORIGENLAB_WORKER_DATABASE_EXPECTED_HOST"),
        ca_file=write_ca_file(env.get("ORIGENLAB_WORKER_DATABASE_CA_PEM", "")),
    )


@asynccontextmanager
async def cycle_lock(target: WorkerTarget):
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
        allowed = {"history", "resync"} if command == "gmail-sync" else {"file", "paused"}
        expected = "gmail_sync" if command == "gmail-sync" else "drive_file"
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
                    stage=run_stage, lock=cycle_lock, log=None) -> None:
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
                        # The former && contract: never file after failed capture.
                        drive = await stage("drive-file", env)
                        break
                    except CaptureTransient:
                        if attempt == len(RETRY_DELAYS):
                            raise
                        await asyncio.sleep(RETRY_DELAYS[attempt])
        except TimeoutError:
            raise CaptureTransient("capture_cycle_timeout") from None
        revision = env.get("RENDER_GIT_COMMIT", "")
        emit({"event": "capture_cycle", "outcome": "success",
              "revision": revision if re.fullmatch(r"[0-9a-f]{40}", revision) else None,
              "gmail_mode": gmail["mode"], "drive_mode": drive["mode"],
              "gmail_counts": gmail.get("counts", {}), "drive_counts": drive.get("counts", {})})


def build_app(target: WorkerTarget | None = None, env: Mapping[str, str] | None = None,
              connector=None, *, cycle=run_cycle) -> procrastinate.App:
    app = procrastinate.App(connector=connector or testing.InMemoryConnector())

    @app.periodic(cron="*/10 * * * *", periodic_id=PERIODIC_ID)
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
    # than leave a healthy-looking scheduler failing every ten minutes.
    from origenlab_worker.cli import config_from_env, drive_config_from_env
    config_from_env(env)
    if env.get("ORIGENLAB_WORKER_DRIVE_FILING_ENABLED", "").strip().lower() == "true":
        drive_config_from_env(env)
    app = build_app(target, env, connector_for(target))

    async def serve():
        async with app.open_async():
            await app.run_worker_async(queues=[QUEUE], concurrency=1, name="capture",
                                       install_signal_handlers=True, delete_jobs="successful",
                                       shutdown_graceful_timeout=15)
    asyncio.run(serve())
    return 0
