"""The worker's writes on a disposable `origenlab_test_<hex>` database as the real `origenlab_worker`
login: one message per transaction, idempotent on every unique key, the mailbox, and the run lock."""

from __future__ import annotations

import time
from datetime import timedelta

import psycopg
import pytest

from dbhelp import owner_rows, seed_mailbox
from mailfixtures import INTERNAL_MS, MAILBOX, RAW_8BIT, make_raw
from origenlab_worker.capture import build_capture, unparsed_message
from origenlab_worker.database import RecordOutcome, local_test_target, open_worker_db
from origenlab_worker.gmail_client import READONLY_SCOPE
from v2_command_harness import build_disposable_database, needs_worker_db, worker_dsn

pytestmark = needs_worker_db

RAW = make_raw(pdf_name="Solicitud CN 1005.pdf", cc="Pedro <pedro@otro.invalid>")


@pytest.fixture(scope="module")
def dsn():
    yield from build_disposable_database()


@pytest.fixture(scope="module")
def mailbox_id(dsn):
    return seed_mailbox(dsn)


@pytest.fixture
def db(dsn):
    with open_worker_db(local_test_target(worker_dsn(dsn))) as worker:
        yield worker


def capture(gmail_id, raw=RAW, labels=("INBOX",)):
    return build_capture(raw, gmail_message_id=gmail_id, gmail_thread_id="t-1", label_ids=labels,
                         label_names={}, internal_date_ms=INTERNAL_MS, mailbox_address=MAILBOX)


def record(db, mailbox_id, c, raw=RAW):
    return db.record(c, mailbox_id=mailbox_id, eml_path=f"mail/{MAILBOX}/2026/10/{c.provider_message_id}.eml",
                     eml_sha256=c.payload["raw_sha256"], size_bytes=len(raw))


def test_a_capture_is_one_message_its_people_its_attachment_and_pending_evidence(db, dsn, mailbox_id) -> None:
    c = capture("w1")
    assert record(db, mailbox_id, c) == RecordOutcome(new_message=True, evidence_created=True)
    assert db.message_exists(mailbox_id, "w1")
    assert owner_rows(dsn, """
        select m.direction, m.parse_status, m.labels, m.rfc822_message_id_norm,
               (select count(*) from comms.message_participant p where p.message_id = m.id),
               (select count(*) from comms.attachment a where a.message_id = m.id),
               s.review_status, s.source_uri, s.payload->>'raw_sha256'
          from comms.message m
          join evidence.source_record s on s.dedupe_key = 'gmail_message:' || m.provider_message_id
         where m.provider_message_id = 'w1'
    """) == [("inbound", "parsed", ["INBOX"], "m1@cliente.invalid", 3, 1, "pending", "gmail://msg/w1",
              c.payload["raw_sha256"])]


def test_recording_the_same_message_again_writes_nothing(db, dsn, mailbox_id) -> None:
    c = capture("w2")
    record(db, mailbox_id, c)
    assert record(db, mailbox_id, c) == RecordOutcome(new_message=False, evidence_created=False)
    assert owner_rows(dsn, "select count(*) from comms.message where provider_message_id = 'w2'") == [(1,)]
    assert owner_rows(dsn, "select count(*) from evidence.source_record where dedupe_key = 'gmail_message:w2'") == [(1,)]


def test_a_campaign_copy_is_a_message_without_evidence(db, dsn, mailbox_id) -> None:
    raw = make_raw(frm=f"OrigenLab <{MAILBOX}>", to="x@cliente.invalid", list_unsubscribe=True)
    assert record(db, mailbox_id, capture("w3", raw, ("SENT",)), raw) == RecordOutcome(True, False)
    assert owner_rows(dsn, "select direction from comms.message where provider_message_id = 'w3'") == [("outbound",)]
    assert owner_rows(dsn, "select count(*) from evidence.source_record where dedupe_key = 'gmail_message:w3'") == [(0,)]


@pytest.mark.parametrize("gmail_id,reason,eml_path,sha", [
    ("w4", "no_from_header", "mail/x/w4.eml", "b" * 64),
    ("w5", "too_large", None, None),
])
def test_an_unparsed_message_is_kept_with_its_reason_and_no_evidence(db, dsn, mailbox_id, gmail_id, reason, eml_path, sha) -> None:
    row = unparsed_message(gmail_message_id=gmail_id, gmail_thread_id=None, label_ids=("INBOX",), label_names={},
                           internal_date_ms=INTERNAL_MS, headers={"subject": "x"}, mailbox_address=MAILBOX,
                           reason=reason)
    assert db.record_unparsed(row, mailbox_id=mailbox_id, eml_path=eml_path, eml_sha256=sha, size_bytes=10)
    assert not db.record_unparsed(row, mailbox_id=mailbox_id, eml_path=eml_path, eml_sha256=sha, size_bytes=10)
    assert owner_rows(dsn, "select parse_status, parse_error, eml_storage_path, eml_sha256 from comms.message "
                           "where provider_message_id = %s", (gmail_id,)) == [("parse_failed", reason, eml_path, sha)]
    assert owner_rows(dsn, "select count(*) from evidence.source_record where dedupe_key = %s",
                      (f"gmail_message:{gmail_id}",)) == [(0,)]


def test_eight_bit_headers_reach_postgres(db, dsn, mailbox_id) -> None:
    """Review Focus 4, database half: the cleaned text inserts into `text` and `jsonb`."""
    c = build_capture(RAW_8BIT, gmail_message_id="w6", gmail_thread_id=None, label_ids=("INBOX",),
                      label_names={}, internal_date_ms=INTERNAL_MS, mailbox_address=MAILBOX)
    record(db, mailbox_id, c, RAW_8BIT)
    assert owner_rows(dsn, """
        select m.subject, s.payload->>'subject_raw'
          from comms.message m join evidence.source_record s on s.dedupe_key = 'gmail_message:w6'
         where m.provider_message_id = 'w6'
    """) == [("Cotizaci�n urgente",) * 2]


def test_authorize_takes_the_baseline_once_and_a_re_authorization_resumes(db, dsn, mailbox_id) -> None:
    box = db.mailbox(MAILBOX)
    assert (box.authorization_state, box.history_id, box.last_synced_at) == ("unauthorized", None, None)
    assert db.authorize(mailbox_id, baseline_history_id="100", scopes=[READONLY_SCOPE]) == "baseline"
    box = db.mailbox(MAILBOX)
    assert (box.authorization_state, box.history_id) == ("authorized", "100") and box.last_synced_at is not None
    db.mark_revoked(mailbox_id)
    assert db.mailbox(MAILBOX).authorization_state == "revoked"
    assert db.authorize(mailbox_id, baseline_history_id="999", scopes=[READONLY_SCOPE]) == "resumed"
    assert db.mailbox(MAILBOX).history_id == "100"
    db.advance_cursor(mailbox_id, "150")
    assert db.mailbox(MAILBOX).history_id == "150"
    assert owner_rows(dsn, "select granted_scopes from comms.mailbox where id = %s", (mailbox_id,)) == [([READONLY_SCOPE],)]


def test_one_run_at_a_time_and_a_dead_runs_session_is_ended(dsn) -> None:
    target = local_test_target(worker_dsn(dsn))
    with open_worker_db(target) as first, open_worker_db(target) as second:
        assert first.try_lock()
        assert not second.try_lock()                       # idle, but not for 30 minutes
        time.sleep(0.2)
        assert second.try_lock(stale_after=timedelta(0))   # idle past the threshold: ended, lock taken
        with pytest.raises(psycopg.OperationalError):
            first.connection.execute("select 1")
