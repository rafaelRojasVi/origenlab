"""`prepare-prospect-email` («Preparar correo») against a disposable database, with a fake Gmail.

The command drafts, never sends: the fake records every call and has no `send`. Every value is
invented (public repository).
"""

from __future__ import annotations

import email
import email.policy
import uuid

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_v2_prospect_findings_reads_db import _Lookup, _owner, blocked, finding
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
from origenlab_api.v2.gmail_send import GmailSendError
from origenlab_api.v2.identity import LocalDevIdentity, OperatorIdentity
from origenlab_api.v2.prospect_findings.commands import V2ProspectCommandRepository
from origenlab_api.v2.prospect_findings.reads import ProspectFindingReadRepository
from origenlab_api.v2.prospect_findings.routes import prospect_command_router, prospect_read_router

pytestmark = needs_db
LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"
SIGNATURE = "<div>Firma Ficticia<br>OrigenLab</div>"


class FakeGmail:
    """Drafts and labels only — there is deliberately no `send`, so a send would raise."""

    def __init__(self, *, labels_fail: bool = False, draft_fail: bool = False, signature: str | None = SIGNATURE):
        self.drafts: list[bytes] = []
        self.labelled: list[tuple[str, list[str]]] = []
        self.labels_fail, self.draft_fail, self.signature = labels_fail, draft_fail, signature

    def fetch_send_as_signature(self) -> str | None:
        return self.signature

    def create_draft(self, raw: bytes) -> tuple[str, str]:
        if self.draft_fail:
            raise GmailSendError("draft_creation_failed", "Gmail answered 500", "HTTP 500")
        self.drafts.append(raw)
        n = len(self.drafts)
        return f"r-draft{n}", f"msg{n}"

    def apply_labels(self, message_id: str, names: list[str]) -> None:
        if self.labels_fail:
            raise GmailSendError("label_apply_failed", "Gmail answered 403", "HTTP 403")
        self.labelled.append((message_id, names))


@pytest.fixture(scope="module")
def db():
    yield from build_disposable_database()


@pytest.fixture(scope="module")
def operators(db):
    out = {}
    with _owner(db) as conn:
        for role in ("sales", "viewer"):
            out[role] = OperatorIdentity(
                operator_id=conn.execute(
                    "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                    "values (gen_random_uuid(), %s, %s, %s, 'active') returning id::text",
                    (f"{role}-correo@example.test", f"Operador {role}", role)).fetchone()[0],
                email_norm=f"{role}-correo@example.test", display_name=f"Operador {role}", role=role,
                status="active")
    return out


def _client(db, operators, sender) -> TestClient:
    app = FastAPI()
    app.include_router(prospect_read_router)
    app.include_router(prospect_command_router)
    app.state.v2_prospect_read_repository = ProspectFindingReadRepository(psycopg.connect, runtime_dsn(db))
    app.state.v2_prospect_command_repository = V2ProspectCommandRepository(
        psycopg.connect, runtime_dsn(db), sender=sender)
    app.state.v2_identity = LocalDevIdentity(LOOPBACK, _Lookup(operators))
    return TestClient(app)


def _prepare(client, operators, body, *, role="sales", key=None):
    return client.post("/v2/commands/prepare-prospect-email", json=body,
                       headers={OPERATOR_EMAIL_HEADER: operators[role].email_norm,
                                "Idempotency-Key": key or str(uuid.uuid4())})


def _one(db, sql, params=()):
    with _owner(db) as conn:
        return conn.execute(sql, params).fetchone()


def _drafted_events(db, rid):
    return _one(db, "select count(*) from crm.domain_event where aggregate_id = %s "
                    "and event_type = 'source_record.prospect_email_drafted'", (rid,))[0]


def test_drafts_with_catalogue_signature_and_labels_and_contacts(db, operators):
    gmail = FakeGmail()
    client = _client(db, operators, gmail)
    rid = finding(db, email="dra.ficticia@example.test")
    r = _prepare(client, operators, {"finding_id": rid})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["gmail_url"] == "https://mail.google.com/mail/u/0/#drafts?compose=msg1"
    assert body["labels_applied"] is True and body["signature"] is True and body["contacted"] is True
    assert gmail.labelled == [("msg1", ["Claude", "Prospecto"])]
    msg = email.message_from_bytes(gmail.drafts[0], policy=email.policy.default)
    assert msg["To"] == "dra.ficticia@example.test"
    assert msg["Subject"] == "Equipamiento para su proyecto Fondequip 2025 — OrigenLab"
    parts = {p.get_content_type(): p for p in msg.walk()}
    assert parts["application/pdf"].get_filename() == "Catalogo-OrigenLab-2026.pdf"
    assert parts["application/pdf"].get_payload(decode=True).startswith(b"%PDF")
    assert "Firma Ficticia" in parts["text/html"].get_payload(decode=True).decode()
    assert "Firma Ficticia" in parts["text/plain"].get_payload(decode=True).decode()
    # «Contactar» ran first: the prospect left the queue and its person is in the CRM.
    assert _one(db, "select verdict from crm.prospect_review where source_record_id = %s "
                    "order by reviewed_at desc limit 1", (rid,))[0] == "contactar"
    assert _one(db, "select count(*) from crm.contact_point where value_norm = 'dra.ficticia@example.test'")[0] == 1
    item = client.get(f"/v2/prospect-findings/{rid}",
                      headers={OPERATOR_EMAIL_HEADER: operators["sales"].email_norm}).json()
    item = item.get("item", item)
    assert item["email_draft"]["gmail_url"] == body["gmail_url"]


