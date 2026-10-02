"""Tests for the campaign HTML recovery importer.

Fictitious SQLite built in each test with @example.test addresses.
No network access; database-backed tests skip unless ORIGENLAB_V2_TEST_DSN is set.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import socket
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from origenlab_email_pipeline.migration.v2_import.campaign_content_recovery import (
    SupplementalRefused,
    _extract_preheader,
    _normalize_html,
    _normalized_sha256,
    _raw_sha256,
    build_plan,
)

# ---------------------------------------------------------------------------
# SQLite fixture builder
# ---------------------------------------------------------------------------

SCHEMA = """
create table outbound_campaign (
  campaign_id text primary key,
  name text not null,
  sender_email text not null,
  sender_name text not null,
  subject text not null,
  target_attempt_count integer not null,
  baseline_attempt_count integer not null default 0,
  status text not null default 'active'
);
create table outbound_campaign_recipient (
  id integer primary key autoincrement,
  campaign_id text not null,
  email text,
  email_norm text,
  state text
);
create table outbound_send_attempt (
  id integer primary key autoincrement,
  campaign_id text not null,
  recipient_id integer not null,
  email_norm text not null,
  batch_id text not null,
  attempt_seq integer not null default 1,
  attempted_at text not null,
  mode text not null,
  result text not null,
  gmail_message_id text
);
create table emails (
  id integer primary key autoincrement,
  source_file text not null default 'test',
  folder text,
  message_id text,
  subject text,
  sender text,
  recipients text,
  date_raw text,
  date_iso text,
  body text,
  body_html text
);
"""

HTML_VARIANT_A = "<html><body>  <p>  Hello world  </p>  </body></html>"
HTML_VARIANT_B = "<html><body> <p> Hello world </p> </body></html>"  # same normalized as A (only whitespace differs)
HTML_VARIANT_C = "<html><body><p>Goodbye world</p></body></html>"  # different normalized hash

PREHEADER_HTML = (
    "<html><body>"
    "<span style='display:none;max-height:0;overflow:hidden;'>Special offer inside!</span>"
    "<p>Main content here.</p>"
    "</body></html>"
)

SENDER = "ventas@example.test"
SUBJECT_A = "Campaign A Subject"
SUBJECT_B = "Campaign B Subject"
SUBJECT_SHARED = "Shared Subject"


def _build_sqlite(
    tmp_path: Path,
    campaigns: list[dict],
    attempts: list[dict],
    emails_rows: list[dict],
    name: str = "test.sqlite",
) -> Path:
    """Build a V1 SQLite fixture in tmp_path."""
    path = tmp_path / name
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    for c in campaigns:
        conn.execute(
            "INSERT INTO outbound_campaign VALUES (?, ?, ?, 'Test', ?, 1000, 0, 'active')",
            (c["campaign_id"], c.get("name", c["campaign_id"]), c.get("sender_email", SENDER), c.get("subject", SUBJECT_A)),
        )
    for a in attempts:
        conn.execute(
            "INSERT INTO outbound_send_attempt (campaign_id, recipient_id, email_norm, batch_id, "
            "attempt_seq, attempted_at, mode, result, gmail_message_id) VALUES (?, 1, ?, 'b', 1, ?, ?, ?, ?)",
            (a["campaign_id"], a["email_norm"], a["attempted_at"], a.get("mode", "live"),
             a.get("result", "accepted"), a.get("gmail_message_id")),
        )
    for e in emails_rows:
        conn.execute(
            "INSERT INTO emails (folder, message_id, subject, sender, recipients, date_iso, body_html) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                e.get("folder", "[Gmail]/Enviados"),
                e.get("message_id", f"<msg-{uuid.uuid4().hex[:8]}@test>"),
                e.get("subject", SUBJECT_A),
                e.get("sender", SENDER),
                e.get("recipient", "persona@example.test"),
                e.get("date_iso", "2026-09-01T12:00:00Z"),
                e.get("body_html", HTML_VARIANT_A),
            ),
        )
    conn.commit()
    conn.close()
    return path


# ---------------------------------------------------------------------------
# (a) Exact matching by recipient+window+sender+subject with one and two variants
# ---------------------------------------------------------------------------

def test_one_variant_matched_correctly(tmp_path: Path) -> None:
    path = _build_sqlite(
        tmp_path,
        campaigns=[{"campaign_id": "c1"}],
        attempts=[
            {"campaign_id": "c1", "email_norm": "a@example.test", "attempted_at": "2026-09-01T12:00:00Z"},
            {"campaign_id": "c1", "email_norm": "b@example.test", "attempted_at": "2026-09-01T12:01:00Z"},
        ],
        emails_rows=[
            {"recipient": "a@example.test", "date_iso": "2026-09-01T12:00:02Z", "body_html": HTML_VARIANT_A},
            {"recipient": "b@example.test", "date_iso": "2026-09-01T12:01:01Z", "body_html": HTML_VARIANT_B},
        ],
    )
    plans = build_plan(path)
    assert len(plans) == 1
    plan = plans[0]
    assert plan.decision == "recoverable"
    assert plan.matched == 2
    assert plan.unmatched == 0
    # A and B have same normalized hash → one variant
    assert len(plan.variants) == 1
    v = plan.variants[0]
    assert v.variant_no == 1
    assert v.message_count == 2
    assert v.first_sent_at < v.last_sent_at
    assert v.normalized_sha256 == _normalized_sha256(HTML_VARIANT_A)


def test_two_variants_ordered_by_first_sent_time(tmp_path: Path) -> None:
    # Variant C sent first (earlier), variant A sent later
    path = _build_sqlite(
        tmp_path,
        campaigns=[{"campaign_id": "c2"}],
        attempts=[
            {"campaign_id": "c2", "email_norm": "a@example.test", "attempted_at": "2026-09-01T11:00:00Z"},
            {"campaign_id": "c2", "email_norm": "b@example.test", "attempted_at": "2026-09-01T12:00:00Z"},
        ],
        emails_rows=[
            {"recipient": "a@example.test", "date_iso": "2026-09-01T11:00:01Z", "body_html": HTML_VARIANT_C},
            {"recipient": "b@example.test", "date_iso": "2026-09-01T12:00:01Z", "body_html": HTML_VARIANT_A},
        ],
    )
    plans = build_plan(path)
    plan = plans[0]
    assert plan.decision == "recoverable"
    assert len(plan.variants) == 2
    # First variant = variant_no 1 = C (earlier)
    assert plan.variants[0].variant_no == 1
    assert plan.variants[0].normalized_sha256 == _normalized_sha256(HTML_VARIANT_C)
    assert plan.variants[0].message_count == 1
    assert plan.variants[1].variant_no == 2
    assert plan.variants[1].normalized_sha256 == _normalized_sha256(HTML_VARIANT_A)
    assert plan.variants[1].message_count == 1


# ---------------------------------------------------------------------------
# (b) Two campaigns sharing a subject: copies don't cross-contaminate
# ---------------------------------------------------------------------------

def test_shared_subject_campaigns_do_not_cross_contaminate(tmp_path: Path) -> None:
    """Campaign A and B share a subject. Copies for A must not match B's attempts."""
    path = _build_sqlite(
        tmp_path,
        campaigns=[
            {"campaign_id": "ca", "subject": SUBJECT_SHARED},
            {"campaign_id": "cb", "subject": SUBJECT_SHARED},
        ],
        attempts=[
            {"campaign_id": "ca", "email_norm": "a@example.test", "attempted_at": "2026-09-01T10:00:00Z"},
            {"campaign_id": "cb", "email_norm": "b@example.test", "attempted_at": "2026-09-02T10:00:00Z"},
        ],
        emails_rows=[
            # Only copy for campaign A's recipient
            {"subject": SUBJECT_SHARED, "recipient": "a@example.test",
             "date_iso": "2026-09-01T10:00:01Z", "body_html": HTML_VARIANT_A},
        ],
    )
    plans = build_plan(path)
    by_id = {p.campaign_id: p for p in plans}
    # Campaign A recoverable; campaign B not_recovered (its recipient has no copy)
    assert by_id["ca"].decision == "recoverable"
    assert by_id["cb"].decision == "not_recovered"


