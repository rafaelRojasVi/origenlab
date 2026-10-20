"""«Quitar anexo»: `remove-tender-annex` against a disposable database, as `origenlab_api`.

A wrong anexo is removed once, with its reason, as a case document is (the row stays, `removed_at`
set, an `opportunity.document_removed` event); a removed anexo is no longer on the page nor taken by
a new preparation. Every value is invented (public repository).
"""

from __future__ import annotations

import uuid

import pytest
from v2_command_harness import build_disposable_database, needs_db

from test_v2_tender_preparation_db import (  # noqa: F401 - fixtures and helpers shared with the main module
    _owner,
    add_annex,
    ask,
    client,
    code,
    headers,
    open_tender,
    operators,
    storage,
)

pytestmark = needs_db
URL = "/v2/commands/remove-tender-annex"


@pytest.fixture(scope="module")
def db():
    yield from build_disposable_database()


def remove(client, case: str, document: str, reason: str = "Anexo de otra licitación", *, role: str = "sales",
           key: str | None = None):
    return client.post(URL, json={"opportunity_id": case, "document_id": document, "reason": reason},
                       headers=headers(role, key))


def test_an_annex_is_removed_with_its_reason_and_leaves_the_page_and_the_next_read(db, operators, client) -> None:
    case = open_tender(db, operators)
    wrong, kept = add_annex(db, case, operators), add_annex(db, case, operators)
    r = remove(client, case, wrong)
    assert r.status_code == 200, r.text
    assert r.json()["document_id"] == wrong and r.json()["command"] == "remove-tender-annex"
    with _owner(db) as conn:
        row = conn.execute("select removed_at is not null, remove_reason, removed_by_operator_id::text "
                           "from crm.opportunity_document where id = %s", (wrong,)).fetchone()
        events = conn.execute("select payload from crm.domain_event where aggregate_id = %s "
                              "and event_type = 'opportunity.document_removed'", (case,)).fetchall()
    assert row == (True, "Anexo de otra licitación", operators["sales"].operator_id)
    assert [e[0] for e in events] == [{"document_id": wrong, "kind": "tender_annex",
                                       "reason": "Anexo de otra licitación"}]
    page = client.get(f"/v2/tenders/{case}", headers=headers()).json()
    assert [a["document_id"] for a in page["annexes"]] == [kept]
    queued = ask(client, case, "read")
    assert queued.status_code == 200, queued.text
    with _owner(db) as conn:
        taken = conn.execute("select annex_document_ids::text[] from evidence.bid_preparation where id = %s",
                             (queued.json()["preparation_id"],)).fetchone()[0]
    assert taken == [kept]


def test_the_same_key_replays_and_a_second_removal_is_refused(db, operators, client) -> None:
    case = open_tender(db, operators)
    doc = add_annex(db, case, operators)
    key = f"pytest-remove-{uuid.uuid4().hex}"
    first = remove(client, case, doc, key=key)
    again = remove(client, case, doc, key=key)
    assert first.status_code == again.status_code == 200
    assert again.json()["replayed"] is True
    assert {k: v for k, v in again.json().items() if k != "replayed"} == {
        k: v for k, v in first.json().items() if k != "replayed"}
    second = remove(client, case, doc)
    assert second.status_code == 409 and code(second) == "document_already_removed"
    with _owner(db) as conn:
        assert conn.execute("select count(*) from crm.domain_event where aggregate_id = %s "
                            "and event_type = 'opportunity.document_removed'", (case,)).fetchone()[0] == 1


def test_only_an_annex_of_this_tender_is_removed(db, operators, client) -> None:
    case, other = open_tender(db, operators), open_tender(db, operators)
    theirs = add_annex(db, other, operators)
    r = remove(client, case, theirs)
    assert r.status_code == 404 and code(r) == "annex_not_found"
    r = remove(client, case, str(uuid.uuid4()))
    assert r.status_code == 404 and code(r) == "annex_not_found"
    with _owner(db) as conn:
        sale = conn.execute("insert into crm.opportunity (title, stage, owner_operator_id) values "
                            "('Venta ficticia', 'lead', %s) returning id::text",
                            (operators["sales"].operator_id,)).fetchone()[0]
    r = remove(client, sale, theirs)
    assert r.status_code == 422 and code(r) == "not_a_tender_case"
    with _owner(db) as conn:
        assert conn.execute("select removed_at from crm.opportunity_document where id = %s",
                            (theirs,)).fetchone()[0] is None


def test_a_reason_is_required_and_a_viewer_may_not_remove(db, operators, client) -> None:
    case = open_tender(db, operators)
    doc = add_annex(db, case, operators)
    assert remove(client, case, doc, reason="no").status_code == 422
    assert remove(client, case, doc, role="viewer").status_code == 403
    assert remove(client, "no-es-un-id", doc).status_code == 422
    with _owner(db) as conn:
        assert conn.execute("select removed_at from crm.opportunity_document where id = %s",
                            (doc,)).fetchone()[0] is None
