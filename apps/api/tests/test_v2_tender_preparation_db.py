"""Bid preparation against a disposable database: every refusal, the queued row, the event and the page.

Runs as `origenlab_api`. Every value is invented (public repository).
"""

from __future__ import annotations

import itertools
import uuid
from datetime import UTC, datetime

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
from origenlab_api.v2.catalog.storage import FakeStorage
from origenlab_api.v2.commands import request_digest
from origenlab_api.v2.identity import LocalDevIdentity, OperatorIdentity, OperatorLookup
from origenlab_api.v2.tenders.mercado_publico import TenderDetail
from origenlab_api.v2.tenders.open_case import (
    OPEN_TENDER_CASE,
    OpenTenderCaseBody,
    V2TenderCommandRepository,
    prepare_open_tender_case,
)
from origenlab_api.v2.tenders.reads import TenderReadRepository
from origenlab_api.v2.tenders.routes import tender_command_router, tender_read_router

pytestmark = needs_db
LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"
_KEYS = itertools.count(1)
_SERIALS = itertools.count(1)
URL = "/v2/commands/prepare-tender-bid"


@pytest.fixture(scope="module")
def db():
    yield from build_disposable_database()


def _owner(db):
    conn = psycopg.connect(db, autocommit=True)
    conn.execute("set role origenlab_owner")
    return conn


@pytest.fixture(scope="module")
def operators(db):
    out = {}
    with _owner(db) as conn:
        for role in ("sales", "viewer"):
            out[role] = OperatorIdentity(
                operator_id=conn.execute(
                    "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                    "values (gen_random_uuid(), %s, %s, %s, 'active') returning id::text",
                    (f"{role}-prep@example.test", f"Operador {role}", role)).fetchone()[0],
                email_norm=f"{role}-prep@example.test", display_name=f"Operador {role}", role=role,
                status="active")
    return out


class _Lookup(OperatorLookup):
    def __init__(self, operators) -> None:
        self._by_email = {o.email_norm: o for o in operators.values()}

    def by_email(self, email_norm: str):
        return self._by_email.get(email_norm)


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage("crm-documents")


@pytest.fixture
def client(db, operators, storage) -> TestClient:
    app = FastAPI()
    app.include_router(tender_command_router)
    app.include_router(tender_read_router)
    app.state.v2_tender_command_repository = V2TenderCommandRepository(psycopg.connect, runtime_dsn(db))
    app.state.v2_tender_read_repository = TenderReadRepository(psycopg.connect, runtime_dsn(db))
    app.state.case_document_storage = storage
    app.state.v2_identity = LocalDevIdentity(LOOPBACK, _Lookup(operators))
    return TestClient(app)


def headers(role: str = "sales", key: str | None = None) -> dict[str, str]:
    return {OPERATOR_EMAIL_HEADER: f"{role}-prep@example.test",
            "Idempotency-Key": key or f"pytest-prep-{next(_KEYS)}"}


def open_tender(db, operators) -> str:
    n = next(_SERIALS)
    tag = uuid.uuid4().hex[:6]
    code = f"{1235600 + n}-12-LE26"
    detail = TenderDetail(
        code=code, title=f"Adquisición de balanzas {tag}", buyer_name=f"Hospital Ejemplo {tag}",
        buyer_code=f"7{n:03d}{tag[:2]}", buyer_rut=None, closes_at=None, published_at=None,
        source_status="Publicada", head={"CodigoExterno": code})
    body = OpenTenderCaseBody(reference=code)
    fields = prepare_open_tender_case(body, ticket="ticket-ficticio", fetch=lambda c, ticket: detail)
    return V2TenderCommandRepository(psycopg.connect, runtime_dsn(db)).execute(
        command_name=OPEN_TENDER_CASE, operator=operators["sales"], fields=fields,
        idempotency_key=f"pytest-open-{next(_KEYS)}", digest=request_digest(OPEN_TENDER_CASE, body))["opportunity_id"]


def add_annex(db, case: str, operators) -> str:
    sha = uuid.uuid4().hex + uuid.uuid4().hex
    with _owner(db) as conn:
        return conn.execute(
            "insert into crm.opportunity_document (opportunity_id, kind, storage_path, sha256, content_type, "
            "byte_size, original_filename, source, attached_by_operator_id) "
            "values (%s, 'tender_annex', %s, %s, 'application/pdf', 10, 'bases.pdf', 'upload', %s) returning id::text",
            (case, f"opportunities/{case}/{sha}.pdf", sha, operators["sales"].operator_id)).fetchone()[0]


