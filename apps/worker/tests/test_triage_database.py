"""The mail triage and its queue on a disposable `origenlab_test_<hex>` database, as the real
`origenlab_worker` login: the connection probes for both jobs, the pending query, the recorded
proposals and their idempotency, the catalog queries, and a job deferred into Procrastinate."""

from __future__ import annotations

import asyncio
import uuid

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
from origenlab_worker.triage import VALUE_NORM, ModelSettings, TriageDb, triage_one
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
    rows = owner_rows(dsn, "select kind, value_norm, resolution, value->>'class', value->>'model_state' "
                           "from evidence.assertion where source_record_id = %s", (sid,))
    assert rows == [("message_triage", VALUE_NORM, "unresolved", "quote_request", "off")]
    assert owner_rows(dsn, "select review_status from evidence.source_record where id = %s", (sid,)) == [("pending",)]
    assert business_rows(dsn) == before  # nothing in crm.* or outbound.*


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


def test_the_full_text_query_runs_on_the_real_index(target) -> None:
    from origenlab_worker.catalog_match import match_products

    with triage_conn(target) as conn, conn.cursor() as cur:
        assert isinstance(match_products(cur, "Agitador orbital", "con calefacción para laboratorio"), list)


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
