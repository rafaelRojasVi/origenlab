"""One capture run against in-memory fakes: modes, counters, cursor rules and the Review Focus."""

from __future__ import annotations

from datetime import timedelta

import pytest

from fakes import LAST_SYNC, FakeDb, FakeGmail, FakeMessage, FakeStore
from mailfixtures import MAILBOX, RAW_NO_FROM, make_raw
from origenlab_worker.gmail_client import READONLY_SCOPE, GmailUnavailable
from origenlab_worker.gmail_sync import (
    EXIT_AUTH,
    EXIT_CONFIG,
    EXIT_FAILED,
    EXIT_OK,
    MAX_RAW_BYTES,
    run_gmail_sync,
)

INBOUND = make_raw(pdf_name="Solicitud CN 1005.pdf")
OUTBOUND = make_raw(frm=f"OrigenLab <{MAILBOX}>", to="Ana <ana@cliente.invalid>", subject="RE: Cotización CN01005")
CAMPAIGN = make_raw(frm=f"OrigenLab <{MAILBOX}>", to="x@cliente.invalid", list_unsubscribe=True)


def run(db, gmail, store=None, **kw):
    return run_gmail_sync(db=db, gmail=gmail, store=store or FakeStore(), enabled=kw.pop("enabled", True), **kw)


def gmail_with(**messages: FakeMessage) -> FakeGmail:
    return FakeGmail(pages=[list(messages)], messages=dict(messages))


def test_a_normal_run_stores_inbox_and_sent_skips_spam_and_moves_the_cursor() -> None:
    db = FakeDb()
    store = FakeStore()
    gmail = gmail_with(m1=FakeMessage(INBOUND), m2=FakeMessage(OUTBOUND, labels=("SENT",)),
                       m3=FakeMessage(INBOUND, labels=("SPAM",)))
    report = run(db, gmail, store)
    assert (report.mode, report.exit_code) == ("history", EXIT_OK)
    c = report.counts
    assert (c.seen, c.stored, c.evidence, c.skipped_spam) == (3, 2, 2, 1)
    assert db.cursor == ["200"] and len(store.objects) == 2 and gmail.label_calls == 1
    assert set(store.objects) == {f"{MAILBOX}/2026/10/m1.eml", f"{MAILBOX}/2026/10/m2.eml"}
    assert "m3" not in gmail.raw_calls  # spam is never downloaded


def test_drafts_and_trash_are_skipped_and_counted() -> None:
    gmail = gmail_with(d=FakeMessage(INBOUND, labels=("DRAFT",)), t=FakeMessage(INBOUND, labels=("INBOX", "TRASH")))
    c = run(FakeDb(), gmail).counts
    assert (c.skipped_draft, c.skipped_trash, c.stored) == (1, 1, 0)


def test_a_message_listed_twice_and_in_both_inbox_and_sent_is_stored_once() -> None:
    """Review Focus 2."""
    self_note = make_raw(frm=f"OrigenLab <{MAILBOX}>", to=MAILBOX, subject="nota")
    gmail = FakeGmail(pages=[["m1"], ["m1"]], messages={"m1": FakeMessage(self_note, labels=("INBOX", "SENT"))})
    db = FakeDb()
    report = run(db, gmail)
    assert (report.counts.seen, report.counts.stored, report.counts.evidence) == (1, 1, 1)
    status, capture, *_ = db.messages["m1"]
    assert capture.direction == "outbound"
    again = run(db, FakeGmail(pages=[["m1"]], messages=gmail.messages))
    assert (again.counts.duplicates, again.counts.stored, len(db.evidence)) == (1, 0, 1)


def test_history_expiring_between_pages_resyncs_from_the_last_sync_minus_a_day() -> None:
    """Review Focus 1: page 1 read, page 2 404 → the window restarts from last_synced_at − 1 day,
    and the new cursor is the history id read *before* the list (fake moves it to 999 during it)."""
    gmail = FakeGmail(pages=[["m1"], ["m2"]], expire_on_page=1, after=["m1", "m2"],
                      messages={"m1": FakeMessage(INBOUND), "m2": FakeMessage(OUTBOUND, labels=("SENT",))})
    db = FakeDb()
    report = run(db, gmail)
    assert (report.mode, report.exit_code, report.counts.stored) == ("resync", EXIT_OK, 2)
    assert gmail.after_calls == [int((LAST_SYNC - timedelta(days=1)).timestamp())]
    assert db.cursor == ["200"]


def test_a_mailbox_without_a_cursor_resyncs_instead_of_failing() -> None:
    db = FakeDb(history_id=None)
    report = run(db, FakeGmail(after=[]))
    assert (report.mode, db.cursor) == ("resync", ["200"])


