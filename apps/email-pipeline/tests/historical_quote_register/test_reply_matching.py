from __future__ import annotations

import sys
from pathlib import Path

_TDIR = Path(__file__).resolve().parent.parent
if str(_TDIR) not in sys.path:
    sys.path.insert(0, str(_TDIR))

from origenlab_email_pipeline.db import insert_email
from origenlab_email_pipeline.historical_quote_register.candidates import SENT_FOLDERS
from origenlab_email_pipeline.historical_quote_register.db_readonly import open_readonly
from origenlab_email_pipeline.historical_quote_register.reply_matching import (
    ReplyCandidateRow,
    classify_response,
    find_candidate_replies,
)
from historical_quote_register.sqlite_fixture import build_fixture_db, open_writable_fixture


def test_find_candidate_replies_bounded_to_customer_and_window(tmp_path):
    db_path = build_fixture_db(tmp_path)
    conn = open_writable_fixture(db_path)
    insert_email(
        conn, source_file="x", folder="INBOX", message_id="<r1>",
        subject="RE: Cotización COT-2026-014", sender="compras@cliente.cl",
        recipients="contacto@origenlab.cl", date_raw="", date_iso="2026-05-12T00:00:00", body="",
    )
    insert_email(  # different customer — must not appear
        conn, source_file="x", folder="INBOX", message_id="<r2>",
        subject="RE: Cotización", sender="otro@otro-cliente.cl",
        recipients="contacto@origenlab.cl", date_raw="", date_iso="2026-05-12T00:00:00", body="",
    )
    insert_email(  # before send — must not appear
        conn, source_file="x", folder="INBOX", message_id="<r3>",
        subject="Consulta previa", sender="compras@cliente.cl",
        recipients="contacto@origenlab.cl", date_raw="", date_iso="2026-05-01T00:00:00", body="",
    )
    conn.commit()
    conn.close()

    candidates = find_candidate_replies(
        open_readonly(db_path), customer_contact_email="compras@cliente.cl",
        sent_at_iso="2026-05-10T00:00:00", window_end_iso=None,
    )
    assert [c.email_id for c in candidates] == [1]  # only the in-window, same-customer, post-send row


def test_find_candidate_replies_respects_window_end(tmp_path):
    db_path = build_fixture_db(tmp_path)
    conn = open_writable_fixture(db_path)
    insert_email(
        conn, source_file="x", folder="INBOX", message_id="<r1>",
        subject="RE: Cotización COT-2026-014", sender="compras@cliente.cl",
        recipients="contacto@origenlab.cl", date_raw="", date_iso="2026-05-12T00:00:00", body="",
    )
    insert_email(  # after window_end_iso — must not appear
        conn, source_file="x", folder="INBOX", message_id="<r2>",
        subject="RE: Cotización COT-2026-014 otra vez", sender="compras@cliente.cl",
        recipients="contacto@origenlab.cl", date_raw="", date_iso="2026-06-01T00:00:00", body="",
    )
    conn.commit()
    conn.close()

    candidates = find_candidate_replies(
        open_readonly(db_path), customer_contact_email="compras@cliente.cl",
        sent_at_iso="2026-05-10T00:00:00", window_end_iso="2026-05-20T00:00:00",
    )
    assert [c.email_id for c in candidates] == [1]


def test_find_candidate_replies_excludes_sent_folders(tmp_path):
    """A row in a known Sent folder must never surface as a reply candidate,
    even if sender+date otherwise match. The legacy mbox archive stores its
    Sent folder as a full absolute path (see
    candidates.LEGACY_MBOX_SENT_FOLDER_SUFFIX), so exclusion must go through
    `is_sent_folder` (which handles both the Gmail exact-literal and the
    legacy suffix match) rather than an exact-literal SENT_FOLDERS check
    alone — a plain `folder NOT IN (SENT_FOLDERS)` would silently let
    legacy-archive Sent rows leak into the reply candidate set."""
    db_path = build_fixture_db(tmp_path)
    conn = open_writable_fixture(db_path)
    insert_email(  # Gmail Sent folder (exact-literal match) — must not appear
        conn, source_file="x", folder=SENT_FOLDERS[0], message_id="<s1>",
        subject="RE: Cotización COT-2026-014", sender="compras@cliente.cl",
        recipients="contacto@origenlab.cl", date_raw="", date_iso="2026-05-12T00:00:00", body="",
    )
    insert_email(  # legacy mbox Sent folder (suffix match on a full path) — must not appear
        conn, source_file="x",
        folder="/some/data_root/mbox/contacto@labdelivery.cl/contacto@labdelivery.cl/Elementos enviados",
        message_id="<s2>", subject="RE: Cotización COT-2026-014", sender="compras@cliente.cl",
        recipients="contacto@origenlab.cl", date_raw="", date_iso="2026-05-13T00:00:00", body="",
    )
    insert_email(  # real Inbox reply — must appear
        conn, source_file="x", folder="INBOX", message_id="<r1>",
        subject="RE: Cotización COT-2026-014", sender="compras@cliente.cl",
        recipients="contacto@origenlab.cl", date_raw="", date_iso="2026-05-14T00:00:00", body="",
    )
    conn.commit()
    conn.close()

    candidates = find_candidate_replies(
        open_readonly(db_path), customer_contact_email="compras@cliente.cl",
        sent_at_iso="2026-05-10T00:00:00", window_end_iso=None,
    )
    assert [c.email_id for c in candidates] == [3]


