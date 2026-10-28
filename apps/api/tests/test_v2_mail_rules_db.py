"""Email → cases against a real database: preview, apply, idempotency, undo of every action type.

Runs in a disposable `origenlab_test_<hex>` (tests/v2_command_harness.py) as the unprivileged
`origenlab_api` role. Every address, domain, institution and number is fictitious.
"""

from __future__ import annotations

import hashlib
import itertools
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


def _all(db, sql, *args):
    with _owner(db) as conn:
        return conn.execute(sql, args).fetchall()


def _repo(db):
    import psycopg

    from origenlab_api.v2.mail_rules_repository import MailRulesRepository

    return MailRulesRepository(psycopg.connect, runtime_dsn(db), commands_enabled=True)


def _operator(operator_id, role="admin"):
    from origenlab_api.v2.identity import OperatorIdentity

    return OperatorIdentity(operator_id=operator_id, email_norm="admin@example.test",
                            display_name="Admin Ficticio", role=role, status="active")


def _mail(conn, *, thread, direction_hint, sender, recipients, subject, documents=(), direction=None,
          review_status="pending", staged=False):
    """A live 4a capture: the evidence record and its `comms.message`, as the worker writes them.
    `staged=True` makes a historical staged record instead (no message, a staging hash)."""
    gid = uuid.uuid4().hex[:16]
    payload = {
        "gmail_message_id": gid, "gmail_thread_id": thread, "direction_hint": direction_hint,
        "sender": sender, "recipients": recipients, "subject_raw": subject,
        "sent_at": "2026-10-01T15:00:00+00:00", "documents": list(documents),
        "proposed_quote_numbers": sorted({t for d in documents for t in d["cn_tokens"]}),
        "staging_source_record_sha256": ("f" * 64) if staged else None,
    }
    record = conn.execute(
        "insert into evidence.source_record (kind, dedupe_key, payload, source_uri, review_status) "
        "values ('gmail_message', %s, %s::jsonb, %s, %s) returning id::text",
        (f"gmail_message:{gid}", json.dumps(payload), f"gmail://msg/{gid}", review_status),
    ).fetchone()[0]
    if not staged:
        mailbox = conn.execute(
            "insert into comms.mailbox (address_norm) values (%s) returning id",
            (f"mbox-{gid}@example.test",)).fetchone()[0]
        conn.execute(
            "insert into comms.message (mailbox_id, provider_message_id, provider_thread_id, direction, internal_date) "
            "values (%s, %s, %s, %s, now())",
            (mailbox, gid, thread, direction or ("outbound" if direction_hint else "inbound")))
    return record


def _doc(filename, cn_tokens=()):
    return {"filename": filename, "sha256": hashlib.sha256(filename.encode() + uuid.uuid4().bytes).hexdigest(),
            "cn_tokens": list(cn_tokens)}


def _requester_check(conn, source_record_id, *, organization_id=None, organization_name=None):
    matched = organization_id is not None
    value = {
        "requester_version": 1,
        "status": "matched" if matched else "none",
        "how": "exact_name_in_body",
    }
    if matched:
        value["organization_id"] = organization_id
        value["organization_name"] = organization_name
    conn.execute(
        "insert into evidence.assertion (source_record_id, kind, value_norm, value) "
        "values (%s, 'message_triage', 'requester:v1', %s::jsonb)",
        (source_record_id, json.dumps(value)),
    )


_SERIALS = itertools.count(1000 + (uuid.uuid4().int % 300) * 10, 10)


@pytest.fixture
def world(db):
    """An admin, a known client, a supplier and five fresh emails on two threads."""
    tag = uuid.uuid4().hex[:8]
    # A correlative nobody else in this module's database uses: each world takes serial..serial+3,
    # so worlds are 10 apart (a random pick collided between tests often enough to flake).
    serial = next(_SERIALS)
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


def _pairs(repo, ids):
    return [{"evidence_id": a["evidence_id"], "rule_id": a["rule_id"]}
            for a in repo.preview()["actions"] if a["evidence_id"] in ids and a["mode"] == "auto"]


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
    result = repo.apply(op, _pairs(repo, [world["e_known"], world["e_new"], world["e_free"], world["e_supplier"]]))
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
    assert _pairs(repo, [world["e_known"], world["e_new"]]) == []
    again = repo.apply(op, [{"evidence_id": world["e_known"], "rule_id": "R3"}])
    assert again["applied"] == []
    assert [r["code"] for r in again["refused"]] == ["plan_changed"]
    assert _counts(db) == before
    assert _action(repo.preview(), world["e_free"])["mode"] == "none"
    assert not any(a["evidence_id"] == world["e_known"] for a in repo.preview()["actions"])


def _apply_only(db, world, evidence_id):
    repo = _repo(db)
    result = repo.apply(_operator(world["admin"]), _pairs(repo, [evidence_id]))
    assert not result["refused"], result["refused"]
    assert len(result["applied"]) == 1, result
    return result["applied"][0]


def test_r7_request_then_r1_sent_quote_walks_en_estudio_to_enviada_and_undoes(db, world) -> None:
    repo = _repo(db)
    op = _operator(world["admin"])
    with _owner(db) as conn:
        request = _mail(
            conn, thread=f"t-rfq-{world['tag']}", direction_hint=None,
            sender=f"investigadora@{world['client_domain']}", recipients="contacto@origenlab.cl",
            subject="Solicitud de cotización de productos",
        )
    r7 = _apply_only(db, world, request)
    assert r7["rule_id"] == "R7"
    case_id = r7["case_id"]
    assert _one(db, "select stage, organization_id::text from crm.opportunity where id = %s", case_id) == (
        "qualified", world["client"])

    serial = int(world["number"][1:-3]) + 4
    token = f"CN0{serial}"
    with _owner(db) as conn:
        sent = _mail(
            conn, thread=f"t-rfq-{world['tag']}", direction_hint="external",
            sender="Tatiana <contacto@origenlab.cl>",
            recipients=f"investigadora@{world['client_domain']}",
            subject="RE: Solicitud de cotización de productos",
            documents=[_doc(f"{token}-cliente.pdf", [token])],
        )
    planned = _action(repo.preview(), sent)
    assert (planned["rule_id"], planned["mode"]) == ("R1", "auto")
    applied = _apply_only(db, world, sent)
    assert applied["quote_number"] == f"0{serial}-26"
    assert _one(db, "select stage from crm.opportunity where id = %s", case_id)[0] == "quoting"
    assert _one(db, "select status from crm.quote_revision where id = %s",
                applied["created"]["quote_revision_id"])[0] == "sent"

    undone = repo.undo(op, applied["command_receipt_id"], "la cotización no correspondía a este caso")
    assert [step["command"] for step in undone["steps"]] == [
        "void_historical_quote_revision", "advance_case_stage", "unlink_case_evidence",
    ]
    assert _one(db, "select stage from crm.opportunity where id = %s", case_id)[0] == "qualified"
    assert _one(db, "select status from crm.quote_revision where id = %s",
                applied["created"]["quote_revision_id"])[0] == "void"


