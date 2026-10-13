"""Capture scheduling, safe retries, output redaction and process isolation."""
import asyncio
import json
import os
import signal
import subprocess
import sys
import threading
from contextlib import asynccontextmanager

import pytest
from procrastinate import periodic, testing

from origenlab_worker import capture_queue as capture, mail_worker


@asynccontextmanager
async def acquired(_target):
    yield True


@asynccontextmanager
async def held(_target):
    yield False


def test_default_off_does_not_start_capture_or_need_oauth(monkeypatch):
    calls = []
    monkeypatch.setattr(mail_worker, "supervise", lambda commands, env, **kw: calls.extend(commands) or 0)
    assert mail_worker.run_mail_worker({}) == 0
    assert [x[-1] for x in calls] == ["triage-child"]
    assert not capture.enabled({})
    assert not capture.enabled({capture.ENV_CAPTURE_SCHEDULER: "false"})


def test_existing_render_command_uses_supervisor_without_recursive_children(monkeypatch):
    from origenlab_worker import cli
    calls=[]
    monkeypatch.setattr(mail_worker,"run_mail_worker",lambda env: calls.append("supervisor") or 0)
    monkeypatch.setattr(cli,"run_triage_worker",lambda env,started: calls.append("triage") or 0)
    assert cli.main(["triage-worker"],environ={}) == 0
    assert cli.main(["triage-child"],environ={}) == 0
    assert calls == ["supervisor","triage"]


def test_rls_compatibility_guard_does_not_swallow_programmer_assertions():
    from origenlab_worker.task_queue import RlsQueueConnector
    with pytest.raises(AssertionError,match="unrelated"):
        RlsQueueConnector._masked_violation(AssertionError("unrelated"))


def test_new_gate_does_not_override_existing_gmail_pause():
    from origenlab_worker.errors import ConfigRefused
    with pytest.raises(ConfigRefused, match="requires_existing_gmail_flag"):
        capture.target_from_env({capture.ENV_CAPTURE_SCHEDULER: "true"})


def test_periodic_defer_is_deduplicated_and_backlog_is_bounded():
    connector = testing.InMemoryConnector()
    app = capture.build_app(connector=connector)
    async def ticks():
        async with app.open_async():
            task, = app.periodic_registry.periodic_tasks.values()
            assert (task.cron, task.periodic_id) == ("*/10 * * * *", capture.PERIODIC_ID)
            defer = periodic.PeriodicDeferrer(registry=app.periodic_registry)
            await defer.defer_jobs([(task, 1800000000), (task, 1800000000), (task, 1800000600)])
    asyncio.run(ticks())
    jobs = list(connector.jobs.values())
    assert len(jobs) == 1
    assert jobs[0]["queue_name"] == "capture"
    assert jobs[0]["queueing_lock"] == capture.CYCLE_LOCK
    assert jobs[0]["lock"] is None  # orphan `doing` does not block next period


MODES = {"gmail-sync": "history", "drive-file": "file", "quote-read": "read"}


def test_cycle_preserves_gmail_then_drive_then_quotes_and_does_not_expose_child_data():
    calls, events = [], []
    async def stage(command, env):
        calls.append(command)
        return {"mode": MODES[command], "private": "must-not-log"}
    asyncio.run(capture.run_cycle(object(), {}, stage=stage, lock=acquired, log=events.append))
    # The legacy cron's order: gmail-sync && drive-file && quote-read.
    assert calls == ["gmail-sync", "drive-file", "quote-read"]
    assert events == [{"event": "capture_cycle", "outcome": "success", "revision": None, "gmail_mode": "history",
                       "drive_mode": "file", "quote_mode": "read", "gmail_counts": {}, "drive_counts": {},
                       "quote_counts": {}, "quote_error": None}]


@pytest.mark.parametrize("failure", [capture.CaptureTransient("capture_stage_failed"),
                                     capture.CaptureRefused("capture_stage_auth_or_config_refused")])
