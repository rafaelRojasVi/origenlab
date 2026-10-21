"""The worker's resident memory (2026-10-21): the supervisor and its children import only what they run,
and the tender child's discovery runs in a process that exits. Starter's 512 MB was outgrown when every
long-running process imported the whole CLI (FastAPI, the API's routers, the V1 pipeline)."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from origenlab_worker import entry, mail_worker, triage_cli


def _modules_after(statement: str) -> set[str]:
    code = f"import sys, json\n{statement}\nprint(json.dumps(sorted(sys.modules)))"
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    return set(json.loads(done.stdout.splitlines()[-1]))


def test_the_supervisor_imports_no_child_code() -> None:
    loaded = _modules_after("import origenlab_worker.entry, origenlab_worker.mail_worker")
    heavy = {"origenlab_worker.cli", "origenlab_worker.capture_queue", "origenlab_worker.triage_cli",
             "procrastinate", "psycopg", "fastapi"}
    assert not loaded & heavy
    assert not any(m.startswith(("origenlab_api", "origenlab_email_pipeline")) for m in loaded)


def test_the_capture_scheduler_imports_neither_triage_nor_the_v1_pipeline() -> None:
    loaded = _modules_after("import origenlab_worker.capture_queue")
    assert "origenlab_worker.triage" not in loaded and "origenlab_worker.cli" not in loaded
    assert not any(m.startswith("origenlab_email_pipeline") for m in loaded)


def test_a_v2_helper_does_not_import_the_api() -> None:
    loaded = _modules_after("import origenlab_api.v2.quote_case_archive")
    assert "fastapi" not in loaded and "origenlab_api.v2.routes" not in loaded


def test_the_v2_package_names_still_resolve() -> None:
    import origenlab_api.v2 as v2

    for name in v2.__all__:
        assert getattr(v2, name) is not None
    with pytest.raises(AttributeError):
        v2.no_such_name  # noqa: B018


def test_the_supervisor_starts_its_children_through_the_light_entry(monkeypatch) -> None:
    seen = []
    monkeypatch.setattr(mail_worker, "supervise", lambda commands, env, stop: seen.extend(commands) or 0)
    env = {"ORIGENLAB_WORKER_CAPTURE_SCHEDULER_ENABLED": "true", "ORIGENLAB_WORKER_BID_ENABLED": "true"}
    assert mail_worker.run_mail_worker(env) == 0
    assert [c[1:] for c in seen] == [["-m", "origenlab_worker.entry", c[-1]] for c in seen]
    assert [c[-1] for c in seen] == ["triage-child", "capture-worker", "tender-child"]


def test_other_commands_go_to_the_cli(monkeypatch) -> None:
    from origenlab_worker import cli

    calls = []
    monkeypatch.setattr(cli, "main", lambda argv, environ: calls.append((argv, environ)) or 7)
    assert entry.main(["drive-file", "--dry-run"], {}) == 7
    assert calls == [(["drive-file", "--dry-run"], {})]


def test_a_refused_supervisor_setting_is_one_json_line(monkeypatch, capsys) -> None:
    from origenlab_worker.errors import ConfigRefused

    def refuse(env):
        raise ConfigRefused("capture_target_missing")

    monkeypatch.setattr(mail_worker, "run_mail_worker", refuse)
    assert entry.main(["mail-worker"], {}) == 3
    line = json.loads(capsys.readouterr().out)
    assert (line["event"], line["exit"], line["error"]) == ("mail_worker", 3, "capture_target_missing")


def _done(stdout: str, code: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=stdout)


def test_discovery_runs_in_its_own_process_and_returns_its_counts() -> None:
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return _done('warning: noise\n{"outcome": "ran", "listed": 4778}\n')

    counts = triage_cli.discovery_in_subprocess({"A": "1"}, run=run)()
    assert counts == {"outcome": "ran", "listed": 4778}
    command, kwargs = calls[0]
    assert command[1:] == ["-m", "origenlab_worker.entry", "tender-discovery"]
    assert kwargs["env"] == {"A": "1"} and kwargs["stderr"] is subprocess.DEVNULL


def test_a_discovery_process_that_dies_or_hangs_is_reported_without_its_text() -> None:
    assert triage_cli.discovery_in_subprocess({}, run=lambda c, **k: _done("Traceback: ticket=secret", 1))() == {
        "outcome": "failed", "error": "exit_1"}

    def hang(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    assert triage_cli.discovery_in_subprocess({}, run=hang, timeout_s=1)() == {
        "outcome": "failed", "error": "TimeoutExpired"}


def test_the_discovery_command_prints_a_refused_setting_as_counts(capsys) -> None:
    assert entry.main(["tender-discovery"], {}) == 0
    line = json.loads(capsys.readouterr().out)
    assert line["outcome"] == "config_refused"