def test_r1_two_alternatives_in_one_email_are_both_sent_quotes_of_the_thread_s_case_and_undo_together(db, world) -> None:
    # The 2026-10-16 shape: one reply on the request's thread carries two quotes (T18 and T10)
    # plus a data sheet. Both are recorded as sent on that case; undo voids both.
    repo = _repo(db)
    op = _operator(world["admin"])
    with _owner(db) as conn:
        request = _mail(
            conn, thread=f"t-two-{world['tag']}", direction_hint=None,
            sender=f"quimica@{world['client_domain']}", recipients="contacto@origenlab.cl",
            subject="Solicitud de cotización de homogeneizador",
        )
    case_id = _apply_only(db, world, request)["case_id"]
    first, second = int(world["number"][1:-3]) + 7, int(world["number"][1:-3]) + 8
    with _owner(db) as conn:
        sent = _mail(
            conn, thread=f"t-two-{world['tag']}", direction_hint="external",
            sender="Tatiana <contacto@origenlab.cl>", recipients=f"quimica@{world['client_domain']}",
            subject="RE: Solicitud de cotización de homogeneizador",
            documents=[_doc(f"CN0{first}-cliente-T18.pdf", [f"CN0{first}"]),
                       _doc(f"CN0{second}-cliente-T10.pdf", [f"CN0{second}"]),
                       _doc("ficha-tecnica.pdf", [])],
        )
    planned = _action(repo.preview(), sent)
    assert (planned["rule_id"], planned["mode"]) == ("R1", "auto")
    applied = _apply_only(db, world, sent)
    revisions = applied["created"]["quote_revision_ids"]
    assert len(revisions) == 2
    assert sorted(r[0] for r in _all(db, "select q.quote_number from crm.quote q where q.opportunity_id = %s", case_id)) == [
        f"0{first}-26", f"0{second}-26"]
    assert {_one(db, "select status from crm.quote_revision where id = %s", r)[0] for r in revisions} == {"sent"}
    assert _one(db, "select stage from crm.opportunity where id = %s", case_id)[0] == "quoting"
    assert all(a["evidence_id"] != sent for a in repo.preview()["actions"])  # applied, not planned again

    undone = repo.undo(op, applied["command_receipt_id"], "las cotizaciones no eran de este caso")
    assert [step["command"] for step in undone["steps"]] == [
        "void_historical_quote_revision", "void_historical_quote_revision", "advance_case_stage", "unlink_case_evidence",
    ]
    assert {_one(db, "select status from crm.quote_revision where id = %s", r)[0] for r in revisions} == {"void"}
    assert _one(db, "select stage from crm.opportunity where id = %s", case_id)[0] == "qualified"


def test_r9_a_quote_sent_to_gmail_reaches_the_case_of_the_institution_its_pdf_names_and_undoes(db, world) -> None:
    # The 2026-10-13 shape: the customer writes from gmail.com on a new thread; only the Cotizador
    # file name «CN0…-<institución>.pdf» says which institution the quote is for.
    repo = _repo(db)
    op = _operator(world["admin"])
    with _owner(db) as conn:
        request = _mail(
            conn, thread=f"t-r9-rfq-{world['tag']}", direction_hint=None,
            sender=f"investigadora@{world['client_domain']}", recipients="contacto@origenlab.cl",
            subject="Solicitud de cotización de productos",
        )
    case_id = _apply_only(db, world, request)["case_id"]
    serial = int(world["number"][1:-3]) + 5
    token = f"CN0{serial}"
    with _owner(db) as conn:
        sent = _mail(
            conn, thread=f"t-r9-sent-{world['tag']}", direction_hint="external",
            sender="Tatiana <contacto@origenlab.cl>", recipients="investigadora.personal@gmail.com",
            subject="Cotización", documents=[_doc(f"{token}-Cliente Ficticio {world['tag']}.pdf", [token])],
        )
    planned = _action(repo.preview(), sent)
    # The case has no quote yet: R13 (2026-10-15) takes it first, with R9's three steps.
    assert (planned["rule_id"], planned["mode"], planned["case_id"]) == ("R13", "auto", case_id)
    applied = _apply_only(db, world, sent)
    assert applied["quote_number"] == f"0{serial}-26"
    assert _one(db, "select stage from crm.opportunity where id = %s", case_id)[0] == "quoting"
    assert _one(db, "select status from crm.quote_revision where id = %s",
                applied["created"]["quote_revision_id"])[0] == "sent"

    undone = repo.undo(op, applied["command_receipt_id"], "la cotización era de otro caso")
    assert [step["command"] for step in undone["steps"]] == [
        "void_historical_quote_revision", "advance_case_stage", "unlink_case_evidence",
    ]
    assert _one(db, "select stage from crm.opportunity where id = %s", case_id)[0] == "qualified"


def test_r10_creates_the_institution_the_pdf_names_with_its_case_in_enviada_and_undoes(db, world) -> None:
    repo = _repo(db)
    op = _operator(world["admin"])
    serial = int(world["number"][1:-3]) + 6
    token = f"CN0{serial}"
    name = f"Instituto Nuevo {world['tag']} (INU)"
    with _owner(db) as conn:
        sent = _mail(conn, thread=f"t-r10-{world['tag']}", direction_hint="external",
                     sender="contacto@origenlab.cl", recipients="persona.nueva@gmail.com", subject="Cotización",
                     documents=[_doc(f"{token}-{name}.pdf", [token])])
    planned = _action(repo.preview(), sent)
    assert (planned["rule_id"], planned["mode"]) == ("R10", "auto")
    applied = _apply_only(db, world, sent)
    case_id, org_id = applied["case_id"], applied["created"]["organization_id"]
    assert _one(db, "select name, confirmation from crm.organization where id = %s", org_id) == (name, "machine_proposed")
    assert _one(db, "select count(*) from crm.organization_domain where organization_id = %s", org_id)[0] == 0
    assert _one(db, "select stage from crm.opportunity where id = %s", case_id)[0] == "quoting"

    repo.undo(op, applied["command_receipt_id"], "la institución no era esa")
    assert _one(db, "select stage from crm.opportunity where id = %s", case_id)[0] == "abandoned"
    assert _one(db, "select status from crm.organization where id = %s", org_id)[0] == "archived"


