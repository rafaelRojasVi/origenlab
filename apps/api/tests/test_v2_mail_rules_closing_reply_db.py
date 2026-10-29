"""R18 and R19 against a real database (owner 2026-10-29): the client's closing reply leaves a receipt
only, shown in «Hecho automáticamente» and removable with «Quitar»; a reply the AI reads as lost closes
the case, and «Quitar» reopens it.

Runs in a disposable `origenlab_test_<hex>` as the unprivileged `origenlab_api` role. Every name,
number and address is fictitious.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from test_v2_mail_rules_client_reply_db import _reply, _sent_case, _stage
from test_v2_mail_rules_cotizador_sent_db import _admin, _mail_rules, _planned
from test_v2_quote_commands import _owner
from test_v2_quote_commands import world as world  # noqa: PLC0414 - the module's catalog fixture, reused
from v2_command_harness import needs_db

pytestmark = needs_db


def _read(dsn: str, record: str, **reading) -> None:
    """The worker's triage reading of the email (`triage:v4`)."""
    _owner(dsn, "insert into evidence.assertion (source_record_id, kind, value_norm, value) "
                "values (%s, 'message_triage', 'triage:v4', %s::jsonb)",
           (record, json.dumps({"class": "commercial", "reading": reading})))


def _closing(world: dict, admin, **reading) -> tuple[str, str]:
    dsn = world["dsn"]
    case_id = _sent_case(world, admin)
    answer = _reply(dsn, case_id, admin.operator_id, datetime.now(UTC) + timedelta(minutes=5))
    _read(dsn, answer, **{"intent": "quote_followup", "stage": "negotiating", "needs_reply": False,
                          "summary_es": "Agradece y avisará.", **reading})
    return case_id, answer


def test_r18_applies_as_a_receipt_only_and_shows_in_recent_automatic(world) -> None:
    dsn = world["dsn"]
    admin = _admin(dsn)
    case_id, answer = _closing(world, admin)
    repo = _mail_rules(dsn)
    action = next(a for a in repo.plan()[0] if a.evidence_id == answer and a.rule_id == "R18")
    result = repo.apply_planned(admin, action, automatic=True)
    assert result["rule_id"] == "R18" and result["case_id"] == case_id
    assert result["steps"] == []
    assert _stage(dsn, case_id) == "quoting"  # R18 changes nothing in the CRM
    shown = [a for a in repo.recent_automatic() if a["receipt_id"] == result["command_receipt_id"]]
    assert [a["rule_id"] for a in shown] == ["R18"]
    assert "R18" not in _planned(repo, answer)  # once


def test_r18_undo_brings_the_row_back(world) -> None:
    dsn = world["dsn"]
    admin = _admin(dsn)
    _, answer = _closing(world, admin)
    repo = _mail_rules(dsn)
    applied = repo.apply(admin, [{"evidence_id": answer, "rule_id": "R18"}])
    assert not applied["refused"], applied["refused"]
    undone = repo.undo(admin, applied["applied"][0]["command_receipt_id"], "sí había que responder")
    assert [s["command"] for s in undone["steps"]] == ["no_reply_withdrawn"]
    assert "R18" not in _planned(repo, answer)  # an undone action is not planned again


def test_r19_closes_the_case_and_undo_reopens_it(world) -> None:
    dsn = world["dsn"]
    admin = _admin(dsn)
    case_id, answer = _closing(world, admin, intent="lost", stage="lost", needs_reply=False,
                               summary_es="Compraron a otro proveedor.")
    # A free-mail client on the case's own thread may speak for it (the R6 sender gate).
    _owner(dsn, "update evidence.source_record set payload = jsonb_set(payload, '{sender}', %s::jsonb) where id = %s",
           (json.dumps("Persona <persona.ficticia@gmail.com>"), answer))
    repo = _mail_rules(dsn)
    # R17 moves the case to «Conversación» first; R19 closes it on the next pass.
    first = repo.apply(admin, [{"evidence_id": answer, "rule_id": "R17"}])
    assert not first["refused"], first["refused"]
    assert _planned(repo, answer)["R19"]["mode"] == "auto"
    closed = repo.apply(admin, [{"evidence_id": answer, "rule_id": "R19"}])
    assert not closed["refused"], closed["refused"]
    assert _stage(dsn, case_id) == "lost"

    undone = repo.undo(admin, closed["applied"][0]["command_receipt_id"], "sigue evaluando")
    assert [s["command"] for s in undone["steps"]] == ["correct_case_stage"]
    assert _stage(dsn, case_id) == "negotiating"


def _inbound(dsn: str, case_id: str) -> dict:
    import psycopg

    from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository
    from v2_command_harness import runtime_dsn

    workspace = CrmWorkspaceRepository(psycopg.connect, runtime_dsn(dsn))
    [card] = [c for c in workspace.pipeline()["items"] if c["opportunity_id"] == case_id]
    return card["last_contact"]["inbound"]


def test_card_reports_no_reply_until_undone(world) -> None:
    dsn = world["dsn"]
    admin = _admin(dsn)
    case_id, answer = _closing(world, admin)
    assert "no_reply" not in _inbound(dsn, case_id)
    repo = _mail_rules(dsn)
    applied = repo.apply(admin, [{"evidence_id": answer, "rule_id": "R18"}])
    assert not applied["refused"], applied["refused"]
    assert _inbound(dsn, case_id)["no_reply"] is True
    repo.undo(admin, applied["applied"][0]["command_receipt_id"], "sí había que responder")
    assert "no_reply" not in _inbound(dsn, case_id)
