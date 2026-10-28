"""The mail triage and its queue on a disposable `origenlab_test_<hex>` database, as the real
`origenlab_worker` login: the connection probes for both jobs, the pending query, the recorded
proposals and their idempotency, the catalog queries, and a job deferred into Procrastinate."""

from __future__ import annotations

import asyncio
import uuid
from email.message import EmailMessage

import psycopg
import pytest

from dbhelp import business_rows, owner_rows, seed_mailbox
from mailfixtures import INTERNAL_MS, MAILBOX, make_raw
from origenlab_worker.capture import build_capture
from origenlab_worker.database import (
    QUEUE_REQUIRED_POLICIES,
    QUEUE_REQUIRED_PRIVILEGES,
    TRIAGE_REQUIRED_POLICIES,
    TRIAGE_REQUIRED_PRIVILEGES,
    averify_worker_connection,
    local_test_target,
    open_verified_connection,
    open_worker_db,
)
from origenlab_worker.case_match import CASE_MATCH_VALUE_NORM
from origenlab_worker.triage import REQUESTER_VALUE_NORM, VALUE_NORM, ModelSettings, TriageDb, triage_one
from origenlab_worker.triage_model import DEFAULT_MODEL
from v2_command_harness import build_disposable_database, needs_worker_db, worker_dsn

pytestmark = needs_worker_db

# A message captured "now", so the sweep's look-back window includes it.
NOW_MS = INTERNAL_MS


@pytest.fixture(scope="module")
def dsn():
    yield from build_disposable_database()


@pytest.fixture(scope="module")
def target(dsn):
    return local_test_target(worker_dsn(dsn))


@pytest.fixture(scope="module")
def mailbox_id(dsn):
    return seed_mailbox(dsn)


class Store:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def get(self, key: str) -> bytes:
        return self.objects[key]


def ndr_raw(body: str, *, failed: str | None = None) -> bytes:
    msg = EmailMessage()
    msg["From"] = "Mail Delivery Subsystem <mailer-daemon@googlemail.invalid>"
    msg["To"] = MAILBOX
    msg["Subject"] = "Delivery Status Notification (Failure)"
    msg["Message-ID"] = f"<ndr-{uuid.uuid4().hex}@googlemail.invalid>"
    if failed:
        msg["X-Failed-Recipients"] = failed
    msg.set_content(body)
    return msg.as_bytes()


def capture(target, mailbox_id, store: Store, gmail_id: str, raw: bytes) -> str:
    c = build_capture(raw, gmail_message_id=gmail_id, gmail_thread_id=f"t-{gmail_id}", label_ids=("INBOX",),
                      label_names={}, internal_date_ms=NOW_MS, mailbox_address=MAILBOX)
    key = f"{MAILBOX}/2026/10/{gmail_id}.eml"
    store.objects[key] = raw
    with open_worker_db(target) as db:
        db.record(c, mailbox_id=mailbox_id, eml_path=f"mail/{key}", eml_sha256=c.payload["raw_sha256"],
                  size_bytes=len(raw))
    return gmail_id


def triage_conn(target):
    return open_verified_connection(target, application_name="test-triage", privileges=TRIAGE_REQUIRED_PRIVILEGES,
                                    policies=TRIAGE_REQUIRED_POLICIES)


def source_record(dsn, gmail_id: str) -> str:
    return owner_rows(dsn, "select id::text from evidence.source_record where dedupe_key = %s",
                      (f"gmail_message:{gmail_id}",))[0][0]


def test_the_worker_passes_the_triage_and_the_queue_probes(target) -> None:
    with triage_conn(target):
        pass

    async def probe() -> None:
        conn = await psycopg.AsyncConnection.connect(target.dsn, **target.connect_options)
        try:
            await averify_worker_connection(conn, target, privileges=QUEUE_REQUIRED_PRIVILEGES,
                                            policies=QUEUE_REQUIRED_POLICIES)
        finally:
            await conn.close()

    asyncio.run(probe())


def test_a_captured_request_is_pending_then_recorded_once(dsn, target, mailbox_id) -> None:
    store = Store()
    gid = capture(target, mailbox_id, store, f"tr-{uuid.uuid4().hex[:8]}",
                  make_raw(subject="Solicitud de cotización", message_id=f"<{uuid.uuid4().hex}@cliente.invalid>"))
    sid = source_record(dsn, gid)
    before = business_rows(dsn)
    with triage_conn(target) as conn:
        db = TriageDb(conn)
        assert sid in db.pending(since_days=36500, limit=500)
        result = triage_one(db, store, sid, ModelSettings(None, DEFAULT_MODEL))
        assert (result.outcome, result.triage_class, result.model) == ("recorded", "quote_request", "off")
        assert sid not in db.pending(since_days=36500, limit=500)
        assert triage_one(db, store, sid, ModelSettings(None, DEFAULT_MODEL)).outcome == "already_triaged"
    rows = owner_rows(
        dsn,
        "select kind, value_norm, resolution, value->>'class', value->>'model_state', value->>'status' "
        "from evidence.assertion where source_record_id = %s order by value_norm",
        (sid,),
    )
    assert rows == [
        ("message_triage", CASE_MATCH_VALUE_NORM, "unresolved", None, None, None),
        ("message_triage", "people:v1", "unresolved", None, None, None),
        ("message_triage", "quote_refs:v1", "unresolved", None, None, None),
        ("message_triage", REQUESTER_VALUE_NORM, "unresolved", None, None, "none"),
        ("message_triage", VALUE_NORM, "unresolved", "quote_request", "off", None),
    ]
    assert owner_rows(dsn, "select review_status from evidence.source_record where id = %s", (sid,)) == [("pending",)]
    assert business_rows(dsn) == before  # nothing in crm.* or outbound.*