def test_r7_free_mail_request_is_visible_as_solicitada_without_false_institution(db, world) -> None:
    with _owner(db) as conn:
        request = _mail(
            conn, thread=f"t-free-rfq-{world['tag']}", direction_hint=None,
            sender="Irina Example <irina@gmail.com>", recipients="contacto@origenlab.cl",
            subject="Solicitud de cotización de productos",
        )
        # The API waits for the worker's deterministic body check before opening a free-mail RFQ.
        assert _action(_repo(db).preview(), request)["mode"] == "proposal"
        _requester_check(conn, request)
    r7 = _apply_only(db, world, request)
    assert r7["rule_id"] == "R7"
    assert _one(db, "select stage, organization_id from crm.opportunity where id = %s", r7["case_id"]) == (
        "lead", None)
    _repo(db).undo(_operator(world["admin"]), r7["command_receipt_id"], "prueba de reversión")
    assert _one(db, "select stage, close_reason from crm.opportunity where id = %s", r7["case_id"]) == (
        "abandoned", "discarded_by_correction")


def test_r7_free_mail_exact_requester_identity_moves_directly_to_en_estudio(db, world) -> None:
    with _owner(db) as conn:
        request = _mail(
            conn, thread=f"t-free-known-{world['tag']}", direction_hint=None,
            sender="Irina Example <irina@gmail.com>", recipients="contacto@origenlab.cl",
            subject="Solicitud de cotización de productos",
        )
        client_name = conn.execute(
            "select name from crm.organization where id = %s", (world["client"],)
        ).fetchone()[0]
        _requester_check(
            conn, request, organization_id=world["client"], organization_name=client_name
        )
    r7 = _apply_only(db, world, request)
    assert r7["rule_id"] == "R7"
    assert _one(db, "select stage, organization_id::text from crm.opportunity where id = %s", r7["case_id"]) == (
        "qualified", world["client"])


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


# ------------------------------------------------------------------ review fixes


def test_rejected_and_staged_records_are_never_planned(db, world) -> None:
    pdf = lambda: [_doc(f"CN0{uuid.uuid4().int % 8000 + 1000}.pdf", [f"CN0{uuid.uuid4().int % 8000 + 1000}"])]  # noqa: E731
    with _owner(db) as conn:
        rejected = _mail(conn, thread=f"t-r-{world['tag']}", direction_hint="external", sender="contacto@origenlab.cl",
                         recipients=f"x@{world['client_domain']}", subject="Cotización", documents=pdf(),
                         review_status="rejected")
        staged = _mail(conn, thread=f"t-s-{world['tag']}", direction_hint="external", sender="contacto@origenlab.cl",
                       recipients=f"x@{world['client_domain']}", subject="Cotización", documents=pdf(), staged=True)
    planned = {a["evidence_id"] for a in _repo(db).preview()["actions"]}
    assert rejected not in planned and staged not in planned
    assert world["e_known"] in planned


def test_a_triage_reading_does_not_hide_a_live_email_but_another_assertion_still_does(db, world) -> None:
    pdf = lambda: [_doc(f"CN0{uuid.uuid4().int % 8000 + 1000}.pdf", [f"CN0{uuid.uuid4().int % 8000 + 1000}"])]  # noqa: E731
    with _owner(db) as conn:
        triaged = _mail(conn, thread=f"t-tr-{world['tag']}", direction_hint="external", sender="contacto@origenlab.cl",
                        recipients=f"x@{world['client_domain']}", subject="Cotización", documents=pdf())
        other = _mail(conn, thread=f"t-ot-{world['tag']}", direction_hint="external", sender="contacto@origenlab.cl",
                      recipients=f"x@{world['client_domain']}", subject="Cotización", documents=pdf())
        with conn.cursor() as cur:
            cur.execute("insert into evidence.assertion (source_record_id, kind, value_norm, value) values "
                        "(%s, 'message_triage', 'triage:v1', '{}'), (%s, 'product_mention', 'UP200HT', '{}')",
                        (triaged, triaged))
            cur.execute("insert into evidence.assertion (source_record_id, kind, value_norm) "
                        "values (%s, 'organization_name', 'Ejemplo')", (other,))
    planned = {a["evidence_id"] for a in _repo(db).preview()["actions"]}
    assert triaged in planned and other not in planned


def test_apply_takes_only_previewed_pairs_and_refuses_a_changed_plan(db, world) -> None:
    repo = _repo(db)
    op = _operator(world["admin"])
    previewed = _pairs(repo, [world["e_known"]])
    with _owner(db) as conn:
        late = _mail(conn, thread=f"t-late-{world['tag']}", direction_hint="external", sender="contacto@origenlab.cl",
                     recipients=f"y@{world['client_domain']}", subject="Cotización",
                     documents=[_doc("CN09990.pdf", ["CN09990"])])
    assert _action(repo.preview(), late)["mode"] == "auto"
    result = repo.apply(op, [*previewed, {"evidence_id": world["e_new"], "rule_id": "R3"}])
    assert [a["evidence_id"] for a in result["applied"]] == [world["e_known"]]
    assert [(r["evidence_id"], r["code"]) for r in result["refused"]] == [(world["e_new"], "plan_changed")]
    assert _action(repo.preview(), late)["mode"] == "auto"  # the late email was not applied

    from origenlab_api.v2.commands import CommandRefused

    with pytest.raises(CommandRefused) as refused:
        repo.apply(op, [{"evidence_id": str(uuid.uuid4()), "rule_id": "R1"}] * 11)
    assert refused.value.code == "batch_too_large"