def test_a_quote_read_failure_never_fails_or_retries_the_captured_cycle(failure):
    calls, events = [], []
    async def stage(command, env):
        calls.append(command)
        if command == "quote-read":
            raise failure
        return {"mode": MODES[command]}
    asyncio.run(capture.run_cycle(object(), {}, stage=stage, lock=acquired, log=events.append))
    assert calls == ["gmail-sync", "drive-file", "quote-read"]
    assert events[0]["outcome"] == "success" and events[0]["quote_mode"] == "failed"
    assert events[0]["quote_error"] == str(failure)


def test_a_failed_filing_never_reads_quotes():
    calls = []
    async def stage(command, env):
        calls.append(command)
        if command == "drive-file":
            raise capture.CaptureRefused("synthetic")
        return {"mode": MODES[command]}
    with pytest.raises(capture.CaptureRefused):
        asyncio.run(capture.run_cycle(object(), {}, stage=stage, lock=acquired))
    assert calls == ["gmail-sync", "drive-file"]


def test_capture_failure_never_files_and_lock_contention_never_runs():
    calls, events = [], []
    async def stage(command, env):
        calls.append(command)
        raise capture.CaptureTransient("synthetic")
    async def no_wait(delay): pass
    from unittest.mock import patch
    with patch.object(capture.asyncio,"sleep",no_wait), pytest.raises(capture.CaptureTransient):
        asyncio.run(capture.run_cycle(object(), {}, stage=stage, lock=acquired, log=events.append))
    assert calls == ["gmail-sync"] * 3 and events == []
    calls.clear()
    asyncio.run(capture.run_cycle(object(), {}, stage=stage, lock=held, log=events.append))
    assert calls == [] and events == [{"event": "capture_cycle", "outcome": "locked"}]


def test_retries_are_bounded_inside_cycle_and_auth_is_not_retried(monkeypatch):
    calls, waits = [], []
    async def transient(command,env):
        calls.append(command)
        raise capture.CaptureTransient("safe")
    async def no_wait(delay): waits.append(delay)
    monkeypatch.setattr(capture.asyncio,"sleep",no_wait)
    with pytest.raises(capture.CaptureTransient):
        asyncio.run(capture.run_cycle(object(),{},stage=transient,lock=acquired))
    assert calls == ["gmail-sync"] * 3 and waits == [30,90]
    calls.clear(); waits.clear()
    async def refused(command,env):
        calls.append(command)
        raise capture.CaptureRefused("safe")
    with pytest.raises(capture.CaptureRefused):
        asyncio.run(capture.run_cycle(object(),{},stage=refused,lock=acquired))
    assert calls == ["gmail-sync"] and waits == []


def test_entire_cycle_deadline_cancels_even_a_stuck_stage(monkeypatch):
    async def stuck(command,env): await asyncio.Future()
    monkeypatch.setattr(capture,"CYCLE_TIMEOUT",0.02)
    with pytest.raises(capture.CaptureTransient,match="cycle_timeout"):
        asyncio.run(capture.run_cycle(object(),{},stage=stuck,lock=acquired))


@pytest.mark.parametrize("report,code,exception", [
    ({"event":"gmail_sync","mode":"history","exit":0},0,None),
    ({"event":"gmail_sync","mode":"not_authorized","exit":0},0,capture.CaptureRefused),
    ({"event":"gmail_sync","mode":"locked","exit":0},0,capture.CaptureRefused),
    ({"event":"gmail_sync","mode":"paused","exit":0},0,capture.CaptureRefused),
    ({"private":"never print this"},1,capture.CaptureTransient),
    ({"private":"never print this"},2,capture.CaptureRefused),
])
def test_actual_child_exit_and_report_are_reconciled(monkeypatch, capsys, report, code, exception):
    create = asyncio.create_subprocess_exec
    async def fake(*args, **kwargs):
        return await create(sys.executable,"-c",f"import sys; print({json.dumps(json.dumps(report))}); sys.exit({code})",**kwargs)
    monkeypatch.setattr(capture.asyncio,"create_subprocess_exec",fake)
    if exception:
        with pytest.raises(exception) as exc:
            asyncio.run(capture.run_stage("gmail-sync", os.environ))
        assert "never print" not in str(exc.value)
    else:
        assert asyncio.run(capture.run_stage("gmail-sync", os.environ)) == {"mode":"history", "counts":{}}
    assert capsys.readouterr().out == ""


