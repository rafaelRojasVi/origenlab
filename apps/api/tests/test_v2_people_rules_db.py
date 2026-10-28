"""Who wrote each email, against a real database (people first, Task 5): snapshot, apply, undo.

Runs in a disposable `origenlab_test_<hex>` (tests/v2_command_harness.py) as the unprivileged
`origenlab_api` role, like `test_v2_mail_rules_db.py`. Every name, address and institution is
fictitious; every name carries the test's tag so no two tests' persons ever match each other.
"""

from __future__ import annotations

import json
import uuid

import pytest

from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

pytestmark = needs_db


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


def _all(db, sql, *args):
    with _owner(db) as conn:
        return conn.execute(sql, args).fetchall()


def _repo(db):
    import psycopg

    from origenlab_api.v2.people_rules_repository import PeopleRulesRepository

    return PeopleRulesRepository(psycopg.connect, runtime_dsn(db))


def _authoring(db):
    import psycopg

    from origenlab_api.v2.crm_authoring import V2CrmAuthoringRepository

    return V2CrmAuthoringRepository(psycopg.connect, runtime_dsn(db))


def _operator(operator_id):
    from origenlab_api.v2.identity import OperatorIdentity

    return OperatorIdentity(operator_id=operator_id, email_norm="admin@example.test",
                            display_name="Admin Ficticio", role="admin", status="active")


def _word(tag: str) -> str:
    """A surname only this test uses: letters only, so `name_norm` keeps it whole."""
    return "".join("abcdefghij"[int(c, 16) % 10] for c in tag).capitalize()


@pytest.fixture
def world(db):
    tag = uuid.uuid4().hex[:8]
    w: dict[str, str] = {"tag": tag, "sur": _word(tag), "domain": f"u-{tag}.test",
                         "org_name": f"Universidad Ficticia {_word(tag)}"}
    with _owner(db) as conn:
        w["admin"] = conn.execute(
            "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
            "values (gen_random_uuid(), %s, 'Admin Ficticio', 'admin', 'active') returning id::text",
            (f"admin-{tag}@example.test",)).fetchone()[0]
        w["org"] = conn.execute(
            "insert into crm.organization (kind, name, confirmation) values ('institution', %s, 'confirmed') "
            "returning id::text", (w["org_name"],)).fetchone()[0]
        conn.execute("insert into crm.organization_domain (organization_id, domain_norm, scope) "
                     "values (%s, %s, 'exclusive')", (w["org"], w["domain"]))
        w["other_org"] = conn.execute(
            "insert into crm.organization (kind, name, confirmation) values ('institution', %s, 'confirmed') "
            "returning id::text", (f"Instituto Otro {_word(tag)}",)).fetchone()[0]
    return w


def _reading(db, address, *, header=None, sig_name=None, institution=None, title=None):
    """A live capture with its `from` participant and the worker's `people:v1` reading."""
    gid = uuid.uuid4().hex[:16]
    with _owner(db) as conn:
        record = conn.execute(
            "insert into evidence.source_record (kind, dedupe_key, payload, source_uri) "
            "values ('gmail_message', %s, %s::jsonb, %s) returning id::text",
            (f"gmail_message:{gid}", json.dumps({"gmail_message_id": gid, "sender": address}),
             f"gmail://msg/{gid}")).fetchone()[0]
        mailbox = conn.execute("insert into comms.mailbox (address_norm) values (%s) returning id",
                               (f"mbox-{gid}@example.test",)).fetchone()[0]
        message = conn.execute(
            "insert into comms.message (mailbox_id, provider_message_id, provider_thread_id, direction, internal_date) "
            "values (%s, %s, %s, 'inbound', now()) returning id", (mailbox, gid, f"t-{gid}")).fetchone()[0]
        participant = conn.execute(
            "insert into comms.message_participant (message_id, role, address_norm, display_name) "
            "values (%s, 'from', %s, %s) returning id::text", (message, address, header)).fetchone()[0]
        signature = None
        if sig_name or institution or title:
            signature = {"name": sig_name, "title": title, "institution": institution, "phone": None}
        value = {"version": 1, "sender_address": address, "header_name": header, "signature": signature,
                 "co_addresses": [], "signature_raw": ""}
        assertion = conn.execute(
            "insert into evidence.assertion (source_record_id, kind, value_norm, value) "
            "values (%s, 'message_triage', 'people:v1', %s::jsonb) returning id::text",
            (record, json.dumps(value))).fetchone()[0]
    return {"assertion": assertion, "record": record, "participant": participant}


