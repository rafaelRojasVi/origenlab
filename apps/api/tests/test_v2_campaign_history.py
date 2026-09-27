"""Campaign history reads — the Marketing campaign detail's tabs and the card totals.

**In process, always run:** the recipient filters are validated, a viewer's search predicate never
touches the address, every total has one predicate, the outcome of a recipient, the explicit
address mask, and that the new routes are GET only and never call Gmail.

**Database-backed, opt-in:** one fictitious archived campaign with every recorded outcome — sent,
bounced, blocked, marked inactive, never sent, rejected only, rejected then accepted, replied, a
«BAJA» proven by lineage, a «BAJA» by address, a «BAJA» held for review — and a second archived
campaign with nothing replied. Every total equals the row count of its filtered list, across
pages; recipients are not attempts; a viewer can neither read an address nor probe for one; the
reply categories keep their lineage apart; a campaign with no stored reply says it was never
synchronized; the audit says the history is immutable and the database refuses to change it.
Runs only in a disposable `origenlab_test_<hex>` the harness creates and drops. Every value is
fictitious.
"""

from __future__ import annotations

import ast
import pathlib
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from origenlab_api.v2.campaign_history import (
    TOTALS,
    RecipientQuery,
    outcome,
    replies_state,
    shown_address,
    totals_sql,
)
from origenlab_api.v2.identity import IdentityRefused, OperatorIdentity
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "origenlab_api" / "v2"


# --------------------------------------------------------------------------- in process


def test_every_total_is_one_predicate_used_by_both_the_card_and_the_list() -> None:
    assert set(TOTALS) == {"audience", "included", "sent", "excluded", "blocked", "unsent", "rejected",
                           "bounced", "responses"}
    for scope in ("one", "all"):
        sql = totals_sql(scope)
        for key, predicate in TOTALS.items():
            assert f"count(*) filter (where {predicate})::int as {key}" in sql
    for key, predicate in TOTALS.items():
        where, _ = RecipientQuery(total=key).where("admin")
        assert where == f"({predicate})"


@pytest.mark.parametrize("kw", [{"total": "opened"}, {"reason": "whatever"}, {"identity": "guessed"},
                                {"page": 0}, {"page_size": 0}, {"page_size": 101}])
def test_recipient_filters_are_validated(kw) -> None:
    with pytest.raises(ValueError):
        RecipientQuery(**kw)


def test_a_viewer_searches_names_only_so_a_count_cannot_reveal_an_address() -> None:
    q = RecipientQuery(q="ana_50%")
    viewer, params = q.where("viewer")
    assert "address_norm" not in viewer and "pe.display_name" in viewer and "org.name" in viewer
    assert params["q"] == "%ana\\_50\\%%"  # LIKE wildcards in the query are literal
    for role in ("sales", "admin"):
        assert "b.address_norm ilike" in q.where(role)[0]
    assert q.as_dict("viewer")["search_scope"] == "names_only"
    assert RecipientQuery(q="   ").q is None


def test_the_address_is_masked_in_the_read_itself() -> None:
    assert shown_address("maría.peña@lab.example", "viewer") == "***@lab.example"
    assert shown_address("maría.peña@lab.example", None) == "***@lab.example"
    assert shown_address("maría.peña@lab.example", "sales") == "maría.peña@lab.example"
    assert shown_address(None, "viewer") is None


def test_one_outcome_per_recipient() -> None:
    base = {"state": "sent", "bounced_attempts": 0, "accepted": 1, "rejected": 0}
    assert outcome(base) == "sent"
    assert outcome({**base, "state": "excluded"}) == "excluded"
    assert outcome({**base, "state": "bounced"}) == "bounced"
    assert outcome({**base, "accepted": 0, "rejected": 1, "state": "snapshotted"}) == "rejected"
    assert outcome({**base, "accepted": 0, "state": "snapshotted"}) == "unsent"
    assert outcome({**base, "rejected": 1}) == "sent"  # retried and accepted


def test_no_stored_reply_is_never_shown_as_zero_replies() -> None:
    assert replies_state(0, 0) == {"state": "not_synced", "label": "Respuestas no sincronizadas desde Gmail",
                                   "count": None}
    assert replies_state(2, 0)["count"] == 2 and replies_state(0, 1)["state"] == "partial"


