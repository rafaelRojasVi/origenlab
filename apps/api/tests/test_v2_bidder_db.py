"""Bidder data against a disposable database: the profile, its documents, the events and the viewer gate.

Runs as `origenlab_api`. Every value is invented (public repository): «Oferente Ejemplo», RUT
11.111.111-1 and 61.111.111-8, a fake bucket. One database serves the module and the bidder is a
single global row set, so each test reads the current profile before it saves.
"""

from __future__ import annotations

import itertools
import json
import uuid

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
from origenlab_api.v2.catalog.storage import FakeStorage
from origenlab_api.v2.identity import LocalDevIdentity, OperatorIdentity, OperatorLookup
from origenlab_api.v2.tenders.bidder import current_bidder_profile, live_bidder_documents
from origenlab_api.v2.tenders.open_case import V2TenderCommandRepository
from origenlab_api.v2.tenders.reads import TenderReadRepository
from origenlab_api.v2.tenders.routes import bidder_read_router, tender_command_router, tender_read_router

pytestmark = needs_db
LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"
_KEYS = itertools.count(1)
PDF = b"%PDF-1.7 documento de ejemplo "


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
        for role in ("sales", "admin", "viewer"):
            out[role] = OperatorIdentity(
                operator_id=conn.execute(
                    "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                    "values (gen_random_uuid(), %s, %s, %s, 'active') returning id::text",
                    (f"{role}-oferente@example.test", f"Operador {role}", role)).fetchone()[0],
                email_norm=f"{role}-oferente@example.test", display_name=f"Operador {role}", role=role,
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
    for router in (tender_command_router, tender_read_router, bidder_read_router):
        app.include_router(router)
    app.state.v2_tender_command_repository = V2TenderCommandRepository(psycopg.connect, runtime_dsn(db))
    app.state.v2_tender_read_repository = TenderReadRepository(psycopg.connect, runtime_dsn(db))
    app.state.case_document_storage = storage
    app.state.v2_identity = LocalDevIdentity(LOOPBACK, _Lookup(operators))
    return TestClient(app)


def headers(role: str = "sales", key: str | None = None) -> dict[str, str]:
    return {OPERATOR_EMAIL_HEADER: f"{role}-oferente@example.test",
            "Idempotency-Key": key or f"pytest-bidder-{next(_KEYS)}"}


def save(client, role="sales", **over):
    current = client.get("/v2/bidder", headers=headers("sales")).json()["profile"]
    body = {"bidder_kind": "natural_person", "legal_name": "Oferente Ejemplo", "rut": "11.111.111-1",
            "reason": "Carga de prueba", "expected_current_id": current["id"] if current else None, **over}
    return client.post("/v2/commands/save-bidder-profile", json=body, headers=headers(role))


def add_document(client, data=PDF, *, kind="cedula", title="Cédula de identidad", name="cedula.pdf", role="sales"):
    return client.post("/v2/commands/add-bidder-document", data={"kind": kind, "title": title},
                       files={"file": (name, data, "application/octet-stream")}, headers=headers(role))


def events(db, aggregate_id: str) -> list[tuple[str, dict]]:
    with _owner(db) as conn:
        return conn.execute(
            "select event_type, payload from crm.domain_event where aggregate_kind = 'bidder' "
            "and aggregate_id = %s order by seq", (aggregate_id,)).fetchall()


# ------------------------------------------------------------------ the profile


def test_save_and_read_back_normalises_the_rut_and_keeps_history(db, client) -> None:
    saved = save(client, giro="Venta de equipos", comuna="Santiago", is_emt=True)
    assert saved.status_code == 200, saved.text
    profile_id = saved.json()["profile_id"]

    view = client.get("/v2/bidder", headers=headers()).json()
    profile = view["profile"]
    assert profile["id"] == profile_id
    assert (profile["legal_name"], profile["rut"], profile["bidder_kind"]) == ("Oferente Ejemplo", "11111111-1",
                                                                              "natural_person")
    assert profile["giro"] == "Venta de equipos" and profile["is_emt"] is True and profile["is_local_supplier"] is False
    assert profile["set_by"] == "Operador sales" and "set_by_operator_id" not in profile
    assert view["history"][0] == {"id": profile_id, "set_by": "Operador sales", "reason": "Carga de prueba",
                                  "created_at": view["history"][0]["created_at"]}
    with psycopg.connect(runtime_dsn(db)) as conn, conn.cursor() as cur:
        assert current_bidder_profile(cur)["id"] == profile_id


def test_a_second_save_is_a_new_row_and_an_admin_may_save(db, client) -> None:
    first = save(client).json()["profile_id"]
    second = save(client, role="admin", phone="+56 9 0000 0000", reason="Teléfono").json()["profile_id"]
    assert second != first
    history = client.get("/v2/bidder", headers=headers()).json()["history"]
    ids = [h["id"] for h in history]
    assert ids.index(second) < ids.index(first)
    assert history[ids.index(second)]["set_by"] == "Operador admin"


def test_a_stale_expected_current_id_is_409_and_a_wrong_first_save_too(db, client) -> None:
    first = save(client).json()["profile_id"]
    save(client, phone="111", reason="Cambio posterior")
    stale = client.post("/v2/commands/save-bidder-profile", headers=headers(), json={
        "bidder_kind": "natural_person", "legal_name": "Oferente Ejemplo", "rut": "11.111.111-1",
        "reason": "Desde una vista vieja", "expected_current_id": first})
    assert (stale.status_code, stale.json()["detail"]["code"]) == (409, "bidder_profile_changed")
    blind = client.post("/v2/commands/save-bidder-profile", headers=headers(), json={
        "bidder_kind": "natural_person", "legal_name": "Oferente Ejemplo", "rut": "11.111.111-1",
        "reason": "Sin haber visto nada"})
    assert (blind.status_code, blind.json()["detail"]["code"]) == (409, "bidder_profile_changed")


def test_company_without_a_rep_is_allowed_a_natural_person_with_one_is_not(client) -> None:
    ok = save(client, bidder_kind="company", legal_name="Empresa Ejemplo SpA", rut="61.111.111-8")
    assert ok.status_code == 200, ok.text
    with_rep = save(client, bidder_kind="company", legal_rep_name="Rep Ejemplo", legal_rep_rut="11.111.111-1",
                    legal_name="Empresa Ejemplo SpA", rut="61.111.111-8")
    assert with_rep.status_code == 200
    assert client.get("/v2/bidder", headers=headers()).json()["profile"]["legal_rep_rut"] == "11111111-1"
    natural = save(client, legal_rep_name="Rep Ejemplo")
    assert (natural.status_code, natural.json()["detail"]["code"]) == (422, "legal_rep_only_for_company")
    bad = save(client, rut="11.111.111-2")
    assert (bad.status_code, bad.json()["detail"]["code"]) == (422, "rut_invalid")
    save(client)  # leave a natural-person profile behind


def test_the_event_names_the_changed_fields_and_never_a_value(db, client) -> None:
    first = save(client, rut="11.111.111-1", bank_account_number="000123456789", address="Calle Ejemplo 123").json()
    second = save(client, bank_account_number="000123456780", address="Calle Ejemplo 123",
                  reason="Cuenta corregida").json()["profile_id"]
    (event_type, payload), = events(db, second)
    assert event_type == "bidder.profile_saved"
    assert payload["changed_fields"] == ["bank_account_number"]
    assert payload["previous_profile_id"] == first["profile_id"]
    dump = json.dumps(payload)
    for secret in ("11111111", "000123456780", "Calle Ejemplo", "Oferente Ejemplo"):
        assert secret not in dump
    (_, first_payload), = events(db, first["profile_id"])
    assert {"bank_account_number", "address"} <= set(first_payload["changed_fields"])
    assert "11111111" not in json.dumps(first_payload) and "000123456789" not in json.dumps(first_payload)


def test_a_replayed_save_is_not_saved_twice(client) -> None:
    key = f"pytest-replay-{uuid.uuid4().hex}"
    current = client.get("/v2/bidder", headers=headers()).json()["profile"]
    body = {"bidder_kind": "natural_person", "legal_name": "Oferente Ejemplo", "rut": "11.111.111-1",
            "reason": "Una sola vez", "phone": uuid.uuid4().hex[:8], "expected_current_id": current["id"]}
    one = client.post("/v2/commands/save-bidder-profile", json=body, headers=headers(key=key)).json()
    two = client.post("/v2/commands/save-bidder-profile", json=body, headers=headers(key=key)).json()
    assert two["profile_id"] == one["profile_id"] and two["replayed"] is True


# ------------------------------------------------------------------ the documents


def test_add_a_document_then_retire_it_twice(db, client, storage) -> None:
    data = PDF + uuid.uuid4().hex.encode()
    added = add_document(client, data)
    assert added.status_code == 200, added.text
    document_id = added.json()["document_id"]
    assert [p for p in storage.objects if p.startswith("bidder/") and p.endswith(".pdf")]
    assert storage.objects[next(iter(storage.objects))][1] == "application/pdf"

    documents = client.get("/v2/bidder", headers=headers()).json()["documents"]
    mine = next(d for d in documents if d["id"] == document_id)
    assert (mine["kind"], mine["title"], mine["original_filename"], mine["content_type"]) == (
        "cedula", "Cédula de identidad", "cedula.pdf", "application/pdf")
    assert "storage_path" not in mine and mine["byte_size"] == len(data)

    url = client.get(f"/v2/bidder/documents/{document_id}/url", headers=headers())
    assert url.status_code == 200 and url.headers["cache-control"] == "no-store"
    assert url.json()["expires_in"] == 600 and "bidder/" in url.json()["url"]

    retire = {"document_id": document_id, "reason": "Documento vencido"}
    first = client.post("/v2/commands/retire-bidder-document", json=retire, headers=headers())
    assert first.status_code == 200, first.text
    second = client.post("/v2/commands/retire-bidder-document", json=retire, headers=headers())
    assert (second.status_code, second.json()["detail"]["code"]) == (409, "document_already_retired")
    assert document_id not in [d["id"] for d in client.get("/v2/bidder", headers=headers()).json()["documents"]]
    assert client.get(f"/v2/bidder/documents/{document_id}/url", headers=headers()).status_code == 404
    kinds = [e[0] for e in events(db, document_id)]
    assert kinds == ["bidder.document_added", "bidder.document_retired"]
    with psycopg.connect(runtime_dsn(db)) as conn, conn.cursor() as cur:
        assert document_id not in [d["id"] for d in live_bidder_documents(cur)]


def test_the_same_bytes_cannot_be_added_twice_but_can_after_retirement(client) -> None:
    data = PDF + uuid.uuid4().hex.encode()
    first = add_document(client, data).json()["document_id"]
    again = add_document(client, data)
    assert (again.status_code, again.json()["detail"]["code"]) == (409, "bidder_document_already_added")
    client.post("/v2/commands/retire-bidder-document", json={"document_id": first, "reason": "Se reemplaza"},
                headers=headers())
    assert add_document(client, data).status_code == 200


def test_unknown_and_malformed_documents(client) -> None:
    missing = client.post("/v2/commands/retire-bidder-document", headers=headers(),
                          json={"document_id": str(uuid.uuid4()), "reason": "No existe"})
    assert (missing.status_code, missing.json()["detail"]["code"]) == (404, "document_not_found")
    assert client.get(f"/v2/bidder/documents/{uuid.uuid4()}/url", headers=headers()).status_code == 404
    assert client.get("/v2/bidder/documents/not-a-uuid/url", headers=headers()).status_code == 404
    text = add_document(client, b"hola", name="nota.txt")
    assert (text.status_code, text.json()["detail"]["code"]) == (415, "unsupported_document")


# ------------------------------------------------------------------ the viewer gate


def test_a_viewer_is_refused_with_the_data_in_place(db, client, storage) -> None:
    save(client)
    document_id = add_document(client, PDF + uuid.uuid4().hex.encode()).json()["document_id"]
    puts = storage.puts
    for response in (
        client.get("/v2/bidder", headers=headers("viewer")),
        client.get(f"/v2/bidder/documents/{document_id}/url", headers=headers("viewer")),
        save(client, role="viewer"),
        add_document(client, PDF + b"otro", role="viewer"),
        client.post("/v2/commands/retire-bidder-document", headers=headers("viewer"),
                    json={"document_id": document_id, "reason": "No debería poder"}),
    ):
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["code"] == "role_may_not_view_bidder"
        assert "Oferente Ejemplo" not in response.text and "11111111" not in response.text
    assert storage.puts == puts
    with _owner(db) as conn:
        assert conn.execute("select retired_at from crm.bidder_document where id = %s", (document_id,)).fetchone()[0] is None