def _person(db, name, *, address=None, org=None, created_at="2026-01-01", confirmation="machine_proposed"):
    with _owner(db) as conn:
        pid = conn.execute(
            "insert into crm.person (display_name, confirmation, created_at) values (%s, %s, %s::timestamptz) "
            "returning id::text", (name, confirmation, created_at)).fetchone()[0]
        cp = None
        if address:
            cp = conn.execute(
                "insert into crm.contact_point (kind, value_norm, value_display, person_id, organization_id, usage, "
                "confirmation) values ('email', %s, %s, %s, %s, %s, 'machine_proposed') returning id::text",
                (address, address, pid, org, "work" if org else "personal")).fetchone()[0]
        if org:
            conn.execute("insert into crm.affiliation (person_id, organization_id, valid_from, confirmation) "
                         "values (%s, %s, '2026-01-01', 'machine_proposed')", (pid, org))
    return pid, cp


def _actions(repo, reading):
    return [a for a in repo.plan() if a.assertion_id == reading["assertion"]]


def _apply_all(repo, w, reading):
    op = _operator(w["admin"])
    out = []
    for action in _actions(repo, reading):
        result = repo.apply_planned(op, action, automatic=True)
        assert "code" not in result, result
        out.append(result)
    return out


def _new_person_reading(db, w, first="Ana"):
    name = f"{first} {w['sur']}"
    address = f"{first.lower()}.{w['tag']}@{w['domain']}"
    return name, address, _reading(db, address, sig_name=name, institution=w["org_name"], title="Jefa de laboratorio")


# ─────────────────────────────────────────────────────────────── apply ──

def test_p3_creates_person_contact_point_affiliation_machine_proposed(db, world) -> None:
    name, address, reading = _new_person_reading(db, world)
    repo = _repo(db)
    assert [a.rule_id for a in _actions(repo, reading)] == ["P3"]
    (result,) = _apply_all(repo, world, reading)
    created = result["created"]
    person = _one(db, "select display_name, confirmation, confirmed_by_operator_id, title, origin_source_record_id::text "
                      "from crm.person where id = %s", created["person_id"])
    assert person == (name, "machine_proposed", None, "Jefa de laboratorio", reading["record"])
    cp = _one(db, "select value_norm, person_id::text, organization_id::text, usage, confirmation "
                  "from crm.contact_point where id = %s", created["contact_point_id"])
    assert cp == (address, created["person_id"], world["org"], "work", "machine_proposed")
    aff = _one(db, "select person_id::text, organization_id::text, valid_to, confirmation "
                   "from crm.affiliation where id = %s", created["affiliation_id"])
    assert aff == (created["person_id"], world["org"], None, "machine_proposed")
    assert result["trigger"] == "automatic" and result["rule_id"] == "P3"
    # The reading is handled: the next plan does not see it again.
    assert _actions(repo, reading) == []


def test_apply_is_idempotent(db, world) -> None:
    name, _address, reading = _new_person_reading(db, world, first="Berta")
    repo = _repo(db)
    (action,) = _actions(repo, reading)
    op = _operator(world["admin"])
    first = repo.apply_planned(op, action, automatic=True)
    second = repo.apply_planned(op, action, automatic=True)
    assert "code" not in first and second["replayed"] is True
    assert second["created"] == first["created"]
    assert _one(db, "select count(*) from crm.person where display_name = %s", name)[0] == 1
    assert _one(db, "select count(*) from platform.command_receipt where idempotency_key = %s",
                f"people-rule:{reading['assertion']}:P3")[0] == 1


