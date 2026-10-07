"""The queue wiring, on Procrastinate's in-memory connector: one waiting job per message, the
retry policy, the periodic sweep, and the task's refusal to run unconfigured."""

from __future__ import annotations

import procrastinate
import pytest
from procrastinate import testing

from origenlab_worker import task_queue
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
    periodic = list(built.periodic_registry.periodic_tasks.values())
    assert [(p.task.name, p.cron, p.periodic_id) for p in periodic] == [("sweep_untriaged", "* * * * *", "sweep")]


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

    monkeypatch.setattr(task_queue, "averify_worker_connection", fake_verify)
    asyncio.run(task_queue.pool_configure(object())(Conn()))
    assert calls == [("verify", 16, 16), ("execute", "set search_path to procrastinate, pg_catalog"), "commit"]


def test_the_module_app_imports_without_a_database() -> None:
    assert isinstance(task_queue.app, procrastinate.App)