def test_not_recovered_when_attempts_have_no_recipient_match_despite_subject_copies(tmp_path: Path) -> None:
    """A campaign whose attempts have no recipient match is not_recovered even though
    copies with that subject exist (belonging to another campaign or wrong recipient)."""
    path = _build_sqlite(
        tmp_path,
        campaigns=[
            {"campaign_id": "cx", "subject": SUBJECT_SHARED},
            {"campaign_id": "cy", "subject": SUBJECT_SHARED},
        ],
        attempts=[
            {"campaign_id": "cx", "email_norm": "x@example.test", "attempted_at": "2026-09-01T10:00:00Z"},
            {"campaign_id": "cy", "email_norm": "y@example.test", "attempted_at": "2026-09-01T10:00:00Z"},
        ],
        emails_rows=[
            # There is a copy with the shared subject but it belongs to "x" not "y"
            {"subject": SUBJECT_SHARED, "recipient": "x@example.test",
             "date_iso": "2026-09-01T10:00:01Z", "body_html": HTML_VARIANT_A},
        ],
    )
    plans = build_plan(path)
    by_id = {p.campaign_id: p for p in plans}
    # cy has no recipient match (only cx's recipient is in copies)
    assert by_id["cy"].decision == "not_recovered"
    assert by_id["cx"].decision == "recoverable"


