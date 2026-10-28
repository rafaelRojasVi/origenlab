"""One message end to end with fakes: what is recorded, when the model runs, how failures behave."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from mailfixtures import make_raw
from origenlab_worker.catalog_match import ProductCandidate
from origenlab_worker.storage import StorageError
from origenlab_worker.triage_rules import TRIAGE_VERSION
from origenlab_worker.triage import (
    REQUESTER_VALUE_NORM,
    VALUE_NORM,
    ModelSettings,
    ModelUnavailable,
    PendingMessage,
    is_permanent_api_error,
    match_requester_organization,
    mentions_for,
    triage_one,
)
from origenlab_worker.triage_model import DEFAULT_MODEL

PATH = "mail/contacto@origenlab.cl/2026/10/g1.eml"
SENT = "2026-10-12T13:00:00+00:00"


class FakeDb:
    def __init__(self, *, direction="inbound", labels=("INBOX",), done=False, done_class="business_other",
                 delivery_done=False, requester_done=False, candidates=(), requester=None,
                 case_match_done=True, cards=()) -> None:
        self.msg = PendingMessage("sr-1", "m-1", direction, labels, PATH, "t-1", SENT)
        self.done = done
        self.done_class = done_class
        self.delivery_done = delivery_done
        self.requester_done = requester_done
        self._candidates = list(candidates)
        self._requester = requester or {"requester_version": 1, "status": "none", "how": "exact_name_in_body"}
        self.recorded: list[tuple[str, dict, list, dict | None]] = []
        self.requesters: list[tuple[str, dict]] = []
        self.delivery_failures = []
        self.case_match_done = case_match_done
        self.cards = list(cards)
        self.tender_threads: set[str] = set()
        self.case_matches: list[tuple[str, dict]] = []
        self.people = ("ana@cliente.invalid", {"ana@cliente.invalid": "Ana Pérez"}, ("lab@cliente.invalid",))
        self.people_recorded: list[tuple[str, dict]] = []

    def message_people(self, message_id):
        return self.people

    def record_people(self, sid, value):
        self.people_recorded.append((sid, value))

    def load(self, sid):
        if sid != "sr-1":
            return None
        return (self.msg, self.done, self.done_class if self.done else None, self.delivery_done,
                self.requester_done, self.case_match_done)

    def case_cards(self):
        return self.cards

    def tender_thread(self, thread_id):
        return thread_id in self.tender_threads

    def case_context(self, message):
        return ("contacto@origenlab.cl",), ("CN09153.pdf",) if message.thread_id == "t-9" else ()

    def known_quote_numbers(self):
        return []

    def past_decisions(self, sender):
        self.past_asked = sender
        return []

    def record_case_match(self, sid, value):
        self.case_matches.append((sid, value))

    def candidates(self, mail):
        return self._candidates

    def linked_cases(self, thread_id):
        from origenlab_worker.triage_model import LinkedCase

        self.thread_asked = thread_id
        return [LinkedCase("o-1", "Caso", "quoting")] if thread_id == "t-1" else []

    def requester_match(self, body):
        self.requester_body = body
        return self._requester

    def record_requester(self, sid, requester):
        self.requesters.append((sid, requester))

    def record(self, sid, reading, mentions, requester=None, case_match=None):
        self.recorded.append((sid, reading, list(mentions), requester))
        if case_match is not None:
            self.case_matches.append((sid, case_match))
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
    sid, value, mentions, requester = db.recorded[0]
    assert sid == "sr-1" and value["class"] == "quote_request" and value["reading"]["stage"] == "lead"
    assert requester["status"] == "none"
    assert mentions == [("UP200HT", {"triage_version": TRIAGE_VERSION, "source": "model", "description": "Homogeneizador",
                                     "brand": None, "model": "UP200Ht", "quantity": 1, "catalog_product_id": "p-1"})]
    # The reading, then the case pick (no case is sure); this reader's answer is not a pick, so it is refused.
    assert len(reader.requests) == 2 and db.case_matches[0][1]["pick_state"] == "closed_value_unknown"


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
    _, value, mentions, requester = db.recorded[0]
    assert "reading" not in value and value["candidates"][0]["product_id"] == "p-1"
    assert requester["status"] == "none"
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


def test_requester_match_is_exact_unique_and_ignores_short_generic_names() -> None:
    organizations = [
        ("o-pucv", "Pontificia Universidad Católica de Valparaíso"),
        ("o-short", "PUCV"),
        ("o-other", "Universidad Ejemplo"),
    ]
    matched = match_requester_organization(
        "Saludos\nLaboratorio de Ecofisiología\nPontificia Universidad Católica de Valparaíso",
        organizations,
    )
    assert matched == {
        "requester_version": 1,
        "status": "matched",
        "organization_id": "o-pucv",
        "organization_name": "Pontificia Universidad Católica de Valparaíso",
        "how": "exact_name_in_body",
    }
    assert match_requester_organization("Escribo desde otra institución.", organizations)["status"] == "none"
    ambiguous = match_requester_organization(
        "Pontificia Universidad Católica de Valparaíso y Universidad Ejemplo", organizations
    )
    assert ambiguous["status"] == "ambiguous"


def test_a_previously_triaged_quote_request_backfills_requester_without_model() -> None:
    requester = {
        "requester_version": 1, "status": "matched", "organization_id": "o-pucv",
        "organization_name": "Pontificia Universidad Católica de Valparaíso", "how": "exact_name_in_body",
    }
    db = FakeDb(done=True, done_class="quote_request", requester_done=False, requester=requester)
    reader = Reader(READING)
    result = triage_one(db, FakeStore(make_raw()), "sr-1", ModelSettings(reader, DEFAULT_MODEL))
    assert (result.outcome, result.triage_class, result.model) == ("recorded", "quote_request", "not_needed")
    assert db.requesters == [("sr-1", requester)]
    assert db.recorded == [] and reader.requests == []


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
    assert VALUE_NORM == "triage:v4"
    assert REQUESTER_VALUE_NORM == "requester:v1"


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


# -- forwarded requests (spec 2026-10-14): read after the triage, never in its way

from email.message import EmailMessage as _Em  # noqa: E402


def _forward_raw(sender: str = "Tatiana <contacto@origenlab.cl>", subject: str = "Fwd: Solicitud cotización") -> bytes:
    m = _Em()
    m["From"], m["To"], m["Subject"] = sender, "contacto@origenlab.cl", subject
    m.set_content("---------- Forwarded message ---------\nDe: Pedro Ejemplo <pedro@universidad-ficticia.cl>\n"
                  "Date: lun, 5 oct 2026\nSubject: Solicitud cotización Gradillas\nTo: <contacto@origenlab.cl>\n\n"
                  "Necesitamos 20 gradillas.\n")
    return m.as_bytes()


class ForwardDb(FakeDb):
    def __init__(self, **kw) -> None:
        super().__init__(direction="outbound", labels=("SENT",), **kw)
        self.forwards: list[tuple[str, dict]] = []

    def record_forward(self, sid, value):
        self.forwards.append((sid, value))


def test_a_forward_we_sent_records_who_really_asked_after_the_triage(monkeypatch) -> None:
    monkeypatch.delenv("ORIGENLAB_WORKER_FORWARD_READING", raising=False)
    db = ForwardDb()
    result = triage_one(db, FakeStore(_forward_raw()), "sr-1", ModelSettings(None, DEFAULT_MODEL))
    assert result.outcome == "recorded" and len(db.recorded) == 1
    [(sid, value)] = db.forwards
    assert sid == "sr-1" and value["original_email"] == "pedro@universidad-ficticia.cl"


def test_the_switch_turns_forward_reading_off(monkeypatch) -> None:
    monkeypatch.setenv("ORIGENLAB_WORKER_FORWARD_READING", "off")
    db = ForwardDb()
    triage_one(db, FakeStore(_forward_raw()), "sr-1", ModelSettings(None, DEFAULT_MODEL))
    assert db.forwards == [] and len(db.recorded) == 1


def test_a_reader_that_breaks_never_breaks_the_triage(monkeypatch) -> None:
    import origenlab_worker.triage as triage_module

    monkeypatch.delenv("ORIGENLAB_WORKER_FORWARD_READING", raising=False)
    monkeypatch.setattr(triage_module, "read_forward", lambda raw: (_ for _ in ()).throw(RuntimeError("boom")))
    db = ForwardDb()
    result = triage_one(db, FakeStore(_forward_raw()), "sr-1", ModelSettings(None, DEFAULT_MODEL))
    assert result.outcome == "recorded" and len(db.recorded) == 1 and db.forwards == []


def test_a_failed_forward_write_never_breaks_the_triage(monkeypatch) -> None:
    monkeypatch.delenv("ORIGENLAB_WORKER_FORWARD_READING", raising=False)

    class Broken(ForwardDb):
        def record_forward(self, sid, value):
            raise RuntimeError("db down")

    db = Broken()
    result = triage_one(db, FakeStore(_forward_raw()), "sr-1", ModelSettings(None, DEFAULT_MODEL))
    assert result.outcome == "recorded" and len(db.recorded) == 1


def test_a_customer_email_is_never_read_as_a_forward(monkeypatch) -> None:
    monkeypatch.delenv("ORIGENLAB_WORKER_FORWARD_READING", raising=False)
    db = ForwardDb()
    triage_one(db, FakeStore(_forward_raw(sender="cliente@universidad-ficticia.cl")), "sr-1",
               ModelSettings(None, DEFAULT_MODEL))
    assert db.forwards == []


def test_the_forward_backfill_reads_only_and_writes_only_on_apply(monkeypatch) -> None:
    from origenlab_worker.triage import backfill_forwards

    monkeypatch.delenv("ORIGENLAB_WORKER_FORWARD_READING", raising=False)
    db = ForwardDb()
    dry = backfill_forwards(db, FakeStore(_forward_raw()), ["sr-1", "sr-missing"], apply=False)
    assert dry == {"found": 2, "readable": 1, "recorded": 0, "skipped": 1}
    assert db.forwards == [] and db.recorded == []          # never the triage itself
    applied = backfill_forwards(db, FakeStore(_forward_raw()), ["sr-1"], apply=True)
    assert applied == {"found": 1, "readable": 1, "recorded": 1, "skipped": 0}
    assert db.recorded == [] and db.forwards[0][1]["original_email"] == "pedro@universidad-ficticia.cl"

def _card(oid="o-1", quote="09153-26"):
    from origenlab_worker.case_match import CaseCard

    return CaseCard(oid, "Caso", "won", "2026-10-01T00:00:00+00:00", None, (), (quote,), (), (), ())


def test_human_mail_records_the_case_match_with_the_triage() -> None:
    db = FakeDb(cards=[_card()])
    raw = make_raw(subject="Re: despacho puntas", pdf_name="Cotización 09153A.pdf")
    result = triage_one(db, FakeStore(raw), "sr-1", ModelSettings(None, DEFAULT_MODEL))
    assert result.case_match == "exact"
    [(sid, value)] = db.case_matches
    assert sid == "sr-1" and value["decision"] == "exact" and value["candidates"][0]["opportunity_id"] == "o-1"
    assert value["pick_state"] == "off"


def test_a_thread_a_tender_holds_gets_no_sales_case_suggestion() -> None:
    """The same email that matches `o-1` exactly, on a thread a tender holds: no candidate, no new case,
    no model pick (`origenlab_api.v2.case_kind`)."""
    db, reader = FakeDb(cards=[_card()]), PickReader({"choice": "o-1", "confidence": "high", "reason_es": "x",
                                                      "new_case": None})
    db.tender_threads = {db.msg.thread_id}
    raw = make_raw(subject="Re: despacho puntas", pdf_name="Cotización 09153A.pdf")
    result = triage_one(db, FakeStore(raw), "sr-1", ModelSettings(reader, DEFAULT_MODEL))
    [(_, value)] = db.case_matches
    assert (result.case_match, value["decision"], value["candidates"], value["new_case_quote"]) == (
        "none", "none", [], None)
    assert (value["held_by"], value["pick_state"], reader.picks) == ("tender", "not_needed", [])


def test_machine_mail_gets_no_case_match() -> None:
    db = FakeDb(cards=[_card()])
    triage_one(db, FakeStore(make_raw(subject="Newsletter", list_unsubscribe=True)), "sr-1",
               ModelSettings(None, DEFAULT_MODEL))
    assert db.case_matches == []


def test_the_threads_earlier_quote_pdf_is_read() -> None:
    db = FakeDb(cards=[_card()])
    db.msg = PendingMessage("sr-1", "m-1", "inbound", ("INBOX",), PATH, "t-9", SENT)
    triage_one(db, FakeStore(make_raw(subject="Re: Cotización")), "sr-1", ModelSettings(None, DEFAULT_MODEL))
    assert db.case_matches[0][1]["candidates"][0]["signals"] == ["thread_pdf_quote"]


def test_previously_triaged_human_mail_backfills_the_case_match_without_the_model() -> None:
    db, reader = FakeDb(done=True, done_class="quote_followup", case_match_done=False, cards=[_card()]), Reader(READING)
    raw = make_raw(subject="Re: despacho", pdf_name="CN09153.pdf")
    result = triage_one(db, FakeStore(raw), "sr-1", ModelSettings(reader, DEFAULT_MODEL))
    assert (result.outcome, result.model, result.case_match) == ("recorded", "not_needed", "exact")
    assert db.recorded == [] and reader.requests == [] and len(db.case_matches) == 1


class PickReader:
    """Answers the triage reading and the case pick, told apart by their system prompt."""

    def __init__(self, pick, exc=None) -> None:
        self.pick, self.exc, self.picks, self.readings = pick, exc, [], []

    def __call__(self, request):
        from origenlab_worker.case_match_model import PICK_SYSTEM_PROMPT

        if request["system"][0]["text"] == PICK_SYSTEM_PROMPT:
            self.picks.append(request)
            if self.exc:
                raise self.exc
            payload = self.pick
        else:
            self.readings.append(request)
            payload = READING
        return SimpleNamespace(stop_reason="end_turn", usage=None,
                               content=[SimpleNamespace(type="text", text=json.dumps(payload))])


def test_exact_layer1_skips_model() -> None:
    db, reader = FakeDb(cards=[_card()]), PickReader({"choice": "o-1", "confidence": "high", "reason_es": "x",
                                                      "new_case": None})
    triage_one(db, FakeStore(make_raw(subject="Re: despacho", pdf_name="CN09153.pdf")), "sr-1",
               ModelSettings(reader, DEFAULT_MODEL))
    assert reader.picks == [] and db.case_matches[0][1]["pick_state"] == "not_needed"


def test_an_unsure_match_asks_the_model_and_keeps_its_pick() -> None:
    db = FakeDb(cards=[_card(), _card("o-2", "09161-26")])
    reader = PickReader({"choice": "o-2", "confidence": "medium", "reason_es": "Mismo producto.", "new_case": None})
    result = triage_one(db, FakeStore(make_raw(subject="Re: puntas")), "sr-1", ModelSettings(reader, DEFAULT_MODEL))
    value = db.case_matches[0][1]
    assert len(reader.picks) == 1 and db.past_asked == "ana@cliente.invalid"
    assert value["pick_state"] == "ran" and value["pick"]["choice"] == "o-2" and result.case_match == "none"


def test_a_pick_the_model_was_not_offered_is_dropped_and_the_triage_still_records() -> None:
    db = FakeDb(cards=[_card()])
    reader = PickReader({"choice": "o-9", "confidence": "high", "reason_es": "x", "new_case": None})
    result = triage_one(db, FakeStore(make_raw(subject="Re: puntas")), "sr-1", ModelSettings(reader, DEFAULT_MODEL))
    assert result.outcome == "recorded"
    value = db.case_matches[0][1]
    assert value["pick_state"] == "choice_not_offered" and "pick" not in value


def test_a_failed_pick_call_never_blocks_the_triage() -> None:
    db = FakeDb(cards=[_card()])
    reader = PickReader(None, exc=RuntimeError("network"))
    result = triage_one(db, FakeStore(make_raw(subject="Re: puntas")), "sr-1", ModelSettings(reader, DEFAULT_MODEL))
    assert result.outcome == "recorded" and db.case_matches[0][1]["pick_state"] == "unavailable"


def test_a_database_failure_in_the_matcher_never_blocks_the_triage() -> None:
    class Broken(FakeDb):
        def case_cards(self):
            raise RuntimeError("statement timeout")

    db = Broken(cards=[_card()])
    result = triage_one(db, FakeStore(make_raw(subject="Re: puntas")), "sr-1", ModelSettings(None, DEFAULT_MODEL))
    assert result.outcome == "recorded" and len(db.recorded) == 1 and db.case_matches == []
    assert result.case_match == "unavailable"


PEOPLE_READING = {**READING, "sender": {"name": "Ana Pérez", "title": "Jefa de laboratorio",
                                        "institution": "Universidad Ficticia", "phone": None}}


def test_who_wrote_the_email_is_recorded_as_people_v1_with_the_model_signature() -> None:
    from origenlab_worker.people_reading import PEOPLE_VALUE_NORM

    db = FakeDb(candidates=[CAND])
    triage_one(db, FakeStore(make_raw()), "sr-1", ModelSettings(Reader(PEOPLE_READING), DEFAULT_MODEL))
    [(sid, value)] = db.people_recorded
    assert sid == "sr-1" and PEOPLE_VALUE_NORM == "people:v1"
    assert value["sender_address"] == "ana@cliente.invalid" and value["header_name"] == "Ana Pérez"
    assert value["signature"] == PEOPLE_READING["sender"]
    assert value["co_addresses"] == ["lab@cliente.invalid"]
    assert value["signature_raw"] == ""  # no delimiter: the tail went to the model but is never stored


def test_with_the_model_off_the_header_name_is_still_read() -> None:
    db = FakeDb()
    triage_one(db, FakeStore(make_raw()), "sr-1", ModelSettings(None, DEFAULT_MODEL))
    [(_, value)] = db.people_recorded
    assert value["signature"] is None and value["header_name"] == "Ana Pérez"


def test_a_delimited_signature_is_stored_raw() -> None:
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = "Ana Pérez <ana@cliente.invalid>", "contacto@origenlab.cl", "Consulta"
    msg.set_content("Hola, ¿tienen stock?\n\n-- \nAna Pérez\nUniversidad Ficticia\n")
    db = FakeDb()
    triage_one(db, FakeStore(msg.as_bytes()), "sr-1", ModelSettings(None, DEFAULT_MODEL))
    assert db.people_recorded[0][1]["signature_raw"] == "Ana Pérez\nUniversidad Ficticia"


def test_machine_mail_gets_no_people_reading() -> None:
    db = FakeDb()
    triage_one(db, FakeStore(make_raw(subject="Respuesta automática: Solicitud de cotización")), "sr-1",
               ModelSettings(None, DEFAULT_MODEL))
    assert db.recorded and db.people_recorded == []
    bounce = (
        b"From: Mail Delivery Subsystem <mailer-daemon@googlemail.invalid>\r\n"
        b"To: contacto@origenlab.cl\r\n"
        b"Subject: Delivery Status Notification (Failure)\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
        b"Final-Recipient: rfc822; dead@cliente.invalid\r\n"
        b"Status: 5.1.1\r\n550 5.1.1 User unknown; address does not exist.\r\n"
    )
    db = FakeDb()
    db.people = ("mailer-daemon@googlemail.invalid", {"mailer-daemon@googlemail.invalid": "Mail Delivery Subsystem"}, ())
    result = triage_one(db, FakeStore(bounce), "sr-1", ModelSettings(None, DEFAULT_MODEL))
    assert result.triage_class == "bounce" and db.people_recorded == []


def test_outbound_and_forwarded_mail_get_no_people_reading() -> None:
    db = FakeDb(direction="outbound")
    triage_one(db, FakeStore(make_raw()), "sr-1", ModelSettings(None, DEFAULT_MODEL))
    assert db.people_recorded == []
    db = FakeDb()
    triage_one(db, FakeStore(make_raw(subject="RV: Solicitud de cotización")), "sr-1", ModelSettings(None, DEFAULT_MODEL))
    assert db.people_recorded == []


def test_a_failing_people_write_never_breaks_the_triage() -> None:
    class Broken(FakeDb):
        def record_people(self, sid, value):
            raise RuntimeError("boom")

    db = Broken()
    assert triage_one(db, FakeStore(make_raw()), "sr-1", ModelSettings(None, DEFAULT_MODEL)).outcome == "recorded"
    assert db.recorded
