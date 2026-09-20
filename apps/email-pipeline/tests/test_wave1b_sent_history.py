"""Canonical Gmail Sent evidence and the combined prior-contact union.

Every fixture is a synthetic in-memory SQLite database and every address is on
the reserved ``example.invalid`` domain. Nothing here opens a mailbox, and no
test may reach the network.
"""

from __future__ import annotations

import sqlite3

import pytest

from origenlab_email_pipeline.marketing_export_context import (
    DEFAULT_SENT_FOLDERS,
    load_sent_recipient_norms,
)
from origenlab_email_pipeline.migration.sent_history import (
    SOURCE_CAMPAIGN_ACCEPTED,
    SOURCE_OUTREACH_STATE,
    SOURCE_SENT_HISTORY,
    SentHistoryRefused,
    collect_sent_history,
    combine_prior_contact,
    resolve_mailbox,
)
from origenlab_email_pipeline.outbound_core import DEFAULT_GMAIL_USER_FALLBACK

MAILBOX = "operator@example.invalid"
FOLDER = DEFAULT_SENT_FOLDERS[0]
SNAPSHOT = "2026-09-05T04:24:25Z"


def _db(rows: list[tuple[str, str | None]], *, folder: str = FOLDER) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE emails (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " source_file TEXT NOT NULL, folder TEXT, subject TEXT, sender TEXT,"
        " recipients TEXT, date_iso TEXT, body TEXT)"
    )
    for index, (recipients, date_iso) in enumerate(rows, start=1):
        conn.execute(
            "INSERT INTO emails (source_file, folder, subject, sender, recipients,"
            " date_iso, body) VALUES (?,?,?,?,?,?,?)",
            (f"gmail:{MAILBOX}/{index}", folder, "s", MAILBOX, recipients, date_iso, "b"),
        )
    conn.commit()
    return conn


def _mailbox():
    return resolve_mailbox(gmail_user=MAILBOX, sent_folders=(FOLDER,))


# --- mailbox resolution -----------------------------------------------------


def test_mailbox_resolution_records_where_each_choice_came_from() -> None:
    explicit = resolve_mailbox(gmail_user=MAILBOX, sent_folders=("[Gmail]/Custom",))
    assert explicit.gmail_user == MAILBOX
    assert explicit.gmail_user_source == "operator"
    assert explicit.sent_folders == ("[Gmail]/Custom",)
    assert explicit.sent_folders_source == "operator"

    fallback = resolve_mailbox(gmail_user=None, sent_folders=None)
    assert fallback.gmail_user == DEFAULT_GMAIL_USER_FALLBACK
    assert fallback.sent_folders == DEFAULT_SENT_FOLDERS
    assert "DEFAULT" in fallback.gmail_user_source
    assert "DEFAULT" in fallback.sent_folders_source


def test_the_recorded_mailbox_carries_no_credential_or_private_path() -> None:
    record = _mailbox().to_record()
    assert set(record) == {
        "gmail_user",
        "gmail_user_source",
        "sent_folders",
        "sent_folders_source",
    }
    blob = str(record).lower()
    for forbidden in ("password", "token", "secret", "oauth", "/home/", ".env"):
        assert forbidden not in blob


# --- the delta --------------------------------------------------------------


def test_a_normal_non_campaign_message_after_the_snapshot_enters_the_delta() -> None:
    conn = _db([("plain@example.invalid", "2026-09-12T10:00:00+00:00")])
    evidence = collect_sent_history(conn, mailbox=_mailbox(), snapshot=SNAPSHOT)
    assert set(evidence.recipients) == {"plain@example.invalid"}
    assert evidence.recipients["plain@example.invalid"]["sent_message_count"] == 1
    assert evidence.complete is True


def test_pre_snapshot_sent_evidence_does_not_enter_the_delta() -> None:
    conn = _db(
        [
            ("old@example.invalid", "2026-08-30T10:00:00+00:00"),
            ("new@example.invalid", "2026-09-12T10:00:00+00:00"),
        ]
    )
    evidence = collect_sent_history(conn, mailbox=_mailbox(), snapshot=SNAPSHOT)
    assert set(evidence.recipients) == {"new@example.invalid"}
    assert evidence.rows_scanned == 2
    assert evidence.rows_after_snapshot == 1


def test_a_message_exactly_at_the_snapshot_instant_is_pre_snapshot() -> None:
    conn = _db(
        [
            ("edge@example.invalid", "2026-09-05T04:24:25+00:00"),
            ("after@example.invalid", "2026-09-05T04:24:26+00:00"),
        ]
    )
    evidence = collect_sent_history(conn, mailbox=_mailbox(), snapshot=SNAPSHOT)
    assert set(evidence.recipients) == {"after@example.invalid"}


