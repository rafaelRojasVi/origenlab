"""The quote reader's run on a disposable `origenlab_test_<hex>` database, as the real
`origenlab_worker` login: a registered revision's PDF becomes one `quote_document` and its lines;
a rerun writes nothing; one PDF on two revisions is one document; a refusal is one `quote_read`
observation per PDF and is not retried the same week; one failing PDF never stops the run.

The tests share one database and one fake Storage, so every assertion is about the PDF the test
seeded. Every value is invented; each PDF has a real text layer (one line) so the reader's
no-text-layer refusal does not fire."""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest

from dbhelp import owner_rows, seed_mailbox
from origenlab_worker.database import local_test_target, open_verified_connection
from origenlab_worker.quote_reader import (
    NET_INCLUDES_OPTIONAL,
    PREVIOUS_READER_VERSION,
    QUOTE_READ_REQUIRED_POLICIES,
    QUOTE_READ_REQUIRED_PRIVILEGES,
    READER_VERSION,
    QuoteReader,
    week_tags,
)
from test_drive_filing import _seed_quote, _Store
from test_quote_reader import _minimal_pdf
from v2_command_harness import build_disposable_database, needs_worker_db, worker_dsn

pytestmark = needs_worker_db
MODEL = "claude-opus-5-5"


@pytest.fixture(scope="module")
def dsn():
    yield from build_disposable_database()


@pytest.fixture(scope="module")
def mailbox_id(dsn):
    return seed_mailbox(dsn)


@pytest.fixture(scope="module")
def store():
    return _Store()


def _pdf() -> bytes:
    return _minimal_pdf(f"COTIZACION FICTICIA {uuid.uuid4().hex[:8]}")


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


class _ApiError(Exception):
    def __init__(self, status):
        super().__init__("upstream")
        self.status_code = status


class FailingFor:
    """Raises `error` for requests whose PDF is `pdf`; answers like `Reader` otherwise."""

    def __init__(self, pdf: bytes, error, extraction):
        import base64

        self.marker, self.error, self.after, self.failed = base64.b64encode(pdf).decode(), error, Reader(extraction), 0

    def __call__(self, request):
        if request["messages"][0]["content"][0]["source"]["data"] == self.marker:
            self.failed += 1
            raise self.error
        return self.after(request)


def _conn(dsn):
    return open_verified_connection(local_test_target(worker_dsn(dsn)), application_name="test-quote-read",
                                    privileges=QUOTE_READ_REQUIRED_PRIVILEGES, policies=QUOTE_READ_REQUIRED_POLICIES)


def _documents(dsn, sha):
    return owner_rows(dsn, "select id::text, payload from evidence.source_record where dedupe_key = %s",
                      (f"quote_document:{sha}",))


def _refusals(dsn, sha):
    return owner_rows(dsn, "select a.value_norm, a.value ->> 'code' from evidence.assertion a "
                           "where a.kind = 'quote_read' and a.value_norm like %s", (f"%:{sha}",))


def _read_all(dsn, store, reader, model=MODEL):
    with _conn(dsn) as conn:
        return QuoteReader(conn, store, reader, model).run(limit=50)


def test_a_registered_quote_is_read_once_into_a_document_and_its_lines(dsn, mailbox_id, store) -> None:
    quote = _seed_quote(dsn, mailbox_id, pdf=_pdf(), quote_number="00991-26", store=store)
    reader = Reader(_extraction("00991-26"))
    _read_all(dsn, store, reader)
    calls = reader.calls
    _read_all(dsn, store, reader)
    assert reader.calls == calls  # read: never read twice
    docs = _documents(dsn, quote["sha"])
    assert len(docs) == 1 and docs[0][1]["net_total"] == "300000"
    lines = owner_rows(dsn, "select model, line_total::text, extractor from evidence.document_line "
                            "where source_record_id = %s", (docs[0][0],))
    assert lines == [("XF-200", "300000.0000", "ai_v2+template_v1")]


def test_one_pdf_on_two_revisions_is_one_document(dsn, mailbox_id, store) -> None:
    pdf = _pdf()
    first = _seed_quote(dsn, mailbox_id, pdf=pdf, quote_number="00992-26", store=store)
    _seed_quote(dsn, mailbox_id, pdf=pdf, quote_number="00993-26", store=store)
    _read_all(dsn, store, Reader(_extraction("00992-26")))
    _read_all(dsn, store, Reader(_extraction("00992-26")))
    assert len(_documents(dsn, first["sha"])) == 1


def test_a_refused_pdf_writes_one_observation_no_line_and_is_not_retried_this_week(dsn, mailbox_id, store) -> None:
    quote = _seed_quote(dsn, mailbox_id, pdf=_pdf(), quote_number="00994-26", store=store)
    reader = Reader(_extraction("00994-26", is_origenlab_issued_quote=False))
    _read_all(dsn, store, reader)
    calls = reader.calls
    _read_all(dsn, store, reader)
    assert reader.calls == calls
    assert _documents(dsn, quote["sha"]) == []
    seen = _refusals(dsn, quote["sha"])
    assert len(seen) == 1 and seen[0][0].startswith(f"{READER_VERSION}:") and seen[0][1] == "not_origenlab_quote"