def test_the_history_module_reads_only_and_calls_nothing_outside_postgres() -> None:
    tree = ast.parse((SRC / "campaign_history.py").read_text(encoding="utf-8"))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not imported & {"httpx", "requests", "urllib.request", "googleapiclient", "sqlite3", "smtplib"}
    text = (SRC / "campaign_history.py").read_text(encoding="utf-8").lower()
    for verb in ("insert into", "update outbound", "delete from"):
        assert verb not in text


def test_the_history_routes_are_get_only() -> None:
    from origenlab_api.v2.crm_workspace_routes import workspace_router

    history = [r for r in workspace_router.routes if "/history/" in r.path]
    assert {r.path.rsplit("/", 1)[1] for r in history} == {"recipients", "replies", "audit"}
    assert all(r.methods == {"GET"} for r in history)


# --------------------------------------------------------------------------- database (opt-in)


@pytest.fixture(scope="module")
def disposable_database():
    yield from build_disposable_database()


def _owner(cur, sql, params=()):
    cur.execute(sql, params)
    return cur.fetchone()[0] if cur.description else None


@pytest.fixture(scope="module")
def world(disposable_database) -> dict[str, Any]:
    """Archived campaign A with every outcome, archived campaign B with nothing replied."""
    import psycopg

    tag = uuid.uuid4().hex[:8]
    w: dict[str, Any] = {"tag": tag}
    addr = {k: f"{k}-{tag}@lab.test" for k in
            ("sent", "bounced", "blocked", "inactive", "unsent", "rejonly", "retry", "replied", "lineage", "cc",
             "held")}
    addr["ana"] = f"peña.ana-{tag}@uni.test"  # a person linked in the CRM, with a non-ASCII local part
    w["addr"] = addr
    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        for key, role in (("admin", "admin"), ("viewer", "viewer")):
            w[key] = _owner(cur, "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                                 "values (gen_random_uuid(), %s, %s, %s, 'active') returning id::text",
                            (f"hist-{key}-{tag}@example.test", f"Hist {key}", role))
        mailbox = _owner(cur, "insert into comms.mailbox (address_norm) values (%s) returning id::text",
                         (f"ventas-{tag}@example.invalid",))
        org = _owner(cur, "insert into crm.organization (kind, name, confirmation) values ('institution', %s, 'confirmed') "
                          "returning id::text", (f"Universidad Ficticia {tag}",))
        person = _owner(cur, "insert into crm.person (display_name, confirmation) values (%s, 'machine_proposed') "
                             "returning id::text", (f"Ana Ficticia {tag}",))
        w["org_name"], w["person_name"] = f"Universidad Ficticia {tag}", f"Ana Ficticia {tag}"
        w["A"] = _owner(cur, "insert into outbound.campaign (name, status, mailbox_id, max_sends) "
                             "values (%s, 'archived', %s, 2000) returning id::text", (f"Histórica A {tag}", mailbox))
        w["B"] = _owner(cur, "insert into outbound.campaign (name, status, mailbox_id, max_sends) "
                             "values (%s, 'archived', %s, 1000) returning id::text", (f"Histórica B {tag}", mailbox))
        rid: dict[str, str] = {}

        def recipient(key, state, reasons=(), campaign="A", **links):
            cols = ["campaign_id", "address_norm", "state", "exclusion_reasons", *links]
            rid[key] = _owner(cur, f"insert into outbound.campaign_recipient ({', '.join(cols)}) "
                                   f"values ({', '.join(['%s'] * len(cols))}) returning id::text",
                              (w[campaign], addr[key], state, list(reasons), *links.values()))

        def attempt(key, result, when="2026-09-02T13:08:06+00:00", campaign="A"):
            accepted = result == "accepted"
            return _owner(cur, "insert into outbound.send_attempt (purpose, campaign_id, campaign_recipient_id, mailbox_id, "
                               "address_norm, submission_state, delivery_state, error_class, accepted_at) "
                               "values ('marketing', %s, %s, %s, %s, %s, %s, %s, %s) returning id::text",
                          (w[campaign], rid[key], mailbox, addr[key], "accepted" if accepted else "rejected",
                           "pending" if accepted else "n/a", None if accepted else "permanent",
                           when if accepted else None))

        recipient("ana", "sent", person_id=person, organization_id=org)
        attempt("ana", "accepted")
        recipient("sent", "sent")
        attempt("sent", "accepted", "2026-09-03T15:00:00+00:00")
        recipient("bounced", "bounced")
        attempt("bounced", "accepted")
        recipient("blocked", "excluded", ["block"])
        recipient("inactive", "excluded", ["manual_inactive"])
        recipient("unsent", "snapshotted")
        recipient("rejonly", "snapshotted")
        attempt("rejonly", "rejected")
        recipient("retry", "sent")
        attempt("retry", "rejected")
        attempt("retry", "accepted", "2026-09-03T16:00:00+00:00")
        recipient("replied", "sent")
        replied_attempt = attempt("replied", "accepted")
        recipient("lineage", "sent")
        lineage_attempt = attempt("lineage", "accepted")
        # B: one recipient, sent, nothing replied.
        recipient("cc", "sent", campaign="B")  # its address is only a cc of A's lineage message below
        attempt("cc", "accepted", campaign="B")
        # A stored reply (lineage: the recipient), and the outbound message the «BAJA» answers.
        inbound = _owner(cur, "insert into comms.message (mailbox_id, provider_message_id, direction, internal_date) "
                              "values (%s, %s, 'inbound', '2026-09-04T10:00:00+00:00') returning id::text",
                         (mailbox, f"gm-reply-{tag}"))
        cur.execute("insert into outbound.campaign_reply (campaign_id, campaign_recipient_id, message_id, send_attempt_id, "
                    "received_at, proposed_class, proposed_by) values (%s, %s, %s, %s, '2026-09-04T10:00:00+00:00', "
                    "'human_reply', 'ingest_classifier')", (w["A"], rid["replied"], inbound, replied_attempt))
        w["outbound_mid"] = f"<campana-a-{tag}@mail.origenlab.test>"
        sent = _owner(cur, "insert into comms.message (mailbox_id, provider_message_id, rfc822_message_id_norm, direction, "
                           "internal_date, send_attempt_id) values (%s, %s, %s, 'outbound', '2026-09-02T13:08:06+00:00', %s) "
                           "returning id::text",
                      (mailbox, f"gm-out-{tag}", f"campana-a-{tag}@mail.origenlab.test", lineage_attempt))
        for role, key in (("to", "lineage"), ("cc", "held")):
            cur.execute("insert into comms.message_participant (message_id, role, address_norm) values (%s, %s, %s)",
                        (sent, role, addr[key] if key == "lineage" else f"copia-{tag}@lab.test"))
    w["rid"] = rid
    w["admin_identity"] = OperatorIdentity(operator_id=w["admin"], email_norm=f"hist-admin-{tag}@example.test",
                                           display_name="Hist admin", role="admin", status="active")
    return w


