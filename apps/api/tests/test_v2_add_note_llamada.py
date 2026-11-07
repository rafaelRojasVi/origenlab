"""add-note as a typed «Llamada»: the outcome and next step are `crm.note` columns.

The body shape is checked without a database (a bad request is a 422 before it reaches Postgres);
the stored row, the event and the three note read models are proven against the disposable
database as the real `origenlab_api` role. Every name is invented.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from origenlab_api.v2.call_outcome import CALL_OUTCOMES, NOTE_KIND_LLAMADA
from origenlab_api.v2.commands import request_digest
from origenlab_api.v2.crm_authoring import V2CrmAuthoringRepository
from origenlab_api.v2.crm_authoring_routes import AddNoteBody, crm_authoring_router
from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository
from origenlab_api.v2.identity import OperatorIdentity
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

SUBJECT = "00000000-0000-4000-8000-0000000000aa"


def _body(**over) -> dict:
    return {"subject_kind": "opportunity", "subject_id": SUBJECT, "body": "Hablé con el laboratorio.", **over}


# ───────────────────────────────────────────────────────────── the five outcomes ──


def test_the_five_outcomes_are_defined_once() -> None:
    assert CALL_OUTCOMES == ("contesto", "no_contesto", "buzon", "numero_malo", "reunion")
    assert NOTE_KIND_LLAMADA == "llamada"


def test_dashboard_names_the_same_outcomes_as_the_api() -> None:
    """`callOutcome.ts` repeats these five values and the 500-character limit (it cannot import them)."""
    import re
    from pathlib import Path

    from origenlab_api.v2.call_outcome import CALL_NEXT_STEP_MAX

    ts = (Path(__file__).resolve().parents[2] / "dashboard/src/crm/callOutcome.ts").read_text(encoding="utf-8")
    block = re.search(r"export const CALL_OUTCOMES = \[(.*?)\] as const;", ts, re.S)
    assert block is not None
    assert re.findall(r'"([a-z_]+)"', block.group(1)) == list(CALL_OUTCOMES)
    limit = re.search(r"export const CALL_NEXT_STEP_MAX = (\d+);", ts)
    assert limit is not None and int(limit.group(1)) == CALL_NEXT_STEP_MAX


# ─────────────────────────────────────────────────────────────── body validation ──


@pytest.mark.parametrize("outcome", CALL_OUTCOMES)
def test_every_outcome_makes_a_valid_llamada(outcome: str) -> None:
    body = AddNoteBody(**_body(note_kind="llamada", call_outcome=outcome))
    assert (body.note_kind, body.call_outcome, body.call_next_step) == ("llamada", outcome, None)


def test_a_llamada_carries_its_next_step() -> None:
    body = AddNoteBody(**_body(note_kind="llamada", call_outcome="contesto",
                               call_next_step="Llamar la semana que viene"))
    assert body.call_next_step == "Llamar la semana que viene"


@pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
def test_a_blank_next_step_is_none(blank: str) -> None:
    body = AddNoteBody(**_body(note_kind="llamada", call_outcome="buzon", call_next_step=blank))
    assert body.call_next_step is None


def test_an_ordinary_note_has_no_call_fields() -> None:
    body = AddNoteBody(**_body())
    assert (body.note_kind, body.call_outcome, body.call_next_step) == (None, None, None)


def test_an_outcome_without_a_kind_is_refused() -> None:
    with pytest.raises(ValidationError):
        AddNoteBody(**_body(call_outcome="contesto"))


def test_a_next_step_without_a_kind_is_refused() -> None:
    with pytest.raises(ValidationError):
        AddNoteBody(**_body(call_next_step="Llamar"))


def test_a_kind_without_an_outcome_is_refused() -> None:
    with pytest.raises(ValidationError):
        AddNoteBody(**_body(note_kind="llamada"))


def test_an_unknown_outcome_or_kind_is_refused() -> None:
    with pytest.raises(ValidationError):
        AddNoteBody(**_body(note_kind="llamada", call_outcome="colgó"))
    with pytest.raises(ValidationError):
        AddNoteBody(**_body(note_kind="visita", call_outcome="contesto"))


def test_a_next_step_is_at_most_500_characters() -> None:
    AddNoteBody(**_body(note_kind="llamada", call_outcome="contesto", call_next_step="x" * 500))
    with pytest.raises(ValidationError):
        AddNoteBody(**_body(note_kind="llamada", call_outcome="contesto", call_next_step="x" * 501))


class _Stub:
    """Stands in for the repository: the route's job is validation and hand-over."""

    def __init__(self) -> None:
        self.fields: dict | None = None

    def execute(self, **kwargs) -> dict:
        self.fields = kwargs["fields"]
        return {"ok": True, "note_id": str(uuid.uuid4()), "version": 1}