def test_a_previous_quote_request_gets_requester_identity_backfilled_without_retriage(
    dsn, target, mailbox_id
) -> None:
    tag = uuid.uuid4().hex[:8]
    org_name = f"Pontificia Universidad Ejemplo de Valparaíso {tag}"
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("set role origenlab_owner")
        org_id = conn.execute(
            "insert into crm.organization (kind, name, confirmation) "
            "values ('institution', %s, 'confirmed') returning id::text",
            (org_name,),
        ).fetchone()[0]
    store = Store()
    raw = make_raw(
        subject="Solicitud de cotización de productos",
        message_id=f"<{uuid.uuid4().hex}@gmail.invalid>",
    ).replace(b"Hola,\nadjunto el documento.\n", f"Hola,\n{org_name}\n".encode())
    gid = capture(target, mailbox_id, store, f"tr-{tag}", raw)
    sid = source_record(dsn, gid)

    with triage_conn(target) as conn:
        db = TriageDb(conn)
        assert triage_one(db, store, sid, ModelSettings(None, DEFAULT_MODEL)).outcome == "recorded"
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("set role origenlab_owner")
        conn.execute(
            "delete from evidence.assertion where source_record_id = %s and kind = 'message_triage' "
            "and value_norm = %s",
            (sid, REQUESTER_VALUE_NORM),
        )
    with triage_conn(target) as conn:
        db = TriageDb(conn)
        assert sid in db.pending(since_days=36500, limit=500)
        result = triage_one(db, store, sid, ModelSettings(None, DEFAULT_MODEL))
        assert (result.outcome, result.triage_class, result.model) == ("recorded", "quote_request", "not_needed")
    assert owner_rows(
        dsn,
        "select value->>'status', value->>'organization_id', value->>'organization_name' "
        "from evidence.assertion where source_record_id = %s and kind = 'message_triage' "
        "and value_norm = %s",
        (sid, REQUESTER_VALUE_NORM),
    ) == [("matched", org_id, org_name)]


def test_a_clear_no_such_user_bounce_becomes_a_global_block(dsn, target, mailbox_id) -> None:
    address = f"dead-{uuid.uuid4().hex[:8]}@cliente.invalid"
    store = Store()
    raw = ndr_raw(
        f"Final-Recipient: rfc822; {address}\n"
        "Action: failed\nStatus: 5.1.1\n"
        "Diagnostic-Code: smtp; 550 5.1.1 User unknown; address does not exist.\n"
    )
    gid = capture(target, mailbox_id, store, f"tr-{uuid.uuid4().hex[:8]}", raw)
    sid = source_record(dsn, gid)

    with triage_conn(target) as conn:
        result = triage_one(TriageDb(conn), store, sid, ModelSettings(None, DEFAULT_MODEL))

    assert (result.outcome, result.triage_class) == ("recorded", "bounce")
    assert owner_rows(
        dsn,
        "select kind, value_norm, value->>'reason_code', value->>'batch', "
        "value->'auto_block_addresses'->>0 from evidence.assertion "
        "where source_record_id = %s and kind = 'delivery_failure'",
        (sid,),
    ) == [("delivery_failure", "delivery:v1", "bounce_no_such_user", "A", address)]
    assert owner_rows(
        dsn,
        "select kind, purpose, reason, source, origin_source_record_id::text "
        "from outbound.contact_control where scope = 'address' and value_norm = %s",
        (address,),
    ) == [("block", "all", "invalid_address", "ndr_handler", sid)]
    assert owner_rows(
        dsn,
        "select actor_kind, payload->>'reason_code' from crm.domain_event "
        "where aggregate_kind = 'contact_control' and aggregate_id = "
        "(select id from outbound.contact_control where scope='address' and value_norm=%s and kind='block' and purpose='all')",
        (address,),
    ) == [("worker", "bounce_no_such_user")]


def test_an_spf_policy_rejection_is_evidence_only(dsn, target, mailbox_id) -> None:
    address = f"spf-{uuid.uuid4().hex[:8]}@cliente.invalid"
    store = Store()
    raw = ndr_raw(
        f"Final-Recipient: rfc822; {address}\n"
        "Action: failed\nStatus: 5.7.1\n"
        "Diagnostic-Code: smtp; 554 5.7.1 Message rejected by policy; SPF no valido; access denied.\n"
    )
    gid = capture(target, mailbox_id, store, f"tr-{uuid.uuid4().hex[:8]}", raw)
    sid = source_record(dsn, gid)

    with triage_conn(target) as conn:
        result = triage_one(TriageDb(conn), store, sid, ModelSettings(None, DEFAULT_MODEL))

    assert result.triage_class == "bounce"
    assert owner_rows(
        dsn,
        "select value->>'reason_code', value->>'batch', jsonb_array_length(value->'auto_block_addresses') "
        "from evidence.assertion where source_record_id = %s and kind = 'delivery_failure'",
        (sid,),
    ) == [("bounce_access_denied", "D", 0)]
    assert owner_rows(
        dsn,
        "select count(*) from outbound.contact_control where scope='address' and value_norm=%s",
        (address,),
    ) == [(0,)]


