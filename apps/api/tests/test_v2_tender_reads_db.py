"""«Licitaciones» reads, `set-tender-status` and Hoy's «cierra mañana», against a disposable database.

Runs as `origenlab_api`. Every value is invented (public repository): buyers «Hospital Ejemplo …»,
codes 12345xx-12-LE26, quote numbers 09xxx-26, RUT 61.111.111-8. One database serves the module, so
each test names its own tenders and compares relative to what it created.
"""

from __future__ import annotations

import itertools
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
from origenlab_api.v2.commands import CommandRefused, request_digest
from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository
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
SANTIAGO = ZoneInfo("America/Santiago")
LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"
_SERIALS = itertools.count(1)
_KEYS = itertools.count(1)


@pytest.fixture(scope="module")
def db():
    yield from build_disposable_database()


def _owner(db):
    conn = psycopg.connect(db, autocommit=True)
    conn.execute("set role origenlab_owner")
    return conn


def scalar(db, sql, *args):
    with _owner(db) as conn:
        row = conn.execute(sql, args).fetchone()
    return row[0] if row else None


@pytest.fixture(scope="module")
def operators(db):
    out = {}
    with _owner(db) as conn:
        for role in ("sales", "viewer"):
            out[role] = OperatorIdentity(
                operator_id=conn.execute(
                    "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                    "values (gen_random_uuid(), %s, %s, %s, 'active') returning id::text",
                    (f"{role}-licitaciones@example.test", f"Operador {role}", role)).fetchone()[0],
                email_norm=f"{role}-licitaciones@example.test", display_name=f"Operador {role}", role=role,
                status="active")
    return out


class _Lookup(OperatorLookup):
    def __init__(self, operators) -> None:
        self._by_email = {o.email_norm: o for o in operators.values()}

    def by_email(self, email_norm: str):
        return self._by_email.get(email_norm)


@pytest.fixture
def client(db, operators) -> TestClient:
    app = FastAPI()
    app.include_router(tender_command_router)
    app.include_router(tender_read_router)
    app.state.v2_tender_command_repository = V2TenderCommandRepository(psycopg.connect, runtime_dsn(db))
    app.state.v2_tender_read_repository = TenderReadRepository(psycopg.connect, runtime_dsn(db))
    app.state.v2_identity = LocalDevIdentity(LOOPBACK, _Lookup(operators))
    return TestClient(app)


def headers(role: str = "sales", key: str | None = None) -> dict[str, str]:
    out = {OPERATOR_EMAIL_HEADER: f"{role}-licitaciones@example.test"}
    out["Idempotency-Key"] = key or f"pytest-reads-{next(_KEYS)}"
    return out


def open_tender(db, operators, *, closes_at: datetime | None, head_extra: dict | None = None) -> dict:
    """Open a tender through the real command, then set its closing time (and head) as the notice has it."""
    n = next(_SERIALS)
    tag = uuid.uuid4().hex[:6]
    code = f"{1234500 + n}-12-LE26"
    detail = TenderDetail(
        code=code, title=f"Adquisición de balanzas {tag}", buyer_name=f"Hospital Ejemplo {tag}",
        buyer_code=f"7{n:03d}{tag[:2]}", buyer_rut=None, closes_at=None, published_at=None,
        source_status="Publicada", head={"CodigoExterno": code, "Descripcion": f"Compra de balanzas {tag}",
                                         **(head_extra or {})})
    body = OpenTenderCaseBody(reference=code)
    fields = prepare_open_tender_case(body, ticket="ticket-ficticio", fetch=lambda c, ticket: detail)
    result = V2TenderCommandRepository(psycopg.connect, runtime_dsn(db)).execute(
        command_name=OPEN_TENDER_CASE, operator=operators["sales"], fields=fields,
        idempotency_key=f"pytest-open-{next(_KEYS)}", digest=request_digest(OPEN_TENDER_CASE, body))
    with _owner(db) as conn:
        conn.execute("update procurement.notice set closes_at = %s, head = head || %s::jsonb where id = %s",
                     (closes_at, psycopg.types.json.Jsonb(head_extra or {}), result["notice_id"]))
    return {"id": result["opportunity_id"], "version": result["opportunity_version"], "code": code,
            "notice_id": result["notice_id"], "organization_id": result["organization_id"]}


def sale_case(db, operators, title: str) -> str:
    with _owner(db) as conn:
        return conn.execute(
            "insert into crm.opportunity (title, stage, owner_operator_id) values (%s, 'lead', %s) returning id::text",
            (title, operators["sales"].operator_id)).fetchone()[0]