def test_a_huge_message_is_recorded_without_its_body_and_the_run_continues() -> None:
    """Review Focus 3: over the cap (Gmail's own sizeEstimate) it is never downloaded."""
    big = FakeMessage(INBOUND, size=MAX_RAW_BYTES + 1)
    gmail = gmail_with(big=big, m2=FakeMessage(OUTBOUND, labels=("SENT",)))
    db = FakeDb()
    report = run(db, gmail)
    assert (report.exit_code, report.counts.too_large, report.counts.stored) == (EXIT_OK, 1, 1)
    assert "big" not in gmail.raw_calls
    status, row, eml_path, digest = db.messages["big"]
    assert (status, row.reason, eml_path, digest) == ("parse_failed", "too_large", None, None)
    assert db.cursor == ["200"]


def test_storage_refusing_a_message_gmail_says_is_under_the_cap_fails_the_run() -> None:
    """A misconfigured bucket limit must not silently turn evidence into a too_large row."""
    gmail = gmail_with(m1=FakeMessage(INBOUND), m3=FakeMessage(INBOUND))
    db = FakeDb()
    report = run(db, gmail, FakeStore(too_large={"m3"}))
    assert (report.exit_code, report.error) == (EXIT_FAILED, "storage_too_large_below_cap")
    assert (list(db.messages), db.cursor, report.counts.too_large) == (["m1"], [], 0)


def test_consent_revoked_between_pages_stops_the_run_and_marks_the_mailbox() -> None:
    """Review Focus 5: exit 2, `revoked`, cursor untouched."""
    db = FakeDb()
    report = run(db, FakeGmail(pages=[["m1"], ["m2"]], revoke_on_page=1))
    assert (report.mode, report.exit_code, report.error) == ("history", EXIT_AUTH, "invalid_grant")
    assert (db.revoked, db.cursor) == (True, [])


def test_consent_revoked_mid_window_keeps_what_committed_and_holds_the_cursor() -> None:
    gmail = gmail_with(m1=FakeMessage(INBOUND), m2=FakeMessage(OUTBOUND, labels=("SENT",)))
    gmail.revoke_on_metadata = "m2"
    db = FakeDb()
    report = run(db, gmail)
    assert (report.exit_code, report.counts.stored, list(db.messages), db.cursor, db.revoked) == (EXIT_AUTH, 1, ["m1"], [], True)


def test_a_parse_failure_keeps_the_eml_and_no_evidence_and_the_run_continues() -> None:
    gmail = gmail_with(bad=FakeMessage(RAW_NO_FROM), m2=FakeMessage(INBOUND))
    db = FakeDb()
    store = FakeStore()
    report = run(db, gmail, store)
    assert (report.counts.parse_failed, report.counts.stored, report.counts.evidence) == (1, 1, 1)
    status, row, eml_path, digest = db.messages["bad"]
    assert (status, row.reason, eml_path) == ("parse_failed", "no_from_header", f"mail/{MAILBOX}/2026/10/bad.eml")
    assert digest and f"{MAILBOX}/2026/10/bad.eml" in store.objects


def test_gmail_failing_mid_window_exits_1_and_holds_the_cursor() -> None:
    gmail = gmail_with(m1=FakeMessage(INBOUND), m2=FakeMessage(OUTBOUND, labels=("SENT",)))
    gmail.unavailable_raw = "m2"
    db = FakeDb()
    report = run(db, gmail)
    assert (report.exit_code, report.error, list(db.messages), db.cursor) == (EXIT_FAILED, "http_503", ["m1"], [])


def test_a_failure_is_logged_by_class_never_by_its_text() -> None:
    db = FakeDb()
    db.fail_on_record = "m1"
    report = run(db, gmail_with(m1=FakeMessage(INBOUND)))
    assert report.error == "RuntimeError" and "ana@" not in str(report.as_log())


def test_a_message_deleted_before_the_run_reached_it_is_gone() -> None:
    gmail = FakeGmail(pages=[["ghost", "m1"]], messages={"m1": FakeMessage(INBOUND)})
    c = run(FakeDb(), gmail).counts
    assert (c.gone, c.stored) == (1, 1)


def test_a_campaign_send_is_stored_without_evidence() -> None:
    db = FakeDb()
    c = run(db, gmail_with(c1=FakeMessage(CAMPAIGN, labels=("SENT",)))).counts
    assert (c.stored, c.bulk_sends, c.evidence, db.evidence) == (1, 1, 0, set())


def test_a_run_with_nothing_new_still_refreshes_the_cursor() -> None:
    db = FakeDb()
    assert run(db, FakeGmail()).counts.seen == 0 and db.cursor == ["200"]


