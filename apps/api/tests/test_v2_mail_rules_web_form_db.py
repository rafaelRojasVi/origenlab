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
    _one,
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


def test_a_proposal_still_records_the_consent_but_no_interest_and_opens_no_case(db, world) -> None:
    shared = f"compartido-{uuid.uuid4().hex[:6]}.test"
    with _owner(db) as conn:
        for n in (1, 2):
            org = conn.execute(
                "insert into crm.organization (kind, name, confirmation) values ('institution', %s, 'confirmed') "
                "returning id::text", (f"Instituto Ficticio {n} {shared}",)).fetchone()[0]
            conn.execute("insert into crm.organization_domain (organization_id, domain_norm, scope) "
                         "values (%s, %s, 'shared')", (org, shared))
        visitor = f"ana@{shared}"
        record = _web_form_mail(conn, reply_to=visitor)
        cases_before = conn.execute("select count(*) from crm.opportunity").fetchone()[0]
    _facts(db, record, product="UP400St", consent_version=VERSION, accepted_at=STAMP)
    repo = _repo(db)
    planned = _planned_for(repo, record)
    assert planned is not None and planned.mode == "proposal"
    assert repo.record_web_form_consents() >= 1
    assert repo.record_web_form_consents() == 0  # recorded once
    assert [c[0] for c in _consents(db, record)] == [visitor]
    assert _one(db, "select count(*) from crm.opportunity")[0] == cases_before
    assert _one(db, "select count(*) from crm.opportunity_interest where model_text = 'UP400St' "
                    "and origin_source_record_id = %s", record)[0] == 0
    assert _planned_for(repo, record).mode == "proposal"  # nothing was applied on it


def test_a_marked_message_from_a_foreign_sender_records_no_consent(db, world) -> None:
    visitor = f"ana-{uuid.uuid4().hex[:6]}@{world['new_domain']}"
    with _owner(db) as conn:
        record = _web_form_mail(conn, reply_to=visitor)
        conn.execute("update evidence.source_record set payload = payload || '{\"sender\": \"x@elsewhere.invalid\"}' "
                     "where id = %s", (record,))
    _facts(db, record, consent_version=VERSION, accepted_at=STAMP)
    _repo(db).record_web_form_consents()
    assert _consents(db, record) == []


def test_a_web_form_without_the_lines_changes_nothing(db, world) -> None:
    record, _visitor = _form(db, world)
    applied = _apply_only(db, world, record)
    assert applied["rule_id"] == "R7"
    assert [s["command"] for s in applied["steps"]][-1] != "record_web_form_interest"
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


# ------------------------------------------------------------------ the consent sweep

def _foreign_sender(db, record) -> None:
    with _owner(db) as conn:
        conn.execute("update evidence.source_record set payload = payload || '{\"sender\": \"x@elsewhere.invalid\"}' "
                     "where id = %s", (record,))


def _drop_reply_to(db, record) -> None:
    with _owner(db) as conn:
        conn.execute(
            "delete from comms.message_participant p using comms.message cm, evidence.source_record sr "
            "where p.message_id = cm.id and p.role = 'reply_to' and sr.id = %s "
            "and cm.provider_message_id = sr.payload ->> 'gmail_message_id'", (record,))


def test_rows_that_can_never_yield_a_consent_do_not_fill_the_batch(db, world) -> None:
    """Newer dead rows (foreign sender, no Reply-To) are excluded in SQL: a batch of one still
    reaches the real submission behind them."""
    _repo(db).record_web_form_consents(limit=10_000)  # clear whatever earlier tests left pending
    good, visitor = _form(db, world, consent_version=VERSION, accepted_at=STAMP)
    for _ in range(3):
        dead, _v = _form(db, world, consent_version=VERSION, accepted_at=STAMP)
        _foreign_sender(db, dead)
    no_reply, _v = _form(db, world, consent_version=VERSION, accepted_at=STAMP)
    _drop_reply_to(db, no_reply)
    assert _repo(db).record_web_form_consents(limit=1) == 1
    assert [c[0] for c in _consents(db, good)] == [visitor]


def test_the_sweep_takes_the_newest_submission_first(db, world) -> None:
    _repo(db).record_web_form_consents(limit=10_000)
    older, _v = _form(db, world, consent_version=VERSION, accepted_at=STAMP)
    newer, _v = _form(db, world, consent_version=VERSION, accepted_at=STAMP)
    assert _repo(db).record_web_form_consents(limit=1) == 1
    assert len(_consents(db, newer)) == 1
    assert _consents(db, older) == []
    assert _repo(db).record_web_form_consents(limit=1) == 1
    assert len(_consents(db, older)) == 1


def test_one_failing_row_does_not_roll_back_the_batch(db, world, monkeypatch) -> None:
    _repo(db).record_web_form_consents(limit=10_000)
    first, _v = _form(db, world, consent_version=VERSION, accepted_at=STAMP)
    bad, _v = _form(db, world, consent_version=VERSION, accepted_at=STAMP)
    last, _v = _form(db, world, consent_version=VERSION, accepted_at=STAMP)
    repo = _repo(db)
    real = repo.write_web_form_consent

    def flaky(cur, **kw):
        if kw["source_record_id"] == bad:
            cur.execute("select 1 / 0")  # aborts the transaction, as a refused insert would
        return real(cur, **kw)

    monkeypatch.setattr(repo, "write_web_form_consent", flaky)
    assert repo.record_web_form_consents() == 2
    assert len(_consents(db, first)) == 1
    assert len(_consents(db, last)) == 1
    assert _consents(db, bad) == []
    monkeypatch.undo()
    assert _repo(db).record_web_form_consents() == 1  # retried on the next sweep
    assert len(_consents(db, bad)) == 1
