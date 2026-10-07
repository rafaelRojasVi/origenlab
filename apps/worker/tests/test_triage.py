"""One message end to end with fakes: what is recorded, when the model runs, how failures behave."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from mailfixtures import make_raw
from origenlab_worker.catalog_match import ProductCandidate
from origenlab_worker.storage import StorageError
from origenlab_worker.triage import (
    VALUE_NORM,
    ModelSettings,
    ModelUnavailable,
    PendingMessage,
    is_permanent_api_error,
    mentions_for,
    triage_one,
)
from origenlab_worker.triage_model import DEFAULT_MODEL

PATH = "mail/contacto@origenlab.cl/2026/10/g1.eml"


class FakeDb:
    def __init__(self, *, direction="inbound", labels=("INBOX",), done=False, done_class="business_other",
                 delivery_done=False, candidates=()) -> None:
        self.msg = PendingMessage("sr-1", "m-1", direction, labels, PATH, "t-1")
        self.done = done
        self.done_class = done_class
        self.delivery_done = delivery_done
        self._candidates = list(candidates)
        self.recorded: list[tuple[str, dict, list]] = []
        self.delivery_failures = []

    def load(self, sid):
        if sid != "sr-1":
            return None
        return self.msg, self.done, self.done_class if self.done else None, self.delivery_done

    def candidates(self, mail):
        return self._candidates

    def linked_cases(self, thread_id):
        from origenlab_worker.triage_model import LinkedCase

        self.thread_asked = thread_id
        return [LinkedCase("o-1", "Caso", "quoting")] if thread_id == "t-1" else []

    def record(self, sid, reading, mentions):
        self.recorded.append((sid, reading, list(mentions)))
        return len(mentions)

    def record_delivery_failure(self, sid, analysis):
        self.delivery_failures.append((sid, analysis))
        return len(analysis.auto_block_addresses)


class FakeStore:
    def __init__(self, raw: bytes | None) -> None:
        self.raw, self.keys = raw, []

    def get(self, key):
        self.keys.append(key)
        if self.raw is None:
            raise StorageError("storage_get_http_404")
        return self.raw


class Reader:
    def __init__(self, payload=None, exc=None) -> None:
        self.payload, self.exc, self.requests = payload, exc, []

    def __call__(self, request):
        self.requests.append(request)
        if self.exc:
            raise self.exc
        return SimpleNamespace(stop_reason="end_turn", usage=None,
                               content=[SimpleNamespace(type="text", text=json.dumps(self.payload))])


READING = {"intent": "quote_request", "stage": "lead", "urgency": "high",
           "products": [{"description": "Homogeneizador", "brand": None, "model": "UP200Ht", "quantity": 1,
                         "catalog_product_id": "p-1"}],
           "requester_organization": "Universidad Ejemplo", "summary_es": "Pide cotizar.", "needs_reply": True}
CAND = ProductCandidate("p-1", "UP200Ht", "UP200HT", "Homogeneizador", "model_key", "UP200HT")


def test_a_quote_request_is_read_by_the_model_and_recorded_with_its_products() -> None:
    db, reader = FakeDb(candidates=[CAND]), Reader(READING)
    result = triage_one(db, FakeStore(make_raw()), "sr-1", ModelSettings(reader, DEFAULT_MODEL))
    assert (result.outcome, result.triage_class, result.model, result.mentions) == \
        ("recorded", "quote_request", "ran", 1)
    sid, value, mentions = db.recorded[0]
    assert sid == "sr-1" and value["class"] == "quote_request" and value["reading"]["stage"] == "lead"
    assert mentions == [("UP200HT", {"triage_version": 1, "source": "model", "description": "Homogeneizador",
                                     "brand": None, "model": "UP200Ht", "quantity": 1, "catalog_product_id": "p-1"})]
    assert len(reader.requests) == 1


def test_the_eml_key_is_the_storage_path_without_the_bucket() -> None:
    store = FakeStore(make_raw())
    triage_one(FakeDb(), store, "sr-1", ModelSettings(None, DEFAULT_MODEL))
    assert store.keys == ["contacto@origenlab.cl/2026/10/g1.eml"]


def test_noise_never_reaches_the_model() -> None:
    reader = Reader(READING)
    raw = make_raw(subject="Respuesta automática: Solicitud de cotización")
    result = triage_one(FakeDb(), FakeStore(raw), "sr-1", ModelSettings(reader, DEFAULT_MODEL))
    assert (result.triage_class, result.model) == ("auto_reply", "not_needed") and reader.requests == []


def test_with_the_model_off_the_rules_and_exact_catalog_matches_are_still_recorded() -> None:
    db = FakeDb(candidates=[CAND])
    result = triage_one(db, FakeStore(make_raw()), "sr-1", ModelSettings(None, DEFAULT_MODEL))
    assert result.model == "off"
    _, value, mentions = db.recorded[0]
    assert "reading" not in value and value["candidates"][0]["product_id"] == "p-1"
    assert [m[0] for m in mentions] == ["UP200HT"] and mentions[0][1]["source"] == "catalog_model_key"


def test_an_already_triaged_or_missing_message_writes_nothing() -> None:
    db = FakeDb(done=True)
    assert triage_one(db, FakeStore(make_raw()), "sr-1", ModelSettings(None, DEFAULT_MODEL)).outcome == "already_triaged"
    assert triage_one(db, FakeStore(make_raw()), "other", ModelSettings(None, DEFAULT_MODEL)).outcome == "not_found"
    assert db.recorded == []


def test_a_previously_triaged_bounce_gets_delivery_analysis_without_retriaging_other_mail() -> None:
    raw = (
        b"From: Mail Delivery Subsystem <mailer-daemon@googlemail.invalid>\r\n"
        b"To: contacto@origenlab.cl\r\n"
        b"Subject: Delivery Status Notification (Failure)\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
        b"Final-Recipient: rfc822; dead@cliente.invalid\r\n"
        b"Status: 5.1.1\r\n550 5.1.1 User unknown; address does not exist.\r\n"
    )
    db = FakeDb(done=True, done_class="bounce", delivery_done=False)
    result = triage_one(db, FakeStore(raw), "sr-1", ModelSettings(None, DEFAULT_MODEL))
    assert (result.outcome, result.triage_class, result.model) == ("recorded", "bounce", "not_needed")
    assert db.recorded == []
    assert db.delivery_failures[0][1].auto_block_addresses == ("dead@cliente.invalid",)


def test_a_storage_failure_writes_nothing_so_the_sweep_retries() -> None:
    db = FakeDb()
    assert triage_one(db, FakeStore(None), "sr-1", ModelSettings(None, DEFAULT_MODEL)).outcome == "storage_error"
    assert db.recorded == []


class _ApiError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__("secret text from the API that must not be logged")
        self.status_code = status


def test_a_transient_model_failure_raises_for_the_queue_to_retry_and_records_nothing() -> None:
    db = FakeDb()
    with pytest.raises(ModelUnavailable) as err:
        triage_one(db, FakeStore(make_raw()), "sr-1", ModelSettings(Reader(exc=_ApiError(529)), DEFAULT_MODEL))
    assert str(err.value) == "_ApiError" and db.recorded == []


def test_a_permanent_model_failure_is_recorded_so_it_is_not_retried_forever() -> None:
    db = FakeDb()
    result = triage_one(db, FakeStore(make_raw()), "sr-1", ModelSettings(Reader(exc=_ApiError(400)), DEFAULT_MODEL))
    assert result.model == "api_error_400" and db.recorded[0][1]["model_state"] == "api_error_400"


@pytest.mark.parametrize("status,permanent", [(400, True), (401, True), (404, True), (408, False), (409, False),
                                              (429, False), (500, False), (529, False), (None, False)])
def test_which_api_errors_are_permanent(status, permanent) -> None:
    exc = _ApiError(status) if status else RuntimeError()
    assert is_permanent_api_error(exc) is permanent


def test_mentions_are_deduplicated_and_fall_back_to_the_folded_description() -> None:
    from origenlab_worker.triage_model import ModelProduct, ModelReading

    reading = ModelReading(model="m", intent="quote_request", stage="lead", urgency="normal",
                           products=(ModelProduct("Baño termorregulado", None, None, 1, None),
                                     ModelProduct("Homogeneizador", None, "UP-200Ht", 1, None),
                                     ModelProduct("Homogeneizador", None, "UP200HT", 2, None)),
                           requester_organization=None, summary_es="x", needs_reply=True)
    assert [n for n, _ in mentions_for(reading, [])] == ["bano termorregulado", "UP200HT"]


def test_the_reading_is_stored_under_the_versioned_value_norm() -> None:
    assert VALUE_NORM == "triage:v1"


def test_the_threads_cases_are_read_sent_to_the_model_and_stored_with_the_legality_of_the_move() -> None:
    db, reader = FakeDb(candidates=[CAND]), Reader({**READING, "stage": "negotiating"})
    triage_one(db, FakeStore(make_raw()), "sr-1", ModelSettings(reader, DEFAULT_MODEL))
    assert db.thread_asked == "t-1"
    assert '"stage": "quoting"' in reader.requests[0]["messages"][0]["content"]
    value = db.recorded[0][1]
    assert value["linked_cases"] == [{"opportunity_id": "o-1", "stage": "quoting"}]
    assert value["reading"]["linked_cases"] == [{"opportunity_id": "o-1", "current_stage": "quoting",
                                                 "transition_allowed": True}]


def test_noise_does_not_look_up_cases() -> None:
    db = FakeDb()
    triage_one(db, FakeStore(make_raw(subject="Respuesta automática: hola")), "sr-1", ModelSettings(None, DEFAULT_MODEL))
    assert not hasattr(db, "thread_asked")
