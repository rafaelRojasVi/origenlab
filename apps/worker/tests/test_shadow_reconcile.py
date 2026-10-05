"""The shadow-week reconciliation's pure core, on an invented V1 SQLite file."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mailfixtures import MAILBOX
from scriptload import load_script

shadow = load_script("shadow_reconcile")
SINCE = datetime(2026, 10, 12, 15, 0, tzinfo=timezone.utc)


def v1_sqlite(tmp_path: Path, rows) -> Path:
    path = tmp_path / "emails.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("create table emails (id integer primary key, source_file text, folder text, "
                 "message_id text, date_iso text)")
    conn.executemany("insert into emails values (?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()
    return path


def test_only_contacto_gmail_rows_since_go_live_are_read(tmp_path: Path) -> None:
    path = v1_sqlite(tmp_path, [
        (1, f"gmail:{MAILBOX}/INBOX", "INBOX", "<a@x.invalid>", "2026-10-12T13:00:00-03:00"),            # 16:00Z
        (2, f"gmail:{MAILBOX}/INBOX", "INBOX", "<b@x.invalid>", "2026-10-12T11:00:00-03:00"),            # before
        (3, "gmail:otra@example.invalid/INBOX", "INBOX", "<c@x.invalid>", "2026-10-13T10:00:00-03:00"),  # other box
        (4, f"gmail:{MAILBOX}/[Gmail]/Enviados", "[Gmail]/Enviados", "<d@x.invalid>", "2026-10-13T10:00:00-03:00"),
    ])
    rows, undated = shadow.read_v1_rows(path, SINCE)
    assert ([row.email_id for row in rows], undated) == ([1, 4], [])


def test_every_v1_row_is_present_explained_or_missing() -> None:
    at = "2026-10-12T13:00:00-03:00"
    rows = [
        shadow.V1Row(1, "INBOX", "<A@x.invalid>", at),
        shadow.V1Row(2, "[Gmail]/Spam", "<b@x.invalid>", at),
        shadow.V1Row(3, "[Gmail]/Borradores", "<c@x.invalid>", at),
        shadow.V1Row(4, "INBOX", "<d@x.invalid>", at),
        shadow.V1Row(5, "INBOX", None, at),
    ]
    counts, missing = shadow.reconcile(rows, {"a@x.invalid"})
    assert dict(counts) == {"present": 1, "explained_spam": 1, "explained_draft": 1, "missing": 1, "no_message_id": 1}
    assert [row.email_id for row in missing] == [4, 5]


def test_the_detail_file_is_private_carries_no_address_and_never_lands_in_the_repository(tmp_path: Path) -> None:
    path = shadow.write_missing(tmp_path / "out", [shadow.V1Row(4, "INBOX", "<d@x.invalid>", "2026-10-12")])
    assert oct(path.stat().st_mode & 0o777) == "0o600" and "d@x.invalid" not in path.read_text()
    with pytest.raises(SystemExit):
        shadow.write_missing(shadow.REPO_ROOT / "apps", [])


def test_missing_mail_is_split_into_sent_received_and_other() -> None:
    at = "2026-10-12T13:00:00-03:00"
    rows = [
        shadow.V1Row(1, "[Gmail]/Enviados", "<s1@x.invalid>", at),
        shadow.V1Row(2, "[Gmail]/Enviados", "<s2@x.invalid>", at),
        shadow.V1Row(3, "INBOX", "<r@x.invalid>", at),
        shadow.V1Row(4, "[Gmail]/Todos", "<o@x.invalid>", at),
    ]
    assert dict(shadow.missing_by_direction(rows)) == {"missing_sent": 2, "missing_received": 1, "missing_other": 1}


def test_extra_ids_are_in_v2_only_and_the_id_files_are_private(tmp_path: Path) -> None:
    rows = [shadow.V1Row(1, "INBOX", "<A@x.invalid>", "2026-10-12")]
    assert shadow.extra_ids(rows, {"a@x.invalid", "z@x.invalid"}) == ["z@x.invalid"]
    path = shadow.write_ids(tmp_path / "out", "extra_message_ids.txt", ["z@x.invalid"])
    assert oct(path.stat().st_mode & 0o777) == "0o600" and path.read_text() == "z@x.invalid\n"
    with pytest.raises(SystemExit):
        shadow.write_ids(shadow.REPO_ROOT / "apps", "x.txt", [])


def test_a_naive_date_is_read_as_utc_and_an_undated_row_is_counted(tmp_path: Path) -> None:
    mine = f"gmail:{MAILBOX}/INBOX"
    path = v1_sqlite(tmp_path, [
        (1, mine, "INBOX", "<a@x.invalid>", "2026-10-12T15:30:00"),    # naive, 15:30Z: after go-live
        (2, mine, "INBOX", "<b@x.invalid>", "2026-10-12T14:30:00"),    # naive, 14:30Z: before
        (3, mine, "INBOX", "<c@x.invalid>", None),                      # no date
        (4, mine, "INBOX", "<d@x.invalid>", ""),                        # empty
        (5, mine, "INBOX", "<e@x.invalid>", "not a date"),              # unparseable
        (6, mine, "INBOX", "<f@x.invalid>", "2026-10-01 garbage"),      # unparseable, sorts early
        (7, "gmail:otra@example.invalid/INBOX", "INBOX", "<g@x.invalid>", None),  # other mailbox
    ])
    rows, undated = shadow.read_v1_rows(path, SINCE)
    assert ([row.email_id for row in rows], [r.email_id for r in undated]) == ([1], [3, 4, 5, 6])


def test_an_undated_row_makes_the_exit_code_one(tmp_path: Path, monkeypatch, capsys) -> None:
    from contextlib import contextmanager
    from types import SimpleNamespace

    path = v1_sqlite(tmp_path, [(1, f"gmail:{MAILBOX}/INBOX", "INBOX", "<a@x.invalid>", None)])

    @contextmanager
    def fake_db(_target):
        yield SimpleNamespace(connection=None)

    import origenlab_worker.cli as cli
    import origenlab_worker.database as database
    monkeypatch.setattr(cli, "config_from_env", lambda *_a, **_k: SimpleNamespace(database=None))
    monkeypatch.setattr(database, "open_worker_db", fake_db)
    monkeypatch.setattr(shadow, "read_v2_ids", lambda *_a: set())
    monkeypatch.setattr("sys.argv", ["x", "--sqlite", str(path), "--since", "2026-10-12T15:00:00Z",
                                     "--out", str(tmp_path / "out")])
    assert shadow.main() == 1
    assert "undated=1" in capsys.readouterr().out


def _undated(*specs):
    return [shadow.V1Row(i, folder, None, None) for i, folder in specs]


def test_a_folder_explained_undated_row_never_counts() -> None:
    rows = _undated((1, "INBOX"), (2, "[Gmail]/Spam"), (3, "[Gmail]/Borradores"), (4, "[Gmail]/Papelera"))
    assert [r.email_id for r in shadow.undated_in_scope(rows, None)] == [1]


def test_undated_rows_count_only_above_the_go_live_v1_id() -> None:
    rows = _undated((10, "INBOX"), (11, "INBOX"), (12, "[Gmail]/Enviados"))
    assert [r.email_id for r in shadow.undated_in_scope(rows, 10)] == [11, 12]
    assert [r.email_id for r in shadow.undated_in_scope(rows, None)] == [10, 11, 12]


def test_the_undated_file_is_private_and_lists_ids_folders_and_dates(tmp_path: Path) -> None:
    path = shadow.write_undated(tmp_path / "out", [shadow.V1Row(7, "INBOX", "<a@x.invalid>", "garbage")])
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert path.read_text().splitlines() == ["v1_email_id,folder,date_iso", "7,INBOX,garbage"]
    assert "x.invalid" not in path.read_text()
    with pytest.raises(SystemExit):
        shadow.write_undated(shadow.REPO_ROOT / "apps", [])


def test_historical_and_folder_explained_undated_rows_do_not_fail_the_run(tmp_path: Path, monkeypatch, capsys) -> None:
    from contextlib import contextmanager
    from types import SimpleNamespace

    mine = f"gmail:{MAILBOX}/INBOX"
    path = v1_sqlite(tmp_path, [
        (5, mine, "INBOX", "<a@x.invalid>", None),                       # historical: id <= go-live id
        (50, mine, "[Gmail]/Spam", "<b@x.invalid>", None),               # explained by its folder
    ])

    @contextmanager
    def fake_db(_target):
        yield SimpleNamespace(connection=None)

    import origenlab_worker.cli as cli
    import origenlab_worker.database as database
    monkeypatch.setattr(cli, "config_from_env", lambda *_a, **_k: SimpleNamespace(database=None))
    monkeypatch.setattr(database, "open_worker_db", fake_db)
    monkeypatch.setattr(shadow, "read_v2_ids", lambda *_a: set())
    argv = ["x", "--sqlite", str(path), "--since", "2026-10-12T15:00:00Z", "--out", str(tmp_path / "out")]
    monkeypatch.setattr("sys.argv", argv + ["--since-v1-id", "10"])
    assert shadow.main() == 0
    assert "undated=0" in capsys.readouterr().out
    assert (tmp_path / "out" / "undated.csv").read_text().count("\n") == 1  # header only
    monkeypatch.setattr("sys.argv", argv)  # no bound: the historical row counts
    assert shadow.main() == 1
    assert "undated=1" in capsys.readouterr().out
