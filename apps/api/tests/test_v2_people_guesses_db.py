"""Weak person guesses, read and answered, against a real database (people first, Task 7).

A W1 guess («¿Es Pedro Soto (Universidad Ficticia)?») shows on the person's contact page and under
the email's sender on the case; one Sí/No answers it. «Sí» attaches the address as a person's act
(`confirmed`, `actor_kind = 'operator'`); «No» is a receipt only, and the planner never asks again.
The contact page also says where each name, address and institution was learned, and «Confirmar»
(`confirm-person-fact`) turns a machine-made one into a person's.

Same disposable database and `origenlab_api` role as `test_v2_people_rules_db.py`, whose fixture
helpers it reuses. Every name, address and institution is fictitious and carries the test's tag.
"""

from __future__ import annotations

import json
import uuid

import pytest

from test_v2_people_rules_db import _new_person_reading, _one, _operator, _owner, _person, _repo, _word
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

pytestmark = needs_db


@pytest.fixture(scope="module")
def db():
    yield from build_disposable_database()


@pytest.fixture
def world(db):
    tag = uuid.uuid4().hex[:8]
    w: dict[str, str] = {"tag": tag, "sur": _word(tag), "domain": f"u-{tag}.test",
                         "org_name": f"Universidad Ficticia {_word(tag)}"}
    with _owner(db) as conn:
        w["admin"] = conn.execute(
            "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
            "values (gen_random_uuid(), %s, 'Admin Ficticio', 'admin', 'active') returning id::text",
            (f"admin-{tag}@example.test",)).fetchone()[0]
        w["org"] = conn.execute(
            "insert into crm.organization (kind, name, confirmation) values ('institution', %s, 'confirmed') "
            "returning id::text", (w["org_name"],)).fetchone()[0]
        conn.execute("insert into crm.organization_domain (organization_id, domain_norm, scope) "
                     "values (%s, %s, 'exclusive')", (w["org"], w["domain"]))
    return w


def _workspace(db):
    import psycopg

    from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository

    return CrmWorkspaceRepository(psycopg.connect, runtime_dsn(db))


def _mail(db, address, *, header=None, sig_name=None, institution=None, sent_at="2026-10-21T15:00:00+00:00"):
    """One captured email on its own thread, with its `from` participant and `people:v1` reading."""
    gid = uuid.uuid4().hex[:16]
    with _owner(db) as conn:
        record = conn.execute(
            "insert into evidence.source_record (kind, dedupe_key, payload, source_uri) "
            "values ('gmail_message', %s, %s::jsonb, %s) returning id::text",
            (f"gmail_message:{gid}",
             json.dumps({"gmail_message_id": gid, "gmail_thread_id": f"t-{gid}", "sender": f"{header or ''} <{address}>",
                         "sent_at": sent_at, "subject_raw": "Consulta ficticia"}),
             f"gmail://msg/{gid}")).fetchone()[0]
        mailbox = conn.execute("insert into comms.mailbox (address_norm) values (%s) returning id",
                               (f"mbox-{gid}@example.test",)).fetchone()[0]
        message = conn.execute(
            "insert into comms.message (mailbox_id, provider_message_id, provider_thread_id, direction, internal_date) "
            "values (%s, %s, %s, 'inbound', now()) returning id", (mailbox, gid, f"t-{gid}")).fetchone()[0]
        participant = conn.execute(
            "insert into comms.message_participant (message_id, role, address_norm, display_name) "
            "values (%s, 'from', %s, %s) returning id::text", (message, address, header)).fetchone()[0]
        signature = None
        if sig_name or institution:
            signature = {"name": sig_name, "title": None, "institution": institution, "phone": None}
        value = {"version": 1, "sender_address": address, "header_name": header, "signature": signature,
                 "co_addresses": [], "signature_raw": ""}
        assertion = conn.execute(
            "insert into evidence.assertion (source_record_id, kind, value_norm, value) "
            "values (%s, 'message_triage', 'people:v1', %s::jsonb) returning id::text",
            (record, json.dumps(value))).fetchone()[0]
    return {"assertion": assertion, "record": record, "participant": participant, "message_id": gid}


def _case_with(db, w, record):
    with _owner(db) as conn:
        case_id = conn.execute(
            "insert into crm.opportunity (title, stage, owner_operator_id) values (%s, 'lead', %s) returning id::text",
            (f"Caso ficticio {w['tag']}", w["admin"])).fetchone()[0]
        conn.execute("insert into crm.opportunity_evidence (opportunity_id, source_record_id, relation, "
                     "linked_by_operator_id) values (%s, %s, 'origin', %s)", (case_id, record, w["admin"]))
    return case_id


