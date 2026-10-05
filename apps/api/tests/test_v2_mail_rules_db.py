"""Email → cases against a real database: preview, apply, idempotency, undo of every action type.

Runs in a disposable `origenlab_test_<hex>` (tests/v2_command_harness.py) as the unprivileged
`origenlab_api` role. Every address, domain, institution and number is fictitious.
"""

from __future__ import annotations

import hashlib
import json
import uuid

import pytest

from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

pytestmark = needs_db


@pytest.fixture(scope="module")
def db():
    yield from build_disposable_database()


def _owner(db):
    import psycopg

    conn = psycopg.connect(db, autocommit=True)
    conn.execute("set role origenlab_owner")
    return conn


def _one(db, sql, *args):
    with _owner(db) as conn:
        row = conn.execute(sql, args).fetchone()
    return row


def _repo(db):
    import psycopg

    from origenlab_api.v2.mail_rules_repository import MailRulesRepository

    return MailRulesRepository(psycopg.connect, runtime_dsn(db), commands_enabled=True)


def _operator(operator_id, role="admin"):
    from origenlab_api.v2.identity import OperatorIdentity

    return OperatorIdentity(operator_id=operator_id, email_norm="admin@example.test",
                            display_name="Admin Ficticio", role=role, status="active")


def _mail(conn, *, thread, direction_hint, sender, recipients, subject, documents=()):
    gid = uuid.uuid4().hex[:16]
    payload = {
        "gmail_message_id": gid, "gmail_thread_id": thread, "direction_hint": direction_hint,
        "sender": sender, "recipients": recipients, "subject_raw": subject,
        "sent_at": "2026-10-01T15:00:00+00:00", "documents": list(documents),
        "proposed_quote_numbers": sorted({t for d in documents for t in d["cn_tokens"]}),
    }
    return conn.execute(
        "insert into evidence.source_record (kind, dedupe_key, payload, source_uri) "
        "values ('gmail_message', %s, %s::jsonb, %s) returning id::text",
        (f"gmail_message:{gid}", json.dumps(payload), f"gmail://msg/{gid}"),
    ).fetchone()[0]


def _doc(filename, cn_tokens=()):
    return {"filename": filename, "sha256": hashlib.sha256(filename.encode() + uuid.uuid4().bytes).hexdigest(),
            "cn_tokens": list(cn_tokens)}


@pytest.fixture
def world(db):
    """An admin, a known client, a supplier and five fresh emails on two threads."""
    tag = uuid.uuid4().hex[:8]
    serial = int(tag[:4], 16) % 8000 + 1000  # a correlative nobody else in this database uses
    w: dict[str, str] = {"tag": tag, "client_domain": f"cliente-{tag}.test", "new_domain": f"nuevo-{tag}.test"}
    w["number"] = f"0{serial}-26"
    token = f"CN{serial}5"  # «CN0<serial>5.pdf» captured without its leading zero, revision 5
    with _owner(db) as conn:
        w["admin"] = conn.execute(
            "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
            "values (gen_random_uuid(), %s, 'Admin Ficticio', 'admin', 'active') returning id::text",
            (f"admin-{tag}@example.test",)).fetchone()[0]
        w["client"] = conn.execute(
            "insert into crm.organization (kind, name, confirmation) values ('institution', %s, 'confirmed') "
            "returning id::text", (f"Cliente Ficticio {tag}",)).fetchone()[0]
        conn.execute("insert into crm.organization_domain (organization_id, domain_norm, scope) values (%s, %s, 'shared')",
                     (w["client"], w["client_domain"]))
        supplier = conn.execute(
            "insert into crm.organization (kind, name, confirmation) values ('institution', %s, 'confirmed') "
            "returning id::text", (f"Proveedor Ficticio {tag}",)).fetchone()[0]
        conn.execute("insert into crm.organization_domain (organization_id, domain_norm, scope) values (%s, %s, 'shared')",
                     (supplier, f"proveedor-{tag}.test"))
        conn.execute("insert into crm.organization_relationship (organization_id, role, valid_from) "
                     "values (%s, 'supplier', current_date)", (supplier,))
        w["t1"], w["t2"] = f"thread-a-{tag}", f"thread-b-{tag}"
        w["e_known"] = _mail(conn, thread=w["t1"], direction_hint="external", sender="Ventas <contacto@origenlab.cl>",
                             recipients=f"Compras <compras@{w['client_domain']}>", subject="Cotización",
                             documents=[_doc(f"{token[:2]}0{token[2:]}-cliente.pdf", [token])])
        w["e_new"] = _mail(conn, thread=w["t2"], direction_hint="external", sender="contacto@origenlab.cl",
                           recipients=f"lab@{w['new_domain']}", subject="Cotización",
                           documents=[_doc(f"CN0{serial + 1}-nuevo.pdf", [f"CN0{serial + 1}"])])
        w["e_free"] = _mail(conn, thread=f"thread-c-{tag}", direction_hint="external", sender="contacto@origenlab.cl",
                            recipients="alguien@gmail.com", subject="Cotización",
                            documents=[_doc(f"CN0{serial + 2}.pdf", [f"CN0{serial + 2}"])])
        w["e_supplier"] = _mail(conn, thread=f"thread-d-{tag}", direction_hint="external", sender="contacto@origenlab.cl",
                                recipients=f"ventas@proveedor-{tag}.test", subject="Consulta precios",
                                documents=[_doc(f"CN0{serial + 3}.pdf", [f"CN0{serial + 3}"])])
    return w


