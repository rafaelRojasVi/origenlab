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


def test_the_automatic_run_links_r1_and_r2_only_while_switched_on_and_stays_undoable(db, world) -> None:
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
    planned = {a["evidence_id"]: (a["rule_id"], a["mode"]) for a in repo.preview()["actions"]}
    assert planned[reply] == ("R1", "auto") and planned[by_number] == ("R2", "auto")
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
        True, "Admin Ficticio", "probado en vista previa", ["R1", "R2"])

    # One pass: R1 and R2 are linked, as the system, on behalf of the admin; R4 waits for a person.
    result = auto.run_once()
    assert result["skipped"] is None and result["applied"] >= 2, result
    links = _one(db, "select array_agg(source_record_id::text order by source_record_id) from crm.opportunity_evidence "
                     "where opportunity_id = %s and unlinked_at is null and source_record_id in (%s, %s)",
                 case_id, reply, by_number)[0]
    assert sorted(links) == sorted([reply, by_number])
    assert _one(db, "select count(*) from crm.opportunity where origin_source_record_id = %s", world["e_new"])[0] == 0
    assert _action(repo.preview(), world["e_new"])["mode"] == "auto"
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