def test_a_refusal_of_an_earlier_reader_version_does_not_block_a_reread(dsn, mailbox_id, store, monkeypatch) -> None:
    """The candidates key a refusal by reader version and week: v1's refusals this week are re-read by v2."""
    quote = _seed_quote(dsn, mailbox_id, pdf=_pdf(), quote_number="00979-26", store=store)
    monkeypatch.setattr("origenlab_worker.quote_reader.READER_VERSION", "quote_read:v1")
    _read_all(dsn, store, Reader(_extraction("00979-26", is_origenlab_issued_quote=False)))
    assert [(norm.split(":")[1], code) for norm, code in _refusals(dsn, quote["sha"])] == [("v1", "not_origenlab_quote")]
    monkeypatch.undo()
    reader = Reader(_extraction("00979-26"))
    counts = _read_all(dsn, store, reader)
    assert reader.calls >= 1 and counts.read >= 1 and len(_documents(dsn, quote["sha"])) == 1


def test_a_neto_that_includes_the_optional_lines_is_written_flagged(dsn, mailbox_id, store) -> None:
    quote = _seed_quote(dsn, mailbox_id, pdf=_pdf(), quote_number="00978-26", store=store)
    extraction = _extraction("00978-26", subtotal_net="350000")
    extraction["lines"].append({"item": "2", "parent_item": None, "kind": "accessory", "brand": "ACME", "model": "OPC-9",
                                "description": "Opcional ficticio", "qty": "1", "unit_price": "50000",
                                "line_total": "50000", "optional": True})
    counts = _read_all(dsn, store, Reader(extraction))
    [(_, payload)] = _documents(dsn, quote["sha"])
    assert payload["net_total"] == "350000" and payload[NET_INCLUDES_OPTIONAL] is True
    from origenlab_api.v2.quote_documents import QUOTE_DOCUMENT_COLUMNS, quote_contents

    [row] = owner_rows(dsn, f"select {QUOTE_DOCUMENT_COLUMNS} from evidence.source_record qd where qd.dedupe_key = %s",
                       (f"quote_document:{quote['sha']}",))
    contents = quote_contents(dict(zip(("doc_currency", "doc_net_total", "doc_net_includes_optional", "doc_lines"), row,
                                       strict=True)))
    assert contents["net_includes_optional"] is True and contents["net_total"] == "350000"
    assert counts.as_log()[NET_INCLUDES_OPTIONAL] >= 1  # this PDF (any other unread one gets the same answer)


def _record_refusal(dsn, quote, version: str, code: str) -> None:
    """A refusal as `QuoteReader._refused` records it, this ISO week, under `version`."""
    from datetime import date

    week = week_tags(date.today())[0].split(":")[-1]
    revision, source = owner_rows(dsn, "select id::text, origin_source_record_id::text from crm.quote_revision "
                                       "where pdf_sha256 = %s", (quote["sha"],))[0]
    owner_rows(dsn, "insert into evidence.assertion (source_record_id, kind, value_norm, value) "
                    "values (%s, 'quote_read', %s, %s::jsonb)",
               (source, f"{version}:{week}:{quote['sha']}", json.dumps({"code": code, "counts": {}, "revision_id": revision})))


@pytest.mark.parametrize("n,code,reread", [(0, "not_origenlab_quote", True), (1, "net_mismatch", True),
                                           (2, "model_refused", False), (3, "unreadable", False),
                                           (4, "no_text_layer", False)])
def test_a_bump_rereads_only_the_previous_versions_refusals_it_changed(dsn, mailbox_id, store, n, code, reread) -> None:
    number = f"0096{n}-26"
    quote = _seed_quote(dsn, mailbox_id, pdf=_pdf(), quote_number=number, store=store)
    _record_refusal(dsn, quote, PREVIOUS_READER_VERSION, code)
    _read_all(dsn, store, Reader(_extraction(number)))
    assert len(_documents(dsn, quote["sha"])) == (1 if reread else 0)


def test_operaciones_counts_pending_pdfs_against_the_current_versions_refusals(dsn, mailbox_id, store) -> None:
    from origenlab_api.v2.operations import _QUOTES_PENDING_SQL

    import psycopg

    def pending() -> int:
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute("set role origenlab_owner")
            cur.execute(_QUOTES_PENDING_SQL)  # as `read_operation_facts` runs it: no parameters
            return cur.fetchone()[0]

    quotes = [_seed_quote(dsn, mailbox_id, pdf=_pdf(), quote_number=f"0095{n}-26", store=store) for n in range(4)]
    before = pending()
    _record_refusal(dsn, quotes[0], READER_VERSION, "model_refused")  # refused now: not pending
    _record_refusal(dsn, quotes[1], PREVIOUS_READER_VERSION, "net_mismatch")  # v2 re-reads it: pending
    _record_refusal(dsn, quotes[2], PREVIOUS_READER_VERSION, "model_refused")  # still blocked: not pending
    assert pending() == before - 2


