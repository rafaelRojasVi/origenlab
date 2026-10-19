"""R14 and R15 against a real database (spec 2026-10-19): the obvious decisions, through the existing
commands, as the system, once, and undone with «Deshacer»; and `unconfirm-organization-record`.

Runs in a disposable `origenlab_test_<hex>` as the unprivileged `origenlab_api` role. Every address,
domain, institution and number is fictitious.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

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
        return conn.execute(sql, args).fetchone()


def _repo(db, **kw):
    import psycopg

    from origenlab_api.v2.mail_rules_repository import MailRulesRepository

    return MailRulesRepository(psycopg.connect, runtime_dsn(db), commands_enabled=True, **kw)


def _operator(operator_id, role="admin"):
    from origenlab_api.v2.identity import OperatorIdentity

    return OperatorIdentity(operator_id=operator_id, email_norm="admin@example.test",
                            display_name="Admin Ficticio", role=role, status="active")


def _doc(filename, cn_tokens=()):
    return {"filename": filename, "sha256": hashlib.sha256(filename.encode() + uuid.uuid4().bytes).hexdigest(),
            "cn_tokens": list(cn_tokens)}


#: The quote year the rules read from a message sent now (the Santiago year of `sent_at`).
YY = datetime.now(ZoneInfo("America/Santiago")).year % 100


def _mail(conn, *, thread, sender, recipients, subject, documents=(), direction="outbound", days_ago=0):
    gid = uuid.uuid4().hex[:16]
    sent_at = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()
    payload = {"gmail_message_id": gid, "gmail_thread_id": thread, "direction_hint": "external",
               "sender": sender, "recipients": recipients, "subject_raw": subject,
               "sent_at": sent_at, "documents": list(documents)}
    record = conn.execute(
        "insert into evidence.source_record (kind, dedupe_key, payload, source_uri) "
        "values ('gmail_message', %s, %s::jsonb, %s) returning id::text",
        (f"gmail_message:{gid}", json.dumps(payload), f"gmail://msg/{gid}")).fetchone()[0]
    mailbox = conn.execute("insert into comms.mailbox (address_norm) values (%s) returning id",
                           (f"mbox-{gid}@example.test",)).fetchone()[0]
    conn.execute("insert into comms.message (mailbox_id, provider_message_id, provider_thread_id, direction, "
                 "internal_date) values (%s, %s, %s, %s, now())", (mailbox, gid, thread, direction))
    return record


_SERIALS = itertools.count(2000 + (uuid.uuid4().int % 300) * 10, 10)


@pytest.fixture
def world(db):
    tag = uuid.uuid4().hex[:8]
    serial = next(_SERIALS)
    w = {"tag": tag, "serial": serial, "client_domain": f"cliente-{tag}.test", "new_domain": f"nuevo-{tag}.test"}
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
    return w


def _apply(repo, admin, evidence_id, rule_id):
    planned = {(a["evidence_id"], a["rule_id"]): a for a in repo.preview()["actions"]}
    assert planned[(evidence_id, rule_id)]["mode"] == "auto", planned.get((evidence_id, rule_id))
    result = repo.apply(_operator(admin), [{"evidence_id": evidence_id, "rule_id": rule_id}])
    assert not result["refused"], result["refused"]
    return result["applied"][0]


def _planned(repo, evidence_id):
    return {a["rule_id"] for a in repo.preview()["actions"] if a["evidence_id"] == evidence_id}


def _name_requester(db, repo, world, case_id):
    """A person names the client as the case's requesting institution (`add_case_organization`)."""
    from origenlab_api.v2.case_commands import ADD_CASE_ORGANIZATION, AddCaseOrganizationBody, validated_case

    version = _one(db, "select version from crm.opportunity where id = %s", case_id)[0]
    org_version = _one(db, "select version from crm.organization where id = %s", world["client"])[0]
    fields = validated_case(ADD_CASE_ORGANIZATION, AddCaseOrganizationBody(
        opportunity_id=case_id, opportunity_version=version, organization_id=world["client"],
        organization_version=org_version, role="requesting_institution", note="la persona nombra la institución"))
    repo.execute(command_name=ADD_CASE_ORGANIZATION, operator=_operator(world["admin"]), fields=fields,
                 idempotency_key=f"add-org-{case_id}", digest="d" * 64)


# ------------------------------------------------------------------ R14