def test_a_dry_run_reads_everything_and_writes_nothing() -> None:
    db, store = FakeDb(), FakeStore()
    gmail = gmail_with(m1=FakeMessage(INBOUND), c1=FakeMessage(CAMPAIGN, labels=("SENT",)), bad=FakeMessage(RAW_NO_FROM))
    report = run(db, gmail, store, dry_run=True)
    assert report.mode == "history_dry_run"
    assert (report.counts.stored, report.counts.bulk_sends, report.counts.parse_failed) == (2, 1, 1)
    assert (db.messages, db.cursor, store.objects) == ({}, [], {})


def test_another_run_holding_the_lock_is_a_quiet_exit() -> None:
    gmail = FakeGmail()
    report = run(FakeDb(locked_by_other=True), gmail)
    assert (report.mode, report.exit_code, gmail.calls, gmail.after_calls) == ("locked", EXIT_OK, [], [])


def test_a_stuck_lock_holder_fails_the_run_loudly() -> None:
    gmail = FakeGmail()
    report = run(FakeDb(locked_by_other="stuck"), gmail)
    assert (report.mode, report.exit_code, report.error, gmail.calls) == ("locked", EXIT_FAILED, "locked_by_stuck_session", [])


def test_the_pause_switch_and_an_unauthorized_mailbox_touch_nothing() -> None:
    for kwargs, mode in (({"enabled": False}, "paused"), ({}, "not_authorized")):
        db = FakeDb(state="unauthorized") if mode == "not_authorized" else FakeDb()
        gmail = FakeGmail(pages=[["m1"]], messages={"m1": FakeMessage(INBOUND)})
        report = run(db, gmail, **kwargs)
        assert (report.mode, report.exit_code) == (mode, EXIT_OK)
        assert (gmail.calls, gmail.raw_calls, gmail.after_calls, db.cursor, db.messages) == ([], [], [], [], {})


def test_a_missing_mailbox_row_is_a_configuration_error() -> None:
    report = run(FakeDb(no_mailbox=True), FakeGmail())
    assert (report.mode, report.exit_code) == ("no_mailbox", EXIT_CONFIG)


def test_a_token_for_another_account_is_refused() -> None:
    db = FakeDb()
    report = run(db, FakeGmail(email="otra@example.invalid"))
    assert (report.mode, report.exit_code, db.cursor) == ("wrong_account", EXIT_AUTH, [])


def test_init_takes_the_baseline_once_even_while_paused() -> None:
    db, store = FakeDb(state="unauthorized", history_id=None, last_synced_at=None), FakeStore()
    report = run(db, FakeGmail(current="4321"), store, init=True, enabled=False)
    assert (report.mode, db.authorized, store.checked) == ("init_baseline", [("4321", [READONLY_SCOPE])], 1)


def test_init_after_a_pause_resumes_the_existing_cursor() -> None:
    db = FakeDb(state="revoked", history_id="150")
    assert run(db, FakeGmail(), init=True).mode == "init_resumed"


def test_init_on_an_authorized_mailbox_fails_loudly() -> None:
    db = FakeDb()
    report = run(db, FakeGmail(), init=True)
    assert (report.mode, report.exit_code, db.authorized) == ("already_initialized", EXIT_CONFIG, [])


def test_init_dry_run_checks_gmail_and_storage_and_writes_nothing() -> None:
    db, store = FakeDb(state="unauthorized", history_id=None), FakeStore()
    report = run(db, FakeGmail(), store, init=True, dry_run=True)
    assert (report.mode, db.authorized, store.checked) == ("init_dry_run", [], 1)


def test_a_database_failure_while_marking_revoked_still_exits_2_by_class_only() -> None:
    db = FakeDb()

    def broken(mailbox_id):
        raise RuntimeError("ana@cliente.invalid in the DETAIL")

    db.mark_revoked = broken
    report = run(db, FakeGmail(pages=[["m1"]], revoke_on_page=0))
    assert (report.exit_code, report.error) == (EXIT_AUTH, "invalid_grant+RuntimeError")


def test_a_storage_conflict_stops_the_run_by_class_and_holds_the_cursor() -> None:
    gmail = gmail_with(m1=FakeMessage(INBOUND), m2=FakeMessage(OUTBOUND, labels=("SENT",)))
    store = FakeStore()
    store.objects[f"{MAILBOX}/2026/10/m2.eml"] = b"different bytes"
    db = FakeDb()
    report = run(db, gmail, store)
    assert (report.exit_code, report.error, list(db.messages), db.cursor) == (EXIT_FAILED, "storage_object_conflict", ["m1"], [])


@pytest.mark.parametrize("kind", ["token_refresh_failed", "scope_not_readonly", "unauthorized"])
def test_only_invalid_grant_revokes_the_mailbox(kind: str) -> None:
    db = FakeDb()
    report = run(db, FakeGmail(pages=[["m1"]], revoke_on_page=0, auth_kind=kind))
    assert (report.exit_code, report.error, db.revoked, db.cursor) == (EXIT_AUTH, kind, False, [])


