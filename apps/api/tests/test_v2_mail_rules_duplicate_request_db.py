"""R23 against a real database (owner 2026-11-22): a client's request waits for its quote; the quote is
made in the Cotizador as «+ Cliente nuevo» (a person's name, no address), so it opens a second case;
its PDF goes out to the request's own address → R16 marks it sent, then R23 closes the request as a
duplicate of that case, once, and «Quitar» reopens it.

Runs in a disposable `origenlab_test_<hex>` as the unprivileged `origenlab_api` role, through the real
Cotizador commands. Every name, number and address is fictitious. No test reads the wall clock: the
email's time is the approval's own, as the database stored it, plus a minute.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import psycopg

from origenlab_api.v2.commands import request_digest
from origenlab_api.v2.quotes.commands import V2QuoteRepository
from origenlab_api.v2.quotes.draft import QUOTE_BODIES
from test_v2_mail_rules_cotizador_sent_db import _admin, _doc, _email, _mail_rules, _planned
from test_v2_quote_commands import NOW, WARRANTY, _catalog_item, _org, _owner, _rows, _unattributed, fake_fx
from test_v2_quote_commands import world as world  # noqa: PLC0414 - the module's catalog fixture, reused
from v2_command_harness import needs_db, runtime_dsn

pytestmark = needs_db


def _run(world: dict, command: str, raw: dict) -> dict:
    body = QUOTE_BODIES[command](**raw)
    repo = V2QuoteRepository(psycopg.connect, runtime_dsn(world["dsn"]), fx_resolver=fake_fx, clock=lambda: NOW)
    return repo.execute(command_name=command, operator=world["operator"], fields=body.model_dump(mode="json"),
                        idempotency_key=uuid.uuid4().hex, digest=request_digest(command, body))


def _request(world: dict, address: str, *, organization: str | None) -> tuple[str, str]:
    """A client's request (lead) with that address as its quote recipient, as a forward opens one."""
    dsn = world["dsn"]
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:  # one transaction: the requesting-institution trigger
        cur.execute("set role origenlab_owner")
        cur.execute("insert into crm.opportunity (title, stage, owner_operator_id, organization_id) "
                    "values ('Solicitud — Solicitud de cotización', 'lead', %s, %s) returning id::text",
                    (world["operator"].operator_id, organization))
        case = cur.fetchone()[0]
        if organization is not None:
            cur.execute("insert into crm.opportunity_organization (opportunity_id, organization_id, role, valid_from, "
                        "confirmation, confirmed_by_operator_id) values (%s, %s, 'requesting_institution', "
                        "current_date, 'confirmed', %s)", (case, organization, world["operator"].operator_id))
        conn.commit()
    point = _unattributed(dsn, address)
    _owner(dsn, "insert into crm.opportunity_participant (opportunity_id, contact_point_id, role, is_primary, "
                "valid_from, confirmation) values (%s, %s, 'quote_recipient', true, current_date, 'confirmed')",
           (case, point))
    return case, point


def _quote_typed_as_new_client(world: dict, point: str, tag: str) -> dict:
    """«+ Cliente nuevo «Persona Tecleada»» with no address, then the recipient picked and approved."""
    created = _run(world, "create-quote", {"client": {"new_organization": {"name": f"Persona Tecleada {tag}"}}})
    assert (created["case_created"], created["organization_created"]) == (True, True)
    saved = _run(world, "save-quote-draft", {"revision_id": created["revision_id"], "expected_version": created["version"],
                                             "draft": {"items": [_catalog_item(world)],
                                                       "terms": {"warranty_terms_es": WARRANTY},
                                                       "recipient": {"contact_point_id": point}}})
    _run(world, "approve-quote-revision", {"revision_id": created["revision_id"], "expected_version": saved["version"]})
    return created


def _stage(dsn: str, case: str) -> dict:
    return _rows(dsn, "select stage, close_reason, closed_at from crm.opportunity where id = %s", (case,))[0]


