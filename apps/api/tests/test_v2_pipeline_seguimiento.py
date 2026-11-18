"""The pipeline read for «Ventas › Seguimiento»: encargado, phone, call counts, calls per operator.

Database tests, as the unprivileged `origenlab_api` role in a disposable `origenlab_test_<hex>`.
Every value is fictitious.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import psycopg

from origenlab_api.v2.case_commands import SET_CASE_FOLLOW_UP, SetCaseFollowUpBody
from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository
from v2_command_harness import needs_db, runtime_dsn

from test_v2_case_command_boundary import (  # noqa: F401 - fixtures and helpers of the case boundary
    _open_case,
    _repo,
    _run,
    disposable_database,
    world,
)

SANTIAGO = timezone(timedelta(hours=-3))
NOW = datetime(2026, 11, 18, 9, 0, tzinfo=SANTIAGO)


def _owner(dsn, sql, params=()):
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


def _card(dsn, case_id, now=NOW):
    payload = CrmWorkspaceRepository(psycopg.connect, runtime_dsn(dsn)).pipeline(now=now)
    [card] = [c for c in payload["items"] if c["opportunity_id"] == case_id]
    return card, payload


def _call(dsn, case_id, author, at, outcome="no_contesto"):
    _owner(dsn, "insert into crm.note (subject_kind, subject_id, body, author_operator_id, note_kind, "
                "call_outcome, created_at) values ('opportunity', %s, 'Llamada ficticia.', %s, 'llamada', %s, %s)",
           (case_id, author, outcome, at))


def _phone(dsn, case_id, number, confirmation):
    """A person on the case with a phone point; returns nothing."""
    person = _owner(dsn, "insert into crm.person (display_name, confirmation) values ('Persona Ficticia', "
                         "'machine_proposed') returning id")[0][0]
    _owner(dsn, "insert into crm.opportunity_participant (opportunity_id, person_id, role, is_primary, "
                "valid_from, confirmation) values (%s, %s, 'quote_recipient', true, current_date, "
                "'machine_proposed')", (case_id, person))
    _owner(dsn, "insert into crm.contact_point (kind, value_norm, value_display, person_id, usage, confirmation) "
                "values ('phone', %s, %s, %s, 'personal', %s)", (number.replace(" ", ""), number, person, confirmation))


@needs_db
def test_card_follow_up_defaults_null(disposable_database, world) -> None:
    case_id, _ = _open_case(_repo(disposable_database), world, "Caso sin encargado")
    card, _ = _card(disposable_database, case_id)
    assert card["follow_up"] == {"operator_id": None, "operator_name": None, "channel": None}
    assert card["call_counts"] == {"total": 0, "today": 0}
    assert card["last_call_any"] is None


@needs_db
def test_card_follow_up_after_command(disposable_database, world) -> None:
    repo = _repo(disposable_database)
    case_id, version = _open_case(repo, world, "Caso con encargado")
    _run(repo, SET_CASE_FOLLOW_UP, SetCaseFollowUpBody(
        opportunity_id=case_id, opportunity_version=version,
        follow_up_operator_id=world["other_operator_id"], channel="correo"), world)
    card, _ = _card(disposable_database, case_id)
    assert card["follow_up"] == {"operator_id": world["other_operator_id"],
                                 "operator_name": "Pytest Operator", "channel": "correo"}


@needs_db
def test_contact_phone_prefers_confirmed(disposable_database, world) -> None:
    case_id, _ = _open_case(_repo(disposable_database), world, "Caso con teléfono")
    _phone(disposable_database, case_id, "+56 9 1234 5678", "machine_proposed")
    card, _ = _card(disposable_database, case_id)
    assert (card["contact"]["phone"], card["contact"]["phone_confirmed"]) == ("+56 9 1234 5678", False)
    person = _owner(disposable_database, "select person_id from crm.opportunity_participant "
                                         "where opportunity_id = %s", (case_id,))[0][0]
    _owner(disposable_database, "insert into crm.contact_point (kind, value_norm, value_display, person_id, "
                                "usage, confirmation, created_at) values ('phone', '+56911110000', "
                                "'+56 9 1111 0000', %s, 'personal', 'confirmed', now() - interval '3 days')", (person,))
    card, _ = _card(disposable_database, case_id)
    assert (card["contact"]["phone"], card["contact"]["phone_confirmed"]) == ("+56 9 1111 0000", True)


@needs_db
def test_contact_without_phone_has_nulls(disposable_database, world) -> None:
    case_id, _ = _open_case(_repo(disposable_database), world, "Caso sin teléfono")
    person = _owner(disposable_database, "insert into crm.person (display_name, confirmation) values "
                                         "('Persona Ficticia', 'machine_proposed') returning id")[0][0]
    _owner(disposable_database, "insert into crm.opportunity_participant (opportunity_id, person_id, role, "
                                "is_primary, valid_from, confirmation) values (%s, %s, 'quote_recipient', true, "
                                "current_date, 'machine_proposed')", (case_id, person))
    card, _ = _card(disposable_database, case_id)
    assert (card["contact"]["phone"], card["contact"]["phone_confirmed"]) == (None, None)


@needs_db
def test_call_counts_several_today(disposable_database, world) -> None:
    case_id, _ = _open_case(_repo(disposable_database), world, "Caso con llamadas")
    op = world["operator_id"]
    for minutes in (5, 60, 120):
        _call(disposable_database, case_id, op, NOW - timedelta(minutes=minutes))
    _call(disposable_database, case_id, op, NOW - timedelta(days=1, hours=2), "contesto")
    card, _ = _card(disposable_database, case_id)
    assert card["call_counts"] == {"total": 4, "today": 3}
    assert card["last_call_any"]["outcome"] == "no_contesto"
    assert card["last_call_any"]["author_name"] == "Pytest Operator"
    assert card["last_call_any"]["at"].endswith("Z")
    assert card["last_call"] is None or card["last_call"]["outcome"] == "contesto"


@needs_db
def test_call_today_uses_santiago_day(disposable_database, world) -> None:
    case_id, _ = _open_case(_repo(disposable_database), world, "Caso a medianoche")
    # 23:30 on the 17th in Santiago is already the 18th in UTC.
    _call(disposable_database, case_id, world["operator_id"], datetime(2026, 11, 17, 23, 30, tzinfo=SANTIAGO))
    card, payload = _card(disposable_database, case_id)
    assert card["call_counts"] == {"total": 1, "today": 0}
    assert all(r["operator_id"] != world["operator_id"] or r["today"] == 0
               for r in payload["calls_by_operator"])


@needs_db
def test_calls_by_operator_today(disposable_database, world) -> None:
    case_id, _ = _open_case(_repo(disposable_database), world, "Caso de dos autores")
    for _ in range(2):
        _call(disposable_database, case_id, world["operator_id"], NOW - timedelta(hours=1))
    _call(disposable_database, case_id, world["other_operator_id"], NOW - timedelta(hours=2))
    _, payload = _card(disposable_database, case_id)
    by_op = {r["operator_id"]: r for r in payload["calls_by_operator"]}
    assert by_op[world["operator_id"]]["today"] == 2
    assert by_op[world["other_operator_id"]]["today"] == 1
    assert by_op[world["operator_id"]]["operator_name"] == "Pytest Operator"
