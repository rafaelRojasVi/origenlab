"""Tests for outbound_campaign_store: campaign CRUD, selection, attempt ledger, progress."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from origenlab_email_pipeline.candidate_export_gate import GateContext
from origenlab_email_pipeline.outbound_campaign_schema import ensure_outbound_campaign_tables
from origenlab_email_pipeline.outbound_campaign_store import (
    CampaignAlreadyExistsError,
    CampaignNotFoundError,
    begin_live_attempt,
    campaign_progress,
    create_campaign,
    finish_live_attempt,
    get_campaign,
    has_accepted_attempt,
    latest_attempt_status,
    list_candidates,
    list_reserved_batch,
    record_attempt,
    reserve_next_batch,
    upsert_recipient_candidate,
)


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(tmp_path / "t.sqlite"))
    ensure_outbound_campaign_tables(c)
    yield c
    c.close()


def _make_hielscher(conn: sqlite3.Connection) -> None:
    create_campaign(
        conn, campaign_id="hielscher-sonicators-2026", name="Hielscher Sonicadores Laboratorio",
        sender_email="contacto@origenlab.cl", sender_name="Tatiana Vivanco | OrigenLab",
        subject="Sonicadores Hielscher para laboratorio | OrigenLab",
        target_attempt_count=2000, baseline_attempt_count=874,
    )


def _permissive_ctx(**overrides) -> GateContext:
    base = dict(
        sent_recipient_norms=frozenset(),
        suppressed_norms=frozenset(),
        outreach_state_by_email={},
        supplier_domains=frozenset(),
        blocked_domains=frozenset(),
    )
    base.update(overrides)
    return GateContext(**base)


# --- Campaign + candidate CRUD (Task 3) ---------------------------------------------------


def test_create_and_get_campaign(conn: sqlite3.Connection) -> None:
    _make_hielscher(conn)
    row = get_campaign(conn, "hielscher-sonicators-2026")
    assert row is not None
    assert row.target_attempt_count == 2000
    assert row.baseline_attempt_count == 874
    assert row.status == "active"


def test_get_unknown_campaign_returns_none(conn: sqlite3.Connection) -> None:
    assert get_campaign(conn, "nope") is None


def test_duplicate_campaign_id_raises(conn: sqlite3.Connection) -> None:
    _make_hielscher(conn)
    with pytest.raises(CampaignAlreadyExistsError):
        _make_hielscher(conn)


def test_upsert_candidate_creates_row(conn: sqlite3.Connection) -> None:
    _make_hielscher(conn)
    rid = upsert_recipient_candidate(
        conn, campaign_id="hielscher-sonicators-2026", email="lab@example.cl",
        source_kind="manual", institution_name="Example Lab",
    )
    assert isinstance(rid, int)
    rows = list_candidates(conn, "hielscher-sonicators-2026")
    assert len(rows) == 1
    assert rows[0]["email_norm"] == "lab@example.cl"
    assert rows[0]["state"] == "candidate"


def test_upsert_candidate_is_idempotent_by_email(conn: sqlite3.Connection) -> None:
    _make_hielscher(conn)
    rid1 = upsert_recipient_candidate(
        conn, campaign_id="hielscher-sonicators-2026", email="Lab@Example.CL", source_kind="manual",
    )
    rid2 = upsert_recipient_candidate(
        conn, campaign_id="hielscher-sonicators-2026", email="lab@example.cl", source_kind="manual",
        institution_name="Example Lab Updated",
    )
    assert rid1 == rid2
    rows = list_candidates(conn, "hielscher-sonicators-2026")
    assert len(rows) == 1
    assert rows[0]["institution_name"] == "Example Lab Updated"


def test_same_email_allowed_in_different_campaigns(conn: sqlite3.Connection) -> None:
    _make_hielscher(conn)
    create_campaign(
        conn, campaign_id="other-2026", name="Other", sender_email="s@x.cl", sender_name="S",
        subject="subj", target_attempt_count=100,
    )
    upsert_recipient_candidate(conn, campaign_id="hielscher-sonicators-2026", email="a@b.cl", source_kind="manual")
    upsert_recipient_candidate(conn, campaign_id="other-2026", email="a@b.cl", source_kind="manual")
    assert len(list_candidates(conn, "hielscher-sonicators-2026")) == 1
    assert len(list_candidates(conn, "other-2026")) == 1


# --- Selection / reservation (Task 5) -------------------------------------------------------


def test_reserve_next_batch_moves_eligible_candidates_to_reserved(conn: sqlite3.Connection) -> None:
    _make_hielscher(conn)
    for em in ("a@x.cl", "b@x.cl", "c@x.cl"):
        upsert_recipient_candidate(conn, campaign_id="hielscher-sonicators-2026", email=em, source_kind="manual")
    result = reserve_next_batch(
        conn, "hielscher-sonicators-2026", gate_ctx=_permissive_ctx(), manual_status_by_email={}, n=10,
    )
    assert len(result.reserved) == 3
    assert result.blocked == []
    batch = list_reserved_batch(conn, "hielscher-sonicators-2026")
    assert {r["email_norm"] for r in batch} == {"a@x.cl", "b@x.cl", "c@x.cl"}
    assert len(list_candidates(conn, "hielscher-sonicators-2026")) == 0


def test_reserve_next_batch_respects_n_cap(conn: sqlite3.Connection) -> None:
    _make_hielscher(conn)
    for em in ("a@x.cl", "b@x.cl", "c@x.cl"):
        upsert_recipient_candidate(conn, campaign_id="hielscher-sonicators-2026", email=em, source_kind="manual")
    result = reserve_next_batch(
        conn, "hielscher-sonicators-2026", gate_ctx=_permissive_ctx(), manual_status_by_email={}, n=2,
    )
    assert len(result.reserved) == 2
    assert len(list_candidates(conn, "hielscher-sonicators-2026")) == 1


def test_reserve_next_batch_blocks_ineligible_with_reason(conn: sqlite3.Connection) -> None:
    _make_hielscher(conn)
    upsert_recipient_candidate(conn, campaign_id="hielscher-sonicators-2026", email="carolinalobo@pharmaisa.cl", source_kind="manual")
    result = reserve_next_batch(
        conn, "hielscher-sonicators-2026", gate_ctx=_permissive_ctx(), n=10,
        manual_status_by_email={"carolinalobo@pharmaisa.cl": "inactive"},
    )
    assert result.reserved == []
    assert len(result.blocked) == 1
    row = conn.execute(
        "SELECT state, block_reason FROM outbound_campaign_recipient WHERE campaign_id=? AND email_norm=?",
        ("hielscher-sonicators-2026", "carolinalobo@pharmaisa.cl"),
    ).fetchone()
    assert row == ("blocked", "manual_inactive")


def test_carolina_lobo_never_reserved_across_multiple_select_calls(conn: sqlite3.Connection) -> None:
    _make_hielscher(conn)
    upsert_recipient_candidate(conn, campaign_id="hielscher-sonicators-2026", email="carolinalobo@pharmaisa.cl", source_kind="manual")
    manual = {"carolinalobo@pharmaisa.cl": "inactive"}
    for _ in range(3):
        reserve_next_batch(conn, "hielscher-sonicators-2026", gate_ctx=_permissive_ctx(), manual_status_by_email=manual, n=10)
    batch = list_reserved_batch(conn, "hielscher-sonicators-2026")
    assert batch == []


# --- Attempt ledger + progress (Task 6) -----------------------------------------------------


def test_record_attempt_appends_and_assigns_attempt_seq(conn: sqlite3.Connection) -> None:
    _make_hielscher(conn)
    rid = upsert_recipient_candidate(conn, campaign_id="hielscher-sonicators-2026", email="a@x.cl", source_kind="manual")
    id1 = record_attempt(
        conn, campaign_id="hielscher-sonicators-2026", recipient_id=rid, email_norm="a@x.cl",
        batch_id="b1", mode="dry_run", result="accepted",
    )
    id2 = record_attempt(
        conn, campaign_id="hielscher-sonicators-2026", recipient_id=rid, email_norm="a@x.cl",
        batch_id="b2", mode="live", result="failed", error_detail="boom",
    )
    assert id2 != id1
    seqs = [r[0] for r in conn.execute(
        "SELECT attempt_seq FROM outbound_send_attempt WHERE campaign_id=? AND recipient_id=? ORDER BY attempt_seq",
        ("hielscher-sonicators-2026", rid),
    ).fetchall()]
    assert seqs == [1, 2]


def test_record_accepted_attempt_marks_recipient_sent(conn: sqlite3.Connection) -> None:
    _make_hielscher(conn)
    rid = upsert_recipient_candidate(conn, campaign_id="hielscher-sonicators-2026", email="a@x.cl", source_kind="manual")
    record_attempt(
        conn, campaign_id="hielscher-sonicators-2026", recipient_id=rid, email_norm="a@x.cl",
        batch_id="b1", mode="live", result="accepted", gmail_message_id="MSG123",
    )
    row = conn.execute(
        "SELECT state, last_gmail_message_id FROM outbound_campaign_recipient WHERE id=?", (rid,)
    ).fetchone()
    assert row == ("sent", "MSG123")


def test_has_accepted_attempt_detects_prior_send(conn: sqlite3.Connection) -> None:
    _make_hielscher(conn)
    rid = upsert_recipient_candidate(conn, campaign_id="hielscher-sonicators-2026", email="a@x.cl", source_kind="manual")
    assert has_accepted_attempt(conn, "hielscher-sonicators-2026", rid) is None
    record_attempt(
        conn, campaign_id="hielscher-sonicators-2026", recipient_id=rid, email_norm="a@x.cl",
        batch_id="b1", mode="live", result="accepted", gmail_message_id="MSG1",
    )
    existing = has_accepted_attempt(conn, "hielscher-sonicators-2026", rid)
    assert existing is not None
    assert existing[1] == "MSG1"


def test_partial_send_persists_each_attempt_independently(conn: sqlite3.Connection) -> None:
    _make_hielscher(conn)
    r1 = upsert_recipient_candidate(conn, campaign_id="hielscher-sonicators-2026", email="a@x.cl", source_kind="manual")
    r2 = upsert_recipient_candidate(conn, campaign_id="hielscher-sonicators-2026", email="b@x.cl", source_kind="manual")
    record_attempt(conn, campaign_id="hielscher-sonicators-2026", recipient_id=r1, email_norm="a@x.cl", batch_id="b1", mode="live", result="accepted", gmail_message_id="M1")
    record_attempt(conn, campaign_id="hielscher-sonicators-2026", recipient_id=r2, email_norm="b@x.cl", batch_id="b1", mode="live", result="failed", error_detail="quota")
    conn.commit()
    assert has_accepted_attempt(conn, "hielscher-sonicators-2026", r1) is not None
    assert has_accepted_attempt(conn, "hielscher-sonicators-2026", r2) is None
    counts = dict(conn.execute(
        "SELECT result, COUNT(*) FROM outbound_send_attempt WHERE campaign_id=? GROUP BY result",
        ("hielscher-sonicators-2026",),
    ).fetchall())
    assert counts == {"accepted": 1, "failed": 1}


def test_campaign_progress_baseline_and_target_math(conn: sqlite3.Connection) -> None:
    _make_hielscher(conn)
    for i in range(5):
        rid = upsert_recipient_candidate(conn, campaign_id="hielscher-sonicators-2026", email=f"c{i}@x.cl", source_kind="manual")
        if i < 3:
            record_attempt(
                conn, campaign_id="hielscher-sonicators-2026", recipient_id=rid, email_norm=f"c{i}@x.cl",
                batch_id="b1", mode="live", result="accepted", gmail_message_id=f"M{i}",
            )
    progress = campaign_progress(conn, "hielscher-sonicators-2026")
    assert progress.target == 2000
    assert progress.baseline == 874
    assert progress.ledger_attempts == 3
    assert progress.total_accepted == 877
    assert progress.remaining == 1123
    assert progress.sent == 3
    assert progress.candidates == 2


def test_campaign_progress_unknown_campaign_raises(conn: sqlite3.Connection) -> None:
    with pytest.raises(CampaignNotFoundError):
        campaign_progress(conn, "nope")


# --- Two-phase live attempt state machine (hardening pass) ---------------------------------


def test_begin_live_attempt_persists_in_flight_before_any_gmail_call(conn: sqlite3.Connection) -> None:
    _make_hielscher(conn)
    rid = upsert_recipient_candidate(conn, campaign_id="hielscher-sonicators-2026", email="a@x.cl", source_kind="manual")
    conn.execute("UPDATE outbound_campaign_recipient SET state='reserved' WHERE id=?", (rid,))
    attempt_id = begin_live_attempt(conn, campaign_id="hielscher-sonicators-2026", recipient_id=rid, email_norm="a@x.cl", batch_id="b1")
    conn.commit()
    latest = latest_attempt_status(conn, "hielscher-sonicators-2026", rid)
    assert latest == ("in_flight", attempt_id)
    # Recipient is not yet marked sent -- only finish_live_attempt does that.
    row = conn.execute("SELECT state FROM outbound_campaign_recipient WHERE id=?", (rid,)).fetchone()
    assert row == ("reserved",)


def test_finish_live_attempt_accepted_marks_recipient_sent(conn: sqlite3.Connection) -> None:
    _make_hielscher(conn)
    rid = upsert_recipient_candidate(conn, campaign_id="hielscher-sonicators-2026", email="a@x.cl", source_kind="manual")
    attempt_id = begin_live_attempt(conn, campaign_id="hielscher-sonicators-2026", recipient_id=rid, email_norm="a@x.cl", batch_id="b1")
    finish_live_attempt(conn, attempt_id=attempt_id, recipient_id=rid, result="accepted", gmail_message_id="MSG-1")
    latest = latest_attempt_status(conn, "hielscher-sonicators-2026", rid)
    assert latest == ("accepted", attempt_id)
    row = conn.execute(
        "SELECT state, last_gmail_message_id FROM outbound_campaign_recipient WHERE id=?", (rid,)
    ).fetchone()
    assert row == ("sent", "MSG-1")
    resolved_at = conn.execute("SELECT resolved_at FROM outbound_send_attempt WHERE id=?", (attempt_id,)).fetchone()[0]
    assert resolved_at is not None


def test_finish_live_attempt_failed_leaves_recipient_reserved_for_retry(conn: sqlite3.Connection) -> None:
    _make_hielscher(conn)
    rid = upsert_recipient_candidate(conn, campaign_id="hielscher-sonicators-2026", email="a@x.cl", source_kind="manual")
    conn.execute("UPDATE outbound_campaign_recipient SET state='reserved' WHERE id=?", (rid,))
    attempt_id = begin_live_attempt(conn, campaign_id="hielscher-sonicators-2026", recipient_id=rid, email_norm="a@x.cl", batch_id="b1")
    finish_live_attempt(conn, attempt_id=attempt_id, recipient_id=rid, result="failed", error_detail="quota exceeded")
    latest = latest_attempt_status(conn, "hielscher-sonicators-2026", rid)
    assert latest == ("failed", attempt_id)
    row = conn.execute("SELECT state FROM outbound_campaign_recipient WHERE id=?", (rid,)).fetchone()
    assert row == ("reserved",)


def test_crash_window_leaves_attempt_in_flight_when_finish_never_runs(conn: sqlite3.Connection) -> None:
    """Simulates: Gmail may have accepted the message, but the process died before
    finish_live_attempt ran. The row must be left in_flight, not silently resolved."""
    _make_hielscher(conn)
    rid = upsert_recipient_candidate(conn, campaign_id="hielscher-sonicators-2026", email="a@x.cl", source_kind="manual")
    begin_live_attempt(conn, campaign_id="hielscher-sonicators-2026", recipient_id=rid, email_norm="a@x.cl", batch_id="b1")
    conn.commit()
    # No finish_live_attempt call -- represents the crash.
    assert latest_attempt_status(conn, "hielscher-sonicators-2026", rid)[0] == "in_flight"
    assert has_accepted_attempt(conn, "hielscher-sonicators-2026", rid) is None
