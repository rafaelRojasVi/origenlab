"""The owner scripts that never touch Storage run without the S3 settings in the environment."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from mailfixtures import MAILBOX
from origenlab_worker.cli import config_from_env
from origenlab_worker.errors import ConfigRefused
from scriptload import load_script

HOST = "aws-0-sa-east-1.pooler.supabase.com"
CA = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----"
#: Database and Gmail settings only — no ORIGENLAB_WORKER_STORAGE_* at all.
NO_STORAGE_ENV = {
    "ORIGENLAB_WORKER_DATABASE_URL": f"postgresql://origenlab_worker.abcdef@{HOST}:5432/postgres",
    "ORIGENLAB_WORKER_DATABASE_EXPECTED_HOST": HOST,
    "ORIGENLAB_WORKER_DATABASE_CA_PEM": CA,
    "ORIGENLAB_WORKER_GMAIL_CLIENT_ID": "cid",
    "ORIGENLAB_WORKER_GMAIL_CLIENT_SECRET": "sec",
    "ORIGENLAB_WORKER_GMAIL_REFRESH_TOKEN": "rt",
}


@contextmanager
def _fake_db(_target):
    yield SimpleNamespace(connection=None)


def test_storage_is_required_by_default_and_skipped_on_request() -> None:
    with pytest.raises(ConfigRefused) as refused:
        config_from_env(NO_STORAGE_ENV)
    assert refused.value.code == "storage_endpoint_missing"
    config = config_from_env(NO_STORAGE_ENV, need_storage=False)
    assert config.storage is None and config.gmail.client_id == "cid"


def test_payload_parity_runs_with_no_storage_settings(tmp_path: Path, monkeypatch) -> None:
    parity = load_script("payload_parity")
    monkeypatch.setattr("os.environ", dict(NO_STORAGE_ENV))
    monkeypatch.setattr("origenlab_worker.database.open_worker_db", _fake_db)
    monkeypatch.setattr(parity, "read_staged", lambda _conn: [])
    monkeypatch.setattr("sys.argv", ["x", "--out", str(tmp_path / "out")])
    assert parity.main() == 0


def test_shadow_reconcile_runs_with_no_storage_settings(tmp_path: Path, monkeypatch) -> None:
    shadow = load_script("shadow_reconcile")
    sqlite_path = tmp_path / "emails.sqlite"
    conn = sqlite3.connect(sqlite_path)
    conn.execute("create table emails (id integer primary key, source_file text, folder text, "
                 "message_id text, date_iso text)")
    conn.execute("insert into emails values (1, ?, 'INBOX', '<a@x.invalid>', '2026-10-12T16:00:00Z')",
                 (f"gmail:{MAILBOX}/INBOX",))
    conn.commit()
    conn.close()
    monkeypatch.setattr("os.environ", dict(NO_STORAGE_ENV))
    monkeypatch.setattr("origenlab_worker.database.open_worker_db", _fake_db)
    monkeypatch.setattr(shadow, "read_v2_ids", lambda *_a: {"a@x.invalid"})
    monkeypatch.setattr("sys.argv", ["x", "--sqlite", str(sqlite_path), "--since", "2026-10-12T15:00:00Z",
                                     "--out", str(tmp_path / "out")])
    assert shadow.main() == 0
