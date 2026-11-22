"""`origenlab-worker quote-read`: paused unless switched on, refuses a model without a key, runs the
template-only reader with the model off. No database: the components are injected."""

from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

from origenlab_worker import cli

STORAGE = {"ORIGENLAB_WORKER_STORAGE_S3_ENDPOINT": "https://ref.storage.supabase.co/storage/v1/s3",
           "ORIGENLAB_WORKER_STORAGE_S3_REGION": "us-west-2", "ORIGENLAB_WORKER_STORAGE_S3_ACCESS_KEY_ID": "k",
           "ORIGENLAB_WORKER_STORAGE_S3_SECRET_ACCESS_KEY": "s"}


def _line(capsys) -> dict:
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_quote_read_is_paused_unless_switched_on(capsys) -> None:
    def boom(_config):
        raise AssertionError("a paused run opens nothing")

    assert cli.main(["quote-read"], STORAGE, quote_components=boom) == 0
    assert (_line(capsys)["event"], "paused") == ("quote_read", "paused")


def test_a_model_without_a_key_is_a_configuration_refusal(capsys) -> None:
    env = {**STORAGE, "ORIGENLAB_WORKER_QUOTE_READ_ENABLED": "true", "ORIGENLAB_WORKER_QUOTE_READ_MODEL": "claude-opus-5-5"}
    assert cli.main(["quote-read"], env, quote_components=lambda c: (_ for _ in ()).throw(AssertionError())) == 3
    line = _line(capsys)
    assert (line["mode"], line["error"]) == ("config_refused", "anthropic_api_key_missing")


def test_with_the_model_off_it_runs_without_a_key_and_reports_counts(capsys, monkeypatch) -> None:
    seen = {}

    class FakeReader:
        def __init__(self, conn, store, reader, model):
            seen.update(reader=reader, model=model)

        def run(self, *, limit):
            seen["limit"] = limit
            from origenlab_worker.quote_reader import ReadCounts

            return ReadCounts(candidates=2, read=1, lines=3, refused={"unreadable": 1})

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *a):
            pass

        def fetchone(self):
            return (True,)

    conn = type("Conn", (), {"cursor": lambda self: Cursor()})()

    @contextmanager
    def components(config):
        yield conn, object()

    monkeypatch.setattr(cli, "QuoteReader", FakeReader)
    monkeypatch.setattr(cli, "quote_config_from_env",
                        lambda env: cli.QuoteConfig(database=None, storage=None, model="off"))  # type: ignore[arg-type]
    env = {**STORAGE, "ORIGENLAB_WORKER_QUOTE_READ_ENABLED": "true", "ORIGENLAB_WORKER_QUOTE_READ_MODEL": "off"}
    assert cli.main(["quote-read", "--limit", "4"], env, quote_components=components) == 0
    line = _line(capsys)
    assert (line["event"], line["mode"], line["read"], line["refused"]) == ("quote_read", "read", 1, {"unreadable": 1})
    assert seen == {"reader": None, "model": "off", "limit": 4}


def test_with_the_model_on_the_reader_has_no_sdk_retries_a_long_timeout_and_a_cap(capsys, monkeypatch) -> None:
    """The capture stage kills quote-read at 300 s: no SDK retries, 120 s per call, 3 PDFs a run."""
    from origenlab_worker import triage_model
    from origenlab_worker.quote_reader import DEFAULT_LIMIT

    from origenlab_worker import model_budget
    from origenlab_worker.model_budget import BudgetReached

    built, seen, checked, called = [], {}, [], []
    monkeypatch.setattr(triage_model, "anthropic_reader",
                        lambda key, **kw: built.append(kw) or (lambda request: called.append(request)))

    class CappedSpend:
        def __init__(self, conn, *, env=None):
            pass

        def spend_check(self, feature, worst_usd):
            checked.append(feature)
            raise BudgetReached(0, 0)

        def spend_record(self, *a):
            raise AssertionError("nothing was called")

    monkeypatch.setattr(model_budget, "ConnectionSpend", CappedSpend)

    class FakeReader:
        def __init__(self, conn, store, reader, model):
            seen["reader"] = reader

        def run(self, *, limit):
            seen["limit"] = limit
            from origenlab_worker.quote_reader import ReadCounts

            return ReadCounts()

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *a):
            pass

        def fetchone(self):
            return (True,)

    conn = type("Conn", (), {"cursor": lambda self: Cursor()})()

    @contextmanager
    def components(config):
        yield conn, object()

    monkeypatch.setattr(cli, "QuoteReader", FakeReader)
    monkeypatch.setattr(cli, "pdftotext_available", lambda: True)
    env = {**STORAGE, "ORIGENLAB_WORKER_QUOTE_READ_ENABLED": "true", "ORIGENLAB_WORKER_QUOTE_READ_MODEL": "claude-opus-5-5",
           "ORIGENLAB_WORKER_ANTHROPIC_API_KEY": "k", "ORIGENLAB_WORKER_DATABASE_URL": "postgresql://u@db.invalid/x"}
    monkeypatch.setattr(cli, "quote_config_from_env",
                        lambda env: cli.QuoteConfig(database=None, storage=None, model="claude-opus-5-5",  # type: ignore[arg-type]
                                                    api_key="k"))
    assert cli.main(["quote-read"], env, quote_components=components) == 0
    assert built == [{"timeout_s": 120.0, "max_retries": 0}]
    assert DEFAULT_LIMIT == 3 and seen["limit"] == 3
    # The reader QuoteReader gets is metered as quote_read: over the cap it never calls the model.
    with pytest.raises(BudgetReached):
        seen["reader"]({"model": "claude-opus-5-5", "max_tokens": 10, "messages": []})
    assert checked == ["quote_read"] and called == []


def test_an_unknown_model_id_is_a_configuration_refusal(capsys) -> None:
    env = {**STORAGE, "ORIGENLAB_WORKER_QUOTE_READ_ENABLED": "true", "ORIGENLAB_WORKER_QUOTE_READ_MODEL": "gpt-banana",
           "ORIGENLAB_WORKER_ANTHROPIC_API_KEY": "k"}
    assert cli.main(["quote-read"], env, quote_components=lambda c: (_ for _ in ()).throw(AssertionError())) == 3
    assert _line(capsys)["error"] == "model_invalid"


def test_a_model_without_pdftotext_is_a_configuration_refusal(capsys, monkeypatch) -> None:
    monkeypatch.setattr(cli, "pdftotext_available", lambda: False)
    env = {**STORAGE, "ORIGENLAB_WORKER_QUOTE_READ_ENABLED": "true", "ORIGENLAB_WORKER_QUOTE_READ_MODEL": "claude-opus-5-5",
           "ORIGENLAB_WORKER_ANTHROPIC_API_KEY": "k"}
    assert cli.main(["quote-read"], env, quote_components=lambda c: (_ for _ in ()).throw(AssertionError())) == 3
    assert _line(capsys)["error"] == "pdftotext_missing"