def test_r14_records_the_quote_an_earlier_link_left_unrecorded_once_and_undoes_without_unlinking(db, world) -> None:
    repo = _repo(db)
    s = world["serial"]
    with _owner(db) as conn:
        first = _mail(conn, thread=f"t1-{world['tag']}", sender="contacto@origenlab.cl",
                      recipients=f"compras@{world['client_domain']}", subject="Cotización",
                      documents=[_doc(f"CN0{s}-cliente.pdf", [f"CN0{s}"])])
    case_id = _apply(repo, world["admin"], first, "R3")["case_id"]
    # A second email on a new thread names the first quote and carries a new one: R2 links it by the
    # known number and records nothing — the 2026-10 «Por decidir» row.
    with _owner(db) as conn:
        both = _mail(conn, thread=f"t2-{world['tag']}", sender="Tatiana <contacto@origenlab.cl>",
                     recipients=f"compras@{world['client_domain']}", subject="Cotizaciones",
                     documents=[_doc(f"CN0{s}-cliente.pdf", [f"CN0{s}"]),
                                _doc(f"CN0{s + 1}-cliente.pdf", [f"CN0{s + 1}"])])
    linked = _apply(repo, world["admin"], both, "R2")
    assert linked["case_id"] == case_id

    applied = _apply(repo, world["admin"], both, "R14")
    assert (applied["case_id"], applied["quote_number"]) == (case_id, f"0{s + 1}-{YY}")
    assert [step["command"] for step in applied["steps"]] == ["record_historical_quotation"]
    assert applied["created"]["opportunity_evidence_ids"] == []
    revision = applied["created"]["quote_revision_id"]
    assert _one(db, "select status from crm.quote_revision where id = %s", revision)[0] == "sent"
    # As the system, attributed to R14, keyed `mail-rule:<email>:R14`.
    assert applied["idempotency_key"] == f"mail-rule:{both}:R14"
    assert _one(db, "select bool_and(actor_kind = 'worker') from crm.domain_event "
                    "where payload -> 'attribution' ->> 'rule_id' = 'R14' "
                    "and payload -> 'attribution' ->> 'evidence_id' = %s", both)[0] is True
    assert "R14" not in _planned(repo, both)  # once

    undone = repo.undo(_operator(world["admin"]), applied["command_receipt_id"], "no era esta cotización")
    assert [step["command"] for step in undone["steps"]] == ["void_historical_quote_revision"]
    assert _one(db, "select status from crm.quote_revision where id = %s", revision)[0] == "void"
    # The email stays on its case (R2's link), and R14 does not come back.
    assert _one(db, "select count(*) from crm.opportunity_evidence where source_record_id = %s "
                    "and unlinked_at is null", both)[0] == 1
    assert "R14" not in _planned(repo, both)

    # A stale manual «Aplicar» of another rule on that email is still refused (R14's receipt counts).
    from origenlab_api.v2.mail_rules import AUTO, PlannedAction

    stale = repo.apply_planned(_operator(world["admin"]), PlannedAction(both, "R1", AUTO, ("viejo",)))
    assert stale["code"] == "already_applied"


def test_r14_leaves_a_backfilled_old_email_alone(db, world) -> None:
    # Sent 90 days ago, captured now, on a case still open: no old quote, no stage move.
    repo = _repo(db)
    s = world["serial"] + 7
    with _owner(db) as conn:
        case_id = conn.execute(
            "insert into crm.opportunity (title, stage, owner_operator_id) "
            "values ('Caso abierto ficticio', 'lead', %s) returning id::text", (world["admin"],)).fetchone()[0]
        old = _mail(conn, thread=f"t7-{world['tag']}", sender="contacto@origenlab.cl",
                    recipients=f"compras@{world['client_domain']}", subject="Cotización",
                    documents=[_doc(f"CN0{s}-cliente.pdf", [f"CN0{s}"])], days_ago=90)
        conn.execute("insert into crm.opportunity_evidence (opportunity_id, source_record_id, relation, "
                     "linked_by_operator_id) values (%s, %s, 'mentions', %s)", (case_id, old, world["admin"]))
    _name_requester(db, repo, world, case_id)
    assert "R14" not in _planned(repo, old)
    assert _one(db, "select stage from crm.opportunity where id = %s", case_id)[0] == "lead"