def test_cotizaciones_no_leidas_lists_only_the_newest_refusal_of_each_pdf(dsn, mailbox_id, store) -> None:
    import psycopg

    from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository

    quote = _seed_quote(dsn, mailbox_id, pdf=_pdf(), quote_number="00949-26", store=store)
    _record_refusal(dsn, quote, PREVIOUS_READER_VERSION, "model_refused")
    _record_refusal(dsn, quote, READER_VERSION, "net_mismatch")
    items = [i for i in CrmWorkspaceRepository(psycopg.connect, dsn).quote_readings(limit=500)["items"]
             if i["quote_number"] == "00949-26"]
    assert [(i["reader_version"], i["code"]) for i in items] == [(READER_VERSION, "net_mismatch")]


def test_a_refusal_is_per_pdf_so_the_same_pdf_from_another_email_is_not_reread(dsn, mailbox_id, store) -> None:
    pdf = _pdf()
    first = _seed_quote(dsn, mailbox_id, pdf=pdf, quote_number="00984-26", store=store)
    reader = Reader(_extraction("00984-26", is_origenlab_issued_quote=False))
    _read_all(dsn, store, reader)
    calls = reader.calls
    _seed_quote(dsn, mailbox_id, pdf=pdf, quote_number="00985-26", store=store)  # same PDF, another email
    _read_all(dsn, store, reader)
    assert reader.calls == calls
    assert [code for _, code in _refusals(dsn, first["sha"])] == ["not_origenlab_quote"]


def test_a_permanent_api_error_on_one_pdf_is_recorded_and_the_next_pdf_is_still_read(dsn, mailbox_id, store) -> None:
    bad, good = _pdf(), _pdf()
    a = _seed_quote(dsn, mailbox_id, pdf=bad, quote_number="00981-26", store=store)
    b = _seed_quote(dsn, mailbox_id, pdf=good, quote_number="00982-26", store=store)
    counts = _read_all(dsn, store, FailingFor(bad, _ApiError(400), _extraction("00982-26")))
    assert counts.as_log()["refused"].get("request_failed") == 1
    assert [code for _, code in _refusals(dsn, a["sha"])] == ["request_failed"]
    assert len(_documents(dsn, b["sha"])) == 1


def test_a_transient_api_error_is_counted_and_retried_next_run_without_a_record(dsn, mailbox_id, store) -> None:
    pdf = _pdf()
    q = _seed_quote(dsn, mailbox_id, pdf=pdf, quote_number="00983-26", store=store)
    counts = _read_all(dsn, store, FailingFor(pdf, _ApiError(429), _extraction("00983-26")))
    assert counts.as_log()["refused"].get("error:_ApiError") == 1
    assert _refusals(dsn, q["sha"]) == [] and _documents(dsn, q["sha"]) == []
    _read_all(dsn, store, Reader(_extraction("00983-26")))
    assert len(_documents(dsn, q["sha"])) == 1


def test_a_pdf_missing_from_its_email_is_recorded_so_it_does_not_hold_the_queue(dsn, mailbox_id, store) -> None:
    before = set(store.objects)
    q = _seed_quote(dsn, mailbox_id, pdf=_pdf(), quote_number="00986-26", store=store)
    (key,) = set(store.objects) - before
    store.objects[key] = store.objects[key].replace(b"@origenlab.invalid>", b"@origenlab.invalie>")  # a different .eml
    reader = Reader(_extraction("00986-26"))
    _read_all(dsn, store, reader)
    assert [code for _, code in _refusals(dsn, q["sha"])] in (["hash_mismatch"], ["attachment_not_in_eml"])
    with _conn(dsn) as conn:
        assert q["sha"] not in {c["pdf_sha256"] for c in QuoteReader(conn, store, reader, MODEL).candidates(50)}


def test_with_the_model_off_nothing_is_written_or_recorded(dsn, mailbox_id, store) -> None:
    quote = _seed_quote(dsn, mailbox_id, pdf=_pdf(), quote_number="00995-26", store=store)
    counts = _read_all(dsn, store, None, "off")
    assert counts.as_log()["refused"].get("model_off", 0) >= 1
    assert _documents(dsn, quote["sha"]) == [] and _refusals(dsn, quote["sha"]) == []
    with _conn(dsn) as conn:  # still waiting for the model
        assert quote["sha"] in {c["pdf_sha256"] for c in QuoteReader(conn, store, None, "off").candidates(50)}
