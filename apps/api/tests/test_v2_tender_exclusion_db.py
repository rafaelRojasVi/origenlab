"""A tender stays out of every general read and every mail rule (spec 2026-10-19 §6.10, `case_kind.py`).

One sale case and one tender on the same institution, against a real disposable database as
`origenlab_api`. Each of them carries the same things — quotes, an open task, a note, a captured email
on the buyer's domain, a refused quote reading, a history entry, an automatic action — so every read
below would show the tender if it did not filter it, and shows the sale (where the read lists cases at
all) to prove it read something. Quote numbers are stored as `01300-26` (ruling R10).

Every name, domain, RUT, code and number is invented (public repository).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

from origenlab_api.v2.commands import CommandRefused, request_digest
from origenlab_api.v2.identity import OperatorIdentity

pytestmark = needs_db

TAG = uuid.uuid4().hex[:6]
TENDER_CODE = "1234599-12-LE26"
TENDER_NUMBERS = ("01300-26", "01302-26")
SALE_NUMBERS = ("01299-26", "01297-26")
MODEL = f"EJ{TAG.upper()}"


def _owner(dsn: str, sql: str, params: tuple = ()):
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


def _one(dsn: str, sql: str, params: tuple = ()):
    return _owner(dsn, sql, params)[0][0]


def _case(dsn: str, operator: str, org: str, *, title: str, kind: str) -> str:
    """A case walked to `quoting`, with its requesting institution (the deferred agreement check passes in
    one transaction), as `test_v2_quote_commands._case` builds one."""
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute("insert into crm.opportunity (title, stage, owner_operator_id, organization_id, kind, tender_status) "
                    "values (%s, 'lead', %s, %s, %s, %s) returning id::text",
                    (title, operator, org, kind, "preparando" if kind == "tender" else None))
        case_id = cur.fetchone()[0]
        for stage in ("qualifying", "qualified", "quoting"):  # the stage guard's one road
            cur.execute("update crm.opportunity set stage = %s, version = version + 1 where id = %s", (stage, case_id))
        cur.execute("insert into crm.opportunity_organization (opportunity_id, organization_id, role, valid_from, "
                    "confirmation, confirmed_by_operator_id) values (%s, %s, 'requesting_institution', "
                    "current_date, 'confirmed', %s)", (case_id, org, operator))
        conn.commit()
    return case_id


def _quote(dsn: str, case: str, number: str, operator: str, mailbox: str, *,
           draft_too: bool = False) -> tuple[str, str, str]:
    """A minted, authored quote approved and sent yesterday on its own outbound message (and a draft
    revision after it when `draft_too`, so it has two live revisions): (quote id, sent revision id,
    its PDF's SHA-256)."""
    sha = hashlib.sha256(f"{case}{number}".encode()).hexdigest()
    quote = _one(dsn, "insert into crm.quote (opportunity_id, quote_number, number_origin) values (%s, %s, 'minted') "
                      "returning id::text", (case, number))
    message = _one(dsn, "insert into comms.message (mailbox_id, provider_message_id, provider_thread_id, direction, "
                        "internal_date) values (%s, %s, %s, 'outbound', now() - interval '1 day') returning id::text",
                   (mailbox, f"sent-{uuid.uuid4().hex[:12]}", f"sent-thread-{uuid.uuid4().hex[:12]}"))
    revision = _one(dsn, "insert into crm.quote_revision (quote_id, revision_no, status, origin, quote_currency, "
                         "price_decimals, subtotal, discount_total, tax_base, tax_total, grand_total, totals_computed_at, "
                         "approved_at, approved_by_operator_id, sent_at, sent_message_id, pdf_sha256, party_snapshot, "
                         "party_snapshot_version) values (%s, 1, 'sent', 'authored', 'CLP', 0, 100000, 0, 100000, 19000, "
                         "119000, now() - interval '1 day', now() - interval '1 day', %s, now() - interval '1 day', %s, "
                         "%s, '{}'::jsonb, 1) returning id::text", (quote, operator, message, sha))
    if draft_too:
        _owner(dsn, "insert into crm.quote_revision (quote_id, revision_no, status, origin, quote_currency, "
                    "price_decimals) values (%s, 2, 'draft', 'authored', 'CLP', 0)", (quote,))
    return quote, revision, sha


def _mail(dsn: str, mailbox: str, *, thread: str, sender: str, subject: str, direction: str = "inbound",
          filename: str | None = None) -> str:
    """A live capture: the evidence record, its `comms.message` and its sender (as the worker writes
    them). Its id."""
    gid = uuid.uuid4().hex[:16]
    payload = {"gmail_message_id": gid, "gmail_thread_id": thread, "sender": sender,
               "recipients": "contacto@origenlab.cl" if direction == "inbound" else f"compras@hospital-{TAG}.test",
               "subject_raw": subject, "sent_at": datetime.now(UTC).isoformat(), "documents": [],
               "direction_hint": "external" if direction == "outbound" else None}
    record = _one(dsn, "insert into evidence.source_record (kind, dedupe_key, payload, source_uri) values "
                       "('gmail_message', %s, %s, %s) returning id::text",
                  (f"gmail_message:{gid}", Jsonb(payload), f"gmail://msg/{gid}"))
    message = _one(dsn, "insert into comms.message (mailbox_id, provider_message_id, provider_thread_id, direction, "
                        "internal_date, subject, parse_status) values (%s, %s, %s, %s, now() - interval '1 hour', %s, "
                        "'parsed') returning id::text", (mailbox, gid, thread, direction, subject))
    _owner(dsn, "insert into comms.message_participant (message_id, role, address_norm) values (%s, 'from', %s)",
           (message, sender.lower()))
    if filename:
        _owner(dsn, "insert into comms.attachment (message_id, part_index, filename, mime_type) values (%s, 1, %s, "
                    "'application/pdf')", (message, filename))
    return record


def _receipt(dsn: str, operator: str, command: str, body: dict) -> None:
    _owner(dsn, "insert into platform.command_receipt (operator_id, idempotency_key, command_name, request_digest, "
                "status, response_status, response_body, completed_at) values (%s, %s, %s, %s, 'completed', 200, %s, "
                "now() - interval '1 hour')", (operator, f"k-{uuid.uuid4().hex}", command, "d" * 64, json.dumps(body)))


@pytest.fixture(scope="module")
def db():
    yield from build_disposable_database()


@pytest.fixture(scope="module")
def world(db):
    """The sale case and the tender, each with the same kinds of rows hanging from it."""
    dsn = db
    operator = _one(dsn, "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) values "
                         "(gen_random_uuid(), %s, 'Vendedora Ficticia', 'sales', 'active') returning id::text",
                    (f"ventas-{TAG}@example.test",))
    org = _one(dsn, "insert into crm.organization (kind, name, confirmation) values ('institution', %s, 'confirmed') "
                    "returning id::text", (f"Hospital Ejemplo {TAG}",))
    _owner(dsn, "insert into crm.organization_domain (organization_id, domain_norm, scope) values (%s, %s, 'shared')",
           (org, f"hospital-{TAG}.test"))
    mailbox = _one(dsn, "insert into comms.mailbox (address_norm) values (%s) returning id::text",
                   (f"buzon-{TAG}@example.test",))
    w = {"dsn": dsn, "operator": operator, "org": org, "mailbox": mailbox}
    w["sale_title"] = f"Venta Ejemplo{TAG} balanzas"
    w["tender_title"] = f"Licitación {TENDER_CODE} — Ejemplo{TAG} centrífugas"
    w["sale"] = _case(dsn, operator, org, title=w["sale_title"], kind="sale")
    w["tender"] = _case(dsn, operator, org, title=w["tender_title"], kind="tender")
    for side, numbers in (("sale", SALE_NUMBERS), ("tender", TENDER_NUMBERS)):
        case = w[side]
        w[f"{side}_quote"], w[f"{side}_revision"], sha = _quote(dsn, case, numbers[0], operator, mailbox)
        w[f"{side}_quote2"], _, _ = _quote(dsn, case, numbers[1], operator, mailbox, draft_too=True)
        # Follow-up, note, history entry and automatic action on the case.
        w[f"{side}_task"] = _one(dsn, "insert into crm.task (opportunity_id, owner_operator_id, title, due_at, "
                                      "created_by_operator_id) values (%s, %s, %s, now() + interval '1 day', %s) "
                                      "returning id::text", (case, operator, f"Llamar {side} {TAG}", operator))
        _owner(dsn, "insert into crm.note (subject_kind, subject_id, body, author_operator_id, revision_no, status) "
                    "values ('opportunity', %s, %s, %s, 1, 'active')", (case, f"Nota {side} {TAG}", operator))
        _receipt(dsn, operator, "advance_case_stage", {"opportunity_id": case, "stage": "quoting"})
        _receipt(dsn, operator, "apply_mail_rule", {"rule_id": "R1", "trigger": "automatic", "case_id": case,
                                                    "case_title": f"acción {side} {TAG}"})
        # The quote PDF as the reader saw it: one priced line (price history) and one refusal (quote readings).
        document = _one(dsn, "insert into evidence.source_record (kind, dedupe_key, payload) values ('quote_document', "
                             "%s, %s) returning id::text",
                        (f"quote_document:pdf-{side}-{TAG}",
                         Jsonb({"printed_quote_number": numbers[0], "quote_date": "2026-10-10"})))
        _owner(dsn, "insert into evidence.document_line (source_record_id, line_no, model, model_key, qty, unit_price, "
                    "line_total, currency, extractor, check_status) values (%s, 1, %s, %s, 1, 1000, 1000, 'CLP', 'test', "
                    "'verified')", (document, MODEL, MODEL))
        _owner(dsn, "insert into evidence.assertion (source_record_id, kind, value_norm, value) values "
                    "(%s, 'quote_read', %s, %s)",
               (document, f"quote_reader:v9:{sha}", Jsonb({"revision_id": w[f"{side}_revision"], "code": "unreadable"})))
        # A captured email from the buyer's domain, filed on the case, with a reply on its thread
        # (R1) that the triage asked a person about.
        thread = f"thread-{side}-{TAG}"
        filed = _mail(dsn, mailbox, thread=thread, sender=f"compras@hospital-{TAG}.test",
                      subject=f"Consulta {side} {TAG}")
        _owner(dsn, "insert into crm.opportunity_evidence (opportunity_id, source_record_id, relation, "
                    "linked_by_operator_id) values (%s, %s, 'mentions', %s)", (case, filed, operator))
        w[f"{side}_reply"] = _mail(dsn, mailbox, thread=thread, sender=f"compras@hospital-{TAG}.test",
                                   subject=f"RE: Consulta {side} {TAG}")
        _owner(dsn, "insert into evidence.assertion (source_record_id, kind, value_norm, value) values "
                    "(%s, 'message_triage', 'triage:v1', %s)",
               (w[f"{side}_reply"], Jsonb({"class": "quote_followup", "needs_model": True, "model_state": "ran",
                                           "reading": {"stage": "negotiating", "summary_es": "Pregunta plazo.",
                                                       "products": []}})))
    # A new email naming the tender's quote number in its subject (R2 would file it there).
    w["by_number"] = _mail(dsn, mailbox, thread=f"thread-n-{TAG}", sender=f"compras@hospital-{TAG}.test",
                           subject=f"Consulta cotización N°{TENDER_NUMBERS[0]}")
    # contacto@ sent a quote PDF on the tender's thread that no quote records yet (Operaciones' card).
    w["sent_on_tender"] = _mail(dsn, mailbox, thread=f"thread-tender-{TAG}", sender="contacto@origenlab.cl",
                                subject="Oferta", direction="outbound", filename="CN01390-Hospital Ejemplo.pdf")
    _owner(dsn, "insert into crm.opportunity_evidence (opportunity_id, source_record_id, relation, "
                "linked_by_operator_id) values (%s, %s, 'mentions', %s)", (w["tender"], w["sent_on_tender"], operator))
    _owner(dsn, "insert into catalog.cost_parameter (key, value_numeric, valid_from, reason) "
                "values ('quote_number_seed', 1200, now() - interval '1 day', 'prueba')")
    _owner(dsn, "insert into catalog.cost_parameter (key, value_json, valid_from, reason) values "
                "('quote_default_warranty', to_jsonb('Garantía de prueba'::text), now() - interval '1 day', 'prueba')")
    return w


class _Identity:
    def __init__(self, operator_id: str) -> None:
        self._id = operator_id

    def resolve(self, headers):  # noqa: ANN001, ANN201
        return OperatorIdentity(operator_id=self._id, email_norm=f"ventas-{TAG}@example.test",
                                display_name="Vendedora Ficticia", role="sales", status="active")


def _fx(currency, on):  # noqa: ANN001, ANN202
    return {"currency": currency, "clp_per_unit": 1000, "as_of": on, "source": "bcentral", "provider": "test"}


@pytest.fixture(scope="module")
def client(world):
    """The read routers as `main.py` mounts them, on this database, for a sales operator."""
    from origenlab_api.v2.cockpit_repository import CockpitRepository
    from origenlab_api.v2.cockpit_routes import cockpit_router
    from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository
    from origenlab_api.v2.crm_workspace_routes import workspace_router
    from origenlab_api.v2.mail_rules_repository import MailRulesRepository
    from origenlab_api.v2.mail_rules_routes import mail_rules_preview_router
    from origenlab_api.v2.quotes.reads import V2QuoteReads
    from origenlab_api.v2.quotes.routes import quote_read_router
    from origenlab_api.v2.repository import V2Repository
    from origenlab_api.v2.routes import router as v2_router
    from origenlab_api.v2.triage_review import TriageReviewRepository
    from origenlab_api.v2.triage_review_routes import triage_review_read_router

    dsn = runtime_dsn(world["dsn"])
    app = FastAPI()
    app.state.v2_identity = _Identity(world["operator"])
    app.state.v2_repository = V2Repository(psycopg.connect, dsn)
    app.state.cockpit_repository = CockpitRepository(psycopg.connect, dsn)
    app.state.crm_workspace = CrmWorkspaceRepository(psycopg.connect, dsn)
    app.state.mail_rules_repository = MailRulesRepository(psycopg.connect, dsn)
    app.state.triage_review_repository = TriageReviewRepository(psycopg.connect, dsn)
    app.state.quote_reads = V2QuoteReads(psycopg.connect, dsn, fx_resolver=_fx)
    for router in (v2_router, cockpit_router, workspace_router, mail_rules_preview_router, triage_review_read_router,
                   quote_read_router):
        app.include_router(router)
    return TestClient(app)


def _tender_marks(world: dict) -> list[str]:
    return [world["tender"], world["tender_title"], world["tender_quote"], world["tender_quote2"],
            world["tender_task"], f"Nota tender {TAG}", f"acción tender {TAG}", *TENDER_NUMBERS]


#: Each general read, and whether it lists the sale case (its id) — `None`: the read lists no case.
#: `{org}` and `{tag}` are filled in per test, so every xdist worker collects the same ids.
GENERAL_READS = [
    ("/v2/workspace/pipeline", "sale"),
    ("/v2/workspace/overview", None),
    ("/v2/workspace/history", "sale"),
    ("/v2/workspace/operations", None),
    ("/v2/workspace/automatic-actions", "sale"),
    ("/v2/workspace/quote-readings", "sale"),
    ("/v2/workspace/triage-readings?status=all", None),
    ("/v2/cockpit/kpis", None),
    ("/v2/cockpit/work-queue", "sale"),
    ("/v2/cockpit/opportunities", "sale"),
    ("/v2/cockpit/quotations", "sale"),
    ("/v2/cockpit/search?q=Ejemplo{tag}", "sale"),
    ("/v2/cockpit/search?q=0130", None),
    ("/v2/cases", "sale"),
    ("/v2/opportunities/active", "sale"),
    ("/v2/tasks/due?horizon_days=7", "sale"),
    ("/v2/quotes/followup", "sale"),
    ("/v2/organizations/{org}", "sale"),
    ("/v2/organizations/{org}/cases", "sale"),
    ("/v2/quotes?status=all", "sale_quote"),
    ("/v2/quotes/clients?q=Hospital Ejemplo {tag}", "sale"),
    ("/v2/quotes/economics", "sale_quote"),
]


@pytest.mark.parametrize(("path", "shows"), GENERAL_READS)
def test_general_read_never_shows_tender(world, client, path, shows) -> None:
    response = client.get(path.format(org=world["org"], tag=TAG))
    assert response.status_code == 200, response.text
    body = response.text
    assert [mark for mark in _tender_marks(world) if mark in body] == []
    if shows is not None:
        assert world[shows] in body


def test_counts_hold_the_sale_cases_only(world, client) -> None:
    dsn = world["dsn"]
    sales = _one(dsn, "select count(*) from crm.opportunity where kind = 'sale'")
    quoting = _one(dsn, "select count(*) from crm.opportunity where kind = 'sale' and stage = 'quoting'")
    assert _one(dsn, "select count(*) from crm.opportunity") == sales + 1  # the tender is there
    overview = client.get("/v2/workspace/overview").json()
    assert {e["key"]: e["count"] for e in overview["entities"]}["opportunities"] == sales
    assert overview["opportunities_by_stage"]["quoting"] == quoting
    kpis = client.get("/v2/cockpit/kpis").json()
    assert kpis["opportunities"]["by_stage"]["quoting"] == quoting


@pytest.mark.parametrize("path", ["/v2/cockpit/opportunities/{id}", "/v2/cockpit/opportunities/{id}/timeline",
                                  "/v2/cases/{id}"])
def test_one_case_reads_answer_404_for_a_tender(world, client, path) -> None:
    assert client.get(path.format(id=world["tender"])).status_code == 404
    assert client.get(path.format(id=world["sale"])).status_code == 200


def test_a_tender_quote_is_404_in_the_quotations_read(world, client) -> None:
    assert client.get(f"/v2/cockpit/quotations/{world['tender_quote']}").status_code == 404
    assert client.get(f"/v2/cockpit/quotations/{world['sale_quote']}").status_code == 200


def test_price_history_leaves_out_a_tender_offer(world) -> None:
    from origenlab_api.v2.catalog.reads import V2CatalogReads

    history = json.dumps(V2CatalogReads(psycopg.connect, runtime_dsn(world["dsn"])).price_history(MODEL), default=str)
    assert SALE_NUMBERS[0] in history and TENDER_NUMBERS[0] not in history


def test_mail_rules_plan_ignores_tender(world) -> None:
    from origenlab_api.v2.mail_rules_repository import MailRulesRepository

    actions, _, snapshot = MailRulesRepository(psycopg.connect, runtime_dsn(world["dsn"])).plan()
    assert world["tender"] not in {c.id for c in snapshot.cases} and world["sale"] in {c.id for c in snapshot.cases}
    assert [a for a in actions if a.case_id == world["tender"] or world["tender"] in a.candidates] == []
    by_email = {a.evidence_id: a for a in actions}
    # The reply on the sale's thread is filed there (R1); the one on the tender's is not.
    assert (by_email[world["sale_reply"]].rule_id, by_email[world["sale_reply"]].case_id) == ("R1", world["sale"])
    assert by_email.get(world["tender_reply"]) is None or by_email[world["tender_reply"]].rule_id != "R1"
    assert by_email.get(world["by_number"]) is None or by_email[world["by_number"]].rule_id != "R2"


def _operator(world: dict) -> OperatorIdentity:
    return OperatorIdentity(operator_id=world["operator"], email_norm=f"ventas-{TAG}@example.test",
                            display_name="Vendedora Ficticia", role="sales", status="active")


def _refusal(run) -> tuple[str, int]:  # noqa: ANN001
    with pytest.raises(CommandRefused) as caught:
        run()
    return caught.value.code, caught.value.status_code


def _version(world: dict, case: str) -> int:
    return _one(world["dsn"], "select version from crm.opportunity where id = %s", (case,))


def test_case_command_on_tender_refused(world) -> None:
    from origenlab_api.v2.case_command_repository import V2CaseCommandRepository
    from origenlab_api.v2.case_commands import (
        ADVANCE_CASE_STAGE,
        RECORD_CASE_WON,
        AdvanceCaseStageBody,
        RecordCaseWonBody,
        validated_case,
    )

    repo = V2CaseCommandRepository(psycopg.connect, runtime_dsn(world["dsn"]))
    receipts = _one(world["dsn"], "select count(*) from platform.command_receipt")

    def run(command, body_type, **raw):  # noqa: ANN001, ANN202
        body = body_type(opportunity_id=world["tender"], opportunity_version=_version(world, world["tender"]),
                         note="Prueba", **raw)
        return repo.execute(command_name=command, operator=_operator(world), fields=validated_case(command, body),
                            idempotency_key=uuid.uuid4().hex, digest=request_digest(command, body))

    assert _refusal(lambda: run(ADVANCE_CASE_STAGE, AdvanceCaseStageBody, stage="negotiating")) == (
        "tender_not_a_sale", 409)
    assert _refusal(lambda: run(ADVANCE_CASE_STAGE, AdvanceCaseStageBody, stage="lost", close_reason="precio")) == (
        "tender_not_a_sale", 409)
    assert _refusal(lambda: run(RECORD_CASE_WON, RecordCaseWonBody, quote_id=world["tender_quote"], revision_no=1)) == (
        "tender_not_a_sale", 409)
    # Nothing moved and no receipt stayed behind.
    assert _one(world["dsn"], "select stage from crm.opportunity where id = %s", (world["tender"],)) == "quoting"
    assert _one(world["dsn"], "select count(*) from platform.command_receipt") == receipts
    # The same command on the sale case still runs.
    sale = AdvanceCaseStageBody(opportunity_id=world["sale"], opportunity_version=_version(world, world["sale"]),
                                note="Prueba", stage="negotiating")
    moved = repo.execute(command_name=ADVANCE_CASE_STAGE, operator=_operator(world),
                         fields=validated_case(ADVANCE_CASE_STAGE, sale), idempotency_key=uuid.uuid4().hex,
                         digest=request_digest(ADVANCE_CASE_STAGE, sale))
    assert moved["opportunity_id"] == world["sale"]


def test_task_and_note_commands_on_tender_refused(world) -> None:
    from origenlab_api.v2.crm_authoring import V2CrmAuthoringRepository
    from origenlab_api.v2.task_command_repository import V2TaskCommandRepository
    from origenlab_api.v2.task_commands import COMPLETE_TASK, CREATE_TASK

    dsn = runtime_dsn(world["dsn"])
    tasks = V2TaskCommandRepository(psycopg.connect, dsn)
    due = datetime.now(UTC) + timedelta(days=2)
    assert _refusal(lambda: tasks.execute(
        command_name=CREATE_TASK, operator=_operator(world),
        fields={"note": "Prueba", "opportunity_id": world["tender"], "title": "Llamar", "due_at": due},
        idempotency_key=uuid.uuid4().hex, digest="a" * 64)) == ("tender_not_a_sale", 409)
    assert _refusal(lambda: tasks.execute(
        command_name=COMPLETE_TASK, operator=_operator(world),
        fields={"note": "Hecho", "task_id": world["tender_task"], "task_version": 1},
        idempotency_key=uuid.uuid4().hex, digest="b" * 64)) == ("tender_not_a_sale", 409)
    notes = V2CrmAuthoringRepository(psycopg.connect, dsn)
    assert _refusal(lambda: notes.execute(
        command_name="add-note", operator=_operator(world),
        fields={"subject_kind": "opportunity", "subject_id": world["tender"], "body": "Una nota"},
        idempotency_key=uuid.uuid4().hex, digest="c" * 64)) == ("tender_not_a_sale", 409)
    assert _one(world["dsn"], "select status from crm.task where id = %s", (world["tender_task"],)) == "open"
    # On the sale case a follow-up is still written.
    made = tasks.execute(command_name=CREATE_TASK, operator=_operator(world),
                         fields={"note": "Prueba", "opportunity_id": world["sale"], "title": "Llamar", "due_at": due},
                         idempotency_key=uuid.uuid4().hex, digest="d" * 64)
    assert _one(world["dsn"], "select opportunity_id::text from crm.task where id = %s", (made["task_id"],)) == world["sale"]


def test_create_quote_for_client_skips_tender(world) -> None:
    """The institution has two open cases, the sale and the tender: the client's single open case is
    the sale, so the new quote lands there (with the tender counted it would be `case_choice_needed`)."""
    from origenlab_api.v2.quotes.commands import V2QuoteRepository
    from origenlab_api.v2.quotes.draft import QUOTE_BODIES

    body = QUOTE_BODIES["create-quote"](client={"organization_id": world["org"]})
    created = V2QuoteRepository(psycopg.connect, runtime_dsn(world["dsn"]), fx_resolver=_fx).execute(
        command_name="create-quote", operator=_operator(world), fields=body.model_dump(mode="json"),
        idempotency_key=uuid.uuid4().hex, digest=request_digest("create-quote", body))
    assert _one(world["dsn"], "select opportunity_id::text from crm.quote where id = %s",
                (created["quote_id"],)) == world["sale"]
    # Named explicitly, a tender still takes a quote (Task 10's offer).
    on_tender = QUOTE_BODIES["create-quote"](opportunity_id=world["tender"])
    offer = V2QuoteRepository(psycopg.connect, runtime_dsn(world["dsn"]), fx_resolver=_fx).execute(
        command_name="create-quote", operator=_operator(world), fields=on_tender.model_dump(mode="json"),
        idempotency_key=uuid.uuid4().hex, digest=request_digest("create-quote", on_tender))
    assert _one(world["dsn"], "select opportunity_id::text from crm.quote where id = %s",
                (offer["quote_id"],)) == world["tender"]
