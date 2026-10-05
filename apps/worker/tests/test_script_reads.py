"""The owner scripts' two SQL reads, as the real `origenlab_worker` login on a disposable database
(its RLS included). Invented rows only."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from dbhelp import owner_rows, seed_mailbox
from mailfixtures import MAILBOX
from origenlab_worker.database import local_test_target, open_worker_db
from scriptload import load_script
from v2_command_harness import build_disposable_database, needs_worker_db, worker_dsn

pytestmark = needs_worker_db

parity = load_script("payload_parity")
shadow = load_script("shadow_reconcile")
SINCE = datetime(2026, 10, 12, 15, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def dsn():
    yield from build_disposable_database()


def test_read_staged_returns_only_the_staged_gmail_records(dsn) -> None:
    staged = {"gmail_message_id": "p1", "staging_source_record_sha256": "a" * 64, "tier": "G1_gmail_doc"}
    # 'gmail_message' is a kind from the slice-2 migration; use it for the first two rows.
    for kind, key, payload in (
        ("gmail_message", "gmail_message:p1", staged),
        ("gmail_message", "gmail_message:p2", {"gmail_message_id": "p2", "staging_source_record_sha256": None}),
        ("workbook_import", "other:p3", {"staging_source_record_sha256": "b" * 64}),
    ):
        owner_rows(dsn, "insert into evidence.source_record (kind, dedupe_key, payload) values (%s, %s, %s::jsonb)",
                   (kind, key, json.dumps(payload)))
    with open_worker_db(local_test_target(worker_dsn(dsn))) as db:
        assert parity.read_staged(db.connection) == [("gmail_message:p1", staged)]


def test_read_v2_ids_returns_normalised_ids_of_the_mailbox_inside_the_window(dsn) -> None:
    mailbox = seed_mailbox(dsn)
    other = owner_rows(dsn, "insert into comms.mailbox (address_norm) values ('otra@example.invalid') returning id::text")[0][0]
    for mailbox_id, pid, rfc, when in (
        (mailbox, "v1", "in@x.invalid", "2026-10-12T16:00:00Z"),
        (mailbox, "v2", "edge@x.invalid", "2026-10-10T16:00:00Z"),    # inside the 2-day margin
        (mailbox, "v3", "old@x.invalid", "2026-10-09T16:00:00Z"),     # outside
        (mailbox, "v4", None, "2026-10-13T16:00:00Z"),                # no Message-ID
        (other, "v5", "elsewhere@x.invalid", "2026-10-13T16:00:00Z"), # another mailbox
    ):
        owner_rows(dsn, "insert into comms.message (mailbox_id, provider_message_id, rfc822_message_id_norm, "
                        "direction, internal_date) values (%s, %s, %s, 'inbound', %s)", (mailbox_id, pid, rfc, when))
    with open_worker_db(local_test_target(worker_dsn(dsn))) as db:
        assert shadow.read_v2_ids(db.connection, SINCE) == {"in@x.invalid", "edge@x.invalid"}
