"""`people-backfill`: `people:v1` for mail captured before the reading existed — one people-only model
call per inbound email a person wrote. Fakes first, then the pending query on a real database.

Every name, address and institution is fictitious (`.invalid`, «Universidad Ficticia»).
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest

from mailfixtures import make_raw
from origenlab_worker.people_reading import PEOPLE_VALUE_NORM
from origenlab_worker.triage import (
    ModelUnavailable,
    PendingMessage,
    backfill_people,
    record_people_reading,
)
from origenlab_worker.triage_model import (
    OUTPUT_SCHEMA,
    SYSTEM_PROMPT,
    ModelSender,
    build_people_request,
    parse_people,
)
from origenlab_worker.mail_text import parse_mail
from v2_command_harness import needs_worker_db

PATH = "mail/contacto@origenlab.cl/2026/10/{sid}.eml"
SENDER = {"name": "Ana Pérez Soto", "title": "Jefa de laboratorio", "institution": "Universidad Ficticia",
          "phone": None}


class FakeStore:
    def __init__(self, raws: dict[str, bytes]) -> None:
        self.raws = raws

    def get(self, key: str) -> bytes:
        return self.raws[key]


class FakeDb:
    """`load`, `message_people` and `record_people` of `TriageDb`, for messages `sr-*`."""

    def __init__(self, messages: dict[str, tuple[str, str | None]]) -> None:
        # sid -> (direction, triage class or None when the email has no triage reading)
        self.messages = messages
        self.people = ("ana@cliente.invalid", {"ana@cliente.invalid": "Ana Pérez"}, ("lab@cliente.invalid",))
        self.recorded: list[tuple[str, dict]] = []

    def load(self, sid):
        if sid not in self.messages:
            return None
        direction, triage_class = self.messages[sid]
        msg = PendingMessage(sid, f"m-{sid}", direction, ("INBOX",), PATH.format(sid=sid), f"t-{sid}", None)
        return msg, triage_class is not None, triage_class, False, False, True

    def message_people(self, message_id):
        return self.people

    def record_people(self, sid, value):
        self.recorded.append((sid, value))


class Reader:
    def __init__(self, sender=SENDER, *, error: Exception | None = None) -> None:
        self.sender = sender
        self.error = error
        self.requests: list[dict] = []

    def __call__(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=1200, output_tokens=80),
                               content=[SimpleNamespace(type="text", text=json.dumps({"sender": self.sender}))])


def _world(classes: dict[str, str | None], *, subject: str = "Solicitud de cotización", direction="inbound"):
    raws = {f"contacto@origenlab.cl/2026/10/{sid}.eml": make_raw(subject=subject) for sid in classes}
    return FakeDb({sid: (direction, c) for sid, c in classes.items()}), FakeStore(raws)


# --- the request ----------------------------------------------------------------------------------

def test_the_people_request_asks_only_who_wrote_it_with_the_triages_own_sender_bullet() -> None:
    mail = parse_mail(make_raw())
    request = build_people_request(mail, model="claude-opus-5-5")
    schema = request["output_config"]["format"]["schema"]
    assert schema["required"] == ["sender"] and set(schema["properties"]) == {"sender"}
    assert schema["properties"]["sender"] == OUTPUT_SCHEMA["properties"]["sender"]
    bullet = SYSTEM_PROMPT[SYSTEM_PROMPT.index("- sender:"):]
    assert bullet in request["system"][0]["text"]
    assert "<email>" in request["messages"][0]["content"]


def test_parse_people_reads_the_sender_and_the_tokens() -> None:
    sender, tokens = parse_people(Reader()(build_people_request(parse_mail(make_raw()))))
    assert sender == ModelSender("Ana Pérez Soto", "Jefa de laboratorio", "Universidad Ficticia", None)
    assert tokens == (1200, 80)


# --- the backfill ---------------------------------------------------------------------------------

def test_backfill_dry_run_writes_nothing() -> None:
    db, store = _world({"sr-1": "quote_request", "sr-2": "business_other"})
    reader = Reader()
    counts = backfill_people(db, store, reader, ["sr-1", "sr-2"], apply=False, sample=None)
    assert db.recorded == [] and reader.requests == []  # a plain dry run spends nothing
    assert counts == {"found": 2, "read": 2, "model_calls": 0, "written": 0, "skipped": 0,
                      "input_tokens": 0, "output_tokens": 0}


def test_backfill_writes_exactly_what_the_triage_would_have_written() -> None:
    db, store = _world({"sr-1": "quote_request"})
    counts = backfill_people(db, store, Reader(), ["sr-1"], apply=True, sample=None)
    assert counts["written"] == 1 and counts["model_calls"] == 1
    [(sid, value)] = db.recorded
    # The triage's own writer, given the same email and the same model sender, writes the same value.
    twin = FakeDb({"sr-1": ("inbound", "quote_request")})
    msg = twin.load("sr-1")[0]
    mail = parse_mail(store.get("contacto@origenlab.cl/2026/10/sr-1.eml"))
    reading = SimpleNamespace(sender=ModelSender(**SENDER))
    assert record_people_reading(twin, msg, mail, reading, "quote_request")
    assert (sid, value) == twin.recorded[0]
    assert value["signature"] == SENDER and value["header_name"] == "Ana Pérez"


def test_backfill_skips_messages_with_reading_machine_mail_outbound_and_forwards() -> None:
    db, store = _world({"sr-1": "bounce", "sr-2": None, "sr-3": "bulk"})
    reader = Reader()
    counts = backfill_people(db, store, reader, ["sr-1", "sr-2", "sr-3", "sr-missing"], apply=True, sample=None)
    assert counts["skipped"] == 4 and counts["written"] == 0 and reader.requests == []

    out, out_store = _world({"sr-1": "quote_request"}, direction="outbound")
    assert backfill_people(out, out_store, reader, ["sr-1"], apply=True, sample=None)["skipped"] == 1

    fwd, fwd_store = _world({"sr-1": "quote_request"}, subject="RV: Solicitud de cotización")
    assert backfill_people(fwd, fwd_store, reader, ["sr-1"], apply=True, sample=None)["skipped"] == 1
    assert reader.requests == [] and out.recorded == [] and fwd.recorded == []


def test_backfill_never_asks_the_model_about_our_own_sender() -> None:
    db, store = _world({"sr-1": "business_other"})
    db.people = ("ana@origenlab.cl", {"ana@origenlab.cl": "Ana"}, ())
    reader = Reader()
    assert backfill_people(db, store, reader, ["sr-1"], apply=True, sample=None)["skipped"] == 1
    assert reader.requests == []


def test_backfill_sample_reports_tokens() -> None:
    db, store = _world({f"sr-{i}": "quote_request" for i in range(5)})
    reader = Reader()
    counts = backfill_people(db, store, reader, [f"sr-{i}" for i in range(5)], apply=False, sample=3)
    assert len(reader.requests) == 3 and db.recorded == []  # a sample without --apply spends, never writes
    assert counts == {"found": 5, "read": 3, "model_calls": 3, "written": 0, "skipped": 0,
                      "input_tokens": 3600, "output_tokens": 240}


def test_a_refused_or_permanently_failing_answer_is_written_without_a_signature_like_the_triage() -> None:
    db, store = _world({"sr-1": "quote_request"})
    error = type("BadRequestError", (Exception,), {})()
    error.status_code = 400
    counts = backfill_people(db, store, Reader(error=error), ["sr-1"], apply=True, sample=None)
    assert counts["written"] == 1 and db.recorded[0][1]["signature"] is None

    refused, refused_store = _world({"sr-1": "quote_request"})
    counts = backfill_people(refused, refused_store, Reader(sender="nobody"), ["sr-1"], apply=True, sample=None)
    assert counts["written"] == 1 and refused.recorded[0][1]["signature"] is None


def test_a_passing_model_outage_stops_the_run_so_it_resumes_later() -> None:
    db, store = _world({"sr-1": "quote_request"})
    with pytest.raises(ModelUnavailable):
        backfill_people(db, store, Reader(error=TimeoutError()), ["sr-1"], apply=True, sample=None)
    assert db.recorded == []


def test_without_a_model_the_backfill_writes_the_header_name_alone() -> None:
    db, store = _world({"sr-1": "quote_request"})
    assert backfill_people(db, store, None, ["sr-1"], apply=True, sample=None)["written"] == 1
    assert db.recorded[0][1]["signature"] is None and db.recorded[0][1]["header_name"] == "Ana Pérez"


# --- the pending query, on a real database --------------------------------------------------------

@pytest.fixture(scope="module")
def dsn():
    from v2_command_harness import build_disposable_database

    yield from build_disposable_database()


@needs_worker_db
def test_backfill_skips_messages_with_reading_on_a_real_database(dsn) -> None:
    from dbhelp import owner_rows, seed_mailbox
    from mailfixtures import INTERNAL_MS, MAILBOX
    from origenlab_worker.capture import build_capture
    from origenlab_worker.database import (
        TRIAGE_REQUIRED_POLICIES, TRIAGE_REQUIRED_PRIVILEGES, local_test_target, open_verified_connection,
        open_worker_db,
    )
    from origenlab_worker.triage import ModelSettings, TriageDb, triage_one
    from origenlab_worker.triage_model import DEFAULT_MODEL
    from v2_command_harness import worker_dsn

    target = local_test_target(worker_dsn(dsn))
    mailbox_id = seed_mailbox(dsn)
    raws: dict[str, bytes] = {}

    def capture(raw: bytes) -> str:
        gid = f"pb-{uuid.uuid4().hex[:8]}"
        c = build_capture(raw, gmail_message_id=gid, gmail_thread_id=f"t-{gid}", label_ids=("INBOX",),
                          label_names={}, internal_date_ms=INTERNAL_MS, mailbox_address=MAILBOX)
        key = f"{MAILBOX}/2026/10/{gid}.eml"
        raws[key] = raw
        with open_worker_db(target) as wdb:
            wdb.record(c, mailbox_id=mailbox_id, eml_path=f"mail/{key}", eml_sha256=c.payload["raw_sha256"],
                       size_bytes=len(raw))
        return owner_rows(dsn, "select id::text from evidence.source_record where dedupe_key = %s",
                          (f"gmail_message:{gid}",))[0][0]

    def mid() -> str:
        return f"<{uuid.uuid4().hex}@cliente.invalid>"

    named = capture(make_raw(frm="Ana Pérez <ana@cliente.invalid>", message_id=mid()))
    bare = capture(make_raw(frm="pedro@cliente.invalid", message_id=mid()))
    untriaged = capture(make_raw(frm="luis@cliente.invalid", message_id=mid()))
    store = FakeStore(raws)
    with open_verified_connection(target, application_name="test-people-backfill",
                                  privileges=TRIAGE_REQUIRED_PRIVILEGES,
                                  policies=TRIAGE_REQUIRED_POLICIES) as conn:
        db = TriageDb(conn)
        for sid in (named, bare):  # triaged with the model off: `named` gets people:v1, `bare` nothing
            triage_one(db, store, sid, ModelSettings(None, DEFAULT_MODEL))
        pending = db.pending_people(since_days=36500, limit=500)
        assert bare in pending and named not in pending and untriaged not in pending
        reader = Reader()
        assert backfill_people(db, store, reader, pending, apply=False, sample=1)["written"] == 0
        assert db.pending_people(since_days=36500, limit=500) == pending
        counts = backfill_people(db, store, reader, [bare], apply=True, sample=None)
        assert counts["written"] == 1
        assert bare not in db.pending_people(since_days=36500, limit=500)  # resumable: a second run skips it
    [(value,)] = owner_rows(dsn, "select value from evidence.assertion where source_record_id = %s "
                                 "and kind = 'message_triage' and value_norm = %s", (bare, PEOPLE_VALUE_NORM))
    assert value["sender_address"] == "pedro@cliente.invalid" and value["signature"] == SENDER


# --- the command ----------------------------------------------------------------------------------

CLI_ENV = {
    "ORIGENLAB_WORKER_DATABASE_URL": "postgresql://origenlab_worker.abc:pw@aws-0-x.pooler.supabase.com:5432/postgres",
    "ORIGENLAB_WORKER_DATABASE_EXPECTED_HOST": "aws-0-x.pooler.supabase.com",
    "ORIGENLAB_WORKER_DATABASE_CA_PEM": "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----",
    "ORIGENLAB_WORKER_STORAGE_S3_ENDPOINT": "https://abc.storage.supabase.co/storage/v1/s3",
    "ORIGENLAB_WORKER_STORAGE_S3_REGION": "sa-east-1",
    "ORIGENLAB_WORKER_STORAGE_S3_ACCESS_KEY_ID": "id",
    "ORIGENLAB_WORKER_STORAGE_S3_SECRET_ACCESS_KEY": "secret",
}


def _cli_line(capsys) -> dict:
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def _run(args, env, db, store, reader):
    import time
    from contextlib import contextmanager

    from origenlab_worker import triage_cli

    @contextmanager
    def open_connection(target):
        yield object()

    class Db(FakeDb):
        def __init__(self, conn):  # noqa: D107 - the fake built by the command
            self.__dict__.update(db.__dict__)

        def pending_people(self, *, since_days, limit):
            self.asked = (since_days, limit)
            return list(self.messages)[:limit]

    original = triage_cli.TriageDb
    triage_cli.TriageDb = Db
    try:
        return triage_cli.run_people_backfill(args, env, time.monotonic(), open_connection=open_connection,
                                              store_factory=lambda s: store, reader_factory=lambda key: reader)
    finally:
        triage_cli.TriageDb = original


def test_the_cli_knows_people_backfill_with_a_90_day_default() -> None:
    from origenlab_worker.cli import _parser as build_parser

    args = build_parser().parse_args(["people-backfill"])
    assert (args.since_days, args.sample, args.apply) == (90, None, False)
    args = build_parser().parse_args(["people-backfill", "--sample", "100", "--apply"])
    assert (args.sample, args.apply) == (100, True)


def test_a_sample_prices_the_whole_run_and_prints_no_address(capsys) -> None:
    db, store = _world({f"sr-{i}": "quote_request" for i in range(4)})
    env = {**CLI_ENV, "ORIGENLAB_WORKER_ANTHROPIC_API_KEY": "k"}
    args = SimpleNamespace(apply=False, since_days=90, limit=5000, sample=2)
    assert _run(args, env, db, store, Reader()) == 0
    line = _cli_line(capsys)
    assert (line["mode"], line["found"], line["model_calls"], line["input_tokens"]) == ("sample", 4, 2, 2400)
    assert float(line["estimated_usd_all"]) == pytest.approx(2 * float(line["sample_usd"]), abs=1e-4)
    assert "ana@" not in json.dumps(line) and "Pérez" not in json.dumps(line)


def test_a_model_run_needs_its_key_but_a_plain_dry_run_does_not(capsys) -> None:
    db, store = _world({"sr-1": "quote_request"})
    args = SimpleNamespace(apply=True, since_days=90, limit=5000, sample=None)
    assert _run(args, CLI_ENV, db, store, Reader()) == 3
    assert _cli_line(capsys)["error"] == "anthropic_api_key_missing"
    args = SimpleNamespace(apply=False, since_days=90, limit=5000, sample=None)
    assert _run(args, CLI_ENV, db, store, Reader()) == 0
    line = _cli_line(capsys)
    assert (line["mode"], line["read"], line["model_calls"]) == ("dry_run", 1, 0) and db.recorded == []
