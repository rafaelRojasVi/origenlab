"""R21 against a real database (owner 2026-11-11): the client who writes on an open case becomes one
of its people, once; «Quitar» ends that participant and the address is never added to the case again.

Runs in a disposable `origenlab_test_<hex>` as the unprivileged `origenlab_api` role. Every name,
number and address is fictitious.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from test_v2_mail_rules_client_reply_db import _reply
from test_v2_mail_rules_cotizador_sent_db import _admin, _mail_rules
from test_v2_quote_commands import _case, _rows
from test_v2_quote_commands import world as world  # noqa: PLC0414 - the module's catalog fixture, reused
from v2_command_harness import needs_db

from origenlab_api.v2.mail_rules import case_people_key

pytestmark = needs_db

CLIENT = "persona@cliente-ficticio.test"


def _people(dsn: str, case_id: str) -> list[str]:
    return [r["value_norm"] for r in _rows(
        dsn, "select cp.value_norm from crm.opportunity_participant p "
             "join crm.contact_point cp on cp.id = p.contact_point_id "
             "where p.opportunity_id = %s and p.valid_to is null order by 1", (case_id,))]


def test_who_writes_on_an_open_case_becomes_one_of_its_people_and_quitar_takes_them_out(world) -> None:
    dsn = world["dsn"]
    admin = _admin(dsn)
    case_id = _case(dsn, world["operator"], world["institution"])
    _reply(dsn, case_id, admin.operator_id, datetime.now(UTC) + timedelta(minutes=5))
    key = case_people_key(case_id, CLIENT)

    repo = _mail_rules(dsn)
    planned = {a["evidence_id"]: a for a in repo.preview()["actions"] if a["rule_id"] == "R21"}
    assert planned[key]["mode"] == "auto" and planned[key]["case_id"] == case_id

    result = repo.apply(admin, [{"evidence_id": key, "rule_id": "R21"}])
    assert not result["refused"], result["refused"]
    assert CLIENT in _people(dsn, case_id)
    assert key not in {a["evidence_id"] for a in repo.preview()["actions"]}  # once

    undone = repo.undo(admin, result["applied"][0]["command_receipt_id"], "es un proveedor, no el cliente")
    assert [s["command"] for s in undone["steps"]] == ["end_case_participant"]
    assert CLIENT not in _people(dsn, case_id)
    assert key not in {a["evidence_id"] for a in repo.preview()["actions"]}  # never again for this case
    [event] = _rows(dsn, "select event_type from crm.domain_event where aggregate_kind = 'opportunity_participant' "
                         "and event_type = 'participant.ended'")
    assert event["event_type"] == "participant.ended"
