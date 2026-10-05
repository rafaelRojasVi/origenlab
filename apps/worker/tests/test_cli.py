"""The cron entry point: environment rules, exit codes and the one log line. No network."""

from __future__ import annotations

import json
import os
import stat
from contextlib import contextmanager

import pytest

from fakes import FakeDb, FakeGmail, FakeMessage, FakeStore
from mailfixtures import make_raw
from origenlab_worker.cli import config_from_env, main
from origenlab_worker.errors import ConfigRefused

#: A syntactically complete PEM block with no real certificate in it.
FAKE_CA = "-----BEGIN CERTIFICATE-----\nZmFrZQ==\n-----END CERTIFICATE-----\n"
HOST = "pooler.example.invalid"
GOOD_ENV = {
    "ORIGENLAB_WORKER_DATABASE_URL": f"postgresql://origenlab_worker.abcdefghijklmnopqrst:pw@{HOST}:5432/postgres",
    "ORIGENLAB_WORKER_DATABASE_EXPECTED_HOST": HOST,
    "ORIGENLAB_WORKER_DATABASE_CA_PEM": FAKE_CA,
    "ORIGENLAB_WORKER_GMAIL_CLIENT_ID": "cid",
    "ORIGENLAB_WORKER_GMAIL_CLIENT_SECRET": "sec",
    "ORIGENLAB_WORKER_GMAIL_REFRESH_TOKEN": "rt",
    "ORIGENLAB_WORKER_STORAGE_S3_ENDPOINT": "https://abcdefghijklmnopqrst.storage.supabase.co/storage/v1/s3",
    "ORIGENLAB_WORKER_STORAGE_S3_REGION": "sa-east-1",
    "ORIGENLAB_WORKER_STORAGE_S3_ACCESS_KEY_ID": "kid",
    "ORIGENLAB_WORKER_STORAGE_S3_SECRET_ACCESS_KEY": "ksec",
    "ORIGENLAB_WORKER_GMAIL_SYNC_ENABLED": "true",
}


def test_a_complete_environment_builds_a_verify_full_target_and_a_private_ca_file() -> None:
    config = config_from_env(GOOD_ENV)
    options = config.database.connect_options
    assert options["sslmode"] == "verify-full" and config.enabled is True
    assert stat.S_IMODE(os.stat(options["sslrootcert"]).st_mode) == 0o600


@pytest.mark.parametrize("override,code", [
    ({"ORIGENLAB_WORKER_GMAIL_REFRESH_TOKEN": ""}, "gmail_credentials_missing"),
    ({"ORIGENLAB_WORKER_DATABASE_CA_PEM": ""}, "ca_pem_missing"),
    ({"ORIGENLAB_WORKER_DATABASE_URL": f"postgresql://origenlab_worker.x:pw@{HOST}:6543/postgres"}, "transaction_pooler_refused"),
    ({"ORIGENLAB_WORKER_STORAGE_S3_REGION": ""}, "storage_region_missing"),
])
def test_an_incomplete_environment_is_refused_by_code(override, code) -> None:
    with pytest.raises(ConfigRefused) as exc:
        config_from_env({**GOOD_ENV, **override})
    assert exc.value.code == code


@pytest.mark.parametrize("value,enabled", [("true", True), ("TRUE", True), ("false", False), ("", False), ("1", False)])
def test_only_true_turns_the_capture_on(value, enabled) -> None:
    assert config_from_env({**GOOD_ENV, "ORIGENLAB_WORKER_GMAIL_SYNC_ENABLED": value}).enabled is enabled


def _components(db, gmail, store):
    @contextmanager
    def opened(_config):
        yield db, gmail, store

    return opened


def _line(capsys) -> dict:
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1, "exactly one log line per run"
    return json.loads(out[0])


def test_a_run_prints_one_line_of_counts_and_returns_its_exit_code(capsys) -> None:
    gmail = FakeGmail(pages=[["m1"]], messages={"m1": FakeMessage(make_raw())})
    code = main(["gmail-sync"], GOOD_ENV, components=_components(FakeDb(), gmail, FakeStore()))
    line = _line(capsys)
    assert code == 0 and (line["mode"], line["stored"], line["exit"]) == ("history", 1, 0)
    assert {"elapsed_ms", "max_rss_mb", "event"} <= set(line)


def test_the_log_line_never_carries_an_address_a_subject_or_a_gmail_id(capsys) -> None:
    db = FakeDb()
    db.fail_on_record = "m1"
    raw = make_raw(subject="Cotización secreta")
    code = main(["gmail-sync"], GOOD_ENV, components=_components(db, FakeGmail(pages=[["m1"]], messages={"m1": FakeMessage(raw)}), FakeStore()))
    text = json.dumps(_line(capsys))
    assert code == 1 and "RuntimeError" in text
    for leak in ("ana@", "cliente.invalid", "secreta", "m1"):
        assert leak not in text


def test_a_refused_configuration_exits_3_and_opens_nothing(capsys) -> None:
    opened = []

    @contextmanager
    def components(_config):
        opened.append(True)
        yield None, None, None

    code = main(["gmail-sync"], {**GOOD_ENV, "ORIGENLAB_WORKER_DATABASE_URL": "postgresql://postgres:pw@x/y"}, components=components)
    assert code == 3 and opened == [] and _line(capsys)["error"] == "database_host_not_expected"


def test_a_connection_failure_exits_1_by_class(capsys) -> None:
    @contextmanager
    def components(_config):
        raise OSError("could not connect to pooler.example.invalid")
        yield  # pragma: no cover

    code = main(["gmail-sync", "--dry-run"], GOOD_ENV, components=components)
    line = _line(capsys)
    assert code == 1 and line["error"] == "OSError" and "pooler" not in json.dumps(line)


def test_init_is_passed_through(capsys) -> None:
    db = FakeDb(state="unauthorized", history_id=None, last_synced_at=None)
    assert main(["gmail-sync", "--init"], GOOD_ENV, components=_components(db, FakeGmail(), FakeStore())) == 0
    assert _line(capsys)["mode"] == "init_baseline"


def test_an_exception_message_with_an_address_never_reaches_the_log_line(capsys) -> None:
    @contextmanager
    def components(_config):
        raise RuntimeError("DETAIL: key (address)=(ana@cliente.invalid) Subject: Cotización secreta id 18c3f")
        yield  # pragma: no cover

    code = main(["gmail-sync"], GOOD_ENV, components=components)
    captured = capsys.readouterr()
    assert code == 1
    text = captured.out + captured.err
    assert json.loads(captured.out)["error"] == "RuntimeError"
    for leak in ("ana@", "cliente", "secreta", "18c3f", "DETAIL"):
        assert leak not in text


def test_no_credential_value_reaches_the_output(capsys) -> None:
    main(["gmail-sync"], {**GOOD_ENV, "ORIGENLAB_WORKER_DATABASE_URL": "postgresql://postgres:pw@x/y"})
    captured = capsys.readouterr()
    for secret in ("sec", "ksec", "pw@", "rt\""):
        assert secret not in captured.out + captured.err
