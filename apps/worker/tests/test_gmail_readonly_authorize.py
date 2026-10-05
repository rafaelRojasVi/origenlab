"""The owner's one-time consent helper: it writes a token file only for contacto@ and gmail.readonly."""

from __future__ import annotations

from pathlib import Path

import pytest

from mailfixtures import MAILBOX
from origenlab_worker.gmail_client import READONLY_SCOPE
from scriptload import load_script

authorize = load_script("gmail_readonly_authorize")


def test_the_file_holds_the_three_cron_values_for_contacto() -> None:
    out = authorize.token_payload("cid", "sec", "rt", "Contacto@OrigenLab.cl", [READONLY_SCOPE])
    assert out == {
        "ORIGENLAB_WORKER_GMAIL_CLIENT_ID": "cid",
        "ORIGENLAB_WORKER_GMAIL_CLIENT_SECRET": "sec",
        "ORIGENLAB_WORKER_GMAIL_REFRESH_TOKEN": "rt",
        "address": MAILBOX,
    }


@pytest.mark.parametrize("email,scopes,refresh", [
    ("otra@example.invalid", [READONLY_SCOPE], "rt"),
    (MAILBOX, [READONLY_SCOPE, "https://mail.google.com/"], "rt"),
    (MAILBOX, [], "rt"),
    (MAILBOX, [READONLY_SCOPE], None),
])
def test_another_account_another_scope_or_no_refresh_token_writes_nothing(email, scopes, refresh) -> None:
    with pytest.raises(SystemExit):
        authorize.token_payload("cid", "sec", refresh, email, scopes)


def test_the_file_is_private(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "token.json"
    authorize.write_private(path, {"a": "b"})
    assert oct(path.stat().st_mode & 0o777) == "0o600"