def test_p1_sets_resolved_contact_point_id(db, world) -> None:
    name = f"Carla {world['sur']}"
    address = f"carla.{world['tag']}@{world['domain']}"
    _pid, cp = _person(db, name, address=address, org=world["org"])
    reading = _reading(db, address, header=name)
    repo = _repo(db)
    actions = _actions(repo, reading)
    assert [a.rule_id for a in actions] == ["P1"]
    (result,) = _apply_all(repo, world, reading)
    assert result["resolved_participant_ids"] == [reading["participant"]]
    assert _one(db, "select resolved_contact_point_id::text from comms.message_participant where id = %s",
                reading["participant"])[0] == cp


def test_p1b_adds_a_name_and_undo_soft_removes_it(db, world) -> None:
    address = f"dsoto.{world['tag']}@{world['domain']}"
    pid, _cp = _person(db, f"Diego {world['sur']}", address=address, org=world["org"])
    reading = _reading(db, address, header=f"Juan Diego {world['sur']} Rivas")
    repo = _repo(db)
    results = _apply_all(repo, world, reading)
    assert [r["rule_id"] for r in results] == ["P1", "P1b"]
    name_id = results[1]["created"]["person_name_id"]
    row = _one(db, "select person_id::text, source, confirmation, removed_at from crm.person_name where id = %s", name_id)
    assert row == (pid, "display_name", "machine_proposed", None)
    repo.undo(_operator(world["admin"]), results[1]["command_receipt_id"], "no era su nombre")
    assert _one(db, "select removed_at is not null from crm.person_name where id = %s", name_id)[0] is True


# ─────────────────────────────────────────────────────────────── undo ──

def test_undo_p3_leaves_no_live_rows(db, world) -> None:
    _name, _address, reading = _new_person_reading(db, world, first="Elena")
    repo = _repo(db)
    (result,) = _apply_all(repo, world, reading)
    created = result["created"]
    undone = repo.undo(_operator(world["admin"]), result["command_receipt_id"], "Quitado de Hoy")
    assert undone["undoes_receipt_id"] == result["command_receipt_id"]
    assert _one(db, "select status, archive_reason from crm.person where id = %s", created["person_id"]) == (
        "archived", "Quitado de Hoy")
    assert _one(db, "select status from crm.contact_point where id = %s", created["contact_point_id"])[0] == "inactive"
    assert _one(db, "select valid_to is not null from crm.affiliation where id = %s", created["affiliation_id"])[0]
    assert _one(db, "select resolved_contact_point_id from comms.message_participant where id = %s",
                reading["participant"])[0] is None
    # Undone still counts as handled: the reading never re-applies by itself.
    assert _actions(repo, reading) == []
    assert all(r["receipt_id"] != result["command_receipt_id"] for r in repo.recent_automatic())


