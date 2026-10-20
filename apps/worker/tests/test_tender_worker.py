"""The tender child (ruling R4): started by the supervisor only when tenders are on, keeps the
Drive settings the triage child loses, and refuses a model it cannot price. No database."""

from __future__ import annotations

import sys
import threading

import pytest

from origenlab_worker import mail_worker, triage_cli
from origenlab_worker.errors import ConfigRefused

FAKE_CA = "-----BEGIN CERTIFICATE-----\nZmFrZQ==\n-----END CERTIFICATE-----\n"


def _env(**over):
    env = {
        triage_cli.ENV_BID_ENABLED: "true",
        triage_cli.ENV_ANTHROPIC_API_KEY: "clave-ficticia",
        "ORIGENLAB_WORKER_DATABASE_URL": "postgresql://origenlab_worker.ref:pw@db.example.test:5432/postgres",
        "ORIGENLAB_WORKER_DATABASE_EXPECTED_HOST": "db.example.test",
        "ORIGENLAB_WORKER_DATABASE_CA_PEM": FAKE_CA,
        "ORIGENLAB_WORKER_STORAGE_S3_ENDPOINT": "https://ref.storage.supabase.co/storage/v1/s3",
        "ORIGENLAB_WORKER_STORAGE_S3_REGION": "sa-east-1",
        "ORIGENLAB_WORKER_STORAGE_S3_ACCESS_KEY_ID": "id-ficticio",
        "ORIGENLAB_WORKER_STORAGE_S3_SECRET_ACCESS_KEY": "secreto-ficticio",
    }
    env.update(over)
    return {k: v for k, v in env.items() if v is not None}


def test_the_supervisor_starts_the_tender_child_only_when_tenders_are_on(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(mail_worker, "supervise", lambda commands, env, **kw: calls.extend(commands) or 0)
    mail_worker.run_mail_worker({})
    assert [c[-1] for c in calls] == ["triage-child"]
    calls.clear()
    mail_worker.run_mail_worker({triage_cli.ENV_BID_ENABLED: "true"})
    assert [c[-1] for c in calls] == ["triage-child", "tender-child"]


def test_the_tender_child_keeps_drive_but_not_gmail_or_the_capture_gate() -> None:
    stop = threading.Event()
    stop.set()
    seen = []

    class Child:
        pid = 999999999

        def poll(self):
            return 0

        def wait(self, **kw):
            return 0

    def start(cmd, **kw):
        seen.append(kw["env"])
        return Child()

    env = {"ORIGENLAB_WORKER_GMAIL_REFRESH_TOKEN": "fake", "ORIGENLAB_WORKER_DRIVE_CLIENT_SECRET": "fake",
           "ORIGENLAB_WORKER_CAPTURE_SCHEDULER_ENABLED": "true", "KEEP": "yes"}
    mail_worker.supervise([[sys.executable, "-m", "origenlab_worker.cli", "tender-child"]], env, stop=stop,
                          popen=start)
    assert seen == [{"ORIGENLAB_WORKER_DRIVE_CLIENT_SECRET": "fake", "KEEP": "yes"}]


def test_the_cli_runs_the_tender_child(monkeypatch) -> None:
    from origenlab_worker import cli

    calls = []
    monkeypatch.setattr(cli, "run_tender_worker", lambda env, started: calls.append("tender") or 0)
    assert cli.main(["tender-child"], environ={}) == 0
    assert calls == ["tender"]


def test_the_default_models() -> None:
    config = triage_cli.bid_config_from_env(_env(), reader_factory=lambda key: "reader")
    assert (config.models.read, config.models.fill, config.models.match) == (
        "claude-opus-5-5", "claude-opus-5-5", "claude-sonnet-5-5")
    assert config.reader == "reader"


@pytest.mark.parametrize("var", ["ENV_BID_READ_MODEL", "ENV_BID_FILL_MODEL", "ENV_BID_MATCH_MODEL"])
def test_a_model_without_a_price_is_refused(var) -> None:
    with pytest.raises(ConfigRefused) as exc:
        triage_cli.bid_config_from_env(_env(**{getattr(triage_cli, var): "claude-unpriced-9"}),
                                       reader_factory=lambda key: None)
    assert exc.value.code == "model_unpriced"


def test_a_malformed_model_id_is_refused() -> None:
    with pytest.raises(ConfigRefused) as exc:
        triage_cli.bid_config_from_env(_env(**{triage_cli.ENV_BID_READ_MODEL: "gpt; drop"}),
                                       reader_factory=lambda key: None)
    assert exc.value.code == "model_invalid"


def test_the_key_is_required() -> None:
    with pytest.raises(ConfigRefused) as exc:
        triage_cli.bid_config_from_env(_env(**{triage_cli.ENV_ANTHROPIC_API_KEY: None}),
                                       reader_factory=lambda key: None)
    assert exc.value.code == "anthropic_api_key_missing"


def test_the_flag_is_strict() -> None:
    assert triage_cli.bid_enabled({triage_cli.ENV_BID_ENABLED: " TRUE "})
    assert not triage_cli.bid_enabled({triage_cli.ENV_BID_ENABLED: "1"})
    assert not triage_cli.bid_enabled({})


def test_a_tender_child_with_a_bad_setting_idles_instead_of_taking_triage_down(monkeypatch, capsys) -> None:
    idled = []
    monkeypatch.setattr(triage_cli, "_idle_until_terminated", lambda event="triage_worker": idled.append(event))
    assert triage_cli.run_tender_worker(_env(**{triage_cli.ENV_ANTHROPIC_API_KEY: None}), 0.0) == 0
    out = capsys.readouterr().out
    assert '"config_refused"' in out and "anthropic_api_key_missing" in out and idled == ["tender_worker"]


def test_mail_worker_does_not_import_the_triage_cli() -> None:
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(mail_worker))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert "origenlab_worker.triage_cli" not in imported


