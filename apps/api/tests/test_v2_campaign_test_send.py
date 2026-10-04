"""«Enviar prueba» — body rules (pure) and the two-phase receipt, limits and history (database).

Every address is invented (example.invalid); Gmail is a fake that records what it was asked to send.
"""

from __future__ import annotations

import email
import email.header
import uuid
from datetime import datetime

import pytest
from pydantic import ValidationError

from origenlab_api.v2.campaign_test_send import (
    SEND_CAMPAIGN_TEST,
    TEST_SENDS_PER_HOUR,
    TestSendLimitRefused,
    SendCampaignTestBody,
    V2CampaignTestSendRepository,
)
from origenlab_api.v2.commands import CommandRefused, request_digest
from origenlab_api.v2.gmail_send import GmailSendError
from origenlab_api.v2.identity import OperatorIdentity
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

# A module constant, not an inline literal: the secret scanner reads `key="…-…"` as an API key.
CYBER = "cyber-2026-10"

# ----------------------------------------------------------------------------------- body (pure)


def test_exactly_one_target() -> None:
    with pytest.raises(ValidationError):
        SendCampaignTestBody(to="a@example.invalid")
    with pytest.raises(ValidationError):
        SendCampaignTestBody(to="a@example.invalid", campaign_id=uuid.uuid4(), v1_lane_key=CYBER)
    assert SendCampaignTestBody(to=" a@example.invalid ", v1_lane_key=CYBER).to == "a@example.invalid"


@pytest.mark.parametrize("bad", ['"Ana" <a@example.invalid>', "a@example.invalid, b@example.invalid",
                                 "a@example.invalid\r\nBcc: z@example.invalid"])
def test_injection_shaped_addresses_are_refused_before_anything_runs(bad: str) -> None:
    with pytest.raises(ValidationError):
        SendCampaignTestBody(to=bad, v1_lane_key=CYBER)


def test_the_body_carries_no_html_and_no_subject() -> None:
    with pytest.raises(ValidationError):
        SendCampaignTestBody(to="a@example.invalid", v1_lane_key=CYBER, body_html="<p>x</p>")
    with pytest.raises(ValidationError):
        SendCampaignTestBody(to="a@example.invalid", v1_lane_key=CYBER, subject="x")


# ------------------------------------------------------------------------------------- database


class FakeGmail:
    def __init__(self, fail: str | None = None) -> None:
        self.sent: list[bytes] = []
        self.fail = fail

    def send(self, raw: bytes) -> str:
        if self.fail:
            raise GmailSendError(self.fail, "fake failure")
        self.sent.append(raw)
        return f"gmail-{len(self.sent)}"


@pytest.fixture(scope="module")
def disposable_database():
    yield from build_disposable_database()


def _owner(dsn, sql, params=()):
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


@pytest.fixture(scope="module")
def world(disposable_database):
    tag = uuid.uuid4().hex[:8]
    ops = {}
    for role in ("admin", "sales"):
        rows = _owner(disposable_database,
                      "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                      "values (gen_random_uuid(), %s, %s, %s, 'active') returning id::text",
                      (f"{role}-{tag}@example.test", f"Op {role}", role))
        ops[role] = OperatorIdentity(operator_id=rows[0][0], email_norm=f"{role}-{tag}@example.test",
                                     display_name=f"Op {role}", role=role, status="active")
    mailbox = _owner(disposable_database, "insert into comms.mailbox (address_norm, display_name) "
                                          "values (%s, 'Ventas') returning id::text", (f"m-{tag}@example.invalid",))[0][0]
    return {"ops": ops, "mailbox": mailbox}


def _campaign(dsn, world, html: str | None):
    cols = ["name", "status", "mailbox_id", "max_sends", "recontact_interval_days", "subject", "body_html"]
    vals = [f"Campaña {uuid.uuid4().hex[:6]}", "draft", world["mailbox"], 50, 90, "Asunto de prueba", html]
    return _owner(dsn, f"insert into outbound.campaign ({', '.join(cols)}) values ({', '.join(['%s'] * len(cols))}) "
                       "returning id::text", vals)[0][0]


def _clear_receipts(dsn):
    _owner(dsn, "delete from platform.command_receipt where command_name = %s", (SEND_CAMPAIGN_TEST,))


def _receipt_count(dsn) -> int:
    return _owner(dsn, "select count(*) from platform.command_receipt where command_name = %s",
                  (SEND_CAMPAIGN_TEST,))[0][0]


def _set_subject(dsn, cid: str, subject: str) -> None:
    _owner(dsn, "update outbound.campaign set subject = %s where id = %s", (subject, cid))