def _action(preview, evidence_id):
    return next(a for a in preview["actions"] if a["evidence_id"] == evidence_id)


def _counts(db):
    return _one(db, "select (select count(*) from crm.opportunity), (select count(*) from crm.quote_revision), "
                    "(select count(*) from crm.domain_event)")


def test_preview_plans_and_writes_nothing(db, world) -> None:
    before = _counts(db)
    preview = _repo(db).preview()
    assert _counts(db) == before
    known = _action(preview, world["e_known"])
    assert (known["rule_id"], known["mode"]) == ("R3", "auto")
    assert known["quote_number"] == world["number"]
    assert known["organization_id"] == world["client"]
    assert known["reasons"]
    new = _action(preview, world["e_new"])
    assert (new["rule_id"], new["mode"], new["proposed_domain"]) == ("R4", "auto", world["new_domain"])
    assert _action(preview, world["e_free"])["mode"] == "none"
    assert _action(preview, world["e_supplier"])["mode"] == "none"


def test_apply_creates_through_the_commands_as_the_system_and_is_idempotent(db, world) -> None:
    repo = _repo(db)
    op = _operator(world["admin"])
    result = repo.apply(op, [world["e_known"], world["e_new"], world["e_free"], world["e_supplier"]])
    assert not result["refused"], result["refused"]
    by_rule = {a["rule_id"]: a for a in result["applied"]}
    assert set(by_rule) == {"R3", "R4"}

    case_id = by_rule["R3"]["case_id"]
    stage, org_id = _one(db, "select stage, organization_id::text from crm.opportunity where id = %s", case_id)
    assert (stage, org_id) == ("quoting", world["client"])
    assert _one(db, "select quote_number from crm.quote where opportunity_id = %s", case_id)[0] == world["number"]

    # Every event of the action is the machine's, attributed to the rule; the receipt names the admin.
    kinds = _one(db, "select array_agg(distinct actor_kind), bool_and(actor_operator_id is null), "
                     "bool_and(payload -> 'attribution' ->> 'rule_id' = 'R3') "
                     "from crm.domain_event where command_receipt_id = %s", by_rule["R3"]["command_receipt_id"])
    assert kinds == (["worker"], True, True)
    assert _one(db, "select operator_id::text from platform.command_receipt where id = %s",
                by_rule["R3"]["command_receipt_id"])[0] == world["admin"]

    new_org = by_rule["R4"]["organization_id"]
    assert _one(db, "select confirmation, name from crm.organization where id = %s", new_org) == (
        "machine_proposed", world["new_domain"])
    assert _one(db, "select stage from crm.opportunity where id = %s", by_rule["R4"]["case_id"])[0] == "quoting"

    # Nothing for free-mail or a supplier.
    assert _one(db, "select count(*) from crm.opportunity where origin_source_record_id in (%s, %s)",
                world["e_free"], world["e_supplier"])[0] == 0

    before = _counts(db)
    again = repo.apply(op, [world["e_known"], world["e_new"], world["e_free"], world["e_supplier"]])
    assert again == {"applied": [], "refused": [], "proposals_left_for_review": 0, "no_rule_applies": 2}
    assert _counts(db) == before
    assert _action(repo.preview(), world["e_free"])["mode"] == "none"
    assert not any(a["evidence_id"] == world["e_known"] for a in repo.preview()["actions"])


def _apply_only(db, world, evidence_id):
    result = _repo(db).apply(_operator(world["admin"]), [evidence_id])
    assert not result["refused"], result["refused"]
    assert len(result["applied"]) == 1, result
    return result["applied"][0]


