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
from origenlab_api.v2.tenders.discovery import load_vocabulary, relevance_of
from origenlab_api.v2.tenders.open_case import V2TenderCommandRepository
from origenlab_api.v2.tenders.reads import TenderReadRepository
from origenlab_api.v2.tenders.routes import tender_command_router, tender_read_router

pytestmark = needs_db
LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"
_SERIALS = itertools.count(1)


@pytest.fixture(autouse=True)
def fresh_vocabulary(monkeypatch):
    """Each test sees the catalog it seeded, not a vocabulary cached by an earlier one."""
    from origenlab_api.v2.tenders import discovery

    monkeypatch.setattr(discovery, "_cached", None)


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
    app.include_router(tender_command_router)
    app.state.v2_tender_read_repository = TenderReadRepository(psycopg.connect, runtime_dsn(db))
    app.state.v2_tender_command_repository = V2TenderCommandRepository(psycopg.connect, runtime_dsn(db))
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
        # As the worker leaves it: a notice whose detail was read carries its match (`relevance`).
        relevance = None
        if head and "Comprador" in head:
            with conn.cursor() as cur:
                relevance = json.dumps(relevance_of(title, head, load_vocabulary(cur), now))
        conn.execute(
            "insert into procurement.notice (codigo_externo, title, buyer_name, closes_at, head, first_seen_at, "
            "last_seen_at, disappeared_at, promoted_opportunity_id, relevance) "
            "values (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s::jsonb)",
            (code, title, "Hospital Ejemplo Sur" if head and "Comprador" in head else None,
             now + closes_in if closes_in is not None else None, json.dumps(head or {"CodigoExterno": code}),
             seen, seen, seen if disappeared else None, promoted_to, relevance))
    return code


def notice_id(db, code: str) -> str:
    with _owner(db) as conn:
        return conn.execute("select id::text from procurement.notice where codigo_externo = %s", (code,)).fetchone()[0]


def review(client, notice: str, verdict: str, *, role: str = "sales", key: str | None = None):
    return client.post("/v2/commands/review-notice", json={"notice_id": notice, "verdict": verdict},
                       headers={OPERATOR_EMAIL_HEADER: f"{role}-buscar@example.test",
                                "Idempotency-Key": key or f"pytest-review-{uuid.uuid4().hex}"})


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


def test_one_notice_page_has_every_date_item_and_fact_and_a_catalog_match(db, client) -> None:
    with _owner(db) as conn:
        maker = conn.execute("insert into crm.organization (kind, name, confirmation) values ('manufacturer', 'Fabricante Ejemplo Termo', "
                             "'confirmed') returning id").fetchone()[0]
        conn.execute("insert into catalog.product (manufacturer_organization_id, model_number, name_es, product_kind) "
                     "values (%s, 'TX900', 'Termociclador de gradiente TX900', 'equipment')", (maker,))
    head = {"Comprador": {"NombreOrganismo": "Hospital Ejemplo Sur", "NombreUnidad": "Laboratorio central",
                          "RegionUnidad": "Región Ejemplo"},
            "Tipo": "LE", "Estado": "Publicada", "Descripcion": "Compra de termociclador para biología molecular",
            "TiempoDuracionContrato": "12", "UnidadTiempoDuracionContrato": 4, "CantidadReclamos": 3,
            "Fechas": {"FechaPublicacion": "2026-10-10T09:00:00", "FechaFinal": "2026-10-14T18:00:00",
                       "FechaEstimadaAdjudicacion": "2026-11-20T00:00:00"},
            "Items": {"Listado": [{"NombreProducto": "Termocicladores", "Descripcion": "TX900 o similar", "Cantidad": 1},
                                  {"NombreProducto": "Mesa antivibración", "Cantidad": 2}]}}
    code = notice(db, "Adquisición de termociclador", head=head)
    listing = discovered(client)
    row = by_code(listing)[code]
    assert row["tier"] == "catalogo" and row["families"] == [{"key": "catalogo", "label": "Otros de tu catálogo"}]
    assert any(t.startswith("termociclador") for t in row["terms"])

    page = client.get(f"/v2/tenders/discovered/{row['id']}", headers={OPERATOR_EMAIL_HEADER: "viewer-buscar@example.test"})
    assert page.status_code == 200, page.text
    body = page.json()
    assert [d["label"] for d in body["dates"]][:2] == ["Publicación", "Preguntas hasta"]
    assert "Cierre de ofertas" in [d["label"] for d in body["dates"]]
    assert [i["name"] for i in body["items"]] == ["Termocicladores", "Mesa antivibración"]
    assert body["items"][0]["terms"] == ["tx900", "termocicladores"]
    facts = {f["label"]: f["value"] for f in body["facts"]}
    assert facts["Duración del contrato"] == "12 meses" and facts["Unidad compradora"] == "Laboratorio central"
    assert facts["Reclamos del comprador"].startswith("3 ")
    assert body["description"].startswith("Compra de termociclador") and body["open"] is True