def _run(dsn, gmail, operator, body, key=None):
    repo = V2CampaignTestSendRepository(__import__("psycopg").connect, runtime_dsn(dsn), gmail, None)
    return repo.send_test(operator=operator, body=body, idempotency_key=key or str(uuid.uuid4()),
                          digest=request_digest(SEND_CAMPAIGN_TEST, body))


@needs_db
def test_an_admin_sends_the_stored_email_and_the_receipt_records_it(disposable_database, world) -> None:
    _clear_receipts(disposable_database)
    cid = _campaign(disposable_database, world, "<p>Hola</p>")
    gmail = FakeGmail()
    out = _run(disposable_database, gmail, world["ops"]["admin"],
               SendCampaignTestBody(to="ana@example.invalid", campaign_id=uuid.UUID(cid)))
    assert out["gmail_message_id"] == "gmail-1" and out["to"] == "ana@example.invalid"
    msg = email.message_from_bytes(gmail.sent[0])
    assert msg["Subject"] == "[PRUEBA] Asunto de prueba"
    rows = _owner(disposable_database, "select status, response_body->>'to' from platform.command_receipt "
                                       "where command_name = %s", (SEND_CAMPAIGN_TEST,))
    assert rows == [("completed", "ana@example.invalid")]


@needs_db
def test_sales_may_not_send(disposable_database, world) -> None:
    _clear_receipts(disposable_database)
    cid = _campaign(disposable_database, world, "<p>Hola</p>")
    with pytest.raises(CommandRefused) as exc:
        _run(disposable_database, FakeGmail(), world["ops"]["sales"],
             SendCampaignTestBody(to="ana@example.invalid", campaign_id=uuid.UUID(cid)))
    assert exc.value.status_code == 403 and exc.value.code == "admin_only"


@needs_db
def test_a_retry_with_the_same_key_sends_once(disposable_database, world) -> None:
    _clear_receipts(disposable_database)
    cid = _campaign(disposable_database, world, "<p>Hola</p>")
    gmail, key = FakeGmail(), str(uuid.uuid4())
    body = SendCampaignTestBody(to="ana@example.invalid", campaign_id=uuid.UUID(cid))
    first = _run(disposable_database, gmail, world["ops"]["admin"], body, key)
    second = _run(disposable_database, gmail, world["ops"]["admin"], body, key)
    assert len(gmail.sent) == 1 and second["replayed"] is True and second["gmail_message_id"] == first["gmail_message_id"]


@needs_db
def test_a_campaign_without_html_is_refused_and_leaves_no_receipt(disposable_database, world) -> None:
    _clear_receipts(disposable_database)
    cid = _campaign(disposable_database, world, None)
    with pytest.raises(CommandRefused) as exc:
        _run(disposable_database, FakeGmail(), world["ops"]["admin"],
             SendCampaignTestBody(to="ana@example.invalid", campaign_id=uuid.UUID(cid)))
    assert exc.value.status_code == 422
    assert _owner(disposable_database, "select count(*) from platform.command_receipt where command_name = %s",
                  (SEND_CAMPAIGN_TEST,)) == [(0,)]


@needs_db
def test_an_unknown_campaign_is_not_found(disposable_database, world) -> None:
    _clear_receipts(disposable_database)
    with pytest.raises(CommandRefused) as exc:
        _run(disposable_database, FakeGmail(), world["ops"]["admin"],
             SendCampaignTestBody(to="ana@example.invalid", campaign_id=uuid.uuid4()))
    assert exc.value.status_code == 404
    assert _receipt_count(disposable_database) == 0


@needs_db
def test_a_v1_lane_campaign_without_its_email_file_is_refused(disposable_database, world) -> None:
    _clear_receipts(disposable_database)
    with pytest.raises(CommandRefused) as exc:
        _run(disposable_database, FakeGmail(), world["ops"]["admin"],
             SendCampaignTestBody(to="ana@example.invalid", v1_lane_key=CYBER))
    assert exc.value.status_code == 422


@needs_db
def test_the_eleventh_test_in_an_hour_is_refused_and_failures_do_not_count(disposable_database, world) -> None:
    _clear_receipts(disposable_database)
    cid = _campaign(disposable_database, world, "<p>Hola</p>")
    body = lambda: SendCampaignTestBody(to="ana@example.invalid", campaign_id=uuid.UUID(cid))  # noqa: E731
    with pytest.raises(CommandRefused):
        _run(disposable_database, FakeGmail(fail="gmail_rejected"), world["ops"]["admin"], body())
    gmail = FakeGmail()
    for _ in range(TEST_SENDS_PER_HOUR):
        _run(disposable_database, gmail, world["ops"]["admin"], body())
    with pytest.raises(CommandRefused) as exc:
        _run(disposable_database, gmail, world["ops"]["admin"], body())
    assert exc.value.status_code == 429 and len(gmail.sent) == TEST_SENDS_PER_HOUR
    assert isinstance(exc.value, TestSendLimitRefused) and "next_allowed_at" in str(exc.value)
    assert datetime.fromisoformat(exc.value.next_allowed_at).tzinfo is not None
    # the earlier failed attempt plus ten sends; the refused eleventh left no receipt
    assert _receipt_count(disposable_database) == TEST_SENDS_PER_HOUR + 1