def test_r14_walks_a_request_to_enviada_and_leaves_one_without_institution_to_a_person(db, world) -> None:
    repo = _repo(db)
    s = world["serial"] + 3
    with _owner(db) as conn:
        admin = world["admin"]
        with_org = conn.execute(
            "insert into crm.opportunity (title, stage, owner_operator_id, organization_id) "
            "values ('Solicitud ficticia', 'lead', %s, null) returning id::text", (admin,)).fetchone()[0]
        sent = _mail(conn, thread=f"t3-{world['tag']}", sender="contacto@origenlab.cl",
                     recipients="persona@gmail.com", subject="RE: Solicitud",
                     documents=[_doc(f"CN0{s}-persona.pdf", [f"CN0{s}"])])
        conn.execute("insert into crm.opportunity_evidence (opportunity_id, source_record_id, relation, "
                     "linked_by_operator_id) values (%s, %s, 'mentions', %s)", (with_org, sent, admin))
    assert "R14" not in _planned(repo, sent)  # no requesting institution: a person names it

    _name_requester(db, repo, world, with_org)
    applied = _apply(repo, admin, sent, "R14")
    assert [step["command"] for step in applied["steps"]] == [
        "advance_case_stage", "advance_case_stage", "advance_case_stage", "record_historical_quotation"]
    assert _one(db, "select stage from crm.opportunity where id = %s", with_org)[0] == "quoting"
    repo.undo(_operator(admin), applied["command_receipt_id"], "no correspondía")
    assert _one(db, "select stage from crm.opportunity where id = %s", with_org)[0] == "lead"


# ------------------------------------------------------------------ R15


def _r4_institution(db, world, filename_tail):
    repo = _repo(db)
    s = world["serial"] + 5
    with _owner(db) as conn:
        sent = _mail(conn, thread=f"t5-{world['tag']}", sender="contacto@origenlab.cl",
                     recipients=f"lab@{world['new_domain']}", subject="Cotización",
                     documents=[_doc(f"CN0{s}-{filename_tail}.pdf", [f"CN0{s}"])])
    applied = _apply(repo, world["admin"], sent, "R4")
    return applied["created"]["organization_id"], applied


def test_r15_renames_and_confirms_an_r4_institution_once_its_quote_has_stood_and_undo_restores_it(db, world) -> None:
    tail = f"Persona Ficticia - Acme Ejemplo {world['tag']} SpA"
    org_id, _ = _r4_institution(db, world, tail)
    assert "R15" not in _planned(_repo(db), org_id)  # the quote was sent today: not yet
    repo = _repo(db, settle_days=0)
    applied = _apply(repo, world["admin"], org_id, "R15")
    assert [step["command"] for step in applied["steps"]] == ["update-organization", "confirm-organization-record"]
    name, confirmation, by = _one(db, "select name, confirmation, confirmed_by_operator_id::text "
                                      "from crm.organization where id = %s", org_id)
    # Confirmed by the machine: no person is named as having looked.
    assert (name, confirmation, by) == (f"Acme Ejemplo {world['tag']} SpA", "confirmed", None)
    assert _one(db, "select array_agg(event_type order by seq), bool_and(actor_kind = 'worker'), "
                    "bool_and(actor_operator_id is null) from crm.domain_event "
                    "where aggregate_id = %s and payload -> 'attribution' ->> 'rule_id' = 'R15'", org_id) == (
        ["organization.updated", "organization.confirmed"], True, True)
    assert _one(db, "select payload ->> 'confirmed_from' from crm.domain_event where aggregate_id = %s "
                    "and event_type = 'organization.confirmed'", org_id)[0] == "mail_rule"
    import psycopg

    from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository

    card = CrmWorkspaceRepository(psycopg.connect, runtime_dsn(db)).organization_authoring(org_id)
    assert card["organization"]["confirmed_by_system"] is True
    assert "R15" not in _planned(repo, org_id)

    undone = repo.undo(_operator(world["admin"]), applied["command_receipt_id"], "aún no está claro")
    assert [step["command"] for step in undone["steps"]] == ["unconfirm-organization-record"]
    assert _one(db, "select name, confirmation, confirmed_by_operator_id from crm.organization where id = %s",
                org_id) == (world["new_domain"], "machine_proposed", None)
    assert "R15" not in _planned(repo, org_id)  # a person's undo stands


