"""create-person with an address the CRM already holds, against PostgreSQL as `origenlab_api`.

`crm.contact_point` has one row per address, globally (`contact_point_kind_value_key`), and the
historical import left the quote recipients there with no owner. Before this, `create-person`
inserted a second row and the unique violation surfaced as a 500. Now it claims an ownerless row
(`unattributed` → `personal`; `individual_owner_unknown` → `work`, institution kept) and refuses,
by name, an address that is another person's, a shared mailbox or deactivated — and a refusal
leaves no person behind. Every address is invented (`example.invalid`).
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from origenlab_api.v2.commands import CommandRefused, request_digest
from origenlab_api.v2.crm_authoring import V2CrmAuthoringRepository
from origenlab_api.v2.crm_authoring_routes import CreatePersonBody
from origenlab_api.v2.identity import OperatorIdentity
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

COMMAND = "create-person"


@pytest.fixture(scope="module")
def disposable_database():
    yield from build_disposable_database()


def _owner(dsn: str, sql: str, params: tuple = ()):
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


@pytest.fixture(scope="module")
def world(disposable_database):
    dsn, tag = disposable_database, uuid.uuid4().hex[:8]
    email = f"sales-{tag}@example.test"
    op = _owner(dsn, "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                     "values (gen_random_uuid(), %s, 'Vendedora', 'sales', 'active') returning id::text", (email,))[0][0]
    org = _owner(dsn, "insert into crm.organization (kind, name, confirmation, confirmed_by_operator_id) "
                      "values ('universidad', %s, 'confirmed', %s) returning id::text",
                 (f"Universidad Ficticia {tag}", op))[0][0]
    holder = _owner(dsn, "insert into crm.person (display_name, confirmation, confirmed_by_operator_id) "
                         "values ('Persona Existente', 'confirmed', %s) returning id::text", (op,))[0][0]
    addr = {k: f"{k}-{tag}@uni-{tag}.example.invalid" for k in ("loose", "operated", "desk", "taken", "off", "new")}

    def point(address: str, usage: str, *, person: str | None = None, organization: str | None = None,
              inactive: bool = False) -> None:
        _owner(dsn,
               "insert into crm.contact_point (kind, value_norm, value_display, person_id, organization_id, usage, "
               "confirmation, status, deactivated_at, deactivated_by_operator_id) "
               "values ('email', %s, %s, %s, %s, %s, 'machine_proposed', %s, "
               "case when %s then now() end, case when %s then %s::uuid end)",
               (address, address, person, organization, usage, "inactive" if inactive else "active",
                inactive, inactive, op))

    point(addr["loose"], "unattributed")
    point(addr["operated"], "individual_owner_unknown", organization=org)
    point(addr["desk"], "shared_mailbox", organization=org)
    point(addr["taken"], "personal", person=holder)
    point(addr["off"], "unattributed", inactive=True)
    operator = OperatorIdentity(operator_id=op, email_norm=email, display_name="Vendedora", role="sales", status="active")
    return {"org": org, "addr": addr, "operator": operator}


def _create(dsn: str, w: dict, email: str, *, name: str | None = None) -> dict:
    body = CreatePersonBody(display_name=name or f"Ana Prueba {uuid.uuid4().hex[:4]}", email=email,
                            organization_id=w["org"], note="sugerida desde cotizaciones")
    repo = V2CrmAuthoringRepository(psycopg.connect, runtime_dsn(dsn))
    return repo.execute(command_name=COMMAND, operator=w["operator"], fields=body.model_dump(),
                        idempotency_key=uuid.uuid4().hex, digest=request_digest(COMMAND, body))


def _point(dsn: str, address: str) -> list:
    return _owner(dsn, "select person_id::text, organization_id::text, usage, confirmation, version "
                       "from crm.contact_point where kind = 'email' and value_norm = %s", (address,))


@needs_db
def test_an_unattributed_address_becomes_the_new_persons(disposable_database, world) -> None:
    out = _create(disposable_database, world, world["addr"]["loose"])
    assert out["email_claimed"] is True
    assert _point(disposable_database, world["addr"]["loose"]) == [(out["person_id"], None, "personal", "confirmed", 2)]
    [(payload,)] = _owner(disposable_database,
                          "select payload from crm.domain_event where aggregate_kind = 'contact_point' "
                          "and aggregate_id = %s and event_type = 'contact_point.updated'",
                          (out["email_contact_point_id"],))
    assert (payload["claimed_by_person_id"], payload["previous_usage"]) == (out["person_id"], "unattributed")
    assert "@" not in str(payload)
    assert _owner(disposable_database, "select count(*) from crm.affiliation where person_id = %s and organization_id = %s",
                  (out["person_id"], world["org"])) == [(1,)]


@needs_db
def test_an_address_the_institution_operates_becomes_a_work_address_and_keeps_it(disposable_database, world) -> None:
    out = _create(disposable_database, world, world["addr"]["operated"])
    [(person, organization, usage, _, _)] = _point(disposable_database, world["addr"]["operated"])
    assert (person, organization, usage) == (out["person_id"], world["org"], "work")


@needs_db
@pytest.mark.parametrize(
    ("which", "code"),
    [("taken", "contact_point_taken"), ("desk", "shared_mailbox"), ("off", "contact_point_inactive")],
)
def test_an_address_that_is_not_free_is_refused_by_name_and_leaves_no_person(disposable_database, world, which, code) -> None:
    name = f"Refusada {which} {uuid.uuid4().hex[:4]}"
    with pytest.raises(CommandRefused) as exc:
        _create(disposable_database, world, world["addr"][which], name=name)
    assert (exc.value.status_code, exc.value.code) == (409, code)
    assert _owner(disposable_database, "select count(*) from crm.person where display_name = %s", (name,)) == [(0,)]


@needs_db
def test_a_new_address_is_created_as_before(disposable_database, world) -> None:
    out = _create(disposable_database, world, world["addr"]["new"])
    assert out["email_claimed"] is False
    [(person, organization, usage, confirmation, _)] = _point(disposable_database, world["addr"]["new"])
    assert (person, organization, usage, confirmation) == (out["person_id"], None, "personal", "confirmed")
