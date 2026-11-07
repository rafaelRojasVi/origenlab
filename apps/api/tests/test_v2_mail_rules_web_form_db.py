"""A web-form message's consent and product, recorded by R7/R12 in the rule's own transaction.

Runs in a disposable `origenlab_test_<hex>` as the unprivileged `origenlab_api` role. The worker's
`web_form:v1` reading is written here the way its triage writes it. Every name, address and
institution is fictitious.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from test_v2_mail_rules_db import (  # noqa: F401 - `db` and `world` are the module's fixtures, reused
    _all,
    _apply_only,
        _operator,
    _owner,
    _repo,
    _web_form_mail,
    db,
    world,
)
from v2_command_harness import needs_db

pytestmark = needs_db

VERSION = "consent-v1-2026-11"
STAMP = "2026-11-08T10:15:00-03:00"


def _facts(db, record, **facts) -> None:
    with _owner(db) as conn:
        conn.execute(
            "insert into evidence.assertion (source_record_id, kind, value_norm, value) "
            "values (%s, 'message_triage', 'web_form:v1', %s::jsonb)",
            (record, json.dumps({"version": 1, **facts})))


def _consents(db, record):
    return _all(db, "select email, purpose, text_version, accepted_at, source, source_record_id::text "
                    "from crm.consent where source_record_id = %s", record)


def _interests(db, case_id):
    return _all(db, "select model_text, confirmation from crm.opportunity_interest where opportunity_id = %s",
                case_id)


def _form(db, world, **facts):
    visitor = f"ana-{uuid.uuid4().hex[:6]}@{world['new_domain']}"
    with _owner(db) as conn:
        record = _web_form_mail(conn, reply_to=visitor)
    if facts:
        _facts(db, record, **facts)
    return record, visitor


def test_r7_records_the_consent_and_the_product_once_and_replaying_adds_nothing(db, world) -> None:
    record, visitor = _form(db, world, product="UP400St", consent_version=VERSION, accepted_at=STAMP)
    applied = _apply_only(db, world, record)
    assert applied["rule_id"] == "R7"
    consents = _consents(db, record)
    assert len(consents) == 1
    email, purpose, version, accepted_at, source, source_record = consents[0]
    assert (email, purpose, version, source, source_record) == (visitor, "quote_request", VERSION,
                                                                "web-quote-form/v1", record)
    assert accepted_at == datetime.fromisoformat(STAMP)
    assert _interests(db, applied["case_id"]) == [("UP400St", "confirmed")]
    # The same apply again is a replay of the receipt: nothing more is written.
    # Applying again: the email is acted on, the rules plan nothing for it any more.
    assert _planned_for(_repo(db), record) is None
    assert len(_consents(db, record)) == 1
    assert len(_interests(db, applied["case_id"])) == 1


def _planned_for(repo, record):
    actions, _display, _snapshot = repo.plan()
    return next((a for a in actions if a.evidence_id == record), None)


def test_a_second_form_joining_the_case_adds_its_consent_but_not_the_same_interest_again(db, world) -> None:
    first, visitor = _form(db, world, product="UP400St", consent_version=VERSION, accepted_at=STAMP)
    case_id = _apply_only(db, world, first)["case_id"]
    with _owner(db) as conn:
        second = _web_form_mail(conn, reply_to=visitor)
    _facts(db, second, product=" up400st ", consent_version=VERSION)
    joined = _apply_only(db, world, second)
    assert (joined["rule_id"], joined["case_id"]) == ("R12", case_id)
    assert len(_consents(db, second)) == 1
    # No time on the line: the message's own time stands in for it.
    assert _consents(db, second)[0][3].isoformat().startswith("2026-10-01T15:00:00")
    assert _interests(db, case_id) == [("UP400St", "confirmed")]
    # A different product is a second interest.
    with _owner(db) as conn:
        third = _web_form_mail(conn, reply_to=visitor)
    _facts(db, third, product="UP200St")
    _apply_only(db, world, third)
    assert sorted(m for m, _ in _interests(db, case_id)) == ["UP200St", "UP400St"]
    assert _consents(db, third) == []  # no consent line, no consent row


def test_a_web_form_without_the_lines_changes_nothing(db, world) -> None:
    record, _visitor = _form(db, world)
    applied = _apply_only(db, world, record)
    assert applied["rule_id"] == "R7"
    assert [s["command"] for s in applied["steps"]][-1] != "record_web_form_consent"
    assert _consents(db, record) == []
    assert _interests(db, applied["case_id"]) == []


def test_the_repository_methods_lower_case_the_address_and_are_idempotent(db, world) -> None:
    record, _visitor = _form(db, world)
    case_id = _apply_only(db, world, record)["case_id"]
    repo = _repo(db)
    operator = _operator(world["admin"])
    address = f"Ana.Mayus-{uuid.uuid4().hex[:6]}@Cliente-Ficticio.example.test"
    with repo._write() as cur:
        assert repo.write_web_form_consent(cur, email=address, text_version=VERSION, accepted_at=None,
                                           source_record_id=record) is True
        assert repo.write_web_form_consent(cur, email=address, text_version=VERSION, accepted_at=None,
                                           source_record_id=record) is False
        receipt = cur.execute("select id::text from platform.command_receipt limit 1").fetchone()[0]
        first = repo.maybe_record_case_interest(cur, opportunity_id=case_id, model_text="MB32", operator=operator,
                                                receipt_id=receipt, note="prueba")
        second = repo.maybe_record_case_interest(cur, opportunity_id=case_id, model_text="mb32", operator=operator,
                                                 receipt_id=receipt, note="prueba")
    assert first is not None and second is None
    rows = _all(db, "select email from crm.consent where source_record_id = %s and email like 'ana.mayus%%'", record)
    assert rows == [(address.lower(),)]
    assert _interests(db, case_id) == [("MB32", "confirmed")]
