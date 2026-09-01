"""Tests for outbound_campaign_sender. Never calls the real Gmail API — always mocked."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from origenlab_email_pipeline.candidate_export_gate import GateContext
from origenlab_email_pipeline.outbound_campaign_schema import ensure_outbound_campaign_tables
from origenlab_email_pipeline.outbound_campaign_sender import send_campaign_batch
from origenlab_email_pipeline.outbound_campaign_store import (
    create_campaign,
    has_accepted_attempt,
    upsert_recipient_candidate,
)


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(tmp_path / "t.sqlite"))
    ensure_outbound_campaign_tables(c)
    create_campaign(
        c, campaign_id="hielscher-sonicators-2026", name="Hielscher Sonicadores Laboratorio",
        sender_email="contacto@origenlab.cl", sender_name="Tatiana Vivanco | OrigenLab",
        subject="Sonicadores Hielscher para laboratorio | OrigenLab",
        target_attempt_count=2000, baseline_attempt_count=874,
    )
    yield c
    c.close()


def _permissive_ctx() -> GateContext:
    return GateContext(
        sent_recipient_norms=frozenset(), suppressed_norms=frozenset(),
        outreach_state_by_email={}, supplier_domains=frozenset(), blocked_domains=frozenset(),
    )


@pytest.fixture()
def html_file(tmp_path: Path) -> Path:
    p = tmp_path / "campaign.html"
    p.write_text("<html><body>Hola</body></html>", encoding="utf-8")
    return p


def test_dry_run_does_not_call_gmail_api(conn: sqlite3.Connection, html_file: Path) -> None:
    rid = upsert_recipient_candidate(conn, campaign_id="hielscher-sonicators-2026", email="a@x.cl", source_kind="manual")
    with patch("origenlab_email_pipeline.outbound_campaign_sender.gmail_api_send_message") as mock_send:
        outcomes = send_campaign_batch(
            conn, campaign_id="hielscher-sonicators-2026", recipients=[(rid, "a@x.cl")],
            html=html_file.read_text(), html_dir=html_file.parent, live=False, access_token=None,
            gate_ctx=_permissive_ctx(), batch_id="b1",
        )
        mock_send.assert_not_called()
    assert outcomes[0].result == "accepted"
    assert outcomes[0].mode == "dry_run"
    assert outcomes[0].gmail_message_id is None


def test_live_send_records_message_id(conn: sqlite3.Connection, html_file: Path) -> None:
    rid = upsert_recipient_candidate(conn, campaign_id="hielscher-sonicators-2026", email="a@x.cl", source_kind="manual")
    with patch(
        "origenlab_email_pipeline.outbound_campaign_sender.gmail_api_send_message",
        return_value={"id": "MSG-ABC"},
    ):
        outcomes = send_campaign_batch(
            conn, campaign_id="hielscher-sonicators-2026", recipients=[(rid, "a@x.cl")],
            html=html_file.read_text(), html_dir=html_file.parent, live=True, access_token="tok",
            gate_ctx=_permissive_ctx(), batch_id="b1",
        )
    assert outcomes[0].result == "accepted"
    assert outcomes[0].gmail_message_id == "MSG-ABC"
    assert has_accepted_attempt(conn, "hielscher-sonicators-2026", rid) is not None


def test_retry_after_accepted_send_is_a_noop(conn: sqlite3.Connection, html_file: Path) -> None:
    rid = upsert_recipient_candidate(conn, campaign_id="hielscher-sonicators-2026", email="a@x.cl", source_kind="manual")
    with patch(
        "origenlab_email_pipeline.outbound_campaign_sender.gmail_api_send_message",
        return_value={"id": "MSG-1"},
    ) as mock_send:
        send_campaign_batch(
            conn, campaign_id="hielscher-sonicators-2026", recipients=[(rid, "a@x.cl")],
            html=html_file.read_text(), html_dir=html_file.parent, live=True, access_token="tok",
            gate_ctx=_permissive_ctx(), batch_id="b1",
        )
        outcomes2 = send_campaign_batch(
            conn, campaign_id="hielscher-sonicators-2026", recipients=[(rid, "a@x.cl")],
            html=html_file.read_text(), html_dir=html_file.parent, live=True, access_token="tok",
            gate_ctx=_permissive_ctx(), batch_id="b2",
        )
    assert mock_send.call_count == 1  # second invocation never calls the API again
    assert outcomes2[0].result == "skipped"
    assert outcomes2[0].error == "already_sent"
    assert outcomes2[0].gmail_message_id == "MSG-1"


def test_partial_batch_failure_preserves_prior_accepted_attempts(conn: sqlite3.Connection, html_file: Path) -> None:
    r1 = upsert_recipient_candidate(conn, campaign_id="hielscher-sonicators-2026", email="a@x.cl", source_kind="manual")
    r2 = upsert_recipient_candidate(conn, campaign_id="hielscher-sonicators-2026", email="b@x.cl", source_kind="manual")
    with patch(
        "origenlab_email_pipeline.outbound_campaign_sender.gmail_api_send_message",
        side_effect=[{"id": "MSG-1"}, RuntimeError("quota exceeded")],
    ):
        outcomes = send_campaign_batch(
            conn, campaign_id="hielscher-sonicators-2026", recipients=[(r1, "a@x.cl"), (r2, "b@x.cl")],
            html=html_file.read_text(), html_dir=html_file.parent, live=True, access_token="tok",
            gate_ctx=_permissive_ctx(), batch_id="b1", stop_on_error=True,
        )
    assert outcomes[0].result == "accepted"
    assert outcomes[1].result == "failed"
    assert has_accepted_attempt(conn, "hielscher-sonicators-2026", r1) is not None
    assert has_accepted_attempt(conn, "hielscher-sonicators-2026", r2) is None


def test_pre_send_recheck_blocks_manual_inactive(conn: sqlite3.Connection, html_file: Path) -> None:
    rid = upsert_recipient_candidate(
        conn, campaign_id="hielscher-sonicators-2026", email="carolinalobo@pharmaisa.cl", source_kind="manual",
    )
    from origenlab_email_pipeline.manual_contact_status import upsert_manual_contact_status, validate_manual_contact_status_payload
    upsert_manual_contact_status(
        conn, payload=validate_manual_contact_status_payload(email="carolinalobo@pharmaisa.cl", status="inactive"),
    )
    with patch("origenlab_email_pipeline.outbound_campaign_sender.gmail_api_send_message") as mock_send:
        outcomes = send_campaign_batch(
            conn, campaign_id="hielscher-sonicators-2026", recipients=[(rid, "carolinalobo@pharmaisa.cl")],
            html=html_file.read_text(), html_dir=html_file.parent, live=True, access_token="tok",
            gate_ctx=_permissive_ctx(), batch_id="b1",
        )
        mock_send.assert_not_called()
    assert outcomes[0].result == "skipped"
    assert outcomes[0].error == "manual_inactive"


def test_no_automatic_cc_to_maribel(conn: sqlite3.Connection, html_file: Path) -> None:
    """Maribel's 'copy on QC comms' note must never translate into an auto-CC header."""
    from origenlab_email_pipeline.manual_contact_status import upsert_manual_contact_status, validate_manual_contact_status_payload
    upsert_manual_contact_status(
        conn,
        payload=validate_manual_contact_status_payload(
            email="maribelcastillo@pharmaisa.cl", status="active",
            role_label="Control de Calidad - solicita copia en comunicaciones QC",
        ),
    )
    rid = upsert_recipient_candidate(conn, campaign_id="hielscher-sonicators-2026", email="jeanettetorres@pharmaisa.cl", source_kind="manual")
    captured = {}

    def _capture(*, sender_email, to_emails, subject, html, html_dir, cc_emails=None):
        from origenlab_email_pipeline.gmail_send import build_gmail_message_with_inline_images as real
        msg, imgs = real(sender_email=sender_email, to_emails=to_emails, subject=subject, html=html, html_dir=html_dir, cc_emails=cc_emails)
        captured["cc"] = msg.get("Cc")
        return msg, imgs

    with patch("origenlab_email_pipeline.outbound_campaign_sender.build_gmail_message_with_inline_images", side_effect=_capture):
        send_campaign_batch(
            conn, campaign_id="hielscher-sonicators-2026", recipients=[(rid, "jeanettetorres@pharmaisa.cl")],
            html=html_file.read_text(), html_dir=html_file.parent, live=False, access_token=None,
            gate_ctx=_permissive_ctx(), batch_id="b1",
        )
    assert captured["cc"] is None


