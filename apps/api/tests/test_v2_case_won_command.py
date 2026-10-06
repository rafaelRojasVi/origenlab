"""`record_case_won` — a person marks a case won, against a database.

The in-process rules (request shape, the route, the switch) live in
`test_v2_case_command_boundary.py`. This module proves what only PostgreSQL can: the case moves
to `won` with the named revision, a refused win writes nothing, a stale version is a 409, a
replay decides nothing twice, and every event is the operator's — not «Sistema (correo)», which
is what the same writer records when the email → cases rules call it.

Runs in a disposable `origenlab_test_<hex>` (tests/v2_command_harness.py) as the unprivileged
`origenlab_api` role. Every name, number and address is fictitious.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

import pytest

from origenlab_api.v2.case_commands import (
    ADD_CASE_ORGANIZATION,
    ADVANCE_CASE_STAGE,
    OPEN_COMMERCIAL_CASE,
    RECORD_CASE_WON,
    AddCaseOrganizationBody,
    AdvanceCaseStageBody,
    OpenCommercialCaseBody,
    RecordCaseWonBody,
    validated_case,
)
from origenlab_api.v2.commands import CommandRefused, request_digest
from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2.quote_import_commands import (
    RECORD_HISTORICAL_QUOTATION,
    RecordHistoricalQuotationBody,
    validated_quote_import,
)
from origenlab_api.v2.quote_import_repository import document_reference_value
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

pytestmark = needs_db

SENT = datetime(2026, 9, 25, 15, 0, tzinfo=UTC)
_KEYS = iter(range(1, 1_000_000))


@pytest.fixture(scope="module")
def db():
    yield from build_disposable_database()


@pytest.fixture
def world(db):
    """One sales operator, one institution, one email carrying three quote PDFs."""
    import psycopg

    tag = uuid.uuid4().hex[:12]
    sha = {k: hashlib.sha256(f"{k}{tag}".encode()).hexdigest() for k in "abc"}
    w: dict[str, object] = {"tag": tag, **{f"sha_{k}": v for k, v in sha.items()},
                            "number": f"0{tag[:5]}-26", "other_number": f"1{tag[:5]}-26"}
    with psycopg.connect(db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
            "values (gen_random_uuid(), %s, 'Vendedora Ficticia', 'sales', 'active') returning id::text",
            (f"ventas-{tag}@example.test",),
        )
        w["operator_id"] = cur.fetchone()[0]
        payload = {"subject": "pytest", "documents": [{"sha256": s, "filename": f"{s[:4]}.pdf"}
                                                      for s in sha.values()]}
        cur.execute(
            "insert into evidence.source_record (kind, dedupe_key, payload, source_uri) "
            "values ('gmail_message', %s, %s::jsonb, %s) returning id::text",
            (f"pytest-won:{tag}", json.dumps(payload), f"gmail://msg/won{tag}"),
        )
        w["record_id"] = cur.fetchone()[0]
        for s in sha.values():
            cur.execute(
                "insert into evidence.assertion (source_record_id, kind, value_norm, value) "
                "values (%s, 'document_reference', %s, '{}'::jsonb)",
                (w["record_id"], document_reference_value(s)),
            )
        cur.execute(
            "insert into crm.organization (kind, name, confirmation) values "
            "('institution', %s, 'confirmed') returning id::text, version",
            (f"Universidad Ficticia {tag}",),
        )
        w["org_id"], w["org_version"] = cur.fetchone()
    return w


def _operator(world) -> OperatorIdentity:
    return OperatorIdentity(operator_id=world["operator_id"], email_norm="ventas@example.test",
                            display_name="Vendedora Ficticia", role="sales", status="active")


def _repos(db):
    import psycopg

    from origenlab_api.v2.case_command_repository import V2CaseCommandRepository
    from origenlab_api.v2.quote_import_repository import V2QuoteImportRepository

    dsn = runtime_dsn(db)
    return V2CaseCommandRepository(psycopg.connect, dsn), V2QuoteImportRepository(psycopg.connect, dsn)


def _case(repo, name, body, world, key=None):
    return repo.execute(command_name=name, operator=_operator(world), fields=validated_case(name, body),
                        idempotency_key=key or f"pytest-won-{next(_KEYS)}", digest=request_digest(name, body))


def _quote(repo, world, case_id, sha, number, supersedes=None):
    body = RecordHistoricalQuotationBody(
        opportunity_id=case_id, quote_number=number, printed_quote_numbers=[number],
        document_sha256=sha, sent_at=SENT, origin_source_record_id=world["record_id"],
        ledger_decision_id=f"d-{next(_KEYS)}", supersedes_document_sha256=supersedes,
        note="enviada al cliente",
    )
    return repo.execute(command_name=RECORD_HISTORICAL_QUOTATION, operator=_operator(world),
                        fields=validated_quote_import(RECORD_HISTORICAL_QUOTATION, body),
                        idempotency_key=f"pytest-won-q-{next(_KEYS)}",
                        digest=request_digest(RECORD_HISTORICAL_QUOTATION, body))


def _case_at(db, world, stop_at: str) -> tuple[str, int]:
    """A case with a confirmed requester, walked to `stop_at` (`quoting` or `negotiating`)."""
    cases, _ = _repos(db)
    opened = _case(cases, OPEN_COMMERCIAL_CASE, OpenCommercialCaseBody(
        title="Equipo de laboratorio", origin_source_record_id=world["record_id"], note="correo"), world)
    case_id, version = opened["opportunity_id"], opened["opportunity_version"]
    added = _case(cases, ADD_CASE_ORGANIZATION, AddCaseOrganizationBody(
        opportunity_id=case_id, opportunity_version=version, organization_id=world["org_id"],
        organization_version=world["org_version"], role="requesting_institution", note="confirmada"), world)
    version = added["opportunity_version"]
    stages = ["qualifying", "qualified", "quoting"] + (["negotiating"] if stop_at == "negotiating" else [])
    for stage in stages:
        version = _case(cases, ADVANCE_CASE_STAGE, AdvanceCaseStageBody(
            opportunity_id=case_id, opportunity_version=version, stage=stage, note="avanza"), world
        )["opportunity_version"]
    return case_id, version


def _quoted_case(db, world, stop_at="negotiating"):
    """The case at `stop_at`, with r1 (superseded by r2) of one quote: r2 is the current one."""
    case_id, _ = _case_at(db, world, "quoting")
    _, quotes = _repos(db)
    r1 = _quote(quotes, world, case_id, world["sha_a"], world["number"])
    _quote(quotes, world, case_id, world["sha_b"], world["number"], supersedes=world["sha_a"])
    cases, _ = _repos(db)
    version = _one(db, "select version from crm.opportunity where id = %s", case_id)[0]
    if stop_at == "negotiating":
        version = _case(cases, ADVANCE_CASE_STAGE, AdvanceCaseStageBody(
            opportunity_id=case_id, opportunity_version=version, stage="negotiating", note="negocia"), world
        )["opportunity_version"]
    return case_id, version, r1["quote_id"]


def _one(db, sql, *args):
    import psycopg

    with psycopg.connect(db) as conn:
        conn.execute("set role origenlab_owner")
        return conn.execute(sql, args).fetchone()


def _won(db, world, case_id, version, quote_id, revision_no, key=None):
    cases, _ = _repos(db)
    return _case(cases, RECORD_CASE_WON, RecordCaseWonBody(
        opportunity_id=case_id, opportunity_version=version, quote_id=quote_id,
        revision_no=revision_no, note="llegó la orden de compra"), world, key=key)


def _events(db):
    return _one(db, "select count(*) from crm.domain_event")[0]


def test_the_database_is_disposable(db) -> None:
    assert db.rpartition("/")[2].startswith("origenlab_test_")


def test_a_person_wins_a_case_against_the_current_revision(db, world) -> None:
    case_id, version, quote_id = _quoted_case(db, world)
    result = _won(db, world, case_id, version, quote_id, 2)
    assert result["stage"] == "won" and result["previous_stage"] == "negotiating"
    assert result["won_quote_id"] == quote_id and result["won_revision_no"] == 2
    assert result["opportunity_version"] == version + 1
    assert result["replayed"] is False
    row = _one(db, "select stage, won_quote_id::text, won_revision_no, closed_at is not null, version "
                   "from crm.opportunity where id = %s", case_id)
    assert row == ("won", quote_id, 2, True, version + 1)


def test_the_win_is_the_operators_not_the_systems(db, world) -> None:
    case_id, version, quote_id = _quoted_case(db, world)
    result = _won(db, world, case_id, version, quote_id, 2)
    rows = _one(
        db,
        "select array_agg(event_type order by seq), array_agg(distinct actor_kind), "
        "bool_and(actor_operator_id::text = %s), bool_and(payload -> 'attribution' is null) "
        "from crm.domain_event where command_receipt_id = %s",
        world["operator_id"], result["command_receipt_id"],
    )
    assert rows == (["opportunity.staged", "opportunity.closed"], ["operator"], True, True)
    receipt = _one(db, "select operator_id::text, command_name from platform.command_receipt where id = %s",
                   result["command_receipt_id"])
    assert receipt == (world["operator_id"], RECORD_CASE_WON)


def test_a_case_still_quoting_is_refused_and_nothing_is_written(db, world) -> None:
    case_id, version, quote_id = _quoted_case(db, world, stop_at="quoting")
    before = _events(db)
    with pytest.raises(CommandRefused) as excinfo:
        _won(db, world, case_id, version, quote_id, 2)
    assert (excinfo.value.status_code, excinfo.value.code) == (409, "case_not_negotiating")
    assert _events(db) == before
    assert _one(db, "select stage from crm.opportunity where id = %s", case_id)[0] == "quoting"


def test_a_superseded_revision_is_not_won(db, world) -> None:
    case_id, version, quote_id = _quoted_case(db, world)
    with pytest.raises(CommandRefused) as excinfo:
        _won(db, world, case_id, version, quote_id, 1)
    assert (excinfo.value.status_code, excinfo.value.code) == (409, "quote_revision_not_current")
    assert _one(db, "select stage, won_quote_id from crm.opportunity where id = %s", case_id) == (
        "negotiating", None)


def test_a_revision_of_another_case_or_none_at_all_is_404(db, world) -> None:
    case_id, version, quote_id = _quoted_case(db, world)
    other_case, _ = _case_at(db, world, "quoting")
    _, quotes = _repos(db)
    other_quote = _quote(quotes, world, other_case, world["sha_c"], world["other_number"])["quote_id"]
    for qid, no in ((other_quote, 1), (quote_id, 9)):
        with pytest.raises(CommandRefused) as excinfo:
            _won(db, world, case_id, version, qid, no)
        assert (excinfo.value.status_code, excinfo.value.code) == (404, "quote_revision_not_on_case")


def test_a_stale_version_is_409_and_writes_nothing(db, world) -> None:
    case_id, version, quote_id = _quoted_case(db, world)
    before = _events(db)
    with pytest.raises(CommandRefused) as excinfo:
        _won(db, world, case_id, version - 1, quote_id, 2)
    assert (excinfo.value.status_code, excinfo.value.code) == (409, "case_version_conflict")
    assert _events(db) == before
    assert _one(db, "select count(*) from platform.command_receipt where command_name = %s "
                    "and operator_id = %s and status <> 'completed'", RECORD_CASE_WON, world["operator_id"])[0] == 0


def test_the_same_key_replays_instead_of_winning_twice(db, world) -> None:
    case_id, version, quote_id = _quoted_case(db, world)
    key = f"pytest-won-replay-{world['tag']}"
    first = _won(db, world, case_id, version, quote_id, 2, key=key)
    events = _events(db)
    again = _won(db, world, case_id, version, quote_id, 2, key=key)
    assert again["replayed"] is True
    assert again["command_receipt_id"] == first["command_receipt_id"]
    assert _events(db) == events
    # A new key on a case that is now won is a refusal, not a second win.
    with pytest.raises(CommandRefused) as excinfo:
        _won(db, world, case_id, version + 1, quote_id, 2)
    assert excinfo.value.code == "case_is_closed"

