"""`attach_case_document` / `remove_case_document` against a database: the row and its event, the
refusals that write nothing, the duplicate rule, one removal, and that attaching takes no case
version (so «Marcar ganada» can attach the OC and then record the win against the version it saw).

Runs in a disposable `origenlab_test_<hex>` (tests/v2_command_harness.py) as the unprivileged
`origenlab_api` role. Every name, number and hash is fictitious.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

import pytest

from origenlab_api.v2.case_commands import (
    ADD_CASE_ORGANIZATION,
    ADVANCE_CASE_STAGE,
    OPEN_COMMERCIAL_CASE,
    AddCaseOrganizationBody,
    AdvanceCaseStageBody,
    OpenCommercialCaseBody,
    validated_case,
)
from origenlab_api.v2.case_documents import ATTACH_CASE_DOCUMENT, REMOVE_CASE_DOCUMENT, document_path
from origenlab_api.v2.commands import CommandRefused, request_digest
from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2.quote_import_commands import (
    RECORD_HISTORICAL_QUOTATION,
    RecordHistoricalQuotationBody,
    validated_quote_import,
)
from origenlab_api.v2.quote_import_repository import document_reference_value
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

pytestmark = needs_db

SENT = datetime(2026, 9, 25, 15, 0, tzinfo=UTC)
_KEYS = iter(range(1, 1_000_000))


@pytest.fixture(scope="module")
def db():
    yield from build_disposable_database()


@pytest.fixture
def world(db):
    import psycopg

    tag = uuid.uuid4().hex[:12]
    quote_sha = hashlib.sha256(f"q{tag}".encode()).hexdigest()
    po_sha = hashlib.sha256(f"po{tag}".encode()).hexdigest()
    w: dict[str, object] = {"tag": tag, "quote_sha": quote_sha, "po_sha": po_sha, "number": f"0{tag[:5]}-26"}
    with psycopg.connect(db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
            "values (gen_random_uuid(), %s, 'Vendedora Ficticia', 'sales', 'active') returning id::text",
            (f"ventas-doc-{tag}@example.test",),
        )
        w["operator_id"] = cur.fetchone()[0]
        payload = {"subject": "OC", "documents": [{"sha256": quote_sha, "filename": "cot.pdf"},
                                                   {"sha256": po_sha, "filename": "OC 4500.pdf"}]}
        cur.execute(
            "insert into evidence.source_record (kind, dedupe_key, payload, source_uri) "
            "values ('gmail_message', %s, %s::jsonb, %s) returning id::text",
            (f"pytest-doc:{tag}", json.dumps(payload), f"gmail://msg/doc{tag}"),
        )
        w["record_id"] = cur.fetchone()[0]
        cur.execute(
            "insert into evidence.assertion (source_record_id, kind, value_norm, value) "
            "values (%s, 'document_reference', %s, '{}'::jsonb)",
            (w["record_id"], document_reference_value(quote_sha)),
        )
        cur.execute(
            "insert into crm.organization (kind, name, confirmation) values "
            "('institution', %s, 'confirmed') returning id::text, version",
            (f"Universidad Ficticia {tag}",),
        )
        w["org_id"], w["org_version"] = cur.fetchone()
    return w


def _operator(world) -> OperatorIdentity:
    return OperatorIdentity(operator_id=world["operator_id"], email_norm="ventas@example.test",
                            display_name="Vendedora Ficticia", role="sales", status="active")


def _repo(db):
    import psycopg

    from origenlab_api.v2.case_command_repository import V2CaseCommandRepository

    return V2CaseCommandRepository(psycopg.connect, runtime_dsn(db))


def _case_cmd(db, name, body, world):
    return _repo(db).execute(command_name=name, operator=_operator(world), fields=validated_case(name, body),
                             idempotency_key=f"pytest-doc-{next(_KEYS)}", digest=request_digest(name, body))


def _quoted_case(db, world) -> tuple[str, int, str]:
    """A case at `quoting` with one quote revision; returns (case, version, revision id)."""
    import psycopg

    from origenlab_api.v2.quote_import_repository import V2QuoteImportRepository

    opened = _case_cmd(db, OPEN_COMMERCIAL_CASE, OpenCommercialCaseBody(
        title="Equipo", origin_source_record_id=world["record_id"], note="correo"), world)
    case_id, version = opened["opportunity_id"], opened["opportunity_version"]
    version = _case_cmd(db, ADD_CASE_ORGANIZATION, AddCaseOrganizationBody(
        opportunity_id=case_id, opportunity_version=version, organization_id=world["org_id"],
        organization_version=world["org_version"], role="requesting_institution", note="confirmada"),
        world)["opportunity_version"]
    for stage in ("qualifying", "qualified", "quoting"):
        version = _case_cmd(db, ADVANCE_CASE_STAGE, AdvanceCaseStageBody(
            opportunity_id=case_id, opportunity_version=version, stage=stage, note="avanza"), world)["opportunity_version"]
    body = RecordHistoricalQuotationBody(
        opportunity_id=case_id, quote_number=world["number"], printed_quote_numbers=[world["number"]],
        document_sha256=world["quote_sha"], sent_at=SENT, origin_source_record_id=world["record_id"],
        ledger_decision_id=f"d-{next(_KEYS)}", note="enviada")
    V2QuoteImportRepository(psycopg.connect, runtime_dsn(db)).execute(
        command_name=RECORD_HISTORICAL_QUOTATION, operator=_operator(world),
        fields=validated_quote_import(RECORD_HISTORICAL_QUOTATION, body),
        idempotency_key=f"pytest-doc-q-{next(_KEYS)}", digest=request_digest(RECORD_HISTORICAL_QUOTATION, body))
    revision_id, version = _one(db, """select r.id::text, o.version from crm.quote_revision r
                                         join crm.quote q on q.id = r.quote_id
                                         join crm.opportunity o on o.id = q.opportunity_id
                                        where q.opportunity_id = %s""", case_id)
    return case_id, version, revision_id


def _one(db, sql, *args):
    import psycopg

    with psycopg.connect(db) as conn:
        conn.execute("set role origenlab_owner")
        return conn.execute(sql, args).fetchone()


def _attach(db, world, case_id, *, sha=None, source="upload", revision=None, number="4500", key=None):
    sha = sha or world["po_sha"]
    fields = {
        "opportunity_id": case_id, "kind": "purchase_order", "quote_revision_id": revision,
        "document_number": number, "note": "OC adjuntada", "storage_path": document_path(case_id, sha, "application/pdf"),
        "sha256": sha, "content_type": "application/pdf", "byte_size": 2048, "original_filename": "OC 4500.pdf",
        "source": source, "source_record_id": world["record_id"] if source == "email" else None,
    }
    return _repo(db).execute(command_name=ATTACH_CASE_DOCUMENT, operator=_operator(world), fields=fields,
                             idempotency_key=key or f"pytest-doc-a-{next(_KEYS)}", digest="a" * 64)


def test_attaching_records_the_document_and_its_event_and_leaves_the_case_version(db, world) -> None:
    case_id, version, revision_id = _quoted_case(db, world)
    out = _attach(db, world, case_id, revision=revision_id)
    doc = out["document"]
    assert doc["quote_revision_id"] == revision_id and doc["document_number"] == "4500"
    assert doc["source"] == "upload" and doc["removed_at"] is None
    assert _one(db, "select version from crm.opportunity where id = %s", case_id)[0] == version
    event = _one(db, """select event_type, actor_kind, payload ->> 'document_id', payload ->> 'kind'
                          from crm.domain_event where aggregate_id = %s order by seq desc limit 1""", case_id)
    assert event == ("opportunity.document_attached", "operator", doc["document_id"], "purchase_order")


def test_an_email_document_must_be_one_its_message_carries(db, world) -> None:
    case_id, _, _ = _quoted_case(db, world)
    assert _attach(db, world, case_id, source="email")["document"]["source_record_id"] == world["record_id"]
    with pytest.raises(CommandRefused) as refused:
        _attach(db, world, case_id, sha="f" * 64, source="email")
    assert refused.value.code == "document_not_in_message"


def test_the_same_bytes_twice_is_a_409_and_a_foreign_revision_a_404(db, world) -> None:
    case_id, _, _ = _quoted_case(db, world)
    other_revision = str(uuid.uuid4())  # a revision that is not on this case
    _attach(db, world, case_id)
    with pytest.raises(CommandRefused) as dup:
        _attach(db, world, case_id)
    assert (dup.value.status_code, dup.value.code) == (409, "document_already_attached")
    with pytest.raises(CommandRefused) as foreign:
        _attach(db, world, case_id, sha="e" * 64, revision=other_revision)
    assert (foreign.value.status_code, foreign.value.code) == (404, "quote_revision_not_on_case")
    assert _one(db, "select count(*) from crm.opportunity_document where opportunity_id = %s", case_id)[0] == 1


def test_a_document_is_removed_once_and_may_then_be_attached_again(db, world) -> None:
    case_id, _, _ = _quoted_case(db, world)
    doc_id = _attach(db, world, case_id)["document"]["document_id"]
    repo = _repo(db)

    def remove(key):
        return repo.execute(command_name=REMOVE_CASE_DOCUMENT, operator=_operator(world),
                            fields={"document_id": doc_id, "note": "archivo equivocado"},
                            idempotency_key=key, digest="b" * 64)

    removed = remove(f"pytest-doc-r-{next(_KEYS)}")
    assert removed["document"]["remove_reason"] == "archivo equivocado"
    with pytest.raises(CommandRefused) as again:
        remove(f"pytest-doc-r-{next(_KEYS)}")
    assert again.value.code == "document_already_removed"
    assert _attach(db, world, case_id)["document"]["document_id"] != doc_id
    events = _one(db, """select array_agg(event_type order by seq) from crm.domain_event
                          where aggregate_id = %s and event_type like 'opportunity.document%%'""", case_id)[0]
    assert events == ["opportunity.document_attached", "opportunity.document_removed", "opportunity.document_attached"]


def test_a_replayed_key_decides_nothing_twice(db, world) -> None:
    case_id, _, _ = _quoted_case(db, world)
    first = _attach(db, world, case_id, key="pytest-doc-same-key-" + world["tag"])
    second = _attach(db, world, case_id, key="pytest-doc-same-key-" + world["tag"])
    assert second["replayed"] is True and second["document"] == first["document"]


def test_an_unknown_case_is_a_404(db, world) -> None:
    with pytest.raises(CommandRefused) as refused:
        _attach(db, world, str(uuid.uuid4()))
    assert (refused.value.status_code, refused.value.code) == (404, "case_not_found")