def test_temp_batch_file_removed_after_success(conn: sqlite3.Connection, html_file: Path) -> None:
    rid = upsert_recipient_candidate(conn, campaign_id="hielscher-sonicators-2026", email="a@x.cl", source_kind="manual")
    before = set(Path(tempfile.gettempdir()).glob("outbound_campaign_batch_*"))
    with patch("origenlab_email_pipeline.outbound_campaign_sender.gmail_api_send_message", return_value={"id": "M1"}):
        send_campaign_batch(
            conn, campaign_id="hielscher-sonicators-2026", recipients=[(rid, "a@x.cl")],
            html=html_file.read_text(), html_dir=html_file.parent, live=True, access_token="tok",
            gate_ctx=_permissive_ctx(), batch_id="b1",
        )
    after = set(Path(tempfile.gettempdir()).glob("outbound_campaign_batch_*"))
    assert after == before  # no leftover temp file


def test_temp_batch_file_removed_after_sender_failure(conn: sqlite3.Connection, html_file: Path) -> None:
    rid = upsert_recipient_candidate(conn, campaign_id="hielscher-sonicators-2026", email="a@x.cl", source_kind="manual")
    before = set(Path(tempfile.gettempdir()).glob("outbound_campaign_batch_*"))
    with patch(
        "origenlab_email_pipeline.outbound_campaign_sender.gmail_api_send_message",
        side_effect=RuntimeError("network error"),
    ):
        send_campaign_batch(
            conn, campaign_id="hielscher-sonicators-2026", recipients=[(rid, "a@x.cl")],
            html=html_file.read_text(), html_dir=html_file.parent, live=True, access_token="tok",
            gate_ctx=_permissive_ctx(), batch_id="b1",
        )
    after = set(Path(tempfile.gettempdir()).glob("outbound_campaign_batch_*"))
    assert after == before


def test_live_requires_access_token(conn: sqlite3.Connection, html_file: Path) -> None:
    rid = upsert_recipient_candidate(conn, campaign_id="hielscher-sonicators-2026", email="a@x.cl", source_kind="manual")
    with pytest.raises(ValueError, match="access_token"):
        send_campaign_batch(
            conn, campaign_id="hielscher-sonicators-2026", recipients=[(rid, "a@x.cl")],
            html=html_file.read_text(), html_dir=html_file.parent, live=True, access_token=None,
            gate_ctx=_permissive_ctx(), batch_id="b1",
        )
