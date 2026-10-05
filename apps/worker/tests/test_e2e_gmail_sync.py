"""Spec §7.2 end to end: the real database boundary on a disposable `origenlab_test_<hex>` database
as the `origenlab_worker` login, a fake Gmail over HTTP and an in-process S3. Two runs with a
failure between messages, a duplicate, spam/draft/trash, a parse failure and a campaign copy, then
a resync — and the rows and the cursor are exact."""

from __future__ import annotations

import time

import pytest

from dbhelp import business_rows, owner_rows, seed_mailbox
from fake_gmail_server import FakeGmailServer, FakeMailbox
from mailfixtures import MAILBOX, RAW_NO_FROM, make_raw
from origenlab_worker.capture import PAYLOAD_KEYS
from origenlab_worker.database import local_test_target, open_worker_db
from origenlab_worker.gmail_client import GmailCredentials, GmailReader
from origenlab_worker.gmail_sync import run_gmail_sync
from origenlab_worker.storage import BUCKET, S3EmlStore
from v2_command_harness import build_disposable_database, needs_worker_db, worker_dsn

pytestmark = needs_worker_db

CREDS = GmailCredentials(client_id="cid", client_secret="sec", refresh_token="rt")


@pytest.fixture(scope="module")
def dsn():
    yield from build_disposable_database()


def new_mail(s: FakeMailbox, now_ms: int) -> None:
    """m1 a request with a CN PDF (listed twice), m2 our quotation, m3–m5 spam/draft/trash,
    m6 no From, m7 a campaign copy, m8 a plain reply."""
    s.add("m1", make_raw(pdf_name="Solicitud CN 1005.pdf"), labels=("INBOX",), internal_ms=now_ms)
    s.add("m2", make_raw(frm=f"OrigenLab <{MAILBOX}>", to="Ana <ana@cliente.invalid>", cc="compras@cliente.invalid",
                         subject="RE: Cotización CN01005", message_id="<m2@origenlab.invalid>",
                         pdf_name="Cotizacion CN01005.pdf", pdf_inline_with_cid=True),
          labels=("SENT",), internal_ms=now_ms + 1)
    s.add("m3", make_raw(message_id="<m3@cliente.invalid>"), labels=("SPAM",), internal_ms=now_ms + 2)
    s.add("m4", make_raw(message_id="<m4@cliente.invalid>"), labels=("DRAFT",), internal_ms=now_ms + 3)
    s.add("m5", make_raw(message_id="<m5@cliente.invalid>"), labels=("TRASH",), internal_ms=now_ms + 4)
    s.add("m6", RAW_NO_FROM, labels=("INBOX",), internal_ms=now_ms + 5)
    s.add("m7", make_raw(frm=f"OrigenLab <{MAILBOX}>", to="x@cliente.invalid", list_unsubscribe=True,
                         message_id="<m7@origenlab.invalid>"), labels=("SENT",), internal_ms=now_ms + 6)
    s.add("m8", make_raw(frm="Pedro <pedro@otro.invalid>", cc="ana@cliente.invalid", message_id="<m8@otro.invalid>"),
          labels=("INBOX",), internal_ms=now_ms + 7)
    s.list_again("m1")


