"""`GET /v2/tenders/discovered` («En Mercado Público») against a disposable database, as `origenlab_api`.

The notices are seeded as the worker's sweep leaves them. Every value is invented (public repository):
buyers «Hospital Ejemplo …», codes 87654xx-xx-LE26. One database serves the module, so each test finds
its own rows by code.
"""

from __future__ import annotations

import itertools
import json
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
from origenlab_api.v2.identity import LocalDevIdentity, OperatorIdentity, OperatorLookup
from origenlab_api.v2.tenders.reads import TenderReadRepository
from origenlab_api.v2.tenders.routes import tender_read_router

pytestmark = needs_db
LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"
_SERIALS = itertools.count(1)


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
                    (f"{role}-buscar@example.test", f"Operador {role}", role)).fetchone()[0],
                email_norm=f"{role}-buscar@example.test", display_name=f"Operador {role}", role=role,
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
    app.include_router(tender_read_router)
    app.state.v2_tender_read_repository = TenderReadRepository(psycopg.connect, runtime_dsn(db))
    app.state.v2_identity = LocalDevIdentity(LOOPBACK, _Lookup(operators))
    return TestClient(app)


def _detail(name: str, unspsc: int, description: str = "", amount: int | None = None) -> dict:
    return {"Comprador": {"NombreOrganismo": "Hospital Ejemplo Sur", "RegionUnidad": "Región Ejemplo"},
            "MontoEstimado": amount, "Moneda": "CLP",
            "Items": {"Listado": [{"CodigoProducto": unspsc, "NombreProducto": name, "Descripcion": description,
                                   "Cantidad": 1, "UnidadMedida": "Unidad"}]}}


def notice(db, title: str, *, head: dict | None = None, closes_in: timedelta | None = timedelta(days=5),
           seen_ago: timedelta = timedelta(minutes=5), disappeared: bool = False, promoted_to: str | None = None) -> str:
    code = f"{8765400 + next(_SERIALS)}-{uuid.uuid4().hex[:2].upper()}-LE26"
    now = datetime.now(UTC)
    seen = now - seen_ago
    with _owner(db) as conn:
        conn.execute(
            "insert into procurement.notice (codigo_externo, title, buyer_name, closes_at, head, first_seen_at, "
            "last_seen_at, disappeared_at, promoted_opportunity_id) values (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)",
            (code, title, "Hospital Ejemplo Sur" if head and "Comprador" in head else None,
             now + closes_in if closes_in is not None else None, json.dumps(head or {"CodigoExterno": code}),
             seen, seen, seen if disappeared else None, promoted_to))
    return code


def discovered(client, role: str = "sales") -> dict:
    response = client.get("/v2/tenders/discovered", headers={OPERATOR_EMAIL_HEADER: f"{role}-buscar@example.test"})
    assert response.status_code == 200, response.text
    return response.json()


def by_code(listing: dict) -> dict[str, dict]:
    return {n["code"]: n for n in listing["notices"]}


def test_catalog_matches_come_first_then_lab_equipment_and_services_are_hidden(db, client) -> None:
    lab_soon = notice(db, "Equipos para laboratorio clínico", head=_detail("Incubadoras", 41104900),
                      closes_in=timedelta(days=1))
    balanza = notice(db, "Adquisición de balanzas", head=_detail("Balanza analítica", 41111500, amount=2_500_000),
                     closes_in=timedelta(days=9))
    examenes = notice(db, "Exámenes de laboratorio", head=_detail("Laboratorios biológicos", 85121803))

    listing = discovered(client)
    rows = by_code(listing)
    assert examenes not in rows
    codes = [n["code"] for n in listing["notices"]]
    assert codes.index(balanza) < codes.index(lab_soon)  # the catalog tier first, whatever the closing
    row = rows[balanza]
    assert row["tier"] == "catalogo"
    assert row["families"] == [{"key": "pesaje", "label": "Balanzas y humedad"}]
    assert row["buyer"] == "Hospital Ejemplo Sur" and row["region"] == "Región Ejemplo"
    assert row["estimated_amount"] == {"amount": 2_500_000, "currency": "CLP"}
    assert row["items"][0]["name"] == "Balanza analítica" and row["detail"] is True
    assert row["portal_url"].startswith("https://") and balanza in row["portal_url"]
    assert rows[lab_soon]["tier"] == "laboratorio"
    assert listing["counts"]["catalogo"] >= 1 and listing["counts"]["laboratorio"] >= 1
    assert [f["key"] for f in listing["families"]][:2] == ["pesaje", "centrifugas"]


def test_closed_withdrawn_and_stale_notices_are_not_open(db, client) -> None:
    closed = notice(db, "Centrífuga clínica", closes_in=timedelta(days=-1))
    withdrawn = notice(db, "Centrífuga de mesa", disappeared=True)
    stale = notice(db, "Centrífuga refrigerada", seen_ago=timedelta(days=5))
    no_date = notice(db, "Centrífuga sin fecha", closes_in=None)

    rows = by_code(discovered(client))
    assert closed not in rows and withdrawn not in rows and stale not in rows
    assert rows[no_date]["closes_at"] is None and rows[no_date]["detail"] is False


def test_a_notice_with_an_open_case_carries_it(db, operators, client) -> None:
    with _owner(db) as conn:
        case = conn.execute(
            "insert into crm.opportunity (title, stage, owner_operator_id, kind, tender_status) "
            "values ('Licitación ficticia', 'lead', %s, 'tender', 'preparando') returning id::text",
            (operators["sales"].operator_id,)).fetchone()[0]
    code = notice(db, "Sonicador de laboratorio", promoted_to=case)
    assert by_code(discovered(client))[code]["opportunity_id"] == case


def test_a_viewer_reads_the_list(db, client) -> None:
    code = notice(db, "Osmómetro")
    assert code in by_code(discovered(client, "viewer"))


def test_the_route_does_not_shadow_a_tender_page(db, client) -> None:
    response = client.get(f"/v2/tenders/{uuid.uuid4()}", headers={OPERATOR_EMAIL_HEADER: "sales-buscar@example.test"})
    assert response.status_code == 404
