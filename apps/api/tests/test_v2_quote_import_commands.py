"""Historical-quotation commands: request validation (always) and database proofs (opt-in).

The database-backed tests run in a disposable `origenlab_test_<hex>` the harness creates and
drops, as the unprivileged `origenlab_api` role. Every value is fictitious (`.test` / `.invalid`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from origenlab_api.v2.case_commands import (
    ADD_CASE_ORGANIZATION,
    ADVANCE_CASE_STAGE,
    OPEN_COMMERCIAL_CASE,
    AddCaseOrganizationBody,
    AdvanceCaseStageBody,
    OpenCommercialCaseBody,
    validated_case,
)
from origenlab_api.v2.commands import CommandRefused, request_digest
from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2.quote_import_commands import (
    RECORD_HISTORICAL_QUOTATION,
    VOID_HISTORICAL_QUOTE_REVISION,
    RecordHistoricalQuotationBody,
    VoidHistoricalQuoteRevisionBody,
    validated_quote_import,
)
from origenlab_api.v2.quote_import_repository import document_reference_value

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
CASE = "aaaaaaaa-1111-4111-8111-111111111111"
RECORD = "bbbbbbbb-2222-4222-8222-222222222222"
SENT = datetime(2026, 3, 25, 20, 47, 6, tzinfo=UTC)


def _body(**over) -> RecordHistoricalQuotationBody:
    base = dict(
        opportunity_id=CASE, quote_number="01006-26", printed_quote_numbers=["01006-26"],
        document_sha256=SHA_A, sent_at=SENT, origin_source_record_id=RECORD,
        ledger_decision_id="d-1", note="confirmada por el operador",
    )
    base.update(over)
    return RecordHistoricalQuotationBody(**base)


# ------------------------------------------------------------------ in process


def test_a_number_the_document_does_not_print_is_refused() -> None:
    with pytest.raises(ValidationError, match="printed_quote_numbers exactly"):
        _body(quote_number="1006-26")


def test_the_printed_number_is_kept_character_for_character() -> None:
    fields = validated_quote_import(RECORD_HISTORICAL_QUOTATION, _body(
        quote_number="011453AI-26", printed_quote_numbers=["011453AI-26"]))
    assert fields["quote_number"] == "011453AI-26"


def test_a_naive_sent_at_is_refused() -> None:
    with pytest.raises(ValidationError, match="UTC offset"):
        _body(sent_at=datetime(2026, 3, 25, 17, 47))


def test_money_cannot_be_smuggled_in() -> None:
    with pytest.raises(ValidationError):
        RecordHistoricalQuotationBody(**{**_body().model_dump(), "grand_total": "100"})


def test_a_document_cannot_supersede_itself() -> None:
    with pytest.raises(ValidationError, match="supersede itself"):
        _body(supersedes_document_sha256=SHA_A)


def test_a_malformed_digest_is_refused() -> None:
    with pytest.raises(ValidationError):
        _body(document_sha256="xyz")


# ------------------------------------------------------------------ database-backed (opt-in)

from v2_command_harness import (  # noqa: E402
    build_disposable_database,
    needs_db as _needs_db,
    runtime_dsn as _runtime_dsn,
)


@pytest.fixture(scope="module")
def disposable_database():
    yield from build_disposable_database()


def _identity(operator_id: str) -> OperatorIdentity:
    return OperatorIdentity(operator_id=operator_id, email_norm="pytest@example.test",
                            display_name="Pytest Operator", role="sales", status="active")


_KEYS = iter(range(1, 1_000_000))


@pytest.fixture
def world(disposable_database):
    """One operator, two institutions, and evidence records carrying three documents."""
    import json

    import psycopg

    import hashlib

    tag = uuid.uuid4().hex[:12]
    sha = {k: hashlib.sha256(f"{k}{tag}".encode()).hexdigest() for k in "abc"}
    state: dict[str, object] = {"tag": tag, "sha_a": sha["a"], "sha_b": sha["b"], "sha_c": sha["c"],
                               "number": f"0{tag[:5]}-26"}
    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
            "values (gen_random_uuid(), %s, 'Pytest Operator', 'sales', 'active') returning id::text",
            (f"pytest-quote-{tag}@example.test",),
        )
        state["operator_id"] = cur.fetchone()[0]
        for key, shas in (("record_id", [sha["a"], sha["b"]]), ("other_record_id", [sha["c"]])):
            payload = {"subject": "pytest", "documents": [{"sha256": s, "filename": f"{s[:4]}.pdf"} for s in shas]}
            cur.execute(
                "insert into evidence.source_record (kind, dedupe_key, payload, source_uri) "
                "values ('gmail_message', %s, %s::jsonb, %s) returning id::text",
                (f"pytest-quote:{key}:{tag}", json.dumps(payload), f"gmail://msg/{key}{tag}"),
            )
            state[key] = cur.fetchone()[0]
            for s in shas:
                cur.execute(
                    "insert into evidence.assertion (source_record_id, kind, value_norm, value) "
                    "values (%s, 'document_reference', %s, '{}'::jsonb) returning id::text",
                    (state[key], document_reference_value(s)),
                )
                state[f"assertion_{next(k for k, v in sha.items() if v == s)}"] = cur.fetchone()[0]
        for key in ("org_id", "other_org_id"):
            cur.execute(
                "insert into crm.organization (kind, name, confirmation) values "
                "('institution', %s, 'confirmed') returning id::text, version",
                (f"Instituto Ficticio {key} {tag}",),
            )
            state[key], state[f"{key}_version"] = cur.fetchone()
    return state


def _run(repo, validate, name, body, world, key=None):
    return repo.execute(command_name=name, operator=_identity(world["operator_id"]),
                        fields=validate(name, body), idempotency_key=key or f"pytest-q-{next(_KEYS)}",
                        digest=request_digest(name, body))


def _repos(db):
    import psycopg

    from origenlab_api.v2.case_command_repository import V2CaseCommandRepository
    from origenlab_api.v2.quote_import_repository import V2QuoteImportRepository

    dsn = _runtime_dsn(db)
    return V2CaseCommandRepository(psycopg.connect, dsn), V2QuoteImportRepository(psycopg.connect, dsn)


def _quoting_case(db, world, org_key="org_id", record_key="record_id", stop_at="quoting") -> str:
    cases, _ = _repos(db)
    opened = _run(cases, validated_case, OPEN_COMMERCIAL_CASE, OpenCommercialCaseBody(
        title="Caso histórico", origin_source_record_id=world[record_key], note="correo enviado"), world)
    case_id, version = opened["opportunity_id"], opened["opportunity_version"]
    if stop_at == "lead":
        return case_id
    added = _run(cases, validated_case, ADD_CASE_ORGANIZATION, AddCaseOrganizationBody(
        opportunity_id=case_id, opportunity_version=version, organization_id=world[org_key],
        organization_version=world[f"{org_key}_version"], role="requesting_institution",
        note="el operador confirmó la institución"), world)
    version = added["opportunity_version"]
    for stage in ("qualifying", "qualified", "quoting"):
        moved = _run(cases, validated_case, ADVANCE_CASE_STAGE, AdvanceCaseStageBody(
            opportunity_id=case_id, opportunity_version=version, stage=stage, note="histórico"), world)
        version = moved["opportunity_version"]
    return case_id


def _record(db, world, case_id, **over):
    _, quotes = _repos(db)
    over.setdefault("document_sha256", world["sha_a"])
    over.setdefault("quote_number", world["number"])
    over.setdefault("printed_quote_numbers", [over["quote_number"]])
    body = _body(opportunity_id=case_id, origin_source_record_id=world["record_id"], **over)
    return _run(quotes, validated_quote_import, RECORD_HISTORICAL_QUOTATION, body, world)


def _count(db, sql, *args) -> int:
    import psycopg

    with psycopg.connect(db) as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchone()[0]


@_needs_db
def test_the_harness_database_is_disposable(disposable_database) -> None:
    assert disposable_database.rpartition("/")[2].startswith("origenlab_test_")


@_needs_db
def test_recording_writes_quote_revision_events_and_resolves_the_assertion(disposable_database, world) -> None:
    case_id = _quoting_case(disposable_database, world)
    result = _record(disposable_database, world, case_id)
    assert result["created"] == ["quote", "quote_revision"]
    assert result["revision_no"] == 1
    assert len(result["event_ids"]) == 3
    db = disposable_database
    assert _count(db, "select count(*) from crm.quote where id = %s and number_origin = 'printed_historical' "
                      "and quote_number = %s", result["quote_id"], world["number"]) == 1
    assert _count(db, "select count(*) from crm.quote_revision where id = %s and status = 'sent' and "
                      "origin = 'historical_import' and pdf_sha256 = %s", result["quote_revision_id"], world["sha_a"]) == 1
    assert _count(db, "select count(*) from evidence.assertion where id = %s and resolution = 'promoted' "
                      "and resolved_kind = 'quote_revision'", world["assertion_a"]) == 1
    assert _count(db, "select count(*) from crm.domain_event where command_receipt_id = %s",
                  result["command_receipt_id"]) == 3


@_needs_db
def test_the_same_key_replays_and_writes_nothing(disposable_database, world) -> None:
    case_id = _quoting_case(disposable_database, world)
    _, quotes = _repos(disposable_database)
    body = _body(opportunity_id=case_id, origin_source_record_id=world["record_id"], document_sha256=world["sha_a"],
                 quote_number=world["number"], printed_quote_numbers=[world["number"]])
    first = _run(quotes, validated_quote_import, RECORD_HISTORICAL_QUOTATION, body, world, key=f"k-{world['tag']}")
    before = _count(disposable_database, "select count(*) from crm.domain_event")
    again = _run(quotes, validated_quote_import, RECORD_HISTORICAL_QUOTATION, body, world, key=f"k-{world['tag']}")
    assert again["replayed"] is True
    assert again["quote_revision_id"] == first["quote_revision_id"]
    assert _count(disposable_database, "select count(*) from crm.domain_event") == before


@_needs_db
def test_a_case_that_is_not_quoting_is_refused_and_nothing_is_written(disposable_database, world) -> None:
    case_id = _quoting_case(disposable_database, world, stop_at="lead")
    before = _count(disposable_database, "select count(*) from platform.command_receipt")
    with pytest.raises(CommandRefused) as err:
        _record(disposable_database, world, case_id)
    assert err.value.code == "case_not_quoting"
    assert _count(disposable_database, "select count(*) from platform.command_receipt") == before
    assert _count(disposable_database, "select count(*) from crm.quote_revision where pdf_sha256 = %s", world["sha_a"]) == 0


@_needs_db
def test_evidence_that_does_not_carry_the_document_is_refused(disposable_database, world) -> None:
    case_id = _quoting_case(disposable_database, world)
    with pytest.raises(CommandRefused) as err:
        _record(disposable_database, world, case_id, document_sha256=world["sha_c"])
    assert err.value.code == "source_record_does_not_carry_document"


@_needs_db
def test_one_document_is_never_two_revisions(disposable_database, world) -> None:
    case_id = _quoting_case(disposable_database, world)
    _record(disposable_database, world, case_id)
    with pytest.raises(CommandRefused) as err:
        _record(disposable_database, world, case_id, quote_number=f"X{world['tag'][:6]}", printed_quote_numbers=[f"X{world['tag'][:6]}"])
    assert err.value.code == "document_already_recorded"


@_needs_db
def test_a_printed_number_on_another_case_needs_a_reason(disposable_database, world) -> None:
    first = _quoting_case(disposable_database, world)
    _record(disposable_database, world, first)
    second = _quoting_case(disposable_database, world, org_key="other_org_id")
    with pytest.raises(CommandRefused) as err:
        _record(disposable_database, world, second, document_sha256=world["sha_b"])
    assert err.value.code == "number_on_other_opportunity"
    ok = _record(disposable_database, world, second, document_sha256=world["sha_b"],
                 number_on_other_opportunity_reason="dos clientes distintos recibieron el mismo número")
    assert ok["created"] == ["quote", "quote_revision"]


@_needs_db
def test_a_revision_supersedes_the_earlier_document_of_the_same_quote(disposable_database, world) -> None:
    case_id = _quoting_case(disposable_database, world)
    first = _record(disposable_database, world, case_id)
    second = _record(disposable_database, world, case_id, document_sha256=world["sha_b"],
                     supersedes_document_sha256=world["sha_a"])
    assert second["created"] == ["quote_revision"]
    assert second["quote_id"] == first["quote_id"]
    assert second["revision_no"] == 2
    assert _count(disposable_database, "select count(*) from crm.quote_revision where id = %s "
                  "and superseded_by_revision_no = 2", first["quote_revision_id"]) == 1


@_needs_db
def test_void_is_the_only_way_back_and_nothing_is_deleted(disposable_database, world) -> None:
    import psycopg

    case_id = _quoting_case(disposable_database, world)
    recorded = _record(disposable_database, world, case_id)
    _, quotes = _repos(disposable_database)
    voided = _run(quotes, validated_quote_import, VOID_HISTORICAL_QUOTE_REVISION,
                  VoidHistoricalQuoteRevisionBody(quote_revision_id=recorded["quote_revision_id"],
                                                  quote_revision_version=1, note="registrada por error"), world)
    assert voided["status"] == "void"
    with pytest.raises(CommandRefused):
        _run(quotes, validated_quote_import, VOID_HISTORICAL_QUOTE_REVISION,
             VoidHistoricalQuoteRevisionBody(quote_revision_id=recorded["quote_revision_id"],
                                             quote_revision_version=2, note="otra vez"), world)
    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        with pytest.raises(psycopg.errors.RaiseException, match="never deleted"):
            cur.execute("delete from crm.quote_revision where id = %s", (recorded["quote_revision_id"],))
        with pytest.raises(psycopg.errors.RaiseException, match="immutable"):
            cur.execute("update crm.quote_revision set pdf_sha256 = %s where id = %s",
                        (world["sha_c"], recorded["quote_revision_id"]))
        with pytest.raises(psycopg.errors.RaiseException, match="sent → void"):
            cur.execute("update crm.quote_revision set status = 'sent' where id = %s",
                        (recorded["quote_revision_id"],))


@_needs_db
def test_minted_numbers_stay_globally_unique(disposable_database, world) -> None:
    import psycopg

    first = _quoting_case(disposable_database, world)
    second = _quoting_case(disposable_database, world, org_key="other_org_id")
    number = f"M{world['tag'][:8]}"
    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute("insert into crm.quote (opportunity_id, quote_number) values (%s, %s)", (first, number))
        with pytest.raises(psycopg.errors.UniqueViolation):
            cur.execute("insert into crm.quote (opportunity_id, quote_number) values (%s, %s)", (second, number))