def test_an_unknown_or_malformed_notice_is_404(db, client) -> None:
    for path in (f"/v2/tenders/discovered/{uuid.uuid4()}", "/v2/tenders/discovered/no-es-un-id"):
        response = client.get(path, headers={OPERATOR_EMAIL_HEADER: "sales-buscar@example.test"})
        assert response.status_code == 404


def test_a_notice_not_matched_yet_shows_on_a_candidate_title_only(db, client) -> None:
    candidate = notice(db, "Adquisición de microcentrífugas")  # list head only: no relevance yet
    other = notice(db, "Servicio de aseo de oficinas")
    rows = by_code(discovered(client))
    assert rows[candidate]["detail"] is False and rows[candidate]["tier"] == "catalogo"
    assert other not in rows


def test_the_stored_match_decides_and_nothing_is_re_read(db, client) -> None:
    code = notice(db, "Adquisición de equipamiento", head={
        "Comprador": {"NombreOrganismo": "Hospital Ejemplo Sur"},
        "Items": {"Listado": [{"CodigoProducto": 41103800, "NombreProducto": "Centrífuga refrigerada", "Cantidad": 1}]}})
    row = by_code(discovered(client))[code]
    assert row["tier"] == "catalogo" and [f["key"] for f in row["families"]] == ["centrifugas"]
    assert row["item_count"] == 1 and row["detail"] is True
    with _owner(db) as conn:  # the worker re-matched it: nothing of ours after all
        conn.execute("update procurement.notice set relevance = relevance || '{\"tier\": null}'::jsonb "
                     "where codigo_externo = %s", (code,))
    assert code not in by_code(discovered(client))


def test_review_notice_records_the_verdict_with_an_event_and_moves_the_notice(db, operators, client) -> None:
    code = notice(db, "Adquisición de balanzas de precisión", head={
        "Comprador": {"NombreOrganismo": "Hospital Ejemplo Sur"},
        "Items": {"Listado": [{"CodigoProducto": 41111500, "NombreProducto": "Balanza de precisión", "Cantidad": 2}]}})
    nid = notice_id(db, code)

    response = review(client, nid, "interesa")
    assert response.status_code == 200, response.text
    assert response.json()["verdict"] == "interesa" and response.json()["previous_verdict"] is None
    listing = discovered(client)
    assert listing["notices"][0]["code"] == code  # «te interesa» first
    assert listing["notices"][0]["review"]["verdict"] == "interesa"
    assert listing["notices"][0]["review"]["by"] == "Operador sales"
    assert review(client, nid, "interesa").status_code == 409  # already so

    assert review(client, nid, "no_es_nuestro").status_code == 200
    listing = discovered(client)
    assert code not in by_code(listing)
    assert code in {n["code"] for n in listing["dismissed"]}
    assert listing["counts"]["descartadas"] >= 1
    with _owner(db) as conn:
        events = conn.execute("select event_type, payload->>'verdict', payload->>'previous_verdict' from crm.domain_event "
                              "where aggregate_kind = 'notice' and aggregate_id = %s::uuid order by recorded_at",
                              (nid,)).fetchall()
        rows = conn.execute("select verdict from crm.notice_review where notice_id = %s::uuid order by reviewed_at",
                            (nid,)).fetchall()
    assert events == [("notice.reviewed", "interesa", None), ("notice.reviewed", "no_es_nuestro", "interesa")]
    assert [r[0] for r in rows] == ["interesa", "no_es_nuestro"]  # append-only: the history stays

    page = client.get(f"/v2/tenders/discovered/{nid}", headers={OPERATOR_EMAIL_HEADER: "sales-buscar@example.test"})
    assert page.json()["review"]["verdict"] == "no_es_nuestro"


def test_a_notice_the_machine_dropped_stays_listed_when_a_person_said_it_interests(db, client) -> None:
    code = notice(db, "Servicio de arriendo de equipos", head={"Comprador": {"NombreOrganismo": "Hospital Ejemplo Sur"},
                                                               "Items": {"Listado": []}})
    assert code not in by_code(discovered(client))
    assert review(client, notice_id(db, code), "interesa").status_code == 200
    assert by_code(discovered(client))[code]["review"]["verdict"] == "interesa"


def test_review_notice_refusals(db, client) -> None:
    code = notice(db, "Centrífuga de mesa")
    nid = notice_id(db, code)
    assert review(client, nid, "interesa", role="viewer").status_code == 403
    assert review(client, str(uuid.uuid4()), "interesa").status_code == 404
    assert review(client, "no-es-un-id", "interesa").status_code == 422
    bad = client.post("/v2/commands/review-notice", json={"notice_id": nid, "verdict": "quizas"},
                      headers={OPERATOR_EMAIL_HEADER: "sales-buscar@example.test", "Idempotency-Key": "pytest-review-bad"})
    assert bad.status_code == 422

