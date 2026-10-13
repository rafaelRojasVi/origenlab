"""«Completar con la cotización del correo»: one command, one transaction, from a sent quote PDF to a
case in «Enviada» with its institution and its quote. Every value is invented (public repository)."""

from __future__ import annotations

import hashlib
import itertools
import json
import uuid

import pytest
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

from origenlab_api.v2.case_commands import (
    COMPLETE_CASE_FROM_SENT_QUOTE,
    OPEN_COMMERCIAL_CASE,
    CompleteCaseFromSentQuoteBody,
    OpenCommercialCaseBody,
    validated_case,
)
from origenlab_api.v2.commands import CommandRefused, request_digest
from origenlab_api.v2.identity import OperatorIdentity

pytestmark = needs_db
SENT = "2026-10-06T15:00:00+00:00"
_SERIALS = itertools.count(7100, 10)
_KEYS = itertools.count(1)


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


def _sent(cur, tag: str, tokens: list[str]) -> str:
    """A sent Gmail message carrying one quote PDF per token."""
    docs = [{"sha256": hashlib.sha256(f"{t}{tag}".encode()).hexdigest(), "filename": f"{t}-Persona Ficticia.pdf",
             "cn_tokens": [t]} for t in tokens]
    payload = {"subject_raw": "Cotización", "sent_at": SENT, "documents": docs}
    cur.execute("insert into evidence.source_record (kind, dedupe_key, payload, source_uri) "
                "values ('gmail_message', %s, %s::jsonb, %s) returning id::text",
                (f"pytest-cs:{tag}:{uuid.uuid4().hex[:8]}", json.dumps(payload), f"gmail://msg/cs{tag}"))
    return cur.fetchone()[0]


@pytest.fixture
def world(db):
    tag = uuid.uuid4().hex[:10]
    serial = next(_SERIALS)
    w: dict[str, object] = {"tag": tag, "token": f"CN0{serial}", "number": f"0{serial}-26"}
    with _owner(db) as conn, conn.cursor() as cur:
        cur.execute("insert into platform.operator (auth_user_id, email_norm, display_name, role, status) values "
                    "(gen_random_uuid(), %s, 'Vendedora Ficticia', 'sales', 'active') returning id::text",
                    (f"ventas-{tag}@example.test",))
        w["operator_id"] = cur.fetchone()[0]
        w["record_id"] = _sent(cur, tag, [w["token"]])
        w["two_pdfs"] = _sent(cur, f"{tag}-2", [f"CN0{serial + 1}", f"CN0{serial + 2}"])
        cur.execute("insert into crm.organization (kind, name, confirmation) values ('institution', %s, 'confirmed') "
                    "returning id::text, version", (f"Instituto Ficticio {tag}",))
        w["org_id"], w["org_version"] = cur.fetchone()
    w["sha"] = hashlib.sha256(f"{w['token']}{tag}".encode()).hexdigest()
    return w


def _run(db, world, name, body, key=None):
    import psycopg

    from origenlab_api.v2.case_command_repository import V2CaseCommandRepository

    operator = OperatorIdentity(operator_id=world["operator_id"], email_norm="ventas@example.test",
                                display_name="Vendedora Ficticia", role="sales", status="active")
    return V2CaseCommandRepository(psycopg.connect, runtime_dsn(db)).execute(
        command_name=name, operator=operator, fields=validated_case(name, body),
        idempotency_key=key or f"pytest-cs-{next(_KEYS)}", digest=request_digest(name, body))


def _complete(db, world, key=None, **body):
    return _run(db, world, COMPLETE_CASE_FROM_SENT_QUOTE,
                CompleteCaseFromSentQuoteBody(note="Completado desde el correo", **body), key)


def _case(db, case_id):
    return _one(db, "select o.stage, o.organization_id::text, org.name, org.confirmation, "
                    "(select string_agg(q.quote_number || ':' || r.status, ',') from crm.quote q "
                    "   join crm.quote_revision r on r.quote_id = q.id where q.opportunity_id = o.id) "
                    "from crm.opportunity o left join crm.organization org on org.id = o.organization_id where o.id = %s",
                case_id)


def test_from_the_email_alone_it_opens_the_case_creates_the_institution_and_records_the_quote(db, world) -> None:
    out = _complete(db, world, source_record_id=world["record_id"],
                    new_organization_name=f"Instituto Nuevo {world['tag']} (INF)")
    stage, org_id, name, confirmation, quotes = _case(db, out["opportunity_id"])
    assert (stage, name, confirmation) == ("quoting", f"Instituto Nuevo {world['tag']} (INF)", "machine_proposed")
    assert quotes == f"{world['number']}:sent"
    assert out["quote_number"] == world["number"] and out["organization_id"] == org_id