def test_two_concurrent_applies_of_one_email_apply_it_once(db, world) -> None:
    import threading

    repo = _repo(db)
    pairs = _pairs(repo, [world["e_known"]])
    with _owner(db) as conn:
        other = conn.execute(
            "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
            "values (gen_random_uuid(), %s, 'Otra Admin', 'admin', 'active') returning id::text",
            (f"concurrente-{uuid.uuid4().hex[:8]}@example.test",)).fetchone()[0]
    results: list[dict] = []
    barrier = threading.Barrier(2)

    def run(operator_id):
        barrier.wait()
        results.append(_repo(db).apply(_operator(operator_id), pairs))

    threads = [threading.Thread(target=run, args=(o,)) for o in (world["admin"], other)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    applied = [a for r in results for a in r["applied"]]
    refused = [x for r in results for x in r["refused"]]
    assert len(applied) == 1
    assert [x["code"] for x in refused] in (["already_applied"], ["plan_changed"])
    assert _one(db, "select count(*) from crm.opportunity where origin_source_record_id = %s", world["e_known"])[0] == 1


def test_undo_r4_removes_the_domain_so_the_next_email_can_create_again(db, world) -> None:
    repo = _repo(db)
    r4 = _apply_only(db, world, world["e_new"])
    undone = repo.undo(_operator(world["admin"]), r4["command_receipt_id"], "dominio equivocado")
    assert any(s["command"] == "remove-organization-domain" for s in undone["steps"])
    assert _one(db, "select count(*) from crm.organization_domain where domain_norm = %s and removed_at is null",
                world["new_domain"])[0] == 0
    with _owner(db) as conn:
        again = _mail(conn, thread=f"t-again-{world['tag']}", direction_hint="external", sender="contacto@origenlab.cl",
                      recipients=f"otra@{world['new_domain']}", subject="Cotización",
                      documents=[_doc("CN09977.pdf", ["CN09977"])])
    assert (_action(repo.preview(), again)["rule_id"], _action(repo.preview(), again)["mode"]) == ("R4", "auto")


def test_undo_r4_keeps_an_institution_another_live_case_uses(db, world) -> None:
    repo = _repo(db)
    r4 = _apply_only(db, world, world["e_new"])
    with _owner(db) as conn:
        second = _mail(conn, thread=f"t-two-{world['tag']}", direction_hint="external", sender="contacto@origenlab.cl",
                       recipients=f"otra@{world['new_domain']}", subject="Cotización",
                       documents=[_doc("CN09966.pdf", ["CN09966"])])
    r3 = _apply_only(db, world, second)
    assert (r3["rule_id"], r3["organization_id"]) == ("R3", r4["organization_id"])
    undone = repo.undo(_operator(world["admin"]), r4["command_receipt_id"], "no era nuevo")
    assert undone["organization_kept"]
    assert _one(db, "select status from crm.organization where id = %s", r4["organization_id"])[0] == "active"


def test_a_stage_a_person_set_afterwards_is_not_corrected_and_the_refusal_is_a_409(db, world) -> None:
    from origenlab_api.v2.commands import CommandRefused

    repo = _repo(db)
    r3 = _apply_only(db, world, world["e_known"])
    with _owner(db) as conn:
        lost = _mail(conn, thread=world["t1"], direction_hint=None, sender=f"compras@{world['client_domain']}",
                     recipients="contacto@origenlab.cl", subject="ya fue gestionada por otro proveedor")
    r6 = _apply_only(db, world, lost)
    with _owner(db) as conn:
        seq = conn.execute("select max(seq) + 1 from crm.domain_event where aggregate_id = %s",
                           (r3["case_id"],)).fetchone()[0]
        conn.execute(
            "insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, "
            "actor_kind, actor_operator_id) values ('opportunity', %s, %s, 'opportunity.staged', 1, "
            "'{\"from_stage\": \"quoting\", \"to_stage\": \"lost\"}', 'operator', %s)",
            (r3["case_id"], seq, world["admin"]))
    with pytest.raises(CommandRefused) as refused:
        repo.undo(_operator(world["admin"]), r6["command_receipt_id"], "deshacer")
    assert (refused.value.status_code, refused.value.code) == (409, "stage_correction_refused")
    assert _one(db, "select stage from crm.opportunity where id = %s", r3["case_id"])[0] == "lost"


# ─────────────────────────────────────────────── the automatic run (mail_rules_auto.py) ──
#
# One test, last in this module: the switch is one row-set for the whole database, so its
# "never switched on" start can be observed only once, and other tests' leftover emails may be
# acted on by a pass — the assertions name this test's own emails only.


@pytest.mark.serial_db  # run_once scans the whole shared cluster: a parallel test's mail would be applied
def test_the_automatic_run_handles_r1_r2_and_r7_only_while_switched_on_and_stays_undoable(db, world) -> None:
    from origenlab_api.v2.commands import CommandRefused
    from origenlab_api.v2.mail_rules_auto import AutoMailRules

    repo = _repo(db)
    auto = AutoMailRules(repo, interval_seconds=60)
    admin = _operator(world["admin"])

    # Never switched on: off, and a pass writes nothing.
    assert auto.state()["enabled"] is False and auto.state()["changed_by"] is None
    before = _counts(db)
    assert auto.run_once()["skipped"] == "off"
    assert _counts(db) == before

    # A case to link to, opened the manual way (R3 is never automatic).
    case_id = _apply_only(db, world, world["e_known"])["case_id"]
    serial = world["number"][1:-3]
    with _owner(db) as conn:
        reply = _mail(conn, thread=world["t1"], direction_hint=None, sender=f"compras@{world['client_domain']}",
                      recipients="contacto@origenlab.cl", subject="RE: Cotización")
        by_number = _mail(conn, thread=f"thread-n-{world['tag']}", direction_hint=None,
                          sender=f"jefa@{world['client_domain']}", recipients="contacto@origenlab.cl",
                          subject="Consulta", documents=[_doc(f"CN{serial}5-copia.pdf", [f"CN{serial}5"])])
        fresh_rfq = _mail(conn, thread=f"thread-rfq-{world['tag']}", direction_hint=None,
                          sender="persona@gmail.com", recipients="contacto@origenlab.cl",
                          subject="Solicitud de cotización de reactivos")
        _requester_check(conn, fresh_rfq)
    planned = {a["evidence_id"]: (a["rule_id"], a["mode"]) for a in repo.preview()["actions"]}
    assert planned[reply] == ("R1", "auto") and planned[by_number] == ("R2", "auto")
    assert planned[fresh_rfq] == ("R7", "auto")
    assert planned[world["e_new"]] == ("R4", "auto")

    # Only an admin switches it, with a note; switching to the current state is refused.
    sales = _operator(world["admin"], role="sales")
    with pytest.raises(CommandRefused) as refused:
        auto.set_enabled(sales, True, "x", "auto-sales")
    assert refused.value.status_code == 403
    with pytest.raises(CommandRefused):
        auto.set_enabled(admin, True, " ", "auto-blank")
    on = auto.set_enabled(admin, True, "probado en vista previa", f"auto-on-{world['tag']}")
    assert on["enabled"] is True
    assert auto.set_enabled(admin, True, "probado en vista previa", f"auto-on-{world['tag']}")["replayed"] is True
    with pytest.raises(CommandRefused) as unchanged:
        auto.set_enabled(admin, True, "otra vez", f"auto-on2-{world['tag']}")
    assert unchanged.value.code == "auto_mail_rules_unchanged"
    state = auto.state()
    assert (state["enabled"], state["changed_by"], state["note"], state["rules"]) == (
        True, "Admin Ficticio", "probado en vista previa",
        ["R1", "R11", "R12", "R13", "R14", "R15", "R16", "R17", "R2", "R3", "R4", "R7", "R9"])

    # One pass: R1/R2 link, R7 opens the RFQ lead and (2026-10-15) R4 opens the new domain's case too.
    result = auto.run_once()
    assert result["skipped"] is None and result["applied"] >= 3, result
    links = _one(db, "select array_agg(source_record_id::text order by source_record_id) from crm.opportunity_evidence "
                     "where opportunity_id = %s and unlinked_at is null and source_record_id in (%s, %s)",
                 case_id, reply, by_number)[0]
    assert sorted(links) == sorted([reply, by_number])
    assert _one(db, "select count(*) from crm.opportunity where origin_source_record_id = %s", world["e_new"])[0] == 1
    assert _one(db, "select stage, organization_id from crm.opportunity where origin_source_record_id = %s",
                fresh_rfq) == ("lead", None)
    assert not any(a["evidence_id"] == world["e_new"] for a in repo.preview()["actions"])  # applied, not planned again
    applied = {a["evidence_id"]: a for a in repo.applied()}
    assert applied[reply]["automatic"] is True and applied[reply]["applied_by"] == "Admin Ficticio"
    assert _one(db, "select bool_and(actor_kind = 'worker'), bool_and(payload -> 'attribution' ->> 'trigger' = 'automatic') "
                    "from crm.domain_event where command_receipt_id = %s", applied[reply]["receipt_id"]) == (True, True)
    assert auto.last_run()["applied"] == result["applied"]
    assert auto.run_once()["applied"] == 0  # nothing twice

    # Undo works exactly as for an action an admin applied.
    repo.undo(admin, applied[by_number]["receipt_id"], "era otra cotización")
    assert _one(db, "select unlinked_at is not null from crm.opportunity_evidence where source_record_id = %s",
                by_number)[0] is True
    assert auto.run_once()["applied"] == 0  # an undone action never comes back

    # Off: the next reply waits.
    auto.set_enabled(admin, False, "pausa", f"auto-off-{world['tag']}")
    with _owner(db) as conn:
        later = _mail(conn, thread=world["t1"], direction_hint=None, sender=f"compras@{world['client_domain']}",
                      recipients="contacto@origenlab.cl", subject="RE: RE: Cotización")
    assert auto.run_once()["skipped"] == "off"
    assert _action(repo.preview(), later)["rule_id"] == "R1"

    # On again, then the admin who switched it on loses the role: the run stops and says why,
    # and another admin may switch it on in their own name.
    auto.set_enabled(admin, True, "seguimos", f"auto-on3-{world['tag']}")
    with _owner(db) as conn:
        conn.execute("update platform.operator set role = 'sales' where id = %s", (world["admin"],))
        other = conn.execute(
            "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
            "values (gen_random_uuid(), %s, 'Otra Admin', 'admin', 'active') returning id::text",
            (f"otra-auto-{world['tag']}@example.test",)).fetchone()[0]
    assert auto.run_once()["skipped"] == "operator_not_admin"
    assert "ya no es un administrador activo" in auto.state()["blocked"]
    auto.set_enabled(_operator(other), True, "la retomo yo", f"auto-on4-{world['tag']}")
    assert auto.run_once()["skipped"] is None
    assert _one(db, "select operator_id::text from platform.command_receipt "
                    "where command_name = 'apply_mail_rule' and response_body ->> 'evidence_id' = %s", later)[0] == other
    auto.set_enabled(_operator(other), False, "fin de la prueba", f"auto-off2-{world['tag']}")


def _reading(conn, source_record_id, value_norm, value):
    conn.execute("insert into evidence.assertion (source_record_id, kind, value_norm, value) "
                 "values (%s, 'message_triage', %s, %s::jsonb)", (source_record_id, value_norm, json.dumps(value)))


def test_a_payment_notice_naming_the_quote_in_its_body_is_filed_on_the_won_case_and_undoes(db, world) -> None:
    repo = _repo(db)
    op = _operator(world["admin"])
    case_id = _apply_only(db, world, world["e_known"])["case_id"]
    with _owner(db) as conn:
        order = _mail(conn, thread=world["t1"], direction_hint=None, sender=f"compras@{world['client_domain']}",
                      recipients="contacto@origenlab.cl", subject="Orden de compra", documents=[_doc("OC 4500123.pdf")])
    _apply_only(db, world, order)
    assert _one(db, "select stage from crm.opportunity where id = %s", case_id) == ("won",)

    with _owner(db) as conn:
        notice = _mail(conn, thread=f"thread-pay-{world['tag']}", direction_hint=None,
                       sender="transferencias@banco-ficticio.test", recipients="contacto@origenlab.cl",
                       subject="Aviso de transferencia de fondos")
        _reading(conn, notice, "quote_refs:v1", {"version": 1, "tokens": [world["number"]]})
    filed = _apply_only(db, world, notice)
    assert (filed["rule_id"], filed["case_id"]) == ("R2", case_id)
    assert _one(db, "select stage, closed_at is not null from crm.opportunity where id = %s", case_id) == ("won", True)
    assert _one(db, "select count(*) from crm.opportunity_evidence where opportunity_id = %s and source_record_id = %s "
                    "and unlinked_at is null", case_id, notice) == (1,)

    repo.undo(op, filed["command_receipt_id"], "no era de este caso")
    assert _one(db, "select unlinked_at is not null from crm.opportunity_evidence "
                    "where opportunity_id = %s and source_record_id = %s", case_id, notice) == (True,)


def test_the_ai_reading_opens_a_request_whose_subject_has_no_quote_word(db, world) -> None:
    with _owner(db) as conn:
        ask = _mail(conn, thread=f"thread-ai-{world['tag']}", direction_hint=None,
                    sender=f"ana@laboratorio-{world['tag']}.test", recipients="contacto@origenlab.cl",
                    subject="Consulta por equipos")
        _requester_check(conn, ask)
        _reading(conn, ask, "triage:v3", {"class": "business_other", "reading": {"intent": "quote_request"}})
    opened = _apply_only(db, world, ask)
    assert opened["rule_id"] == "R7"
    assert any("IA" in r for r in opened["reasons"])
    assert _one(db, "select stage from crm.opportunity where id = %s", opened["case_id"]) == ("lead",)


def _forward(conn, email, *, origin="origenlab", company=None, item="Gradillas"):
    """A request forwarded to contacto@: our copy (a gmail_message record) and the worker's reading."""
    gid = uuid.uuid4().hex[:12]
    mid = conn.execute("insert into evidence.source_record (kind, dedupe_key, payload) values ('gmail_message', %s, "
                       "%s::jsonb) returning id::text",
                       (f"gmail_message:fw-{gid}", json.dumps({"gmail_message_id": gid,
                                                                "sent_at": "2026-10-13T12:00:00+00:00"}))).fetchone()[0]
    payload = {"original_email": email, "original_name": "Ana Ficticia", "company": company, "requested_item": item,
               "origin": origin, "subject": f"Fwd: {item}", "forwarded_message_id": mid, "reading": "forwarded:v1"}
    fid = conn.execute("insert into evidence.source_record (kind, dedupe_key, payload) values ('forwarded_request', %s, "
                       "%s::jsonb) returning id::text", (f"forwarded_request:{mid}", json.dumps(payload))).fetchone()[0]
    return fid, mid


def test_r11_a_new_customers_forward_becomes_a_request_and_quitar_removes_it(db, world) -> None:
    repo = _repo(db)
    with _owner(db) as conn:
        fid, mid = _forward(conn, f"ana@nuevo-{world['tag']}.test", company=f"Laboratorio Nuevo {world['tag']}")
    opened = _apply_only(db, world, fid)
    assert opened["rule_id"] == "R11"
    case_id = opened["case_id"]
    assert _one(db, "select stage from crm.opportunity where id = %s", case_id) == ("lead",)
    assert _one(db, "select review_status from evidence.source_record where id = %s", fid) == ("promoted",)
    assert _one(db, "select count(*) from crm.opportunity_evidence where opportunity_id = %s and source_record_id = %s",
                case_id, mid) == (1,)

    repo.undo(_operator(world["admin"]), opened["command_receipt_id"], "no era una solicitud")
    assert _one(db, "select stage, close_reason from crm.opportunity where id = %s", case_id) == (
        "abandoned", "discarded_by_correction")
    assert _one(db, "select review_status from evidence.source_record where id = %s", fid) == ("rejected",)
    assert not any(a["evidence_id"] == fid for a in repo.preview()["actions"])


def test_r11_a_forward_from_a_customer_already_on_a_case_joins_it_and_quitar_hands_it_back(db, world) -> None:
    repo = _repo(db)
    case_id = _apply_only(db, world, world["e_known"])["case_id"]
    with _owner(db) as conn:
        reply = _mail(conn, thread=world["t1"], direction_hint=None, sender=f"Compras <compras@{world['client_domain']}>",
                      recipients="contacto@origenlab.cl", subject="RE: Cotización")
    _apply_only(db, world, reply)
    with _owner(db) as conn:
        fid, mid = _forward(conn, f"compras@{world['client_domain']}")
        lab, lab_mid = _forward(conn, f"compras@{world['client_domain']}", origin="labdelivery")
    joined = _apply_only(db, world, fid)
    assert (joined["rule_id"], joined["case_id"]) == ("R11", case_id)
    assert _one(db, "select count(*) from crm.opportunity_evidence where opportunity_id = %s and source_record_id = %s "
                    "and unlinked_at is null", case_id, mid) == (1,)
    # Its Labdelivery-mailbox copy is the same request: handled with it, and never linked.
    assert _one(db, "select review_status from evidence.source_record where id = %s", lab) == ("promoted",)
    assert _one(db, "select count(*) from crm.opportunity_evidence where source_record_id = %s", lab_mid) == (0,)

    repo.undo(_operator(world["admin"]), joined["command_receipt_id"], "era otra solicitud")
    assert _one(db, "select unlinked_at is not null from crm.opportunity_evidence "
                    "where opportunity_id = %s and source_record_id = %s", case_id, mid) == (True,)
    assert _one(db, "select review_status from evidence.source_record where id = %s", fid) == ("pending",)


def test_hoy_lists_the_automatic_actions_and_sales_removes_only_those(db, world) -> None:
    from origenlab_api.v2.commands import CommandRefused

    repo = _repo(db)
    with _owner(db) as conn:
        fid, _ = _forward(conn, f"luis@otro-{world['tag']}.test", item="Pipetas")
        seller = conn.execute(
            "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
            "values (gen_random_uuid(), %s, 'Vendedora', 'sales', 'active') returning id::text",
            (f"v-{world['tag']}@example.test",)).fetchone()[0]
    actions, _display, _snapshot = repo.plan()
    action = next(a for a in actions if a.evidence_id == fid)
    auto = repo.apply_planned(_operator(world["admin"]), action, automatic=True)
    manual = _apply_only(db, world, world["e_known"])  # «Aplicar» by an admin: not removable from Hoy

    listed = {i["receipt_id"]: i for i in repo.recent_automatic(hours=48)}
    assert auto["command_receipt_id"] in listed and manual["command_receipt_id"] not in listed
    item = listed[auto["command_receipt_id"]]
    assert (item["rule_id"], item["kind"], item["case_id"]) == ("R11", "opened", auto["case_id"])
    assert "Pipetas" in item["what"]

    with pytest.raises(CommandRefused) as refused:
        repo.remove_automatic(_operator(seller, "sales"), manual["command_receipt_id"])
    assert refused.value.code == "not_automatic"
    repo.remove_automatic(_operator(seller, "sales"), auto["command_receipt_id"])
    assert _one(db, "select stage from crm.opportunity where id = %s", auto["case_id"]) == ("abandoned",)
    assert auto["command_receipt_id"] not in {i["receipt_id"] for i in repo.recent_automatic(hours=48)}


def test_r12_the_context_matcher_places_a_new_thread_on_its_case_and_quitar_unlinks_it(db, world) -> None:
    repo = _repo(db)
    case_id = _apply_only(db, world, world["e_known"])["case_id"]
    with _owner(db) as conn:
        ask = _mail(conn, thread=f"thread-ctx-{world['tag']}", direction_hint=None,
                    sender=f"Compras <compras@{world['client_domain']}>", recipients="contacto@origenlab.cl",
                    subject="Cotización: ¿plazo de entrega?")
        _requester_check(conn, ask)
        _reading(conn, ask, "case_match:v1", {"decision": "propose", "pick_state": "ran",
                 "candidates": [{"opportunity_id": case_id, "score": 5, "signals": ["domain", "product"]}],
                 "pick": {"choice": case_id, "confidence": "high", "reason_es": "mismo producto y contacto"}})
    placed = _apply_only(db, world, ask)
    assert (placed["rule_id"], placed["case_id"]) == ("R12", case_id)
    assert _one(db, "select count(*) from crm.opportunity where title like %s", "%¿plazo%") == (0,)  # no new request
    repo.undo(_operator(world["admin"]), placed["command_receipt_id"], "no era")
    assert _one(db, "select unlinked_at is not null from crm.opportunity_evidence "
                    "where opportunity_id = %s and source_record_id = %s", case_id, ask) == (True,)


def test_r13_a_quote_sent_on_a_new_thread_lands_on_the_forwarded_request_and_undoes(db, world) -> None:
    repo = _repo(db)
    customer = f"esteban@lab-{world['tag']}.test"
    with _owner(db) as conn:
        fid, _ = _forward(conn, customer, company=f"Laboratorio Ficticio {world['tag']} EIRL", item="Tabletas")
    request = _apply_only(db, world, fid)
    case_id = request["case_id"]
    serial = int(world["number"][1:5]) + 7
    with _owner(db) as conn:
        quote_mail = _mail(conn, thread=f"thread-q-{world['tag']}", direction_hint="external",
                           sender="contacto@origenlab.cl", recipients=f"Esteban <{customer}>", subject="Cotización tabletas",
                           documents=[_doc(f"CN0{serial}-Esteban – Laboratorio.pdf", [f"CN0{serial}"])])
    filed = _apply_only(db, world, quote_mail)
    assert (filed["rule_id"], filed["case_id"]) == ("R13", case_id)
    assert _one(db, "select stage from crm.opportunity where id = %s", case_id) == ("quoting",)
    assert _one(db, "select count(*) from crm.quote where opportunity_id = %s", case_id) == (1,)

    repo.undo(_operator(world["admin"]), filed["command_receipt_id"], "no era esta solicitud")
    assert _one(db, "select stage from crm.opportunity where id = %s", case_id) == ("lead",)


# ─────────────────────────────── web quote form: a self-sent message opens a case like inbound ──


def _web_form_mail(conn, *, reply_to, marked=True, subject="Solicitud de cotización — sitio web — U. Ficticia"):
    """A captured self-sent contacto@ → contacto@ message with a stored Reply-To participant.

    `marked=True` adds the triage reading the worker writes for a web-form message (reason
    `header:x-origenlab-origin`); the raw header itself is not stored anywhere."""
    record = _mail(conn, thread=f"t-web-{uuid.uuid4().hex[:8]}", direction_hint="internal_only",
                   sender="contacto@origenlab.cl", recipients="contacto@origenlab.cl", subject=subject,
                   direction="outbound")
    message_id = conn.execute(
        "select cm.id from comms.message cm join evidence.source_record sr "
        "on cm.provider_message_id = sr.payload ->> 'gmail_message_id' where sr.id = %s", (record,)
    ).fetchone()[0]
    conn.execute("update comms.message set subject = %s where id = %s", (subject, message_id))
    for role, address in (("from", "contacto@origenlab.cl"), ("to", "contacto@origenlab.cl"),
                          ("reply_to", reply_to)):
        conn.execute("insert into comms.message_participant (message_id, role, address_norm) "
                     "values (%s, %s, %s)", (message_id, role, address))
    if marked:
        conn.execute(
            "insert into evidence.assertion (source_record_id, kind, value_norm, value) "
            "values (%s, 'message_triage', 'triage:v4', %s::jsonb)",
            (record, json.dumps({"class": "quote_request", "reasons": ["header:x-origenlab-origin"]})))
    return record


def test_web_form_message_is_planned_as_r7_from_the_reply_to_sender(db, world) -> None:
    with _owner(db) as conn:
        form = _web_form_mail(conn, reply_to=f"ana@{world['new_domain']}")
        plain = _mail(conn, thread=f"t-eq-{world['tag']}", direction_hint=None,
                      sender=f"Ana <ana@{world['new_domain']}>", recipients="contacto@origenlab.cl",
                      subject="Solicitud de cotización — sitio web — U. Ficticia")
        _requester_check(conn, plain)  # the form needs no wait: its Reply-To is the requester
    preview = _repo(db).preview()
    got, want = _action(preview, form), _action(preview, plain)
    assert got["rule_id"] == "R7" == want["rule_id"]
    assert got["mode"] == want["mode"]
    r7 = _apply_only(db, world, form)
    assert r7["rule_id"] == "R7"
    assert _one(db, "select stage from crm.opportunity where id = %s", r7["case_id"]) is not None


def test_a_web_form_case_reads_the_visitor_as_its_contact(db, world) -> None:
    """The case R7 opens from a web-form mail: its card, its emails and the Cotizador's «Para»
    read the Reply-To as the client who wrote, not contacto@ as an internal email."""
    import psycopg
    from psycopg.rows import dict_row

    from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository
    from origenlab_api.v2.quotes.reads import _CASE_PEOPLE_SQL, email_people

    visitor = f"ana@{world['new_domain']}"
    with _owner(db) as conn:
        form = _web_form_mail(conn, reply_to=visitor,
                              subject=f"Solicitud de cotización — sitio web — U. Ficticia {world['tag']}")
    case_id = _apply_only(db, world, form)["case_id"]

    workspace = CrmWorkspaceRepository(psycopg.connect, runtime_dsn(db))
    [card] = [c for c in workspace.pipeline()["items"] if c["opportunity_id"] == case_id]
    assert card["title"] == f"Solicitud de cotización — sitio web — U. Ficticia {world['tag']}"
    assert (card["contact"]["source"], card["contact"]["address"]) == ("web_form", visitor)
    assert card["last_contact"]["inbound"] is not None and card["last_contact"]["outbound"] is None

    [thread] = workspace.opportunity_threads(case_id)["threads"]
    assert thread["kind"] == "cliente" and thread["messages"][0]["direction"] == "inbound"
    # The reader opens an email of the case's threads, and nothing else.
    message_id = thread["messages"][0]["message_id"]
    assert workspace.opportunity_message_source(case_id, message_id) is not None
    assert workspace.opportunity_message_source(case_id, "no-such-message") is None

    with _owner(db) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_CASE_PEOPLE_SQL, {"case": case_id})
        assert [p["email"] for p in email_people(cur.fetchall())] == [visitor]