def _apply_baja(dsn, w, sender, **kw):
    from test_v2_unsubscribe import _apply, _record

    return _apply(dsn, w["admin_identity"], [_record(sender=sender, mid=f"<{uuid.uuid4().hex}@lab.test>", **kw)])


@pytest.fixture(scope="module")
def bajas(disposable_database, world) -> dict[str, Any]:
    """Three «BAJA», applied through the real W10 command: a cc of A's message (lineage), a
    recipient of A (a known address: by address only), a stranger (held for review) who is then
    — as the importer could — added to A's audience."""
    import psycopg

    tag = world["tag"]
    out = {
        "lineage": _apply_baja(disposable_database, world, f"copia-{tag}@lab.test", in_reply_to=world["outbound_mid"]),
        "known": _apply_baja(disposable_database, world, world["addr"]["sent"]),
        "held": _apply_baja(disposable_database, world, world["addr"]["held"], in_reply_to="<nunca@x.test>"),
    }
    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute("insert into outbound.campaign_recipient (campaign_id, address_norm, state) values (%s, %s, 'snapshotted') "
                    "returning id::text", (world["A"], world["addr"]["held"]))
        world["rid"]["held"] = cur.fetchone()[0]
    return out


class _Port:
    def __init__(self, role: str | None, operator_id: str):
        self._role, self._id = role, operator_id

    def resolve(self, headers: dict[str, str]) -> OperatorIdentity:
        if self._role is None:
            raise IdentityRefused("no identity")
        return OperatorIdentity(operator_id=self._id, email_norm="operator@example.invalid",
                                display_name="Operator", role=self._role, status="active")


def _client(dsn, w, role) -> TestClient:
    import psycopg

    from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository
    from origenlab_api.v2.crm_workspace_routes import workspace_router

    app = FastAPI()
    app.state.v2_identity = _Port(role, w["admin"])
    app.state.crm_workspace = CrmWorkspaceRepository(psycopg.connect, runtime_dsn(dsn))
    app.include_router(workspace_router)
    return TestClient(app)