def _merge_world(db, w):
    """Two persons of one name and institution — the older one wins — and, on the loser, a second
    contact point, an affiliation the winner shares, one it does not, a note, a case participant and
    an unfrozen campaign recipient. Then the email that merges them."""
    m = {"name": f"Marta {w['sur']}", "address": f"marta.{w['tag']}@{w['domain']}"}
    m["winner"], m["winner_cp"] = _person(db, m["name"], address=f"mf.{w['tag']}@{w['domain']}", org=w["org"],
                                          created_at="2026-01-01")
    m["loser"], m["loser_cp"] = _person(db, m["name"], address=m["address"], org=w["org"], created_at="2026-06-01")
    phone = "+569" + str(int(w["tag"], 16) % 10**8).zfill(8)
    with _owner(db) as conn:
        m["loser_cp2"] = conn.execute(
            "insert into crm.contact_point (kind, value_norm, value_display, person_id, usage, confirmation) "
            "values ('phone', %s, %s, %s, 'personal', 'machine_proposed') returning id::text",
            (phone, phone, m["loser"])).fetchone()[0]
        m["other_aff"] = conn.execute(
            "insert into crm.affiliation (person_id, organization_id, valid_from, confirmation) "
            "values (%s, %s, '2026-01-01', 'machine_proposed') returning id::text",
            (m["loser"], w["other_org"])).fetchone()[0]
        m["dup_aff"] = conn.execute("select id::text from crm.affiliation where person_id = %s and organization_id = %s",
                                    (m["loser"], w["org"])).fetchone()[0]
        m["note"] = conn.execute(
            "insert into crm.note (subject_kind, subject_id, body, author_operator_id) "
            "values ('person', %s, 'nota ficticia', %s) returning id::text", (m["loser"], w["admin"])).fetchone()[0]
        case = conn.execute("insert into crm.opportunity (title, stage, owner_operator_id) values (%s, 'lead', %s) "
                            "returning id::text", (f"Caso ficticio {w['tag']}", w["admin"])).fetchone()[0]
        m["participant"] = conn.execute(
            "insert into crm.opportunity_participant (opportunity_id, person_id, role, is_primary, valid_from, "
            "confirmation) values (%s, %s, 'quote_recipient', true, current_date, 'machine_proposed') "
            "returning id::text", (case, m["loser"])).fetchone()[0]
        mailbox = conn.execute("insert into comms.mailbox (address_norm) values (%s) returning id",
                               (f"camp-{w['tag']}@example.test",)).fetchone()[0]
        campaign = conn.execute(
            "insert into outbound.campaign (name, max_sends, recontact_interval_days, status, mailbox_id, "
            "created_by_operator_id) values (%s, 10, 30, 'archived', %s, %s) returning id::text",
            (f"Campaña ficticia {w['tag']}", mailbox, w["admin"])).fetchone()[0]
        m["recipient"] = conn.execute(
            "insert into outbound.campaign_recipient (campaign_id, address_norm, person_id, state) "
            "values (%s, %s, %s, 'sent') returning id::text", (campaign, m["address"], m["loser"])).fetchone()[0]
    m["reading"] = _reading(db, m["address"], sig_name=m["name"], institution=w["org_name"])
    return m


def _person_of(db, table, row_id):
    return _one(db, f"select person_id::text from {table} where id = %s", row_id)[0]