def test_an_exact_model_number_becomes_a_product_mention(dsn, target, mailbox_id) -> None:
    maker = owner_rows(dsn, "insert into crm.organization (kind, name, confirmation) values ('manufacturer', %s, "
                            "'machine_proposed') returning id::text", (f"Fabricante Ficticio {uuid.uuid4().hex[:6]}",))[0][0]
    model = f"TQ-{uuid.uuid4().hex[:4]}9"
    pid = owner_rows(dsn, "insert into catalog.product (manufacturer_organization_id, model_number, name) "
                          "values (%s, %s, 'Agitador orbital') returning id::text", (maker, model))[0][0]
    store = Store()
    gid = capture(target, mailbox_id, store, f"tr-{uuid.uuid4().hex[:8]}",
                  make_raw(subject=f"Cotizar {model}", message_id=f"<{uuid.uuid4().hex}@cliente.invalid>"))
    sid = source_record(dsn, gid)
    with triage_conn(target) as conn:
        result = triage_one(TriageDb(conn), store, sid, ModelSettings(None, DEFAULT_MODEL))
    assert result.mentions == 1
    key = model.replace("-", "").upper()
    assert owner_rows(dsn, "select value_norm, value->>'catalog_product_id', value->>'source' from evidence.assertion "
                           "where source_record_id = %s and kind = 'product_mention'", (sid,)) == \
        [(key, pid, "catalog_model_key")]


def test_a_model_printed_as_an_alias_becomes_a_mention_of_its_product(dsn, target, mailbox_id) -> None:
    # The catalog stores the maker's item number; the email prints the model (DOMAIN.md §7 #59).
    maker = owner_rows(dsn, "insert into crm.organization (kind, name, confirmation) values ('manufacturer', %s, "
                            "'machine_proposed') returning id::text", (f"Fabricante Ejemplo {uuid.uuid4().hex[:6]}",))[0][0]
    item = f"3{uuid.uuid4().int % 10**7:07d}"
    alias = f"XQ{uuid.uuid4().hex[:4]}7"
    pid = owner_rows(dsn, "insert into catalog.product (manufacturer_organization_id, model_number, name) "
                          "values (%s, %s, 'Balanza analítica') returning id::text", (maker, item))[0][0]
    op = owner_rows(dsn, "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                         "values (gen_random_uuid(), %s, 'Alias Prueba', 'sales', 'active') returning id::text",
                    (f"alias-{uuid.uuid4().hex[:8]}@example.test",))[0][0]
    owner_rows(dsn, "insert into catalog.product_model_alias (product_id, alias, source, created_by_operator_id) "
                    "values (%s, %s, 'operator', %s) returning id", (pid, alias, op))
    store = Store()
    gid = capture(target, mailbox_id, store, f"tr-{uuid.uuid4().hex[:8]}",
                  make_raw(subject=f"Cotizar balanza {alias}", message_id=f"<{uuid.uuid4().hex}@cliente.invalid>"))
    sid = source_record(dsn, gid)
    with triage_conn(target) as conn:
        result = triage_one(TriageDb(conn), store, sid, ModelSettings(None, DEFAULT_MODEL))
    assert result.mentions == 1
    assert owner_rows(dsn, "select value_norm, value->>'catalog_product_id', value->>'source' from evidence.assertion "
                           "where source_record_id = %s and kind = 'product_mention'", (sid,)) == \
        [(item, pid, "catalog_model_key")]


def test_the_full_text_query_runs_on_the_real_index(target) -> None:
    from origenlab_worker.catalog_match import match_products

    with triage_conn(target) as conn:
        with conn.cursor() as cur:
            assert isinstance(match_products(cur, "Agitador orbital", "con calefacción para laboratorio"), list)
        assert TriageDb(conn).linked_cases(f"no-such-thread-{uuid.uuid4().hex}") == []


def test_a_job_deferred_through_the_real_connector_lands_in_the_queue(dsn, target) -> None:
    from origenlab_worker import task_queue

    app = task_queue.build_app(task_queue.connector_for(target, max_size=1))
    lock = f"test-{uuid.uuid4().hex[:8]}"

    async def go() -> None:
        async with app.open_async():
            await app.tasks["triage_message"].configure(queueing_lock=lock).defer_async(source_record_id="x")

    asyncio.run(go())
    assert owner_rows(dsn, "select task_name, queue_name, status::text from procrastinate.procrastinate_jobs "
                           "where queueing_lock = %s", (lock,)) == [("triage_message", "triage", "todo")]


