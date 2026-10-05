"""confirm-organization-record against PostgreSQL, as the real `origenlab_api` role.

The card's «Confirmar institución»: machine_proposed → confirmed with the operator recorded, one
`organization.confirmed` event, the version bumped. An institution already confirmed is a no-op
answer — even from a stale version — never an error. Every name is invented.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from origenlab_api.v2.commands import CommandRefused, request_digest
from origenlab_api.v2.crm_authoring import V2CrmAuthoringRepository
from origenlab_api.v2.crm_authoring_routes import ConfirmOrganizationRecordBody
from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository
from origenlab_api.v2.identity import OperatorIdentity
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

COMMAND = "confirm-organization-record"


@pytest.fixture(scope="module")
def disposable_database():
    yield from build_disposable_database()


def _owner(dsn: str, sql: str, params: tuple = ()):
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


@pytest.fixture(scope="module")
def operator(disposable_database) -> OperatorIdentity:
    email = f"sales-{uuid.uuid4().hex[:8]}@example.test"
    op_id = _owner(
        disposable_database,
        "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
        "values (gen_random_uuid(), %s, 'Vendedora Prueba', 'sales', 'active') returning id::text",
        (email,),
    )[0][0]
    return OperatorIdentity(operator_id=op_id, email_norm=email, display_name="Vendedora Prueba",
                            role="sales", status="active")


def _org(dsn: str, *, archived_by: str | None = None) -> str:
    name = f"Instituto Ficticio {uuid.uuid4().hex[:6]}"
    if archived_by:
        return _owner(dsn, "insert into crm.organization (kind, name, confirmation, status, archived_at, "
                           "archived_by_operator_id, archive_reason) values ('unknown', %s, 'machine_proposed', "
                           "'archived', now(), %s, 'duplicado') returning id::text", (name, archived_by))[0][0]
    return _owner(dsn, "insert into crm.organization (kind, name, confirmation) "
                       "values ('unknown', %s, 'machine_proposed') returning id::text", (name,))[0][0]


def _run(dsn: str, operator: OperatorIdentity, fields: dict, key: str | None = None) -> dict:
    body = ConfirmOrganizationRecordBody(**fields)
    repo = V2CrmAuthoringRepository(psycopg.connect, runtime_dsn(dsn))
    return repo.execute(command_name=COMMAND, operator=operator, fields=body.model_dump(mode="json"),
                        idempotency_key=key or uuid.uuid4().hex, digest=request_digest(COMMAND, body))


def _state(dsn: str, org_id: str) -> tuple:
    return _owner(dsn, "select confirmation, confirmed_by_operator_id::text, version "
                       "from crm.organization where id = %s", (org_id,))[0]


def _confirmed_events(dsn: str, org_id: str) -> list:
    return _owner(dsn, "select payload from crm.domain_event where aggregate_kind = 'organization' "
                       "and aggregate_id = %s and event_type = 'organization.confirmed' order by seq", (org_id,))


@needs_db
def test_a_proposed_institution_is_confirmed_by_the_operator(disposable_database, operator) -> None:
    org = _org(disposable_database)
    out = _run(disposable_database, operator, {"organization_id": org, "expected_version": 1, "note": "revisada en la web"})
    assert (out["already_confirmed"], out["version"]) == (False, 2)
    assert _state(disposable_database, org) == ("confirmed", operator.operator_id, 2)
    [(payload,)] = _confirmed_events(disposable_database, org)
    assert payload == {"confirmed_from": "crm_authoring", "note": "revisada en la web"}


@needs_db
def test_an_already_confirmed_institution_is_a_no_op_even_from_a_stale_version(disposable_database, operator) -> None:
    org = _org(disposable_database)
    _run(disposable_database, operator, {"organization_id": org, "expected_version": 1})
    again = _run(disposable_database, operator, {"organization_id": org, "expected_version": 1})
    assert (again["already_confirmed"], again["version"]) == (True, 2)
    assert _state(disposable_database, org)[2] == 2
    assert len(_confirmed_events(disposable_database, org)) == 1


@needs_db
def test_a_stale_version_on_a_proposed_institution_is_refused_and_writes_nothing(disposable_database, operator) -> None:
    org = _org(disposable_database)
    with pytest.raises(CommandRefused) as exc:
        _run(disposable_database, operator, {"organization_id": org, "expected_version": 7})
    assert (exc.value.status_code, exc.value.code) == (409, "stale_version")
    assert _state(disposable_database, org) == ("machine_proposed", None, 1)
    assert _confirmed_events(disposable_database, org) == []


@needs_db
def test_an_archived_or_unknown_institution_is_refused(disposable_database, operator) -> None:
    archived = _org(disposable_database, archived_by=operator.operator_id)
    with pytest.raises(CommandRefused) as exc:
        _run(disposable_database, operator, {"organization_id": archived, "expected_version": 1})
    assert (exc.value.status_code, exc.value.code) == (409, "archived_subject")
    with pytest.raises(CommandRefused) as exc:
        _run(disposable_database, operator, {"organization_id": str(uuid.uuid4()), "expected_version": 1})
    assert (exc.value.status_code, exc.value.code) == (404, "organization_not_found")


@needs_db
def test_the_same_key_twice_confirms_once(disposable_database, operator) -> None:
    org = _org(disposable_database)
    key = uuid.uuid4().hex
    first = _run(disposable_database, operator, {"organization_id": org, "expected_version": 1}, key)
    second = _run(disposable_database, operator, {"organization_id": org, "expected_version": 1}, key)
    assert second["replayed"] is True and second["version"] == first["version"] == 2
    assert len(_confirmed_events(disposable_database, org)) == 1


@needs_db
def test_the_card_read_says_who_confirmed_and_when(disposable_database, operator) -> None:
    org = _org(disposable_database)
    _run(disposable_database, operator, {"organization_id": org, "expected_version": 1})
    o = CrmWorkspaceRepository(psycopg.connect, runtime_dsn(disposable_database)).organization_authoring(org)["organization"]
    assert (o["confirmation"], o["confirmed_by_operator_id"], o["confirmed_by_name"]) == (
        "confirmed", operator.operator_id, "Vendedora Prueba",
    )
    assert o["confirmed_at"]