def test_a_dry_run_never_revokes() -> None:
    db = FakeDb()
    report = run(db, FakeGmail(pages=[["m1"]], revoke_on_page=0), dry_run=True)
    assert (report.mode, report.exit_code, report.error, db.revoked) == ("history_dry_run", EXIT_AUTH, "invalid_grant", False)


def test_an_expired_history_without_a_last_sync_fails_closed() -> None:
    db = FakeDb(last_synced_at=None)
    gmail = FakeGmail(pages=[["m1"]], expire_on_page=0, after=["m1"], messages={"m1": FakeMessage(INBOUND)})
    report = run(db, gmail, now=lambda: LAST_SYNC)
    assert (report.mode, report.exit_code, report.error) == ("resync", EXIT_CONFIG, "no_resync_baseline")
    assert (gmail.after_calls, db.cursor, db.messages) == ([], [], {})


def test_a_bad_token_at_init_does_not_revoke_a_never_authorized_mailbox() -> None:
    db = FakeDb(state="unauthorized", history_id=None, last_synced_at=None)
    report = run(db, FakeGmail(revoke_on_profile=True), init=True)
    assert (report.exit_code, report.error, db.revoked) == (EXIT_AUTH, "invalid_grant", False)


def test_an_empty_history_id_never_becomes_the_cursor() -> None:
    db = FakeDb()
    gmail = gmail_with(m1=FakeMessage(INBOUND))
    gmail.current = ""
    report = run(db, gmail)
    assert (report.exit_code, report.error, db.cursor) == (EXIT_FAILED, "empty_history_id", [])


def test_a_failed_resync_listing_reports_resync_and_a_failed_dry_run_says_so() -> None:
    report = run(FakeDb(), FakeGmail(pages=[["m1"]], expire_on_page=0, after_error=True))
    assert (report.mode, report.exit_code, report.error) == ("resync", EXIT_FAILED, "http_503")
    dry = run(FakeDb(), FakeGmail(pages=[["m1"]], revoke_on_page=0), dry_run=True)
    assert dry.mode == "history_dry_run"
    failed = run(FakeDb(), FakeGmail(pages=[["m1"]], expire_on_page=0, after_error=True), dry_run=True)
    assert failed.mode == "resync_dry_run"


def test_a_database_error_is_its_class_and_sqlstate_never_its_text() -> None:
    import psycopg.errors

    class Leaky(psycopg.errors.UniqueViolation):
        sqlstate = "23505"

    db = FakeDb()

    def broken(*_a, **_k):
        raise Leaky("duplicate key (address)=(ana@cliente.invalid)")

    db.record = broken
    report = run(db, gmail_with(m1=FakeMessage(INBOUND)))
    assert report.error == "Leaky:23505" and "ana@" not in str(report.as_log())
    bare = FakeDb()
    bare.record = lambda *_a, **_k: (_ for _ in ()).throw(psycopg.OperationalError("ana@cliente.invalid"))
    assert run(bare, gmail_with(m1=FakeMessage(INBOUND))).error == "OperationalError"


def test_an_unknown_failure_stays_class_only() -> None:
    from origenlab_worker.gmail_sync import failure_code
    assert failure_code(KeyError("ana@cliente.invalid")) == "KeyError"


def test_a_draft_sent_later_is_skipped_on_run_one_and_captured_on_run_two() -> None:
    db, store = FakeDb(), FakeStore()
    draft = FakeMessage(OUTBOUND, labels=("DRAFT",))
    first = run(db, FakeGmail(pages=[["d1"]], messages={"d1": draft}), store)
    assert (first.counts.skipped_draft, first.counts.stored, list(db.messages)) == (1, 0, [])
    sent = FakeMessage(OUTBOUND, labels=("SENT",))  # history now lists it again, via labelAdded
    second = run(db, FakeGmail(pages=[["d1"]], messages={"d1": sent}), store)
    assert (second.counts.stored, second.counts.evidence, list(db.messages)) == (1, 1, ["d1"])
    third = run(db, FakeGmail(pages=[["d1"]], messages={"d1": sent}), store)
    assert (third.counts.duplicates, third.counts.stored) == (1, 0)


def test_history_mode_moves_the_cursor_to_the_profile_read_before_the_list() -> None:
    """A message added while the history pages are read is past the profile id, so the next run
    replays it as a duplicate instead of starting after it."""
    gmail = gmail_with(m1=FakeMessage(INBOUND))
    gmail.window_id = "260"  # history() answered later than the profile (200)
    db = FakeDb()
    assert run(db, gmail).mode == "history"
    assert db.cursor == ["200"]
