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
    assert [row.email_id for row in shadow.read_v1_rows(path, SINCE)] == [1, 4]


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