def _base(cid: str) -> str:
    return f"/v2/workspace/marketing/campaigns/{cid}/history"


def _all_rows(client, cid, **params) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows, page, body = [], 1, {}
    while True:
        body = client.get(f"{_base(cid)}/recipients", params={**params, "page": page, "page_size": 3}).json()
        rows.extend(body["rows"])
        if page >= body["pages"]:
            return rows, body
        page += 1


# Ten recipients seeded, plus the stranger the `bajas` fixture adds after holding their «BAJA».
EXPECTED_A = {"audience": 11, "included": 9, "sent": 6, "excluded": 2, "blocked": 1, "unsent": 3,
              "rejected": 2, "bounced": 1, "responses": 1}


@needs_db
def test_every_total_equals_its_filtered_rows_across_pages(disposable_database, world, bajas) -> None:
    client = _client(disposable_database, world, "admin")
    card = next(c for c in client.get("/v2/workspace/marketing").json()["campaigns"] if c["campaign_id"] == world["A"])
    assert card["totals"] == EXPECTED_A
    for key, value in EXPECTED_A.items():
        rows, body = _all_rows(client, world["A"], total=key)
        assert body["total_rows"] == value == len(rows) == len({r["recipient_id"] for r in rows}), key
        assert body["totals"] == EXPECTED_A
    sent_rows, _ = _all_rows(client, world["A"], total="sent")
    assert {r["recipient_id"] for r in sent_rows} == {world["rid"][k] for k in
                                                      ("ana", "sent", "bounced", "retry", "replied", "lineage")}


@needs_db
def test_recipients_are_not_attempts_and_accepted_is_not_delivered(disposable_database, world, bajas) -> None:
    client = _client(disposable_database, world, "admin")
    card = next(c for c in client.get("/v2/workspace/marketing").json()["campaigns"] if c["campaign_id"] == world["A"])
    attempts = card["attempt_totals"]
    assert (attempts["attempts"], attempts["accepted"], attempts["rejected"]) == (8, 6, 2)
    assert attempts["delivery_confirmed"] == 0 and attempts["delivery_pending"] == 6
    assert card["totals"]["sent"] == 6 and card["totals"]["rejected"] == 2 and card["totals"]["audience"] == 11
    retry = next(r for r in _all_rows(client, world["A"], total="rejected")[0] if r["recipient_id"] == world["rid"]["retry"])
    assert (retry["attempts"], retry["accepted_attempts"], retry["rejected_attempts"], retry["outcome"]) == (2, 1, 1, "sent")
    assert retry["delivery_confirmed"] is False and retry["gmail_url"] is None


@needs_db
def test_filters_identity_and_reason(disposable_database, world, bajas) -> None:
    client = _client(disposable_database, world, "admin")
    person, body = _all_rows(client, world["A"], identity="crm_person")
    assert body["total_rows"] == 1 and person[0]["person_name"] == world["person_name"]
    assert person[0]["organization_name"] == world["org_name"] and person[0]["identity"] == "crm_person"
    historical, _ = _all_rows(client, world["A"], identity="historical_address")
    assert len(historical) == 10 and all(r["organization_name"] is None for r in historical)
    inactive, _ = _all_rows(client, world["A"], total="excluded", reason="manual_inactive")
    assert [r["recipient_id"] for r in inactive] == [world["rid"]["inactive"]]
    assert inactive[0]["exclusion_reasons"][0]["label"].startswith("Marcada inactiva")
    assert client.get(f"{_base(world['A'])}/recipients", params={"total": "opened"}).status_code == 422
    assert client.get(f"{_base(world['A'])}/recipients", params={"page_size": 500}).status_code == 422
    assert client.get(f"{_base(str(uuid.uuid4()))}/recipients").status_code == 404