def _sent_email(world: dict, created: dict, address: str, admin) -> str:
    dsn = world["dsn"]
    [(approved_at,)] = _owner(dsn, "select approved_at from crm.quote_revision where id = %s", (created["revision_id"],))
    doc = _doc(f"Cotización CN{created['quote_number'][:5]} — Persona Tecleada.pdf")
    email, _ = _email(dsn, [doc], case_id=created["opportunity_id"], linked_by=admin.operator_id,
                      sent_at=approved_at + timedelta(minutes=1))
    _owner(dsn, "update evidence.source_record set payload = jsonb_set(payload, '{recipients}', to_jsonb(%s::text)) "
                "where id = %s", (f"{address}; copia-{uuid.uuid4().hex[:6]}@gmail.com", email))
    return email


def test_the_request_beside_a_cotizador_case_closes_once_r16_sent_its_quote_and_quitar_reopens_it(world) -> None:
    dsn, tag = world["dsn"], uuid.uuid4().hex[:8]
    admin = _admin(dsn)
    address = f"compras-{tag}@hospital-ficticio.test"
    hospital = _org(dsn, "institution", "Hospital Ficticio")
    request, point = _request(world, address, organization=hospital)
    draft = _run(world, "create-quote", {"opportunity_id": request})  # the request's own draft stays on it
    created = _quote_typed_as_new_client(world, point, tag)
    email = _sent_email(world, created, address, admin)

    repo = _mail_rules(dsn)
    assert "R23" not in _planned(repo, email)  # R16 first
    sent = repo.apply(admin, [{"evidence_id": email, "rule_id": "R16"}])
    assert not sent["refused"], sent["refused"]

    action = _planned(repo, email)["R23"]
    assert (action["mode"], action["case_id"], action["candidates"]) == ("auto", request, [created["opportunity_id"]])
    result = repo.apply(admin, [{"evidence_id": email, "rule_id": "R23"}])
    assert not result["refused"], result["refused"]
    [applied] = result["applied"]
    closed = _stage(dsn, request)
    assert closed["stage"] == "abandoned"
    assert closed["close_reason"].startswith(f"Duplicado: la cotización {created['quote_number']}")
    assert created["opportunity_id"][:8] in closed["close_reason"]
    assert _stage(dsn, created["opportunity_id"])["stage"] == "quoting"  # the sending case, untouched
    # The email is not filed on the request; its draft stays there, a draft.
    assert _rows(dsn, "select count(*) as n from crm.opportunity_evidence where source_record_id = %s "
                      "and opportunity_id = %s", (email, request))[0]["n"] == 0
    assert _rows(dsn, "select status from crm.quote_revision where id = %s", (draft["revision_id"],))[0]["status"] == "draft"
    events = _rows(dsn, "select actor_kind, payload from crm.domain_event where aggregate_id = %s "
                        "and event_type = 'opportunity.staged' order by seq desc limit 1", (request,))
    assert events[0]["actor_kind"] == "worker" and events[0]["payload"]["attribution"]["rule_id"] == "R23"
    assert "R23" not in _planned(repo, email)  # once

    undone = repo.undo(admin, applied["command_receipt_id"], "no era la misma compra")
    assert [s["command"] for s in undone["steps"]] == ["advance_case_stage"]
    back = _stage(dsn, request)
    assert (back["stage"], back["closed_at"]) == ("lead", None)
    assert "R23" not in _planned(repo, email)  # «Quitar» is final: never closed again


def test_a_request_of_another_address_is_left_open(world) -> None:
    dsn, tag = world["dsn"], uuid.uuid4().hex[:8]
    admin = _admin(dsn)
    address = f"otra-{tag}@hospital-ficticio.test"
    other, _ = _request(world, f"distinta-{tag}@hospital-ficticio.test", organization=None)
    _, point = _request(world, address, organization=None)
    created = _quote_typed_as_new_client(world, point, tag)
    email = _sent_email(world, created, address, admin)
    repo = _mail_rules(dsn)
    assert not repo.apply(admin, [{"evidence_id": email, "rule_id": "R16"}])["refused"]
    planned = _planned(repo, email).get("R23")
    # The one request of this address is closed; the other address's never is.
    assert planned is not None and planned["case_id"] != other
    assert _stage(dsn, other)["stage"] == "lead"
