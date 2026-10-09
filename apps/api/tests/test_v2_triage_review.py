"""Mail triage review — the queue of the worker's suggestions and `review_triage`.

**In process, always run:** the two routes, who may use them, the body's rules (a correction says
what changed, only a correction carries fields, closed vocabularies, a blank note is no note).

**Database-backed, opt-in** (`ORIGENLAB_V2_TEST_DSN`): a pending suggestion is listed with its email
and the stage of the case its thread is on; a verdict is one append-only row and one event under
one receipt; a replay writes nothing; a second verdict supersedes without rewriting; the noise the
model never read is not listed; a non-triage assertion is refused. Every name is fictitious.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
from origenlab_api.v2.commands import CommandRefused, request_digest
from origenlab_api.v2.identity import LocalDevIdentity, OperatorIdentity, OperatorLookup
from origenlab_api.v2.triage_review import (
    REVIEW_TRIAGE,
    TRIAGE_STAGES,
    ReviewTriageBody,
    validated_review,
)

LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"
ASSERTION_ID = "aaaaaaaa-1111-4111-8111-111111111111"


class _Lookup(OperatorLookup):
    def __init__(self, operator: OperatorIdentity) -> None:
        self._operator = operator

    def by_email(self, email_norm: str) -> OperatorIdentity | None:
        return self._operator


def _operator(role: str = "sales") -> OperatorIdentity:
    return OperatorIdentity(operator_id="00000000-0000-4000-8000-000000000001", email_norm="operator@example.cl",
                            display_name="Operator", role=role, status="active")


class _FakeRepo:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        return {"command": kwargs["command_name"], "replayed": False}

    def readings(self, *, status, limit):
        self.calls.append({"status": status, "limit": limit})
        return {"status": status, "items": []}


def _client(repo: _FakeRepo, role: str = "sales") -> TestClient:
    from fastapi import FastAPI

    from origenlab_api.v2.triage_review_routes import triage_review_command_router, triage_review_read_router

    app = FastAPI()
    app.include_router(triage_review_read_router)
    app.include_router(triage_review_command_router)
    app.state.triage_review_repository = repo
    app.state.v2_identity = LocalDevIdentity(LOOPBACK, _Lookup(_operator(role)))
    return TestClient(app)


def _headers(key: str | None = "key-1") -> dict[str, str]:
    headers = {OPERATOR_EMAIL_HEADER: "operator@example.cl"}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def test_a_verdict_reaches_the_repository_and_a_viewer_may_only_read() -> None:
    repo = _FakeRepo()
    body = {"assertion_id": ASSERTION_ID, "verdict": "approved"}
    assert _client(repo).post("/v2/commands/review-triage", json=body, headers=_headers()).status_code == 200
    assert repo.calls[0]["command_name"] == REVIEW_TRIAGE
    viewer = _FakeRepo()
    assert _client(viewer, "viewer").post("/v2/commands/review-triage", json=body,
                                          headers=_headers()).status_code == 403
    assert _client(viewer, "viewer").get("/v2/workspace/triage-readings", headers=_headers(None)).status_code == 200
    assert viewer.calls == [{"status": "pending", "limit": 100}]


def test_a_verdict_needs_a_key_and_refuses_unknown_fields() -> None:
    repo = _FakeRepo()
    body = {"assertion_id": ASSERTION_ID, "verdict": "approved"}
    assert _client(repo).post("/v2/commands/review-triage", json=body, headers=_headers(None)).status_code == 400
    assert _client(repo).post("/v2/commands/review-triage", json={**body, "stage": "won"},
                              headers=_headers()).status_code == 422
    assert repo.calls == []


def test_the_body_rules() -> None:
    fields = validated_review(ReviewTriageBody.model_validate(
        {"assertion_id": ASSERTION_ID, "verdict": "corrected", "note": "  ",
         "corrected": {"class": "quote_followup", "stage": "negotiating",
                       "products": [{"description": "Balanza analítica", "quantity": 2}]}}))
    assert fields["note"] is None
    assert fields["corrected"] == {"class": "quote_followup", "stage": "negotiating",
                                   "products": [{"description": "Balanza analítica", "quantity": 2}]}
    with pytest.raises(CommandRefused, match="correction"):
        validated_review(ReviewTriageBody(assertion_id=ASSERTION_ID, verdict="corrected"))
    with pytest.raises(CommandRefused):
        validated_review(ReviewTriageBody.model_validate(
            {"assertion_id": ASSERTION_ID, "verdict": "rejected", "corrected": {"stage": "won"}}))
    for bad in ({"stage": "ganada"}, {"class": "spam"}, {"price": 3}):
        with pytest.raises(ValidationError):
            ReviewTriageBody.model_validate({"assertion_id": ASSERTION_ID, "verdict": "corrected", "corrected": bad})
    assert TRIAGE_STAGES[-2:] == ("not_a_case", "unclear")


# ------------------------------------------------------------------ against PostgreSQL

from v2_command_harness import build_disposable_database, needs_db, runtime_dsn  # noqa: E402

_KEYS = iter(range(1, 1_000_000))


@pytest.fixture(scope="module")
def db():
    yield from build_disposable_database()


@pytest.fixture
def world(db):
    """A sales operator, a case at `lead` linked to a thread, and two triage readings on that
    thread: one the model read, one bounce it never did."""
    import psycopg
    from psycopg.types.json import Jsonb

    tag = uuid.uuid4().hex[:12]
    thread = f"thr-{tag}"
    with psycopg.connect(db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute("insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                    "values (gen_random_uuid(), %s, 'Vendedora Ficticia', 'sales', 'active') returning id::text",
                    (f"ventas-{tag}@example.test",))
        operator_id = cur.fetchone()[0]
        cur.execute("insert into comms.mailbox (address_norm) values (%s) returning id",
                    (f"buzon-{tag}@example.test",))
        mailbox = cur.fetchone()[0]
        records = []
        for n, (subject, value) in enumerate([
            ("RE: Cotización balanzas", {"class": "quote_followup", "needs_model": True, "model_state": "ran",
                                         "reading": {"stage": "negotiating", "summary_es": "Pregunta plazo.",
                                                     "products": []}}),
            ("Undeliverable: hola", {"class": "bounce", "needs_model": False, "model_state": "not_needed"}),
        ]):
            gid = f"g-{tag}-{n}"
            cur.execute("insert into evidence.source_record (kind, dedupe_key, payload) values "
                        "('gmail_message', %s, %s) returning id::text",
                        (f"gmail_message:{gid}", Jsonb({"gmail_message_id": gid, "gmail_thread_id": thread})))
            sid = cur.fetchone()[0]
            cur.execute("insert into comms.message (mailbox_id, provider_message_id, provider_thread_id, direction, "
                        "internal_date, subject, parse_status) values (%s, %s, %s, 'inbound', now(), %s, 'parsed') "
                        "returning id", (mailbox, gid, thread, subject))
            mid = cur.fetchone()[0]
            cur.execute("insert into comms.message_participant (message_id, role, address_norm) "
                        "values (%s, 'from', %s)", (mid, f"cliente-{tag}@lab-{tag}.example"))
            cur.execute("insert into evidence.assertion (source_record_id, kind, value_norm, value) values "
                        "(%s, 'message_triage', 'triage:v1', %s) returning id::text", (sid, Jsonb(value)))
            records.append((sid, cur.fetchone()[0]))
        cur.execute("insert into crm.opportunity (title, stage, owner_operator_id) values (%s, 'lead', %s) "
                    "returning id::text", (f"Caso ficticio {tag}", operator_id))
        case_id = cur.fetchone()[0]
        cur.execute("insert into crm.opportunity_evidence (opportunity_id, source_record_id, relation, "
                    "linked_by_operator_id) values (%s, %s, 'mentions', %s)", (case_id, records[0][0], operator_id))
    return {"db": db, "operator_id": operator_id, "case_id": case_id,
            "read": records[0][1], "noise": records[1][1], "noise_source": records[1][0],
            "sender": f"cliente-{tag}@lab-{tag}.example"}


def _repo(world):
    import psycopg

    from origenlab_api.v2.triage_review import TriageReviewRepository

    return TriageReviewRepository(psycopg.connect, runtime_dsn(world["db"]))


def _review(world, body, key=None):
    operator = OperatorIdentity(operator_id=world["operator_id"], email_norm="ventas@example.test",
                                display_name="Vendedora Ficticia", role="sales", status="active")
    parsed = ReviewTriageBody.model_validate(body)
    return _repo(world).execute(command_name=REVIEW_TRIAGE, operator=operator, fields=validated_review(parsed),
                                idempotency_key=key or f"pytest-triage-{next(_KEYS)}",
                                digest=request_digest(REVIEW_TRIAGE, parsed))


def _owner(world, sql, *args):
    import psycopg

    with psycopg.connect(world["db"], autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(sql, args)
        return cur.fetchall()


@needs_db
def test_the_queue_lists_what_the_model_read_with_its_case_stage_and_hides_noise(world) -> None:
    items = {i["assertion_id"]: i for i in _repo(world).readings(status="pending", limit=200)["items"]}
    assert world["noise"] not in items
    item = items[world["read"]]
    assert item["class"] == "quote_followup" and item["stage"] == "negotiating"
    assert item["cases"][0]["opportunity_id"] == world["case_id"] and item["cases"][0]["stage"] == "lead"
    assert item["subject"] == "RE: Cotización balanzas" and item["review"] is None


@needs_db
def test_the_queue_says_when_the_sender_is_a_registered_supplier(world) -> None:
    def flag():
        items = {i["assertion_id"]: i for i in _repo(world).readings(status="all", limit=200)["items"]}
        return items[world["read"]]["sender_is_supplier"]

    domain = world["sender"].split("@", 1)[1]
    assert flag() is False
    org = _owner(world, "insert into crm.organization (kind, name, confirmation) values "
                        "('supplier', 'Proveedor Ficticio', 'confirmed') returning id")[0][0]
    _owner(world, "insert into crm.organization_domain (organization_id, domain_norm, scope) "
                  "values (%s, %s, 'exclusive') returning id", org, domain)
    assert flag() is True


@needs_db
def test_a_verdict_is_one_row_one_event_and_a_replay_writes_nothing(world) -> None:
    body = {"assertion_id": world["read"], "verdict": "approved", "note": "correcto"}
    first = _review(world, body, key=f"pytest-triage-replay-{world['read']}")
    again = _review(world, body, key=f"pytest-triage-replay-{world['read']}")
    assert again["replayed"] is True and again["review_id"] == first["review_id"]
    assert _owner(world, "select verdict, note from evidence.triage_review where assertion_id = %s",
                  world["read"]) == [("approved", "correcto")]
    assert _owner(world, "select event_type, payload->>'verdict' from crm.domain_event where aggregate_kind = "
                         "'assertion' and aggregate_id = %s", world["read"]) == [("assertion.triage_reviewed", "approved")]
    assert world["read"] not in {i["assertion_id"] for i in _repo(world).readings(status="pending")["items"]}
    reviewed = {i["assertion_id"]: i for i in _repo(world).readings(status="reviewed")["items"]}
    assert reviewed[world["read"]]["review"]["verdict"] == "approved"


@needs_db
def test_a_second_verdict_supersedes_without_rewriting(world) -> None:
    _review(world, {"assertion_id": world["read"], "verdict": "approved"})
    _review(world, {"assertion_id": world["read"], "verdict": "corrected", "note": "seguía en Enviada",
                    "corrected": {"stage": "quoting"}})
    assert _owner(world, "select verdict from evidence.triage_review where assertion_id = %s order by reviewed_at",
                  world["read"]) == [("approved",), ("corrected",)]
    item = {i["assertion_id"]: i for i in _repo(world).readings(status="all")["items"]}[world["read"]]
    assert item["review"]["verdict"] == "corrected" and item["review"]["corrected"] == {"stage": "quoting"}


@needs_db
def test_only_a_triage_suggestion_is_reviewed_and_a_refusal_writes_nothing(world) -> None:
    other = _owner(world, "insert into evidence.assertion (source_record_id, kind, value_norm) values "
                          "(%s, 'organization_name', 'Ejemplo') returning id::text", world["noise_source"])[0][0]
    with pytest.raises(CommandRefused) as exc:
        _review(world, {"assertion_id": other, "verdict": "approved"})
    assert exc.value.code == "not_a_triage_suggestion"
    with pytest.raises(CommandRefused) as missing:
        _review(world, {"assertion_id": str(uuid.uuid4()), "verdict": "approved"})
    assert missing.value.code == "suggestion_not_found"
    assert _owner(world, "select count(*) from evidence.triage_review where assertion_id = %s", other) == [(0,)]