def test_two_runs_a_failure_between_messages_and_a_resync_leave_exact_rows(dsn, s3_client) -> None:
    seed_mailbox(dsn)
    before = business_rows(dsn)
    target = local_test_target(worker_dsn(dsn))
    with FakeGmailServer() as server:
        reader = GmailReader(CREDS, api_base=server.api_base, token_url=server.token_url, sleep=lambda _s: None)
        store = S3EmlStore(s3_client)

        def run(**kw):
            with open_worker_db(target) as db:
                return run_gmail_sync(db=db, gmail=reader, store=store, enabled=True, **kw)

        assert run(init=True).mode == "init_baseline"
        assert owner_rows(dsn, "select authorization_state, history_id from comms.mailbox") == [("authorized", "100")]

        new_mail(server.state, int(time.time() * 1000) - 3_600_000)
        server.state.failing_raw = {"m7"}
        first = run()
        c = first.counts
        assert (first.mode, first.exit_code, first.error) == ("history", 1, "http_503")
        assert (c.seen, c.stored, c.evidence, c.parse_failed) == (7, 2, 2, 1)
        assert (c.skipped_spam, c.skipped_draft, c.skipped_trash) == (1, 1, 1)
        assert owner_rows(dsn, "select history_id from comms.mailbox") == [("100",)]

        server.state.failing_raw = set()
        second = run()
        c = second.counts
        assert (second.mode, second.exit_code) == ("history", 0)
        assert (c.seen, c.duplicates, c.stored, c.bulk_sends, c.evidence) == (8, 3, 2, 1, 1)
        latest = str(server.state.history_id)
        assert owner_rows(dsn, "select history_id from comms.mailbox") == [(latest,)]

        server.state.expired_below = 10_000
        third = run()
        assert (third.mode, third.exit_code, third.counts.duplicates, third.counts.stored) == ("resync", 0, 5, 0)
        assert owner_rows(dsn, "select history_id from comms.mailbox") == [(latest,)]

    assert owner_rows(dsn, "select provider_message_id, direction, parse_status, parse_error, "
                           "eml_storage_path is not null from comms.message order by 1") == [
        ("m1", "inbound", "parsed", None, True),
        ("m2", "outbound", "parsed", None, True),
        ("m6", "inbound", "parse_failed", "no_from_header", True),
        ("m7", "outbound", "parsed", None, True),
        ("m8", "inbound", "parsed", None, True),
    ]
    assert owner_rows(dsn, "select count(*) from comms.message_participant") == [(10,)]
    assert owner_rows(dsn, "select count(*) from comms.attachment") == [(2,)]
    assert owner_rows(dsn, "select dedupe_key, review_status from evidence.source_record "
                           "where kind = 'gmail_message' order by 1") == [
        ("gmail_message:m1", "pending"), ("gmail_message:m2", "pending"), ("gmail_message:m8", "pending")]
    keys = owner_rows(dsn, "select distinct jsonb_object_keys(payload) from evidence.source_record "
                           "where kind = 'gmail_message'")
    assert {key for (key,) in keys} == PAYLOAD_KEYS
    assert owner_rows(dsn, "select payload->'proposed_quote_numbers' from evidence.source_record "
                           "where dedupe_key = 'gmail_message:m2'") == [(["CN01005"],)]
    assert s3_client.list_objects_v2(Bucket=BUCKET)["KeyCount"] == 5
    assert business_rows(dsn) == before  # not one row in crm.* or outbound.*


@pytest.fixture(scope="module")
def label_dsn():
    yield from build_disposable_database()


def test_a_draft_sent_later_and_a_message_moved_out_of_spam_are_captured_on_the_next_run(label_dsn, s3_client) -> None:
    """R7, narrowed: `labelAdded` of SENT or INBOX makes a message capturable; both go through the
    real reader, HTTP, the database boundary and Storage."""
    seed_mailbox(label_dsn)
    target = local_test_target(worker_dsn(label_dsn))
    now_ms = int(time.time() * 1000) - 3_600_000
    with FakeGmailServer() as server:
        reader = GmailReader(CREDS, api_base=server.api_base, token_url=server.token_url, sleep=lambda _s: None)
        store = S3EmlStore(s3_client)

        def run(**kw):
            with open_worker_db(target) as db:
                return run_gmail_sync(db=db, gmail=reader, store=store, enabled=True, **kw)

        assert run(init=True).mode == "init_baseline"
        s = server.state
        s.add("dr1", make_raw(frm=f"OrigenLab <{MAILBOX}>", to="ana@cliente.invalid", message_id="<dr1@origenlab.invalid>"),
              labels=("DRAFT",), internal_ms=now_ms)
        s.add("sp1", make_raw(message_id="<sp1@cliente.invalid>"), labels=("SPAM",), internal_ms=now_ms + 1)
        first = run().counts
        assert (first.skipped_draft, first.skipped_spam, first.stored) == (1, 1, 0)
        s.messages["dr1"]["labels"].remove("DRAFT")
        s.add_label("dr1", "SENT")
        s.messages["sp1"]["labels"].remove("SPAM")  # «no es spam»
        s.add_label("sp1", "INBOX")
        second = run().counts
        assert (second.stored, second.evidence, second.skipped_draft, second.skipped_spam) == (2, 2, 0, 0)
        third = run().counts
        assert (third.seen, third.duplicates, third.stored) == (0, 0, 0)
    assert owner_rows(label_dsn, "select provider_message_id, direction from comms.message order by 1") == [
        ("dr1", "outbound"), ("sp1", "inbound")]