def _guess_world(db, w, first="Pedro"):
    """A person of the institution, and a Gmail address that writes with their name but no
    signature: only a W1 guess."""
    name = f"{first} {w['sur']}"
    pid, _cp = _person(db, name, org=w["org"])
    address = f"{first.lower()}.{w['tag']}@gmail.com"
    mail = _mail(db, address, header=name)
    return pid, name, address, mail


# ─────────────────────────────────────────────────────────────── read ──

def test_guess_listed_on_person_and_case(db, world) -> None:
    pid, name, address, mail = _guess_world(db, world)
    repo = _repo(db)
    (guess,) = repo.guesses_for_person(pid)
    assert guess["guess_id"] == f"{mail['assertion']}:{pid}"
    assert guess["address"] == address and guess["person_id"] == pid and guess["person_name"] == name
    assert guess["organization_name"] == world["org_name"]
    assert guess["question_es"] == f"¿Es {name} ({world['org_name']})?"
    assert guess["reasons"] and all(isinstance(r, str) for r in guess["reasons"])

    workspace = _workspace(db)
    body = workspace.person_authoring(pid, guesses=repo.guesses_for_person)
    assert [g["guess_id"] for g in body["guesses"]] == [guess["guess_id"]]

    case_id = _case_with(db, world, mail["record"])
    [thread] = workspace.opportunity_threads(case_id, person_guesses=repo.guesses_for_case)["threads"]
    [message] = thread["messages"]
    assert message["message_id"] == mail["message_id"]
    assert [g["question_es"] for g in message["person_guesses"]] == [guess["question_es"]]
    # Without the people rules the field is still there, empty.
    [thread] = workspace.opportunity_threads(case_id)["threads"]
    assert thread["messages"][0]["person_guesses"] == []


# ─────────────────────────────────────────────────────────────── answer ──

def test_answer_si_attaches_confirmed(db, world) -> None:
    pid, _name, address, mail = _guess_world(db, world, first="Sofia")
    repo = _repo(db)
    (guess,) = repo.guesses_for_person(pid)
    op = _operator(world["admin"])
    out = repo.answer_guess(op, guess["guess_id"], "si", f"k-{uuid.uuid4().hex}")
    assert out["answer"] == "si" and out["address"] == address and out["person_id"] == pid
    cp_id = out["created"]["contact_point_id"]
    row = _one(db, "select person_id::text, usage, confirmation, organization_id from crm.contact_point where id = %s",
               cp_id)
    assert row == (pid, "personal", "confirmed", None)
    actor = _one(db, "select actor_kind, actor_operator_id::text from crm.domain_event "
                     "where aggregate_kind = 'contact_point' and aggregate_id = %s", cp_id)
    assert actor == ("operator", world["admin"])
    assert _one(db, "select resolved_contact_point_id::text from comms.message_participant where id = %s",
                mail["participant"])[0] == cp_id
    assert repo.guesses_for_person(pid) == []
    with repo._read() as cur:
        assert repo.answered(cur)[(address, pid)] is True
    # Replay with the same key answers the same, and attaches nothing twice.
    key = f"k-{uuid.uuid4().hex}"
    pid2, _n, _a, _m = _guess_world(db, world, first="Tomas")
    (g2,) = repo.guesses_for_person(pid2)
    first = repo.answer_guess(op, g2["guess_id"], "si", key)
    again = repo.answer_guess(op, g2["guess_id"], "si", key)
    assert again["replayed"] is True and again["created"] == first["created"]


def test_answer_no_hides_guess_forever(db, world) -> None:
    pid, name, address, _mail_row = _guess_world(db, world, first="Rocio")
    repo = _repo(db)
    (guess,) = repo.guesses_for_person(pid)
    out = repo.answer_guess(_operator(world["admin"]), guess["guess_id"], "no", f"k-{uuid.uuid4().hex}")
    assert out["answer"] == "no" and out["created"] == {}
    assert _one(db, "select count(*) from crm.contact_point where value_norm = %s", address)[0] == 0
    assert repo.guesses_for_person(pid) == []
    # A later email from the same address, even one whose signature would attach it by itself, asks
    # nothing and attaches nothing: a person said it is not them.
    later = _mail(db, address, header=name, sig_name=name, institution=world["org_name"])
    assert repo.guesses_for_person(pid) == []
    assert [a for a in repo.plan() if a.assertion_id == later["assertion"]] == []
    # A second answer to the same guess is refused.
    from origenlab_api.v2.commands import CommandRefused

    with pytest.raises(CommandRefused) as exc:
        repo.answer_guess(_operator(world["admin"]), guess["guess_id"], "si", f"k-{uuid.uuid4().hex}")
    assert exc.value.code == "already_answered"