def test_auto_merge_records_ids_and_undo_restores_exactly(db, world) -> None:
    m = _merge_world(db, world)
    winner, winner_cp, loser, loser_cp, loser_cp2 = m["winner"], m["winner_cp"], m["loser"], m["loser_cp"], m["loser_cp2"]
    other_aff, dup_aff, note, reading = m["other_aff"], m["dup_aff"], m["note"], m["reading"]
    repo = _repo(db)
    results = _apply_all(repo, world, reading)
    assert [r["rule_id"] for r in results] == ["P1", "P5"]
    merge = results[1]["merge"]
    assert (merge["winner_person_id"], merge["loser_person_id"]) == (winner, loser)
    line = [r["what"] for r in repo.recent_automatic() if r["receipt_id"] == results[1]["command_receipt_id"]]
    assert len(line) == 1 and line[0].endswith(": fusionados") and " ↔ " in line[0]
    assert sorted(merge["moved"]["contact_point_ids"]) == sorted([loser_cp, loser_cp2])
    assert merge["moved"]["affiliation_ids"] == [other_aff]
    assert merge["moved"]["closed_affiliation_ids"] == [dup_aff]
    assert merge["moved"]["note_ids"] == [note]
    assert merge["moved"]["opportunity_participant_ids"] == [m["participant"]]
    assert merge["moved"]["campaign_recipient_ids"] == [m["recipient"]]
    assert _person_of(db, "crm.opportunity_participant", m["participant"]) == winner
    assert _person_of(db, "outbound.campaign_recipient", m["recipient"]) == winner
    assert _one(db, "select merged_into_person_id::text, status from crm.person where id = %s", loser) == (winner, "archived")
    assert {r[0] for r in _all(db, "select id::text from crm.contact_point where person_id = %s", winner)} == {
        winner_cp, loser_cp, loser_cp2}
    assert _one(db, "select subject_id::text from crm.note where id = %s", note)[0] == winner
    # The winner holds the other institution now; the loser holds no open affiliation.
    assert {r[0] for r in _all(db, "select organization_id::text from crm.affiliation "
                                   "where person_id = %s and valid_to is null", winner)} == {world["org"], world["other_org"]}
    assert _all(db, "select id from crm.affiliation where person_id = %s and valid_to is null", loser) == []

    # A person adds an address to the winner after the merge: it stays with the winner.
    v = _one(db, "select version from crm.person where id = %s", winner)[0]
    added = _authoring(db).execute(
        command_name="add-contact-point", operator=_operator(world["admin"]),
        fields={"person_id": winner, "expected_version": v, "kind": "email",
                "value": f"nueva.{world['tag']}@{world['domain']}", "usage": "personal", "note": "nueva"},
        idempotency_key=uuid.uuid4().hex, digest="a" * 64)["contact_point_id"]

    repo.undo(_operator(world["admin"]), results[1]["command_receipt_id"], "no eran la misma persona")
    assert _one(db, "select merged_into_person_id, status, archived_at from crm.person where id = %s", loser) == (
        None, "active", None)
    assert {r[0] for r in _all(db, "select id::text from crm.contact_point where person_id = %s", loser)} == {
        loser_cp, loser_cp2}
    assert {r[0] for r in _all(db, "select id::text from crm.contact_point where person_id = %s", winner)} == {
        winner_cp, added}
    assert _one(db, "select subject_id::text from crm.note where id = %s", note)[0] == loser
    assert _person_of(db, "crm.opportunity_participant", m["participant"]) == loser
    assert _person_of(db, "outbound.campaign_recipient", m["recipient"]) == loser
    assert {r[0] for r in _all(db, "select id::text from crm.affiliation where person_id = %s and valid_to is null",
                               loser)} == {other_aff, dup_aff}
    assert {r[0] for r in _all(db, "select organization_id::text from crm.affiliation "
                                   "where person_id = %s and valid_to is null", winner)} == {world["org"]}


def test_undo_refused_when_person_edited_since(db, world) -> None:
    from origenlab_api.v2.commands import CommandRefused

    _name, _address, reading = _new_person_reading(db, world, first="Fabiola")
    repo = _repo(db)
    (result,) = _apply_all(repo, world, reading)
    pid = result["created"]["person_id"]
    v = _one(db, "select version from crm.person where id = %s", pid)[0]
    _authoring(db).execute(command_name="update-person", operator=_operator(world["admin"]),
                           fields={"person_id": pid, "expected_version": v, "title": "Directora", "note": "x"},
                           idempotency_key=uuid.uuid4().hex, digest="b" * 64)
    with pytest.raises(CommandRefused) as refused:
        repo.undo(_operator(world["admin"]), result["command_receipt_id"], "Quitado de Hoy")
    assert refused.value.code == "changed_since"
    assert refused.value.message == "Alguien cambió este contacto después; deshazlo desde la ficha"
    assert _one(db, "select status from crm.person where id = %s", pid)[0] == "active"