def test_our_reply_to_the_visitor_stays_outbound_and_answers_the_web_form_case(db, world) -> None:
    """contacto@ answers the visitor on the form's thread (no marker, no Reply-To): that email is
    ours, the newest `last_contact.outbound`, so Hoy reads the case as answered, not «Responder»."""
    import psycopg

    from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository

    visitor = f"ana@{world['new_domain']}"
    with _owner(db) as conn:
        form = _web_form_mail(conn, reply_to=visitor)
    case_id = _apply_only(db, world, form)["case_id"]
    with _owner(db) as conn:
        thread = conn.execute("select payload->>'gmail_thread_id' from evidence.source_record where id = %s",
                              (form,)).fetchone()[0]
        answer = _mail(conn, thread=thread, direction_hint=None, sender="OrigenLab <contacto@origenlab.cl>",
                       recipients=visitor, subject="Re: Solicitud de cotización — sitio web — U. Ficticia",
                       direction="outbound")
        conn.execute("update evidence.source_record set payload = jsonb_set(payload, '{sent_at}', "
                     "'\"2026-10-02T15:00:00+00:00\"') where id = %s", (answer,))
        conn.execute("insert into comms.message_participant (message_id, role, address_norm) "
                     "select cm.id, r.role, r.address from comms.message cm, "
                     "(values ('from', 'contacto@origenlab.cl'), ('to', %s)) r(role, address) "
                     "join evidence.source_record sr on sr.id = %s "
                     "where cm.provider_message_id = sr.payload->>'gmail_message_id'", (visitor, answer))

    workspace = CrmWorkspaceRepository(psycopg.connect, runtime_dsn(db))
    [card] = [c for c in workspace.pipeline()["items"] if c["opportunity_id"] == case_id]
    sent, received = card["last_contact"]["outbound"], card["last_contact"]["inbound"]
    assert sent is not None and sent["at"] == "2026-10-02T15:00:00+00:00"
    assert received is not None and received["at"] < sent["at"]  # Hoy: our turn is done
    assert card["contact"]["source"] == "web_form" and card["contact"]["address"] == visitor
    [thread_out] = workspace.opportunity_threads(case_id)["threads"]
    assert [m["direction"] for m in thread_out["messages"]] == ["outbound", "inbound"]