def test_answer_refuses_a_malformed_or_unknown_guess(db, world) -> None:
    from origenlab_api.v2.commands import CommandRefused

    repo = _repo(db)
    op = _operator(world["admin"])
    for guess_id, code in (("nope", "malformed_identifier"),
                           (f"{uuid.uuid4()}:{uuid.uuid4()}", "guess_not_found")):
        with pytest.raises(CommandRefused) as exc:
            repo.answer_guess(op, guess_id, "no", f"k-{uuid.uuid4().hex}")
        assert exc.value.code == code


def test_answer_requires_sales_or_admin(db, world) -> None:
    """The route refuses a viewer before anything is read; sales answers."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
    from origenlab_api.v2.crm_authoring_routes import crm_authoring_router
    from origenlab_api.v2.identity import LocalDevIdentity, OperatorIdentity, OperatorLookup

    pid, _name, _address, _mail_row = _guess_world(db, world, first="Ines")
    repo = _repo(db)
    (guess,) = repo.guesses_for_person(pid)

    class _Lookup(OperatorLookup):
        def __init__(self, role):
            self.role = role

        def by_email(self, email_norm):
            return OperatorIdentity(operator_id=world["admin"], email_norm=email_norm, display_name="Op",
                                    role=self.role, status="active")

    def client(role):
        app = FastAPI()
        app.include_router(crm_authoring_router)
        app.state.crm_authoring_repository = object()
        app.state.people_rules_repository = repo
        app.state.v2_identity = LocalDevIdentity("postgresql://origenlab_api:pw@127.0.0.1:54332/x", _Lookup(role))
        return TestClient(app)

    headers = {OPERATOR_EMAIL_HEADER: "op@example.test", "Idempotency-Key": f"k-{uuid.uuid4().hex}"}
    body = {"guess_id": guess["guess_id"], "answer": "no"}
    r = client("viewer").post("/v2/commands/answer-person-guess", json=body, headers=headers)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "role_may_not_decide"
    assert repo.guesses_for_person(pid) != []
    r = client("viewer").post("/v2/commands/confirm-person-fact",
                              json={"kind": "name", "id": str(uuid.uuid4())}, headers=headers)
    assert r.status_code == 403
    r = client("sales").post("/v2/commands/answer-person-guess",
                             json={**body, "idempotency_key": f"k-{uuid.uuid4().hex}"},
                             headers={OPERATOR_EMAIL_HEADER: "op@example.test"})
    assert r.status_code == 200, r.text
    assert r.json()["answer"] == "no"
    assert repo.guesses_for_person(pid) == []


# ─────────────────────────────────────────────────────────────── provenance and confirm ──

def test_provenance_says_where_each_fact_was_learned_and_confirm_makes_it_a_persons(db, world) -> None:
    name, address, reading = _new_person_reading(db, world, first="Lucia")
    repo = _repo(db)
    op = _operator(world["admin"])
    (applied,) = [repo.apply_planned(op, a, automatic=True) for a in repo.plan()
                  if a.assertion_id == reading["assertion"]]
    pid, created = applied["person_id"], applied["created"]
    with _owner(db) as conn:
        conn.execute("update evidence.source_record set payload = payload || '{\"sent_at\": \"2026-10-21T15:00:00Z\"}' "
                     "where id = %s", (reading["record"],))
        typed = conn.execute(
            "insert into crm.person_name (person_id, name_display, name_norm, source, confirmation) "
            "values (%s, %s, %s, 'operator', 'confirmed') returning id::text",
            (pid, f"L. {world['sur']}", f"l {world['sur'].lower()}")).fetchone()[0]
        header_name = conn.execute(
            "insert into crm.person_name (person_id, name_display, name_norm, source, confirmation) "
            "values (%s, %s, %s, 'display_name', 'machine_proposed') returning id::text",
            (pid, f"Lucy {world['sur']}", f"lucy {world['sur'].lower()}")).fetchone()[0]

    body = _workspace(db).person_authoring(pid)
    assert body["guesses"] == []
    assert sorted(n["name"] for n in body["other_names"]) == sorted([f"L. {world['sur']}", f"Lucy {world['sur']}"])
    items = {(p["kind"], p["id"]): p for p in body["provenance"]}
    address_item = items[("address", created["contact_point_id"])]
    assert address_item == {"kind": "address", "id": created["contact_point_id"], "label": address,
                            "source_es": "firma del correo 21 oct", "machine": True,
                            "receipt_id": applied["command_receipt_id"]}
    aff_item = items[("affiliation", created["affiliation_id"])]
    assert (aff_item["label"], aff_item["source_es"], aff_item["machine"], aff_item["receipt_id"]) == (
        world["org_name"], "firma del correo 21 oct", True, applied["command_receipt_id"])
    assert (items[("name", typed)]["source_es"], items[("name", typed)]["machine"],
            items[("name", typed)]["receipt_id"]) == ("lo agregó una persona", False, None)
    assert (items[("name", header_name)]["source_es"], items[("name", header_name)]["machine"]) == (
        "nombre en el correo", True)

    for kind, fact_id in (("address", created["contact_point_id"]), ("affiliation", created["affiliation_id"]),
                          ("name", header_name)):
        out = repo.confirm_fact(op, kind, fact_id, f"k-{uuid.uuid4().hex}")
        assert out["kind"] == kind and out["id"] == fact_id and out["person_id"] == pid
    assert _one(db, "select confirmation from crm.contact_point where id = %s", created["contact_point_id"])[0] == "confirmed"
    assert _one(db, "select confirmation, confirmed_by_operator_id::text from crm.affiliation where id = %s",
                created["affiliation_id"]) == ("confirmed", world["admin"])
    assert _one(db, "select confirmation from crm.person_name where id = %s", header_name)[0] == "confirmed"
    assert _one(db, "select count(*) from crm.domain_event where aggregate_kind = 'contact_point' "
                    "and aggregate_id = %s and event_type = 'contact_point.confirmed' and actor_kind = 'operator'",
                created["contact_point_id"])[0] == 1
    body = _workspace(db).person_authoring(pid)
    assert not any(p["machine"] for p in body["provenance"] if p["id"] in
                   (created["contact_point_id"], created["affiliation_id"], header_name))
    # Where it was learned does not change; that a person confirmed it does.
    assert {p["source_es"] for p in body["provenance"] if p["id"] == created["contact_point_id"]} == {
        "firma del correo 21 oct"}

    from origenlab_api.v2.commands import CommandRefused

    with pytest.raises(CommandRefused) as exc:
        repo.confirm_fact(op, "address", created["contact_point_id"], f"k-{uuid.uuid4().hex}")
    assert exc.value.code == "already_confirmed"
    with pytest.raises(CommandRefused) as exc:
        repo.confirm_fact(op, "name", str(uuid.uuid4()), f"k-{uuid.uuid4().hex}")
    assert exc.value.code == "fact_not_found"
    with pytest.raises(CommandRefused) as exc:
        repo.confirm_fact(op, "phone", str(uuid.uuid4()), f"k-{uuid.uuid4().hex}")
    assert exc.value.code == "unknown_kind"
    # What a person confirmed is not «Quitar»-ed by the machine's undo.
    with pytest.raises(CommandRefused) as exc:
        repo.undo(op, applied["command_receipt_id"], "Quitado de Hoy")
    assert exc.value.code == "changed_since"


def test_undo_refused_once_a_learned_name_was_confirmed(db, world) -> None:
    from origenlab_api.v2.commands import CommandRefused

    address = f"vsoto.{world['tag']}@{world['domain']}"
    pid, _cp = _person(db, f"Vicente {world['sur']}", address=address, org=world["org"])
    reading = _mail(db, address, header=f"Jose Vicente {world['sur']} Rivas")
    repo = _repo(db)
    op = _operator(world["admin"])
    results = [repo.apply_planned(op, a, automatic=True) for a in repo.plan() if a.assertion_id == reading["assertion"]]
    (p1b,) = [r for r in results if r["rule_id"] == "P1b"]
    repo.confirm_fact(op, "name", p1b["created"]["person_name_id"], f"k-{uuid.uuid4().hex}")
    with pytest.raises(CommandRefused) as exc:
        repo.undo(op, p1b["command_receipt_id"], "Quitado de Hoy")
    assert exc.value.code == "changed_since"
    assert _one(db, "select removed_at from crm.person_name where id = %s", p1b["created"]["person_name_id"])[0] is None
    assert pid