def test_a_captured_forward_is_read_for_who_asked_by_the_triage_and_the_backfill_skips_it(dsn, target, mailbox_id,
                                                                                        monkeypatch) -> None:
    from origenlab_worker.forwarded_request import FORWARDED_VALUE_NORM
    from origenlab_worker.triage import backfill_forwards

    monkeypatch.delenv("ORIGENLAB_WORKER_FORWARD_READING", raising=False)
    msg = EmailMessage()
    msg["From"] = f"Tatiana <{MAILBOX}>"
    msg["To"] = MAILBOX
    msg["Subject"] = "Fwd: Solicitud cotización Gradillas"
    msg["Message-ID"] = f"<{uuid.uuid4().hex}@origenlab.invalid>"
    msg.set_content("---------- Forwarded message ---------\nDe: Pedro Ejemplo <pedro@universidad-ficticia.cl>\n"
                    "Date: lun, 5 oct 2026\nSubject: Solicitud cotización Gradillas\nTo: <contacto@origenlab.cl>\n\n"
                    "Necesitamos 20 gradillas.\n")
    store = Store()
    sid = source_record(dsn, capture(target, mailbox_id, store, f"fw-{uuid.uuid4().hex[:8]}", msg.as_bytes()))
    with triage_conn(target) as conn:
        db = TriageDb(conn)
        assert sid in db.pending_forwards(since_days=36500, limit=500)
        assert backfill_forwards(db, store, [sid], apply=False)["readable"] == 1
        triage_one(db, store, sid, ModelSettings(None, DEFAULT_MODEL))
        assert sid not in db.pending_forwards(since_days=36500, limit=500)
    # One evidence record of its own: the customer's fields and the email it was read from — no body.
    [(kind, status, payload)] = owner_rows(dsn, "select kind, review_status, payload from evidence.source_record "
                                                "where dedupe_key = %s", (f"forwarded_request:{sid}",))
    assert (kind, status) == ("forwarded_request", "pending")
    assert payload["original_email"] == "pedro@universidad-ficticia.cl" and payload["origin"] == "origenlab"
    assert payload["forwarded_message_id"] == sid and payload["reading"] == FORWARDED_VALUE_NORM


def test_quote_numbers_in_the_body_are_stored_as_tokens_and_the_backfill_reads_older_mail(dsn, target, mailbox_id) -> None:
    from origenlab_worker.quote_refs import QUOTE_REFS_VALUE_NORM
    from origenlab_worker.triage import backfill_quote_refs

    def inbound(body: str) -> bytes:
        msg = EmailMessage()
        msg["From"] = "Pagos <pagos@empresa-ficticia.cl>"
        msg["To"] = MAILBOX
        msg["Subject"] = "Aviso de transferencia"
        msg["Message-ID"] = f"<{uuid.uuid4().hex}@empresa-ficticia.invalid>"
        msg.set_content(body)
        return msg.as_bytes()

    store = Store()
    fresh = source_record(dsn, capture(target, mailbox_id, store, f"qr-{uuid.uuid4().hex[:8]}",
                                       inbound("Transferencia por cotización N°09259-26.")))
    older = source_record(dsn, capture(target, mailbox_id, store, f"qr-{uuid.uuid4().hex[:8]}",
                                       inbound("Pago de la CN09262, saludos.")))
    with triage_conn(target) as conn:
        db = TriageDb(conn)
        triage_one(db, store, fresh, ModelSettings(None, DEFAULT_MODEL))
        # `older` stands for mail triaged before this reading existed: only the backfill reads it.
        assert older in db.pending_quote_refs(since_days=36500, limit=500)
        assert backfill_quote_refs(db, store, [older], apply=False) == {"found": 1, "with_numbers": 1, "recorded": 0,
                                                                         "skipped": 0}
        assert backfill_quote_refs(db, store, [older], apply=True)["recorded"] == 1
        assert older not in db.pending_quote_refs(since_days=36500, limit=500)
    rows = dict(owner_rows(dsn, "select source_record_id::text, value from evidence.assertion "
                                "where kind = 'message_triage' and value_norm = %s and source_record_id = any(%s::uuid[])",
                           (QUOTE_REFS_VALUE_NORM, [fresh, older])))
    assert rows[fresh] == {"version": 1, "tokens": ["09259-26"]}
    assert rows[older] == {"version": 1, "tokens": ["CN09262"]}

def test_the_case_snapshot_leaves_out_discarded_and_long_closed_cases_and_the_reply_finds_its_quote(
    dsn, target, mailbox_id
) -> None:
    tag = uuid.uuid4().hex[:6]
    operator = owner_rows(dsn, "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                               "values (gen_random_uuid(), %s, 'Op', 'sales', 'active') returning id::text",
                          (f"op-{tag}@example.test",))[0][0]

    def case(title: str, quote: str) -> str:
        oid = owner_rows(dsn, "insert into crm.opportunity (title, stage, owner_operator_id) "
                              "values (%s, 'lead', %s) returning id::text", (title, operator))[0][0]
        owner_rows(dsn, "insert into crm.quote (opportunity_id, quote_number, number_origin) "
                        "values (%s, %s, 'printed_historical') returning id::text", (oid, quote))
        return oid

    live = case(f"Vivo {tag}", "09871-26")
    discarded = case(f"Descartado {tag}", "09872-26")
    old = case(f"Antiguo {tag}", "09873-26")
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("set role origenlab_owner")
        conn.execute("update crm.opportunity set stage = 'abandoned', close_reason = 'discarded_by_correction', "
                     "closed_at = now() where id = %s", (discarded,))
        conn.execute("update crm.opportunity set stage = 'lost', close_reason = 'price', "
                     "closed_at = now() - interval '200 days' where id = %s", (old,))

    store = Store()
    gid = f"tr-{tag}"
    capture(target, mailbox_id, store, gid,
            make_raw(frm=MAILBOX, to="ana@cliente.invalid", subject="Cotización", pdf_name="CN09871.pdf",
                     message_id=f"<{uuid.uuid4().hex}@origenlab.invalid>"))
    reply = f"{gid}-r"
    c = build_capture(make_raw(subject="Re: Cotización", message_id=f"<{uuid.uuid4().hex}@cliente.invalid>"),
                      gmail_message_id=reply, gmail_thread_id=f"t-{gid}", label_ids=("INBOX",), label_names={},
                      internal_date_ms=NOW_MS, mailbox_address=MAILBOX)
    raw_key = f"{MAILBOX}/2026/10/{reply}.eml"
    store.objects[raw_key] = make_raw(subject="Re: Cotización", message_id="<x@cliente.invalid>")
    with open_worker_db(target) as wdb:
        wdb.record(c, mailbox_id=mailbox_id, eml_path=f"mail/{raw_key}", eml_sha256=c.payload["raw_sha256"],
                   size_bytes=1)
    sid = source_record(dsn, reply)
    with triage_conn(target) as conn:
        db = TriageDb(conn)
        ids = {card.opportunity_id for card in db.case_cards()}
        assert live in ids and discarded not in ids and old not in ids
        result = triage_one(db, store, sid, ModelSettings(None, DEFAULT_MODEL))
    assert result.case_match == "exact"
    value = owner_rows(dsn, "select value from evidence.assertion where source_record_id = %s and value_norm = %s",
                       (sid, CASE_MATCH_VALUE_NORM))[0][0]
    assert value["candidates"][0] == {"opportunity_id": live, "score": 8, "signals": ["thread_pdf_quote"]}