def set_status(db, tender_id: str, status: str) -> None:
    with _owner(db) as conn:
        conn.execute("update crm.opportunity set tender_status = %s where id = %s", (status, tender_id))


def add_offer(db, operators, tender: dict, *, number: str, status: str, tax_base: float | None,
              lines_total: float | None = None) -> None:
    with _owner(db) as conn:
        quote_id = conn.execute(
            "insert into crm.quote (opportunity_id, quote_number, created_by_operator_id) values (%s, %s, %s) "
            "returning id", (tender["id"], number, operators["sales"].operator_id)).fetchone()[0]
        approved = tax_base is not None
        revision_id = conn.execute(
            "insert into crm.quote_revision (quote_id, revision_no, status, quote_currency, price_decimals, "
            "subtotal, discount_total, tax_base, tax_total, grand_total, totals_computed_at, "
            "approved_at, approved_by_operator_id, party_snapshot, party_snapshot_version) "
            "values (%s, 1, %s, 'CLP', 0, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id",
            (quote_id, status, tax_base, 0 if approved else None, tax_base,
             round(tax_base * 0.19) if approved else None, round(tax_base * 1.19) if approved else None,
             datetime.now(UTC) if approved else None, datetime.now(UTC) if approved else None,
             operators["sales"].operator_id if approved else None,
             psycopg.types.json.Jsonb({}) if approved else None, 1 if approved else None)).fetchone()[0]
        if lines_total is not None:
            conn.execute(
                "insert into crm.quote_line (quote_revision_id, line_no, line_kind, description, quantity, "
                "unit_price_qc, line_total_qc) values (%s, 1, 'item', 'Balanza analítica', 1, %s, %s)",
                (revision_id, lines_total, lines_total))


def row_of(listing: dict, tender: dict) -> dict:
    return next(t for t in listing["tenders"] if t["id"] == tender["id"])


def tomorrow_at(hour: int, days: int = 1) -> datetime:
    day = (datetime.now(SANTIAGO) + timedelta(days=days)).date()
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=SANTIAGO)


# ------------------------------------------------------------------ the list


def test_list_orders_open_first_by_closing_and_counts_every_estado(db, operators, client) -> None:
    before = client.get("/v2/tenders", headers=headers()).json()["counts"]
    late = open_tender(db, operators, closes_at=tomorrow_at(15, 9))
    soon = open_tender(db, operators, closes_at=tomorrow_at(15, 2))
    posted = open_tender(db, operators, closes_at=tomorrow_at(10, 5))
    lost = open_tender(db, operators, closes_at=tomorrow_at(9, 1))
    set_status(db, posted["id"], "postulada")
    set_status(db, lost["id"], "perdida")

    body = client.get("/v2/tenders", headers=headers()).json()
    mine = [t["id"] for t in body["tenders"] if t["id"] in {late["id"], soon["id"], posted["id"], lost["id"]}]
    # Open first, by closing time; the closed one (even though it closes soonest) comes after them all.
    assert mine == [soon["id"], posted["id"], late["id"], lost["id"]]
    after = body["counts"]
    assert set(after) == {"preparando", "postulada", "adjudicada", "perdida"}
    assert after["preparando"] - before["preparando"] == 2
    assert after["postulada"] - before["postulada"] == 1
    assert after["perdida"] - before["perdida"] == 1
    assert after["adjudicada"] == before["adjudicada"]

    row = row_of(body, soon)
    assert row["code"] == soon["code"] and row["status"] == "preparando"
    assert row["institution"]["name"].startswith("Hospital Ejemplo")
    assert row["institution"]["confirmation"] == "machine_proposed"
    assert row["closes_at"] is not None
    assert row["preparation"] == {"read": None, "fill": None}
    assert row["offer_quote_number"] is None

    only = client.get("/v2/tenders", params={"status": "postulada"}, headers=headers()).json()
    assert [t["id"] for t in only["tenders"] if t["id"] in {late["id"], posted["id"]}] == [posted["id"]]
    assert all(t["status"] == "postulada" for t in only["tenders"])
    assert only["counts"] == after  # the counts are for every estado, whatever the filter
    assert client.get("/v2/tenders", params={"status": "otra"}, headers=headers()).status_code == 422