def test_timeout_kills_and_reaps_the_real_command(monkeypatch):
    create = asyncio.create_subprocess_exec
    children=[]
    async def fake(*args, **kwargs):
        child=await create(sys.executable,"-c","import time; time.sleep(60)",**kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(capture.asyncio,"create_subprocess_exec",fake)
    monkeypatch.setattr(capture,"STAGE_TIMEOUT",0.05)
    with pytest.raises(capture.CaptureTransient,match="timeout"):
        asyncio.run(capture.run_stage("gmail-sync",os.environ))
    assert children[0].returncode is not None


def test_dead_capture_fails_service_and_stops_other_process():
    children=[]
    def start(*args,**kwargs):
        child=subprocess.Popen(*args,**kwargs)
        children.append(child)
        return child
    commands=[[sys.executable,"-c","import time; time.sleep(60)"], [sys.executable,"-c","raise SystemExit(0)"]]
    assert mail_worker.supervise(commands,os.environ,stop=threading.Event(),popen=start) == 1
    assert all(child.poll() is not None for child in children)


def test_slow_capture_cannot_consume_triage_slot_and_shutdown_stops_both(tmp_path):
    done=tmp_path/"triage-done"
    stop=threading.Event()
    children=[]
    def start(*args,**kwargs):
        child=subprocess.Popen(*args,**kwargs)
        children.append(child)
        return child
    commands=[[sys.executable,"-c",f"from pathlib import Path; import time; Path({str(done)!r}).write_text('triaged'); time.sleep(60)"],
              [sys.executable,"-c","import time; time.sleep(60)"]]
    # Watch for triage's work while capture is still sleeping, then request shutdown.
    def observe():
        import time
        deadline=time.monotonic()+5
        while time.monotonic()<deadline and not done.exists():
            time.sleep(0.01)
        stop.set()
    watcher=threading.Thread(target=observe)
    watcher.start()
    assert mail_worker.supervise(commands,os.environ,stop=stop,popen=start) == 0
    watcher.join()
    assert done.read_text() == "triaged"
    assert all(child.poll() is not None for child in children)


def test_triage_child_does_not_receive_capture_credentials(monkeypatch):
    stop=threading.Event(); stop.set()
    seen=[]
    class Child:
        pid=999999999
        def poll(self): return 0
        def wait(self,**kw): return 0
    def start(cmd,**kw): seen.append(kw["env"]); return Child()
    mail_worker.supervise([[sys.executable,"-m","origenlab_worker.cli","triage-worker"]],
                          {"ORIGENLAB_WORKER_GMAIL_REFRESH_TOKEN":"fake", "ORIGENLAB_WORKER_DRIVE_CLIENT_SECRET":"fake", "KEEP":"yes"},
                          stop=stop,popen=start)
    assert seen == [{"KEEP":"yes"}]


@pytest.mark.parametrize("report,expected", [
    ({"event": "quote_read", "mode": "read", "exit": 0, "read": 2, "lines": 9, "refused": {"x": 1}},
     {"mode": "read", "counts": {"read": 2, "lines": 9}}),
    ({"event": "quote_read", "mode": "paused", "exit": 0}, {"mode": "paused", "counts": {}}),
    ({"event": "quote_read", "mode": "locked", "exit": 0}, {"mode": "locked", "counts": {}}),
    ({"event": "quote_read", "mode": "config_refused", "exit": 0}, capture.CaptureRefused),
    ({"event": "drive_file", "mode": "read", "exit": 0}, capture.CaptureRefused),
])
def test_the_quote_read_report_is_reconciled(monkeypatch, report, expected):
    create = asyncio.create_subprocess_exec
    async def fake(*args, **kwargs):
        return await create(sys.executable, "-c", f"print({json.dumps(json.dumps(report))})", **kwargs)
    monkeypatch.setattr(capture.asyncio, "create_subprocess_exec", fake)
    if isinstance(expected, type):
        with pytest.raises(expected):
            asyncio.run(capture.run_stage("quote-read", os.environ))
    else:
        assert asyncio.run(capture.run_stage("quote-read", os.environ)) == expected