def test_events_carry_worker_attribution(db, world) -> None:
    _name, _address, reading = _new_person_reading(db, world, first="Gloria")
    repo = _repo(db)
    (result,) = _apply_all(repo, world, reading)
    rows = _all(db, "select actor_kind, actor_operator_id, payload -> 'attribution' from crm.domain_event "
                    "where command_receipt_id = %s", result["command_receipt_id"])
    assert len(rows) >= 3
    for actor_kind, actor_operator_id, attribution in rows:
        assert actor_kind == "worker" and actor_operator_id is None
        assert attribution == {"source": "people_rules", "rule_id": "P3", "trigger": "automatic",
                               "assertion_id": reading["assertion"]}
    recent = [r for r in repo.recent_automatic() if r["receipt_id"] == result["command_receipt_id"]]
    assert recent and recent[0]["rule_id"] == "P3"
    assert recent[0]["what"] == (f"{recent[0]['person_name']}: persona nueva en "
                                 f"{world['org_name']} ({recent[0]['address']})")


def test_hoy_lines_and_quitar_dispatch_by_receipt(db, world) -> None:
    from origenlab_api.v2.commands import CommandRefused

    _name, _address, reading = _new_person_reading(db, world, first="Ines")
    repo = _repo(db)
    (result,) = _apply_all(repo, world, reading)
    receipt = result["command_receipt_id"]
    assert repo.has_receipt(receipt) and not repo.has_receipt(str(uuid.uuid4()))
    repo.remove_automatic(_operator(world["admin"]), receipt)
    assert all(r["receipt_id"] != receipt for r in repo.recent_automatic())
    with pytest.raises(CommandRefused) as refused:
        repo.remove_automatic(_operator(world["admin"]), str(uuid.uuid4()))
    assert refused.value.status_code == 404


def _line(repo, result):
    (row,) = [r for r in repo.recent_automatic() if r["receipt_id"] == result["command_receipt_id"]]
    return row["what"]


def test_hoy_lines_name_what_changed(db, world) -> None:
    address = f"dsoto.{world['tag']}@{world['domain']}"
    _person(db, f"Diego {world['sur']}", address=address, org=world["org"])
    repo = _repo(db)
    results = _apply_all(repo, world, _reading(db, address, header=f"Juan Diego {world['sur']} Rivas"))
    assert _line(repo, results[1]) == f"Diego {world['sur']}: también se llama Juan Diego {world['sur']} Rivas"
    # A receipt written before the texts were stored reads them from the rows it created.
    with _owner(db) as conn:
        conn.execute("update platform.command_receipt set response_body = response_body - 'name' where id = %s",
                     (results[1]["command_receipt_id"],))
    assert _line(repo, results[1]) == f"Diego {world['sur']}: también se llama Juan Diego {world['sur']} Rivas"

    name = f"Irene {world['sur']}"
    address = f"irene.{world['tag']}@{world['domain']}"
    _person(db, name, address=address)
    results = _apply_all(repo, world, _reading(db, address, sig_name=name, institution=world["org_name"]))
    assert _line(repo, results[1]) == f"{name}: trabaja en {world['org_name']}"
    with _owner(db) as conn:
        conn.execute("update platform.command_receipt set response_body = response_body - 'organization_name' "
                     "where id = %s", (results[1]["command_receipt_id"],))
    assert _line(repo, results[1]) == f"{name}: trabaja en {world['org_name']}"


def test_p2_attaches_a_work_address_and_undo_deactivates_it(db, world) -> None:
    name = f"Hugo {world['sur']}"
    pid, _cp = _person(db, name, org=world["org"])
    address = f"hugo.{world['tag']}@{world['domain']}"
    reading = _reading(db, address, sig_name=name, institution=world["org_name"])
    repo = _repo(db)
    (result,) = _apply_all(repo, world, reading)
    assert result["rule_id"] == "P2"
    # Like the authoring handlers, the new address moves the person's version (an open ficha goes stale).
    assert _one(db, "select version from crm.person where id = %s", pid)[0] == 2
    cp = result["created"]["contact_point_id"]
    assert _one(db, "select person_id::text, organization_id::text, usage, confirmation from crm.contact_point "
                    "where id = %s", cp) == (pid, world["org"], "work", "machine_proposed")
    assert result["resolved_participant_ids"] == [reading["participant"]]
    repo.undo(_operator(world["admin"]), result["command_receipt_id"], "no es su dirección")
    assert _one(db, "select status from crm.contact_point where id = %s", cp)[0] == "inactive"
    assert _one(db, "select status from crm.person where id = %s", pid)[0] == "active"