def test_amount_is_the_offer_neto_else_the_estimate_else_nothing(db, operators, client) -> None:
    estimated = open_tender(db, operators, closes_at=tomorrow_at(12, 3), head_extra={"MontoEstimado": 1500000})
    offered = open_tender(db, operators, closes_at=tomorrow_at(12, 3), head_extra={"MontoEstimado": 1500000})
    drafted = open_tender(db, operators, closes_at=tomorrow_at(12, 3), head_extra={"MontoEstimado": 900000})
    bare = open_tender(db, operators, closes_at=tomorrow_at(12, 3))
    add_offer(db, operators, offered, number="09001-26", status="approved", tax_base=1200000)
    add_offer(db, operators, drafted, number="09002-26", status="draft", tax_base=None, lines_total=800000)

    body = client.get("/v2/tenders", headers=headers("viewer")).json()
    assert row_of(body, estimated)["amount"] == {"neto": 1500000, "currency": "CLP", "source": "estimado"}
    assert row_of(body, estimated)["offer_quote_number"] is None
    o = row_of(body, offered)
    assert o["amount"] == {"neto": 1200000, "currency": "CLP", "source": "oferta"}
    assert o["offer_quote_number"] == "09001-26"
    # A draft has no frozen totals yet: its neto is what its lines add up to, and it still beats the estimate.
    assert row_of(body, drafted)["amount"] == {"neto": 800000, "currency": "CLP", "source": "oferta"}
    assert row_of(body, bare)["amount"] is None


def test_the_list_is_readable_by_a_viewer_and_holds_no_bidder_field(db, operators, client) -> None:
    tender = open_tender(db, operators, closes_at=tomorrow_at(12, 3))
    response = client.get("/v2/tenders", headers=headers("viewer"))
    assert response.status_code == 200
    assert set(row_of(response.json(), tender)) == {
        "id", "version", "code", "title", "institution", "closes_at", "status", "amount",
        "offer_quote_number", "preparation"}
    assert client.get("/v2/tenders").status_code == 401


# ------------------------------------------------------------------ the page


def test_page_for_sales_carries_the_offer_annexes_spend_and_readiness(db, operators, client) -> None:
    tender = open_tender(db, operators, closes_at=tomorrow_at(12, 3),
                         head_extra={"MontoEstimado": 1500000, "Moneda": "CLP"})
    add_offer(db, operators, tender, number="09003-26", status="approved", tax_base=1000000)
    response = client.get(f"/v2/tenders/{tender['id']}", headers=headers("sales"))
    assert response.status_code == 200
    page = response.json()
    assert page["tender"]["code"] == tender["code"]
    assert page["tender"]["portal_url"].endswith(tender["code"]) or tender["code"] in page["tender"]["portal_url"]
    assert page["tender"]["notice_head_summary"]["estimated_amount"] == 1500000
    assert page["tender"]["notice_head_summary"]["description"].startswith("Compra de balanzas")
    assert page["annexes"] == []
    assert page["preparations"] == {"read": None, "fill": None}
    assert page["offer_quote"]["number"] == "09003-26" and page["offer_quote"]["status"] == "approved"
    assert (page["offer_quote"]["neto"], page["offer_quote"]["iva"], page["offer_quote"]["total"]) == (
        1000000, 190000, 1190000)
    assert page["spend"]["reached"] is False and page["spend"]["cap_usd"] == "60"
    assert page["bidder_ready"] is False


def test_page_lists_annexes_and_the_latest_preparation_of_each_phase(db, operators, client) -> None:
    tender = open_tender(db, operators, closes_at=tomorrow_at(12, 3))
    sha = "ab" * 32
    with _owner(db) as conn:
        doc = conn.execute(
            "insert into crm.opportunity_document (opportunity_id, kind, storage_path, sha256, content_type, "
            "byte_size, original_filename, source, attached_by_operator_id) values "
            "(%s, 'tender_annex', %s, %s, 'application/pdf', 1000, 'bases.pdf', 'upload', %s) returning id::text",
            (tender["id"], f"opportunities/{tender['id']}/{sha}.pdf", sha, operators["sales"].operator_id)
        ).fetchone()[0]
        conn.execute(
            "insert into evidence.bid_preparation (opportunity_id, phase, status, annex_document_ids, "
            "requested_by_operator_id, result) values (%s, 'read', 'failed', %s, %s, '{}'::jsonb)",
            (tender["id"], [doc], operators["sales"].operator_id))
        newest = conn.execute(
            "insert into evidence.bid_preparation (opportunity_id, phase, status, annex_document_ids, "
            "requested_by_operator_id, result) values (%s, 'read', 'ready', %s, %s, %s::jsonb) returning id::text",
            (tender["id"], [doc], operators["sales"].operator_id, '{"summary": "Resumen ficticio"}')).fetchone()[0]
    page = client.get(f"/v2/tenders/{tender['id']}", headers=headers()).json()
    assert [(a["id"], a["filename"]) for a in page["annexes"]] == [(doc, "bases.pdf")]
    assert page["preparations"]["fill"] is None
    assert page["preparations"]["read"]["id"] == newest and page["preparations"]["read"]["status"] == "ready"
    assert page["preparations"]["read"]["result"] == {"summary": "Resumen ficticio"}
    assert page["tender"]["preparation"] == {"read": "ready", "fill": None}
    listed = row_of(client.get("/v2/tenders", headers=headers()).json(), tender)
    assert listed["preparation"] == {"read": "ready", "fill": None}