def test_the_case_snapshot_reads_each_cases_people_and_the_senders_person_follows_merges(dsn, target) -> None:
    """People first: a case's `person_ids` are its current participants' persons (named directly or
    through their contact point), a merged person read as the person it was merged into; the sender's
    person comes from an active, owned address only."""
    tag = uuid.uuid4().hex[:6]
    operator = owner_rows(dsn, "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                               "values (gen_random_uuid(), %s, 'Op', 'sales', 'active') returning id::text",
                          (f"op-p-{tag}@example.test",))[0][0]

    def person(name: str) -> str:
        return owner_rows(dsn, "insert into crm.person (display_name, confirmation) values (%s, 'machine_proposed') "
                               "returning id::text", (f"{name} {tag}",))[0][0]

    def address(value: str, who: str | None, usage: str = "personal", org: str | None = None) -> str:
        return owner_rows(dsn, "insert into crm.contact_point (kind, value_norm, value_display, person_id, "
                               "organization_id, usage, confirmation) values ('email', %s, %s, %s, %s, %s, "
                               "'machine_proposed') returning id::text", (value, value, who, org, usage))[0][0]

    winner, loser, bruno, gone = person("Ana"), person("Ana duplicada"), person("Bruno"), person("Carla")
    owner_rows(dsn, "update crm.person set merged_into_person_id = %s where id = %s returning id", (winner, loser))
    org = owner_rows(dsn, "insert into crm.organization (kind, name, confirmation) values ('institution', %s, "
                          "'confirmed') returning id::text", (f"Instituto Personas {tag}",))[0][0]
    ana_old = f"ana.antigua-{tag}@cliente.invalid"
    address(ana_old, loser)
    bruno_cp = address(f"bruno-{tag}@cliente.invalid", bruno)
    desk = f"compras-{tag}@cliente.invalid"
    address(desk, None, "shared_mailbox", org)
    retired = f"ana.retirada-{tag}@cliente.invalid"
    address(retired, winner)
    owner_rows(dsn, "update crm.contact_point set status = 'inactive', deactivated_at = now(), "
                    "deactivated_by_operator_id = %s where value_norm = %s returning id", (operator, retired))
    oid = owner_rows(dsn, "insert into crm.opportunity (title, stage, owner_operator_id) values (%s, 'lead', %s) "
                          "returning id::text", (f"Caso personas {tag}", operator))[0][0]
    for who, cp, valid_to in ((loser, None, None), (None, bruno_cp, None), (gone, None, "2026-02-01")):
        owner_rows(dsn, "insert into crm.opportunity_participant (opportunity_id, person_id, contact_point_id, role, "
                        "valid_from, valid_to, confirmation) values (%s, %s, %s, 'end_user', '2026-01-01', %s, "
                        "'machine_proposed') returning id", (oid, who, cp, valid_to))
    with triage_conn(target) as conn:
        db = TriageDb(conn)
        card = next(c for c in db.case_cards() if c.opportunity_id == oid)
        senders = {a: db.sender_person(a) for a in (ana_old, ana_old.upper(), desk, retired,
                                                     f"nadie-{tag}@cliente.invalid", None)}
    assert sorted(card.person_ids) == sorted([winner, bruno])
    assert senders == {ana_old: winner, ana_old.upper(): winner, desk: None, retired: None,
                       f"nadie-{tag}@cliente.invalid": None, None: None}


def test_past_decisions_read_links_and_rejected_suggestions_for_the_senders_domain(dsn, target, mailbox_id) -> None:
    tag = uuid.uuid4().hex[:6]
    operator = owner_rows(dsn, "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                               "values (gen_random_uuid(), %s, 'Op', 'sales', 'active') returning id::text",
                          (f"op-p-{tag}@example.test",))[0][0]
    oid = owner_rows(dsn, "insert into crm.opportunity (title, stage, owner_operator_id) "
                          "values (%s, 'lead', %s) returning id::text", (f"Caso pasado {tag}", operator))[0][0]
    store = Store()
    linked = capture(target, mailbox_id, store, f"pd-{tag}",
                     make_raw(frm=f"Tom <jon@prov-{tag}.invalid>", subject="RE: titulador",
                              message_id=f"<{uuid.uuid4().hex}@prov.invalid>"))
    rejected = capture(target, mailbox_id, store, f"pr-{tag}",
                       make_raw(frm=f"Ann <ann@prov-{tag}.invalid>", subject="Certificado",
                                message_id=f"<{uuid.uuid4().hex}@prov.invalid>"))
    owner_rows(dsn, "insert into crm.opportunity_evidence (opportunity_id, source_record_id, relation, "
                    "linked_by_operator_id) values (%s, %s, 'mentions', %s) returning id",
               (oid, source_record(dsn, linked), operator))
    aid = owner_rows(dsn, "insert into evidence.assertion (source_record_id, kind, value_norm, value) "
                          "values (%s, 'message_triage', %s, %s::jsonb) returning id::text",
                     (source_record(dsn, rejected), CASE_MATCH_VALUE_NORM,
                      '{"candidates": [{"opportunity_id": "%s"}]}' % oid))[0][0]
    owner_rows(dsn, "insert into evidence.triage_review (assertion_id, verdict, corrected, reviewed_by_operator_id) "
                    "values (%s, 'rejected', '{}'::jsonb, %s) returning id", (aid, operator))
    with triage_conn(target) as conn:
        past = TriageDb(conn).past_decisions(f"new@prov-{tag}.invalid")
    assert sorted((p.subject, p.opportunity_id, p.verdict) for p in past) == [
        ("Certificado", oid, "rejected"), ("RE: titulador", oid, "linked")]