def test_classify_response_exact_reply():
    candidates = [
        ReplyCandidateRow(
            email_id=1, sender="compras@cliente.cl", subject="RE: Cotización COT-2026-014",
            date_iso="2026-05-12T00:00:00", body_snippet="Gracias, la revisamos.",
        )
    ]
    result = classify_response(candidates, quote_number="COT-2026-014", normalized_quote_subject_core="cotizacion")
    assert result.response_state == "replied"
    assert result.first_reply_email_id == 1
    assert result.review_required is False


def test_classify_response_no_reply():
    result = classify_response([], quote_number="COT-2026-014", normalized_quote_subject_core="cotizacion")
    assert result.response_state == "no_reply"


def test_classify_response_auto_reply_not_counted_as_replied():
    candidates = [
        ReplyCandidateRow(
            email_id=1, sender="compras@cliente.cl", subject="Automatic reply: Out of office",
            date_iso="2026-05-11T00:00:00", body_snippet="I am out of office until...",
        )
    ]
    result = classify_response(candidates, quote_number="COT-2026-014", normalized_quote_subject_core="cotizacion")
    assert result.response_state == "auto_reply"


def test_classify_response_auto_reply_with_subject_overlap_still_not_replied():
    """An auto-reply that happens to quote the original subject back (a
    common autoresponder pattern) must still never be classified as a real
    reply — the auto-reply check must run before, and win over, subject/
    quote-number overlap matching."""
    candidates = [
        ReplyCandidateRow(
            email_id=1, sender="compras@cliente.cl",
            subject="Automatic reply: RE: Cotización COT-2026-014",
            date_iso="2026-05-11T00:00:00",
            body_snippet="I am out of office until next week. Cotización COT-2026-014.",
        )
    ]
    result = classify_response(candidates, quote_number="COT-2026-014", normalized_quote_subject_core="cotizacion")
    assert result.response_state == "auto_reply"
    assert result.response_state != "replied"


def test_classify_response_bounce_with_subject_overlap_still_not_replied():
    """A bounce (NDR) that quotes the original subject in its body must
    still never be classified as a real reply — the noise/bounce check
    must run before, and win over, subject/quote-number overlap matching."""
    candidates = [
        ReplyCandidateRow(
            email_id=1, sender="mailer-daemon@cliente.cl",
            subject="Undelivered Mail Returned to Sender: RE: Cotización COT-2026-014",
            date_iso="2026-05-10T01:00:00",
            body_snippet="This is an automatically generated message. Cotización COT-2026-014.",
        )
    ]
    result = classify_response(candidates, quote_number="COT-2026-014", normalized_quote_subject_core="cotizacion")
    assert result.response_state == "bounced"
    assert result.response_state != "replied"


def test_classify_response_bounce_not_counted_as_replied():
    candidates = [
        ReplyCandidateRow(
            email_id=1, sender="mailer-daemon@cliente.cl", subject="Undelivered Mail Returned to Sender",
            date_iso="2026-05-10T01:00:00", body_snippet="This is an automatically generated message.",
        )
    ]
    result = classify_response(candidates, quote_number="COT-2026-014", normalized_quote_subject_core="cotizacion")
    assert result.response_state == "bounced"


def test_classify_response_multiple_conflicting_candidates_is_unknown():
    candidates = [
        ReplyCandidateRow(
            email_id=1, sender="compras@cliente.cl", subject="Otro asunto sin relación",
            date_iso="2026-05-12T00:00:00", body_snippet="algo distinto",
        ),
        ReplyCandidateRow(
            email_id=2, sender="compras@cliente.cl", subject="Otro asunto también sin relación",
            date_iso="2026-05-13T00:00:00", body_snippet="otra cosa distinta",
        ),
    ]
    result = classify_response(candidates, quote_number="COT-2026-014", normalized_quote_subject_core="cotizacion")
    assert result.response_state == "unknown"
    assert result.review_required is True
