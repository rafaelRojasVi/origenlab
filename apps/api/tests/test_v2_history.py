"""«Historial»: the recorded decisions in plain Spanish — never a raw command name, a note or an address."""
from __future__ import annotations

from origenlab_api.v2.history import compose_history

OPERATOR = "Operadora Ficticia"


def _row(command: str, body: dict, **extra) -> dict:
    return {"receipt_id": "r-1", "command_name": command, "completed_at": "2026-10-09T15:00:00+00:00",
            "operator": OPERATOR, "response_body": body, "undone_at": None, **extra}


def test_a_stage_change_reads_as_a_sentence_with_the_case_and_its_new_stage() -> None:
    rows = [_row("advance_case_stage", {"opportunity_id": "o1", "stage": "negotiating", "close_reason": "x@ejemplo.invalid"})]
    [item] = compose_history(rows, case_titles={"o1": "Caso ficticio"}, org_names={})
    assert item["action"] == "Cambió la etapa a «Conversación»"
    assert item["case"] == {"opportunity_id": "o1", "title": "Caso ficticio"}
    assert item["operator"] == OPERATOR
    assert "@" not in repr(item)


def test_an_institution_decision_names_the_institution() -> None:
    rows = [_row("confirm-organization-record", {"organization_id": "g1"})]
    [item] = compose_history(rows, case_titles={}, org_names={"g1": "Universidad Ficticia"})
    assert item["action"] == "Confirmó una institución"
    assert item["organization"] == {"organization_id": "g1", "name": "Universidad Ficticia"}


def test_a_won_case_and_a_quote_carry_the_quote_number() -> None:
    rows = [_row("record_case_won", {"opportunity_id": "o1", "quote_number": "01253-26"})]
    [item] = compose_history(rows, case_titles={"o1": "Caso ficticio"}, org_names={})
    assert item["action"] == "Marcó ganada · 01253-26"


def test_an_automatic_mail_action_can_be_undone_until_it_is() -> None:
    body = {"case_id": "o1", "case_title": "Título del correo", "rule_id": "R1", "reasons": ["persona@ejemplo.invalid"]}
    [live] = compose_history([_row("apply_mail_rule", body)], case_titles={"o1": "Caso ficticio"}, org_names={})
    assert live["action"] == "Acción automática del correo"
    assert live["undo"] == {"kind": "mail_rule", "receipt_id": "r-1"} and live["undone_at"] is None
    assert "@" not in repr(live)
    [done] = compose_history([_row("apply_mail_rule", body, undone_at="2026-10-09T16:00:00+00:00")],
                             case_titles={"o1": "Caso ficticio"}, org_names={})
    assert done["undo"] is None and done["undone_at"] == "2026-10-09T16:00:00+00:00"


def test_an_unknown_command_is_left_out_never_shown_raw() -> None:
    assert compose_history([_row("some_internal_thing", {"opportunity_id": "o1"})], case_titles={}, org_names={}) == []


# ───────────────────────────────────────────── real database ──

import uuid  # noqa: E402

import pytest  # noqa: E402
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn  # noqa: E402


@pytest.fixture(scope="module")
def disposable_database():
    yield from build_disposable_database()


@needs_db
def test_db_history_lists_known_decisions_with_the_case_title(disposable_database) -> None:
    import json

    import psycopg

    from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository

    tag = uuid.uuid4().hex[:8]
    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute("insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                    "values (gen_random_uuid(), %s, 'Operadora Historial', 'sales', 'active') returning id::text",
                    (f"hist-{tag}@example.test",))
        op = cur.fetchone()[0]
        cur.execute("insert into crm.opportunity (title, stage, owner_operator_id) values (%s, 'lead', %s) returning id::text",
                    (f"Caso historial {tag}", op))
        case = cur.fetchone()[0]
        for command, body in (("advance_case_stage", {"opportunity_id": case, "stage": "qualifying"}),
                              ("some_internal_thing", {"opportunity_id": case})):
            cur.execute("insert into platform.command_receipt (operator_id, idempotency_key, command_name, request_digest, "
                        "status, response_status, response_body, completed_at) values (%s, %s, %s, %s, 'completed', 200, %s, now())",
                        (op, f"k-{command}-{tag}", command, "d" * 64, json.dumps(body)))

    repo = CrmWorkspaceRepository(psycopg.connect, runtime_dsn(disposable_database))
    mine = [i for i in repo.history(200)["items"] if i["operator"] == "Operadora Historial"]
    assert [i["action"] for i in mine] == ["Cambió la etapa a «Solicitada»"]
    assert mine[0]["case"] == {"opportunity_id": case, "title": f"Caso historial {tag}"}
