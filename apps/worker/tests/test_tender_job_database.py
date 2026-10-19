"""The tender `read` job on a disposable `origenlab_test_<hex>` database, as the real
`origenlab_worker` login, with the anexos in an in-process S3 (moto): a queued preparation is
claimed, read and written back with its spend; the monthly cap stops it before the model is
called; a refusal fails it by code; a locked row is skipped.

The tests share one database and one S3, so each seeds its own case. Every value is invented."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import psycopg
import pytest

from conftest import REGION
from dbhelp import owner_rows
from origenlab_worker.database import local_test_target, open_verified_connection
from origenlab_worker.storage import S3EmlStore
from origenlab_worker.tender.job import (
    BID_REQUIRED_POLICIES,
    BID_REQUIRED_PRIVILEGES,
    DOCUMENTS_BUCKET,
    BidModels,
    BidPreparer,
    run_bid_sweep,
)
from origenlab_worker.tender.read_model import RESUMEN_FIELDS
from test_quote_reader import _minimal_pdf
from v2_command_harness import build_disposable_database, needs_worker_db, worker_dsn

pytestmark = needs_worker_db
MODELS = BidModels(read="claude-opus-5-5", fill="claude-opus-5-5", match="claude-sonnet-5-5")
#: The cap test's own month: its seeded spend never reaches the other tests' month.
CAP_MONTH = datetime(2026, 2, 10, 15, 0, tzinfo=UTC)


def _now() -> datetime:
    return datetime.now(UTC)


@pytest.fixture(scope="module")
def dsn():
    yield from build_disposable_database()


@pytest.fixture(scope="module")
def store(s3_client):
    s3_client.create_bucket(Bucket=DOCUMENTS_BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
    return S3EmlStore(s3_client, bucket=DOCUMENTS_BUCKET)


@pytest.fixture(scope="module")
def operator(dsn):
    return owner_rows(dsn, "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                           "values (gen_random_uuid(), 'op-licitaciones@example.test', 'Op', 'sales', 'active') "
                           "returning id::text")[0][0]


def _conn(dsn):
    return open_verified_connection(local_test_target(worker_dsn(dsn)), application_name="test-bid-preparation",
                                    privileges=BID_REQUIRED_PRIVILEGES, policies=BID_REQUIRED_POLICIES)


def _case(dsn, operator) -> str:
    return owner_rows(dsn, "insert into crm.opportunity (title, stage, owner_operator_id, kind, tender_status) "
                           "values (%s, 'lead', %s, 'tender', 'preparando') returning id::text",
                      (f"Licitación ficticia {uuid.uuid4().hex[:6]}", operator))[0][0]


def _annex(dsn, store, operator, case: str, data: bytes, *, filename: str = "bases.pdf",
           content_type: str = "application/pdf", ext: str = "pdf") -> str:
    sha = hashlib.sha256(data).hexdigest()
    path = f"opportunities/{case}/{sha}.{ext}"
    store._client.put_object(Bucket=DOCUMENTS_BUCKET, Key=path, Body=data)
    return owner_rows(dsn, "insert into crm.opportunity_document (opportunity_id, kind, storage_path, sha256, "
                           "content_type, byte_size, original_filename, source, attached_by_operator_id) "
                           "values (%s, 'tender_annex', %s, %s, %s, %s, %s, 'upload', %s) returning id::text",
                      (case, path, sha, content_type, len(data), filename, operator))[0][0]


def _prep(dsn, operator, case: str, annexes: list[str]) -> str:
    return owner_rows(dsn, "insert into evidence.bid_preparation (opportunity_id, phase, annex_document_ids, "
                           "requested_by_operator_id) values (%s, 'read', %s::uuid[], %s) returning id::text",
                      (case, annexes, operator))[0][0]


def _queued(dsn, store, operator, text: str = "Fecha de cierre 30 de octubre") -> tuple[str, str, str]:
    case = _case(dsn, operator)
    doc = _annex(dsn, store, operator, case, _minimal_pdf(f"{text} {uuid.uuid4().hex[:6]}"))
    return case, doc, _prep(dsn, operator, case, [doc])


def _answer(doc: str) -> dict:
    cita = {"archivo_id": doc, "ubicacion": "p.1"}
    return {
        "resumen": {name: {"texto": f"Texto de {name}", "citas": [cita]} for name in RESUMEN_FIELDS},
        "checklist": [{"documento": "Anexo 1", "tipo": "formulario", "requiere_firma": True,
                       "archivo_formulario_id": doc, "fuente": cita}],
        "items": [{"linea": 1, "descripcion": "Balanza analítica", "cantidad": "2", "unidad": "unidad",
                   "requisitos": ["0,1 mg"], "citas": [cita]}],
    }


class Reader:
    def __init__(self, answer=None, *, stop="end_turn", error=None):
        self.answer, self.stop, self.error = answer, stop, error
        self.calls = 0
        self.requests: list[dict] = []

    def __call__(self, request):
        self.calls += 1
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(stop_reason=self.stop, model=request["model"],
                               content=[SimpleNamespace(type="text", text=json.dumps(self.answer or {}))],
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=200,
                                                     cache_read_input_tokens=0, cache_creation_input_tokens=0))


def _row(dsn, prep: str):
    return owner_rows(dsn, "select status, failure_code, result, models, started_at is not null, "
                           "finished_at is not null from evidence.bid_preparation where id = %s", (prep,))[0]


def _usage(dsn, prep: str, step: str = "read") -> int:
    return owner_rows(dsn, "select count(*) from platform.model_usage where bid_preparation_id = %s and step = %s",
                      (prep, step))[0][0]


def test_job_ready_and_usage_recorded(dsn, store, operator) -> None:
    case, doc, prep = _queued(dsn, store, operator)
    reader = Reader(_answer(doc))
    with _conn(dsn) as conn:
        status = BidPreparer(conn, store, reader, MODELS, now=_now).run(uuid.UUID(prep))
    assert status == "ready" and _usage(dsn, prep) == 1 and reader.calls == 1
    status, code, result, models, started, finished = _row(dsn, prep)
    assert (status, code, started, finished) == ("ready", None, True, True)
    assert set(result) == {"resumen", "checklist", "items", "annexes"}
    assert result["annexes"] == [{"document_id": doc, "filename": "bases.pdf", "outcome": "extraction_success"}]
    assert result["resumen"]["cierre"]["sin_cita"] is False
    assert result["items"][0]["descripcion"] == "Balanza analítica"
    assert models == {"read": "claude-opus-5-5"}
    content = reader.requests[0]["messages"][0]["content"]
    assert f"<<<ARCHIVO id={doc} nombre=bases.pdf>>>" in content and "Fecha de cierre" in content


def test_job_stops_at_cap_before_calling(dsn, store, operator) -> None:
    case, doc, prep = _queued(dsn, store, operator)
    owner_rows(dsn, "insert into platform.model_usage (feature, model, step, usd_cost, created_at) "
                    "values ('licitaciones', 'claude-opus-5-5', 'read', 59.99, %s)", (CAP_MONTH,))
    reader = Reader(_answer(doc))
    with _conn(dsn) as conn:
        assert BidPreparer(conn, store, reader, MODELS, now=lambda: CAP_MONTH).run(uuid.UUID(prep)) == "budget_stopped"
    assert reader.calls == 0 and _usage(dsn, prep) == 0
    assert _row(dsn, prep)[:2] == ("budget_stopped", "budget_reached")


def test_refusal_stop_reason_fails_row(dsn, store, operator) -> None:
    case, doc, prep = _queued(dsn, store, operator)
    with _conn(dsn) as conn:
        assert BidPreparer(conn, store, Reader(stop="refusal"), MODELS, now=_now).run(uuid.UUID(prep)) == "failed"
    assert _row(dsn, prep)[:2] == ("failed", "model_refused")
    assert _usage(dsn, prep) == 1  # a refused call is still recorded: it may be billed


def test_a_truncated_answer_fails_the_row(dsn, store, operator) -> None:
    case, doc, prep = _queued(dsn, store, operator)
    with _conn(dsn) as conn:
        BidPreparer(conn, store, Reader(stop="max_tokens"), MODELS, now=_now).run(uuid.UUID(prep))
    assert _row(dsn, prep)[:2] == ("failed", "model_truncated")


def test_an_api_error_fails_the_row_without_usage(dsn, store, operator) -> None:
    case, doc, prep = _queued(dsn, store, operator)
    with _conn(dsn) as conn:
        BidPreparer(conn, store, Reader(error=RuntimeError("upstream")), MODELS, now=_now).run(uuid.UUID(prep))
    assert _row(dsn, prep)[:2] == ("failed", "request_failed") and _usage(dsn, prep) == 0


def test_no_readable_annex_fails_before_the_model(dsn, store, operator) -> None:
    case = _case(dsn, operator)
    doc = _annex(dsn, store, operator, case, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600,
                 filename="anexo.doc", content_type="application/msword", ext="doc")
    prep = _prep(dsn, operator, case, [doc])
    reader = Reader(_answer(doc))
    with _conn(dsn) as conn:
        assert BidPreparer(conn, store, reader, MODELS, now=_now).run(uuid.UUID(prep)) == "failed"
    assert _row(dsn, prep)[:2] == ("failed", "no_readable_annex") and reader.calls == 0


def test_a_legacy_word_annex_beside_a_readable_one_is_a_checklist_item(dsn, store, operator) -> None:
    case = _case(dsn, operator)
    pdf = _annex(dsn, store, operator, case, _minimal_pdf(f"Bases {uuid.uuid4().hex[:6]}"))
    doc = _annex(dsn, store, operator, case, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x01" * 600,
                 filename="anexo 3.doc", content_type="application/msword", ext="doc")
    prep = _prep(dsn, operator, case, [pdf, doc])
    with _conn(dsn) as conn:
        assert BidPreparer(conn, store, Reader(_answer(pdf)), MODELS, now=_now).run(uuid.UUID(prep)) == "ready"
    result = _row(dsn, prep)[2]
    assert [a["outcome"] for a in result["annexes"]] == ["extraction_success", "needs_converter"]
    [item] = [c for c in result["checklist"] if c.get("archivo_formulario_id") == doc]
    assert item["documento"] == "Falta: convertir a Word (.docx): anexo 3.doc"


def test_claim_skips_locked(dsn, store, operator) -> None:
    _, _, first = _queued(dsn, store, operator)
    _, _, second = _queued(dsn, store, operator)
    with psycopg.connect(dsn) as holder, holder.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute("select id from evidence.bid_preparation where id = %s for update", (first,))
        with _conn(dsn) as conn:
            claimed = BidPreparer(conn, store, Reader(), MODELS, now=_now).claim()
        holder.rollback()
    assert str(claimed) == second
    assert _row(dsn, second)[0] == "running" and _row(dsn, first)[0] == "queued"
    with _conn(dsn) as conn:  # leave nothing queued or running for the other tests
        preparer = BidPreparer(conn, store, Reader(error=RuntimeError("x")), MODELS, now=_now)
        assert preparer.claim() == uuid.UUID(first)
        assert preparer.claim() is None
        preparer.run(uuid.UUID(first))
        preparer.run(uuid.UUID(second))


def test_the_sweep_takes_the_lock_recovers_an_interrupted_row_and_runs_the_queue(dsn, store, operator) -> None:
    case, doc, stuck = _queued(dsn, store, operator)
    owner_rows(dsn, "update evidence.bid_preparation set status = 'running', started_at = now() where id = %s",
               (stuck,))
    case2, doc2, prep = _queued(dsn, store, operator)
    with _conn(dsn) as conn:
        counts = run_bid_sweep(conn, store, Reader(_answer(doc2)), MODELS, now=_now)
    assert counts == {"outcome": "ran", "interrupted": 1, "claimed": 1, "statuses": {"ready": 1}}
    assert _row(dsn, stuck)[:2] == ("failed", "interrupted") and _row(dsn, prep)[0] == "ready"


def test_a_second_sweep_while_one_holds_the_lock_does_nothing(dsn, store, operator) -> None:
    from origenlab_worker.tender.job import LOCK_NAME

    with _conn(dsn) as holder:
        holder.execute("select pg_advisory_lock(hashtextextended(%s, 0))", (LOCK_NAME,))
        with _conn(dsn) as conn:
            assert run_bid_sweep(conn, store, Reader(), MODELS, now=_now) == {"outcome": "locked"}
