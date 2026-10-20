"""«Operaciones» facts against PostgreSQL: the two sent-quote lists. Every name and number is invented."""
from __future__ import annotations

import uuid

import psycopg
import pytest

from origenlab_api.v2.operations import read_operation_facts
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn


@pytest.fixture(scope="module")
def disposable_database():
    yield from build_disposable_database()


def _owner(dsn: str, sql: str, params: tuple = ()):
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


def _sent_quote_email(dsn: str, mailbox: str, filename: str) -> tuple[str, str]:
    """A message contacto@ sent with one quote PDF, and its Gmail evidence record: (message id, record id)."""
    gmail_id = uuid.uuid4().hex[:16]
    [(msg,)] = _owner(dsn, "insert into comms.message (mailbox_id, provider_message_id, provider_thread_id, direction, "
                           "internal_date) values (%s, %s, 'thread-x', 'outbound', now() - interval '2 days') "
                           "returning id::text", (mailbox, gmail_id))
    _owner(dsn, "insert into comms.message_participant (message_id, role, address_norm) values (%s, 'from', "
                "'contacto@origenlab.cl'), (%s, 'to', 'compras@empresa-ficticia.cl')", (msg, msg))
    _owner(dsn, "insert into comms.attachment (message_id, part_index, filename, mime_type) values (%s, 1, %s, "
                "'application/pdf')", (msg, filename))
    [(rec,)] = _owner(dsn, "insert into evidence.source_record (kind, dedupe_key, payload, review_status) values "
                           "('gmail_message', %s, '{}'::jsonb, 'pending') returning id::text", (f"gmail_message:{gmail_id}",))
    return msg, rec


@needs_db
def test_a_quote_sent_on_a_case_email_but_never_recorded_is_listed_with_its_case(disposable_database) -> None:
    dsn = disposable_database
    [(op,)] = _owner(dsn, "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) values "
                          "(gen_random_uuid(), 'ops-db@example.test', 'Ops', 'sales', 'active') returning id::text")
    [(mailbox,)] = _owner(dsn, "select id::text from comms.mailbox limit 1") or _owner(
        dsn, "insert into comms.mailbox (address_norm) values ('contacto@origenlab.cl') returning id::text")
    # A requesting institution is set by the case commands (its trigger demands the confirmed link);
    # the list then names it — here the case has none yet, so the name is null and the form asks.
    [(case,)] = _owner(dsn, "insert into crm.opportunity (title, stage, owner_operator_id) values "
                            "('Cotización 09176-26 — Empresa Ficticia', 'lead', %s) returning id::text", (op,))
    _, linked = _sent_quote_email(dsn, mailbox, "CN09251-Persona - Empresa Ficticia.pdf")
    _owner(dsn, "insert into crm.opportunity_evidence (opportunity_id, source_record_id, relation, linked_by_operator_id) "
                "values (%s, %s, 'mentions', %s)", (case, linked, op))
    _, unlinked = _sent_quote_email(dsn, mailbox, "CN09300-Otra Persona.pdf")

    with psycopg.connect(runtime_dsn(dsn)) as conn, conn.cursor() as cur:
        facts = read_operation_facts(cur)
    on_case = facts["sent_quotes_unrecorded_on_case"]
    assert [(i["source_record_id"], i["quote_number"][:6], i["case_id"], i["organization_name"]) for i in on_case] == [
        (linked, "09251-", case, None)]
    assert [i["source_record_id"] for i in facts["sent_quotes_without_case"]] == [unlinked]
    assert facts["counts"]["sent_quotes_unrecorded_on_case"] == 1


@needs_db
def test_licitaciones_facts_sum_this_months_model_usage_and_preparations(disposable_database) -> None:
    dsn = disposable_database
    [(op,)] = _owner(dsn, "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) values "
                          "(gen_random_uuid(), 'ops-lic@example.test', 'Ops', 'sales', 'active') returning id::text")
    [(case,)] = _owner(dsn, "insert into crm.opportunity (title, stage, owner_operator_id) values "
                            "('Licitación ficticia', 'lead', %s) returning id::text", (op,))
    [(prep,)] = _owner(dsn, "insert into evidence.bid_preparation (opportunity_id, phase, status, annex_document_ids, "
                            "requested_by_operator_id) values (%s, 'read', 'ready', array[gen_random_uuid()], %s) "
                            "returning id::text", (case, op))
    for step, cost in (("read", "8.10"), ("match", "1.30"), ("read", "2.00")):
        _owner(dsn, "insert into platform.model_usage (feature, model, step, bid_preparation_id, input_tokens, "
                    "output_tokens, cache_read_tokens, cache_write_tokens, usd_cost) "
                    "values ('licitaciones', 'claude-sonnet-5-5', %s, %s, 1, 1, 0, 0, %s)", (step, prep, cost))
    with psycopg.connect(runtime_dsn(dsn)) as conn, conn.cursor() as cur:
        lic = read_operation_facts(cur)["licitaciones"]
    assert lic["spent_usd"] == "11.400000" and lic["cap_usd"] == "60" and lic["reached"] is False
    assert lic["preparaciones"] == {"ready": 1}
    assert lic["por_paso"] == {"read": "10.100000", "match": "1.300000", "fill": "0.000000"}