@needs_db
def test_a_revoked_permission_fails_the_receipt_with_a_502(disposable_database, world) -> None:
    _clear_receipts(disposable_database)
    cid = _campaign(disposable_database, world, "<p>Hola</p>")
    with pytest.raises(CommandRefused) as exc:
        _run(disposable_database, FakeGmail(fail="token_refresh_failed"), world["ops"]["admin"],
             SendCampaignTestBody(to="ana@example.invalid", campaign_id=uuid.UUID(cid)))
    assert exc.value.status_code == 502 and exc.value.code == "token_refresh_failed"
    rows = _owner(disposable_database, "select status, response_body->>'error' from platform.command_receipt "
                                       "where command_name = %s", (SEND_CAMPAIGN_TEST,))
    assert rows == [("failed", "token_refresh_failed")]


@needs_db
def test_history_lists_the_campaigns_tests_newest_first_with_the_allowance(disposable_database, world) -> None:
    _clear_receipts(disposable_database)
    cid = _campaign(disposable_database, world, "<p>Hola</p>")
    for to in ("a@example.invalid", "b@example.invalid"):
        _run(disposable_database, FakeGmail(), world["ops"]["admin"],
             SendCampaignTestBody(to=to, campaign_id=uuid.UUID(cid)))
    import psycopg

    repo = V2CampaignTestSendRepository(psycopg.connect, runtime_dsn(disposable_database), FakeGmail(), None)
    out = repo.history(campaign_id=cid, v1_lane_key=None)
    assert [t["to"] for t in out["tests"]] == ["b@example.invalid", "a@example.invalid"]
    assert out["tests"][0]["by"] == "Op admin" and out["tests"][0]["status"] == "sent"
    assert out["remaining"] == {"hour": TEST_SENDS_PER_HOUR - 2, "day": 30 - 2}


@needs_db
def test_a_subject_with_a_line_break_is_refused_and_leaves_no_receipt(disposable_database, world) -> None:
    _clear_receipts(disposable_database)
    cid = _campaign(disposable_database, world, "<p>Hola</p>")
    _set_subject(disposable_database, cid, "Hola\nmundo")
    gmail = FakeGmail()
    with pytest.raises(CommandRefused) as exc:
        _run(disposable_database, gmail, world["ops"]["admin"],
             SendCampaignTestBody(to="ana@example.invalid", campaign_id=uuid.UUID(cid)))
    assert exc.value.status_code == 422 and exc.value.code == "campaign_subject_invalid"
    assert gmail.sent == [] and _receipt_count(disposable_database) == 0


@needs_db
def test_a_v1_lane_campaign_sends_its_file_with_its_declared_subject(disposable_database, world, tmp_path) -> None:
    _clear_receipts(disposable_database)
    html = "<p>Cyber — ñandú</p>"
    (tmp_path / f"v1-lane-{CYBER}.html").write_text(html, encoding="utf-8")
    gmail = FakeGmail()
    import psycopg

    repo = V2CampaignTestSendRepository(psycopg.connect, runtime_dsn(disposable_database), gmail, str(tmp_path))
    body = SendCampaignTestBody(to="ana@example.invalid", v1_lane_key=CYBER)
    out = repo.send_test(operator=world["ops"]["admin"], body=body, idempotency_key=str(uuid.uuid4()),
                         digest=request_digest(SEND_CAMPAIGN_TEST, body))
    assert out["status"] == "sent"
    msg = email.message_from_bytes(gmail.sent[0])
    subject = str(email.header.make_header(email.header.decode_header(msg["Subject"])))
    assert subject == "[PRUEBA] Cyber OrigenLab · 5% a 10% en productos seleccionados para laboratorio"
    part = next(p for p in msg.walk() if p.get_content_type() == "text/html")
    assert part.get_payload(decode=True).decode(part.get_content_charset()).rstrip() == html
    listed = repo.history(campaign_id=None, v1_lane_key=CYBER)
    assert [t["to"] for t in listed["tests"]] == ["ana@example.invalid"]