def test_a_rejection_names_the_case_the_person_was_shown(dsn, target, mailbox_id) -> None:
    tag = uuid.uuid4().hex[:6]
    operator = owner_rows(dsn, "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                               "values (gen_random_uuid(), %s, 'Op', 'sales', 'active') returning id::text",
                          (f"op-n-{tag}@example.test",))[0][0]
    first, shown = (owner_rows(dsn, "insert into crm.opportunity (title, stage, owner_operator_id) "
                                    "values (%s, 'lead', %s) returning id::text", (t, operator))[0][0]
                    for t in (f"Primero {tag}", f"Mostrado {tag}"))
    store = Store()
    gid = capture(target, mailbox_id, store, f"pn-{tag}",
                  make_raw(frm=f"Ann <ann@prov-n-{tag}.invalid>", subject="Plazo de entrega",
                           message_id=f"<{uuid.uuid4().hex}@prov.invalid>"))
    aid = owner_rows(dsn, "insert into evidence.assertion (source_record_id, kind, value_norm, value) "
                          "values (%s, 'message_triage', %s, %s::jsonb) returning id::text",
                     (source_record(dsn, gid), CASE_MATCH_VALUE_NORM,
                      '{"pick": {"choice": "none"}, "candidates": [{"opportunity_id": "%s"}]}' % first))[0][0]
    owner_rows(dsn, "insert into evidence.triage_review (assertion_id, verdict, corrected, note, reviewed_by_operator_id) "
                    "values (%s, 'rejected', '{}'::jsonb, %s, %s) returning id",
               (aid, f"No es de este caso · {shown}", operator))
    with triage_conn(target) as conn:
        past = TriageDb(conn).past_decisions(f"x@prov-n-{tag}.invalid")
    assert [(p.opportunity_id, p.verdict) for p in past] == [(shown, "rejected")]


def test_an_email_a_person_removed_from_a_case_teaches_the_matcher_not_that_case(dsn, target, mailbox_id) -> None:
    # «Quitar» on Hoy (or unlinking by hand) sets unlinked_at: the next pick sees it as rejected.
    tag = uuid.uuid4().hex[:6]
    operator = owner_rows(dsn, "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                               "values (gen_random_uuid(), %s, 'Op', 'sales', 'active') returning id::text",
                          (f"op-q-{tag}@example.test",))[0][0]
    oid = owner_rows(dsn, "insert into crm.opportunity (title, stage, owner_operator_id) "
                          "values (%s, 'lead', %s) returning id::text", (f"Caso quitado {tag}", operator))[0][0]
    store = Store()
    gid = capture(target, mailbox_id, store, f"pq-{tag}",
                  make_raw(frm=f"Eva <eva@cli-q-{tag}.invalid>", subject="Consulta de plazo",
                           message_id=f"<{uuid.uuid4().hex}@cli.invalid>"))
    link = owner_rows(dsn, "insert into crm.opportunity_evidence (opportunity_id, source_record_id, relation, "
                           "linked_by_operator_id) values (%s, %s, 'mentions', %s) returning id::text",
                      (oid, source_record(dsn, gid), operator))[0][0]
    owner_rows(dsn, "update crm.opportunity_evidence set unlinked_at = now(), unlink_reason = 'Quitado desde Hoy' "
                    "where id = %s returning id", (link,))
    with triage_conn(target) as conn:
        past = TriageDb(conn).past_decisions(f"otra@cli-q-{tag}.invalid")
    assert [(p.subject, p.opportunity_id, p.verdict) for p in past] == [("Consulta de plazo", oid, "rejected")]


def test_the_memory_backfill_lists_business_mail_filed_on_an_open_case_oldest_first(dsn, target, mailbox_id) -> None:
    tag = uuid.uuid4().hex[:6]
    operator = owner_rows(dsn, "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                               "values (gen_random_uuid(), %s, 'Op', 'sales', 'active') returning id::text",
                          (f"op-m-{tag}@example.test",))[0][0]
    oid = owner_rows(dsn, "insert into crm.opportunity (title, stage, owner_operator_id) "
                          "values (%s, 'lead', %s) returning id::text", (f"Caso memoria {tag}", operator))[0][0]
    store = Store()
    ids = []
    for n in range(2):
        sid = source_record(dsn, capture(target, mailbox_id, store, f"mb-{tag}-{n}",
                                         make_raw(subject=f"Solicitud de cotización {n}",
                                                  date=f"Mon, 1{2 + n} Oct 2026 10:00:00 -0300",
                                                  message_id=f"<{uuid.uuid4().hex}@cliente.invalid>")))
        with triage_conn(target) as conn:
            triage_one(TriageDb(conn), store, sid, ModelSettings(None, DEFAULT_MODEL))
        owner_rows(dsn, "insert into crm.opportunity_evidence (opportunity_id, source_record_id, relation, "
                        "linked_by_operator_id) values (%s, %s, 'mentions', %s) returning id", (oid, sid, operator))
        ids.append(sid)
    with triage_conn(target) as conn:
        pending = TriageDb(conn).pending_memory_backfill(since_days=36500, limit=500)
    assert [p for p in pending if p in ids] == ids  # both, the older first