def test_an_offset_timestamp_is_compared_in_utc_not_as_a_string() -> None:
    """``2026-09-05T02:00:00-03:00`` is 05:00Z — after the 04:24:25Z snapshot."""
    conn = _db([("offset@example.invalid", "2026-09-05T02:00:00-03:00")])
    evidence = collect_sent_history(conn, mailbox=_mailbox(), snapshot=SNAPSHOT)
    assert set(evidence.recipients) == {"offset@example.invalid"}
    assert evidence.recipients["offset@example.invalid"]["first_sent_at"].endswith("Z")


def test_a_multi_recipient_row_contributes_every_address() -> None:
    conn = _db([("a@example.invalid, b@example.invalid", "2026-09-12T10:00:00+00:00")])
    evidence = collect_sent_history(conn, mailbox=_mailbox(), snapshot=SNAPSHOT)
    assert set(evidence.recipients) == {"a@example.invalid", "b@example.invalid"}


def test_repeated_mail_to_one_address_collapses_with_a_first_and_last_stamp() -> None:
    conn = _db(
        [
            ("x@example.invalid", "2026-09-12T10:00:00+00:00"),
            ("x@example.invalid", "2026-09-18T10:00:00+00:00"),
        ]
    )
    evidence = collect_sent_history(conn, mailbox=_mailbox(), snapshot=SNAPSHOT)
    record = evidence.recipients["x@example.invalid"]
    assert record["sent_message_count"] == 2
    assert record["first_sent_at"] == "2026-09-12T10:00:00Z"
    assert record["last_sent_at"] == "2026-09-18T10:00:00Z"


def test_the_delta_is_always_a_subset_of_the_canonical_gate_set() -> None:
    conn = _db([("plain@example.invalid", "2026-09-12T10:00:00+00:00")])
    evidence = collect_sent_history(conn, mailbox=_mailbox(), snapshot=SNAPSHOT)
    canonical = load_sent_recipient_norms(
        conn, gmail_user=MAILBOX, sent_folders=(FOLDER,)
    )
    assert set(evidence.recipients) <= canonical


# --- fail closed ------------------------------------------------------------


def test_a_missing_emails_table_refuses_the_run() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    with pytest.raises(SentHistoryRefused, match="emails"):
        collect_sent_history(conn, mailbox=_mailbox(), snapshot=SNAPSHOT)


def test_a_folder_mismatch_refuses_the_run() -> None:
    conn = _db(
        [("plain@example.invalid", "2026-09-12T10:00:00+00:00")],
        folder="[Gmail]/Borradores",
    )
    with pytest.raises(SentHistoryRefused, match="preflight"):
        collect_sent_history(conn, mailbox=_mailbox(), snapshot=SNAPSHOT)


def test_sent_rows_that_parse_to_no_address_refuse_the_run() -> None:
    conn = _db([("no address at all", "2026-09-12T10:00:00+00:00")])
    with pytest.raises(SentHistoryRefused, match="preflight"):
        collect_sent_history(conn, mailbox=_mailbox(), snapshot=SNAPSHOT)


@pytest.mark.parametrize("bad", [None, "", "   ", "martes pasado", "2026-13-45"])
def test_an_undatable_row_with_recipients_refuses_the_run(bad) -> None:
    conn = _db(
        [
            ("datable@example.invalid", "2026-09-12T10:00:00+00:00"),
            ("undatable@example.invalid", bad),
        ]
    )
    with pytest.raises(SentHistoryRefused, match="date_iso"):
        collect_sent_history(conn, mailbox=_mailbox(), snapshot=SNAPSHOT)


def test_an_undatable_row_with_no_recipients_does_not_refuse_the_run() -> None:
    """It cannot contribute an address, so it cannot distort the delta."""
    conn = _db(
        [
            ("datable@example.invalid", "2026-09-12T10:00:00+00:00"),
            ("", None),
        ]
    )
    evidence = collect_sent_history(conn, mailbox=_mailbox(), snapshot=SNAPSHOT)
    assert set(evidence.recipients) == {"datable@example.invalid"}


def test_an_unparsable_snapshot_instant_refuses_the_run() -> None:
    conn = _db([("plain@example.invalid", "2026-09-12T10:00:00+00:00")])
    with pytest.raises(SentHistoryRefused, match="snapshot"):
        collect_sent_history(conn, mailbox=_mailbox(), snapshot="last Tuesday")


def test_a_stale_sent_ingest_marks_the_baseline_incomplete() -> None:
    conn = _db([("plain@example.invalid", "2026-09-12T10:00:00+00:00")])
    evidence = collect_sent_history(
        conn,
        mailbox=_mailbox(),
        snapshot=SNAPSHOT,
        latest_campaign_activity_at="2026-10-01T00:00:00Z",
    )
    assert evidence.complete is False
    assert any("stale" in reason for reason in evidence.incomplete_reasons)
    assert evidence.to_report()["complete"] is False