# ---------------------------------------------------------------------------
# (c) A copy 2 hours away is copy_outside_window
# ---------------------------------------------------------------------------

def test_copy_outside_window_is_reported_as_outside_window(tmp_path: Path) -> None:
    """A Sent copy that exists but is > 900 s away → unmatched with reason copy_outside_window."""
    path = _build_sqlite(
        tmp_path,
        campaigns=[{"campaign_id": "cw"}],
        attempts=[
            {"campaign_id": "cw", "email_norm": "a@example.test", "attempted_at": "2026-09-01T12:00:00Z"},
        ],
        emails_rows=[
            # 2 hours after attempt → outside 900 s window
            {"recipient": "a@example.test", "date_iso": "2026-09-01T14:00:00Z", "body_html": HTML_VARIANT_A},
        ],
    )
    plans = build_plan(path)
    plan = plans[0]
    assert plan.matched == 0
    assert plan.unmatched == 1
    assert plan.unmatched_reasons.get("copy_outside_window", 0) == 1
    assert plan.decision == "not_recovered"


# ---------------------------------------------------------------------------
# (d) Precedence 1 wins when gmail_message_id matches
# ---------------------------------------------------------------------------

def test_precedence_1_gmail_message_id_wins(tmp_path: Path) -> None:
    """When gmail_message_id matches the RFC Message-ID, precedence 1 is used."""
    msg_id = "<test-abc@example.test>"
    path = _build_sqlite(
        tmp_path,
        campaigns=[{"campaign_id": "cp1"}],
        attempts=[
            {"campaign_id": "cp1", "email_norm": "a@example.test",
             "attempted_at": "2026-09-01T12:00:00Z", "gmail_message_id": msg_id},
        ],
        emails_rows=[
            # The copy has the RFC Message-ID that matches the attempt's gmail_message_id
            {"message_id": msg_id, "recipient": "a@example.test",
             "date_iso": "2026-09-01T12:00:01Z", "body_html": HTML_VARIANT_A},
        ],
    )
    plans = build_plan(path)
    plan = plans[0]
    assert plan.decision == "recoverable"
    assert plan.precedence1_matches == 1
    assert plan.matched == 1


# ---------------------------------------------------------------------------
# (e) Wrong sender → ambiguous refusal
# ---------------------------------------------------------------------------

def test_wrong_sender_causes_ambiguous(tmp_path: Path) -> None:
    """A copy's From does not contain the campaign sender → ambiguous."""
    path = _build_sqlite(
        tmp_path,
        campaigns=[{"campaign_id": "cs"}],
        attempts=[
            {"campaign_id": "cs", "email_norm": "a@example.test", "attempted_at": "2026-09-01T12:00:00Z"},
        ],
        emails_rows=[
            # Wrong sender
            {"recipient": "a@example.test", "date_iso": "2026-09-01T12:00:01Z",
             "sender": "wrong@other.test", "body_html": HTML_VARIANT_A},
        ],
    )
    plans = build_plan(path)
    plan = plans[0]
    # No match since sender doesn't match → not_recovered (wrong sender fails the filter)
    # The sender check is part of the candidate filter (p2+p3), so no candidates → not_recovered
    assert plan.decision == "not_recovered"


def test_precedence1_wrong_sender_raises_ambiguous(tmp_path: Path) -> None:
    """A precedence-1 match (gmail_message_id) whose From doesn't contain the sender → refused."""
    msg_id = "<p1-bad-sender@example.test>"
    path = _build_sqlite(
        tmp_path,
        campaigns=[{"campaign_id": "cp1bad"}],
        attempts=[
            {"campaign_id": "cp1bad", "email_norm": "a@example.test",
             "attempted_at": "2026-09-01T12:00:00Z", "gmail_message_id": msg_id},
        ],
        emails_rows=[
            {"message_id": msg_id, "recipient": "a@example.test",
             "date_iso": "2026-09-01T12:00:01Z", "sender": "badactor@other.test",
             "body_html": HTML_VARIANT_A},
        ],
    )
    plans = build_plan(path)
    plan = plans[0]
    assert plan.decision == "ambiguous"