DRIVE_ENV = {"ORIGENLAB_WORKER_DRIVE_FILING_ENABLED": "true", "ORIGENLAB_WORKER_DRIVE_CLIENT_ID": "fake-id",
             "ORIGENLAB_WORKER_DRIVE_CLIENT_SECRET": "fake-secret", "ORIGENLAB_WORKER_DRIVE_REFRESH_TOKEN": "fake-token",
             "ORIGENLAB_WORKER_DRIVE_CASOS_FOLDER_ID": "casos-folder",
             "ORIGENLAB_WORKER_DRIVE_PROTECTED_FOLDER_IDS": "pend, env"}


def test_the_tender_child_uses_the_case_archive_drive_settings() -> None:
    config = triage_cli.bid_config_from_env(_env(**DRIVE_ENV), reader_factory=lambda key: "reader")
    assert config.drive is not None and config.drive_target.casos_folder_id == "casos-folder"
    assert config.drive_target.protected_folder_ids == frozenset({"pend", "env"})
    assert config.drive_target.principal == "contacto@origenlab.cl"
    assert "fake-secret" not in repr(config)


@pytest.mark.parametrize("missing", ["ORIGENLAB_WORKER_DRIVE_FILING_ENABLED", "ORIGENLAB_WORKER_DRIVE_REFRESH_TOKEN",
                                     "ORIGENLAB_WORKER_DRIVE_CASOS_FOLDER_ID"])
def test_without_drive_settings_the_tender_child_runs_with_drive_off(missing) -> None:
    config = triage_cli.bid_config_from_env(_env(**{**DRIVE_ENV, missing: None}), reader_factory=lambda key: "reader")
    assert config.drive is None and config.drive_target is None


def test_a_tender_child_that_fails_at_runtime_idles_and_logs_only_the_class(monkeypatch, capsys) -> None:
    """A dead tender child would end the service (the supervisor stops every child when one exits):
    triage and capture must keep running, so the child logs the failure's class and idles."""
    idled = []
    monkeypatch.setattr(triage_cli, "_idle_until_terminated", lambda event="triage_worker": idled.append(event))

    def boom(config):
        raise RuntimeError("texto-secreto-del-error")

    monkeypatch.setattr(triage_cli, "_serve_tenders", boom)
    assert triage_cli.run_tender_worker(_env(), 0.0) == 0
    out = capsys.readouterr().out
    assert '"failed"' in out and "RuntimeError" in out and "texto-secreto-del-error" not in out
    assert idled == ["tender_worker"]