def test_page_is_refused_to_a_viewer_before_anything_is_read(db, operators, client) -> None:
    tender = open_tender(db, operators, closes_at=tomorrow_at(12, 3))
    response = client.get(f"/v2/tenders/{tender['id']}", headers=headers("viewer"))
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "role_may_not_view_bidder"
    # Not even the existence of an id is told to a viewer.
    assert client.get(f"/v2/tenders/{uuid.uuid4()}", headers=headers("viewer")).status_code == 403


def test_a_sale_case_or_an_unknown_id_is_not_a_tender(db, operators, client) -> None:
    sale_id = sale_case(db, operators, "Venta ficticia")
    for tender_id in (sale_id, str(uuid.uuid4()), "no-es-un-uuid"):
        response = client.get(f"/v2/tenders/{tender_id}", headers=headers())
        assert response.status_code == 404, tender_id
        assert response.json()["detail"]["code"] == "tender_not_found"


# ------------------------------------------------------------------ set-tender-status


def post_status(client, tender, status, *, version=None, reason=None, role="sales", key=None):
    body = {"opportunity_id": tender["id"], "status": status,
            "expected_version": version if version is not None else tender["version"]}
    if reason is not None:
        body["reason"] = reason
    return client.post("/v2/commands/set-tender-status", json=body, headers=headers(role, key))


def test_set_status_records_the_change_the_event_and_a_new_version(db, operators, client) -> None:
    tender = open_tender(db, operators, closes_at=tomorrow_at(12, 3))
    response = post_status(client, tender, "postulada")
    assert response.status_code == 200, response.text
    out = response.json()
    assert (out["tender_status"], out["previous_status"], out["opportunity_version"]) == (
        "postulada", "preparando", tender["version"] + 1)
    assert scalar(db, "select tender_status from crm.opportunity where id = %s", tender["id"]) == "postulada"
    assert scalar(db, "select payload->>'to_status' from crm.domain_event where event_type = "
                      "'opportunity.tender_status_set' and aggregate_id = %s", tender["id"]) == "postulada"
    # The case itself is not closed by an estado: the stage machine is left alone.
    assert scalar(db, "select stage from crm.opportunity where id = %s", tender["id"]) == "lead"

    lost = post_status(client, {**tender, "version": out["opportunity_version"]}, "perdida",
                       reason="Precio superior al adjudicado")
    assert lost.status_code == 200 and lost.json()["tender_status"] == "perdida"
    assert scalar(db, "select payload->>'reason' from crm.domain_event where event_type = "
                      "'opportunity.tender_status_set' and aggregate_id = %s and payload->>'to_status' = 'perdida'",
                  tender["id"]) == "Precio superior al adjudicado"


def test_set_status_refusals(db, operators, client) -> None:
    tender = open_tender(db, operators, closes_at=tomorrow_at(12, 3))
    award = post_status(client, tender, "adjudicada")
    assert (award.status_code, award.json()["detail"]["code"]) == (422, "award_is_phase_3")
    no_reason = post_status(client, tender, "perdida")
    assert (no_reason.status_code, no_reason.json()["detail"]["code"]) == (422, "reason_required")
    blank = post_status(client, tender, "perdida", reason="   ")
    assert (blank.status_code, blank.json()["detail"]["code"]) == (422, "reason_required")
    same = post_status(client, tender, "preparando")
    assert (same.status_code, same.json()["detail"]["code"]) == (409, "tender_already_at_status")
    stale = post_status(client, tender, "postulada", version=tender["version"] + 5)
    assert (stale.status_code, stale.json()["detail"]["code"]) == (409, "case_version_conflict")
    viewer = post_status(client, tender, "postulada", role="viewer")
    assert (viewer.status_code, viewer.json()["detail"]["code"]) == (403, "role_may_not_decide")
    assert client.post("/v2/commands/set-tender-status", headers={OPERATOR_EMAIL_HEADER: headers()[OPERATOR_EMAIL_HEADER]},
                       json={"opportunity_id": tender["id"], "status": "postulada", "expected_version": 1}
                       ).status_code == 400
    # Every refusal left the tender untouched.
    assert scalar(db, "select tender_status || version from crm.opportunity where id = %s",
                  tender["id"]) == f"preparando{tender['version']}"
    sale_id = sale_case(db, operators, "Venta ficticia 2")
    not_tender = post_status(client, {"id": sale_id, "version": 1}, "postulada")
    assert (not_tender.status_code, not_tender.json()["detail"]["code"]) == (404, "tender_not_found")
    assert scalar(db, "select count(*) from crm.domain_event where aggregate_id = %s", sale_id) == 0