@needs_db
def test_a_viewer_can_neither_read_nor_probe_an_address(disposable_database, world, bajas) -> None:
    viewer = _client(disposable_database, world, "viewer")
    admin = _client(disposable_database, world, "admin")
    rows, _ = _all_rows(viewer, world["A"])
    assert rows and all(r["address"].startswith("***@") for r in rows)
    everything = viewer.get(f"{_base(world['A'])}/recipients", params={"page_size": 100}).text
    assert "peña.ana" not in everything and all(v not in everything for v in world["addr"].values())
    for local in (f"bounced-{world['tag']}", world["addr"]["bounced"], "peña"):
        assert admin.get(f"{_base(world['A'])}/recipients", params={"q": local}).json()["total_rows"] >= 1
        probe = viewer.get(f"{_base(world['A'])}/recipients", params={"q": local}).json()
        assert probe["total_rows"] == 0 and probe["filters"]["search_scope"] == "names_only"
    by_name = viewer.get(f"{_base(world['A'])}/recipients", params={"q": "Ana Ficticia"}).json()
    assert by_name["total_rows"] == 1 and by_name["rows"][0]["address"] == f"***@uni.test"
    replies = viewer.get(f"{_base(world['A'])}/replies").text
    assert f"copia-{world['tag']}" not in replies and f"replied-{world['tag']}" not in replies


@needs_db
def test_replies_keep_their_lineage_apart(disposable_database, world, bajas) -> None:
    assert bajas["lineage"]["rows"][0]["basis"] == "outbound_lineage"
    assert bajas["known"]["rows"][0]["basis"] == "known_address"  # a recipient: no lineage recorded
    client = _client(disposable_database, world, "admin")
    body = client.get(f"{_base(world['A'])}/replies").json()
    kinds = {(i["kind"], i["association"]) for i in body["items"]}
    assert kinds == {("reply", "recipient"), ("baja", "send_lineage"), ("baja_pending_review", "address_match")}
    assert body["counts"] == {"reply": 1, "baja": 1, "baja_pending_review": 1}
    reply = next(i for i in body["items"] if i["kind"] == "reply")
    assert reply["recipient_id"] == world["rid"]["replied"] and reply["excerpt"] is None
    assert reply["gmail_url"].endswith(f"gm-reply-{world['tag']}")  # the stored provider id, never a Gmail call
    lineage = next(i for i in body["items"] if i["association"] == "send_lineage")
    assert lineage["address"] == f"copia-{world['tag']}@lab.test" and lineage["recipient_id"] == world["rid"]["lineage"]
    assert body["baja_by_address"]["recipients"] == 1  # the known recipient: counted, not listed
    assert body["unassociated"]["baja_without_campaign_lineage"] >= 2
    assert body["sync"]["state"] == "partial" and body["gmail_called"] is False
    rows, _ = _all_rows(client, world["A"])
    flags = {r["recipient_id"]: r["baja"] for r in rows}
    assert flags[world["rid"]["sent"]] == "registered" and flags[world["rid"]["held"]] == "pending_review"
    assert flags[world["rid"]["unsent"]] is None


@needs_db
def test_a_campaign_with_no_stored_reply_says_it_was_never_synchronized(disposable_database, world, bajas) -> None:
    client = _client(disposable_database, world, "admin")
    body = client.get(f"{_base(world['B'])}/replies").json()
    assert body["items"] == [] and body["sync"] == {"state": "not_synced",
                                                    "label": "Respuestas no sincronizadas desde Gmail", "count": None}
    card = next(c for c in client.get("/v2/workspace/marketing").json()["campaigns"] if c["campaign_id"] == world["B"])
    assert card["replies"]["count"] is None and card["totals"]["responses"] == 0


@needs_db
def test_an_archived_campaign_is_immutable_and_says_so(disposable_database, world, bajas) -> None:
    import psycopg

    client = _client(disposable_database, world, "admin")
    audit = client.get(f"{_base(world['A'])}/audit").json()
    assert audit["immutable"] is True and audit["immutable_enforced_by_database"] is True
    assert audit["actions"] == [] and audit["events"] == [] and audit["origin"]["kind"] == "native_v2"
    archive = client.get(f"/v2/workspace/marketing/campaigns/{world['A']}/archive").json()
    assert archive["html_state"] == "not_archived" and archive["subject_state"] == "not_set"
    assert archive["immutable"] is True and archive["totals"] == EXPECTED_A
    with psycopg.connect(runtime_dsn(disposable_database), autocommit=True) as conn:
        for sql in ("update outbound.campaign set status = 'draft' where id = %s",
                    "update outbound.campaign set name = 'x' where id = %s",
                    "update outbound.campaign_recipient set state = 'bounced' where campaign_id = %s"):
            with pytest.raises(psycopg.errors.RaiseException):
                conn.execute(sql, (world["A"],))
    after = client.get(f"{_base(world['A'])}/recipients", params={"page_size": 100}).json()
    assert after["totals"] == EXPECTED_A