def test_the_memory_backfill_writes_case_institution_and_person_memories_on_a_real_database(dsn, target, mailbox_id) -> None:
    import json
    from types import SimpleNamespace

    from origenlab_worker.memory import backfill_memory

    def reader(request):
        memory = {"summary": "Pide una balanza; cliente nuevo.", "facts": [{"k": "product", "v": "Balanza"}],
                  "open_questions": [], "next_step": {"action": "responder", "why": "Pidió precio.", "confidence": "high"}}
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(memory))])

    tag = uuid.uuid4().hex[:6]
    sender = f"ana-{tag}@cliente.invalid"
    operator = owner_rows(dsn, "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                               "values (gen_random_uuid(), %s, 'Op', 'sales', 'active') returning id::text",
                          (f"op-w-{tag}@example.test",))[0][0]
    org = owner_rows(dsn, "insert into crm.organization (kind, name, confirmation) values ('institution', %s, 'confirmed') "
                          "returning id::text", (f"Instituto Ficticio {tag}",))[0][0]
    person = owner_rows(dsn, "insert into crm.person (display_name, confirmation) values (%s, 'confirmed') returning id::text", (f"Ana {tag}",))[0][0]
    owner_rows(dsn, "insert into crm.contact_point (kind, value_norm, value_display, person_id, usage, confirmation) "
                    "values ('email', %s, %s, %s, 'personal', 'confirmed') returning id", (sender, sender, person))
    # The case and its confirmed requesting institution are written together (the agreement trigger).
    oid = owner_rows(dsn, "with o as (insert into crm.opportunity (title, stage, owner_operator_id, organization_id) "
                          "values (%s, 'lead', %s, %s) returning id), "
                          "oo as (insert into crm.opportunity_organization (opportunity_id, organization_id, role, "
                          "valid_from, confirmation, confirmed_by_operator_id) "
                          "select id, %s, 'requesting_institution', current_date, 'confirmed', %s from o) "
                          "select id::text from o", (f"Caso {tag}", operator, org, org, operator))[0][0]
    store = Store()
    sid = source_record(dsn, capture(target, mailbox_id, store, f"mw-{tag}",
                                     make_raw(frm=f"Ana <{sender}>", subject="Solicitud de cotización balanza",
                                              message_id=f"<{uuid.uuid4().hex}@cliente.invalid>")))
    with triage_conn(target) as conn:
        triage_one(TriageDb(conn), store, sid, ModelSettings(None, DEFAULT_MODEL))
    owner_rows(dsn, "insert into crm.opportunity_evidence (opportunity_id, source_record_id, relation, linked_by_operator_id) "
                    "values (%s, %s, 'mentions', %s) returning id", (oid, sid, operator))
    with triage_conn(target) as conn:
        db = TriageDb(conn)
        counts = backfill_memory(db, store, reader, [sid], apply=True, model="claude-haiku-5-5")
    assert counts["updated"] == 3, counts
    kinds = dict(owner_rows(dsn, "select subject_kind, subject_id::text from evidence.subject_memory "
                                 "where subject_id = any(%s::uuid[])", ([oid, org, person],)))
    assert kinds == {"opportunity": oid, "organization": org, "person": person}


