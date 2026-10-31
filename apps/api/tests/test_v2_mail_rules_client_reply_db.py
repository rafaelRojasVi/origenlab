"""R17 against a real database (owner 2026-10-26): a Cotizador quote sent (R16), then the client answers
on the case → the case «Conversación», once; «Quitar» puts it back in «Enviada»; never on a case
«En pausa».

Runs in a disposable `origenlab_test_<hex>` as the unprivileged `origenlab_api` role. Every name,
number and address is fictitious.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

from test_v2_mail_rules_cotizador_sent_db import _admin, _approved, _doc, _email, _mail_rules, _planned
from test_v2_quote_commands import _case, _owner, _rows
from test_v2_quote_commands import world as world  # noqa: PLC0414 - the module's catalog fixture, reused
from v2_command_harness import needs_db

pytestmark = needs_db


def _reply(dsn: str, case_id: str, linked_by: str, sent_at: datetime, subject: str = "Re: Cotización") -> str:
    """The client's inbound answer, already on the case (as R1 leaves it)."""
    gid = uuid.uuid4().hex[:16]
    payload = {"gmail_message_id": gid, "gmail_thread_id": f"t-{gid}", "sender": "Persona <persona@cliente-ficticio.test>",
               "recipients": "contacto@origenlab.cl", "subject_raw": subject, "sent_at": sent_at.isoformat(),
               "documents": []}
    [(record,)] = _owner(dsn, "insert into evidence.source_record (kind, dedupe_key, payload, source_uri) "
                              "values ('gmail_message', %s, %s::jsonb, %s) returning id::text",
                         (f"gmail_message:{gid}", json.dumps(payload), f"gmail://msg/{gid}"))
    [(mailbox,)] = _owner(dsn, "insert into comms.mailbox (address_norm) values (%s) returning id",
                          (f"mbox-{gid}@example.test",))
    _owner(dsn, "insert into comms.message (mailbox_id, provider_message_id, provider_thread_id, direction, "
                "internal_date) values (%s, %s, %s, 'inbound', now())", (mailbox, gid, f"t-{gid}"))
    _owner(dsn, "insert into crm.opportunity_evidence (opportunity_id, source_record_id, relation, "
                "linked_by_operator_id) values (%s, %s, 'mentions', %s)", (case_id, record, linked_by))
    return record


def _stage(dsn: str, case_id: str) -> str:
    return _rows(dsn, "select stage from crm.opportunity where id = %s", (case_id,))[0]["stage"]


def _sent_case(world: dict, admin) -> str:
    dsn = world["dsn"]
    case_id = _case(dsn, world["operator"], world["institution"])
    quote = _approved(world, case_id)
    email, _ = _email(dsn, [_doc(f"Cotización CN{quote['quote_number'][:5]} — Cliente Ficticio.pdf")],
                      case_id=case_id, linked_by=admin.operator_id, sent_at=datetime.now(UTC) + timedelta(minutes=1))
    sent = _mail_rules(dsn).apply(admin, [{"evidence_id": email, "rule_id": "R16"}])
    assert not sent["refused"], sent["refused"]
    assert _stage(dsn, case_id) == "quoting"
    return case_id


def test_the_clients_answer_moves_the_case_to_conversacion_and_quitar_moves_it_back(world) -> None:
    dsn = world["dsn"]
    admin = _admin(dsn)
    case_id = _sent_case(world, admin)
    answer = _reply(dsn, case_id, admin.operator_id, datetime.now(UTC) + timedelta(minutes=5))

    repo = _mail_rules(dsn)
    action = _planned(repo, answer)["R17"]
    assert action["mode"] == "auto" and action["case_id"] == case_id
    result = repo.apply(admin, [{"evidence_id": answer, "rule_id": "R17"}])
    assert not result["refused"], result["refused"]
    assert [s["command"] for s in result["applied"][0]["steps"]] == ["advance_case_stage"]
    assert _stage(dsn, case_id) == "negotiating"
    assert "R17" not in _planned(repo, answer)  # once

    undone = repo.undo(admin, result["applied"][0]["command_receipt_id"], "todavía no responde de verdad")
    assert [s["command"] for s in undone["steps"]] == ["advance_case_stage"]
    assert _stage(dsn, case_id) == "quoting"
    assert "R17" not in _planned(repo, answer)  # an undone action is not planned again


def test_an_automatic_reply_is_not_an_answer(world) -> None:
    dsn = world["dsn"]
    admin = _admin(dsn)
    case_id = _sent_case(world, admin)
    answer = _reply(dsn, case_id, admin.operator_id, datetime.now(UTC) + timedelta(minutes=5),
                    subject="Respuesta automática: Re: Cotización")
    assert "R17" not in _planned(_mail_rules(dsn), answer)


def _task(dsn: str, case_id: str, owner: str, title: str, due_at: datetime) -> None:
    _owner(dsn, "insert into crm.task (opportunity_id, owner_operator_id, title, due_at) values (%s, %s, %s, %s)",
           (case_id, owner, title, due_at))


def test_a_case_en_pausa_waits_but_a_reminder_does_not_hold_it(world) -> None:
    dsn = world["dsn"]
    admin = _admin(dsn)
    paused, reminded = _sent_case(world, admin), _sent_case(world, admin)
    later = datetime.now(UTC) + timedelta(days=90)
    _task(dsn, paused, admin.operator_id, "Retomar: evaluarán el año entrante", later)
    _task(dsn, reminded, admin.operator_id, "Llamar para confirmar", later)
    on_paused = _reply(dsn, paused, admin.operator_id, datetime.now(UTC) + timedelta(minutes=5))
    on_reminded = _reply(dsn, reminded, admin.operator_id, datetime.now(UTC) + timedelta(minutes=5))

    repo = _mail_rules(dsn)
    assert "R17" not in _planned(repo, on_paused)
    assert "R17" in _planned(repo, on_reminded)
