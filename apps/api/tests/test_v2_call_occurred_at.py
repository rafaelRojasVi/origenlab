"""A «Llamada» logged after the fact: `occurred_at` says when the call happened.

The body rule (a call only, not in the future, at most 60 days back) is checked without a
database; the stored row, the event, a revision and the Seguimiento/Hoy reads are proven against
the disposable database as the real `origenlab_api` role. Every value is fictitious.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta, timezone

import psycopg
import pytest
from pydantic import ValidationError

from origenlab_api.v2.call_outcome import check_call_time
from origenlab_api.v2.commands import request_digest
from origenlab_api.v2.crm_authoring import V2CrmAuthoringRepository
from origenlab_api.v2.crm_authoring_routes import AddNoteBody
from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository
from origenlab_api.v2.identity import OperatorIdentity
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

SUBJECT = "00000000-0000-4000-8000-0000000000aa"
SANTIAGO = timezone(timedelta(hours=-3))


def _body(**over) -> dict:
    return {"subject_kind": "opportunity", "subject_id": SUBJECT, "body": "Hablamos ayer.",
            "note_kind": "llamada", "call_outcome": "contesto", **over}


# ─────────────────────────────────────────────────────────────── body validation ──


def test_a_call_may_carry_when_it_happened() -> None:
    at = datetime.now(UTC) - timedelta(days=1)
    assert AddNoteBody(**_body(occurred_at=at.isoformat())).occurred_at == at


def test_a_call_without_occurred_at_is_unchanged() -> None:
    assert AddNoteBody(**_body()).occurred_at is None


@pytest.mark.parametrize("over", [
    {"note_kind": None, "call_outcome": None},                              # an ordinary note
    {"occurred_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()},  # the future
    {"occurred_at": (datetime.now(UTC) - timedelta(days=61)).isoformat()},  # too far back
    {"occurred_at": "2026-11-18T10:00:00"},                                 # no time zone
])
def test_a_bad_occurred_at_is_refused(over: dict) -> None:
    raw = _body(occurred_at=(datetime.now(UTC) - timedelta(days=1)).isoformat())
    raw.update(over)
    with pytest.raises(ValidationError):
        AddNoteBody(**raw)


def test_the_limits_are_inclusive_of_clock_slack() -> None:
    now = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
    check_call_time("llamada", now + timedelta(minutes=4), now)
    check_call_time("llamada", now - timedelta(days=60), now)
    with pytest.raises(ValueError):
        check_call_time("llamada", now + timedelta(minutes=6), now)


# ───────────────────────────────────────────────────────────────── the database ──


@pytest.fixture(scope="module")
def db():
    yield from build_disposable_database()


@pytest.fixture(scope="module")
def operator(db) -> OperatorIdentity:
    tag = uuid.uuid4().hex[:8]
    with psycopg.connect(db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute("insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                    "values (gen_random_uuid(), %s, 'Vendedora Ficticia', 'sales', 'active') returning id::text",
                    (f"ocurrio-{tag}@example.invalid",))
        op_id = cur.fetchone()[0]
    return OperatorIdentity(operator_id=op_id, email_norm=f"ocurrio-{tag}@example.invalid",
                            display_name="Vendedora Ficticia", role="sales", status="active")


def _owner(dsn: str, sql: str, params: tuple = ()):
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


def _case(dsn: str, operator: OperatorIdentity) -> str:
    return _owner(dsn, "insert into crm.opportunity (title, stage, owner_operator_id) "
                       "values ('Equipo ficticio', 'lead', %s) returning id::text", (operator.operator_id,))[0][0]


def _add(dsn: str, operator: OperatorIdentity, **raw) -> dict:
    body = AddNoteBody(**raw)
    return V2CrmAuthoringRepository(psycopg.connect, runtime_dsn(dsn)).execute(
        command_name="add-note", operator=operator, fields=body.model_dump(),
        idempotency_key=uuid.uuid4().hex, digest=request_digest("add-note", body))


def _card(dsn: str, case_id: str, now: datetime) -> tuple[dict, dict]:
    payload = CrmWorkspaceRepository(psycopg.connect, runtime_dsn(dsn)).pipeline(now=now)
    [card] = [c for c in payload["items"] if c["opportunity_id"] == case_id]
    return card, payload


@needs_db
def test_the_call_time_is_stored_and_the_event_carries_it(db, operator) -> None:
    at = datetime.now(UTC).replace(microsecond=0) - timedelta(days=2)
    out = _add(db, operator, **_body(subject_id=_case(db, operator), occurred_at=at.isoformat()))
    [(stored,)] = _owner(db, "select occurred_at from crm.note where id = %s", (out["note_id"],))
    assert stored == at
    [(payload,)] = _owner(db, "select payload from crm.domain_event where aggregate_kind = 'note' "
                              "and aggregate_id = %s", (out["note_id"],))
    assert datetime.fromisoformat(payload["occurred_at"]) == at


@needs_db
def test_a_revision_keeps_the_call_time(db, operator) -> None:
    at = datetime.now(UTC).replace(microsecond=0) - timedelta(days=1)
    out = _add(db, operator, **_body(subject_id=_case(db, operator), occurred_at=at.isoformat()))
    revised = V2CrmAuthoringRepository(psycopg.connect, runtime_dsn(db)).execute(
        command_name="revise-note", operator=operator,
        fields={"note_id": out["note_id"], "expected_version": 1, "body": "Corregida."},
        idempotency_key=uuid.uuid4().hex, digest="f" * 64)
    [(stored,)] = _owner(db, "select occurred_at from crm.note where id = %s", (revised["note_id"],))
    assert stored == at


@needs_db
def test_a_back_dated_call_counts_on_its_own_day(db, operator) -> None:
    """Logged now, happened yesterday: not today's call, but the case's last call is yesterday's."""
    case_id = _case(db, operator)
    now = datetime.now(SANTIAGO)
    yesterday = now - timedelta(days=1)
    _add(db, operator, **_body(subject_id=case_id, occurred_at=yesterday.isoformat()))
    card, payload = _card(db, case_id, now)
    assert card["call_counts"] == {"total": 1, "today": 0}
    assert datetime.fromisoformat(card["last_call_any"]["at"].replace("Z", "+00:00")) == \
        yesterday.astimezone(UTC).replace(microsecond=0)
    assert card["last_call"] is not None  # contestó: Hoy's answered-call touch, dated yesterday
    mine = [r for r in payload["calls_by_operator"] if r["operator_id"] == operator.operator_id]
    assert all(r["today"] == 0 for r in mine)
    day = yesterday.date().isoformat()
    assert any(r["day"] == day and r["outcome"] == "contesto" for r in payload["calls"])