def add_offer(db, operators, case: str, *, status: str = "approved") -> str:
    with _owner(db) as conn:
        quote_id = conn.execute(
            "insert into crm.quote (opportunity_id, quote_number, created_by_operator_id) values (%s, %s, %s) "
            "returning id", (case, f"{next(_SERIALS) + 5000}-26", operators["sales"].operator_id)).fetchone()[0]
        approved = status in ("approved", "sent")
        return conn.execute(
            "insert into crm.quote_revision (quote_id, revision_no, status, quote_currency, price_decimals, "
            "subtotal, discount_total, tax_base, tax_total, grand_total, totals_computed_at, "
            "approved_at, approved_by_operator_id, party_snapshot, party_snapshot_version) "
            "values (%s, 1, %s, 'CLP', 0, 1000, 0, 1000, %s, %s, %s, %s, %s, %s, %s) returning id::text",
            (quote_id, status, 190 if approved else None, 1190 if approved else None,
             datetime.now(UTC) if approved else None, datetime.now(UTC) if approved else None,
             operators["sales"].operator_id if approved else None,
             psycopg.types.json.Jsonb({}) if approved else None, 1 if approved else None)).fetchone()[0]


def set_prep(db, prep_id: str, status: str, **cols) -> None:
    with _owner(db) as conn:
        conn.execute("update evidence.bid_preparation set status = %s, finished_at = now() where id = %s",
                     (status, prep_id)) if status != "running" else conn.execute(
            "update evidence.bid_preparation set status = 'running', started_at = now() where id = %s", (prep_id,))


def ensure_bidder(db, operators) -> None:
    with _owner(db) as conn:
        if conn.execute("select 1 from crm.bidder_profile").fetchone() is None:
            conn.execute(
                "insert into crm.bidder_profile (bidder_kind, legal_name, rut, set_by_operator_id, reason) "
                "values ('natural_person', 'Persona Ejemplo', '11111111-1', %s, 'prueba')",
                (operators["sales"].operator_id,))


def seed_spend(db, usd: str) -> None:
    with _owner(db) as conn:
        conn.execute("insert into platform.model_usage (feature, model, step, usd_cost) "
                     "values ('licitaciones', 'claude-opus-5-5', 'read', %s)", (usd,))


def ask(client, case: str, phase: str, role: str = "sales"):
    return client.post(URL, json={"opportunity_id": case, "phase": phase}, headers=headers(role))


def code(response) -> str:
    return response.json()["detail"]["code"]


def ready_read(client, db, case: str) -> str:
    r = ask(client, case, "read")
    assert r.status_code == 200, r.text
    pid = r.json()["preparation_id"]
    set_prep(db, pid, "ready")
    return pid


def test_read_without_annexes_is_no_annexes(db, operators, client) -> None:
    r = ask(client, open_tender(db, operators), "read")
    assert r.status_code == 422 and code(r) == "no_annexes"


def test_a_queued_read_takes_every_live_annex_and_shows_on_the_page(db, operators, client) -> None:
    case = open_tender(db, operators)
    ids = sorted([add_annex(db, case, operators), add_annex(db, case, operators)])
    r = ask(client, case, "read")
    assert r.status_code == 200, r.text
    pid = r.json()["preparation_id"]
    with _owner(db) as conn:
        row = conn.execute("select phase, status, annex_document_ids::text[], based_on_preparation_id, "
                           "quote_revision_id from evidence.bid_preparation where id = %s", (pid,)).fetchone()
        events = conn.execute("select payload from crm.domain_event where aggregate_id = %s "
                              "and event_type = 'opportunity.bid_preparation_requested'", (case,)).fetchall()
    assert row[0] == "read" and row[1] == "queued" and sorted(row[2]) == ids and row[3] is None and row[4] is None
    assert len(events) == 1 and events[0][0]["preparation_id"] == pid
    page = client.get(f"/v2/tenders/{case}", headers=headers()).json()
    assert page["preparations"]["read"]["status"] == "queued" and page["preparations"]["read"]["id"] == pid
    assert page["preparations"]["fill"] is None


def test_a_second_request_while_queued_or_running_is_refused(db, operators, client) -> None:
    case = open_tender(db, operators)
    add_annex(db, case, operators)
    pid = ask(client, case, "read").json()["preparation_id"]
    again = ask(client, case, "read")
    assert again.status_code == 409 and code(again) == "preparation_running"
    set_prep(db, pid, "running")
    assert code(ask(client, case, "read")) == "preparation_running"
    set_prep(db, pid, "failed")
    assert ask(client, case, "read").status_code == 200


