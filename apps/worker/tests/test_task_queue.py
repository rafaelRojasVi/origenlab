"""The queue wiring, on Procrastinate's in-memory connector: one waiting job per message, the
retry policy, the periodic sweep, and the task's refusal to run unconfigured."""

from __future__ import annotations

import procrastinate
import pytest
from procrastinate import testing

from origenlab_worker import queue_connector, task_queue
from origenlab_worker.triage import ModelUnavailable


@pytest.fixture
def app():
    connector = testing.InMemoryConnector()
    built = task_queue.build_app(connector)
    with built.open():
        yield built, connector


def test_both_tasks_are_on_the_triage_queue_and_the_sweep_runs_every_minute(app) -> None:
    built, _ = app
    assert built.tasks["triage_message"].queue == built.tasks["sweep_untriaged"].queue == "triage"
    assert built.tasks["bid_preparation_sweep"].queue == "tenders"
    periodic = sorted(built.periodic_registry.periodic_tasks.values(), key=lambda p: p.periodic_id)
    assert [(p.task.name, p.cron, p.periodic_id) for p in periodic] == [
        ("bid_preparation_sweep", "* * * * *", "bids"), ("tender_discovery", "*/15 * * * *", "discovery"),
        ("sweep_untriaged", "* * * * *", "sweep")]


def test_each_child_app_carries_only_its_own_queue_so_no_periodic_job_waits_unserved() -> None:
    """A process defers every periodic task of its app: the triage child must not fill the
    `tenders` queue while tenders are off, nor the tender child the `triage` queue while triage is
    paused (ruling R4: tenders run in their own child)."""
    def own(app):
        return {name for name in app.tasks if "builtin" not in name}

    assert own(task_queue.app) == {"triage_message", "sweep_untriaged"}
    assert own(task_queue.tender_app) == {"bid_preparation_sweep", "tender_discovery"}
    bids, discovery = sorted(task_queue.tender_app.periodic_registry.periodic_tasks.values(),
                             key=lambda p: p.periodic_id)
    assert (bids.periodic_id, bids.task.queue) == ("bids", "tenders")
    assert (discovery.periodic_id, discovery.task.queue) == ("discovery", "tenders")
    # One waiting sweep at most, even if nobody serves the queue for a while.
    assert bids.task.queueing_lock == task_queue.BID_SWEEP_LOCK
    assert discovery.task.queueing_lock == task_queue.DISCOVERY_LOCK


def test_the_bid_sweep_refuses_to_run_before_the_cli_configured_it(monkeypatch) -> None:
    monkeypatch.setattr(task_queue, "CONTEXT", task_queue.QueueContext())
    built = task_queue.build_app(testing.InMemoryConnector())
    with pytest.raises(RuntimeError, match="queue_context_not_configured"):
        built.tasks["bid_preparation_sweep"](timestamp=0)


def test_the_bid_sweep_runs_the_configured_sweep_and_logs_its_counts(monkeypatch) -> None:
    logged = []
    ctx = task_queue.QueueContext(bid_sweep=lambda: {"outcome": "ran", "claimed": 1}, log=logged.append)
    monkeypatch.setattr(task_queue, "CONTEXT", ctx)
    built = task_queue.build_app(testing.InMemoryConnector(), queues=(task_queue.TENDER_QUEUE,))
    built.tasks["bid_preparation_sweep"](timestamp=0)
    assert logged == [{"event": "bid_preparation_sweep", "outcome": "ran", "claimed": 1}]


def test_the_discovery_refuses_to_run_unconfigured_and_logs_its_counts_once_configured(monkeypatch) -> None:
    monkeypatch.setattr(task_queue, "CONTEXT", task_queue.QueueContext())
    built = task_queue.build_app(testing.InMemoryConnector(), queues=(task_queue.TENDER_QUEUE,))
    with pytest.raises(RuntimeError, match="queue_context_not_configured"):
        built.tasks["tender_discovery"](timestamp=0)
    logged = []
    monkeypatch.setattr(task_queue, "CONTEXT", task_queue.QueueContext(
        discovery=lambda: {"outcome": "ran", "candidates": 3}, log=logged.append))
    built.tasks["tender_discovery"](timestamp=0)
    assert logged == [{"event": "tender_discovery", "outcome": "ran", "candidates": 3}]


def test_one_waiting_job_per_message(app) -> None:
    built, connector = app
    assert task_queue.defer_triage(built, ["a", "b", "a"]) == 2
    assert task_queue.defer_triage(built, ["a", "b"]) == 0
    jobs = sorted((j["queueing_lock"], j["args"]["source_record_id"]) for j in connector.jobs.values())
    assert jobs == [("triage:a", "a"), ("triage:b", "b")]


def test_only_a_model_outage_is_retried_with_a_bounded_backoff() -> None:
    from types import SimpleNamespace

    retry = task_queue.RETRY

    def wait(attempts, exc=ModelUnavailable("x")):
        decision = retry.get_retry_decision(exception=exc, job=SimpleNamespace(attempts=attempts))
        return None if decision is None else decision.retry_at

    assert retry.retry_exceptions == {ModelUnavailable}
    assert (retry.wait, retry.linear_wait, retry.exponential_wait) == (30, 90, 0)
    assert [retry.wait + retry.linear_wait * n for n in range(5)] == [30, 120, 210, 300, 390]
    assert wait(5) is not None and wait(6) is None
    assert wait(0, ValueError("x")) is None


def test_the_task_refuses_to_run_before_the_cli_configured_it(app, monkeypatch) -> None:
    built, _ = app
    monkeypatch.setattr(task_queue, "CONTEXT", task_queue.QueueContext())
    with pytest.raises(RuntimeError, match="queue_context_not_configured"):
        built.tasks["triage_message"](source_record_id="a")


def test_the_pool_pins_the_queue_search_path_after_verifying(monkeypatch) -> None:
    import asyncio

    calls: list[object] = []

    async def fake_verify(conn, target, *, privileges, policies):
        calls.append(("verify", len(privileges), len(policies)))

    class Conn:
        autocommit = False

        async def execute(self, sql):
            calls.append(("execute", sql))

        async def commit(self):
            calls.append("commit")

    monkeypatch.setattr(queue_connector, "averify_worker_connection", fake_verify)
    asyncio.run(task_queue.pool_configure(object())(Conn()))
    assert calls == [("verify", 16, 16), ("execute", "set search_path to procrastinate, pg_catalog"), "commit"]


def test_the_module_app_imports_without_a_database() -> None:
    assert isinstance(task_queue.app, procrastinate.App)


def test_the_periodic_sweep_defers_into_the_connector_the_cli_swaps_in() -> None:
    """Regression, 2026-10-07: with `with_connector` the hosted worker ran but its sweep jobs went to
    the module app's in-memory connector, so nothing was ever triaged."""
    import asyncio

    from procrastinate import periodic

    real = testing.InMemoryConnector()
    with task_queue.app.replace_connector(real) as swapped:
        async def tick() -> None:
            async with swapped.open_async():
                deferrer = periodic.PeriodicDeferrer(registry=swapped.periodic_registry)
                (sweep,) = swapped.periodic_registry.periodic_tasks.values()
                await deferrer.defer_jobs([(sweep, 1_800_000_000)])

        asyncio.run(tick())
    assert [j["task_name"] for j in real.jobs.values()] == ["sweep_untriaged"]