def _client() -> tuple[_Stub, TestClient]:
    from origenlab_api.v2 import command_routes

    stub = _Stub()
    app = FastAPI()
    app.include_router(crm_authoring_router)
    app.state.crm_authoring_repository = stub
    operator = OperatorIdentity(operator_id="00000000-0000-4000-8000-000000000001", email_norm="op@example.test",
                                display_name="Op", role="sales", status="active")
    app.dependency_overrides[command_routes.deciding_operator] = lambda: operator
    return stub, TestClient(app)


def _post(client: TestClient, body: dict):
    return client.post("/v2/commands/add-note", json=body, headers={"Idempotency-Key": uuid.uuid4().hex})


@pytest.mark.parametrize("bad", [
    {"call_outcome": "contesto"},
    {"note_kind": "llamada"},
    {"note_kind": "llamada", "call_outcome": "colgó"},
    {"note_kind": "llamada", "call_outcome": "contesto", "call_next_step": "x" * 501},
])
def test_the_route_answers_422_before_the_database(bad: dict) -> None:
    stub, client = _client()
    assert _post(client, _body(**bad)).status_code == 422
    assert stub.fields is None


def test_the_route_hands_the_call_fields_to_the_repository() -> None:
    stub, client = _client()
    r = _post(client, _body(note_kind="llamada", call_outcome="reunion", call_next_step="  Visitar el lunes "))
    assert r.status_code == 200, r.text
    assert stub.fields is not None
    assert (stub.fields["note_kind"], stub.fields["call_outcome"], stub.fields["call_next_step"]) == (
        "llamada", "reunion", "Visitar el lunes")


# ───────────────────────────────────────────────────────────────── the database ──


@pytest.fixture(scope="module")
def db():
    yield from build_disposable_database()