def test_fill_needs_a_ready_read(db, operators, client) -> None:
    case = open_tender(db, operators)
    add_annex(db, case, operators)
    r = ask(client, case, "fill")
    assert r.status_code == 422 and code(r) == "read_not_ready"
    ask(client, case, "read")  # queued is not ready
    assert code(ask(client, case, "fill")) == "read_not_ready"


def test_fill_needs_an_offer_that_is_approved_and_a_bidder(db, operators, client) -> None:
    case = open_tender(db, operators)
    add_annex(db, case, operators)
    read_id = ready_read(client, db, case)
    ensure_bidder(db, operators)
    r = ask(client, case, "fill")
    assert r.status_code == 422 and code(r) == "offer_quote_missing"
    add_offer(db, operators, case, status="draft")
    r = ask(client, case, "fill")
    assert r.status_code == 422 and code(r) == "offer_not_approved"
    approved_case = open_tender(db, operators)
    add_annex(db, approved_case, operators)
    ready_read(client, db, approved_case)
    revision = add_offer(db, operators, approved_case, status="approved")
    ok = ask(client, approved_case, "fill")
    assert ok.status_code == 200, ok.text
    with _owner(db) as conn:
        row = conn.execute("select phase, status, based_on_preparation_id::text, quote_revision_id::text "
                           "from evidence.bid_preparation where id = %s", (ok.json()["preparation_id"],)).fetchone()
    assert row[0] == "fill" and row[1] == "queued" and row[3] == revision and row[2] is not None
    assert read_id  # the first case's read stayed untouched


def test_fill_without_a_bidder_profile_is_refused(db, operators, client) -> None:
    with _owner(db) as conn:
        conn.execute("alter table crm.bidder_profile disable trigger bidder_profile_append_only")
        conn.execute("delete from crm.bidder_profile")
    try:
        case = open_tender(db, operators)
        add_annex(db, case, operators)
        ready_read(client, db, case)
        add_offer(db, operators, case)
        r = ask(client, case, "fill")
        assert r.status_code == 422 and code(r) == "bidder_profile_missing"
    finally:
        with _owner(db) as conn:
            conn.execute("alter table crm.bidder_profile enable trigger bidder_profile_append_only")


def test_a_sale_case_and_a_missing_case_are_refused(db, operators, client) -> None:
    with _owner(db) as conn:
        sale = conn.execute(
            "insert into crm.opportunity (title, stage, owner_operator_id) values ('venta', 'lead', %s) "
            "returning id::text", (operators["sales"].operator_id,)).fetchone()[0]
    r = ask(client, sale, "read")
    assert r.status_code == 422 and code(r) == "not_a_tender_case"
    assert ask(client, str(uuid.uuid4()), "read").status_code == 404


def test_a_viewer_is_refused_and_a_replay_returns_the_same_preparation(db, operators, client) -> None:
    case = open_tender(db, operators)
    add_annex(db, case, operators)
    assert ask(client, case, "read", role="viewer").status_code == 403
    key = f"pytest-replay-{next(_KEYS)}"
    body = {"opportunity_id": case, "phase": "read"}
    first = client.post(URL, json=body, headers=headers(key=key)).json()
    second = client.post(URL, json=body, headers=headers(key=key)).json()
    assert second["preparation_id"] == first["preparation_id"] and second["replayed"] is True


def test_the_page_viewer_is_403_and_output_paths_are_hidden(db, operators, client, storage) -> None:
    case = open_tender(db, operators)
    add_annex(db, case, operators)
    pid = ready_read(client, db, case)
    path = f"opportunities/{case}/bid/{pid}/paquete.pdf"
    with _owner(db) as conn:
        conn.execute("update evidence.bid_preparation set outputs = %s where id = %s", (
            psycopg.types.json.Jsonb([{"file_key": "paquete", "filename": "paquete.pdf", "kind": "package",
                                       "completar_count": 3, "storage_path": path}]), pid))
    assert client.get(f"/v2/tenders/{case}", headers=headers("viewer")).status_code == 403
    page = client.get(f"/v2/tenders/{case}", headers=headers()).json()
    assert page["preparations"]["read"]["outputs"] == [
        {"file_key": "paquete", "filename": "paquete.pdf", "kind": "package", "completar_count": 3}]
    assert path not in str(page)
    link = client.get(f"/v2/tenders/{case}/preparations/{pid}/files/paquete/url", headers=headers())
    assert link.status_code == 200, link.text
    assert link.json()["expires_in"] == 600 and link.json()["filename"] == "paquete.pdf"
    assert client.get(f"/v2/tenders/{case}/preparations/{pid}/files/paquete/url",
                      headers=headers("viewer")).status_code == 403
    unknown = client.get(f"/v2/tenders/{case}/preparations/{pid}/files/otro/url", headers=headers())
    assert unknown.status_code == 404 and code(unknown) == "file_not_found"
    other = client.get(f"/v2/tenders/{open_tender(db, operators)}/preparations/{pid}/files/paquete/url",
                       headers=headers())
    assert other.status_code == 404


