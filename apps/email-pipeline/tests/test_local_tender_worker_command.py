from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from origenlab_email_pipeline.operator_cli import (
    local_tender_worker_command as command,
)
from origenlab_email_pipeline.operator_cli.local_tender_worker import (
    LocalTenderWorkerError,
    LocalTenderWorkerHttpError,
)


def _watch_options(tmp_path: Path) -> command.LocalTenderWorkerOptions:
    return command.LocalTenderWorkerOptions(
        downloads_dir=tmp_path / "Downloads",
        state_dir=tmp_path / "state",
        watch=True,
        apply=True,
        poll_seconds=5.0,
        settle_seconds=0.0,
    )


def test_watch_stops_on_local_worker_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fail_local(_options: command.LocalTenderWorkerOptions) -> int:
        nonlocal calls
        calls += 1
        raise LocalTenderWorkerError("deterministic OCR failure")

    monkeypatch.setattr(command, "_run_one", fail_local)
    monkeypatch.setattr(
        command.time,
        "sleep",
        lambda _seconds: pytest.fail("local failure must not retry"),
    )

    assert command.run_local_tender_worker(_watch_options(tmp_path)) == 2
    assert calls == 1


def test_watch_stops_on_non_retryable_http_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_http(_options: command.LocalTenderWorkerOptions) -> int:
        raise LocalTenderWorkerHttpError(422, "request rejected")

    monkeypatch.setattr(command, "_run_one", fail_http)
    monkeypatch.setattr(
        command.time,
        "sleep",
        lambda _seconds: pytest.fail("HTTP 4xx must not retry"),
    )

    assert command.run_local_tender_worker(_watch_options(tmp_path)) == 2


def test_watch_retries_transient_http_with_exponential_backoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcomes: list[BaseException] = [
        LocalTenderWorkerHttpError(503, "temporary failure"),
        LocalTenderWorkerHttpError(None, "network failure"),
        KeyboardInterrupt(),
    ]
    sleeps: list[float] = []

    def run_one(_options: command.LocalTenderWorkerOptions) -> int:
        outcome = outcomes.pop(0)
        raise outcome

    monkeypatch.setattr(command, "_run_one", run_one)
    monkeypatch.setattr(
        command.time,
        "sleep",
        lambda seconds: sleeps.append(seconds),
    )

    assert command.run_local_tender_worker(_watch_options(tmp_path)) == 0
    assert sleeps == [5.0, 10.0]


def test_watch_healthy_iteration_resets_http_backoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcomes: list[BaseException | int] = [
        LocalTenderWorkerHttpError(503, "temporary failure"),
        0,
        LocalTenderWorkerHttpError(503, "temporary failure again"),
        KeyboardInterrupt(),
    ]
    sleeps: list[float] = []

    def run_one(_options: command.LocalTenderWorkerOptions) -> int:
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(command, "_run_one", run_one)
    monkeypatch.setattr(
        command.time,
        "sleep",
        lambda seconds: sleeps.append(seconds),
    )

    assert command.run_local_tender_worker(_watch_options(tmp_path)) == 0
    assert sleeps == [5.0, 5.0, 5.0]


def test_run_one_resolves_auth_before_compute_and_marks_done_after_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    downloads = tmp_path / "Downloads"
    state = tmp_path / "state"

    ticket = SimpleNamespace(
        ticket_id="ticket_command12",
        tender_code="4291-46-le26",
    )
    job = SimpleNamespace(
        ticket=ticket,
        zip_path=downloads / "Licitaciones_command.zip",
    )

    payload = {
        "contract_version": "local_tender_annex_import_v1",
        "tender_code": "4291-46-le26",
        "raw": {
            "archive": {
                "zip_sha256": "a" * 64,
            },
        },
    }

    events: list[str] = []

    monkeypatch.setattr(
        command,
        "find_pending_job",
        lambda *_args, **_kwargs: job,
    )
    monkeypatch.setattr(
        command,
        "zip_is_settled",
        lambda *_args, **_kwargs: True,
    )

    def resolve_base() -> str:
        events.append("base_url")
        return "https://api.origenlab.cl"

    def resolve_token() -> str:
        events.append("token")
        return "synthetic-test-token"

    def build_outbox(*_args, **_kwargs):
        events.append("compute")
        return payload

    def post_result(**_kwargs):
        events.append("post")
        return {"persisted": True}

    mark_done = Mock(side_effect=lambda *_args, **_kwargs: events.append("done"))

    monkeypatch.setattr(command, "resolve_api_base_url", resolve_base)
    monkeypatch.setattr(command, "resolve_api_token", resolve_token)
    monkeypatch.setattr(command, "load_or_build_outbox", build_outbox)
    monkeypatch.setattr(command, "post_structured_local_import", post_result)
    monkeypatch.setattr(command, "mark_job_done", mark_done)

    options = command.LocalTenderWorkerOptions(
        downloads_dir=downloads,
        state_dir=state,
        once=True,
        apply=True,
        settle_seconds=0.0,
    )

    assert command._run_one(options) == 0

    assert events == [
        "base_url",
        "token",
        "compute",
        "post",
        "done",
    ]
    mark_done.assert_called_once_with(job, state, payload)


def test_missing_auth_fails_before_expensive_compute(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    downloads = tmp_path / "Downloads"
    state = tmp_path / "state"

    job = SimpleNamespace(
        ticket=SimpleNamespace(
            ticket_id="ticket_noauth12",
            tender_code="4291-46-le26",
        ),
        zip_path=downloads / "Licitaciones_noauth.zip",
    )

    monkeypatch.setattr(
        command,
        "find_pending_job",
        lambda *_args, **_kwargs: job,
    )
    monkeypatch.setattr(
        command,
        "zip_is_settled",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        command,
        "resolve_api_base_url",
        lambda: "https://api.origenlab.cl",
    )

    def missing_token() -> str:
        raise LocalTenderWorkerError("API authentication is not configured")

    monkeypatch.setattr(command, "resolve_api_token", missing_token)
    monkeypatch.setattr(
        command,
        "load_or_build_outbox",
        lambda *_args, **_kwargs: pytest.fail(
            "OCR/T1 must not run without API authentication"
        ),
    )

    options = command.LocalTenderWorkerOptions(
        downloads_dir=downloads,
        state_dir=state,
        once=True,
        apply=True,
        settle_seconds=0.0,
    )

    assert command.run_local_tender_worker(options) == 2