@needs_db
def test_the_newest_call_by_when_it_happened_wins(db, operator) -> None:
    """A call logged later but dated earlier does not become the case's last call."""
    case_id = _case(db, operator)
    now = datetime.now(SANTIAGO)
    _add(db, operator, **_body(subject_id=case_id, call_outcome="buzon"))  # just now
    _add(db, operator, **_body(subject_id=case_id, occurred_at=(now - timedelta(days=3)).isoformat()))
    card, _ = _card(db, case_id, now)
    assert card["call_counts"] == {"total": 2, "today": 1}
    assert card["last_call_any"]["outcome"] == "buzon"


@needs_db
def test_the_case_notes_expose_occurred_at(db, operator) -> None:
    case_id = _case(db, operator)
    at = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=5)
    _add(db, operator, **_body(subject_id=case_id, body="Con fecha.", occurred_at=at.isoformat()))
    _add(db, operator, **_body(subject_id=case_id, body="Sin fecha."))
    notes = {n["body"]: n for n in CrmWorkspaceRepository(psycopg.connect, runtime_dsn(db))
             .opportunity_notes(case_id)["notes"]}
    assert datetime.fromisoformat(notes["Con fecha."]["occurred_at"]) == at
    assert notes["Sin fecha."]["occurred_at"] is None