def test_a_sent_offer_passes_and_the_older_approved_quote_is_used_when_the_newest_is_void(db, operators, client, monkeypatch) -> None:
    ensure_bidder(db, operators)
    # An authored `sent` revision needs a sent message or attempt row; stand in for it at the lookup.
    from origenlab_api.v2.tenders import preparation

    sent_case = open_tender(db, operators)
    add_annex(db, sent_case, operators)
    ready_read(client, db, sent_case)
    revision = add_offer(db, operators, sent_case, status="approved")
    monkeypatch.setattr(preparation, "_offer_revision", lambda cur, case_id: (revision, "sent"))
    assert ask(client, sent_case, "fill").status_code == 200
    monkeypatch.undo()

    case = open_tender(db, operators)
    add_annex(db, case, operators)
    ready_read(client, db, case)
    older = add_offer(db, operators, case, status="approved")
    with _owner(db) as conn:
        quote_id = conn.execute("select quote_id from crm.quote_revision where id = %s", (older,)).fetchone()[0]
        newer = conn.execute(
            "insert into crm.quote (opportunity_id, quote_number, created_by_operator_id, created_at) "
            "values (%s, %s, %s, now() + interval '1 minute') returning id",
            (case, f"{next(_SERIALS) + 7000}-26", operators["sales"].operator_id)).fetchone()[0]
        conn.execute("insert into crm.quote_revision (quote_id, revision_no, status, quote_currency, price_decimals) values (%s, 1, 'void', 'CLP', 0)", (newer,))
    ok = ask(client, case, "fill")
    assert ok.status_code == 200, ok.text
    with _owner(db) as conn:
        used = conn.execute("select quote_revision_id::text from evidence.bid_preparation where id = %s",
                            (ok.json()["preparation_id"],)).fetchone()[0]
    assert used == older and quote_id is not None


def test_fill_takes_only_the_annexes_still_live_and_refuses_when_none_are(db, operators, client) -> None:
    ensure_bidder(db, operators)
    case = open_tender(db, operators)
    kept, gone = add_annex(db, case, operators), add_annex(db, case, operators)
    ready_read(client, db, case)
    add_offer(db, operators, case)
    with _owner(db) as conn:
        conn.execute("update crm.opportunity_document set removed_at = now(), removed_by_operator_id = %s, "
                     "remove_reason = 'prueba' where id = %s", (operators["sales"].operator_id, gone))
    ok = ask(client, case, "fill")
    assert ok.status_code == 200, ok.text
    with _owner(db) as conn:
        ids = conn.execute("select annex_document_ids::text[] from evidence.bid_preparation where id = %s",
                           (ok.json()["preparation_id"],)).fetchone()[0]
    assert ids == [kept]


def test_a_foreign_recorded_storage_path_is_never_signed(db, operators, client, storage) -> None:
    case = open_tender(db, operators)
    add_annex(db, case, operators)
    pid = ready_read(client, db, case)
    for bad in ("bidder/" + "a" * 64 + ".pdf", f"opportunities/{case}/bid/{pid}/paquete.pdf/../../x.pdf",
                f"opportunities/{uuid.uuid4()}/bid/{pid}/paquete.pdf"):
        with _owner(db) as conn:
            conn.execute("update evidence.bid_preparation set outputs = %s where id = %s", (
                psycopg.types.json.Jsonb([{"file_key": "paquete", "filename": "sin extension", "storage_path": bad}]),
                pid))
        r = client.get(f"/v2/tenders/{case}/preparations/{pid}/files/paquete/url", headers=headers())
        assert r.status_code == 404 and code(r) == "file_not_found", bad