def test_a_tender_is_never_a_triage_case_a_matcher_candidate_or_a_memory(dsn, target, mailbox_id) -> None:
    """Spec 2026-10-19 §6.10 (`origenlab_api.v2.case_kind`): a tender (kind 'tender') lives in
    «Licitaciones». The same email filed on a sale case and on a tender: the sale is a candidate, a
    linked case, a past decision and a memory to write; the tender is none of them."""
    tag = uuid.uuid4().hex[:6]
    operator = owner_rows(dsn, "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                               "values (gen_random_uuid(), %s, 'Op', 'sales', 'active') returning id::text",
                          (f"op-t-{tag}@example.test",))[0][0]
    store = Store()
    cases, threads, emails = {}, {}, {}
    for kind, number in (("sale", "09881-26"), ("tender", "09882-26")):
        oid = owner_rows(dsn, "insert into crm.opportunity (title, stage, owner_operator_id, kind, tender_status) "
                              "values (%s, 'lead', %s, %s, %s) returning id::text",
                         (f"Caso {kind} {tag}", operator, kind, "preparando" if kind == "tender" else None))[0][0]
        owner_rows(dsn, "insert into crm.quote (opportunity_id, quote_number, number_origin) "
                        "values (%s, %s, 'printed_historical') returning id::text", (oid, number))
        gid = capture(target, mailbox_id, store, f"tk-{kind}-{tag}",
                      make_raw(frm=f"Compras <compras@hospital-{tag}.invalid>", subject=f"Solicitud de cotización {kind}",
                               message_id=f"<{uuid.uuid4().hex}@hospital.invalid>"))
        sid = source_record(dsn, gid)
        with triage_conn(target) as conn:
            triage_one(TriageDb(conn), store, sid, ModelSettings(None, DEFAULT_MODEL))
        owner_rows(dsn, "insert into crm.opportunity_evidence (opportunity_id, source_record_id, relation, "
                        "linked_by_operator_id) values (%s, %s, 'mentions', %s) returning id", (oid, sid, operator))
        cases[kind], threads[kind], emails[kind] = oid, f"t-{gid}", sid
    with triage_conn(target) as conn:
        db = TriageDb(conn)
        candidates = {card.opportunity_id for card in db.case_cards()}
        linked = {kind: [c.opportunity_id for c in db.linked_cases(threads[kind])] for kind in cases}
        past = {p.opportunity_id for p in db.past_decisions(f"otra@hospital-{tag}.invalid")}
        pending = set(db.pending_memory_backfill(since_days=36500, limit=500))
        held = {kind: db.tender_thread(threads[kind]) for kind in cases}
    assert held == {"sale": False, "tender": True}
    # A reply on each thread, triaged now: the sale's is matched to the sale; the tender's thread is the
    # tender's, so its reply gets no sales case, no new case and no pick.
    replies = {}
    for kind in cases:
        gid = f"tk-{kind}-{tag}-r"
        c = build_capture(make_raw(frm=f"Compras <compras@hospital-{tag}.invalid>", subject="Re: Solicitud de cotización",
                                   message_id=f"<{uuid.uuid4().hex}@hospital.invalid>"),
                          gmail_message_id=gid, gmail_thread_id=threads[kind], label_ids=("INBOX",), label_names={},
                          internal_date_ms=NOW_MS, mailbox_address=MAILBOX)
        key = f"{MAILBOX}/2026/10/{gid}.eml"
        store.objects[key] = make_raw(subject="Re: Solicitud de cotización", message_id=f"<{gid}@hospital.invalid>")
        with open_worker_db(target) as wdb:
            wdb.record(c, mailbox_id=mailbox_id, eml_path=f"mail/{key}", eml_sha256=c.payload["raw_sha256"],
                       size_bytes=1)
        replies[kind] = source_record(dsn, gid)
        with triage_conn(target) as conn:
            triage_one(TriageDb(conn), store, replies[kind], ModelSettings(None, DEFAULT_MODEL))
    value = {kind: owner_rows(dsn, "select value from evidence.assertion where source_record_id = %s "
                                   "and value_norm = %s", (replies[kind], CASE_MATCH_VALUE_NORM))[0][0]
             for kind in cases}
    assert value["sale"]["candidates"][0]["opportunity_id"] == cases["sale"]
    assert (value["tender"]["decision"], value["tender"]["candidates"], value["tender"]["new_case_quote"],
            value["tender"]["held_by"]) == ("none", [], None, "tender")
    assert cases["sale"] in candidates and cases["tender"] not in candidates
    assert linked == {"sale": [cases["sale"]], "tender": []}
    assert past == {cases["sale"]}
    assert emails["sale"] in pending and emails["tender"] not in pending


def test_who_wrote_an_inbound_email_is_read_from_its_participants_into_people_v1(dsn, target, mailbox_id) -> None:
    from origenlab_worker.people_reading import PEOPLE_VALUE_NORM

    store = Store()
    raw = make_raw(frm="Juan Pedro Soto Rivas <pjsoto@hotmail.invalid>",
                   to=f"{MAILBOX}, compras@ficticia.invalid",
                   cc="Ana <ana@origenlab.cl>, Tatiana <contacto@labdelivery.cl>, lab@ficticia.invalid",
                   subject="Consulta de equipos", message_id=f"<{uuid.uuid4().hex}@hotmail.invalid>")
    sid = source_record(dsn, capture(target, mailbox_id, store, f"pp-{uuid.uuid4().hex[:8]}", raw))
    before = business_rows(dsn)
    with triage_conn(target) as conn:
        triage_one(TriageDb(conn), store, sid, ModelSettings(None, DEFAULT_MODEL))
    [(value,)] = owner_rows(dsn, "select value from evidence.assertion where source_record_id = %s "
                                 "and kind = 'message_triage' and value_norm = %s", (sid, PEOPLE_VALUE_NORM))
    assert value == {"version": 1, "sender_address": "pjsoto@hotmail.invalid", "header_name": "Juan Pedro Soto Rivas",
                     "signature": None, "co_addresses": ["compras@ficticia.invalid", "lab@ficticia.invalid"],
                     "signature_raw": ""}
    assert business_rows(dsn) == before  # nothing in crm.* or outbound.*


def test_a_bounce_gets_no_people_reading_on_a_real_database(dsn, target, mailbox_id) -> None:
    from origenlab_worker.people_reading import PEOPLE_VALUE_NORM

    store = Store()
    sid = source_record(dsn, capture(target, mailbox_id, store, f"pb-{uuid.uuid4().hex[:8]}",
                                     ndr_raw("550 5.1.1 User unknown", failed="dead@cliente.invalid")))
    with triage_conn(target) as conn:
        assert triage_one(TriageDb(conn), store, sid, ModelSettings(None, DEFAULT_MODEL)).triage_class == "bounce"
    assert owner_rows(dsn, "select 1 from evidence.assertion where source_record_id = %s and value_norm = %s",
                      (sid, PEOPLE_VALUE_NORM)) == []