def test_on_an_open_case_with_an_existing_institution(db, world) -> None:
    opened = _run(db, world, OPEN_COMMERCIAL_CASE, OpenCommercialCaseBody(
        title="Cotización ficticia", origin_source_record_id=world["record_id"], note="abierto a mano"))
    out = _complete(db, world, source_record_id=world["record_id"], opportunity_id=opened["opportunity_id"],
                    opportunity_version=opened["opportunity_version"],
                    organization_id=world["org_id"], organization_version=world["org_version"])
    stage, org_id, _, _, quotes = _case(db, opened["opportunity_id"])
    assert (stage, org_id, quotes) == ("quoting", world["org_id"], f"{world['number']}:sent")
    assert out["opportunity_version"] > opened["opportunity_version"]


def test_a_new_name_that_already_names_an_institution_uses_it(db, world) -> None:
    out = _complete(db, world, source_record_id=world["record_id"], new_organization_name=f"instituto ficticio {world['tag']}")
    assert out["organization_id"] == world["org_id"]


def test_the_same_key_replays(db, world) -> None:
    first = _complete(db, world, key="pytest-cs-replay-" + world["tag"], source_record_id=world["record_id"],
                      organization_id=world["org_id"], organization_version=world["org_version"])
    again = _complete(db, world, key="pytest-cs-replay-" + world["tag"], source_record_id=world["record_id"],
                      organization_id=world["org_id"], organization_version=world["org_version"])
    assert again["replayed"] is True and again["opportunity_id"] == first["opportunity_id"]


def test_refusals_leave_nothing_behind(db, world) -> None:
    before = _one(db, "select (select count(*) from crm.opportunity), (select count(*) from crm.organization)")
    with pytest.raises(CommandRefused) as two:
        _complete(db, world, source_record_id=world["two_pdfs"], new_organization_name="Instituto Que No Debe Existir (IQN)")
    assert two.value.code == "not_one_quote_pdf"
    # The quote number already on another case: the whole command rolls back, institution included.
    _complete(db, world, source_record_id=world["record_id"], organization_id=world["org_id"],
              organization_version=world["org_version"])
    mid = _one(db, "select (select count(*) from crm.opportunity), (select count(*) from crm.organization)")
    with pytest.raises(CommandRefused) as taken:
        _complete(db, world, source_record_id=world["record_id"], new_organization_name="Otro Instituto Ficticio (OIF)")
    assert taken.value.code in {"message_already_on_a_case", "document_already_recorded", "number_on_other_opportunity"}
    assert _one(db, "select (select count(*) from crm.opportunity), (select count(*) from crm.organization)") == mid
    assert mid[0] == before[0] + 1 and mid[1] == before[1]


def test_the_body_names_one_institution_and_a_case_with_its_version() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CompleteCaseFromSentQuoteBody(note="x", source_record_id=str(uuid.uuid4()))
    with pytest.raises(ValidationError):
        CompleteCaseFromSentQuoteBody(note="x", source_record_id=str(uuid.uuid4()), organization_id=str(uuid.uuid4()),
                                      organization_version=1, new_organization_name="Instituto Ficticio (IF)")
    with pytest.raises(ValidationError):
        CompleteCaseFromSentQuoteBody(note="x", source_record_id=str(uuid.uuid4()), opportunity_id=str(uuid.uuid4()),
                                      new_organization_name="Instituto Ficticio (IF)")


def test_the_derived_number_is_recorded_as_derived_with_its_printed_token(db, world) -> None:
    out = _complete(db, world, source_record_id=world["record_id"], organization_id=world["org_id"],
                    organization_version=world["org_version"])
    printed, derivation = _one(db, "select e.payload -> 'printed_quote_numbers', e.payload -> 'quote_number_derivation' "
                                   "from crm.domain_event e where e.event_type = 'quote.created' "
                                   "and e.payload ->> 'opportunity_id' = %s", out["opportunity_id"])
    assert printed == [world["token"]]
    assert derivation["token"] == world["token"] and derivation["year_from_sent_at"] == 26


def test_a_correlative_already_quoted_under_another_year_waits_for_a_person(db, world) -> None:
    other = _complete(db, world, source_record_id=world["record_id"], organization_id=world["org_id"],
                      organization_version=world["org_version"])
    with _owner(db) as conn:
        conn.execute("update crm.quote set quote_number = replace(quote_number, '-26', '-25') where opportunity_id = %s",
                     (other["opportunity_id"],))
        cur = conn.cursor()
        again = _sent(cur, world["tag"] + "-y", [world["token"]])
    with pytest.raises(CommandRefused) as refused:
        _complete(db, world, source_record_id=again, organization_id=world["org_id"], organization_version=world["org_version"])
    assert refused.value.code == "correlative_exists_other_year"


def test_a_message_already_on_an_open_case_is_completed_there_not_opened_again(db, world) -> None:
    opened = _run(db, world, OPEN_COMMERCIAL_CASE, OpenCommercialCaseBody(
        title="Cotización ficticia", origin_source_record_id=world["record_id"], note="abierto a mano"))
    with pytest.raises(CommandRefused) as refused:
        _complete(db, world, source_record_id=world["record_id"], organization_id=world["org_id"],
                  organization_version=world["org_version"])
    assert refused.value.code == "message_already_on_a_case"
    assert opened["opportunity_id"]