def test_self_sent_message_without_the_web_form_marker_is_not_an_r7_request(db, world) -> None:
    with _owner(db) as conn:
        unmarked = _web_form_mail(conn, reply_to=f"ana@{world['new_domain']}", marked=False)
    preview = _repo(db).preview()
    assert all(a["evidence_id"] != unmarked or a["rule_id"] != "R7" for a in preview["actions"])


def test_cap_seed_counts_only_form_mails_captured_in_the_last_24_hours(db) -> None:
    import psycopg
    from origenlab_api.v2.web_quote_form import WebQuoteFormCaps, sent_timestamps_last_24h

    dsn = runtime_dsn(db)
    before = len(sent_timestamps_last_24h(psycopg.connect, dsn))
    with _owner(db) as conn:
        _web_form_mail(conn, reply_to="a@seed.test")
        _web_form_mail(conn, reply_to="b@seed.test", marked=False)  # the subject alone counts
        old = _web_form_mail(conn, reply_to="c@seed.test")
        conn.execute("update comms.message set internal_date = now() - interval '25 hours' "
                     "where provider_message_id = (select payload ->> 'gmail_message_id' "
                     "from evidence.source_record where id = %s)", (old,))
        # An ordinary self-sent mail with another subject, and a form-subject mail from someone else.
        _web_form_mail(conn, reply_to="d@seed.test", marked=False, subject="Recordatorio interno")
        stranger = _web_form_mail(conn, reply_to="e@seed.test", marked=False,
                                  subject="Solicitud de cotización — sitio web — falso")
        conn.execute("delete from comms.message_participant where role = 'from' and message_id = "
                     "(select cm.id from comms.message cm join evidence.source_record sr on "
                     "cm.provider_message_id = sr.payload ->> 'gmail_message_id' where sr.id = %s)",
                     (stranger,))
    stamps = sent_timestamps_last_24h(psycopg.connect, dsn)
    assert len(stamps) == before + 2
    caps = WebQuoteFormCaps()
    caps.seed_from_timestamps(stamps)
    assert len(caps.global_deque) == min(len(stamps), caps.GLOBAL_LIMIT)