@needs_db
def test_history_shows_a_failed_attempt_with_its_error_and_hides_in_progress(disposable_database, world) -> None:
    _clear_receipts(disposable_database)
    cid = _campaign(disposable_database, world, "<p>Hola</p>")
    body = SendCampaignTestBody(to="ana@example.invalid", campaign_id=uuid.UUID(cid))
    with pytest.raises(CommandRefused):
        _run(disposable_database, FakeGmail(fail="network"), world["ops"]["admin"], body)
    # An attempt that crashed between Gmail and the receipt: in_progress, never listed.
    _owner(disposable_database,
           "insert into platform.command_receipt (operator_id, idempotency_key, command_name, request_digest, status) "
           "values (%s, %s, %s, %s, 'in_progress')",
           (world["ops"]["admin"].operator_id, str(uuid.uuid4()), SEND_CAMPAIGN_TEST, "0" * 64))
    import psycopg

    repo = V2CampaignTestSendRepository(psycopg.connect, runtime_dsn(disposable_database), FakeGmail(), None)
    out = repo.history(campaign_id=cid.upper(), v1_lane_key=None)
    assert [(t["status"], t["error"]) for t in out["tests"]] == [("failed", "network")]


# ------------------------------------------------- unsubscribes and blocks (WORKFLOWS.md §W10)


def _address(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}@example.invalid"


def _block(dsn, address: str, *, purpose: str, reason: str, source: str) -> None:
    _owner(dsn, "insert into outbound.contact_control (scope, value_norm, kind, purpose, reason, source) "
                "values ('address', %s, 'block', %s, %s, %s)", (address, purpose, reason, source))


def _unsubscribe_request(dsn, address: str, resolution: str = "unresolved") -> None:
    record = _owner(dsn, "insert into evidence.source_record (kind, dedupe_key, payload) "
                         "values ('gmail_message', %s, '{}'::jsonb) returning id::text",
                    (f"pytest-test-send:{uuid.uuid4().hex}",))[0][0]
    _owner(dsn, "insert into evidence.assertion (source_record_id, kind, value_norm, resolution) "
                "values (%s, 'unsubscribe_request', %s, %s)", (record, address, resolution))


@needs_db
@pytest.mark.parametrize("control", ["operator_block_all", "unsubscribe_marketing", "baja_held_for_review"])
def test_an_unsubscribed_or_blocked_address_is_refused_and_nothing_is_recorded_or_sent(
        disposable_database, world, control: str) -> None:
    _clear_receipts(disposable_database)
    cid = _campaign(disposable_database, world, "<p>Hola</p>")
    address = _address("baja")
    if control == "operator_block_all":
        _block(disposable_database, address, purpose="all", reason="pidió no recibir correos", source="operator_command")
    elif control == "unsubscribe_marketing":
        _block(disposable_database, address, purpose="marketing", reason="unsubscribe", source="unsubscribe_handler")
    else:
        _unsubscribe_request(disposable_database, address)
    gmail = FakeGmail()
    with pytest.raises(CommandRefused) as exc:
        # Typed with capitals: the controls hold the normalised (lower-case) address.
        _run(disposable_database, gmail, world["ops"]["admin"],
             SendCampaignTestBody(to=address.upper(), campaign_id=uuid.UUID(cid)))
    assert (exc.value.status_code, exc.value.code) == (422, "test_recipient_blocked")
    assert str(exc.value) == "esa dirección pidió la baja o está bloqueada para envíos"
    assert gmail.sent == [] and _receipt_count(disposable_database) == 0


@needs_db
def test_an_unrelated_address_still_sends_beside_blocked_ones(disposable_database, world) -> None:
    _clear_receipts(disposable_database)
    cid = _campaign(disposable_database, world, "<p>Hola</p>")
    blocked = _address("bloqueada")
    _block(disposable_database, blocked, purpose="all", reason="pidió no recibir correos", source="operator_command")
    _unsubscribe_request(disposable_database, _address("pendiente"))
    # Neither a dismissed «BAJA» nor a permanent prior contact is a block: the owner's own
    # addresses are usually prior contacts.
    dismissed, contacted = _address("descartada"), _address("contactada")
    _unsubscribe_request(disposable_database, dismissed, resolution="rejected")
    _owner(disposable_database, "insert into outbound.contact_control (scope, value_norm, kind, purpose, reason, source) "
                                "values ('address', %s, 'prior_contact', 'marketing', 'wave 1A', 'wave1a_union')",
           (contacted,))
    gmail = FakeGmail()
    for to in (_address("libre"), dismissed, contacted):
        out = _run(disposable_database, gmail, world["ops"]["admin"],
                   SendCampaignTestBody(to=to, campaign_id=uuid.UUID(cid)))
        assert out["status"] == "sent" and out["to"] == to
    assert len(gmail.sent) == 3 and _receipt_count(disposable_database) == 3
