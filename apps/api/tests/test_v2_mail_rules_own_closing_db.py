"""R22 against a real database (owner 2026-11-16): our own closing email pauses the case until the day
it names; «Quitar» resumes it, unless a person changed the pause since.

Runs in a disposable `origenlab_test_<hex>` as the unprivileged `origenlab_api` role. Every name,
number and address is fictitious.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from origenlab_api.v2.commands import CommandRefused

from test_v2_mail_rules_client_reply_db import _rows, _sent_case
from test_v2_mail_rules_cotizador_sent_db import _admin, _mail_rules, _planned
from test_v2_quote_commands import _owner
from test_v2_quote_commands import world as world  # noqa: PLC0414 - the module's catalog fixture, reused
from v2_command_harness import needs_db

pytestmark = needs_db


def _our_closing(dsn: str, case_id: str, linked_by: str, until: str | None) -> str:
    """Our email on the case, read by the worker as closing (`outbound_intent:v1`)."""
    gid = uuid.uuid4().hex[:16]
    payload = {"gmail_message_id": gid, "gmail_thread_id": f"t-{gid}", "sender": "OrigenLab <contacto@origenlab.cl>",
               "recipients": "persona@cliente-ficticio.test", "subject_raw": "Re: Cotización",
               "sent_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(), "documents": []}
    [(record,)] = _owner(dsn, "insert into evidence.source_record (kind, dedupe_key, payload, source_uri) "
                              "values ('gmail_message', %s, %s::jsonb, %s) returning id::text",
                         (f"gmail_message:{gid}", json.dumps(payload), f"gmail://msg/{gid}"))
    [(mailbox,)] = _owner(dsn, "insert into comms.mailbox (address_norm) values (%s) returning id",
                          (f"mbox-{gid}@example.test",))
    _owner(dsn, "insert into comms.message (mailbox_id, provider_message_id, provider_thread_id, direction, "
                "internal_date) values (%s, %s, %s, 'outbound', now())", (mailbox, gid, f"t-{gid}"))
    _owner(dsn, "insert into crm.opportunity_evidence (opportunity_id, source_record_id, relation, "
                "linked_by_operator_id) values (%s, %s, 'mentions', %s)", (case_id, record, linked_by))
    _owner(dsn, "insert into evidence.assertion (source_record_id, kind, value_norm, value) "
                "values (%s, 'message_triage', 'outbound_intent:v1', %s::jsonb)",
           (record, json.dumps({"closing": True, "until": until, "phrase": "lo dejo en pausa"})))
    return record


def _pause(dsn: str, case_id: str):
    row = _rows(dsn, "select paused_until::text, pause_reason from crm.opportunity where id = %s", (case_id,))[0]
    return row["paused_until"], row["pause_reason"]


def test_r22_pauses_the_case_and_quitar_resumes_it(world) -> None:
    dsn = world["dsn"]
    admin = _admin(dsn)
    case_id = _sent_case(world, admin)
    until = (datetime.now(UTC) + timedelta(days=90)).date().isoformat()
    email = _our_closing(dsn, case_id, admin.operator_id, until)

    repo = _mail_rules(dsn)
    assert _planned(repo, email)["R22"]["mode"] == "auto"
    applied = repo.apply(admin, [{"evidence_id": email, "rule_id": "R22"}])
    assert not applied["refused"], applied["refused"]
    assert _pause(dsn, case_id) == (until, "Cliente lo evaluará")
    assert "R22" not in _planned(repo, email)  # once

    undone = repo.undo(admin, applied["applied"][0]["command_receipt_id"], "no era para pausar")
    assert [s["command"] for s in undone["steps"]] == ["set_case_pause"]
    assert _pause(dsn, case_id) == (None, None)


def test_r22_quitar_leaves_a_pause_a_person_changed(world) -> None:
    from origenlab_api.v2.case_command_repository import V2CaseCommandRepository
    from origenlab_api.v2.case_commands import SET_CASE_PAUSE, SetCasePauseBody, validated_case
    import psycopg

    from origenlab_api.v2.commands import request_digest
    from v2_command_harness import runtime_dsn

    dsn = world["dsn"]
    admin = _admin(dsn)
    case_id = _sent_case(world, admin)
    email = _our_closing(dsn, case_id, admin.operator_id, None)
    repo = _mail_rules(dsn)
    applied = repo.apply(admin, [{"evidence_id": email, "rule_id": "R22"}])
    assert not applied["refused"], applied["refused"]

    later = (datetime.now(UTC) + timedelta(days=120)).date()
    version = _rows(dsn, "select version from crm.opportunity where id = %s", (case_id,))[0]["version"]
    body = SetCasePauseBody(opportunity_id=case_id, opportunity_version=version, paused_until=later,
                            reason="Esperando fondos o proyecto", note="lo pidió el cliente")
    V2CaseCommandRepository(psycopg.connect, runtime_dsn(dsn)).execute(
        command_name=SET_CASE_PAUSE, operator=admin, fields=validated_case(SET_CASE_PAUSE, body),
        idempotency_key=f"t-{uuid.uuid4().hex}", digest=request_digest(SET_CASE_PAUSE, body))

    # The pause is now the person's: «Quitar» has nothing of R22's left to undo (as R17 when a person moved the case).
    with pytest.raises(CommandRefused, match="nothing that can be reversed"):
        repo.undo(admin, applied["applied"][0]["command_receipt_id"], "no era para pausar")
    assert _pause(dsn, case_id) == (later.isoformat(), "Esperando fondos o proyecto")