def test_p4_adds_an_affiliation_machine_proposed(db, world) -> None:
    name = f"Irene {world['sur']}"
    address = f"irene.{world['tag']}@{world['domain']}"
    pid, _cp = _person(db, name, address=address)
    reading = _reading(db, address, sig_name=name, institution=world["org_name"])
    repo = _repo(db)
    results = _apply_all(repo, world, reading)
    assert [r["rule_id"] for r in results] == ["P1", "P4"]
    aff = results[1]["created"]["affiliation_id"]
    assert _one(db, "select person_id::text, organization_id::text, confirmation, valid_to from crm.affiliation "
                    "where id = %s", aff) == (pid, world["org"], "machine_proposed", None)
    repo.undo(_operator(world["admin"]), results[1]["command_receipt_id"], "no es de esa institución")
    assert _one(db, "select valid_to is not null from crm.affiliation where id = %s", aff)[0] is True


def test_undo_merge_refused_when_a_moved_row_was_moved_again(db, world) -> None:
    from origenlab_api.v2.commands import CommandRefused

    m = _merge_world(db, world)
    repo = _repo(db)
    results = _apply_all(repo, world, m["reading"])
    third, _ = _person(db, f"Tercera {world['sur']}")
    with _owner(db) as conn:
        conn.execute("update crm.opportunity_participant set person_id = %s where id = %s", (third, m["participant"]))
    before = _all(db, "select id::text, person_id::text from crm.contact_point where person_id in (%s, %s) order by id",
                  m["winner"], m["loser"])
    with pytest.raises(CommandRefused) as refused:
        repo.undo(_operator(world["admin"]), results[1]["command_receipt_id"], "no eran la misma persona")
    assert refused.value.code == "changed_since" and refused.value.message == (
        "Alguien cambió este contacto después; deshazlo desde la ficha")
    # Nothing changed: the loser stays merged, every row where the merge left it.
    assert _one(db, "select merged_into_person_id::text from crm.person where id = %s", m["loser"])[0] == m["winner"]
    assert _all(db, "select id::text, person_id::text from crm.contact_point where person_id in (%s, %s) order by id",
                m["winner"], m["loser"]) == before
    assert _person_of(db, "outbound.campaign_recipient", m["recipient"]) == m["winner"]
    assert _one(db, "select subject_id::text from crm.note where id = %s", m["note"])[0] == m["winner"]


def test_undo_p3_refused_when_the_person_gained_rows_since(db, world) -> None:
    from origenlab_api.v2.commands import CommandRefused

    name, _address, reading = _new_person_reading(db, world, first="Julia")
    repo = _repo(db)
    (result,) = _apply_all(repo, world, reading)
    pid = result["created"]["person_id"]
    # Another name lands on the person without moving its version (as a row written outside a command).
    with _owner(db) as conn:
        conn.execute("insert into crm.person_name (person_id, name_display, name_norm, source, confirmation) "
                     "values (%s, %s, %s, 'operator', 'confirmed')", (pid, f"Julia M {world['sur']}",
                                                                        f"julia m {world['sur'].lower()}"))
    with pytest.raises(CommandRefused) as refused:
        repo.undo(_operator(world["admin"]), result["command_receipt_id"], "Quitado de Hoy")
    assert refused.value.code == "changed_since"
    assert _one(db, "select status from crm.person where id = %s", pid)[0] == "active"
    assert _one(db, "select status from crm.contact_point where id = %s", result["created"]["contact_point_id"])[0] == "active"
