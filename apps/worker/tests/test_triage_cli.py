"""`triage-once`: paused, refused configuration, a dry run and a real pass — with fakes, no database."""

from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from origenlab_worker import cli, triage_cli
from origenlab_worker.triage_model import DEFAULT_MODEL

PEM = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----"
ENV = {
    "ORIGENLAB_WORKER_TRIAGE_ENABLED": "true",
    "ORIGENLAB_WORKER_DATABASE_URL": "postgresql://origenlab_worker.abc:pw@aws-0-x.pooler.supabase.com:5432/postgres",
    "ORIGENLAB_WORKER_DATABASE_EXPECTED_HOST": "aws-0-x.pooler.supabase.com",
    "ORIGENLAB_WORKER_DATABASE_CA_PEM": PEM,
    "ORIGENLAB_WORKER_STORAGE_S3_ENDPOINT": "https://abc.storage.supabase.co/storage/v1/s3",
    "ORIGENLAB_WORKER_STORAGE_S3_REGION": "sa-east-1",
    "ORIGENLAB_WORKER_STORAGE_S3_ACCESS_KEY_ID": "id",
    "ORIGENLAB_WORKER_STORAGE_S3_SECRET_ACCESS_KEY": "secret",
}


def _line(capsys) -> dict:
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_paused_reads_no_secret_and_opens_nothing(capsys) -> None:
    assert cli.main(["triage-once"], {}) == 0
    assert _line(capsys)["mode"] == "paused"


def test_the_model_needs_its_key(capsys) -> None:
    assert cli.main(["triage-once"], {**ENV, "ORIGENLAB_WORKER_TRIAGE_MODEL_ENABLED": "true"}) == 3
    assert _line(capsys)["error"] == "anthropic_api_key_missing"


def test_an_invalid_model_id_is_refused(capsys) -> None:
    assert cli.main(["triage-once"], {**ENV, "ORIGENLAB_WORKER_TRIAGE_MODEL": "gpt-x"}) == 3
    assert _line(capsys)["error"] == "triage_model_invalid"


def test_the_model_defaults_to_opus_and_reads_its_key_only_when_enabled() -> None:
    seen = []
    config = triage_cli.triage_config_from_env({**ENV, "ORIGENLAB_WORKER_TRIAGE_MODEL_ENABLED": "true",
                                               "ORIGENLAB_WORKER_ANTHROPIC_API_KEY": "k"},
                                              reader_factory=lambda key: seen.append(key) or (lambda r: None))
    assert config.model_name == DEFAULT_MODEL and config.model.reader is not None and seen == ["k"]
    off = triage_cli.triage_config_from_env(ENV, reader_factory=lambda key: pytest.fail("read the key"))
    assert off.model.reader is None and not off.model_enabled
    assert "pw" not in repr(off) and "secret" not in repr(off)


class FakeTriageDb:
    def __init__(self, conn) -> None:
        self.conn = conn

    def pending(self, *, since_days, limit):
        self.conn.calls.append((since_days, limit))
        return ["sr-1", "sr-2"]


@pytest.fixture
def fakes(monkeypatch):
    conn = SimpleNamespace(calls=[])

    @contextmanager
    def open_connection(target):
        yield conn

    triaged = []
    monkeypatch.setattr(triage_cli, "TriageDb", FakeTriageDb)
    monkeypatch.setattr(triage_cli, "triage_one",
                        lambda db, store, sid, model: triaged.append(sid) or SimpleNamespace(
                            outcome="recorded", triage_class="quote_request", model="off"))
    return conn, open_connection, triaged


def test_a_dry_run_counts_and_triages_nothing(capsys, fakes) -> None:
    conn, open_connection, triaged = fakes
    code = triage_cli.run_triage_once(SimpleNamespace(dry_run=True, since_days=99999, limit=0), ENV, 0.0,
                                      open_connection=open_connection, store_factory=lambda s: object())
    line = _line(capsys)
    assert code == 0 and line["found"] == 2 and line["model"] == "off" and triaged == []
    assert conn.calls == [(3650, 1)]  # both bounds clamped


def test_a_pass_triages_every_pending_message_and_reports_counts_only(capsys, fakes) -> None:
    _, open_connection, triaged = fakes
    code = triage_cli.run_triage_once(SimpleNamespace(dry_run=False, since_days=14, limit=200), ENV, 0.0,
                                      open_connection=open_connection, store_factory=lambda s: object())
    line = _line(capsys)
    assert code == 0 and triaged == ["sr-1", "sr-2"]
    assert line["outcomes"] == {"recorded": 2} and line["classes"] == {"quote_request": 2}