def test_a_refusal_message_never_carries_an_address() -> None:
    conn = _db([("secret.person@example.invalid", None)])
    with pytest.raises(SentHistoryRefused) as excinfo:
        collect_sent_history(conn, mailbox=_mailbox(), snapshot=SNAPSHOT)
    assert "secret.person" not in str(excinfo.value)
    assert "@" not in str(excinfo.value)


def test_the_report_names_the_canonical_functions_and_zero_network_calls() -> None:
    conn = _db([("plain@example.invalid", "2026-09-12T10:00:00+00:00")])
    report = collect_sent_history(conn, mailbox=_mailbox(), snapshot=SNAPSHOT).to_report()
    assert report["gmail_network_calls"] == 0
    assert "already-ingested" in report["source"]
    for name in (
        "marketing_export_context.sent_history_where",
        "marketing_export_context.load_sent_recipient_norms",
        "business_mart.emails_in",
        "outbound_sent_preflight.evaluate_sent_history_preflight",
    ):
        assert name in report["canonical_implementation_reused"]


# --- the combined union -----------------------------------------------------


def test_duplicates_collapse_while_every_source_category_is_retained() -> None:
    rows, counts = combine_prior_contact(
        campaign_accepted={
            "both@example.invalid": {"address": "both@example.invalid",
                                     "first_at": "2026-09-10T00:00:00Z",
                                     "last_at": "2026-09-10T00:00:00Z"},
            "camp@example.invalid": {"address": "camp@example.invalid"},
        },
        sent_history={
            "both@example.invalid": {"address": "both@example.invalid",
                                     "first_sent_at": "2026-09-06T00:00:00Z",
                                     "last_sent_at": "2026-09-14T00:00:00Z"},
            "sent@example.invalid": {"address": "sent@example.invalid"},
        },
        outreach_state={
            "both@example.invalid": {"address": "both@example.invalid",
                                     "first_at": None, "last_at": None},
        },
    )
    by_address = {row["address"]: row for row in rows}
    assert len(rows) == 3
    assert by_address["both@example.invalid"]["source_categories"] == [
        SOURCE_CAMPAIGN_ACCEPTED,
        SOURCE_OUTREACH_STATE,
        SOURCE_SENT_HISTORY,
    ]
    assert by_address["both@example.invalid"]["multiple_sources"] is True
    assert by_address["camp@example.invalid"]["multiple_sources"] is False
    # The window spans every contributing source.
    assert by_address["both@example.invalid"]["first_at"] == "2026-09-06T00:00:00Z"
    assert by_address["both@example.invalid"]["last_at"] == "2026-09-14T00:00:00Z"


def test_the_counts_separate_raw_totals_overlaps_and_the_deduplicated_total() -> None:
    _, counts = combine_prior_contact(
        campaign_accepted={"a@example.invalid": {}, "b@example.invalid": {}},
        sent_history={"b@example.invalid": {}, "c@example.invalid": {}},
        outreach_state={"c@example.invalid": {}},
    )
    assert counts["raw_by_source"] == {
        SOURCE_CAMPAIGN_ACCEPTED: 2,
        SOURCE_SENT_HISTORY: 2,
        SOURCE_OUTREACH_STATE: 1,
    }
    assert counts["raw_total_before_dedup"] == 5
    assert counts["overlaps"][f"{SOURCE_CAMPAIGN_ACCEPTED}+{SOURCE_SENT_HISTORY}"] == 1
    assert counts["overlaps"][f"{SOURCE_SENT_HISTORY}+{SOURCE_OUTREACH_STATE}"] == 1
    assert counts["overlaps"]["all_three"] == 0
    assert counts["deduplicated_total"] == 3
    assert counts["addresses_with_multiple_sources"] == 2


def test_an_address_in_all_three_sources_is_counted_once() -> None:
    rows, counts = combine_prior_contact(
        campaign_accepted={"one@example.invalid": {}},
        sent_history={"one@example.invalid": {}},
        outreach_state={"one@example.invalid": {}},
    )
    assert len(rows) == 1
    assert counts["deduplicated_total"] == 1
    assert counts["overlaps"]["all_three"] == 1
    assert len(rows[0]["source_categories"]) == 3


def test_an_empty_union_is_stated_as_zero_not_as_an_error() -> None:
    rows, counts = combine_prior_contact(
        campaign_accepted={}, sent_history={}, outreach_state={}
    )
    assert rows == []
    assert counts["deduplicated_total"] == 0
    assert counts["raw_total_before_dedup"] == 0