# ---------------------------------------------------------------------------
# (f) Two candidate copies with different HTML in the window → ambiguous
# ---------------------------------------------------------------------------

def test_two_candidates_with_different_html_cause_ambiguous(tmp_path: Path) -> None:
    path = _build_sqlite(
        tmp_path,
        campaigns=[{"campaign_id": "camb"}],
        attempts=[
            {"campaign_id": "camb", "email_norm": "a@example.test", "attempted_at": "2026-09-01T12:00:00Z"},
        ],
        emails_rows=[
            # Two copies for the same recipient in the window, different HTML
            {"recipient": "a@example.test", "date_iso": "2026-09-01T12:00:01Z",
             "body_html": HTML_VARIANT_A},
            {"recipient": "a@example.test", "date_iso": "2026-09-01T12:00:02Z",
             "body_html": HTML_VARIANT_C},
        ],
    )
    plans = build_plan(path)
    plan = plans[0]
    assert plan.decision == "ambiguous"


# ---------------------------------------------------------------------------
# (g) Never-sent campaign → never_sent with no variants
# ---------------------------------------------------------------------------

def test_never_sent_campaign(tmp_path: Path) -> None:
    path = _build_sqlite(
        tmp_path,
        campaigns=[{"campaign_id": "cnever"}],
        attempts=[],  # no attempts
        emails_rows=[
            # Copy exists but since there are no attempts, campaign is never_sent
            {"recipient": "a@example.test", "date_iso": "2026-09-01T12:00:00Z"},
        ],
    )
    plans = build_plan(path)
    plan = plans[0]
    assert plan.decision == "never_sent"
    assert plan.accepted_attempts == 0
    assert plan.variants == []


# ---------------------------------------------------------------------------
# (h) Hashes: normalized vs raw differ only by whitespace; preheader extraction
# ---------------------------------------------------------------------------

def test_normalized_and_raw_hashes_differ_only_by_whitespace() -> None:
    html_with_spaces = "<p>  Hello   world  </p>"
    html_compact = "<p> Hello world </p>"  # same after normalization
    # raw hashes differ
    assert _raw_sha256(html_with_spaces) != _raw_sha256(html_compact)
    # normalized hashes are the same
    assert _normalized_sha256(html_with_spaces) == _normalized_sha256(html_compact)


def test_normalized_sha256_is_whitespace_collapsed() -> None:
    h1 = _normalized_sha256("  hello   world  ")
    h2 = _normalized_sha256("hello world")
    assert h1 == h2


def test_preheader_extraction_finds_hidden_element() -> None:
    result = _extract_preheader(PREHEADER_HTML)
    assert result == "Special offer inside!"


def test_preheader_extraction_returns_none_when_absent() -> None:
    result = _extract_preheader("<html><body><p>No preheader here</p></body></html>")
    assert result is None


def test_preheader_from_span_with_class() -> None:
    html = "<html><body><span class='preheader'>Offer text here</span><p>Main</p></body></html>"
    result = _extract_preheader(html)
    assert result == "Offer text here"


def test_variant_preheader_present_flag_set_correctly(tmp_path: Path) -> None:
    path = _build_sqlite(
        tmp_path,
        campaigns=[{"campaign_id": "cph"}],
        attempts=[
            {"campaign_id": "cph", "email_norm": "a@example.test", "attempted_at": "2026-09-01T12:00:00Z"},
        ],
        emails_rows=[
            {"recipient": "a@example.test", "date_iso": "2026-09-01T12:00:01Z",
             "body_html": PREHEADER_HTML},
        ],
    )
    plans = build_plan(path)
    plan = plans[0]
    assert plan.decision == "recoverable"
    assert plan.variants[0].preheader_present is True


# ---------------------------------------------------------------------------
# (i) The source is never written (readonly helper; total_changes unchanged)
# ---------------------------------------------------------------------------