def test_the_same_key_replays_and_a_database_level_adjudicada_is_refused(db, operators, client) -> None:
    tender = open_tender(db, operators, closes_at=tomorrow_at(12, 3))
    first = post_status(client, tender, "postulada", key="pytest-status-replay-1")
    again = post_status(client, tender, "postulada", key="pytest-status-replay-1")
    assert first.status_code == again.status_code == 200 and again.json()["replayed"] is True
    from origenlab_api.v2.tenders.status import SET_TENDER_STATUS

    repo = V2TenderCommandRepository(psycopg.connect, runtime_dsn(db))
    set_status(db, tender["id"], "adjudicada")
    with pytest.raises(CommandRefused) as err:  # the handler alone also refuses a final tender
        repo.execute(command_name=SET_TENDER_STATUS, operator=operators["sales"],
                     fields={"opportunity_id": tender["id"], "status": "perdida", "reason": "x",
                             "expected_version": first.json()["opportunity_version"]},
                     idempotency_key=f"pytest-status-final-{next(_KEYS)}", digest="d" * 64)
    assert err.value.code == "tender_status_is_final"


# ------------------------------------------------------------------ Hoy: «cierra mañana»


def closing(db) -> list[dict]:
    return CrmWorkspaceRepository(psycopg.connect, runtime_dsn(db)).pipeline()["tenders_closing"]


def test_tenders_closing_holds_tomorrow_only_and_only_open_ones(db, operators) -> None:
    tomorrow = open_tender(db, operators, closes_at=tomorrow_at(15))
    late_night = open_tender(db, operators, closes_at=tomorrow_at(23))
    in_three_days = open_tender(db, operators, closes_at=tomorrow_at(15, 3))
    today = open_tender(db, operators, closes_at=tomorrow_at(15, 0))
    posted = open_tender(db, operators, closes_at=tomorrow_at(8))
    lost = open_tender(db, operators, closes_at=tomorrow_at(8))
    awarded = open_tender(db, operators, closes_at=tomorrow_at(8))
    no_close = open_tender(db, operators, closes_at=None)
    set_status(db, posted["id"], "postulada")
    set_status(db, lost["id"], "perdida")
    set_status(db, awarded["id"], "adjudicada")

    signal = closing(db)
    ids = [s["id"] for s in signal]
    for expected in (tomorrow, late_night, posted):
        assert expected["id"] in ids
    for excluded in (in_three_days, today, lost, awarded, no_close):
        assert excluded["id"] not in ids
    entry = next(s for s in signal if s["id"] == tomorrow["id"])
    assert set(entry) == {"id", "code", "title", "closes_at"} and entry["code"] == tomorrow["code"]
    # Ordered by closing time: the 08:00 one before the 15:00 one before the 23:00 one.
    assert ids.index(posted["id"]) < ids.index(tomorrow["id"]) < ids.index(late_night["id"])


def test_a_sale_case_never_reaches_the_closing_signal_and_the_cards_stay_sales(db, operators) -> None:
    tender = open_tender(db, operators, closes_at=tomorrow_at(15))
    pipeline = CrmWorkspaceRepository(psycopg.connect, runtime_dsn(db)).pipeline()
    assert tender["id"] in [s["id"] for s in pipeline["tenders_closing"]]
    sale_id = sale_case(db, operators, "Venta ficticia del tablero")
    cards = CrmWorkspaceRepository(psycopg.connect, runtime_dsn(db)).pipeline()["items"]
    ids = [card["opportunity_id"] for card in cards]
    assert sale_id in ids and tender["id"] not in ids
