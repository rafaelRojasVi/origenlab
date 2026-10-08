"""Actual worker-role grants, rolling overlap, periodic dedupe and orphan recovery.

Each test module owns a newly created disposable database; no external APIs used.
"""
import asyncio

import pytest
import procrastinate
from procrastinate import periodic

from origenlab_worker import capture_queue as capture
from origenlab_worker.database import local_test_target
from origenlab_worker.task_queue import connector_for, build_app
from v2_command_harness import build_disposable_database, needs_worker_db, worker_dsn

pytestmark = needs_worker_db


@pytest.fixture(scope="module")
def target():
    for dsn in build_disposable_database():
        yield local_test_target(worker_dsn(dsn))


def test_two_live_cycles_share_a_session_lock_and_cancellation_releases_it(target):
    async def exercise():
        async with capture.cycle_lock(target) as first:
            assert first
            async with capture.cycle_lock(target) as second:
                assert not second
        async with capture.cycle_lock(target) as next_run:
            assert next_run
        started = asyncio.Event()
        async def interrupted():
            async with capture.cycle_lock(target) as got:
                assert got
                started.set()
                await asyncio.Future()
        running = asyncio.create_task(interrupted())
        await started.wait()
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        async with capture.cycle_lock(target) as after_cancel:
            assert after_cancel
    asyncio.run(exercise())


def test_rls_masked_duplicate_also_keeps_triage_deduplication_safe(target):
    app=build_app(connector_for(target))
    async def exercise():
        async with app.open_async():
            task=app.tasks["triage_message"].configure(queueing_lock="triage:synthetic")
            await task.defer_async(source_record_id="synthetic")
            with pytest.raises(procrastinate.exceptions.AlreadyEnqueued):
                await task.defer_async(source_record_id="synthetic")
    asyncio.run(exercise())


def test_real_periodic_backlog_and_orphan_doing_do_not_block_next_cycle(target):
    app = capture.build_app(target, {capture.ENV_CAPTURE_SCHEDULER:"true"}, connector_for(target))
    async def exercise():
        async with app.open_async():
            task, = app.periodic_registry.periodic_tasks.values()
            deferrer = periodic.PeriodicDeferrer(registry=app.periodic_registry)
            await deferrer.defer_jobs([(task,1800000000),(task,1800000000),(task,1800000600)])
            first = await app.job_manager.fetch_job(queues=["capture"],worker_id=None)
            assert first is not None and first.lock is None
            assert await app.job_manager.fetch_job(queues=["capture"],worker_id=None) is None
            # Leave first in `doing`, like an abruptly killed old process. A fresh
            # cycle can be consumed; the session lock still enforces no overlap.
            await deferrer.defer_jobs([(task,1800001200)])
            second = await app.job_manager.fetch_job(queues=["capture"],worker_id=None)
            assert second is not None and second.id != first.id
            assert second.task_name == capture.TASK
    asyncio.run(exercise())