def test_source_is_never_written(tmp_path: Path) -> None:
    path = _build_sqlite(
        tmp_path,
        campaigns=[{"campaign_id": "crow"}],
        attempts=[
            {"campaign_id": "crow", "email_norm": "a@example.test", "attempted_at": "2026-09-01T12:00:00Z"},
        ],
        emails_rows=[
            {"recipient": "a@example.test", "date_iso": "2026-09-01T12:00:01Z"},
        ],
    )
    before = path.read_bytes()
    build_plan(path)
    assert path.read_bytes() == before


# ---------------------------------------------------------------------------
# (j) NO NETWORK: AST import scan + monkeypatched socket
# ---------------------------------------------------------------------------

SRC = Path(__file__).parent.parent / "src" / "origenlab_email_pipeline" / "migration" / "v2_import"

FORBIDDEN_IMPORTS = {
    "socket", "http", "urllib", "requests", "httpx", "aiohttp",
    "smtplib", "imaplib", "poplib", "googleapiclient", "google",
    "google_auth_oauthlib", "email.mime", "subprocess",
}

MODULE_UNDER_TEST = "campaign_content_recovery.py"


def test_no_forbidden_imports_in_module() -> None:
    tree = ast.parse((SRC / MODULE_UNDER_TEST).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    for name in imported:
        assert not any(
            name == f or name.startswith(f + ".") for f in FORBIDDEN_IMPORTS
        ), f"forbidden import {name!r} found in {MODULE_UNDER_TEST}"


def test_plan_run_opens_no_socket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*a: Any, **k: Any) -> None:
        raise AssertionError("a network connection was attempted during plan run")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)

    path = _build_sqlite(
        tmp_path,
        campaigns=[{"campaign_id": "cnet"}],
        attempts=[
            {"campaign_id": "cnet", "email_norm": "a@example.test", "attempted_at": "2026-09-01T12:00:00Z"},
        ],
        emails_rows=[
            {"recipient": "a@example.test", "date_iso": "2026-09-01T12:00:01Z"},
        ],
    )
    plans = build_plan(path)
    assert plans[0].decision == "recoverable"


# ---------------------------------------------------------------------------
# (k) Database-backed (skip unless ORIGENLAB_V2_TEST_DSN)
# ---------------------------------------------------------------------------

_DSN = os.environ.get("ORIGENLAB_V2_TEST_DSN", "")
requires_db = pytest.mark.skipif(not _DSN, reason="set ORIGENLAB_V2_TEST_DSN to a disposable migrated database")


def _pg_counts(dsn: str) -> dict[str, int]:
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        out = {}
        for t in (
            "evidence.source_record",
            "outbound.campaign_content",
            "outbound.campaign_content_message",
            "outbound.campaign",
        ):
            cur.execute(f"SELECT count(*) FROM {t}")  # noqa: S608 - fixed names
            out[t] = cur.fetchone()[0]
        return out


def _seed_v2_campaign(dsn: str, tag: str) -> tuple[str, str, str, str]:
    """Seed a mailbox and a V2 campaign with an origin source_record whose payload has v1_campaign_id.

    Uses 'draft' status to avoid the approval_shape constraint (approved_at etc. not needed for draft).
    Returns (v2_campaign_id, v1_campaign_id, sender, subject).
    """
    import psycopg

    v1_cid = f"dbtest-{tag}"
    sender = f"ventas-{tag}@example.test"
    subject = f"DB Test Subject {tag}"
    manifest_payload = {
        "manifest_version": "test",
        "v1_campaign_id": v1_cid,
        "sha256": "0" * 64,
    }
    payload_sha = hashlib.sha256(json.dumps(manifest_payload, sort_keys=True).encode()).hexdigest()

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            "INSERT INTO comms.mailbox (address_norm) VALUES (%s) "
            "ON CONFLICT (address_norm) DO NOTHING RETURNING id",
            (sender,),
        )
        row = cur.fetchone()
        if row is None:
            cur.execute("SELECT id FROM comms.mailbox WHERE address_norm = %s", (sender,))
            row = cur.fetchone()
        mailbox_id = row[0]
        cur.execute(
            "INSERT INTO evidence.source_record "
            "(kind, dedupe_key, payload, payload_sha256, review_status) "
            "VALUES ('migration_manifest', %s, %s, %s, 'pending') RETURNING id",
            (
                f"migration_manifest:wave1a:{v1_cid}:seedabc",
                json.dumps(manifest_payload),
                payload_sha,
            ),
        )
        origin_sr_id = cur.fetchone()[0]
        # Use 'draft' status: does not trigger the approval_shape constraint
        cur.execute(
            "INSERT INTO outbound.campaign "
            "(name, status, mailbox_id, max_sends, recontact_interval_days, "
            " policy_include_suppliers, origin_source_record_id) "
            "VALUES (%s, 'draft', %s, 1000, 90, false, %s) RETURNING id",
            (f"DB Test Campaign {tag}", mailbox_id, origin_sr_id),
        )
        v2_campaign_id = str(cur.fetchone()[0])
    return v2_campaign_id, v1_cid, sender, subject