@pytest.fixture(scope="module")
def operator(db) -> OperatorIdentity:
    tag = uuid.uuid4().hex[:8]
    with psycopg.connect(db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute("insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                    "values (gen_random_uuid(), %s, 'Vendedora Ficticia', 'sales', 'active') returning id::text",
                    (f"llamada-{tag}@example.invalid",))
        op_id = cur.fetchone()[0]
    return OperatorIdentity(operator_id=op_id, email_norm=f"llamada-{tag}@example.invalid",
                            display_name="Vendedora Ficticia", role="sales", status="active")


def _owner(dsn: str, sql: str, params: tuple = ()):
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


def _person(dsn: str) -> str:
    return _owner(dsn, "insert into crm.person (display_name, confirmation) "
                       "values ('Persona Ficticia', 'machine_proposed') returning id::text")[0][0]


def _org(dsn: str) -> str:
    return _owner(dsn, "insert into crm.organization (kind, name, confirmation) "
                       "values ('unknown', 'Instituto Ficticio', 'machine_proposed') returning id::text")[0][0]


def _case(dsn: str, operator: OperatorIdentity) -> str:
    return _owner(dsn, "insert into crm.opportunity (title, stage, owner_operator_id) "
                       "values ('Equipo de laboratorio', 'lead', %s) returning id::text", (operator.operator_id,))[0][0]


def _add(dsn: str, operator: OperatorIdentity, **raw) -> dict:
    body = AddNoteBody(**raw)
    return V2CrmAuthoringRepository(psycopg.connect, runtime_dsn(dsn)).execute(
        command_name="add-note", operator=operator, fields=body.model_dump(),
        idempotency_key=uuid.uuid4().hex, digest=request_digest("add-note", body))


def _row(dsn: str, note_id: str) -> tuple:
    return _owner(dsn, "select note_kind, call_outcome, call_next_step, body from crm.note where id = %s",
                  (note_id,))[0]


def _events(dsn: str, note_id: str) -> list[tuple[str, dict]]:
    return _owner(dsn, "select event_type, payload from crm.domain_event "
                       "where aggregate_kind = 'note' and aggregate_id = %s order by seq", (note_id,))


@needs_db
def test_a_llamada_is_stored_in_columns_and_the_event_carries_them(db, operator) -> None:
    pid = _person(db)
    out = _add(db, operator, subject_kind="person", subject_id=pid, body="Hablé con ella.",
               note_kind="llamada", call_outcome="contesto", call_next_step="Llamar la semana que viene")
    assert _row(db, out["note_id"]) == ("llamada", "contesto", "Llamar la semana que viene", "Hablé con ella.")
    [(event_type, payload)] = _events(db, out["note_id"])
    assert event_type == "note.created"
    assert payload["note_kind"] == "llamada" and payload["call_outcome"] == "contesto"
    assert payload["call_next_step"] == "Llamar la semana que viene"


@needs_db
def test_a_blank_next_step_is_stored_as_null(db, operator) -> None:
    out = _add(db, operator, subject_kind="person", subject_id=_person(db), body="Buzón de voz.",
               note_kind="llamada", call_outcome="buzon", call_next_step="   ")
    assert _row(db, out["note_id"])[:3] == ("llamada", "buzon", None)
    [(_, payload)] = _events(db, out["note_id"])
    assert "call_next_step" not in payload


@needs_db
def test_an_ordinary_note_stores_nulls_and_an_unchanged_event(db, operator) -> None:
    pid = _person(db)
    out = _add(db, operator, subject_kind="person", subject_id=pid, body="Una anotación.")
    assert _row(db, out["note_id"]) == (None, None, None, "Una anotación.")
    [(_, payload)] = _events(db, out["note_id"])
    assert payload == {"subject_kind": "person", "subject_id": pid}


@needs_db
def test_a_revision_keeps_the_call_record_of_the_note_it_revises(db, operator) -> None:
    pid = _person(db)
    out = _add(db, operator, subject_kind="person", subject_id=pid, body="Primera versión.",
               note_kind="llamada", call_outcome="no_contesto", call_next_step="Insistir mañana")
    revised = V2CrmAuthoringRepository(psycopg.connect, runtime_dsn(db)).execute(
        command_name="revise-note", operator=operator,
        fields={"note_id": out["note_id"], "expected_version": 1, "body": "Segunda versión."},
        idempotency_key=uuid.uuid4().hex, digest="e" * 64)
    assert _row(db, revised["note_id"]) == ("llamada", "no_contesto", "Insistir mañana", "Segunda versión.")


@needs_db
def test_the_note_read_models_expose_the_call_fields(db, operator) -> None:
    pid, oid, cid = _person(db), _org(db), _case(db, operator)
    for kind, subject in (("person", pid), ("organization", oid), ("opportunity", cid)):
        _add(db, operator, subject_kind=kind, subject_id=subject, body="Ordinaria.")
        _add(db, operator, subject_kind=kind, subject_id=subject, body="Llamada.",
             note_kind="llamada", call_outcome="numero_malo", call_next_step="Buscar otro teléfono")
    reads = CrmWorkspaceRepository(psycopg.connect, runtime_dsn(db))
    person_notes = reads.person_authoring(pid)["notes"]
    org_notes = reads.organization_authoring(oid)["notes"]
    case_notes = reads.opportunity_notes(cid)["notes"]
    for notes in (person_notes, org_notes, case_notes):
        by_body = {n["body"]: n for n in notes}
        assert (by_body["Ordinaria."]["note_kind"], by_body["Ordinaria."]["call_outcome"],
                by_body["Ordinaria."]["call_next_step"]) == (None, None, None)
        assert (by_body["Llamada."]["note_kind"], by_body["Llamada."]["call_outcome"],
                by_body["Llamada."]["call_next_step"]) == ("llamada", "numero_malo", "Buscar otro teléfono")