def test_second_draft_needs_again(db, operators):
    gmail = FakeGmail()
    client = _client(db, operators, gmail)
    rid = finding(db, email="dos.borradores@example.test")
    assert _prepare(client, operators, {"finding_id": rid}).status_code == 200
    r = _prepare(client, operators, {"finding_id": rid})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "prospect_email_already_drafted"
    again = _prepare(client, operators, {"finding_id": rid, "again": True})
    assert again.status_code == 200, again.text
    assert again.json()["contacted"] is False
    assert len(gmail.drafts) == 2 and _drafted_events(db, rid) == 2


def test_replay_returns_the_same_draft(db, operators):
    gmail = FakeGmail()
    client = _client(db, operators, gmail)
    rid = finding(db, email="doble.clic.correo@example.test")
    key = str(uuid.uuid4())
    first = _prepare(client, operators, {"finding_id": rid}, key=key)
    second = _prepare(client, operators, {"finding_id": rid}, key=key)
    assert first.status_code == second.status_code == 200
    assert second.json()["replayed"] is True and second.json()["draft_id"] == first.json()["draft_id"]
    assert len(gmail.drafts) == 1


def test_refusals_leave_no_draft(db, operators):
    gmail = FakeGmail()
    client = _client(db, operators, gmail)
    no_mail = finding(db, email=None)
    r = _prepare(client, operators, {"finding_id": no_mail})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "prospect_no_email"
    gone = finding(db, email="se.fue.correo@example.test")
    blocked(db, "se.fue.correo@example.test")
    r = _prepare(client, operators, {"finding_id": gone})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "prospect_blocked"
    r = _prepare(client, operators, {"finding_id": str(uuid.uuid4())})
    assert r.status_code == 404
    r = _prepare(client, operators, {"finding_id": finding(db, email="visor@example.test")}, role="viewer")
    assert r.status_code == 403
    assert gmail.drafts == []
    assert _one(db, "select count(*) from crm.prospect_review where source_record_id = %s", (gone,))[0] == 0


def test_without_gmail_is_refused_not_recorded(db, operators):
    client = _client(db, operators, None)
    rid = finding(db, email="sin.gmail@example.test")
    r = _prepare(client, operators, {"finding_id": rid})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "gmail_not_configured"
    assert _drafted_events(db, rid) == 0


def test_missing_modify_scope_keeps_the_draft_without_labels(db, operators):
    gmail = FakeGmail(labels_fail=True, signature=None)
    client = _client(db, operators, gmail)
    rid = finding(db, email="sin.etiquetas@example.test")
    r = _prepare(client, operators, {"finding_id": rid})
    assert r.status_code == 200, r.text
    assert r.json()["labels_applied"] is False and r.json()["signature"] is False
    plain = next(p for p in email.message_from_bytes(gmail.drafts[0], policy=email.policy.default).walk()
                 if p.get_content_type() == "text/plain")
    assert "contacto@origenlab.cl" in plain.get_payload(decode=True).decode()  # the fallback block


def test_gmail_failure_fails_the_receipt_and_records_no_draft(db, operators):
    client = _client(db, operators, FakeGmail(draft_fail=True))
    rid = finding(db, email="gmail.caido@example.test")
    r = _prepare(client, operators, {"finding_id": rid})
    assert r.status_code == 502 and r.json()["detail"]["code"] == "draft_creation_failed"
    assert _drafted_events(db, rid) == 0
    assert _one(db, "select status from platform.command_receipt where command_name = 'prepare-prospect-email' "
                    "order by created_at desc limit 1")[0] == "failed"
    # A retry with a new key, once Gmail is back, drafts (the contact already happened).
    retry = _prepare(_client(db, operators, FakeGmail()), operators, {"finding_id": rid})
    assert retry.status_code == 200 and retry.json()["contacted"] is False

