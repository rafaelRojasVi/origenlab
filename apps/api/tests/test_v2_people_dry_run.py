"""The people rules' dry run (people first, Task 10): what the rules would do, read-only, masked.

Every name, address and institution here is fictitious (`.invalid`, «Universidad Ficticia»).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from origenlab_api.v2.people_dry_run import mask_address, mask_name, report
from origenlab_api.v2.people_rules import (
    ContactPointRow,
    OrgRow,
    PeopleSnapshot,
    PersonRow,
    Reading,
    name_norm,
    org_key,
    plan_people,
)

from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

UF = "org-uf"
OLD = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _reading(address: str, n: int, *, header=None, sig_name=None, institution=None) -> Reading:
    signature = {"name": sig_name, "title": None, "institution": institution, "phone": None} if sig_name else None
    return Reading(assertion_id=f"a-{n}", source_record_id=f"s-{n}", message_participant_ids=(),
                   value={"version": 1, "sender_address": address, "header_name": header, "signature": signature,
                          "co_addresses": [], "signature_raw": ""})


def _snapshot() -> PeopleSnapshot:
    orgs = {UF: OrgRow(id=UF, name="Universidad Ficticia", status="active")}
    persons = {"p-1": PersonRow(id="p-1", display_name="Marta Rivas Lagos", confirmation="confirmed",
                                status="active", merged_into=None, created_at=OLD)}
    readings = (
        # P3: a full name nobody has, of an institution in the CRM.
        _reading("pjsoto@hotmail.invalid", 1, header="Juan Pedro Soto", sig_name="Juan Pedro Soto",
                 institution="Universidad Ficticia"),
        # P1: a known address.
        _reading("mrivas@ficticia.invalid", 2, header="Marta Rivas Lagos"),
        # W1: only the first name matches.
        _reading("marta.x@hotmail.invalid", 3, header="Marta"),
    )
    return PeopleSnapshot(
        readings=readings, persons=persons, names={name_norm("Marta Rivas Lagos"): {"p-1"}},
        contact_points={"mrivas@ficticia.invalid": ContactPointRow(
            id="cp-1", value_norm="mrivas@ficticia.invalid", person_id="p-1", organization_id=UF,
            usage="work", status="active")},
        affiliations={"p-1": {UF}}, organizations=orgs, org_by_name={org_key("Universidad Ficticia"): {UF}},
        org_by_domain={"ficticia.invalid": UF}, answered={},
    )


@pytest.fixture(scope="module")
def db():
    yield from build_disposable_database()


def test_mask_address_keeps_the_first_letter_and_the_domains_first_label() -> None:
    assert mask_address("pjsoto@hotmail.invalid") == "p***@hotmail…"
    assert mask_address("a@b") == "a***@b…"
    assert mask_address("") == "—"


def test_mask_name_is_initials() -> None:
    assert mask_name("Juan Pedro Soto") == "J. P. S."
    assert mask_name(None) == "—"


def test_dry_run_masks_addresses() -> None:
    snap = _snapshot()
    actions = plan_people(snap)
    text = report(snap, actions)
    assert "readings: 3" in text
    assert "P3 strong: 1" in text and "P1 strong: 1" in text and "W1 weak: 1" in text
    assert "p***@hotmail…" in text and "J. P. S." in text and "M. R. L." in text
    for secret in ("pjsoto", "hotmail.invalid", "mrivas", "Juan", "Soto", "Marta", "Rivas"):
        assert secret not in text, secret


def test_the_report_shows_at_most_twenty_sample_actions() -> None:
    snap = _snapshot()
    actions = plan_people(snap) * 10
    text = report(snap, actions)
    assert text.count("\n  - ") == 20


def test_the_samples_show_every_rule_before_p1() -> None:
    from origenlab_api.v2.people_dry_run import samples_by_rule
    from origenlab_api.v2.people_rules import PeopleAction

    def act(rule: str, n: int) -> PeopleAction:
        return PeopleAction(rule_id=rule, tier="weak" if rule == "W1" else "strong", assertion_id=f"a-{rule}-{n}",
                            address=f"x{n}@ficticia.invalid", person_id=None, fields={}, reasons=())

    actions = [act("P1", n) for n in range(200)] + [act(r, n) for r in ("P1b", "P2", "P3", "P4", "P5", "W1")
                                                    for n in range(10)]
    shown = samples_by_rule(actions)
    assert len(shown) == 20
    assert [a.rule_id for a in shown] == ["P5"] * 4 + ["P3"] * 4 + ["P2"] * 4 + ["P4"] * 4 + ["P1b"] * 4
    few = [act("P1", n) for n in range(30)] + [act("P5", 1), act("W1", 1)]
    assert [a.rule_id for a in samples_by_rule(few)] == ["P5", "W1"] + ["P1"] * 18


@needs_db
def test_the_dry_run_reads_a_real_database_in_a_read_only_transaction_and_writes_nothing(db) -> None:
    import json
    import uuid

    import psycopg

    from origenlab_api.v2.people_dry_run import dry_run

    gid = uuid.uuid4().hex[:16]
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("set role origenlab_owner")
        record = conn.execute(
            "insert into evidence.source_record (kind, dedupe_key, payload, source_uri) "
            "values ('gmail_message', %s, %s::jsonb, %s) returning id::text",
            (f"gmail_message:{gid}", json.dumps({"gmail_message_id": gid}), f"gmail://msg/{gid}")).fetchone()[0]
        value = {"version": 1, "sender_address": f"x{gid}@hotmail.invalid", "header_name": "Rosa Fuentes Mora",
                 "signature": None, "co_addresses": [], "signature_raw": ""}
        conn.execute("insert into evidence.assertion (source_record_id, kind, value_norm, value) "
                     "values (%s, 'message_triage', 'people:v1', %s::jsonb)", (record, json.dumps(value)))

    def counts() -> tuple:
        with psycopg.connect(db, autocommit=True) as conn:
            conn.execute("set role origenlab_owner")
            return conn.execute("select (select count(*) from platform.command_receipt), "
                                "(select count(*) from crm.person), (select count(*) from crm.contact_point)"
                                ).fetchone()

    before = counts()
    text = dry_run(psycopg.connect, runtime_dsn(db))
    assert "readings: " in text and gid not in text and "Rosa" not in text
    assert counts() == before


def test_main_without_a_database_url_fails_closed(monkeypatch, capsys) -> None:
    from origenlab_api.settings import get_settings
    from origenlab_api.v2.people_dry_run import main

    monkeypatch.delenv("ORIGENLAB_V2_DATABASE_URL", raising=False)
    get_settings.cache_clear()
    try:
        assert main([]) == 2
    finally:
        get_settings.cache_clear()
    assert "ORIGENLAB_V2_DATABASE_URL" in capsys.readouterr().err


@needs_db
def test_the_dry_run_transaction_is_read_only_and_honours_the_timeout(db) -> None:
    import psycopg

    from origenlab_api.v2.people_dry_run import read_only

    with read_only(psycopg.connect, runtime_dsn(db), 4321) as cur:
        cur.execute("show statement_timeout")
        assert cur.fetchone()[0] == "4321ms"
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            cur.execute("insert into platform.command_receipt (command_name) values ('x')")
