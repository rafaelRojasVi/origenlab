"""The quote reader's run on a disposable `origenlab_test_<hex>` database, as the real
`origenlab_worker` login: a registered revision's PDF becomes one `quote_document` and its lines;
a rerun writes nothing; one PDF on two revisions is one document; a refusal is one `quote_read`
observation and no line, and is not retried the same week. Every value is invented."""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest

from dbhelp import owner_rows, seed_mailbox
from origenlab_worker.database import local_test_target
from origenlab_worker.quote_reader import QUOTE_READ_REQUIRED_POLICIES, QUOTE_READ_REQUIRED_PRIVILEGES, QuoteReader
from origenlab_worker.database import open_verified_connection
from test_drive_filing import _pdf, _seed_quote, _Store
from v2_command_harness import build_disposable_database, needs_worker_db, worker_dsn

pytestmark = needs_worker_db
MODEL = "claude-opus-5-5"


@pytest.fixture(scope="module")
def dsn():
    yield from build_disposable_database()


@pytest.fixture(scope="module")
def mailbox_id(dsn):
    return seed_mailbox(dsn)


def _extraction(quote_number: str, **over):
    base = {"is_origenlab_issued_quote": True, "quote_number": quote_number, "date": "2026-10-01",
            "client_type": "private_company", "currency": "CLP", "subtotal_net": "300000", "template": None,
            "client_institution": None, "client_unit": None, "client_contact": None,
            "lines": [{"item": "1", "parent_item": None, "kind": "equipment", "brand": "ACME", "model": "XF-200",
                       "description": "Equipo ficticio", "qty": "1", "unit_price": "300000",
                       "line_total": "300000", "optional": False}]}
    base.update(over)
    return base


class Reader:
    def __init__(self, extraction):
        self.extraction = extraction
        self.calls = 0

    def __call__(self, request):
        self.calls += 1
        return SimpleNamespace(stop_reason="end_turn", usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                               content=[SimpleNamespace(type="text", text=json.dumps(self.extraction))])


def _conn(dsn):
    return open_verified_connection(local_test_target(worker_dsn(dsn)), application_name="test-quote-read",
                                    privileges=QUOTE_READ_REQUIRED_PRIVILEGES, policies=QUOTE_READ_REQUIRED_POLICIES)


def _documents(dsn, sha):
    return owner_rows(dsn, "select id::text, payload from evidence.source_record where dedupe_key = %s",
                      (f"quote_document:{sha}",))


def test_a_registered_quote_is_read_once_into_a_document_and_its_lines(dsn, mailbox_id) -> None:
    store = _Store()
    quote = _seed_quote(dsn, mailbox_id, pdf=_pdf(uuid.uuid4().hex), quote_number="00991-26", store=store)
    reader = Reader(_extraction("00991-26"))
    with _conn(dsn) as conn:
        counts = QuoteReader(conn, store, reader, MODEL).run()
        assert counts.as_log()["read"] == 1, counts.as_log()
        again = QuoteReader(conn, store, reader, MODEL).run()
        assert again.candidates == 0 and reader.calls == 1  # read: never read twice
    docs = _documents(dsn, quote["sha"])
    assert len(docs) == 1 and docs[0][1]["net_total"] == "300000"
    lines = owner_rows(dsn, "select model, line_total::text, extractor, check_status from evidence.document_line "
                            "where source_record_id = %s", (docs[0][0],))
    assert lines == [("XF-200", "300000.0000", "ai_v2+template_v1", "single_source")]


def test_one_pdf_on_two_revisions_is_one_document(dsn, mailbox_id) -> None:
    store = _Store()
    pdf = _pdf(uuid.uuid4().hex)
    first = _seed_quote(dsn, mailbox_id, pdf=pdf, quote_number="00992-26", store=store)
    _seed_quote(dsn, mailbox_id, pdf=pdf, quote_number="00993-26", store=store)
    with _conn(dsn) as conn:
        QuoteReader(conn, store, Reader(_extraction("00992-26")), MODEL).run()
        assert QuoteReader(conn, store, Reader(_extraction("00992-26")), MODEL).run().candidates == 0
    assert len(_documents(dsn, first["sha"])) == 1


def test_a_refused_pdf_writes_one_observation_no_line_and_is_not_retried_this_week(dsn, mailbox_id) -> None:
    store = _Store()
    quote = _seed_quote(dsn, mailbox_id, pdf=_pdf(uuid.uuid4().hex), quote_number="00994-26", store=store)
    reader = Reader(_extraction("00994-26", is_origenlab_issued_quote=False))
    with _conn(dsn) as conn:
        counts = QuoteReader(conn, store, reader, MODEL).run()
        assert counts.as_log()["refused"] == {"not_origenlab_quote": 1}
        assert QuoteReader(conn, store, reader, MODEL).run().candidates == 0 and reader.calls == 1
    assert _documents(dsn, quote["sha"]) == []
    seen = owner_rows(dsn, "select a.value_norm, a.value ->> 'code' from evidence.assertion a "
                           "join crm.quote_revision r on r.origin_source_record_id = a.source_record_id "
                           "where a.kind = 'quote_read' and r.pdf_sha256 = %s", (quote["sha"],))
    assert len(seen) == 1 and seen[0][0].startswith("quote_read:v1:") and seen[0][1] == "not_origenlab_quote"


def test_with_the_model_off_nothing_is_written_or_recorded(dsn, mailbox_id) -> None:
    store = _Store()
    quote = _seed_quote(dsn, mailbox_id, pdf=_pdf(uuid.uuid4().hex), quote_number="00995-26", store=store)
    with _conn(dsn) as conn:
        counts = QuoteReader(conn, store, None, "off").run()
        assert counts.as_log()["refused"].get("model_off", 0) >= 1
        assert QuoteReader(conn, store, None, "off").run().candidates >= 1  # still waiting for the model
    assert _documents(dsn, quote["sha"]) == []
    assert owner_rows(dsn, "select count(*) from evidence.assertion a join crm.quote_revision r "
                           "on r.origin_source_record_id = a.source_record_id where a.kind = 'quote_read' "
                           "and r.pdf_sha256 = %s", (quote["sha"],)) == [(0,)]