# ── fix: request cases must have the sender as a participant (owner, 2026-10-22) ──
#
# client_open_cases looks up open cases by contact_point_id; if no participant row exists for the
# sender the lookup misses and create-quote opens a duplicate.  These three tests verify the fix.


def test_r7_known_institution_request_adds_sender_as_participant_and_client_open_cases_finds_it(db, world) -> None:
    """R7 (known institution path) adds the sender's email as a quote_recipient participant so
    client_open_cases can locate the request case when the operator later opens «+ Nueva»."""
    import psycopg
    from origenlab_api.v2.quotes.reads import client_open_cases

    sender_email = f"investigadora2-{world['tag']}@{world['client_domain']}"
    with _owner(db) as conn:
        request = _mail(
            conn, thread=f"t-rfq-part-{world['tag']}", direction_hint=None,
            sender=sender_email, recipients="contacto@origenlab.cl",
            subject="Solicitud de cotización de instrumentos",
        )
    r7 = _apply_only(db, world, request)
    assert r7["rule_id"] == "R7"
    case_id = r7["case_id"]

    # A quote_recipient participant row with the sender's address must be present.
    cp_id, role, is_primary = _one(
        db,
        "select cp.id::text, p.role, p.is_primary "
        "from crm.opportunity_participant p "
        "join crm.contact_point cp on cp.id = p.contact_point_id "
        "where p.opportunity_id = %s and cp.value_norm = %s and p.valid_to is null",
        case_id, sender_email,
    )
    assert role == "quote_recipient" and is_primary is True

    # client_open_cases must find the case by contact_point, the path create-quote uses.
    with psycopg.connect(runtime_dsn(db), autocommit=True) as conn:
        conn.execute("set role origenlab_api")
        with conn.cursor() as cur:
            cases = client_open_cases(cur, contact_point_id=cp_id)
    assert any(c["id"] == case_id for c in cases), (
        f"case {case_id!r} not in {[c['id'] for c in cases]}")


def test_r7_free_mail_request_adds_sender_as_participant(db, world) -> None:
    """R7 (free-mail / unknown-domain path) also adds the sender as a participant."""
    sender_email = "elena@gmail.com"
    with _owner(db) as conn:
        request = _mail(
            conn, thread=f"t-free-rfq-part-{world['tag']}", direction_hint=None,
            sender=f"Elena Ficticia <{sender_email}>", recipients="contacto@origenlab.cl",
            subject="Solicitud de cotización de equipos de laboratorio",
        )
        _requester_check(conn, request)
    r7 = _apply_only(db, world, request)
    assert r7["rule_id"] == "R7"
    case_id = r7["case_id"]

    count = _one(
        db,
        "select count(*) from crm.opportunity_participant p "
        "join crm.contact_point cp on cp.id = p.contact_point_id "
        "where p.opportunity_id = %s and cp.value_norm = %s and p.valid_to is null",
        case_id, sender_email,
    )[0]
    assert count == 1