def test_r15_leaves_an_institution_whose_quotes_name_no_institution(db, world) -> None:
    org_id, _ = _r4_institution(db, world, "Persona Ficticia")
    assert "R15" not in _planned(_repo(db, settle_days=0), org_id)


def test_r15_does_not_confirm_after_the_quote_was_undone(db, world) -> None:
    org_id, r4 = _r4_institution(db, world, f"Persona - Laboratorio Ficticio {world['tag']} Ltda")
    repo = _repo(db, settle_days=0)
    assert "R15" in _planned(repo, org_id)
    repo.undo(_operator(world["admin"]), r4["command_receipt_id"], "no era un cliente")
    assert "R15" not in _planned(repo, org_id)


def test_both_kill_flags_default_off() -> None:
    from origenlab_api.settings import Settings

    settings = Settings(_env_file=None)
    assert settings.v2_auto_rule_r14_enabled is False and settings.v2_auto_rule_r15_enabled is False


def test_the_automatic_run_skips_a_rule_switched_off_by_its_flag(db, world) -> None:
    from origenlab_api.v2.mail_rules_auto import AutoMailRules

    org_id, _ = _r4_institution(db, world, f"Persona - Centro Ejemplo {world['tag']}")
    repo = _repo(db, settle_days=0)
    auto = AutoMailRules(repo, interval_seconds=60, disabled_rules=frozenset({"R15"}))
    auto.set_enabled(_operator(world["admin"]), True, "prueba de bandera", f"auto-{world['tag']}")
    try:
        assert "R15" not in auto.state()["rules"] and "R14" in auto.state()["rules"]
        auto.run_once()
        assert _one(db, "select confirmation from crm.organization where id = %s", org_id)[0] == "machine_proposed"
        AutoMailRules(repo, interval_seconds=60).run_once()
        assert _one(db, "select confirmation from crm.organization where id = %s", org_id)[0] == "confirmed"
        from origenlab_api.v2.operations import read_operation_facts

        with _owner(db) as conn, conn.cursor() as cur:
            facts = read_operation_facts(cur)
        row = next(a for a in facts["actions"] if a["rule_id"] == "R15" and a["organization_id"] == org_id)
        assert row["automatic"] and row["organization_name"] == f"Centro Ejemplo {world['tag']}"
        # «Hoy» lists emails the system filed or opened; an institution is Operaciones' only.
        assert all(a["rule_id"] != "R15" for a in repo.recent_automatic())
    finally:
        auto.set_enabled(_operator(world["admin"]), False, "fin de la prueba", f"auto-off-{world['tag']}")


# ------------------------------------------------------------------ unconfirm-organization-record


def _authoring(db):
    import psycopg

    from origenlab_api.v2.crm_authoring import V2CrmAuthoringRepository

    return V2CrmAuthoringRepository(psycopg.connect, runtime_dsn(db))


def test_unconfirm_organization_record_reverses_a_confirmation(db, world) -> None:
    from origenlab_api.v2.commands import CommandRefused

    repo = _authoring(db)
    op = _operator(world["admin"], role="sales")
    org = world["client"]
    version = _one(db, "select version from crm.organization where id = %s", org)[0]

    def run(fields, key):
        return repo.execute(command_name="unconfirm-organization-record", operator=op, fields=fields,
                            idempotency_key=key, digest=hashlib.sha256(key.encode()).hexdigest())

    with pytest.raises(CommandRefused) as stale:
        run({"organization_id": org, "expected_version": version + 5, "previous_name": None, "note": "x"},
            f"un-stale-{world['tag']}")
    assert stale.value.code == "stale_version"
    done = run({"organization_id": org, "expected_version": version, "previous_name": "nombre-anterior.test",
                "note": "lo confirmó el sistema por error"}, f"un-{world['tag']}")
    assert (done["version"], done["already_unconfirmed"]) == (version + 1, False)
    assert _one(db, "select name, confirmation, confirmed_by_operator_id from crm.organization where id = %s",
                org) == ("nombre-anterior.test", "machine_proposed", None)
    assert _one(db, "select event_type, payload -> 'changed' from crm.domain_event where aggregate_id = %s "
                    "order by seq desc limit 1", org) == ("organization.updated", ["confirmation", "name"])
    again = run({"organization_id": org, "expected_version": version + 1, "previous_name": None, "note": "otra vez"},
                f"un2-{world['tag']}")
    assert again["already_unconfirmed"] is True and again["version"] == version + 1