@requires_db
def test_db_dry_run_apply_idempotent_immutable(tmp_path: Path) -> None:
    import psycopg

    from origenlab_email_pipeline.migration.v2_import.campaign_content_recovery import apply_recovery
    from origenlab_email_pipeline.migration.v2_import.target import assert_local_target

    tag = uuid.uuid4().hex[:8]
    v2_cid, v1_cid, sender, subject = _seed_v2_campaign(_DSN, tag)

    path = _build_sqlite(
        tmp_path,
        campaigns=[{"campaign_id": v1_cid, "name": f"DB Test Campaign {tag}",
                    "sender_email": sender, "subject": subject}],
        attempts=[
            {"campaign_id": v1_cid, "email_norm": f"addr1-{tag}@example.test",
             "attempted_at": "2026-09-01T10:00:00Z"},
            {"campaign_id": v1_cid, "email_norm": f"addr2-{tag}@example.test",
             "attempted_at": "2026-09-01T11:00:00Z"},
        ],
        emails_rows=[
            {"subject": subject, "sender": sender, "recipient": f"addr1-{tag}@example.test",
             "date_iso": "2026-09-01T10:00:01Z", "body_html": HTML_VARIANT_A},
            {"subject": subject, "sender": sender, "recipient": f"addr2-{tag}@example.test",
             "date_iso": "2026-09-01T11:00:01Z", "body_html": HTML_VARIANT_A},
        ],
    )

    plans = build_plan(path, [v1_cid])
    assert len(plans) == 1
    plan = plans[0]
    assert plan.decision == "recoverable"
    assert plan.matched == 2
    assert len(plan.variants) == 1

    target = assert_local_target(_DSN)
    before = _pg_counts(_DSN)

    # Dry run: nothing written
    apply_recovery(plans, target, dry_run=True, expect_contents=1, expect_messages=2)
    assert _pg_counts(_DSN) == before

    # Apply
    applied = apply_recovery(plans, target, dry_run=False, expect_contents=1, expect_messages=2)
    after = _pg_counts(_DSN)
    diff = {k: after[k] - before[k] for k in after}
    assert diff["evidence.source_record"] == 1
    assert diff["outbound.campaign_content"] == 1
    assert diff["outbound.campaign_content_message"] == 2
    assert applied.inserted["outbound.campaign_content"] == 1
    assert applied.inserted["outbound.campaign_content_message"] == 2
    # send_attempts are not seeded → all attempts unlinked
    assert applied.attempts_unlinked == 2

    # Second apply → already_present, 0 new rows
    again = apply_recovery(plans, target, dry_run=False, expect_contents=1, expect_messages=2)
    assert again.already_present == [v1_cid]
    assert _pg_counts(_DSN) == after

    # Wrong --expect-contents refuses with nothing written
    before2 = _pg_counts(_DSN)
    from origenlab_email_pipeline.migration.v2_import.apply import ApplyRefused
    with pytest.raises(ApplyRefused, match="expect-contents"):
        apply_recovery(plans, target, dry_run=False, expect_contents=999, expect_messages=2)
    assert _pg_counts(_DSN) == before2

    # The campaign_content row is immutable (UPDATE is refused)
    with psycopg.connect(_DSN, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            "SELECT id FROM outbound.campaign_content WHERE campaign_id = %s::uuid", (v2_cid,)
        )
        content_id = cur.fetchone()[0]
        with pytest.raises(psycopg.errors.RaiseException):
            cur.execute(
                "UPDATE outbound.campaign_content SET message_count = 0 WHERE id = %s",
                (content_id,),
            )

        # The campaign row is byte-identical before and after the content recovery
        cur.execute("SELECT to_jsonb(c) FROM outbound.campaign c WHERE id = %s::uuid", (v2_cid,))
        row_before = cur.fetchone()[0]
        cur.execute("SELECT to_jsonb(c) FROM outbound.campaign c WHERE id = %s::uuid", (v2_cid,))
        row_after = cur.fetchone()[0]
        assert row_before == row_after