def test_r1_link_r5_win_and_their_undo(db, world) -> None:
    repo = _repo(db)
    op = _operator(world["admin"])
    r3 = _apply_only(db, world, world["e_known"])
    case_id = r3["case_id"]

    with _owner(db) as conn:
        reply = _mail(conn, thread=world["t1"], direction_hint=None, sender=f"compras@{world['client_domain']}",
                      recipients="contacto@origenlab.cl", subject="RE: Cotización")
    r1 = _apply_only(db, world, reply)
    assert (r1["rule_id"], r1["case_id"]) == ("R1", case_id)

    with _owner(db) as conn:
        order = _mail(conn, thread=world["t1"], direction_hint=None, sender=f"compras@{world['client_domain']}",
                      recipients="contacto@origenlab.cl", subject="Orden de compra",
                      documents=[_doc("OC 4500123.pdf")])
    r5 = _apply_only(db, world, order)
    assert r5["rule_id"] == "R5"
    stage, won_quote = _one(db, "select stage, won_quote_id from crm.opportunity where id = %s", case_id)
    assert stage == "won" and won_quote is not None

    undone = repo.undo(op, r5["command_receipt_id"], "no era una orden de compra")
    assert [s["command"] for s in undone["steps"]] == ["correct_case_stage", "advance_case_stage", "unlink_case_evidence"]
    assert _one(db, "select stage, won_quote_id, closed_at from crm.opportunity where id = %s", case_id) == (
        "quoting", None, None)
    assert _one(db, "select actor_kind, payload -> 'attribution' ->> 'undoes_receipt_id' from crm.domain_event "
                    "where event_type = 'opportunity.stage_corrected' and aggregate_id = %s", case_id) == (
        "operator", r5["command_receipt_id"])

    from origenlab_api.v2.commands import CommandRefused

    replay = repo.undo(op, r5["command_receipt_id"], "no era una orden de compra")
    assert replay["replayed"] is True
    with _owner(db) as conn:
        other = conn.execute(
            "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
            "values (gen_random_uuid(), %s, 'Otra Admin', 'admin', 'active') returning id::text",
            (f"otra-{uuid.uuid4().hex[:8]}@example.test",)).fetchone()[0]
    with pytest.raises(CommandRefused) as refused:
        repo.undo(_operator(other), r5["command_receipt_id"], "otra vez")
    assert refused.value.code == "already_undone"

    repo.undo(op, r1["command_receipt_id"], "no era de este caso")
    assert _one(db, "select unlinked_at is not null from crm.opportunity_evidence "
                    "where opportunity_id = %s and source_record_id = %s", case_id, reply)[0] is True

    applied = {a["receipt_id"]: a for a in repo.applied()}
    assert applied[r5["command_receipt_id"]]["undone"] is True
    assert applied[r3["command_receipt_id"]]["undone"] is False
    # An undone action never comes back on the next run.
    assert not any(a["evidence_id"] in (order, reply) for a in repo.preview()["actions"])


def test_r6_lost_and_undo(db, world) -> None:
    repo = _repo(db)
    r3 = _apply_only(db, world, world["e_known"])
    with _owner(db) as conn:
        lost = _mail(conn, thread=world["t1"], direction_hint=None, sender=f"compras@{world['client_domain']}",
                     recipients="contacto@origenlab.cl",
                     subject="RE: Cotización — ya fue gestionada por otro proveedor")
    r6 = _apply_only(db, world, lost)
    assert r6["rule_id"] == "R6"
    assert _one(db, "select stage, close_reason from crm.opportunity where id = %s", r3["case_id"]) == (
        "lost", "otro proveedor")
    assert _one(db, "select relation from crm.opportunity_evidence where source_record_id = %s", lost)[0] == "contradicts"
    repo.undo(_operator(world["admin"]), r6["command_receipt_id"], "se equivocó la regla")
    assert _one(db, "select stage, closed_at from crm.opportunity where id = %s", r3["case_id"]) == ("quoting", None)


def test_undo_of_created_cases_discards_them_and_archives_the_por_confirmar_institution(db, world) -> None:
    repo = _repo(db)
    op = _operator(world["admin"])
    r3 = _apply_only(db, world, world["e_known"])
    r4 = _apply_only(db, world, world["e_new"])

    repo.undo(op, r3["command_receipt_id"], "no era un caso nuevo")
    assert _one(db, "select stage, close_reason from crm.opportunity where id = %s", r3["case_id"]) == (
        "abandoned", "discarded_by_correction")
    assert _one(db, "select status from crm.quote_revision where id = %s",
                r3["created"]["quote_revision_id"])[0] == "void"

    repo.undo(op, r4["command_receipt_id"], "dominio equivocado")
    assert _one(db, "select stage from crm.opportunity where id = %s", r4["case_id"])[0] == "abandoned"
    assert _one(db, "select status from crm.organization where id = %s", r4["organization_id"])[0] == "archived"
    # The timeline keeps everything: the origin link stays, nothing was deleted.
    assert _one(db, "select count(*) from crm.opportunity_evidence where opportunity_id = %s and relation = 'origin' "
                    "and unlinked_at is null", r3["case_id"])[0] == 1


def test_an_unknown_receipt_is_refused(db, world) -> None:
    from origenlab_api.v2.commands import CommandRefused

    with pytest.raises(CommandRefused) as refused:
        _repo(db).undo(_operator(world["admin"]), str(uuid.uuid4()), "nada")
    assert refused.value.code == "applied_action_not_found"
